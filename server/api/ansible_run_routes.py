"""Ansible run / status / stop / host-facts routes (Phase 3 extraction).

Extracted from backend/app.py. Uses lazy proxies to avoid circular imports.
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from subprocess import PIPE, STDOUT
import threading

import yaml
from flask import Blueprint, current_app, jsonify, request

from auth.middleware import require_auth

bp = Blueprint("ansible_run_api", __name__)


def _app_module():
    """Return the imported backend.app module (lazy)."""
    for module_name in ("__main__", "app"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "get_project_id_from_request"):
            return module
    return importlib.import_module("app")


class _AppLogger:
    def __getattr__(self, name):
        return getattr(current_app.logger, name)


app_logger = _AppLogger()


_APP_NAMES = {
    "get_project_id_from_request",
    "get_inventory_groups",
    "get_project_dir",
    "get_project_inventories_dir",
    "get_project_inventory_file",
    "get_project_secrets_dir",
    "get_project_generated_playbook_path",
    "create_execution_record",
    "update_execution_record",
    "append_execution_log",
    "load_execution_settings",
    "generate_dynamic_playbook",
    "apply_retention_policy",
    "create_minimal_ansible_config",
    "get_project_host_vars_dir",
    "get_project_group_vars_dir",
    "resolve_ansible_config_path",
    "resolve_repo_path",
    "find_host_in_inventory",
    "yaml_loader",
    "BASE_DIR",
    "TEMP_DIR",
}


def __getattr__(name):
    if name in _APP_NAMES:
        return getattr(_app_module(), name)
    raise AttributeError(name)


class _LazyAppProxy:
    def __init__(self, name):
        self._name = name

    def _resolve(self):
        return getattr(_app_module(), self._name)

    def __call__(self, *args, **kwargs):
        return self._resolve()(*args, **kwargs)

    def __getattr__(self, item):
        return getattr(self._resolve(), item)

    def __fspath__(self):
        return str(self._resolve())

    def __str__(self):
        return str(self._resolve())

    def __truediv__(self, other):
        return self._resolve() / other


for _name in _APP_NAMES:
    globals()[_name] = _LazyAppProxy(_name)


@bp.route('/api/run_ansible', methods=['POST'])
@require_auth
def run_ansible():
    """ Ansible playbook"""
    ansible_output = _app_module().ansible_output
    
    try:
        # , 
        if ansible_output['status'] == 'running':
            app_logger.warning("Attempt to start Ansible while process is already running")
            return jsonify({'success': False, 'error': 'Ansible is already running'}), 400
        
        # project_id - REQUIRED, no fallback
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        # , inventory 
        data = request.json or {}
        selected_hosts = data.get('hosts', [])
        selected_hosts = ['gitlab_ce' if host == 'gitlab-ce' else host for host in selected_hosts]
        selected_roles = data.get('roles', [])
        selected_inventory_files = data.get('inventory_files', [])
        selected_ansible_config = data.get('ansible_config')
        if not selected_ansible_config:
            app_logger.error(f"[run_ansible] ansible_config not provided in request")
            return jsonify({'success': False, 'error': 'ansible_config is required'}), 400
        app_logger.info(f"User starting Ansible playbook: project={project_id}, hosts={selected_hosts}, roles={selected_roles}, inventory={selected_inventory_files}")
        
        # execution record
        execution_id = None
        settings = load_execution_settings()
        if settings.get('save_history', True):
            # snapshot inventory
            inventory_snapshot = {'groups': []}
            try:
                groups = get_inventory_groups(selected_inventory_files if selected_inventory_files else None)
                for group_name, group_data in groups.items():
                    group_hosts = group_data.get('hosts', [])
                    inventory_snapshot['groups'].append({
                        'groupId': group_name,
                        'groupName': group_name,
                        'hosts': [{'hostId': host, 'ip': host} for host in group_hosts]
                    })
            except Exception as e:
                app_logger.warning(f"Error creating inventory snapshot: {e}")
            
            # snapshot selection
            selection_snapshot = {
                'selectedHostIds': selected_hosts,
                'mandatoryRoleIds': ['00_init', '02_init_sshd', '08_configure_security']
            }
            
            # effective roles by host ( )
            effective_roles_by_host = {}
            for host in selected_hosts:
                effective_roles_by_host[host] = selected_roles
            
            execution_data = {
                'playbookName': 'dynamic_playbook',
                'mode': 'ALL_SELECTED_HOSTS',
                'inventorySnapshot': inventory_snapshot,
                'selectionSnapshot': selection_snapshot,
                'stats': {
                    'hostsTargeted': len(selected_hosts),
                    'totalRoleExecutions': len(selected_roles) * len(selected_hosts)
                },
                'warnings': []
            }
            
            execution_id = create_execution_record(execution_data, project_id=project_id)
            if execution_id:
                ansible_output['execution_id'] = execution_id
                ansible_output['project_id'] = project_id
                app_logger.debug(f"Created execution record: {execution_id} for project {project_id}")
        
        if not selected_hosts:
            app_logger.warning("Attempt to run Ansible without selected hosts")
            return jsonify({'success': False, 'error': 'No hosts selected'}), 400
        
        if not selected_roles:
            app_logger.warning("Attempt to run Ansible without selected roles")
            return jsonify({'success': False, 'error': 'No roles selected'}), 400
        
        # inventory , 
        if not selected_inventory_files:
            selected_inventory_files = ['inventory.yml']
        
        # playbook
        playbook_content = generate_dynamic_playbook(selected_roles)
        
        # playbook runtime/generated_playbooks/<execution_id>.yml
        temp_playbook = None
        if execution_id:
            generated_playbook_path = get_project_generated_playbook_path(project_id, execution_id)
            with open(generated_playbook_path, 'w', encoding='utf-8') as f:
                f.write(playbook_content)
            temp_playbook = generated_playbook_path
        else:
            # Fallback: execution_id , 
            temp_playbook = TEMP_DIR / f'playbook_{uuid.uuid4().hex[:8]}.yaml'
            temp_playbook.parent.mkdir(exist_ok=True)
            with open(temp_playbook, 'w', encoding='utf-8') as f:
                f.write(playbook_content)
        
        # Get group_vars from Project Storage
        project_group_vars_dir = get_project_group_vars_dir(project_id)
        group_vars_file = project_group_vars_dir / 'all.yml'
        
        # Get inventory directory from Project Storage
        project_inventory_dir = get_project_inventory_file(project_id).parent
        
        # inventory 
        temp_inventory = None
        inventory_args = []
        
        if len(selected_inventory_files) == 1:
            # , Project Storage
            inv_path = project_inventory_dir / selected_inventory_files[0]
            if inv_path.exists():
                inventory_args = ['-i', str(inv_path)]
            else:
                # Fallback to default inventory.yml in project storage
                default_inv = get_project_inventory_file(project_id)
                if default_inv.exists():
                    inventory_args = ['-i', str(default_inv)]
                else:
                    return jsonify({'success': False, 'error': f'Inventory file not found in project storage: {selected_inventory_files[0]}'}), 400
        elif len(selected_inventory_files) > 1:
            # , Project Storage
            temp_inventory = TEMP_DIR / f'inventory_{uuid.uuid4().hex[:8]}.yml'
            temp_inventory.parent.mkdir(exist_ok=True)
            
            combined_inventory = {'all': {'hosts': {}}}
            
            for inv_file in selected_inventory_files:
                inv_path = project_inventory_dir / inv_file
                if inv_path.exists():
                    try:
                        with open(inv_path, 'r', encoding='utf-8') as f:
                            inv_data = yaml.safe_load(f)
                            if inv_data and 'all' in inv_data and 'hosts' in inv_data['all']:
                                for host, config in inv_data['all']['hosts'].items():
                                    if host not in combined_inventory['all']['hosts']:
                                        combined_inventory['all']['hosts'][host] = config
                    except Exception as e:
                        app_logger.warning(f"Error reading inventory file {inv_file}: {e}")
            
            # inventory
            with open(temp_inventory, 'w', encoding='utf-8') as f:
                yaml.dump(combined_inventory, f, default_flow_style=False, allow_unicode=True)
            
            inventory_args = ['-i', str(temp_inventory)]
            ansible_output['temp_inventory'] = str(temp_inventory)
        else:
            # , Project Storage
            default_inv = get_project_inventory_file(project_id)
            if not default_inv.exists():
                return jsonify({'success': False, 'error': 'Default inventory.yml not found in project storage'}), 400
            inventory_args = ['-i', str(default_inv)]
        
        # Validate host names — they end up in --limit and as host_vars filenames.
        import re as _re_hosts
        _safe_host_re = _re_hosts.compile(r'^[A-Za-z0-9_.\-]+$')
        for _h in selected_hosts:
            if not isinstance(_h, str) or not _safe_host_re.match(_h):
                return jsonify({'success': False, 'error': f'Invalid host name: {_h!r}'}), 400

        cmd = [
            'ansible-playbook',
            str(temp_playbook),
            *inventory_args,
            '-e', f'project_root={BASE_DIR}',
            '--limit', ','.join(selected_hosts)
        ]
        
        # group_vars/all.yml 
        if group_vars_file.exists():
            cmd.extend(['-e', f'@{group_vars_file}'])
        
        env = os.environ.copy()
        env['GIT_SSH_COMMAND'] = 'ssh -o StrictHostKeyChecking=accept-new'
        env['JOB_NAME'] = 'redis_prepare'
        # Get ansible config ( ansible.cfg — )
        project_dir = get_project_dir(project_id)
        ansible_config_path = resolve_ansible_config_path(project_id, selected_ansible_config)
        ansible_config_path.parent.mkdir(parents=True, exist_ok=True)
        if not ansible_config_path.exists() or not ansible_config_path.is_file():
            app_logger.debug(f"[run_ansible] Config file not found: {ansible_config_path}, creating minimal config")
            if not create_minimal_ansible_config(ansible_config_path):
                app_logger.warning(f"[run_ansible] Failed to create minimal config, continuing without ANSIBLE_CONFIG")
        
        ansible_config_used = None
        app_logger.debug(f"[run_ansible] === Ansible Config Resolution ===")
        app_logger.debug(f"[run_ansible] Selected config file: {selected_ansible_config}")
        app_logger.debug(f"[run_ansible] Project directory: {project_dir}")
        app_logger.debug(f"[run_ansible] Resolved config path: {ansible_config_path}")
        
        if ansible_config_path.exists() and ansible_config_path.is_file():
            env['ANSIBLE_CONFIG'] = str(ansible_config_path)
            ansible_config_used = str(ansible_config_path)
            app_logger.debug(f"[run_ansible] ✓ Using Ansible config: {ansible_config_used}")
        else:
            app_logger.warning(f"[run_ansible] ✗ Ansible config {selected_ansible_config} not found (requested: {ansible_config_path})")
            if not ansible_config_path.parent.exists():
                app_logger.warning(f"[run_ansible] Directory {ansible_config_path.parent} does not exist")
            app_logger.warning(f"[run_ansible] Using default Ansible config")
            ansible_config_used = "default (system default)"
        
        # Prefer the project's resolved Ansible roles path, including IaC/ansible/roles.
        project_roles_path = resolve_repo_path(project_id, 'roles')
        if project_roles_path.exists():
            # ANSIBLE_ROLES_PATH 
            existing_roles_path = env.get('ANSIBLE_ROLES_PATH', '')
            if existing_roles_path:
                env['ANSIBLE_ROLES_PATH'] = f"{str(project_roles_path)}:{existing_roles_path}"
            else:
                env['ANSIBLE_ROLES_PATH'] = str(project_roles_path)
            app_logger.debug(f"[run_ansible] Set ANSIBLE_ROLES_PATH: {env['ANSIBLE_ROLES_PATH']}")
        else:
            app_logger.warning(f"[run_ansible] Roles directory not found: {project_roles_path}")
        
        # ansible-playbook
        cmd_str = ' '.join([f'"{arg}"' if ' ' in str(arg) else str(arg) for arg in cmd])
        
        log_details = [
            f"=== Ansible Playbook Execution Details ===",
            f"Project ID: {project_id}",
            f"Ansible Config: {ansible_config_used}",
            f"Selected Config File: {selected_ansible_config}",
            f"Selected Hosts: {', '.join(selected_hosts)}",
            f"Selected Roles: {', '.join(selected_roles)}",
            f"Inventory Files: {', '.join(selected_inventory_files) if selected_inventory_files else 'default inventory.yml'}",
            f"Playbook File: {temp_playbook}",
            f"Group Vars File: {group_vars_file if group_vars_file.exists() else 'not used'}",
            f"Working Directory: {BASE_DIR}",
            f"",
            f"Command: {cmd_str}",
            f"",
            f"Environment Variables:"
        ]
        
        # , Ansible
        ansible_env_vars = ['ANSIBLE_CONFIG', 'ANSIBLE_ROLES_PATH', 'GIT_SSH_COMMAND', 'JOB_NAME', 'ANSIBLE_HOST_KEY_CHECKING', 
                           'ANSIBLE_SSH_ARGS', 'ANSIBLE_FORCE_COLOR', 'ANSIBLE_NOCOLOR']
        for key in ansible_env_vars:
            if key in env:
                log_details.append(f"  {key}={env[key]}")
        
        # (DEBUG )
        full_log_message = '\n'.join(log_details)
        app_logger.debug(full_log_message)
        
        # execution record UI
        if execution_id:
            append_execution_log(execution_id, full_log_message, project_id=project_id)
        
        ansible_output['output'] = ''
        ansible_output['status'] = 'running'
        ansible_output['start_time'] = time.time()
        ansible_output['temp_playbook'] = str(temp_playbook)
        
        def run_ansible_process():
            ansible_output = _app_module().ansible_output
            temp_file_path = Path(ansible_output.get('temp_playbook', ''))
            try:
                # (DEBUG )
                cmd_str = ' '.join([f'"{arg}"' if ' ' in str(arg) else str(arg) for arg in cmd])
                app_logger.debug(f"[run_ansible_process] ========== Starting ansible-playbook ==========")
                app_logger.debug(f"[run_ansible_process] Full command: {cmd_str}")
                app_logger.debug(f"[run_ansible_process] Working directory (cwd): {project_dir}")
                app_logger.debug(f"[run_ansible_process] ANSIBLE_CONFIG: {env.get('ANSIBLE_CONFIG', 'not set')}")
                app_logger.debug(f"[run_ansible_process] ===========================================")
                
                # project_dir , Ansible group_vars host_vars
                # playbook , 
                cmd_with_abs_paths = []
                i = 0
                while i < len(cmd):
                    arg = cmd[i]
                    # -i, - inventory
                    if arg == '-i' and i + 1 < len(cmd):
                        cmd_with_abs_paths.append(arg)
                        inv_path = Path(cmd[i + 1])
                        cmd_with_abs_paths.append(str(inv_path.resolve()))
                        i += 2
                    elif arg == str(temp_playbook):
                        # playbook 
                        cmd_with_abs_paths.append(str(temp_playbook.resolve()))
                        i += 1
                    else:
                        cmd_with_abs_paths.append(arg)
                        i += 1
                
                process = subprocess.Popen(
                    cmd_with_abs_paths,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True,
                    cwd=str(project_dir),
                    env=env
                )
                
                ansible_output['process'] = process
                app_logger.debug(f"[run_ansible_process] Process started, PID: {process.pid}")
                
                output_lines = []
                for line in process.stdout:
                    line = line.rstrip()
                    output_lines.append(line)
                    ansible_output['output'] = '\n'.join(output_lines)
                    
                    # execution record
                    if ansible_output.get('execution_id'):
                        append_execution_log(ansible_output['execution_id'], line, project_id=project_id)
                
                process.wait()
                
                return_code = process.returncode
                app_logger.debug(f"[run_ansible_process] Process completed with return code: {return_code}")
                
                # execution record
                if ansible_output.get('execution_id'):
                    status_message = f"\n=== Execution completed with return code: {return_code} ==="
                    if return_code == 0:
                        status_message += " (SUCCESS)"
                    else:
                        status_message += f" (FAILED)"
                    append_execution_log(ansible_output['execution_id'], status_message, project_id=project_id)
                
                if ansible_output.get('start_time'):
                    ansible_output['end_time'] = time.time()
                    ansible_output['elapsed_time'] = int(ansible_output['end_time'] - ansible_output['start_time'])
                
                if process.returncode == 0:
                    ansible_output['status'] = 'completed'
                    final_status = 'SUCCESS'
                else:
                    ansible_output['status'] = 'failed'
                    final_status = 'FAILED'
                
                # execution record
                if ansible_output.get('execution_id'):
                    finished_at = time.time()
                    update_data = {
                        'status': final_status,
                        'finishedAt': finished_at
                    }
                    # duration 
                    if ansible_output.get('elapsed_time'):
                        update_data['duration'] = ansible_output['elapsed_time']
                    update_execution_record(ansible_output['execution_id'], update_data, project_id=project_id)
                    # retention policy 
                    apply_retention_policy()
                    
            except Exception as e:
                ansible_output['status'] = 'error'
                error_msg = f'\nError: {str(e)}'
                ansible_output['output'] += error_msg
                
                if ansible_output.get('execution_id'):
                    append_execution_log(ansible_output['execution_id'], error_msg, project_id=project_id)
                    update_execution_record(ansible_output['execution_id'], {
                        'status': 'FAILED',
                        'finishedAt': time.time()
                    }, project_id=project_id)
                    apply_retention_policy()
                
                if ansible_output.get('start_time'):
                    ansible_output['end_time'] = time.time()
                    ansible_output['elapsed_time'] = int(ansible_output['end_time'] - ansible_output['start_time'])
            finally:
                ansible_output['process'] = None
                try:
                    if temp_file_path and temp_file_path.exists():
                        temp_file_path.unlink()
                except Exception:
                    pass
                try:
                    temp_inv_path = ansible_output.get('temp_inventory')
                    if temp_inv_path:
                        inv_path = Path(temp_inv_path)
                        if inv_path.exists():
                            inv_path.unlink()
                except Exception:
                    pass
                ansible_output['temp_playbook'] = None
                ansible_output['temp_inventory'] = None
        
        thread = threading.Thread(target=run_ansible_process, daemon=True)
        thread.start()
        
        response_data = {
            'success': True,
            'message': f'Ansible started on hosts: {", ".join(selected_hosts)}'
        }
        
        if execution_id:
            response_data['executionId'] = execution_id
        
        return jsonify(response_data)
        
    except Exception as e:
        ansible_output['status'] = 'error'
        ansible_output['output'] = str(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/ansible_status', methods=['GET'])
@require_auth
def get_ansible_status():
    """ Ansible"""
    ansible_output = _app_module().ansible_output
    elapsed_time = 0
    
    # , 
    if ansible_output.get('elapsed_time') is not None:
        elapsed_time = ansible_output['elapsed_time']
    elif ansible_output.get('start_time') and ansible_output['status'] == 'running':
        # , 
        elapsed_time = int(time.time() - ansible_output['start_time'])
    
    return jsonify({
        'success': True,
        'status': ansible_output['status'],
        'output': ansible_output['output'],
        'elapsed_time': elapsed_time
    })


@bp.route('/api/stop_ansible', methods=['POST'])
@require_auth
def stop_ansible():
    """ Ansible"""
    ansible_output = _app_module().ansible_output
    
    try:
        if ansible_output['process']:
            ansible_output['process'].terminate()
            ansible_output['status'] = 'stopped'
            ansible_output['output'] += '\n\n[Stopped by user]'
            return jsonify({'success': True, 'message': 'Ansible stopped'})
        else:
            return jsonify({'success': False, 'error': 'Process is not running'}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==== Inventory file management routes moved to api/inventory_files_routes.py ====

# Ansible config routes moved to api/ansible_cfg_routes.py



# ==== Group vars / host vars routes moved to api/group_host_vars_routes.py ====
@bp.route('/api/hosts/<host_name>/facts', methods=['POST'])
@require_auth
def get_host_facts(host_name):
    """ Ansible facts gather_facts"""
    try:
        data = request.json or {}
        project_id = data.get('project_id') or get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        app_logger.info(f"[get_host_facts] Received request for host: {host_name}, project_id: {project_id}")
        
        # inventory, check_host
        # ( check_host)
        
        # Get ansible config ( ansible.cfg — )
        selected_ansible_config = data.get('ansible_config') or 'ansible.cfg'
        project_dir = get_project_dir(project_id)
        ansible_config_path = resolve_ansible_config_path(project_id, selected_ansible_config)
        ansible_config_path.parent.mkdir(parents=True, exist_ok=True)
        if not ansible_config_path.exists():
            create_minimal_ansible_config(ansible_config_path)
        
        # inventory
        inventory_files = data.get('inventory_files', ['inventory.yml'])
        project_dir = get_project_dir(project_id)
        combined_inventory = {'all': {'hosts': {}}}
        
        for inv_file_path in inventory_files:
            # repo (, inventories/prod/hosts.yml)
            # (, inventory.yml)
            if inv_file_path.startswith('inventories/') or inv_file_path.startswith('inventory/'):
                # repo
                inv_path = project_dir / 'repo' / inv_file_path
            elif '/' in inv_file_path:
                # , inventories/
                inv_path = project_dir / 'repo' / 'inventories' / inv_file_path
            else:
                # - get_project_inventories_dir 
                inventories_dir = get_project_inventories_dir(project_id)
                inv_path = inventories_dir / inv_file_path
                # , repo
                if not inv_path.exists():
                    inv_path = project_dir / 'repo' / inv_file_path
            
            if inv_path.exists():
                try:
                    with open(inv_path, 'r', encoding='utf-8') as f:
                        inv_data = yaml.safe_load(f)
                        host_data = find_host_in_inventory(inv_data, host_name)
                        if host_data is not None:
                            combined_inventory['all']['hosts'][host_name] = host_data
                            break
                except Exception as e:
                    app_logger.warning(f"Error reading inventory file {inv_file_path}: {type(e).__name__}: {e}")
        
        if host_name not in combined_inventory['all']['hosts']:
            combined_inventory['all']['hosts'][host_name] = {}
        
        host_data = combined_inventory['all']['hosts'][host_name]
        host_name_actual = host_name
        if 'ansible_host' in host_data:
            host_name_actual = host_data.get('ansible_host', host_name)
        
        # host_vars
        host_vars_dir = get_project_host_vars_dir(project_id)
        host_file = host_vars_dir / f"{host_name_actual}.yml"
        if not host_file.exists() and host_name_actual != host_name:
            host_file = host_vars_dir / f"{host_name}.yml"
            if host_file.exists():
                host_name_actual = host_name
        
        temp_key_files = []
        host_vars_loaded = False
        secret_username = None
        
        if host_file.exists():
            try:
                with open(host_file, 'r', encoding='utf-8') as f:
                    host_vars = yaml_loader.load(f) or {}
                
                if host_vars.get('ansible_ssh_private_key_file') or host_vars.get('ansible_password') or host_vars.get('connectionSecret'):
                    host_vars_loaded = True
                    
                    # connectionSecret, 
                    connection_secret_name = host_vars.get('connectionSecret')
                    if connection_secret_name:
                        try:
                            secrets_dir = get_project_secrets_dir(project_id)
                            ssh_keys_dir = secrets_dir / 'ssh_keys'
                            
                            # (secrets/ssh_keys/)
                            secret_file = ssh_keys_dir / f"{connection_secret_name}.json"
                            if not secret_file.exists():
                                # Fallback 
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
                                        # ansible_user , ( check_host)
                                        # , .. host_vars / root,
                                        # connection secret ( localuser).
                                        secret_username_from_secret = str(secret_data.get('username') or '').strip()
                                        if secret_username_from_secret:
                                            host_vars['ansible_user'] = secret_username_from_secret

                                        import tempfile
                                        temp_key_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.pem', dir=str(TEMP_DIR), encoding='utf-8')
                                        key_content = private_key
                                        if not key_content.endswith('\n'):
                                            key_content = key_content + '\n'
                                        temp_key_file.write(key_content)
                                        temp_key_file.flush()
                                        os.fsync(temp_key_file.fileno())
                                        temp_key_file.close()
                                        os.chmod(temp_key_file.name, 0o600)
                                        temp_key_files.append(temp_key_file.name)
                                        host_vars['ansible_ssh_private_key_file'] = os.path.abspath(temp_key_file.name)
                                elif secret_data.get('type') == 'login_password':
                                    password = secret_data.get('password', '')
                                    if password:
                                        secret_username_from_secret = str(secret_data.get('username') or '').strip()
                                        if secret_username_from_secret:
                                            host_vars['ansible_user'] = secret_username_from_secret
                                        host_vars['ansible_password'] = password
                                        host_vars['ansible_ssh_pass'] = password
                        except Exception as e:
                            app_logger.warning(f"Error creating temporary key from secret: {e}")
                    
                    # simple_inventory
                    ansible_host_from_vars = host_vars.get('ansible_host', host_name_actual)
                    simple_inventory = {
                        'all': {
                            'hosts': {
                                ansible_host_from_vars: {}
                            }
                        }
                    }
                    
                    for key, value in host_vars.items():
                        if key.startswith('ansible_') or key == 'ansible_host':
                            simple_inventory['all']['hosts'][ansible_host_from_vars][key] = value
                    
                    secret_username = host_vars.get('ansible_user', 'root')
                    host_name_actual = ansible_host_from_vars
            except Exception as e:
                app_logger.warning(f"Error reading host vars for {host_name}: {e}")
        
        if not host_vars_loaded:
            # inventory
            simple_inventory = {
                'all': {
                    'hosts': {
                        host_name_actual: {}
                    }
                }
            }
            simple_inventory['all']['hosts'][host_name_actual]['ansible_host'] = host_data.get('ansible_host', host_name)
            for key, value in host_data.items():
                if key.startswith('ansible_') or key == 'ansible_host':
                    if key != 'ansible_ssh_common_args':
                        simple_inventory['all']['hosts'][host_name_actual][key] = value
        
        # inventory 
        temp_inventory = TEMP_DIR / f'facts_inventory_{uuid.uuid4().hex[:8]}.yml'
        temp_inventory.parent.mkdir(exist_ok=True)
        with open(temp_inventory, 'w', encoding='utf-8') as f:
            yaml.dump(simple_inventory, f, default_flow_style=False, allow_unicode=True)
        
        # ansible ad-hoc setup facts JSON
        env = os.environ.copy()
        env['GIT_SSH_COMMAND'] = 'ssh -o StrictHostKeyChecking=accept-new'
        if ansible_config_path.exists() and ansible_config_path.is_file():
            env['ANSIBLE_CONFIG'] = str(ansible_config_path)
        
        # password auth, sshpass
        check_host_data = simple_inventory.get('all', {}).get('hosts', {}).get(host_name_actual, {})
        if secret_username and ('ansible_password' in check_host_data or 'ansible_ssh_pass' in check_host_data):
            try:
                import shutil
                sshpass_path = shutil.which('sshpass')
                if sshpass_path:
                    current_path = env.get('PATH', '')
                    sshpass_dir = str(Path(sshpass_path).parent)
                    if sshpass_dir not in current_path:
                        env['PATH'] = f"{sshpass_dir}:{current_path}"
            except Exception as e:
                app_logger.warning(f"[get_host_facts] Error checking sshpass: {e}")
        
        # playbook gather_facts
        temp_playbook = TEMP_DIR / f'get_facts_playbook_{uuid.uuid4().hex[:8]}.yaml'
        
        if not secret_username:
            secret_username = check_host_data.get('ansible_user', 'root')
        
        playbook_content = f"""---
- hosts: all
  remote_user: {secret_username}
  gather_facts: no
  vars:
    ansible_executable: /bin/sh
    ansible_python_interpreter: /usr/bin/python3
  tasks:
    - name: Gather facts
      ansible.builtin.setup:
      register: facts_result
      
    - name: Convert facts to JSON string
      ansible.builtin.set_fact:
        facts_json: "{{{{ facts_result.ansible_facts | to_json }}}}"
      
    - name: Output facts as JSON
      ansible.builtin.debug:
        msg: "{{{{ facts_json }}}}"
        """
        
        try:
            with open(temp_playbook, 'w', encoding='utf-8') as f:
                f.write(playbook_content)
        except Exception as e:
            app_logger.error(f"[get_host_facts] Failed to create playbook: {e}")
            if temp_inventory.exists():
                try:
                    temp_inventory.unlink()
                except Exception:
                    pass
            return jsonify({
                'success': False,
                'error': 'Failed to create playbook for facts gathering'
            }), 500
        
        # execution 
        execution_id = str(uuid.uuid4())
        generated_playbook_path = get_project_generated_playbook_path(project_id, execution_id)
        generated_playbook_path.parent.mkdir(parents=True, exist_ok=True)
        
        import shutil
        shutil.copy2(temp_playbook, generated_playbook_path)
        generated_inventory_path = generated_playbook_path.parent / f'inventory_{execution_id}.yml'
        shutil.copy2(temp_inventory, generated_inventory_path)
        
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
            'playbookName': f'get_facts_{host_name}',
            'mode': 'HOST_FACTS',
            'runParams': {
                'temp_playbook': str(generated_playbook_path),
                'temp_inventory': str(generated_inventory_path),
                'inventory_files': inventory_files,
                'ansible_config': str(ansible_config_path) if ansible_config_path.exists() else None,
                'project_dir': str(project_dir),
                'host': host_name,
                'limit_host': host_name_actual,
                'execution_type': 'HOST_FACTS',
                'temp_key_files': temp_key_files
            },
            'description': f'Get host facts: {host_name}'
        }
        
        execution_id_created = create_execution_record(execution_data, project_id=project_id)
        
        if not execution_id_created:
            app_logger.error(f"[get_host_facts] Failed to create execution record")
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
        
        app_logger.info(f"[get_host_facts] Created execution {execution_id_created} for get facts: {host_name}")
        
        # execution_id 
        return jsonify({
            'success': True,
            'executionId': execution_id_created,
            'message': 'Get facts queued for execution',
            'status': 'QUEUED'
        })
            
    except Exception as e:
        app_logger.error(f"[get_host_facts] Critical error: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal error gathering facts'
        }), 500


