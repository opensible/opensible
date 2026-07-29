"""Inventory file management routes blueprint.

Extracted from ``backend/app.py`` during the phased refactor. URLs, methods,
view-function names, response shapes, and auth decorators are preserved.

Helpers still living in ``app.py`` (``yaml_loader``, ``BASE_DIR``,
``ensure_group_vars_host_vars_from_inventory``, ``ensure_inventory_dirs``,
``get_repo_layout``, ``update_hosts_vars_file``, vault helpers) are resolved
lazily via the initialized app module to avoid circular imports.
"""
from __future__ import annotations

import re
import sys
import time
import zipfile
from io import BytesIO
from pathlib import Path

import yaml
from flask import Blueprint, jsonify, request, current_app, send_file

try:
    from auth.middleware import require_auth
except ImportError:  # pragma: no cover
    from ..auth.middleware import require_auth

from utils.project_paths import (
    get_project_dir,
    get_project_inventories_dir,
    get_project_inventory_file,
)
from utils.yaml_io import create_backup
from utils.request_ctx import (
    get_project_id_from_request as _get_pid_raw,
    require_project_id_from_request,
)


def get_project_id_from_request():
    return _get_pid_raw(lambda: None)


bp = Blueprint("inventory_files_api", __name__)


# ---------------------------------------------------------------------------
# Lazy proxy for app.py globals
# ---------------------------------------------------------------------------
_APP_NAMES = (
    "yaml_loader",
    "BASE_DIR",
    "ensure_group_vars_host_vars_from_inventory",
    "ensure_inventory_dirs",
    "get_repo_layout",
    "update_hosts_vars_file",
    "_load_vaults",
    "_decrypt_content_if_encrypted",
    "_encrypt_content_if_requested",
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


@bp.route('/api/inventory/list', methods=['GET'])
@require_auth
def list_inventory_files():
    """ inventory inventories.
    
    .yml .yaml inventories ( ), host_vars/ group_vars/,
    (inventory.yml, hosts.yml), ( test.yaml).
       """
    try:
        project_id = require_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        inventory_files = []
        project_dir = get_project_dir(project_id)
        inventories_dir = get_project_inventories_dir(project_id)
        repo_dir = project_dir / 'repo'
        
        inventory_full_paths = []
        if inventories_dir.exists():
            for inv_file in inventories_dir.rglob('*'):
                if not inv_file.is_file():
                    continue
                if not (inv_file.suffix in ('.yml', '.yaml', '.ini') or inv_file.name in ('hosts', 'hosts.ini')):
                    continue
                rel_path = inv_file.relative_to(inventories_dir)
                if 'host_vars' in rel_path.parts or 'group_vars' in rel_path.parts:
                    continue
                inventory_full_paths.append(str(inv_file))
                try:
                    repo_rel_path = inv_file.relative_to(repo_dir)
                except ValueError:
                    repo_rel_path = rel_path
                env = rel_path.parts[0] if len(rel_path.parts) > 1 else None
                inventory_files.append({
                    'name': str(rel_path),
                    'path': str(repo_rel_path),
                    'env': env
                })
        for name in ['inventory.yaml', 'inventory.yml', 'hosts.yaml', 'hosts.yml', 'hosts', 'hosts.ini']:
            root_inv = repo_dir / name
            if root_inv.is_file():
                path_str = name
                if not any(f.get('path') == path_str or f.get('path', '').endswith('/' + name) for f in inventory_files):
                    inventory_files.append({'name': name, 'path': path_str, 'env': None})
                    inventory_full_paths.append(str(root_inv))
        
        if inventory_full_paths:
            ensure_group_vars_host_vars_from_inventory(project_id, inventory_full_paths)
        
        inventory_files.sort(key=lambda x: x['path'])
        return jsonify({'success': True, 'files': inventory_files})
    except Exception as e:
        app_logger.error(f"Error listing inventory files: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/inventory/get', methods=['GET'])
@require_auth
def get_inventory():
    """ inventory Project Storage
    
    inventory , repoLayout.
    inventories .
    
       CRITICAL: No fallback to BASE_DIR. Returns 404 if file doesn't exist in project storage.
       """
    try:
        # projectId - REQUIRED, no fallback
        project_id = require_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        file_path_param = request.args.get('file', 'inventories/inventory.yml')
        project_dir = get_project_dir(project_id)
        file_path = None
        
        # inventories_dir repoLayout
        inventories_dir = get_project_inventories_dir(project_id)
        
        # layout inventories repo
        layout = get_repo_layout(project_id)
        inventories_layout_path = layout.get('inventories', 'inventories')
        
        # :
        # 1. inventories ( repoLayout)
        if file_path_param.startswith(f'{inventories_layout_path}/'):
            # inventories/prod/hosts.yml ansible/inventories/prod/hosts.yml
            # inventories_dir
            rel_path = file_path_param[len(inventories_layout_path) + 1:]  # "inventories/" "ansible/inventories/"
            file_path = inventories_dir / rel_path
        elif file_path_param == 'inventory.yml':
            # 2. : repo/inventory.yml
            file_path = project_dir / 'repo' / 'inventory.yml'
        elif '/' in file_path_param and not file_path_param.startswith('inventory/'):
            # prod/hosts.yml - inventories_dir
            file_path = inventories_dir / file_path_param
        else:
            # , inventories_dir
            file_path = inventories_dir / file_path_param
        
        # Fallback: inventories_dir, repo/<file_path_param>
        if not file_path or not file_path.exists():
            if file_path_param.startswith('inventories/'):
                direct_path = project_dir / 'repo' / file_path_param
                if direct_path.exists() and direct_path.is_file():
                    file_path = direct_path
            if not file_path or not file_path.exists():
                if file_path_param != 'inventory.yml':
                    root_inventory = project_dir / 'repo' / 'inventory.yml'
                    if root_inventory.exists():
                        file_path = root_inventory
                    else:
                        return jsonify({'success': False, 'error': f'File {file_path_param} not found in Project Storage'}), 404
                else:
                    return jsonify({'success': False, 'error': f'File {file_path_param} not found in Project Storage'}), 404
        
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        vault_id = request.args.get('vault_id') or request.args.get('vaultId')
        resp = {'success': True, 'content': content, 'file': file_path_param}
        decrypted, was_encrypted, vault_id_used, vault_id_required = _decrypt_content_if_encrypted(project_id, content, vault_id)
        if was_encrypted:
            if vault_id_required:
                vaults = _load_vaults(project_id)
                return jsonify({
                    'success': True,
                    'encrypted': True,
                    'vaultIdRequired': True,
                    'vaults': vaults,
                    'file': file_path_param
                })
            if decrypted is None:
                return jsonify({'success': False, 'error': 'Failed to decrypt vault-encrypted file. Check vault key.'}), 500
            resp['content'] = decrypted
            resp['encrypted'] = True
            resp['vaultId'] = vault_id_used
        return jsonify(resp)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/inventory/save', methods=['POST'])
@require_auth
def save_inventory():
    """ inventory 
    
    : inventories/<env>/hosts.yml
       """
    try:
        # projectId - REQUIRED, no fallback
        project_id = require_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        data = request.json or {}
        file_path_param = data.get('file', 'inventories/inventory.yml')
        content = data.get('content', '')
        env = data.get('env', None)
        vault_id = data.get('vaultId') or data.get('vault_id')
        
        app_logger.info(f"User saving inventory file: {file_path_param} to project {project_id}")
        
        if not content:
            return jsonify({'success': False, 'error': 'File content cannot be empty'}), 400
        
        # YAML backend ( )
        try:
            yaml.safe_load(content)
        except yaml.YAMLError as e:
            error_msg = str(e)
            if hasattr(e, 'problem_mark'):
                mark = e.problem_mark
                error_msg = f"YAML error at line {mark.line + 1}, column {mark.column + 1}: {error_msg}"
            return jsonify({'success': False, 'error': f'YAML validation error: {error_msg}'}), 400
        
        # Ansible-vault: vault_id
        if vault_id:
            encrypted_content, err = _encrypt_content_if_requested(project_id, content, vault_id)
            if err:
                return jsonify({'success': False, 'error': err}), 400
            content = encrypted_content
        
        project_dir = get_project_dir(project_id)
        # inventories_dir repoLayout
        inventories_dir = get_project_inventories_dir(project_id)
        layout = get_repo_layout(project_id)
        inventories_layout_path = layout.get('inventories', 'inventories')
        
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
        elif file_path_param == 'inventory.yml':
            # inventories: inventories/inventory.yml
            file_path = inventories_dir / 'inventory.yml'
        elif env:
            # , 
            file_path = inventories_dir / env / 'hosts.yml'
        elif '/' in file_path_param:
            # subdir/hosts.yml — inventories_dir
            if file_path_param.startswith('inventories/'):
                rel_path = file_path_param[len('inventories/'):]
                file_path = inventories_dir / rel_path
            else:
                file_path = inventories_dir / file_path_param
        else:
            # , inventories
            file_path = inventories_dir / file_path_param
        
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        if file_path.exists():
            create_backup(file_path)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        # group_vars host_vars inventory , 
        ensure_inventory_dirs(file_path)
        
        return jsonify({'success': True, 'message': f'File {file_path_param} saved'})
    except Exception as e:
        app_logger.error(f"Error saving inventory file: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/inventory/create-folder', methods=['POST'])
@require_auth
def create_inventory_folder():
    """ inventories 
    
    Args:
 folder_path: (, 'prod', 'stage/dev')
    """
    try:
        project_id = require_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        data = request.json or {}
        folder_path_param = data.get('folder_path', '').strip()
        
        if not folder_path_param:
            return jsonify({'success': False, 'error': 'Folder path is required'}), 400
        
        if '..' in folder_path_param or folder_path_param.startswith('/'):
            return jsonify({'success': False, 'error': 'Invalid folder path'}), 400
        
        project_dir = get_project_dir(project_id)
        inventories_dir = get_project_inventories_dir(project_id)
        
        folder_path = inventories_dir / folder_path_param
        
        # , 
        if folder_path.exists():
            return jsonify({'success': False, 'error': f'Folder "{folder_path_param}" already exists'}), 400
        
        folder_path.mkdir(parents=True, exist_ok=True)
        
        app_logger.info(f"Created inventory folder: {folder_path} in project {project_id}")
        
        return jsonify({
            'success': True,
            'message': f'Folder "{folder_path_param}" created successfully',
            'path': f'inventories/{folder_path_param}'
        })
    except Exception as e:
        app_logger.error(f"Error creating inventory folder: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/inventory/download', methods=['GET'])
@require_auth
def download_inventory():
    """ inventory 
    
    : inventories/<env>/hosts.yml
       """
    try:
        # projectId
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        file_path_param = request.args.get('file', 'inventories/inventory.yml')
        project_dir = get_project_dir(project_id)
        # inventories_dir repoLayout
        inventories_dir = get_project_inventories_dir(project_id)
        layout = get_repo_layout(project_id)
        inventories_layout_path = layout.get('inventories', 'inventories')
        
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
        elif file_path_param == 'inventory.yml':
            # : repo/inventory.yml
            file_path = project_dir / 'repo' / 'inventory.yml'
        elif '/' in file_path_param:
            # subdir/hosts.yml — inventories_dir
            file_path = inventories_dir / file_path_param
        else:
            # — inventories
            file_path = inventories_dir / file_path_param
        
        if not file_path or not file_path.exists():
            return jsonify({'success': False, 'error': f'File {file_path_param} not found'}), 404
        
        return send_file(
            str(file_path),
            mimetype='text/yaml',
            as_attachment=True,
            download_name=file_path.name
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/inventory/environments', methods=['GET'])
@require_auth
def list_inventory_environments():
    """ inventories, ().
    
    pgsql/, stage/ .., invent.yaml / inventory.yml / hosts.yml.
       """
    try:
        project_id = require_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        environments = []
        inventory_names = ['invent.yaml', 'invent.yml', 'inventory.yaml', 'inventory.yml', 'hosts.yaml', 'hosts.yml', 'hosts', 'hosts.ini']
        inventories_dir = get_project_inventories_dir(project_id)
        if inventories_dir.exists():
            for env_dir in inventories_dir.iterdir():
                if not env_dir.is_dir() or env_dir.name in ('group_vars', 'host_vars'):
                    continue
                env_name = env_dir.name
                has_inventory = any((env_dir / name).exists() for name in inventory_names)
                environments.append({
                    'name': env_name,
                    'has_inventory': has_inventory,
                    'has_group_vars': (env_dir / 'group_vars').exists(),
                    'has_host_vars': (env_dir / 'host_vars').exists()
                })
        
        environments.sort(key=lambda x: x['name'])
        return jsonify({'success': True, 'environments': environments})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/inventory/export', methods=['GET'])
@require_auth
def export_inventory():
    """ inventory ZIP 
    
    : repo/inventories/<env>/hosts.yml
    : inventory/inventory.yml
       """
    try:
        # projectId
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        project_dir = get_project_dir(project_id)
        
        # ZIP : ( Git )
        zip_buffer = BytesIO()
        inventory_names = ['invent.yaml', 'invent.yml', 'inventory.yaml', 'inventory.yml', 'hosts.yaml', 'hosts.yml', 'hosts', 'hosts.ini']
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            inventories_dir = get_project_inventories_dir(project_id)
            if inventories_dir.exists():
                for inv_file in inventories_dir.rglob('*'):
                    if not inv_file.is_file() or inv_file.name not in inventory_names:
                        continue
                    rel = inv_file.relative_to(inventories_dir)
                    if 'group_vars' in rel.parts or 'host_vars' in rel.parts:
                        continue
                    arc_name = f'inventories/{rel}'
                    zip_file.write(str(inv_file), arc_name)
        
        zip_buffer.seek(0)
        
        # timestamp
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        zip_filename = f'inventory_export_{timestamp}.zip'
        
        return send_file(
            zip_buffer,
            mimetype='application/zip',
            as_attachment=True,
            download_name=zip_filename
        )
    except Exception as e:
        app_logger.error(f"Error exporting inventory: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/inventory/add_host', methods=['POST'])
@require_auth
def add_host_to_inventory():
    """ inventory """
    try:
        # projectId - REQUIRED, no fallback
        project_id = require_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        data = request.json or {}
        host_name = data.get('host_name', '').strip()
        inventory_file = data.get('inventory_file', 'inventory.yml')
        group_name = data.get('group_name', 'all')  # , 
        host_ip = data.get('host_ip', '')  # IP 
        vars_file = data.get('vars_file', '')  # vars_file
        
        if not host_name:
            return jsonify({'success': False, 'error': 'Host name not specified'}), 400
        
        if not re.match(r'^[a-zA-Z0-9._-]+$', host_name):
            return jsonify({'success': False, 'error': 'Host name contains invalid characters'}), 400
        
        # inventory ( repoLayout)
        project_dir = get_project_dir(project_id)
        # inventories_dir repoLayout
        inventories_dir = get_project_inventories_dir(project_id)
        layout = get_repo_layout(project_id)
        inventories_layout_path = layout.get('inventories', 'inventories')
        
        # inventories (, ansible/inventories/)
        if inventory_file.startswith(f'{inventories_layout_path}/'):
            # inventories/prod/hosts.yml ansible/inventories/prod/hosts.yml
            # inventories_dir
            rel_path = inventory_file[len(inventories_layout_path) + 1:]
            file_path = inventories_dir / rel_path
        elif inventory_file.startswith('inventories/'):
            # : inventories/
            file_path = project_dir / 'repo' / inventory_file
        elif inventory_file == 'inventory.yml':
            # inventories: inventories/inventory.yml
            file_path = inventories_dir / 'inventory.yml'
        elif '/' in inventory_file:
            # prod/hosts.yml inventories/inventory.yml - inventories_dir
            if inventory_file.startswith('inventories/'):
                # inventories/ inventories_dir
                rel_path = inventory_file[len('inventories/'):]
                file_path = inventories_dir / rel_path
            else:
                # prod/hosts.yml - inventories_dir
                file_path = inventories_dir / inventory_file
        else:
            # , inventories
            file_path = inventories_dir / inventory_file
        
        # Use a fresh ruamel.yaml YAML instance to avoid thread-safety issues
        # ("I/O operation on closed file") caused by concurrent requests sharing
        # the module-level yaml_loader singleton.
        from ruamel.yaml import YAML
        _yaml = YAML()
        _yaml.preserve_quotes = True
        _yaml.width = 4096

        # No fallback to BASE_DIR - new projects should be empty
        # inventory Project Storage
        if file_path.exists():
            with open(file_path, 'r', encoding='utf-8') as f:
                inventory_data = _yaml.load(f) or {}
        else:
            inventory_data = {'all': {}}
        
        if 'all' not in inventory_data:
            inventory_data['all'] = {}
        
        # vars_file
        if not vars_file:
            vars_file = f'host_vars/{host_name}.yml'
        
        if group_name == 'all':
            # all.hosts
            if 'hosts' not in inventory_data['all']:
                inventory_data['all']['hosts'] = {}
            
            # , 
            if isinstance(inventory_data['all']['hosts'], dict):
                if host_name in inventory_data['all']['hosts']:
                    return jsonify({'success': False, 'error': f'Host {host_name} already exists in inventory'}), 400
                
                # vars_file
                inventory_data['all']['hosts'][host_name] = {
                    'vars_file': vars_file
                }
                
                # IP, ansible_host
                if host_ip:
                    inventory_data['all']['hosts'][host_name]['ansible_host'] = host_ip
            elif isinstance(inventory_data['all']['hosts'], list):
                if host_name in inventory_data['all']['hosts']:
                    return jsonify({'success': False, 'error': f'Host {host_name} already exists in inventory'}), 400
                inventory_data['all']['hosts'].append(host_name)
        else:
            # children
            if 'children' not in inventory_data['all']:
                inventory_data['all']['children'] = {}
            
            if group_name not in inventory_data['all']['children']:
                inventory_data['all']['children'][group_name] = {'hosts': {}}
            
            group_data = inventory_data['all']['children'][group_name]
            if 'hosts' not in group_data:
                group_data['hosts'] = {}
            
            # , 
            if isinstance(group_data['hosts'], dict):
                if host_name in group_data['hosts']:
                    return jsonify({'success': False, 'error': f'Host {host_name} already exists in group {group_name}'}), 400
                
                # vars_file
                group_data['hosts'][host_name] = {
                    'vars_file': vars_file
                }
                
                # IP, ansible_host
                if host_ip:
                    group_data['hosts'][host_name]['ansible_host'] = host_ip
            elif isinstance(group_data['hosts'], list):
                if host_name in group_data['hosts']:
                    return jsonify({'success': False, 'error': f'Host {host_name} already exists in group {group_name}'}), 400
                group_data['hosts'].append(host_name)
        
        if file_path.exists():
            create_backup(file_path)
        
        # inventory
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            _yaml.dump(inventory_data, f)

        
        app_logger.info(f"Host {host_name} added to {inventory_file}, group: {group_name}")
        
        return jsonify({
            'success': True,
            'message': f'Host {host_name} successfully added to group {group_name}',
            'host_name': host_name,
            'group_name': group_name,
            'inventory_file': inventory_file
        })
    except Exception as e:
        app_logger.error(f"Error adding host: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/inventory/import', methods=['POST'])
@require_auth
def import_inventory():
    """ inventory () ZIP """
    try:
        # projectId - REQUIRED, no fallback
        project_id = require_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'File not uploaded'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'File not selected'}), 400
        
        project_inventory_dir = get_project_inventory_file(project_id).parent
        project_inventory_dir.mkdir(parents=True, exist_ok=True)
        
        imported_files = []
        errors = []
        
        # , ZIP 
        if file.filename.endswith('.zip'):
            # ZIP 
            try:
                zip_buffer = BytesIO(file.read())
                with zipfile.ZipFile(zip_buffer, 'r') as zip_file:
                    for file_info in zip_file.namelist():
                        if file_info.endswith('/') or '__MACOSX' in file_info:
                            continue
                        
                        # .yml .yaml 
                        if not (file_info.endswith('.yml') or file_info.endswith('.yaml')):
                            continue
                        
                        try:
                            content = zip_file.read(file_info)
                            
                            # YAML
                            try:
                                yaml.safe_load(content)
                            except yaml.YAMLError as e:
                                errors.append(f"{file_info}: invalid YAML - {str(e)}")
                                continue
                            
                            file_name = Path(file_info).name
                            target_path = project_inventory_dir / file_name
                            
                            target_path.parent.mkdir(parents=True, exist_ok=True)
                            
                            with open(target_path, 'wb') as f:
                                f.write(content)
                            
                            # group_vars host_vars inventory , 
                            ensure_inventory_dirs(target_path)
                            
                            imported_files.append(file_name)
                            app_logger.info(f"Imported inventory file: {file_name}")
                        except Exception as e:
                            errors.append(f"{file_info}: {str(e)}")
                            app_logger.warning(f"Error importing file {file_info}: {e}")
            except zipfile.BadZipFile:
                return jsonify({'success': False, 'error': 'Invalid ZIP archive'}), 400
        else:
            if not (file.filename.endswith('.yml') or file.filename.endswith('.yaml')):
                return jsonify({'success': False, 'error': 'Only .yml and .yaml files are supported'}), 400
            
            try:
                content = file.read()
                
                # YAML
                try:
                    yaml.safe_load(content)
                except yaml.YAMLError as e:
                    return jsonify({'success': False, 'error': f'Invalid YAML: {str(e)}'}), 400
                
                file_name = file.filename
                target_path = project_inventory_dir / file_name
                
                target_path.parent.mkdir(parents=True, exist_ok=True)
                
                with open(target_path, 'wb') as f:
                    f.write(content)
                
                # group_vars host_vars inventory , 
                ensure_inventory_dirs(target_path)
                
                imported_files.append(file_name)
                app_logger.info(f"Imported inventory file: {file_name}")
            except Exception as e:
                return jsonify({'success': False, 'error': f'File import error: {str(e)}'}), 500
        
        if not imported_files:
            return jsonify({
                'success': False,
                'error': 'Failed to import any files',
                'errors': errors
            }), 400
        
        return jsonify({
            'success': True,
            'imported_files': imported_files,
            'errors': errors if errors else None,
            'message': f'Imported files: {len(imported_files)}'
        })
    except Exception as e:
        app_logger.error(f"Error importing inventory: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


def update_hosts_vars_file(group_data, updated=False):
    """ inventory, vars_file , 
    
    Args:
 group_data: ( 'hosts' 'children')
 updated: , , 
    
    Returns:
 tuple: ( , )
    """
    if not isinstance(group_data, dict):
        return group_data, updated
    
    updated_group = group_data.copy()
    
    if 'hosts' in updated_group:
        hosts = updated_group['hosts']
        if isinstance(hosts, dict):
            # hosts: { host1: { vars: ... }, host2: None, host3: ... }
            updated_hosts = {}
            for host, config in hosts.items():
                if isinstance(host, str) and host.startswith('#'):
                    updated_hosts[host] = config
                    continue
                # - 
                if not isinstance(host, str):
                    updated_hosts[host] = config
                    continue
                
                # config None, dict
                if config is None:
                    config = {}
                    updated = True
                
                # config dict, vars_file
                if isinstance(config, dict):
                    if 'vars_file' not in config:
                        # vars_file 
                        config['vars_file'] = f'host_vars/{host}.yml'
                        updated = True
                    updated_hosts[host] = config
                else:
                    updated_hosts[host] = config
            updated_group['hosts'] = updated_hosts
        elif isinstance(hosts, list):
            # hosts: [ host1, host2, ... ] - dict vars_file
            updated_hosts = {}
            for host in hosts:
                if isinstance(host, str) and not host.startswith('#'):
                    # dict vars_file
                    updated_hosts[host] = {'vars_file': f'host_vars/{host}.yml'}
                    updated = True
                else:
                    # ( )
                    updated_hosts[host] = None
            updated_group['hosts'] = updated_hosts
    
    if 'children' in updated_group:
        children = updated_group['children']
        if isinstance(children, dict):
            updated_children = {}
            for child_name, child_data in children.items():
                if isinstance(child_data, dict):
                    updated_child, child_updated = update_hosts_vars_file(child_data, updated)
                    updated_children[child_name] = updated_child
                    if child_updated:
                        updated = True
                else:
                    updated_children[child_name] = child_data
            updated_group['children'] = updated_children
    
    return updated_group, updated


@bp.route('/api/inventory/auto_update_vars_file', methods=['POST'])
@require_auth
def auto_update_inventory_vars_file():
    """ vars_file inventory """
    try:
        data = request.json or {}
        file_names = data.get('files', [])
        
        if not file_names:
            return jsonify({'success': False, 'error': 'File list not specified'}), 400
        
        updated_files = []
        
        for file_name in file_names:
            file_path = BASE_DIR / file_name
            
            if not file_path.exists():
                app_logger.warning(f"Inventory file {file_name} not found, skipping")
                continue
            
            try:
                # inventory ruamel.yaml 
                with open(file_path, 'r', encoding='utf-8') as f:
                    inventory = yaml_loader.load(f)
                
                if not inventory:
                    app_logger.warning(f"Inventory file {file_name} is empty or invalid, skipping")
                    continue
                
                updated_inventory = inventory
                updated = False
                
                # 'all'
                if 'all' in updated_inventory:
                    all_section = updated_inventory['all']
                    if isinstance(all_section, dict):
                        updated_all, all_updated = update_hosts_vars_file(all_section, False)
                        updated_inventory['all'] = updated_all
                        if all_updated:
                            updated = True
                else:
                    # 'all', 
                    updated_root, root_updated = update_hosts_vars_file(updated_inventory, False)
                    if root_updated:
                        updated_inventory = updated_root
                        updated = True
                
                # , 
                if updated:
                    create_backup(file_path)
                    
                    # ruamel.yaml 
                    with open(file_path, 'w', encoding='utf-8') as f:
                        yaml_loader.dump(updated_inventory, f)
                    
                    app_logger.info(f"Automatically updated inventory file {file_name}: added vars_file for hosts")
                    updated_files.append(file_name)
                
            except yaml.YAMLError as e:
                app_logger.error(f"YAML parsing error in file {file_name}: {e}")
                continue
            except Exception as e:
                app_logger.error(f"Error processing inventory file {file_name}: {e}")
                continue
        
        return jsonify({
            'success': True,
            'message': f'Updated files: {len(updated_files)}',
            'updated_files': updated_files
        })
    except Exception as e:
        app_logger.error(f"Error automatically updating vars_file: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/inventory/delete', methods=['POST'])
@require_auth
def delete_inventory():
    """ inventory """
    try:
        data = request.json or {}
        file_name = data.get('file')
        
        if not file_name:
            return jsonify({'success': False, 'error': 'File name not specified'}), 400
        
        file_path = BASE_DIR / file_name
        
        if not file_path.exists():
            return jsonify({'success': False, 'error': f'File {file_name} not found'}), 404
        
        create_backup(file_path)
        
        file_path.unlink()
        
        return jsonify({'success': True, 'message': f'File {file_name} successfully deleted'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

