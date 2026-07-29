"""Host check / facts routes blueprint.

Extracted from ``backend/app.py`` during the phased refactor. URLs, methods,
view-function names, response shapes, and auth decorators are preserved.

Helpers still living in ``app.py`` (``yaml_loader``, ``create_minimal_ansible_config``,
``create_execution_record``, ``resolve_connection_secret_for_host``, ``TEMP_DIR``)
are resolved lazily via the initialized app module to avoid circular imports.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import yaml
from flask import Blueprint, jsonify, request, current_app

try:
    from auth.middleware import require_auth
except ImportError:  # pragma: no cover
    from ..auth.middleware import require_auth

from utils.project_paths import (
    get_project_dir,
    get_project_inventories_dir,
    get_project_generated_playbook_path,
    get_project_secrets_dir,
    resolve_ansible_config_path,
)

from utils.ssh_keys import copy_keys_to_generated_playbooks_dedup as _copy_keys_to_generated_playbooks_dedup

from utils.request_ctx import get_project_id_from_request as _get_pid_raw


def get_project_id_from_request():
    return _get_pid_raw(lambda: None)


bp = Blueprint("host_checks_api", __name__)


# ---------------------------------------------------------------------------
# Lazy proxy for app.py globals
# ---------------------------------------------------------------------------
_APP_NAMES = (
    "yaml_loader",
    "create_minimal_ansible_config",
    "create_execution_record",
    "resolve_connection_secret_for_host",
    "TEMP_DIR",
    "get_project_host_vars_dir",
)


def _app_module():
    for name in ("__main__", "app"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "yaml_loader"):
            return mod
    import app as _a  # noqa
    return _a


class _LazyProxy:
    def __init__(self, name):
        self._name = name

    def _resolve(self):
        return getattr(_app_module(), self._name)

    def __call__(self, *a, **kw):
        return self._resolve()(*a, **kw)

    def __getattr__(self, item):
        return getattr(self._resolve(), item)

    def __truediv__(self, other):
        return self._resolve() / other


for _n in _APP_NAMES:
    globals()[_n] = _LazyProxy(_n)


class _AppLogger:
    def __getattr__(self, item):
        try:
            return getattr(_app_module().app.logger, item)
        except Exception:
            return getattr(current_app.logger, item)


app_logger = _AppLogger()


@bp.route('/api/check_host', methods=['POST'])
@require_auth
def check_host():
    """ Ansible ping"""
    try:
        data = request.json or {}
        host = data.get('host')
        
        app_logger.info(f"User checking host availability: {host}")
        app_logger.debug(f"[check_host] Received request with data: host={host}, connection_secret={data.get('connection_secret')}, project_id={data.get('project_id')}")
        
        if not host:
            return jsonify({'success': False, 'error': 'Host not specified'}), 400
        
        # project_id - REQUIRED, no fallback
        project_id = data.get('project_id') or get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        app_logger.debug(f"[check_host] Project ID: {project_id}, Host: {host}")
        
        # Get ansible config - ansible-config 
        selected_ansible_config = data.get('ansible_config')
        if not selected_ansible_config:
            app_logger.error(f"[check_host] ansible_config not provided in request")
            return jsonify({'success': False, 'error': 'ansible_config is required'}), 400
        project_dir = get_project_dir(project_id)
        ansible_config_path = resolve_ansible_config_path(project_id, selected_ansible_config)
        ansible_config_path.parent.mkdir(parents=True, exist_ok=True)
        if not ansible_config_path.exists() or not ansible_config_path.is_file():
            app_logger.debug(f"[check_host] Config file not found: {ansible_config_path}, creating minimal config")
            if not create_minimal_ansible_config(ansible_config_path):
                app_logger.warning(f"[check_host] Failed to create minimal config, continuing without ANSIBLE_CONFIG")
        
        # inventory 
        selected_inventory_files = data.get('inventory_files', ['inventory.yml'])
        project_dir = get_project_dir(project_id)
        
        # inventory 
        temp_inventory = None
        temp_playbook = None
        try:
            temp_dir = TEMP_DIR
            try:
                temp_dir.mkdir(exist_ok=True)
            except (OSError, PermissionError) as e:
                app_logger.error(f"Failed to create temp directory: {type(e).__name__}")
                return jsonify({
                    'success': False,
                    'error': 'Failed to create temporary directory for host check'
                }), 500
            
            temp_inventory = temp_dir / f'check_host_{uuid.uuid4().hex[:8]}.yml'
            
            # inventory Project Storage 
            combined_inventory = {'all': {'hosts': {}}}
            
            inventories_dir = get_project_inventories_dir(project_id)
            inventory_names_alt = {'inventory.yml': 'inventory.yaml', 'inventory.yaml': 'inventory.yml', 'hosts.yml': 'hosts.yaml', 'hosts.yaml': 'hosts.yml', 'hosts': 'hosts', 'hosts.ini': 'hosts.ini'}
            for inv_file_path in selected_inventory_files:
                # repo (inventories/...) (inventory.yml)
                if inv_file_path.startswith('inventories/') or inv_file_path.startswith('inventory/'):
                    inv_path = project_dir / 'repo' / inv_file_path
                elif '/' in inv_file_path:
                    inv_path = inventories_dir / inv_file_path
                else:
                    inv_path = inventories_dir / inv_file_path
                    if not inv_path.exists() and inv_file_path in inventory_names_alt:
                        inv_path = inventories_dir / inventory_names_alt[inv_file_path]
                    if not inv_path.exists() and inventories_dir.exists():
                        for p in inventories_dir.rglob(inv_file_path):
                            if p.is_file():
                                inv_path = p
                                break
                    if not inv_path.exists():
                        inv_path = project_dir / 'repo' / inv_file_path
                
                app_logger.debug(f"[check_host] Checking inventory file: {inv_path}")
                if inv_path.exists():
                    try:
                        with open(inv_path, 'r', encoding='utf-8') as f:
                            inv_data = yaml.safe_load(f)
                            host_data = find_host_in_inventory(inv_data, host)
                            if host_data is not None:
                                app_logger.debug(f"[check_host] Host {host} found in inventory, data: {host_data}")
                                combined_inventory['all']['hosts'][host] = host_data
                                break  # , 
                            else:
                                app_logger.warning(f"[check_host] Host {host} not found in inventory file {inv_file_path}")
                    except Exception as e:
                        app_logger.warning(f"Error reading inventory file {inv_file_path}: {type(e).__name__}: {e}")
                else:
                    app_logger.warning(f"[check_host] Inventory file does not exist: {inv_path}")
            
            # , inventory
            if host not in combined_inventory['all']['hosts']:
                combined_inventory['all']['hosts'][host] = {}
            
            # inventory vars
            host_data = combined_inventory['all']['hosts'][host]
            
            temp_key_files = []
            
            # host_vars Ansible 
            host_vars_dir = get_project_host_vars_dir(project_id)
            # IP , inventory
            host_name = host  # IP 
            if 'ansible_host' in host_data:
                host_name = host_data.get('ansible_host', host)
            
            host_file = host_vars_dir / f"{host_name}.yml"
            # host_name, host
            if not host_file.exists() and host_name != host:
                host_file = host_vars_dir / f"{host}.yml"
                if host_file.exists():
                    host_name = host
            
            host_vars_loaded = False
            secret_username = None
            
            if host_file.exists():
                try:
                    with open(host_file, 'r', encoding='utf-8') as f:
                        host_vars = yaml_loader.load(f) or {}
                    
                    app_logger.debug(f"[check_host] Host vars loaded: {list(host_vars.keys())}")
                    
                    # connection-related 
                    if host_vars.get('ansible_ssh_private_key_file') or host_vars.get('ansible_password') or host_vars.get('connectionSecret'):
                        app_logger.debug(f"[check_host] Found connection variables in host_vars, using them directly")
                        host_vars_loaded = True
                        
                        # connectionSecret, ( api_run_playbook)
                        connection_secret_name = host_vars.get('connectionSecret')
                        if connection_secret_name:
                            try:
                                secrets_dir = get_project_secrets_dir(project_id)
                                # (secrets/ssh_keys/)
                                ssh_keys_dir = secrets_dir / 'ssh_keys'
                                secret_file = ssh_keys_dir / f"{connection_secret_name}.json"
                                if not secret_file.exists():
                                    # Fallback 
                                    secret_file = secrets_dir / f"{connection_secret_name}.json"
                                
                                if secret_file.exists():
                                    with open(secret_file, 'r', encoding='utf-8') as f:
                                        secret_data = json.load(f)
                                    
                                    if secret_data.get('type') == 'ssh_key':
                                        private_key = secret_data.get('privateKey', '')
                                        if private_key:
                                            # ( api_run_playbook)
                                            import tempfile
                                            temp_key_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.pem', dir=str(TEMP_DIR), encoding='utf-8')
                                            key_content = normalize_pem_key_for_file(private_key)
                                            temp_key_file.write(key_content)
                                            temp_key_file.flush()  # , 
                                            os.fsync(temp_key_file.fileno())
                                            temp_key_file.close()
                                            os.chmod(temp_key_file.name, 0o600)
                                            
                                            temp_key_files.append(temp_key_file.name)
                                            
                                            # ( )
                                            abs_key_path = os.path.abspath(temp_key_file.name)
                                            host_vars['ansible_ssh_private_key_file'] = abs_key_path
                                            
                                            # ansible_user , 
                                            secret_username = secret_data.get('username', '').strip()
                                            if secret_username:
                                                host_vars['ansible_user'] = secret_username
                                                app_logger.debug(f"[check_host] Updated ansible_user from secret {connection_secret_name}: {secret_username}")
                                            
                                            app_logger.debug(f"[check_host] Created temporary SSH key file from secret {connection_secret_name} at {abs_key_path}")
                                        else:
                                            app_logger.error(f"[check_host] Private key is empty in secret {connection_secret_name}")
                                            return jsonify({
                                                'success': False,
                                                'available': False,
                                                'error': f'Private key is empty in secret {connection_secret_name}',
                                                'message': f'Secret {connection_secret_name} does not contain a private key'
                                            }), 400
                                    elif secret_data.get('type') == 'login_password':
                                        password = secret_data.get('password', '')
                                        if password:
                                            host_vars['ansible_password'] = password
                                            host_vars['ansible_ssh_pass'] = password
                                            secret_username = (secret_data.get('username') or '').strip()
                                            if secret_username:
                                                host_vars['ansible_user'] = secret_username
                                            host_vars_loaded = True
                                            app_logger.debug(f"[check_host] Injected password from secret {connection_secret_name} (in-memory only)")
                                else:
                                    app_logger.error(f"[check_host] Secret file not found: {secret_file}")
                                    return jsonify({
                                        'success': False,
                                        'available': False,
                                        'error': f'Secret {connection_secret_name} not found',
                                        'message': f'Secret {connection_secret_name} not found'
                                    }), 400
                            except Exception as e:
                                # ,
                                # ( JSON )
                                app_logger.warning(f"[check_host] Error creating temporary SSH key from secret name {connection_secret_name}: {e}, will try to process path directly if it's a JSON file")
                        
                        # simple_inventory host_name host_vars
                        ansible_host_from_vars = host_vars.get('ansible_host', host_name)
                        simple_inventory = {
                            'all': {
                                'hosts': {
                                    ansible_host_from_vars: {}
                                }
                            }
                        }
                        
                        # ansible_* host_vars ( )
                        for key, value in host_vars.items():
                            if key.startswith('ansible_') or key == 'ansible_host':
                                simple_inventory['all']['hosts'][ansible_host_from_vars][key] = value
                        
                        # username playbook
                        secret_username = host_vars.get('ansible_user', 'root')
                        
                        # SSH , 
                        if host_vars.get('ansible_ssh_private_key_file'):
                            key_file_path = Path(host_vars['ansible_ssh_private_key_file'])
                            # , 
                            if not key_file_path.is_absolute():
                                key_file_path = key_file_path.resolve()
                            
                            # , JSON 
                            # PEM ( ), JSON
                            if key_file_path.exists() and key_file_path.suffix == '.json' and not str(key_file_path).startswith(str(TEMP_DIR)):
                                # JSON - PEM 
                                if connection_secret_name:
                                    try:
                                        with open(key_file_path, 'r', encoding='utf-8') as f:
                                            secret_data = json.load(f)
                                        
                                        if secret_data.get('type') == 'ssh_key':
                                            private_key = secret_data.get('privateKey', '')
                                            if private_key:
                                                import tempfile
                                                temp_key_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.pem', dir=str(TEMP_DIR), encoding='utf-8')
                                                key_content = normalize_pem_key_for_file(private_key)
                                                temp_key_file.write(key_content)
                                                temp_key_file.flush()
                                                os.fsync(temp_key_file.fileno())
                                                temp_key_file.close()
                                                os.chmod(temp_key_file.name, 0o600)
                                                
                                                if 'temp_key_files' not in locals():
                                                    temp_key_files = []
                                                temp_key_files.append(temp_key_file.name)
                                                
                                                abs_key_path = os.path.abspath(temp_key_file.name)
                                                host_vars['ansible_ssh_private_key_file'] = abs_key_path
                                                simple_inventory['all']['hosts'][ansible_host_from_vars]['ansible_ssh_private_key_file'] = abs_key_path
                                                
                                                # ansible_user , 
                                                secret_username = secret_data.get('username', '').strip()
                                                if secret_username:
                                                    host_vars['ansible_user'] = secret_username
                                                    simple_inventory['all']['hosts'][ansible_host_from_vars]['ansible_user'] = secret_username
                                                    app_logger.debug(f"[check_host] Updated ansible_user from JSON secret: {secret_username}")
                                                
                                                app_logger.debug(f"[check_host] Created temporary SSH key file from JSON secret at {abs_key_path}")
                                            else:
                                                app_logger.error(f"[check_host] Private key is empty in secret JSON file {key_file_path}")
                                                return jsonify({
                                                    'success': False,
                                                    'available': False,
                                                    'error': f'Private key is empty in secret file {key_file_path}',
                                                    'message': f'Secret file does not contain a private key'
                                                }), 400
                                        else:
                                            app_logger.error(f"[check_host] Secret file {key_file_path} is not an SSH key type")
                                            return jsonify({
                                                'success': False,
                                                'available': False,
                                                'error': f'Secret file {key_file_path} is not an SSH key type',
                                                'message': f'Secret file type is {secret_data.get("type")}, expected ssh_key'
                                            }), 400
                                    except Exception as e:
                                        app_logger.error(f"[check_host] Error processing JSON secret file {key_file_path}: {e}")
                                        return jsonify({
                                            'success': False,
                                            'available': False,
                                            'error': f'Error processing secret file: {str(e)}',
                                            'message': f'Failed to process secret file {key_file_path}'
                                        }), 400
                                else:
                                    app_logger.error(f"[check_host] Path points to JSON secret file {key_file_path}, but connectionSecret is not set")
                                    return jsonify({
                                        'success': False,
                                        'available': False,
                                        'error': f'Path points to JSON secret file, but connectionSecret is not set',
                                        'message': f'Cannot process JSON secret file without connectionSecret'
                                    }), 400
                            else:
                                # ( JSON)
                                if not key_file_path.is_absolute():
                                    simple_inventory['all']['hosts'][ansible_host_from_vars]['ansible_ssh_private_key_file'] = str(key_file_path)
                                
                                if not key_file_path.exists():
                                    app_logger.error(f"[check_host] SSH key file not found: {key_file_path}")
                                    return jsonify({
                                        'success': False,
                                        'available': False,
                                        'error': f'SSH key file not found: {key_file_path}',
                                        'message': 'SSH key file specified in host_vars, but file does not exist'
                                    }), 400
                                
                                key_stat = os.stat(key_file_path)
                                if key_stat.st_mode & 0o077 != 0:
                                    app_logger.warning(f"[check_host] SSH key file has incorrect permissions: {oct(key_stat.st_mode)}, fixing...")
                                    os.chmod(key_file_path, 0o600)
                                
                                app_logger.debug(f"[check_host] Using SSH key file: {key_file_path} (exists: {key_file_path.exists()}, size: {key_file_path.stat().st_size if key_file_path.exists() else 0} bytes)")
                        
                        # host_name playbook
                        host_name = ansible_host_from_vars
                except Exception as e:
                    app_logger.warning(f"Error reading host vars for {host_name}: {e}")
            
            # host_vars connection , connection_secret
            if not host_vars_loaded:
                # connection secret - 
                connection_secret_name = data.get('connection_secret')
                # None, 'null' connection_secret
                if connection_secret_name in (None, '', 'null', 'undefined'):
                    connection_secret_name = None
                app_logger.debug(f"[check_host] Connection secret from request: {connection_secret_name} (type: {type(connection_secret_name).__name__})")
                if not connection_secret_name:
                    app_logger.error(f"[check_host] ERROR: connection_secret NOT provided in request! Connection is impossible without connection secret.")
                    return jsonify({
                        'success': False,
                        'available': False,
                        'error': 'Connection secret not specified. Select a connection secret for the host in Hosts & Groups.',
                        'message': 'Connection secret is required to connect to the host'
                    }), 400
                
                # connection_secret - 
                if connection_secret_name:
                    try:
                        # secret ( : secrets/ssh_keys/)
                        secrets_dir = get_project_secrets_dir(project_id)
                        ssh_keys_dir = secrets_dir / 'ssh_keys'
                        
                        # (secrets/ssh_keys/)
                        secret_file = ssh_keys_dir / f"{connection_secret_name}.json"
                        if not secret_file.exists():
                            # Fallback 
                            secret_file = secrets_dir / f"{connection_secret_name}.json"
                        
                        app_logger.debug(f"[check_host] Looking for secret file: {secret_file}")
                        
                        if secret_file.exists():
                            app_logger.debug(f"[check_host] Secret file found, loading...")
                            with open(secret_file, 'r', encoding='utf-8') as f:
                                secret_data = json.load(f)
                            
                            secret_type = secret_data.get('type')
                            app_logger.debug(f"[check_host] Secret type: {secret_type} (variable type: {type(secret_type).__name__})")
                            app_logger.debug(f"[check_host] Checking secret type for processing... secret_type == 'ssh_key': {secret_type == 'ssh_key'}")
                            
                            # credentials inventory 
                            if secret_type == 'ssh_key':
                                app_logger.debug(f"[check_host] Processing SSH key secret...")
                                # SSH 
                                import tempfile
                                private_key = secret_data.get('privateKey', '')
                                if not private_key:
                                    app_logger.error(f"[check_host] Private key is empty in secret {connection_secret_name}")
                                    raise ValueError(f"Private key is empty in secret {connection_secret_name}")
                                
                                temp_key_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.pem', dir=str(TEMP_DIR), encoding='utf-8')
                                temp_key_file.write(normalize_pem_key_for_file(private_key))
                                temp_key_file.close()
                                os.chmod(temp_key_file.name, 0o600)
                                
                                # username - secret, 
                                username = secret_data.get('username', '').strip()
                                if not username:
                                    app_logger.error(f"[check_host] ERROR: username not specified in connection secret {connection_secret_name}! Username is required.")
                                    raise ValueError(f"Username is required in connection secret {connection_secret_name} but is missing or empty")
                                
                                # username playbook
                                secret_username = username
                                
                                app_logger.info(f"[check_host] SSH key created: {temp_key_file.name}, username: {username}")
                                app_logger.info(f"[check_host] Key size: {len(private_key)} characters")
                                
                                # inventory ( vars)
                                combined_inventory['all']['hosts'][host]['ansible_ssh_private_key_file'] = temp_key_file.name
                                combined_inventory['all']['hosts'][host]['ansible_user'] = username
                                app_logger.debug(f"[check_host] Added parameters to inventory: ansible_ssh_private_key_file={temp_key_file.name}, ansible_user={username}")
                                # init_ssh_connect - gather facts
                                
                                if 'temp_key_files' not in locals():
                                    temp_key_files = []
                                temp_key_files.append(temp_key_file.name)
                            elif secret_type == 'login_password':
                                # inventory
                                username = secret_data.get('username', '').strip()
                                if not username:
                                    app_logger.error(f"[check_host] ERROR: username not specified in connection secret {connection_secret_name}! Username is required.")
                                    raise ValueError(f"Username is required in connection secret {connection_secret_name} but is missing or empty")
                                
                                # username playbook
                                secret_username = username
                                
                                password = secret_data.get('password', '')
                                
                                if not password:
                                    app_logger.error(f"[check_host] Password is empty in secret {connection_secret_name}")
                                    raise ValueError(f"Password is empty in secret {connection_secret_name}")
                                
                                app_logger.info(f"[check_host] Using password auth, username: {username}, password length: {len(password)}")
                                
                                # inventory ( vars) 
                                combined_inventory['all']['hosts'][host]['ansible_user'] = username
                                combined_inventory['all']['hosts'][host]['ansible_password'] = password
                                app_logger.debug(f"[check_host] Added parameters to inventory: ansible_user={username}, ansible_password=***")
                    except Exception as e:
                        app_logger.error(f"[check_host] ERROR loading connection secret {connection_secret_name}: {e}")
                        return jsonify({
                            'success': False,
                            'available': False,
                            'error': f'Error loading connection secret: {str(e)}',
                            'message': f'Failed to load connection secret {connection_secret_name}'
                        }), 400
            
            # host_vars , inventory combined_inventory
            if not host_vars_loaded:
                # inventory vars
                app_logger.debug(f"[check_host] Creating simple inventory format from combined_inventory...")
                
                # combined_inventory
                host_data = combined_inventory['all']['hosts'][host]
                
                # ( IP inventory)
                host_name = host
                if 'ansible_host' in host_data:
                    host_name = host_data.get('ansible_host', host)
                
                # simple_inventory
                simple_inventory = {
                    'all': {
                        'hosts': {
                            host_name: {}
                        }
                    }
                }
                
                # ansible_host (IP )
                simple_inventory['all']['hosts'][host_name]['ansible_host'] = host_data.get('ansible_host', host)
                
                # ansible_* 
                # ansible_ssh_common_args - inventory
                for key, value in host_data.items():
                    if key.startswith('ansible_') or key == 'ansible_host':
                        # ansible_ssh_common_args
                        if key != 'ansible_ssh_common_args':
                            simple_inventory['all']['hosts'][host_name][key] = value
                    elif key == 'vars':
                        # vars, 
                        for var_key, var_value in value.items():
                            if var_key.startswith('ansible_'):
                                # ansible_ssh_common_args
                                if var_key != 'ansible_ssh_common_args':
                                    simple_inventory['all']['hosts'][host_name][var_key] = var_value
            else:
                # host_vars , simple_inventory 
                app_logger.debug(f"[check_host] Using connection variables from host_vars")
            
            # host key Check ( SSH unreachable)
            for _h in simple_inventory.get('all', {}).get('hosts', {}):
                simple_inventory['all']['hosts'][_h]['ansible_ssh_common_args'] = '-o StrictHostKeyChecking=accept-new'
                break
            
            # execution_id : .
            # runtime/generated_playbooks/ .
            execution_id = str(uuid.uuid4())
            generated_playbooks_dir = get_project_generated_playbook_path(project_id, execution_id).parent
            generated_playbooks_dir.mkdir(parents=True, exist_ok=True)
            key_path_map = _copy_keys_to_generated_playbooks_dedup(
                temp_key_files if 'temp_key_files' in locals() else [],
                generated_playbooks_dir
            )
            for _h, hdata in simple_inventory.get('all', {}).get('hosts', {}).items():
                k = hdata.get('ansible_ssh_private_key_file')
                if k and k in key_path_map:
                    hdata['ansible_ssh_private_key_file'] = key_path_map[k]
            
            app_logger.debug(f"[check_host] Creating temporary inventory file: {temp_inventory}")
            try:
                with open(temp_inventory, 'w', encoding='utf-8') as f:
                    yaml.dump(simple_inventory, f, default_flow_style=False, allow_unicode=True)
                app_logger.debug(f"[check_host] Temporary inventory file created successfully")
                
                # inventory ( )
                safe_inventory = {}
                for host_name_inv, host_data_inv in simple_inventory.get('all', {}).get('hosts', {}).items():
                    safe_inventory[host_name_inv] = {}
                    for key, value in host_data_inv.items():
                        if 'key' in key.lower() or 'password' in key.lower() or 'secret' in key.lower():
                            safe_inventory[host_name_inv][key] = f"***REDACTED*** (path exists: {Path(value).exists() if isinstance(value, str) else 'N/A'})"
                        else:
                            safe_inventory[host_name_inv][key] = value
                app_logger.debug(f"[check_host] Inventory structure: {safe_inventory}")
                
                # SECURITY: Never log inventory file contents (may contain secrets).
            except (OSError, PermissionError, IOError) as e:
                app_logger.error(f"Failed to create temporary inventory file: {type(e).__name__}: {e}")
                return jsonify({
                    'success': False,
                    'error': 'Failed to create temporary file for host check'
                }), 500
        except Exception as e:
            app_logger.error(f"Error preparing temporary files: {type(e).__name__}")
            return jsonify({
                'success': False,
                'error': 'Error preparing host check'
            }), 500
        
        env = os.environ.copy()
        env['GIT_SSH_COMMAND'] = 'ssh -o StrictHostKeyChecking=accept-new'
        if ansible_config_path.exists() and ansible_config_path.is_file():
            env['ANSIBLE_CONFIG'] = str(ansible_config_path)
            app_logger.debug(f"[check_host] Using ANSIBLE_CONFIG: {ansible_config_path}")
        else:
            app_logger.warning(f"Ansible config {selected_ansible_config} not found in project storage for project {project_id} at path {ansible_config_path}, using default Ansible config")
        
        # password auth, sshpass
        # simple_inventory ( )
        check_host_data = simple_inventory.get('all', {}).get('hosts', {}).get(host_name, {})
        if secret_username and ('ansible_password' in check_host_data or 'ansible_ssh_pass' in check_host_data):
            sshpass_path = None
            try:
                import shutil
                sshpass_path = shutil.which('sshpass')
                if sshpass_path:
                    app_logger.debug(f"[check_host] sshpass found: {sshpass_path}")
                    # Ansible sshpass ansible_ssh_pass 
                    password = check_host_data.get('ansible_password') or check_host_data.get('ansible_ssh_pass', '')
                    if password:
                        # , PATH sshpass
                        current_path = env.get('PATH', '')
                        sshpass_dir = str(Path(sshpass_path).parent)
                        if sshpass_dir not in current_path:
                            env['PATH'] = f"{sshpass_dir}:{current_path}"
                else:
                    app_logger.error(f"[check_host] sshpass not found in PATH! Password auth will NOT work without sshpass.")
                    app_logger.error(f"[check_host] Install sshpass: apt-get install sshpass (Debian/Ubuntu) or yum install sshpass (RHEL/CentOS)")
                    return jsonify({
                        'success': False,
                        'available': False,
                        'error': 'sshpass is not installed. Install sshpass to use password authentication.',
                        'message': 'Password authentication requires sshpass. Install: apt-get install sshpass'
                    }), 400
            except Exception as e:
                app_logger.error(f"[check_host] Error checking sshpass: {e}")
                return jsonify({
                    'success': False,
                    'available': False,
                    'error': f'Error checking sshpass: {str(e)}',
                    'message': 'Failed to check sshpass availability'
                }), 500
        
        # playbook raw SSH connectivity check
        try:
            temp_playbook = TEMP_DIR / f'check_host_playbook_{uuid.uuid4().hex[:8]}.yaml'
            
            # playbook - use raw so fresh hosts without Python are still checked cleanly
            # Connection credentials inventory host_vars connection secret
            # remote_user playbook 
            # secret_username host_vars, connection_secret
            if not secret_username:
                # username simple_inventory
                check_host_data = simple_inventory.get('all', {}).get('hosts', {}).get(host_name, {})
                secret_username = check_host_data.get('ansible_user', 'root')
                app_logger.debug(f"[check_host] Using username from inventory: {secret_username}")
            
            playbook_content = f"""---
- hosts: all
  remote_user: {secret_username}
  gather_facts: no
  tasks:
    - name: Test SSH connectivity to host
      ansible.builtin.raw: echo opensible_host_check_ok
      changed_when: false
             """


            app_logger.debug(f"[check_host] Adding remote_user: {secret_username} to playbook from connection secret")
            
            try:
                with open(temp_playbook, 'w', encoding='utf-8') as f:
                    f.write(playbook_content)
            except (OSError, PermissionError, IOError) as e:
                app_logger.error(f"Failed to create temporary playbook file: {type(e).__name__}")
                # temp_inventory 
                if temp_inventory and temp_inventory.exists():
                    try:
                        temp_inventory.unlink()
                    except Exception:
                        pass
                return jsonify({
                    'success': False,
                    'error': 'Failed to create temporary file for host check'
                }), 500
        except Exception as e:
            app_logger.error(f"Error creating temporary playbook: {type(e).__name__}")
            # temp_inventory 
            if temp_inventory and temp_inventory.exists():
                try:
                    temp_inventory.unlink()
                except Exception:
                    pass
            return jsonify({
                'success': False,
                'error': 'Error preparing host check'
            }), 500
        
        # execution 
        # subprocess.run(), execution
        limit_host = host_name if 'host_name' in locals() else host
        
        # playbook (execution_id )
        generated_playbook_path = get_project_generated_playbook_path(project_id, execution_id)
        generated_playbook_path.parent.mkdir(parents=True, exist_ok=True)
        
        # playbook 
        import shutil
        shutil.copy2(temp_playbook, generated_playbook_path)
        app_logger.debug(f"[check_host] Copied playbook to: {generated_playbook_path}")
        
        # inventory 
        generated_inventory_path = get_project_generated_playbook_path(project_id, execution_id).parent / f'inventory_{execution_id}.yml'
        shutil.copy2(temp_inventory, generated_inventory_path)
        app_logger.debug(f"[check_host] Copied inventory to: {generated_inventory_path}")
        
        # ( )
        try:
            if temp_inventory.exists():
                temp_inventory.unlink()
        except Exception:
            pass
        try:
            if temp_playbook.exists():
                temp_playbook.unlink()
        except Exception:
            pass
        
        # execution record
        execution_data = {
            'status': 'QUEUED',
            'playbookName': f'host_check_{host}',
            'mode': 'HOST_CHECK',
            'runParams': {
                'temp_playbook': str(generated_playbook_path),
                'temp_inventory': str(generated_inventory_path),
                'inventory_files': selected_inventory_files,
                'ansible_config': str(ansible_config_path) if ansible_config_path.exists() else None,
                'project_dir': str(project_dir),
                'host': host,
                'limit_host': limit_host,
                'execution_type': 'HOST_CHECK',
                'temp_key_files': temp_key_files if 'temp_key_files' in locals() else []
            },
            'description': f'Check host availability: {host}'
        }
        
        execution_id_created = create_execution_record(execution_data, project_id=project_id, execution_id=execution_id)
        
        if not execution_id_created:
            app_logger.error(f"[check_host] Failed to create execution record")
            try:
                if generated_playbook_path.exists():
                    generated_playbook_path.unlink()
            except Exception:
                pass
            try:
                if generated_inventory_path.exists():
                    generated_inventory_path.unlink()
            except Exception:
                pass
            return jsonify({
                'success': False,
                'error': 'Failed to create execution record'
            }), 500
        
        app_logger.info(f"[check_host] Created execution {execution_id_created} for host check: {host}")
        
        # execution_id 
        return jsonify({
            'success': True,
            'executionId': execution_id_created,
            'message': 'Host check queued for execution',
            'status': 'QUEUED'
        })
            
    except Exception as e:
        if 'temp_inventory' in locals() and temp_inventory and temp_inventory.exists():
            try:
                temp_inventory.unlink()
            except Exception:
                pass
        if 'temp_playbook' in locals() and temp_playbook and temp_playbook.exists():
            try:
                temp_playbook.unlink()
            except Exception:
                pass
        app_logger.error(f"Critical error during host check: {type(e).__name__}")
        return jsonify({
            'success': False,
            'error': 'Internal error checking host'
        }), 500


def _build_one_host_check_entry(project_id, host, connection_secret_name, selected_inventory_files, project_dir, inventories_dir):
    """
 ( check_hosts).
 (inventory_key, entry_dict, temp_key_files) (None, None, []) .
 ( ).
    """
    combined_inventory = {'all': {'hosts': {}}}
    inventory_names_alt = {'inventory.yml': 'inventory.yaml', 'inventory.yaml': 'inventory.yml', 'hosts.yml': 'hosts.yaml', 'hosts.yaml': 'hosts.yml', 'hosts': 'hosts', 'hosts.ini': 'hosts.ini'}
    for inv_file_path in selected_inventory_files:
        if inv_file_path.startswith('inventories/') or inv_file_path.startswith('inventory/'):
            inv_path = project_dir / 'repo' / inv_file_path
        elif '/' in inv_file_path:
            inv_path = inventories_dir / inv_file_path
        else:
            inv_path = inventories_dir / inv_file_path
            if not inv_path.exists() and inv_file_path in inventory_names_alt:
                inv_path = inventories_dir / inventory_names_alt[inv_file_path]
            if not inv_path.exists() and inventories_dir.exists():
                for p in inventories_dir.rglob(inv_file_path):
                    if p.is_file():
                        inv_path = p
                        break
            if not inv_path.exists():
                inv_path = project_dir / 'repo' / inv_file_path
        if inv_path.exists():
            try:
                with open(inv_path, 'r', encoding='utf-8') as f:
                    inv_data = yaml.safe_load(f)
                    host_data = find_host_in_inventory(inv_data, host)
                    if host_data is not None:
                        combined_inventory['all']['hosts'][host] = host_data
                        break
            except Exception:
                pass
    if host not in combined_inventory['all']['hosts']:
        combined_inventory['all']['hosts'][host] = {}
    host_data = combined_inventory['all']['hosts'][host]
    temp_key_files = []
    host_vars_dir = get_project_host_vars_dir(project_id)
    host_name = host
    if 'ansible_host' in host_data:
        host_name = host_data.get('ansible_host', host)
    host_file = host_vars_dir / f"{host_name}.yml"
    if not host_file.exists() and host_name != host:
        host_file = host_vars_dir / f"{host}.yml"
        if host_file.exists():
            host_name = host
    host_vars_loaded = False
    entry = {}
    inv_key = host_name
    if host_file.exists():
        try:
            with open(host_file, 'r', encoding='utf-8') as f:
                host_vars = yaml_loader.load(f) or {}
            if (host_vars.get('ansible_ssh_private_key_file')
                    or host_vars.get('ansible_password')
                    or host_vars.get('connectionSecret')):
                host_vars_loaded = True
                # If credentials come from a login_password connection secret, resolve
                # username/password now so the entry has ansible_user + ansible_password
                # (previously only ssh_key secrets were resolved here).
                connection_secret_name_hv = host_vars.get('connectionSecret')
                if (connection_secret_name_hv
                        and not host_vars.get('ansible_ssh_private_key_file')
                        and not host_vars.get('ansible_password')):
                    try:
                        secrets_dir = get_project_secrets_dir(project_id)
                        ssh_keys_dir = secrets_dir / 'ssh_keys'
                        secret_file = ssh_keys_dir / f"{connection_secret_name_hv}.json"
                        if not secret_file.exists():
                            secret_file = secrets_dir / f"{connection_secret_name_hv}.json"
                        if secret_file.exists():
                            with open(secret_file, 'r', encoding='utf-8') as f:
                                secret_data = json.load(f)
                            host_vars = dict(host_vars)
                            if secret_data.get('type') == 'ssh_key' and secret_data.get('privateKey'):
                                import tempfile
                                temp_key_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.pem', dir=str(TEMP_DIR), encoding='utf-8')
                                temp_key_file.write(normalize_pem_key_for_file(secret_data['privateKey']))
                                temp_key_file.flush()
                                os.fsync(temp_key_file.fileno())
                                temp_key_file.close()
                                os.chmod(temp_key_file.name, 0o600)
                                temp_key_files.append(temp_key_file.name)
                                host_vars['ansible_ssh_private_key_file'] = os.path.abspath(temp_key_file.name)
                                if (secret_data.get('username') or '').strip():
                                    host_vars['ansible_user'] = secret_data['username'].strip()
                            elif secret_data.get('type') == 'login_password':
                                if secret_data.get('password'):
                                    host_vars['ansible_password'] = secret_data['password']
                                    host_vars['ansible_ssh_pass'] = secret_data['password']
                                if (secret_data.get('username') or '').strip():
                                    host_vars['ansible_user'] = secret_data['username'].strip()
                    except Exception:
                        pass
                inv_key = host_vars.get('ansible_host', host_name) or host_name
                connection_secret_name_hv = host_vars.get('connectionSecret')
                if connection_secret_name_hv and host_vars.get('ansible_ssh_private_key_file'):
                    secrets_dir = get_project_secrets_dir(project_id)
                    ssh_keys_dir = secrets_dir / 'ssh_keys'
                    secret_file = ssh_keys_dir / f"{connection_secret_name_hv}.json"
                    if not secret_file.exists():
                        secret_file = secrets_dir / f"{connection_secret_name_hv}.json"
                    if secret_file.exists():
                        with open(secret_file, 'r', encoding='utf-8') as f:
                            secret_data = json.load(f)
                        if secret_data.get('type') == 'ssh_key' and secret_data.get('privateKey'):
                            import tempfile
                            temp_key_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.pem', dir=str(TEMP_DIR), encoding='utf-8')
                            temp_key_file.write(normalize_pem_key_for_file(secret_data['privateKey']))
                            temp_key_file.flush()
                            os.fsync(temp_key_file.fileno())
                            temp_key_file.close()
                            os.chmod(temp_key_file.name, 0o600)
                            temp_key_files.append(temp_key_file.name)
                            host_vars = dict(host_vars)
                            host_vars['ansible_ssh_private_key_file'] = os.path.abspath(temp_key_file.name)
                            if secret_data.get('username', '').strip():
                                host_vars['ansible_user'] = secret_data['username'].strip()
                for key, value in host_vars.items():
                    if (key.startswith('ansible_') or key == 'ansible_host') and key != 'ansible_ssh_common_args':
                        entry[key] = value
        except Exception:
            pass
    if not host_vars_loaded:
        if not connection_secret_name or connection_secret_name in (None, '', 'null', 'undefined'):
            return None, None, []
        secrets_dir = get_project_secrets_dir(project_id)
        ssh_keys_dir = secrets_dir / 'ssh_keys'
        secret_file = ssh_keys_dir / f"{connection_secret_name}.json"
        if not secret_file.exists():
            secret_file = secrets_dir / f"{connection_secret_name}.json"
        if not secret_file.exists():
            raise ValueError(f"Secret {connection_secret_name} not found")
        with open(secret_file, 'r', encoding='utf-8') as f:
            secret_data = json.load(f)
        inv_key = host_data.get('ansible_host', host) or host
        entry['ansible_host'] = host_data.get('ansible_host', host) or host
        if secret_data.get('type') == 'ssh_key':
            private_key = secret_data.get('privateKey', '')
            if not private_key:
                raise ValueError(f"Private key is empty in secret {connection_secret_name}")
            username = (secret_data.get('username') or '').strip()
            if not username:
                raise ValueError(f"Username required in secret {connection_secret_name}")
            import tempfile
            temp_key_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.pem', dir=str(TEMP_DIR), encoding='utf-8')
            temp_key_file.write(normalize_pem_key_for_file(private_key))
            temp_key_file.close()
            os.chmod(temp_key_file.name, 0o600)
            temp_key_files.append(temp_key_file.name)
            entry['ansible_ssh_private_key_file'] = temp_key_file.name
            entry['ansible_user'] = username
        elif secret_data.get('type') == 'login_password':
            username = (secret_data.get('username') or '').strip()
            if not username:
                raise ValueError(f"Username required in secret {connection_secret_name}")
            password = secret_data.get('password', '')
            if not password:
                raise ValueError(f"Password empty in secret {connection_secret_name}")
            entry['ansible_user'] = username
            entry['ansible_password'] = password
        else:
            raise ValueError(f"Unsupported secret type: {secret_data.get('type')}")
    entry['ansible_ssh_common_args'] = '-o StrictHostKeyChecking=accept-new'
    return inv_key, entry, temp_key_files


@bp.route('/api/check_hosts', methods=['POST'])
@require_auth
def check_hosts():
    """ ( execution)."""
    try:
        data = request.json or {}
        hosts = data.get('hosts')
        if not hosts or not isinstance(hosts, list):
            return jsonify({'success': False, 'error': 'hosts must be a non-empty list'}), 400
        hosts = [str(h).strip() for h in hosts if str(h).strip()]
        if not hosts:
            return jsonify({'success': False, 'error': 'hosts must be a non-empty list'}), 400
        project_id = data.get('project_id') or get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        selected_ansible_config = data.get('ansible_config') or 'ansible.cfg'
        project_dir = get_project_dir(project_id)
        ansible_config_path = resolve_ansible_config_path(project_id, selected_ansible_config)
        ansible_config_path.parent.mkdir(parents=True, exist_ok=True)
        if not ansible_config_path.exists() or not ansible_config_path.is_file():
            if not create_minimal_ansible_config(ansible_config_path):
                pass
        selected_inventory_files = data.get('inventory_files', ['inventory.yml'])
        connection_secrets = data.get('connection_secrets') or {}
        inventories_dir = get_project_inventories_dir(project_id)
        combined = {'all': {'hosts': {}}}
        all_temp_key_files = []
        hosts_resolved = []
        for host in hosts:
            secret_name = connection_secrets.get(host)
            if secret_name is None or (isinstance(secret_name, str) and secret_name.strip() in ('', 'null', 'undefined')):
                secret_name, has_conn = resolve_connection_secret_for_host(project_id, host)
                if not has_conn and not secret_name:
                    app_logger.debug(f"[check_hosts] Skipping host {host}: no connection")
                    continue
            try:
                inv_key, entry, keys = _build_one_host_check_entry(
                    project_id, host, secret_name, selected_inventory_files, project_dir, inventories_dir
                )
            except Exception as e:
                app_logger.warning(f"[check_hosts] Host {host}: {e}")
                continue
            if inv_key is None:
                continue
            combined['all']['hosts'][inv_key] = entry
            all_temp_key_files.extend(keys)
            hosts_resolved.append(inv_key)
        if not hosts_resolved:
            return jsonify({'success': False, 'error': 'No hosts with configured connection to check'}), 400
        temp_dir = TEMP_DIR
        temp_dir.mkdir(exist_ok=True)
        execution_id = str(uuid.uuid4())
        generated_playbooks_dir = get_project_generated_playbook_path(project_id, execution_id).parent
        generated_playbooks_dir.mkdir(parents=True, exist_ok=True)
        key_path_map = _copy_keys_to_generated_playbooks_dedup(all_temp_key_files, generated_playbooks_dir)
        for _h, hdata in combined.get('all', {}).get('hosts', {}).items():
            k = hdata.get('ansible_ssh_private_key_file')
            if k and k in key_path_map:
                hdata['ansible_ssh_private_key_file'] = key_path_map[k]
        temp_inventory = temp_dir / f'check_hosts_{uuid.uuid4().hex[:8]}.yml'
        with open(temp_inventory, 'w', encoding='utf-8') as f:
            yaml.dump(combined, f, default_flow_style=False, allow_unicode=True)
        playbook_content = """---
- hosts: all
  gather_facts: no
  tasks:
    - name: Test SSH connectivity to host
      ansible.builtin.raw: echo opensible_host_check_ok
      changed_when: false
        """

        temp_playbook = temp_dir / f'check_hosts_playbook_{uuid.uuid4().hex[:8]}.yaml'
        with open(temp_playbook, 'w', encoding='utf-8') as f:
            f.write(playbook_content)

        generated_playbook_path = get_project_generated_playbook_path(project_id, execution_id)
        generated_inventory_path = generated_playbook_path.parent / f'inventory_{execution_id}.yml'
        import shutil
        shutil.copy2(temp_playbook, generated_playbook_path)
        shutil.copy2(temp_inventory, generated_inventory_path)
        try:
            temp_inventory.unlink()
        except Exception:
            pass
        try:
            temp_playbook.unlink()
        except Exception:
            pass
        execution_data = {
            'status': 'QUEUED',
            'playbookName': f'host_check_{len(hosts_resolved)}_hosts',
            'mode': 'HOST_CHECK',
            'runParams': {
                'temp_playbook': str(generated_playbook_path),
                'temp_inventory': str(generated_inventory_path),
                'inventory_files': selected_inventory_files,
                'ansible_config': str(ansible_config_path) if ansible_config_path.exists() else None,
                'project_dir': str(project_dir),
                'hosts': hosts_resolved,
                'execution_type': 'HOST_CHECK',
                'temp_key_files': all_temp_key_files,
            },
            'description': f'Check host availability: {", ".join(hosts_resolved[:5])}{"..." if len(hosts_resolved) > 5 else ""}'
        }
        execution_id_created = create_execution_record(execution_data, project_id=project_id, execution_id=execution_id)
        if not execution_id_created:
            try:
                generated_playbook_path.unlink(missing_ok=True)
                generated_inventory_path.unlink(missing_ok=True)
            except Exception:
                pass
            return jsonify({'success': False, 'error': 'Failed to create execution record'}), 500
        app_logger.info(f"[check_hosts] Created execution {execution_id_created} for {len(hosts_resolved)} hosts")
        return jsonify({
            'success': True,
            'executionId': execution_id_created,
            'message': f'Host check queued for {len(hosts_resolved)} host(s)',
            'status': 'QUEUED'
        })
    except Exception as e:
        app_logger.error(f"[check_hosts] Error: {type(e).__name__}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


