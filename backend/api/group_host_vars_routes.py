"""Group vars / host vars API routes (extracted from app.py)."""
from __future__ import annotations

import sys
from flask import Blueprint, jsonify, request, send_file

try:
    from auth.middleware import require_auth
except ImportError:  # pragma: no cover
    from ..auth.middleware import require_auth

from utils.request_ctx import (
    get_project_id_from_request as _get_pid_raw,
    require_project_id_from_request,
)


def get_project_id_from_request():
    return _get_pid_raw(lambda: None)


bp = Blueprint("group_host_vars_api", __name__)


def _app_module():
    for name in ("__main__", "app"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "ensure_all_inventory_dirs"):
            return mod
    import app as _a  # noqa
    return _a


_APP_NAMES = [
    "_decrypt_content_if_encrypted", "_encrypt_content_if_requested", "_load_vaults",
    "_is_ini_inventory_file", "_parse_ini_inventory",
    "ensure_all_inventory_dirs", "ensure_inventory_dirs",
    "find_inventory_file_for_group", "get_group_vars_dir_for_inventory",
    "get_host_vars_dir_for_inventory", "get_inventory_hosts",
    "get_project_dir", "get_project_inventories_dir",
    "get_project_group_vars_dir", "get_project_host_vars_dir",
    "yaml_loader", "yaml", "validate_yaml_content", "create_backup", "Path",
]


class _LazyProxy:
    def __init__(self, name): self._n = name
    def __call__(self, *a, **kw): return getattr(_app_module(), self._n)(*a, **kw)
    def __getattr__(self, k): return getattr(getattr(_app_module(), self._n), k)


for _n in _APP_NAMES:
    globals()[_n] = _LazyProxy(_n)


def _app_logger():
    return _app_module().app.logger


class _AppLoggerProxy:
    def __getattr__(self, k): return getattr(_app_logger(), k)


app_logger = _AppLoggerProxy()


@bp.route('/api/group_vars/list', methods=['GET'])
@require_auth
def list_group_vars_files():
    """ group_vars Project Storage inventory
    
    :
    - vars/group_vars/
    - inventory : inventories/*/group_vars/
       """
    try:
        # project_id - REQUIRED
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        project_dir = get_project_dir(project_id)
        repo_dir = project_dir / 'repo'
        
        # Project Storage
        project_group_vars_dir = get_project_group_vars_dir(project_id)
        project_group_vars_dir.mkdir(parents=True, exist_ok=True)
        
        ensure_all_inventory_dirs(project_id)
        
        groups_from_inventory = set()
        inventories_dir = get_project_inventories_dir(project_id)
        
        inventory_files = []
        if inventories_dir.exists():
            for inv_file in inventories_dir.rglob('*.yaml'):
                if inv_file.is_file():
                    rel_path = inv_file.relative_to(inventories_dir)
                    if 'host_vars' not in rel_path.parts and 'group_vars' not in rel_path.parts:
                        inventory_files.append(str(inv_file.relative_to(repo_dir)))
            for inv_file in inventories_dir.rglob('*.yml'):
                if inv_file.is_file():
                    rel_path = inv_file.relative_to(inventories_dir)
                    if 'host_vars' not in rel_path.parts and 'group_vars' not in rel_path.parts:
                        inventory_files.append(str(inv_file.relative_to(repo_dir)))
            for inv_file in inventories_dir.rglob('*.ini'):
                if inv_file.is_file():
                    rel_path = inv_file.relative_to(inventories_dir)
                    if 'host_vars' not in rel_path.parts and 'group_vars' not in rel_path.parts:
                        inventory_files.append(str(inv_file.relative_to(repo_dir)))
            for inv_file in inventories_dir.rglob('*'):
                if inv_file.is_file() and inv_file.name in ('hosts',) and inv_file.suffix == '':
                    rel_path = inv_file.relative_to(inventories_dir)
                    if 'host_vars' not in rel_path.parts and 'group_vars' not in rel_path.parts:
                        path_str = str(inv_file.relative_to(repo_dir))
                        if path_str not in inventory_files:
                            inventory_files.append(path_str)
        
        root_inventory = repo_dir / 'inventory.yml'
        if root_inventory.exists():
            inventory_files.append('inventory.yml')
        
        for inv_file_path in inventory_files:
            full_path = repo_dir / inv_file_path
            if full_path.exists():
                try:
                    if _is_ini_inventory_file(full_path):
                        inventory_data = _parse_ini_inventory(full_path) or {}
                    else:
                        with open(full_path, 'r', encoding='utf-8') as f:
                            inventory_data = yaml_loader.load(f) or {}
                    
                    inventory_dir = full_path.parent
                    group_vars_dir = inventory_dir / 'group_vars'
                    group_vars_dir.mkdir(parents=True, exist_ok=True)
                    
                    groups_in_this_inventory = set()

                    def _collect_group_names(children_map):
                        if not isinstance(children_map, dict):
                            return
                        for gname, gdata in children_map.items():
                            if isinstance(gname, str) and not gname.startswith('#'):
                                groups_in_this_inventory.add(gname)
                                groups_from_inventory.add(gname)
                            if isinstance(gdata, dict):
                                sub = gdata.get('children')
                                if isinstance(sub, dict):
                                    _collect_group_names(sub)

                    all_children = inventory_data.get('all', {}).get('children', {})
                    _collect_group_names(all_children)

                    # `all` group always eligible for a group_vars file
                    groups_in_this_inventory.add('all')
                    groups_from_inventory.add('all')

                    
                    for group_name in groups_in_this_inventory:
                        group_file = group_vars_dir / f"{group_name}.yml"
                        if not group_file.exists():
                            try:
                                with open(group_file, 'w', encoding='utf-8') as f:
                                    yaml_loader.dump({}, f)
                                app_logger.info(f"Auto-created group_vars file for group: {group_name} at {group_file}")
                            except Exception as e:
                                app_logger.error(f"Error creating group_vars file for {group_name} at {group_file}: {e}")
                except Exception as e:
                    app_logger.warning(f"Error reading inventory {inv_file_path} for group_vars sync: {e}")
        
        # group_vars 
        group_vars_files_dict = {}  # dict 
        
        # 1. vars/group_vars/
        for ext in ['*.yml', '*.yaml']:
            for file_path in project_group_vars_dir.glob(ext):
                # Skip backup directory
                if 'backups' in str(file_path):
                    continue
                if file_path.is_file():
                    file_name = file_path.name
                    if file_name not in group_vars_files_dict:
                        group_vars_files_dict[file_name] = {
                            'name': file_name,
                            'path': f'group_vars/{file_name}'
                        }
        
        # 2. inventory : inventories/*/group_vars/
        if inventories_dir.exists():
            for group_vars_dir in inventories_dir.rglob('group_vars'):
                if group_vars_dir.is_dir():
                    for ext in ['*.yml', '*.yaml']:
                        for file_path in group_vars_dir.glob(ext):
                            # Skip backup directory
                            if 'backups' in str(file_path):
                                continue
                            if file_path.is_file():
                                file_name = file_path.name
                                # repo
                                repo_rel_path = file_path.relative_to(repo_dir)
                                if file_name not in group_vars_files_dict:
                                    group_vars_files_dict[file_name] = {
                                        'name': file_name,
                                        'path': str(repo_rel_path)
                                    }
        
        # 3. inventory.yml ( )
        root_group_vars_dir = repo_dir / 'group_vars'
        if root_group_vars_dir.exists() and root_group_vars_dir.is_dir():
            for ext in ['*.yml', '*.yaml']:
                for file_path in root_group_vars_dir.glob(ext):
                    if 'backups' in str(file_path):
                        continue
                    if file_path.is_file():
                        file_name = file_path.name
                        if file_name not in group_vars_files_dict:
                            group_vars_files_dict[file_name] = {
                                'name': file_name,
                                'path': f'group_vars/{file_name}'
                            }
        
        # dict 
        group_vars_files = list(group_vars_files_dict.values())
        group_vars_files.sort(key=lambda x: x['name'])
        
        return jsonify({'success': True, 'files': group_vars_files})
    except Exception as e:
        app_logger.error(f"Error listing group_vars files: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/group_vars/get', methods=['GET'])
@require_auth
def get_group_vars():
    """ group_vars """
    try:
        # projectId - REQUIRED, no fallback
        project_id = require_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        file_name = request.args.get('file', 'all.yml')
        inventory_file = request.args.get('inventory_file')
        rel_path = request.args.get('path')

        project_dir = get_project_dir(project_id)
        candidates = []
        if rel_path:
            candidates.append(project_dir / 'repo' / rel_path)
            candidates.append(project_dir / rel_path)
        if inventory_file:
            candidates.append(get_group_vars_dir_for_inventory(project_id, inventory_file) / file_name)
        candidates.append(get_project_group_vars_dir(project_id) / file_name)

        file_path = None
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
                if resolved.exists() and resolved.is_file() and resolved.is_relative_to(project_dir.resolve()):
                    file_path = resolved
                    break
            except Exception:
                continue

        if not file_path:
            return jsonify({'success': False, 'error': f'File {file_name} not found'}), 404
        
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        vault_id = request.args.get('vault_id') or request.args.get('vaultId')
        decrypted, was_encrypted, vault_id_used, vault_id_required = _decrypt_content_if_encrypted(project_id, content, vault_id)
        if was_encrypted:
            if vault_id_required:
                vaults = _load_vaults(project_id)
                return jsonify({
                    'success': True,
                    'encrypted': True,
                    'vaultIdRequired': True,
                    'vaults': vaults,
                    'file': file_name
                })
            if decrypted is None:
                return jsonify({'success': False, 'error': 'Failed to decrypt vault-encrypted file. Check vault key.'}), 500
            content = decrypted
        resp = {'success': True, 'content': content, 'file': file_name}
        try:
            repo_dir = project_dir / 'repo'
            resp['path'] = str(file_path.relative_to(repo_dir)).replace('\\', '/')
        except Exception:
            pass
        if was_encrypted:
            resp['encrypted'] = True
            resp['vaultId'] = vault_id_used
        return jsonify(resp)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/group_vars/save', methods=['POST'])
@require_auth
def save_group_vars():
    """ group_vars """
    try:
        # projectId - REQUIRED, no fallback
        project_id = require_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        data = request.json or {}
        file_name = data.get('file', 'all.yml')
        content = data.get('content', '')
        inventory_file = data.get('inventory_file')  # inventory ()
        vault_id = data.get('vaultId') or data.get('vault_id')
        rel_path = data.get('path')
        
        app_logger.info(f"User saving group_vars file: {file_name} to project {project_id}, inventory_file: {inventory_file}")
        
        if not content:
            return jsonify({'success': False, 'error': 'File content cannot be empty'}), 400
        
        # YAML ( )
        is_valid, error_msg = validate_yaml_content(content)
        if not is_valid:
            app_logger.warning(f"Invalid YAML in group_vars/{file_name}: {error_msg}")
            return jsonify({'success': False, 'error': f'Invalid YAML syntax: {error_msg}'}), 400
        
        # Ansible-vault: vault_id
        if vault_id:
            encrypted_content, err = _encrypt_content_if_requested(project_id, content, vault_id)
            if err:
                return jsonify({'success': False, 'error': err}), 400
            content = encrypted_content

        # Exact repo-relative path wins when the caller selected a known file.
        # This prevents saving "data_platform.yml" into another group_vars
        # directory when duplicate file names exist across inventories.
        if rel_path:
            rel_path = str(rel_path).replace('\\', '/').strip('/')
            if '..' in rel_path or 'group_vars' not in rel_path.split('/'):
                return jsonify({'success': False, 'error': 'Invalid group_vars path'}), 400
            project_dir = get_project_dir(project_id)
            candidates = [project_dir / 'repo' / rel_path, project_dir / rel_path]
            file_path = None
            for candidate in candidates:
                try:
                    resolved = candidate.resolve()
                    if resolved.is_relative_to(project_dir.resolve()):
                        file_path = resolved
                        break
                except Exception:
                    continue
            if not file_path:
                return jsonify({'success': False, 'error': 'Invalid group_vars path'}), 400
            file_path.parent.mkdir(parents=True, exist_ok=True)
            if file_path.exists():
                create_backup(file_path)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return jsonify({'success': True, 'message': f'File {file_path.name} saved'})
        
        # inventory 
        # inventory_file , 
        if not inventory_file:
            # (, "pgsql_replicas.yml" -> "pgsql_replicas")
            group_name = file_name.replace('.yml', '').replace('.yaml', '')
            if group_name != 'all':
                inventory_file = find_inventory_file_for_group(project_id, group_name)
                if inventory_file:
                    app_logger.info(f"[save_group_vars] Found inventory_file for group {group_name}: {inventory_file}")
            if not inventory_file:
                app_logger.warning(f"[save_group_vars] Could not find inventory_file for group {group_name}, will use fallback directory")
        else:
            app_logger.info(f"[save_group_vars] Using provided inventory_file: {inventory_file}")
        
        # group_vars inventory 
        group_vars_dir = get_group_vars_dir_for_inventory(project_id, inventory_file)
        group_vars_dir.mkdir(parents=True, exist_ok=True)
        file_path = group_vars_dir / file_name
        
        app_logger.info(f"[save_group_vars] Saving group_vars to: {file_path} (inventory_file: {inventory_file}, group_vars_dir: {group_vars_dir})")
        
        # inventory_file , , group_vars host_vars inventory 
        if inventory_file:
            project_dir = get_project_dir(project_id)
            repo_dir = project_dir / 'repo'
            
            # inventory 
            inventory_path = None
            repo_path = repo_dir / inventory_file
            if repo_path.exists():
                inventory_path = repo_path
            else:
                found_files = list(repo_dir.rglob(Path(inventory_file).name))
                if found_files:
                    inventory_path = found_files[0]
            
            if inventory_path and inventory_path.exists():
                # group_vars host_vars inventory 
                ensure_inventory_dirs(inventory_path)
                app_logger.info(f"[save_group_vars] Ensured group_vars/host_vars directories for {inventory_path}")
        
        if file_path.exists():
            create_backup(file_path)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return jsonify({'success': True, 'message': f'File {file_name} saved'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/group_vars/update_var', methods=['POST'])
@require_auth
def update_group_var():
    """ group_vars (merge)"""
    try:
        # projectId
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        data = request.json or {}
        file_name = data.get('file', 'all.yml')
        var_name = data.get('var_name')
        var_value = data.get('var_value')
        inventory_file = data.get('inventory_file')  # inventory ()
        
        if not var_name:
            return jsonify({'success': False, 'error': 'Variable name not specified'}), 400
        
        # inventory 
        # inventory_file , 
        if not inventory_file:
            # (, "pgsql_replicas.yml" -> "pgsql_replicas")
            group_name = file_name.replace('.yml', '').replace('.yaml', '')
            if group_name != 'all':
                inventory_file = find_inventory_file_for_group(project_id, group_name)
                if inventory_file:
                    app_logger.info(f"[update_group_var] Found inventory_file for group {group_name}: {inventory_file}")
            if not inventory_file:
                app_logger.warning(f"[update_group_var] Could not find inventory_file for group {group_name}, will use fallback directory")
        
        # group_vars inventory 
        group_vars_dir = get_group_vars_dir_for_inventory(project_id, inventory_file)
        group_vars_dir.mkdir(parents=True, exist_ok=True)
        file_path = group_vars_dir / file_name
        
        app_logger.info(f"[update_group_var] Updating group_var in: {file_path} (inventory_file: {inventory_file})")
        
        # inventory_file , , group_vars host_vars inventory 
        if inventory_file:
            project_dir = get_project_dir(project_id)
            repo_dir = project_dir / 'repo'
            
            # inventory 
            inventory_path = None
            repo_path = repo_dir / inventory_file
            if repo_path.exists():
                inventory_path = repo_path
            else:
                found_files = list(repo_dir.rglob(Path(inventory_file).name))
                if found_files:
                    inventory_path = found_files[0]
            
            if inventory_path and inventory_path.exists():
                # group_vars host_vars inventory 
                ensure_inventory_dirs(inventory_path)
        
        current_data = {}
        if file_path.exists():
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    current_data = yaml.safe_load(f) or {}
            except Exception as e:
                app_logger.warning(f"Error reading {file_path}: {e}")
                current_data = {}
        
        if var_value is None:
            current_data.pop(var_name, None)
        else:
            current_data[var_name] = var_value
        
        # YAML 
        is_valid, error_msg = validate_yaml_content(current_data)
        if not is_valid:
            app_logger.warning(f"Invalid YAML in group_vars/{file_name}: {error_msg}")
            return jsonify({'success': False, 'error': f'Invalid YAML syntax: {error_msg}'}), 400
        
        if file_path.exists():
            create_backup(file_path)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            yaml.dump(current_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        
        return jsonify({'success': True, 'message': f'Variable {var_name} updated'})
    except Exception as e:
        app_logger.error(f"Error updating group_var: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/host_vars/update_var', methods=['POST'])
@require_auth
def update_host_var():
    """ host_vars (merge) Project Storage"""
    try:
        # project_id - REQUIRED
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        data = request.json or {}
        file_name = data.get('file')
        var_name = data.get('var_name')
        var_value = data.get('var_value')
        inventory_file = data.get('inventory_file')  # inventory ()
        
        if not file_name or not var_name:
            return jsonify({'success': False, 'error': 'File name or variable name not specified'}), 400
        
        # inventory 
        if not inventory_file:
            # inventory_file 
            # inventory 
            host_name = file_name.replace('.yml', '').replace('.yaml', '')
            
            # inventory localStorage ( API)
            # inventory 
            project_dir = get_project_dir(project_id)
            repo_dir = project_dir / 'repo'
            
            # inventory 
            inventory_files = []
            for pattern in ['invent.yaml', 'inventory.yml', 'hosts.yml']:
                for inv_file in repo_dir.rglob(pattern):
                    if inv_file.is_file():
                        inventory_files.append(str(inv_file))
            
            # inventory 
            hosts_list = get_inventory_hosts(inventory_files) if inventory_files else []
            app_logger.debug(f"[update_host_var] Looking for host {host_name} in {len(hosts_list)} hosts from {len(inventory_files)} inventory files")
            
            for h in hosts_list:
                if h['name'] == host_name:
                    inventory_file = h.get('inventory_file', '')
                    if inventory_file:
                        app_logger.info(f"[update_host_var] Found inventory_file for host {host_name}: {inventory_file}")
                        break
            
            if not inventory_file:
                app_logger.warning(f"[update_host_var] Could not find inventory_file for host {host_name}")
        
        # host_vars inventory 
        host_vars_dir = get_host_vars_dir_for_inventory(project_id, inventory_file)
        host_vars_dir.mkdir(parents=True, exist_ok=True)
        file_path = host_vars_dir / file_name
        
        app_logger.info(f"[update_host_var] Using host_vars_dir: {host_vars_dir} for inventory_file: {inventory_file}, file_path: {file_path}")
        
        # inventory_file , , group_vars host_vars inventory 
        if inventory_file:
            project_dir = get_project_dir(project_id)
            repo_dir = project_dir / 'repo'
            
            # inventory 
            inventory_path = None
            repo_path = repo_dir / inventory_file
            if repo_path.exists():
                inventory_path = repo_path
            else:
                found_files = list(repo_dir.rglob(Path(inventory_file).name))
                if found_files:
                    inventory_path = found_files[0]
            
            if inventory_path and inventory_path.exists():
                # group_vars host_vars inventory 
                ensure_inventory_dirs(inventory_path)
                app_logger.info(f"[update_host_var] Ensured group_vars/host_vars directories for {inventory_path}")
        
        # inventory, ( )
        if not file_path.exists():
            project_host_vars_dir = get_project_host_vars_dir(project_id)
            fallback_path = project_host_vars_dir / file_name
            if fallback_path.exists():
                file_path = fallback_path
                app_logger.debug(f"[update_host_var] Using fallback host_vars file for update: {fallback_path}")
        
        current_data = {}
        if file_path.exists():
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    current_data = yaml.safe_load(f) or {}
            except Exception as e:
                app_logger.warning(f"Error reading {file_path}: {e}")
                current_data = {}
        
        if var_value is None:
            current_data.pop(var_name, None)
        else:
            current_data[var_name] = var_value
        
        # YAML 
        is_valid, error_msg = validate_yaml_content(current_data)
        if not is_valid:
            app_logger.warning(f"Invalid YAML in host_vars/{file_name}: {error_msg}")
            return jsonify({'success': False, 'error': f'Invalid YAML syntax: {error_msg}'}), 400
        
        if file_path.exists():
            create_backup(file_path)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            yaml.dump(current_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        
        return jsonify({'success': True, 'message': f'Variable {var_name} updated for {file_name}'})
    except Exception as e:
        app_logger.error(f"Error updating host_var: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/group_vars/download', methods=['GET'])
@require_auth
def download_group_vars():
    """ group_vars Project Storage"""
    try:
        # project_id - REQUIRED
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        file_name = request.args.get('file', 'all.yml')
        project_group_vars_dir = get_project_group_vars_dir(project_id)
        file_path = project_group_vars_dir / file_name
        
        if not file_path.exists():
            return jsonify({'success': False, 'error': f'File {file_name} not found in Project Storage'}), 404
        
        return send_file(
            str(file_path),
            mimetype='text/yaml',
            as_attachment=True,
            download_name=file_name
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/group_vars/delete', methods=['POST'])
@require_auth
def delete_group_vars():
    """Delete a group_vars file (Project Storage or inventory-scoped)."""
    try:
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        data = request.json or {}
        file_name = data.get('file')
        inventory_file = data.get('inventory_file')
        # Optional repo-relative path (e.g. "inventories/group_vars/devops_platform.yml").
        # Preferred when the caller already knows the exact file location.
        rel_path = data.get('path')

        if not file_name and not rel_path:
            return jsonify({'success': False, 'error': 'File name not specified'}), 400

        # Try inventory-scoped location first when provided, then fall back to Project Storage.
        candidates = []
        project_dir = get_project_dir(project_id)
        if rel_path:
            # Repo-relative first, then project-relative.
            candidates.append(project_dir / 'repo' / rel_path)
            candidates.append(project_dir / rel_path)
            if not file_name:
                file_name = Path(rel_path).name
        if inventory_file:
            candidates.append(get_group_vars_dir_for_inventory(project_id, inventory_file) / file_name)
        candidates.append(get_project_group_vars_dir(project_id) / file_name)

        # Security: ensure the resolved file lives inside the project dir.
        file_path = None
        for p in candidates:
            try:
                resolved = p.resolve()
                if resolved.exists() and resolved.is_relative_to(project_dir.resolve()):
                    file_path = resolved
                    break
            except Exception:
                continue
        if not file_path:
            return jsonify({'success': False, 'error': f'File {file_name or rel_path} not found'}), 404

        create_backup(file_path)
        file_path.unlink()

        return jsonify({'success': True, 'message': f'File {file_name} deleted'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500



@bp.route('/api/host_vars/list', methods=['GET'])
@require_auth
def list_host_vars_files():
    """ host_vars Project Storage.
    host_vars inventory ( INI).
       """
    try:
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        project_dir = get_project_dir(project_id)
        repo_dir = project_dir / 'repo'
        
        project_host_vars_dir = get_project_host_vars_dir(project_id)
        project_host_vars_dir.mkdir(parents=True, exist_ok=True)
        
        ensure_all_inventory_dirs(project_id)
        
        inventories_dir = get_project_inventories_dir(project_id)
        inventory_files = []
        if inventories_dir.exists():
            for inv_file in inventories_dir.rglob('*.yaml'):
                if inv_file.is_file():
                    rel_path = inv_file.relative_to(inventories_dir)
                    if 'host_vars' not in rel_path.parts and 'group_vars' not in rel_path.parts:
                        inventory_files.append(str(inv_file.relative_to(repo_dir)))
            for inv_file in inventories_dir.rglob('*.yml'):
                if inv_file.is_file():
                    rel_path = inv_file.relative_to(inventories_dir)
                    if 'host_vars' not in rel_path.parts and 'group_vars' not in rel_path.parts:
                        inventory_files.append(str(inv_file.relative_to(repo_dir)))
            for inv_file in inventories_dir.rglob('*.ini'):
                if inv_file.is_file():
                    rel_path = inv_file.relative_to(inventories_dir)
                    if 'host_vars' not in rel_path.parts and 'group_vars' not in rel_path.parts:
                        inventory_files.append(str(inv_file.relative_to(repo_dir)))
            for inv_file in inventories_dir.rglob('*'):
                if inv_file.is_file() and inv_file.name in ('hosts',) and inv_file.suffix == '':
                    rel_path = inv_file.relative_to(inventories_dir)
                    if 'host_vars' not in rel_path.parts and 'group_vars' not in rel_path.parts:
                        path_str = str(inv_file.relative_to(repo_dir))
                        if path_str not in inventory_files:
                            inventory_files.append(path_str)
        
        root_inv = repo_dir / 'inventory.yml'
        if root_inv.exists():
            inventory_files.append('inventory.yml')
        
        inventory_files_full = [str(repo_dir / p) for p in inventory_files if (repo_dir / p).exists()]
        hosts_from_inv = get_inventory_hosts(inventory_files_full) if inventory_files_full else []
        
        for h in hosts_from_inv:
            host_name = h.get('name')
            if not host_name:
                continue
            inventory_file = h.get('inventory_file', '')
            host_vars_dir = get_host_vars_dir_for_inventory(project_id, inventory_file)
            host_vars_dir.mkdir(parents=True, exist_ok=True)
            host_file = host_vars_dir / f"{host_name}.yml"
            if not host_file.exists():
                try:
                    with open(host_file, 'w', encoding='utf-8') as f:
                        yaml_loader.dump({}, f)
                    app_logger.info(f"Auto-created host_vars file for host: {host_name} at {host_file}")
                except Exception as e:
                    app_logger.error(f"Error creating host_vars file for {host_name}: {e}")
        
        # host_vars 
        host_vars_files_dict = {}  # dict 
        
        # 1. vars/host_vars/
        for ext in ['*.yml', '*.yaml']:
            for file_path in project_host_vars_dir.glob(ext):
                # Skip backup directory
                if 'backups' in str(file_path):
                    continue
                if file_path.is_file():
                    file_name = file_path.name
                    if file_name not in host_vars_files_dict:
                        host_vars_files_dict[file_name] = {
                            'name': file_name,
                            'path': f'host_vars/{file_name}'
                        }
        
        # 2. inventory : inventories/*/host_vars/
        inventories_dir = get_project_inventories_dir(project_id)
        if inventories_dir.exists():
            for host_vars_dir in inventories_dir.rglob('host_vars'):
                if host_vars_dir.is_dir():
                    for ext in ['*.yml', '*.yaml']:
                        for file_path in host_vars_dir.glob(ext):
                            # Skip backup directory
                            if 'backups' in str(file_path):
                                continue
                            if file_path.is_file():
                                file_name = file_path.name
                                # repo
                                repo_rel_path = file_path.relative_to(repo_dir)
                                if file_name not in host_vars_files_dict:
                                    host_vars_files_dict[file_name] = {
                                        'name': file_name,
                                        'path': str(repo_rel_path)
                                    }
        
        # 3. inventory.yml ( )
        root_host_vars_dir = repo_dir / 'host_vars'
        if root_host_vars_dir.exists() and root_host_vars_dir.is_dir():
            for ext in ['*.yml', '*.yaml']:
                for file_path in root_host_vars_dir.glob(ext):
                    if 'backups' in str(file_path):
                        continue
                    if file_path.is_file():
                        file_name = file_path.name
                        if file_name not in host_vars_files_dict:
                            host_vars_files_dict[file_name] = {
                                'name': file_name,
                                'path': f'host_vars/{file_name}'
                            }
        
        # dict 
        host_vars_files = list(host_vars_files_dict.values())
        host_vars_files.sort(key=lambda x: x['name'])
        
        return jsonify({'success': True, 'files': host_vars_files})
    except Exception as e:
        app_logger.error(f"Error listing host_vars files: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/host_vars/get', methods=['GET'])
@require_auth
def get_host_vars():
    """ host_vars Project Storage"""
    try:
        # project_id - REQUIRED, no fallback
        project_id = require_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        file_name = request.args.get('file')
        inventory_file = request.args.get('inventory_file')  # inventory ()
        rel_path = request.args.get('path')
        
        if not file_name:
            return jsonify({'success': False, 'error': 'File name not specified'}), 400
        
        project_dir = get_project_dir(project_id)
        file_path = None

        if rel_path:
            for candidate in [project_dir / 'repo' / rel_path, project_dir / rel_path]:
                try:
                    resolved = candidate.resolve()
                    if resolved.exists() and resolved.is_file() and resolved.is_relative_to(project_dir.resolve()):
                        file_path = resolved
                        break
                except Exception:
                    continue

        # inventory 
        if not file_path and not inventory_file:
            # inventory_file 
            host_name = file_name.replace('.yml', '').replace('.yaml', '')
            hosts_list = get_inventory_hosts()
            for h in hosts_list:
                if h['name'] == host_name:
                    inventory_file = h.get('inventory_file', '')
                    if inventory_file:
                        app_logger.info(f"Found inventory_file for host {host_name}: {inventory_file}")
                        break
        
        # host_vars inventory 
        if not file_path:
            host_vars_dir = get_host_vars_dir_for_inventory(project_id, inventory_file)
            file_path = host_vars_dir / file_name
        
        # inventory, ( )
        if not file_path.exists():
            project_host_vars_dir = get_project_host_vars_dir(project_id)
            fallback_path = project_host_vars_dir / file_name
            if fallback_path.exists():
                file_path = fallback_path
                app_logger.debug(f"Using fallback host_vars file: {fallback_path}")
        
        if not file_path.exists():
            return jsonify({'success': False, 'error': f'File {file_name} not found in Project Storage'}), 404
        
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        vault_id = request.args.get('vault_id') or request.args.get('vaultId')
        decrypted, was_encrypted, vault_id_used, vault_id_required = _decrypt_content_if_encrypted(project_id, content, vault_id)
        if was_encrypted:
            if vault_id_required:
                vaults = _load_vaults(project_id)
                return jsonify({
                    'success': True,
                    'encrypted': True,
                    'vaultIdRequired': True,
                    'vaults': vaults,
                    'file': file_name
                })
            if decrypted is None:
                return jsonify({'success': False, 'error': 'Failed to decrypt vault-encrypted file. Check vault key.'}), 500
            content = decrypted
        resp = {'success': True, 'content': content, 'file': file_name}
        try:
            repo_dir = project_dir / 'repo'
            resp['path'] = str(file_path.relative_to(repo_dir)).replace('\\', '/')
        except Exception:
            pass
        if was_encrypted:
            resp['encrypted'] = True
            resp['vaultId'] = vault_id_used
        return jsonify(resp)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/host_vars/save', methods=['POST'])
@require_auth
def save_host_vars():
    """ host_vars """
    try:
        # projectId - REQUIRED, no fallback
        project_id = require_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        data = request.json or {}
        file_name = data.get('file')
        content = data.get('content', '')
        inventory_file = data.get('inventory_file')  # inventory ()
        vault_id = data.get('vaultId') or data.get('vault_id')
        rel_path = data.get('path')
        
        app_logger.info(f"User saving host_vars file: {file_name} to project {project_id}, inventory_file: {inventory_file}")
        
        if not file_name:
            return jsonify({'success': False, 'error': 'File name not specified'}), 400
        
        if not content:
            return jsonify({'success': False, 'error': 'File content cannot be empty'}), 400
        
        # YAML ( )
        is_valid, error_msg = validate_yaml_content(content)
        if not is_valid:
            app_logger.warning(f"Invalid YAML in host_vars/{file_name}: {error_msg}")
            return jsonify({'success': False, 'error': f'Invalid YAML syntax: {error_msg}'}), 400
        
        # Ansible-vault: vault_id
        if vault_id:
            encrypted_content, err = _encrypt_content_if_requested(project_id, content, vault_id)
            if err:
                return jsonify({'success': False, 'error': err}), 400
            content = encrypted_content

        # Exact repo-relative path wins when the caller selected a known file.
        # This prevents saving "host.yml" into another host_vars directory when
        # duplicate file names exist across inventories.
        if rel_path:
            rel_path = str(rel_path).replace('\\', '/').strip('/')
            if '..' in rel_path or 'host_vars' not in rel_path.split('/'):
                return jsonify({'success': False, 'error': 'Invalid host_vars path'}), 400
            project_dir = get_project_dir(project_id)
            candidates = [project_dir / 'repo' / rel_path, project_dir / rel_path]
            file_path = None
            for candidate in candidates:
                try:
                    resolved = candidate.resolve()
                    if resolved.is_relative_to(project_dir.resolve()):
                        file_path = resolved
                        break
                except Exception:
                    continue
            if not file_path:
                return jsonify({'success': False, 'error': 'Invalid host_vars path'}), 400
            file_path.parent.mkdir(parents=True, exist_ok=True)
            if file_path.exists():
                create_backup(file_path)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return jsonify({'success': True, 'message': f'File {file_path.name} saved'})
        
        # inventory 
        # inventory_file , 
        if not inventory_file:
            # (, "192.168.1.133.yml" -> "192.168.1.133")
            host_name = file_name.replace('.yml', '').replace('.yaml', '')
            app_logger.debug(f"[save_host_vars] Looking for inventory_file for host: {host_name}")
            hosts_list = get_inventory_hosts()
            for h in hosts_list:
                if h['name'] == host_name:
                    inventory_file = h.get('inventory_file', '')
                    if inventory_file:
                        app_logger.info(f"[save_host_vars] Found inventory_file for host {host_name}: {inventory_file}")
                        break
            if not inventory_file:
                app_logger.warning(f"[save_host_vars] Could not find inventory_file for host {host_name}, will use fallback directory")
        else:
            app_logger.info(f"[save_host_vars] Using provided inventory_file: {inventory_file}")
        
        # host_vars inventory 
        host_vars_dir = get_host_vars_dir_for_inventory(project_id, inventory_file)
        host_vars_dir.mkdir(parents=True, exist_ok=True)
        file_path = host_vars_dir / file_name
        
        app_logger.info(f"[save_host_vars] Saving host_vars to: {file_path} (inventory_file: {inventory_file}, host_vars_dir: {host_vars_dir})")
        
        # inventory_file , , group_vars host_vars inventory 
        if inventory_file:
            project_dir = get_project_dir(project_id)
            repo_dir = project_dir / 'repo'
            
            # inventory 
            inventory_path = None
            repo_path = repo_dir / inventory_file
            if repo_path.exists():
                inventory_path = repo_path
            else:
                found_files = list(repo_dir.rglob(Path(inventory_file).name))
                if found_files:
                    inventory_path = found_files[0]
            
            if inventory_path and inventory_path.exists():
                # group_vars host_vars inventory 
                ensure_inventory_dirs(inventory_path)
                app_logger.info(f"[save_host_vars] Ensured group_vars/host_vars directories for {inventory_path}")
        
        if file_path.exists():
            create_backup(file_path)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return jsonify({'success': True, 'message': f'File {file_name} saved'})
    except Exception as e:
        app_logger.error(f"Error saving host_vars: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/host_vars/download', methods=['GET'])
@require_auth
def download_host_vars():
    """ host_vars Project Storage"""
    try:
        # project_id - REQUIRED
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        file_name = request.args.get('file')
        if not file_name:
            return jsonify({'success': False, 'error': 'File name not specified'}), 400
        
        project_host_vars_dir = get_project_host_vars_dir(project_id)
        file_path = project_host_vars_dir / file_name
        
        if not file_path.exists():
            return jsonify({'success': False, 'error': f'File {file_name} not found in Project Storage'}), 404
        
        return send_file(
            str(file_path),
            mimetype='text/yaml',
            as_attachment=True,
            download_name=file_name
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/host_vars/delete', methods=['POST'])
@require_auth
def delete_host_vars():
    """Delete a host_vars file (Project Storage or inventory-scoped)."""
    try:
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        data = request.json or {}
        file_name = data.get('file')
        inventory_file = data.get('inventory_file')
        rel_path = data.get('path')

        if not file_name and not rel_path:
            return jsonify({'success': False, 'error': 'File name not specified'}), 400

        candidates = []
        project_dir = get_project_dir(project_id)
        if rel_path:
            candidates.append(project_dir / 'repo' / rel_path)
            candidates.append(project_dir / rel_path)
            if not file_name:
                file_name = Path(rel_path).name
        if inventory_file:
            candidates.append(get_host_vars_dir_for_inventory(project_id, inventory_file) / file_name)
        candidates.append(get_project_host_vars_dir(project_id) / file_name)

        file_path = None
        for p in candidates:
            try:
                resolved = p.resolve()
                if resolved.exists() and resolved.is_relative_to(project_dir.resolve()):
                    file_path = resolved
                    break
            except Exception:
                continue
        if not file_path:
            return jsonify({'success': False, 'error': f'File {file_name or rel_path} not found'}), 404

        create_backup(file_path)
        file_path.unlink()

        return jsonify({'success': True, 'message': f'File {file_name} deleted'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500




@bp.route('/api/host_vars/create', methods=['POST'])
@require_auth
def create_host_vars():
    """ host_vars Project Storage"""
    try:
        # project_id - REQUIRED
        project_id = get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        data = request.json or {}
        file_name = data.get('file')
        
        if not file_name:
            return jsonify({'success': False, 'error': 'File name not specified'}), 400
        
        if not (file_name.endswith('.yml') or file_name.endswith('.yaml')):
            return jsonify({'success': False, 'error': 'File name must end with .yml or .yaml'}), 400
        
        project_host_vars_dir = get_project_host_vars_dir(project_id)
        project_host_vars_dir.mkdir(parents=True, exist_ok=True)
        file_path = project_host_vars_dir / file_name
        
        if file_path.exists():
            return jsonify({'success': False, 'error': f'File {file_name} already exists'}), 400
        
        template = f"# Host-specific variables for {file_name.replace('.yml', '').replace('.yaml', '')}\n"
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(template)
        
        return jsonify({'success': True, 'message': f'File {file_name} created'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


