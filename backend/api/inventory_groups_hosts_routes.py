"""Inventory groups + hosts + connection-secret routes blueprint.

Extracted from ``backend/app.py`` during the phased refactor. URLs, methods,
view-function names, response shapes, and auth decorators are preserved.

Helpers that still live in ``app.py`` (``yaml_loader``,
``get_project_host_vars_dir``, ``get_project_group_vars_dir``) are resolved
lazily via the initialized app module to avoid circular imports.
"""
from __future__ import annotations

import sys
import json
import os
from pathlib import Path

from flask import Blueprint, jsonify, request, current_app

try:
    from auth.middleware import require_auth
except ImportError:  # pragma: no cover
    from ..auth.middleware import require_auth

from utils.paths import safe_child_path as _safe_child_path
from utils.yaml_io import create_backup
from utils.project_paths import (
    get_project_dir,
    get_project_inventories_dir,
    get_project_inventory_file,
    get_project_secrets_dir,
)
from utils.request_ctx import get_project_id_from_request as _get_pid_raw
from utils.json_io import safe_log_error


def get_project_id_from_request():
    return _get_pid_raw(lambda: None)


bp = Blueprint("inventory_groups_hosts_api", __name__)


# ---------------------------------------------------------------------------
# Lazy proxy for app.py globals
# ---------------------------------------------------------------------------
_APP_NAMES = (
    "yaml_loader",
    "get_project_host_vars_dir",
    "get_project_group_vars_dir",
    "get_repo_layout",
    "get_inventory_groups",
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


for _n in _APP_NAMES:
    globals()[_n] = _LazyProxy(_n)


class _AppLogger:
    def __getattr__(self, item):
        try:
            return getattr(_app_module().app.logger, item)
        except Exception:
            return getattr(current_app.logger, item)


app_logger = _AppLogger()


@bp.route('/api/hosts/<host_name>/connection-secret/resolve', methods=['GET'])
@require_auth
def api_resolve_host_connection_secret(host_name):
    """API: connection secret ( host_vars, group_vars)"""
    try:
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        app_logger.info(f"[api_resolve_host_connection_secret] Resolving connection secret for host {host_name} in project {project_id}")
        
        # host_vars
        host_vars_dir = get_project_host_vars_dir(project_id)
        try:
            host_file = _safe_child_path(host_vars_dir, host_name, '.yml')
        except ValueError as ve:
            return jsonify({'success': False, 'error': str(ve)}), 400
        
        app_logger.info(f"[api_resolve_host_connection_secret] Checking host_vars file: {host_file}")
        
        if host_file.exists():
            try:
                with open(host_file, 'r', encoding='utf-8') as f:
                    host_vars = yaml_loader.load(f) or {}
                
                app_logger.info(f"[api_resolve_host_connection_secret] Host vars loaded: {list(host_vars.keys())}")
                
                # connectionSecret ( - )
                if host_vars.get('connectionSecret'):
                    secret_name = host_vars['connectionSecret']
                    app_logger.info(f"[api_resolve_host_connection_secret] Found connection secret in host_vars: {secret_name}")
                    
                    # , username 
                    ansible_user_from_secret = None
                    secrets_dir = get_project_secrets_dir(project_id)
                    ssh_keys_dir = secrets_dir / 'ssh_keys'
                    secret_file = ssh_keys_dir / f"{secret_name}.json"
                    if not secret_file.exists():
                        secret_file = secrets_dir / f"{secret_name}.json"
                    
                    if secret_file.exists():
                        try:
                            with open(secret_file, 'r', encoding='utf-8') as f:
                                secret_data = json.load(f)
                            secret_username = secret_data.get('username', '').strip()
                            if secret_username:
                                ansible_user_from_secret = secret_username
                                app_logger.debug(f"[api_resolve_host_connection_secret] Found username in secret {secret_name}: {secret_username}")
                        except Exception as e:
                            app_logger.warning(f"[api_resolve_host_connection_secret] Error loading secret {secret_name}: {e}")
                    
                    # username , , host_vars, 'root'
                    ansible_user = ansible_user_from_secret or host_vars.get('ansible_user', 'root')
                    
                    # , 
                    if host_vars.get('ansible_ssh_private_key_file'):
                        # , secrets/ssh_keys
                        key_path = Path(host_vars['ansible_ssh_private_key_file'])
                        project_dir = get_project_dir(project_id)
                        
                        # : secrets/ssh_keys/<key_id>.json
                        if (project_dir / 'repo').exists():
                            expected_key_path = secrets_dir / 'ssh_keys' / f"{secret_name}.json"
                        else:
                            # : secrets/ssh_keys/<secret_name>_key
                            expected_key_path = secrets_dir / 'ssh_keys' / f"{secret_name}_key"
                        
                        # , 
                        if str(key_path) != str(expected_key_path):
                            app_logger.info(f"[api_resolve_host_connection_secret] Key path mismatch, will update on save")
                    
                    return jsonify({
                        'success': True,
                        'secretName': secret_name,
                        'source': 'host',
                        'hasConnection': True,
                        'ansible_user': ansible_user,
                        'ansible_port': host_vars.get('ansible_port', 22)
                    })
                elif host_vars.get('ansible_ssh_private_key_file') or host_vars.get('ansible_password'):
                    # Connection Ansible , connectionSecret
                    # /
                    app_logger.info(f"[api_resolve_host_connection_secret] Found connection variables in host_vars, trying to match secret")
                    
                    # , /
                    secrets_dir = get_project_secrets_dir(project_id)
                    matched_secret = None
                    
                    if host_vars.get('ansible_ssh_private_key_file'):
                        # SSH 
                        key_path = Path(host_vars['ansible_ssh_private_key_file'])
                        project_dir = get_project_dir(project_id)
                        
                        # , secrets/ssh_keys
                        if 'secrets' in str(key_path) and 'ssh_keys' in str(key_path):
                            # : secrets/ssh_keys/<key_id>.json
                            key_file_name = key_path.stem  # .json
                            secret_file = secrets_dir / 'ssh_keys' / f"{key_file_name}.json"
                            if secret_file.exists():
                                matched_secret = key_file_name
                    
                    if not matched_secret and host_vars.get('ansible_password'):
                        password = host_vars['ansible_password']
                        # : secrets/ssh_keys/ 
                        search_dirs = [secrets_dir]
                        ssh_keys_dir = secrets_dir / 'ssh_keys'
                        if ssh_keys_dir.exists():
                            search_dirs.append(ssh_keys_dir)
                        
                        for search_dir in search_dirs:
                            for secret_file in search_dir.glob('*.json'):
                                try:
                                    with open(secret_file, 'r', encoding='utf-8') as f:
                                        secret_data = json.load(f)
                                    if secret_data.get('type') == 'login_password' and secret_data.get('password') == password:
                                        matched_secret = secret_file.stem
                                        break
                                except Exception:
                                    continue
                            if matched_secret:
                                break
                    
                    if matched_secret:
                        # , username 
                        ansible_user_from_secret = None
                        secrets_dir = get_project_secrets_dir(project_id)
                        ssh_keys_dir = secrets_dir / 'ssh_keys'
                        matched_secret_file = ssh_keys_dir / f"{matched_secret}.json"
                        if not matched_secret_file.exists():
                            matched_secret_file = secrets_dir / f"{matched_secret}.json"
                        
                        if matched_secret_file.exists():
                            try:
                                with open(matched_secret_file, 'r', encoding='utf-8') as f:
                                    matched_secret_data = json.load(f)
                                matched_secret_username = matched_secret_data.get('username', '').strip()
                                if matched_secret_username:
                                    ansible_user_from_secret = matched_secret_username
                            except Exception:
                                pass
                        
                        ansible_user = ansible_user_from_secret or host_vars.get('ansible_user', 'root')
                        
                        return jsonify({
                            'success': True,
                            'secretName': matched_secret,
                            'source': 'host',
                            'hasConnection': True,
                            'ansible_user': ansible_user,
                            'ansible_port': host_vars.get('ansible_port', 22)
                        })
                    else:
                        return jsonify({
                            'success': True,
                            'secretName': None,
                            'source': 'host',
                            'hasConnection': True,
                            'ansible_user': host_vars.get('ansible_user', 'root'),
                            'ansible_port': host_vars.get('ansible_port', 22)
                        })
                else:
                    app_logger.info(f"[api_resolve_host_connection_secret] No connection variables in host_vars for {host_name}")
            except Exception as e:
                app_logger.warning(f"Error reading host vars for {host_name}: {e}")
        else:
            app_logger.info(f"[api_resolve_host_connection_secret] Host vars file does not exist: {host_file}")
        
        # host_vars, ( inventory- )
        inventories_dir = get_project_inventories_dir(project_id)
        inventory_names = ['inventory.yaml', 'inventory.yml', 'hosts.yaml', 'hosts.yml', 'hosts', 'hosts.ini']
        inventory_files_to_check = []
        if inventories_dir.exists():
            for inv_file in inventories_dir.rglob('*'):
                if inv_file.is_file() and inv_file.name in inventory_names:
                    rel = inv_file.relative_to(inventories_dir)
                    if 'host_vars' not in rel.parts and 'group_vars' not in rel.parts:
                        inventory_files_to_check.append(inv_file)
        repo_dir = get_project_dir(project_id) / 'repo'
        root_inv = repo_dir / 'inventory.yml'
        if root_inv.is_file():
            inventory_files_to_check.append(root_inv)
        if not inventory_files_to_check:
            inventory_file = get_project_inventory_file(project_id)
            if inventory_file.exists():
                inventory_files_to_check = [inventory_file]
        for inv_path in inventory_files_to_check:
            try:
                with open(inv_path, 'r', encoding='utf-8') as f:
                    inventory_data = yaml_loader.load(f) or {}
                groups = inventory_data.get('all', {}).get('children', {})
                for group_name, group_data in groups.items():
                    if not isinstance(group_data, dict):
                        continue
                    hosts = group_data.get('hosts', [])
                    if host_name not in hosts:
                        continue
                    group_vars_dir = get_project_group_vars_dir(project_id)
                    group_file = group_vars_dir / f"{group_name}.yml"
                    if not group_file.exists():
                        continue
                    try:
                        with open(group_file, 'r', encoding='utf-8') as f:
                            group_vars = yaml_loader.load(f) or {}
                    except Exception as e:
                        app_logger.warning(f"Error reading group vars for {group_name}: {e}")
                        continue
                    if not group_vars.get('connectionSecret'):
                        continue
                    secret_name = group_vars['connectionSecret']
                    ansible_user = group_vars.get('ansible_user', 'root')
                    ansible_port = group_vars.get('ansible_port', 22)
                    secrets_dir = get_project_secrets_dir(project_id)
                    secret_file = secrets_dir / 'ssh_keys' / f"{secret_name}.json"
                    if not secret_file.exists():
                        secret_file = secrets_dir / f"{secret_name}.json"
                    if secret_file.exists():
                        try:
                            with open(secret_file, 'r', encoding='utf-8') as sf:
                                secret_data = json.load(sf)
                            if secret_data.get('username', '').strip():
                                ansible_user = secret_data['username'].strip()
                        except Exception:
                            pass
                    return jsonify({
                        'success': True,
                        'secretName': secret_name,
                        'source': 'group',
                        'group': group_name,
                        'hasConnection': True,
                        'ansible_user': ansible_user,
                        'ansible_port': ansible_port
                    })
            except Exception as e:
                app_logger.warning(f"Error reading inventory {inv_path}: {e}")
        
        return jsonify({
            'success': True,
            'secretName': None,
            'source': 'none'
        })
    except Exception as e:
        safe_error_msg = safe_log_error("Error resolving host connection secret", e)
        app_logger.error(safe_error_msg)
        return jsonify({'success': False, 'error': 'Failed to resolve connection secret'}), 500


@bp.route('/api/inventory/group-vars/<group_name>', methods=['GET'])
@require_auth
def api_get_group_vars(group_name):
    """API: group_vars """
    try:
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        group_vars_dir = get_project_group_vars_dir(project_id)
        try:
            group_file = _safe_child_path(group_vars_dir, group_name, '.yml')
        except ValueError as ve:
            return jsonify({'success': False, 'error': str(ve)}), 400
        
        if not group_file.exists():
            return jsonify({
                'success': True,
                'vars': {}
            })
        
        try:
            with open(group_file, 'r', encoding='utf-8') as f:
                vars_data = yaml_loader.load(f) or {}
            
            return jsonify({
                'success': True,
                'vars': vars_data
            })
        except Exception as e:
            app_logger.error(f"Error reading group vars: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500
    except Exception as e:
        app_logger.error(f"Error getting group vars: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/hosts/<host_name>/connection-secret', methods=['PUT'])
@require_auth
def api_set_host_connection_secret(host_name):
    """API: connection secret ( Ansible host_vars)"""
    try:
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        data = request.json or {}
        secret_name = data.get('secretName')
        port = data.get('port', '22')
        ansible_user = data.get('ansibleUser', 'root')
        host_ip = (data.get('hostIp') or data.get('host_ip') or '').strip()
        
        
        host_vars_dir = get_project_host_vars_dir(project_id)
        try:
            host_file = _safe_child_path(host_vars_dir, host_name, '.yml')
        except ValueError as ve:
            return jsonify({'success': False, 'error': str(ve)}), 400
        
        # host_vars 
        host_vars = {}
        if host_file.exists():
            try:
                with open(host_file, 'r', encoding='utf-8') as f:
                    host_vars = yaml_loader.load(f) or {}
            except Exception as e:
                app_logger.warning(f"Error reading host vars for {host_name}: {e}")
        
        # secrets/ssh_keys, 
        old_connection_secret = host_vars.get('connectionSecret')
        if old_connection_secret and old_connection_secret != secret_name:
            secrets_dir = get_project_secrets_dir(project_id)
            # : secrets/ssh_keys/<key_id>.json
            old_key_file_in_secrets = secrets_dir / 'ssh_keys' / f"{old_connection_secret}.json"
            # , 
            # host_vars
        
        # connection-related 
        # SECURITY: Never write ansible_password or ansible_ssh_private_key_file
        # to host_vars — those files get synced to git. Only the connectionSecret
        # reference is persisted; the worker resolves the actual credential at
        # run-time from the encrypted global secrets store.
        # Preserve any existing ansible_host (real IP/FQDN) — do NOT clobber it
        # with the inventory host name below. Only replace when the caller
        # explicitly provides a new host_ip.
        existing_ansible_host = host_vars.get('ansible_host')
        host_vars.pop('ansible_host', None)
        host_vars.pop('ansible_user', None)
        host_vars.pop('ansible_port', None)
        host_vars.pop('ansible_ssh_private_key_file', None)
        host_vars.pop('ansible_password', None)
        host_vars.pop('ansible_ssh_pass', None)

        if secret_name:
            try:
                # Validate the referenced secret exists so users get a clear error
                # instead of a silent runtime failure. Only metadata is read here.
                secrets_dir = get_project_secrets_dir(project_id)
                ssh_keys_dir = secrets_dir / 'ssh_keys'
                secret_file = ssh_keys_dir / f"{secret_name}.json"
                if not secret_file.exists():
                    secret_file = secrets_dir / f"{secret_name}.json"
                if not secret_file.exists():
                    return jsonify({'success': False, 'error': f'Secret {secret_name} not found'}), 404

                with open(secret_file, 'r', encoding='utf-8') as f:
                    secret_data = json.load(f)

                secret_type = secret_data.get('type')
                if secret_type not in ('ssh_key', 'login_password'):
                    return jsonify({'success': False, 'error': f'Unsupported secret type: {secret_type}'}), 400

                # Prefer the username stored on the secret when present.
                secret_username = (secret_data.get('username') or '').strip()
                if secret_username:
                    ansible_user = secret_username

                # Only write ansible_host if we have a real address. Never
                # fall back to host_name — that's the inventory alias, not an
                # IP/FQDN, and would break SSH resolution.
                effective_host = host_ip or (existing_ansible_host or '')
                if effective_host:
                    host_vars['ansible_host'] = effective_host
                host_vars['ansible_user'] = ansible_user
                host_vars['ansible_port'] = int(port) if port else 22
                host_vars['connectionSecret'] = secret_name

                app_logger.info(
                    f"Connection reference saved for host {host_name} → secret '{secret_name}' "
                    f"(type={secret_type}); no credential material written to host_vars."
                )
            except Exception as e:
                safe_error_msg = safe_log_error("Error validating secret", e)
                app_logger.error(safe_error_msg)
                return jsonify({'success': False, 'error': 'Failed to validate secret'}), 500
        else:
            host_vars.pop('connectionSecret', None)

        # Persist host_vars — now guaranteed free of credential material.
        host_vars_dir.mkdir(parents=True, exist_ok=True)
        with open(host_file, 'w', encoding='utf-8') as f:
            yaml_loader.dump(host_vars, f)

        app_logger.info(f"Connection settings saved for host {host_name} in project {project_id}")
        
        return jsonify({
            'success': True,
            'message': 'Connection settings saved successfully'
        })
    except Exception as e:
        safe_error_msg = safe_log_error("Error setting host connection secret", e)
        app_logger.error(safe_error_msg)
        return jsonify({'success': False, 'error': 'Failed to set connection secret'}), 500


@bp.route('/api/groups/<group_name>/connection-secret', methods=['PUT'])
@require_auth
def api_set_group_connection_secret(group_name):
    """API: connection secret ( group_vars)"""
    try:
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        data = request.json or {}
        secret_name = data.get('secretName')
        
        group_vars_dir = get_project_group_vars_dir(project_id)
        try:
            group_file = _safe_child_path(group_vars_dir, group_name, '.yml')
        except ValueError as ve:
            return jsonify({'success': False, 'error': str(ve)}), 400
        
        # group_vars 
        group_vars = {}
        if group_file.exists():
            try:
                with open(group_file, 'r', encoding='utf-8') as f:
                    group_vars = yaml_loader.load(f) or {}
            except Exception as e:
                app_logger.warning(f"Error reading group vars for {group_name}: {e}")
        
        # connectionSecret
        if secret_name:
            group_vars['connectionSecret'] = secret_name
        else:
            # connectionSecret secret_name is None
            group_vars.pop('connectionSecret', None)
        
        # group_vars
        group_vars_dir.mkdir(parents=True, exist_ok=True)
        with open(group_file, 'w', encoding='utf-8') as f:
            yaml_loader.dump(group_vars, f)
        
        app_logger.info(f"Connection secret set for group {group_name} in project {project_id}: {secret_name or 'removed'}")
        
        return jsonify({
            'success': True,
            'message': 'Connection secret saved successfully'
        })
    except Exception as e:
        safe_error_msg = safe_log_error("Error setting group connection secret", e)
        app_logger.error(safe_error_msg)
        return jsonify({'success': False, 'error': 'Failed to set connection secret'}), 500


@bp.route('/api/inventory/groups', methods=['GET'])
@require_auth
def api_get_inventory_groups():
    """API: inventory
    
    inventory inventory_files
           """
    try:
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        # inventory 
        selected_files = request.args.getlist('inventory_files')
        project_dir = get_project_dir(project_id)
        project_inventory_file = get_project_inventory_file(project_id)
        
        inventory_files_to_use = []
        
        if selected_files:
            # , 
            # inventories_dir repoLayout
            inventories_dir = get_project_inventories_dir(project_id)
            layout = get_repo_layout(project_id)
            inventories_layout_path = layout.get('inventories', 'inventories')
            
            for file_path_param in selected_files:
                file_path = None
                # inventories (, ansible/inventories/)
                if file_path_param.startswith(f'{inventories_layout_path}/'):
                    # inventories/prod/hosts.yml ansible/inventories/prod/hosts.yml
                    # inventories_dir
                    rel_path = file_path_param[len(inventories_layout_path) + 1:]
                    file_path = inventories_dir / rel_path
                elif file_path_param.startswith('inventories/'):
                    # : inventories/
                    file_path = project_dir / 'repo' / file_path_param
                elif file_path_param in ('inventory.yml', 'inventory.yaml', 'hosts.yml', 'hosts.yaml', 'hosts', 'hosts.ini'):
                    # inventories ( api_get_inventory_hosts), repo
                    file_path = inventories_dir / file_path_param
                    if not file_path.exists():
                        file_path = project_dir / 'repo' / file_path_param
                elif '/' in file_path_param and not file_path_param.startswith('inventory/'):
                    # prod/hosts.yml - inventories_dir
                    file_path = inventories_dir / file_path_param
                else:
                    # , inventories_dir
                    file_path = inventories_dir / file_path_param
                
                if file_path and file_path.exists():
                    inventory_files_to_use.append(str(file_path))
        else:
            # — inventory inventories ( api_get_inventory_hosts)
            inventories_dir = get_project_inventories_dir(project_id)
            inventory_names = ['inventory.yaml', 'inventory.yml', 'hosts.yaml', 'hosts.yml', 'hosts', 'hosts.ini']
            if inventories_dir.exists():
                for name in inventory_names:
                    p = inventories_dir / name
                    if p.exists():
                        inventory_files_to_use.append(str(p))
                for inv_file in inventories_dir.rglob('*'):
                    if inv_file.is_file() and inv_file.name in inventory_names:
                        rel = inv_file.relative_to(inventories_dir)
                        if 'group_vars' not in rel.parts and 'host_vars' not in rel.parts:
                            path_str = str(inv_file)
                            if path_str not in inventory_files_to_use:
                                inventory_files_to_use.append(path_str)
            if not inventory_files_to_use and project_inventory_file.exists():
                inventory_files_to_use = [str(project_inventory_file)]
        
        # get_inventory_groups 
        groups = get_inventory_groups(inventory_files_to_use) if inventory_files_to_use else {}
        
        return jsonify({
            'success': True,
            'groups': groups
        })
    except Exception as e:
        app_logger.error(f"Error getting inventory groups: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/inventory/groups', methods=['POST'])
@require_auth
def api_add_inventory_group():
    """API: inventory"""
    try:
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        data = request.json or {}
        group_name = data.get('group_name', '').strip()
        hosts = data.get('hosts', [])
        inventory_file_param = data.get('inventory_file', '')
        
        if not group_name:
            return jsonify({'success': False, 'error': 'Group name is required'}), 400
        
        if not group_name.replace('_', '').replace('-', '').isalnum():
            return jsonify({'success': False, 'error': 'Group name can only contain letters, numbers, underscores and hyphens'}), 400
        
        # inventory 
        project_dir = get_project_dir(project_id)
        inventories_dir = get_project_inventories_dir(project_id)
        layout = get_repo_layout(project_id)
        inventories_layout_path = layout.get('inventories', 'inventories')
        
        inventory_file = None
        
        if inventory_file_param:
            # inventory , 
            if inventory_file_param.startswith(f'{inventories_layout_path}/'):
                rel_path = inventory_file_param[len(inventories_layout_path) + 1:]
                inventory_file = inventories_dir / rel_path
            elif inventory_file_param.startswith('inventories/'):
                inventory_file = project_dir / 'repo' / inventory_file_param
            elif inventory_file_param == 'inventory.yml':
                inventory_file = inventories_dir / 'inventory.yml'
            elif '/' in inventory_file_param:
                if inventory_file_param.startswith('inventories/'):
                    rel_path = inventory_file_param[len('inventories/'):]
                    inventory_file = inventories_dir / rel_path
                else:
                    inventory_file = inventories_dir / inventory_file_param
            else:
                inventory_file = inventories_dir / inventory_file_param
        else:
            # , 
            inventory_file = get_project_inventory_file(project_id)
        
        inventory_file.parent.mkdir(parents=True, exist_ok=True)
        
        # inventory
        inventory_data = {}
        if inventory_file.exists():
            try:
                with open(inventory_file, 'r', encoding='utf-8') as f:
                    inventory_data = yaml_loader.load(f) or {}
            except Exception as e:
                app_logger.error(f"Error reading inventory: {e}")
                return jsonify({'success': False, 'error': f'Error reading inventory: {str(e)}'}), 500
        
        if 'all' not in inventory_data:
            inventory_data['all'] = {}
        if 'children' not in inventory_data['all']:
            inventory_data['all']['children'] = {}
        
        # , 
        if group_name in inventory_data['all']['children']:
            return jsonify({'success': False, 'error': f'Group {group_name} already exists'}), 400
        
        group_data = {}
        if hosts:
            # IP 
            group_data['hosts'] = {}
            for host in hosts:
                if isinstance(host, str):
                    host_name = host.strip()
                    if host_name:
                        # vars_file 
                        vars_file = f'host_vars/{host_name}.yml'
                        group_data['hosts'][host_name] = {
                            'vars_file': vars_file
                        }
        
        inventory_data['all']['children'][group_name] = group_data
        
        if inventory_file.exists():
            create_backup(inventory_file)
        
        # inventory
        with open(inventory_file, 'w', encoding='utf-8') as f:
            yaml_loader.dump(inventory_data, f)
        
        app_logger.info(f"Group {group_name} added to inventory in project {project_id}")
        return jsonify({
            'success': True,
            'message': f'Group {group_name} added successfully'
        })
    except Exception as e:
        app_logger.error(f"Error adding group: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/api/inventory/groups/<group_name>', methods=['DELETE'])
@require_auth
def api_delete_inventory_group(group_name):
    """API: delete group from whichever inventory file contains it."""
    try:
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        # Fresh YAML instance (thread-safety with concurrent requests).
        from ruamel.yaml import YAML
        _yaml = YAML()
        _yaml.preserve_quotes = True
        _yaml.width = 4096

        # Allow explicit inventory_file query param; otherwise scan all inventories.
        explicit = request.args.get('inventory_file')
        inventories_dir = get_project_inventories_dir(project_id)
        project_dir = get_project_dir(project_id)

        candidates = []
        if explicit:
            layout = get_repo_layout(project_id)
            inv_layout = layout.get('inventories', 'inventories')
            if explicit.startswith(f'{inv_layout}/'):
                candidates = [inventories_dir / explicit[len(inv_layout) + 1:]]
            elif explicit.startswith('inventories/'):
                candidates = [project_dir / 'repo' / explicit]
            elif '/' in explicit:
                candidates = [inventories_dir / explicit]
            else:
                candidates = [inventories_dir / explicit]
        else:
            inv_names = {'inventory.yml', 'inventory.yaml', 'hosts.yml', 'hosts.yaml', 'hosts', 'hosts.ini'}
            if inventories_dir.exists():
                for p in inventories_dir.rglob('*'):
                    if p.is_file() and (p.suffix in ('.yml', '.yaml') or p.name in inv_names):
                        rel = p.relative_to(inventories_dir)
                        if 'group_vars' in rel.parts or 'host_vars' in rel.parts:
                            continue
                        candidates.append(p)

        def _remove_group_recursive(children_dict, name):
            """Recursively remove `name` from a children mapping. Returns True if removed."""
            if not isinstance(children_dict, dict):
                return False
            removed = False
            if name in children_dict:
                del children_dict[name]
                removed = True
            for other in list(children_dict.values()):
                if isinstance(other, dict):
                    sub = other.get('children')
                    if isinstance(sub, dict):
                        if _remove_group_recursive(sub, name):
                            removed = True
                    elif isinstance(sub, list) and name in sub:
                        sub.remove(name)
                        removed = True
            return removed

        deleted_from = None
        for inventory_file in candidates:
            if not inventory_file.exists():
                continue
            try:
                with open(inventory_file, 'r', encoding='utf-8') as f:
                    inventory_data = _yaml.load(f) or {}
            except Exception as e:
                app_logger.warning(f"Error reading inventory {inventory_file}: {e}")
                continue

            if not isinstance(inventory_data, dict):
                continue
            all_node = inventory_data.get('all') or {}
            children = all_node.get('children') if isinstance(all_node, dict) else None
            if not isinstance(children, dict):
                continue
            if not _remove_group_recursive(children, group_name):
                continue

            create_backup(inventory_file)
            with open(inventory_file, 'w', encoding='utf-8') as f:
                _yaml.dump(inventory_data, f)
            deleted_from = str(inventory_file)
            break


        if not deleted_from:
            return jsonify({'success': False, 'error': 'Group not found'}), 404

        app_logger.info(f"Group {group_name} deleted from {deleted_from} in project {project_id}")
        return jsonify({
            'success': True,
            'message': f'Group {group_name} deleted successfully',
            'inventory_file': deleted_from,
        })
    except Exception as e:
        app_logger.error(f"Error deleting group: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


def _iter_project_inventory_files(project_id):
    """Yield all inventory-like YAML/INI files under the project's inventories dir,
    excluding group_vars/host_vars folders."""
    inventories_dir = get_project_inventories_dir(project_id)
    if not inventories_dir.exists():
        return
    inv_names = {'inventory.yml', 'inventory.yaml', 'hosts.yml', 'hosts.yaml', 'hosts', 'hosts.ini'}
    for p in inventories_dir.rglob('*'):
        if not p.is_file():
            continue
        if not (p.suffix in ('.yml', '.yaml') or p.name in inv_names):
            continue
        rel = p.relative_to(inventories_dir)
        if 'group_vars' in rel.parts or 'host_vars' in rel.parts:
            continue
        yield p


def _remove_host_from_children(children, host_name):
    """Recursively remove host_name from all groups in a children mapping.
    Returns True if any removal happened."""
    if not isinstance(children, dict):
        return False
    changed = False
    for _gname, gdata in children.items():
        if not isinstance(gdata, dict):
            continue
        hosts = gdata.get('hosts')
        if isinstance(hosts, dict) and host_name in hosts:
            del hosts[host_name]
            if not hosts:
                del gdata['hosts']
            changed = True
        elif isinstance(hosts, list) and host_name in hosts:
            hosts.remove(host_name)
            changed = True
        sub = gdata.get('children')
        if isinstance(sub, dict):
            if _remove_host_from_children(sub, host_name):
                changed = True
    return changed


def _find_group_recursive(children, name):
    """Return the group dict for `name` inside a children mapping, or None."""
    if not isinstance(children, dict):
        return None
    if name in children and isinstance(children[name], dict):
        return children[name]
    for gdata in children.values():
        if isinstance(gdata, dict):
            sub = gdata.get('children')
            if isinstance(sub, dict):
                found = _find_group_recursive(sub, name)
                if found is not None:
                    return found
    return None


@bp.route('/api/inventory/hosts/<host_name>/groups', methods=['PUT'])
@require_auth
def api_update_host_groups(host_name):
    """Update the set of groups a host belongs to.

    Removes the host from every group it currently occupies across ALL
    inventory files in the project, then adds it to `target_groups` in the
    primary inventory file. Uses a fresh YAML instance for thread safety
    and supports nested groups.
    """
    try:
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        data = request.json or {}
        target_groups = data.get('groups', [])

        from ruamel.yaml import YAML
        _yaml = YAML()
        _yaml.preserve_quotes = True
        _yaml.width = 4096

        touched_files = []
        # Remove host from every inventory file across the project.
        for inv_path in _iter_project_inventory_files(project_id):
            try:
                with open(inv_path, 'r', encoding='utf-8') as f:
                    inv_data = _yaml.load(f) or {}
            except Exception as e:
                app_logger.warning(f"Skipping unreadable inventory {inv_path}: {e}")
                continue
            if not isinstance(inv_data, dict):
                continue
            all_node = inv_data.get('all')
            if not isinstance(all_node, dict):
                continue
            changed = False
            # Remove from all.hosts as well.
            top_hosts = all_node.get('hosts')
            if isinstance(top_hosts, dict) and host_name in top_hosts:
                del top_hosts[host_name]
                if not top_hosts:
                    del all_node['hosts']
                changed = True
            elif isinstance(top_hosts, list) and host_name in top_hosts:
                top_hosts.remove(host_name)
                changed = True
            children = all_node.get('children')
            if isinstance(children, dict) and _remove_host_from_children(children, host_name):
                changed = True
            if changed:
                create_backup(inv_path)
                with open(inv_path, 'w', encoding='utf-8') as f:
                    _yaml.dump(inv_data, f)
                touched_files.append(str(inv_path))

        # Add host to target_groups in the primary inventory file.
        if target_groups:
            inventory_file = get_project_inventory_file(project_id)
            if not inventory_file.exists():
                inventory_file.parent.mkdir(parents=True, exist_ok=True)
                inv_data = {'all': {'children': {}}}
            else:
                try:
                    with open(inventory_file, 'r', encoding='utf-8') as f:
                        inv_data = _yaml.load(f) or {}
                except Exception as e:
                    app_logger.error(f"Error reading primary inventory: {e}")
                    return jsonify({'success': False, 'error': f'Error reading inventory: {str(e)}'}), 500

            if 'all' not in inv_data or not isinstance(inv_data.get('all'), dict):
                inv_data['all'] = {}
            all_node = inv_data['all']
            if 'children' not in all_node or not isinstance(all_node.get('children'), dict):
                all_node['children'] = {}

            for gname in target_groups:
                # Prefer existing (possibly nested) group; else create at top level.
                gdata = _find_group_recursive(all_node['children'], gname)
                if gdata is None:
                    all_node['children'][gname] = {'hosts': {}}
                    gdata = all_node['children'][gname]
                if 'hosts' not in gdata or gdata['hosts'] is None:
                    gdata['hosts'] = {}
                hosts = gdata['hosts']
                if isinstance(hosts, dict):
                    if host_name not in hosts:
                        hosts[host_name] = {'vars_file': f'host_vars/{host_name}.yml'}
                elif isinstance(hosts, list):
                    if host_name not in hosts:
                        hosts.append(host_name)
                else:
                    gdata['hosts'] = {host_name: {'vars_file': f'host_vars/{host_name}.yml'}}

            if inventory_file.exists():
                create_backup(inventory_file)
            with open(inventory_file, 'w', encoding='utf-8') as f:
                _yaml.dump(inv_data, f)
            if str(inventory_file) not in touched_files:
                touched_files.append(str(inventory_file))

        app_logger.info(
            f"Host {host_name} groups updated in project {project_id}: {target_groups} "
            f"(files touched: {len(touched_files)})"
        )
        return jsonify({
            'success': True,
            'message': f'Host {host_name} groups updated successfully',
            'files_touched': touched_files,
        })
    except Exception as e:
        app_logger.error(f"Error updating host groups: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/inventory/groups/<group_name>/hosts', methods=['PUT'])
@require_auth
def api_update_group_hosts(group_name):
    """Replace the hosts list of a group. Searches all inventory files and
    walks nested children to locate the group."""
    try:
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        data = request.json or {}
        hosts = data.get('hosts', [])

        from ruamel.yaml import YAML
        _yaml = YAML()
        _yaml.preserve_quotes = True
        _yaml.width = 4096

        for inv_path in _iter_project_inventory_files(project_id):
            try:
                with open(inv_path, 'r', encoding='utf-8') as f:
                    inv_data = _yaml.load(f) or {}
            except Exception as e:
                app_logger.warning(f"Skipping unreadable inventory {inv_path}: {e}")
                continue
            if not isinstance(inv_data, dict):
                continue
            all_node = inv_data.get('all')
            if not isinstance(all_node, dict):
                continue
            children = all_node.get('children')
            if not isinstance(children, dict):
                continue
            group_data = _find_group_recursive(children, group_name)
            if group_data is None:
                continue

            existing_hosts = group_data.get('hosts') if isinstance(group_data.get('hosts'), dict) else {}

            if hosts:
                new_hosts = {}
                for host in hosts:
                    if not isinstance(host, str):
                        continue
                    hn = host.strip()
                    if not hn:
                        continue
                    existing = existing_hosts.get(hn) if isinstance(existing_hosts, dict) else None
                    vars_file = None
                    if isinstance(existing, dict):
                        vars_file = existing.get('vars_file')
                    new_hosts[hn] = {'vars_file': vars_file or f'host_vars/{hn}.yml'}
                    # Preserve any inline vars (e.g. ansible_host) from prior entry.
                    if isinstance(existing, dict):
                        for k, v in existing.items():
                            if k != 'vars_file':
                                new_hosts[hn][k] = v
                group_data['hosts'] = new_hosts
            else:
                if 'hosts' in group_data:
                    del group_data['hosts']

            create_backup(inv_path)
            with open(inv_path, 'w', encoding='utf-8') as f:
                _yaml.dump(inv_data, f)

            app_logger.info(f"Hosts updated for group {group_name} in {inv_path} (project {project_id})")
            return jsonify({
                'success': True,
                'message': f'Hosts updated for group {group_name}',
                'inventory_file': str(inv_path),
            })

        return jsonify({'success': False, 'error': 'Group not found'}), 404
    except Exception as e:
        app_logger.error(f"Error updating group hosts: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================================================
# END CONNECTION SECRETS API
# ============================================================================

# ============================================================================
# END SECRETS API
# ============================================================================

