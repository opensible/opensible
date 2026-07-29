"""Data routes: /api/all_data, /api/preview, /api/save, /api/hosts,
/api/inventory/hosts, /api/dashboard_stats.

Extracted from ``backend/app.py`` during the phased refactor. Routes,
methods, view-function names, and response shapes preserved 1:1.

App-module helpers (functions that still live in ``app.py``) are
resolved lazily via ``_app_module()`` so this blueprint never triggers
a circular import.
"""
from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

try:
    from auth.middleware import require_auth
except ImportError:  # pragma: no cover
    from ..auth.middleware import require_auth

from utils.request_ctx import require_project_id_from_request
from utils.project_paths import (
    get_project_dir,
    get_project_inventories_dir,
    get_project_inventory_file,
)
from utils.yaml_io import (
    yaml_loader,
    parse_yaml_with_comments,
    save_yaml_with_comments,
    get_variable_descriptions,
)
from utils.host_status import get_host_check_status


bp = Blueprint("data_api", __name__)


# ---------------------------------------------------------------------------
# Lazy app-module proxies
# ---------------------------------------------------------------------------
def _app_module():
    for name in ("__main__", "app"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "get_inventory_hosts"):
            return mod
    import app as _a  # noqa
    return _a


_APP_NAMES = (
    "get_project_group_vars_dir",
    "get_project_host_vars_dir",
    "get_host_vars_dir_for_inventory",
    "get_group_vars_dir_for_inventory",
    "find_inventory_file_for_group",
    "ensure_group_vars_host_vars_from_inventory",
    "ensure_inventory_dirs",
    "get_inventory_hosts",
    "get_inventory_groups",
    "get_project_id_from_request",
    "get_repo_layout",
    "BASE_DIR",
)


def __getattr__(name):
    if name in _APP_NAMES:
        return getattr(_app_module(), name)
    raise AttributeError(name)


def _log():
    return current_app.logger


def _load_vars_file_safe(project_id, file_path):
    """Load a group_vars/host_vars YAML file, decrypting vault content when needed.

    Always returns a ``dict`` so callers can safely ``.update(...)`` the result.
    Vault-encrypted files that cannot be decrypted (missing/wrong vault key) are
    returned as ``{}`` rather than raising, so the rest of the payload still loads.
    """
    try:
        if not file_path.exists():
            return {}
        with open(file_path, "r", encoding="utf-8") as f:
            raw = f.read()
        if raw.lstrip().startswith("$ANSIBLE_VAULT"):
            try:
                m = _app_module()
                decrypted, was_encrypted, _vid, vault_id_required = \
                    m._decrypt_content_if_encrypted(project_id, raw, None)
                if was_encrypted and not vault_id_required and decrypted:
                    raw = decrypted
                else:
                    return {}
            except Exception as e:
                _log().warning(f"vault decrypt failed for {file_path}: {e}")
                return {}
            try:
                data = yaml_loader.load(StringIO(raw))
            except Exception as e:
                _log().warning(f"YAML parse failed for {file_path}: {e}")
                return {}
        else:
            data = parse_yaml_with_comments(file_path)
        if not isinstance(data, dict):
            return {}
        # parse_yaml_with_comments returns {'_error': ...} on failure — treat as empty.
        if set(data.keys()) == {"_error"}:
            return {}
        return data
    except Exception as e:
        _log().warning(f"_load_vars_file_safe error for {file_path}: {e}")
        return {}



# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@bp.route('/api/all_data')
@require_auth
def get_all_data():
    """Return merged group_vars/host_vars/hosts/groups payload for a project."""
    try:
        project_id = require_project_id_from_request()
        if not project_id:
            return jsonify({'error': 'Project ID is required'}), 400

        selected_files = request.args.getlist('inventory_files')  # noqa: F841 (parity)

        m = _app_module()
        project_group_vars_dir = m.get_project_group_vars_dir(project_id)
        project_host_vars_dir = m.get_project_host_vars_dir(project_id)
        project_inventory_file = get_project_inventory_file(project_id)

        descriptions = get_variable_descriptions()

        inventory_files_to_use = []
        project_dir = get_project_dir(project_id)
        inventories_dir = get_project_inventories_dir(project_id)
        if inventories_dir.exists():
            for inv_file in inventories_dir.rglob('*'):
                if not inv_file.is_file():
                    continue
                if not (inv_file.suffix in ('.yml', '.yaml', '.ini') or inv_file.name in ('hosts', 'hosts.ini')):
                    continue
                rel = inv_file.relative_to(inventories_dir)
                if 'group_vars' in rel.parts or 'host_vars' in rel.parts:
                    continue
                path_str = str(inv_file)
                if path_str not in inventory_files_to_use:
                    inventory_files_to_use.append(path_str)
        if not inventory_files_to_use and project_inventory_file.exists():
            inventory_files_to_use = [str(project_inventory_file)]

        if inventory_files_to_use:
            m.ensure_group_vars_host_vars_from_inventory(project_id, inventory_files_to_use)

        hosts = m.get_inventory_hosts(inventory_files_to_use) if inventory_files_to_use else []

        inventory_path_mapping = {}
        for full_path in inventory_files_to_use:
            full_path_obj = Path(full_path)
            try:
                rel_path = full_path_obj.relative_to(project_dir)
                rel_path_str = str(rel_path)
                if rel_path_str.startswith('repo/'):
                    rel_path_str = rel_path_str[5:]
                elif rel_path_str.startswith('inventory/'):
                    rel_path_str = rel_path_str[10:]
                inventory_path_mapping[full_path] = rel_path_str
            except ValueError:
                inventory_path_mapping[full_path] = full_path_obj.name

        for host in hosts:
            if host.get('inventory_file'):
                host_inventory_file = host['inventory_file']
                if host_inventory_file in inventory_path_mapping:
                    host['inventory_file'] = inventory_path_mapping[host_inventory_file]
                else:
                    for full_path, rel_path in inventory_path_mapping.items():
                        if full_path == host_inventory_file or full_path.endswith(host_inventory_file) or Path(full_path).name == Path(host_inventory_file).name:
                            host['inventory_file'] = rel_path
                            break
                    else:
                        host['inventory_file'] = Path(host_inventory_file).name

        groups = m.get_inventory_groups(inventory_files_to_use) if inventory_files_to_use else {}

        for group_name, group_data in groups.items():
            if isinstance(group_data, dict) and group_data.get('inventory_file'):
                group_inventory_file = group_data['inventory_file']
                if group_inventory_file in inventory_path_mapping:
                    group_data['inventory_file'] = inventory_path_mapping[group_inventory_file]
                else:
                    for full_path, rel_path in inventory_path_mapping.items():
                        if full_path == group_inventory_file or full_path.endswith(group_inventory_file) or Path(full_path).name == Path(group_inventory_file).name:
                            group_data['inventory_file'] = rel_path
                            break
                    else:
                        if Path(group_inventory_file).is_absolute():
                            group_data['inventory_file'] = Path(group_inventory_file).name

        group_vars = {}
        group_vars_by_group = {}

        all_group_vars_file = project_group_vars_dir / 'all.yml'
        all_group_vars = _load_vars_file_safe(project_id, all_group_vars_file)
        group_vars.update(all_group_vars)
        group_vars_by_group['all'] = all_group_vars

        repo_dir = project_dir / 'repo'  # noqa: F841 (parity)
        inventories_dir = get_project_inventories_dir(project_id)

        if inventories_dir.exists():
            for group_vars_dir in inventories_dir.rglob('group_vars'):
                if group_vars_dir.is_dir():
                    for ext in ['*.yml', '*.yaml']:
                        for group_file in group_vars_dir.glob(ext):
                            if group_file.is_file():
                                group_name = group_file.stem
                                if group_name == 'all':
                                    continue

                                group_vars_data = _load_vars_file_safe(project_id, group_file)
                                if group_vars_data:
                                    if group_name in group_vars_by_group:
                                        group_vars_by_group[group_name].update(group_vars_data)
                                    else:
                                        group_vars_by_group[group_name] = group_vars_data
                                    group_vars.update(group_vars_data)
                                    _log().debug(f"Loaded group_vars for {group_name} from {group_file}")

        for group_name in groups.keys():
            if group_name == 'all':
                continue

            if group_name not in group_vars_by_group:
                fallback_file = project_group_vars_dir / f"{group_name}.yml"
                if fallback_file.exists():
                    group_vars_data = _load_vars_file_safe(project_id, fallback_file)
                    if group_vars_data:
                        group_vars.update(group_vars_data)
                        group_vars_by_group[group_name] = group_vars_data
                        _log().debug(f"Loaded group_vars for {group_name} from fallback: {fallback_file}")
                else:
                    group_vars_by_group[group_name] = {}

        host_vars_data = {}
        for host in hosts:
            host_name = host['name']
            inventory_file = host.get('inventory_file', '')

            host_vars_dir = m.get_host_vars_dir_for_inventory(project_id, inventory_file)
            host_file = host_vars_dir / f"{host_name}.yml"

            if not host_file.exists():
                fallback_file = project_host_vars_dir / f"{host_name}.yml"
                if fallback_file.exists():
                    host_file = fallback_file
                    _log().debug(f"Using fallback host_vars file for {host_name}: {fallback_file}")

            host_vars_data[host_name] = _load_vars_file_safe(project_id, host_file)


        for group_name, group_data in groups.items():
            if isinstance(group_data, dict) and group_data.get('vars'):
                gvars = group_data['vars']
                group_vars_by_group.setdefault(group_name, {}).update(gvars)
                group_vars.update(gvars)
        for h in hosts:
            if h.get('vars'):
                hn = h['name']
                host_vars_data.setdefault(hn, {})
                host_vars_data[hn].update(h['vars'])

        all_keys = set(group_vars.keys())
        for group_vars_data in group_vars_by_group.values():
            all_keys.update(group_vars_data.keys())
        for host_vars in host_vars_data.values():
            all_keys.update(host_vars.keys())

        all_keys = {str(k) for k in all_keys if not str(k).startswith('_')}

        hosts_info = {h['name']: {'inventory_file': h.get('inventory_file'), 'vars_file': h.get('vars_file')} for h in hosts}

        host_statuses = {}
        for h in hosts:
            host_name = h['name']
            status_data = get_host_check_status(project_id, host_name)
            if status_data:
                host_statuses[host_name] = status_data
                host_info = hosts_info.get(host_name, {})
                host_info['status'] = status_data.get('status', 'unknown')
                host_info['last_checked_at'] = status_data.get('last_checked_at')
                host_info['status_expires_at'] = status_data.get('status_expires_at')
                hosts_info[host_name] = host_info
            else:
                host_info = hosts_info.get(host_name, {})
                host_info.setdefault('status', 'unknown')
                host_info.setdefault('last_checked_at', None)
                host_info.setdefault('status_expires_at', None)
                hosts_info[host_name] = host_info

        def _sanitize(o):
            if isinstance(o, dict):
                return {(k if isinstance(k, (str, int, float, bool)) or k is None else str(k)): _sanitize(v) for k, v in o.items()}
            if isinstance(o, (list, tuple)):
                return [_sanitize(x) for x in o]
            return o

        return jsonify(_sanitize({
            'success': True,
            'group_vars': group_vars,
            'group_vars_by_group': group_vars_by_group,
            'host_vars': host_vars_data,
            'hosts': [h['name'] for h in hosts],
            'hosts_info': hosts_info,
            'hosts_status': host_statuses,
            'groups': groups,
            'all_keys': sorted(list(all_keys)),
            'descriptions': descriptions,
            'project_id': project_id
        }))
    except Exception as e:
        _log().error(f"Error in get_all_data: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/preview', methods=['POST'])
@require_auth
def preview_data():
    """Return YAML-serialized preview of group_vars/host_vars."""
    try:
        if not request.is_json:
            return jsonify({'success': False, 'error': 'Request must be JSON'}), 400

        data = request.json
        if data is None:
            return jsonify({'success': False, 'error': 'Empty request body'}), 400

        group_vars = data.get('group_vars', {}) or {}
        host_vars = data.get('host_vars', {}) or {}

        clean_group_vars = {k: v for k, v in group_vars.items() if not k.startswith('_')}

        stream = StringIO()
        yaml_loader.dump(clean_group_vars, stream)
        group_vars_yaml = stream.getvalue()

        host_vars_yaml = {}
        for host, vars_data in host_vars.items():
            if vars_data:
                clean_host_vars = {k: v for k, v in vars_data.items() if not k.startswith('_')}
                stream = StringIO()
                yaml_loader.dump(clean_host_vars, stream)
                host_vars_yaml[host] = stream.getvalue()

        return jsonify({
            'success': True,
            'group_vars': group_vars_yaml,
            'host_vars': host_vars_yaml
        })
    except Exception as e:
        _log().error(f'Error in preview_data: {str(e)}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/save', methods=['POST'])
@require_auth
def save_all_data():
    """Save group_vars/host_vars for a project."""
    try:
        m = _app_module()
        project_id = m.get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        data = request.json
        group_vars = data.get('group_vars', {})
        host_vars = data.get('host_vars', {})
        backup_settings = data.get('backup_settings', {})

        backup_group_vars = backup_settings.get('group_vars', False)
        backup_host_vars = backup_settings.get('host_vars', False)

        project_dir = get_project_dir(project_id)
        repo_dir = project_dir / 'repo'

        project_group_vars_dir = m.get_project_group_vars_dir(project_id)
        project_host_vars_dir = m.get_project_host_vars_dir(project_id)  # noqa: F841 (parity)

        _log().info(f"User saving variables to project {project_id}: group_vars ({len(group_vars)} variables), host_vars ({len(host_vars)} hosts), backup_group_vars={backup_group_vars}, backup_host_vars={backup_host_vars}")

        hosts_list = m.get_inventory_hosts()
        inventory_files_full = []
        for h in hosts_list:
            inv_file = h.get('inventory_file', '')
            if inv_file:
                full_path = repo_dir / inv_file
                if full_path.exists():
                    inventory_files_full.append(str(full_path))
        groups = m.get_inventory_groups(inventory_files_full)  # noqa: F841 (parity)

        is_dict_of_dicts = group_vars and all(isinstance(v, dict) for v in group_vars.values())

        if not is_dict_of_dicts:
            group_vars_file = project_group_vars_dir / 'all.yml'
            project_group_vars_dir.mkdir(parents=True, exist_ok=True)
            try:
                backup_param = backup_group_vars if backup_group_vars is not None else None
                if not save_yaml_with_comments(group_vars_file, group_vars, create_backup_file=backup_param):
                    _log().error("Error saving group_vars")
                    return jsonify({'success': False, 'message': 'Error saving group_vars'}), 500
            except ValueError as e:
                _log().error(f"YAML validation error for group_vars: {e}")
                return jsonify({'success': False, 'message': f'Invalid YAML in group_vars: {str(e)}'}), 400
        else:
            for group_name, vars_data in group_vars.items():
                if not isinstance(vars_data, dict):
                    continue

                inventory_file = m.find_inventory_file_for_group(project_id, group_name)

                group_vars_dir = m.get_group_vars_dir_for_inventory(project_id, inventory_file)
                group_vars_dir.mkdir(parents=True, exist_ok=True)

                if inventory_file:
                    project_dir2 = get_project_dir(project_id)
                    repo_dir2 = project_dir2 / 'repo'
                    inventory_path = repo_dir2 / inventory_file
                    if inventory_path.exists():
                        m.ensure_inventory_dirs(inventory_path)

                group_file = group_vars_dir / f'{group_name}.yml'
                _log().info(f"Saving group_vars for {group_name} to {group_file} (inventory_file: {inventory_file})")

                try:
                    backup_param = backup_group_vars if backup_group_vars is not None else None
                    if not save_yaml_with_comments(group_file, vars_data, create_backup_file=backup_param):
                        _log().error(f"Error saving group_vars for {group_name}")
                        return jsonify({'success': False, 'message': f'Error saving group_vars for {group_name}'}), 500
                except ValueError as e:
                    _log().error(f"YAML validation error for group_vars/{group_name}: {e}")
                    return jsonify({'success': False, 'message': f'Invalid YAML in group_vars for {group_name}: {str(e)}'}), 400

        hosts_list = m.get_inventory_hosts()
        hosts_info_dict = {h['name']: h for h in hosts_list}

        for host, vars_data in host_vars.items():
            host_info = hosts_info_dict.get(host, {})
            inventory_file = host_info.get('inventory_file', '')

            host_vars_dir = m.get_host_vars_dir_for_inventory(project_id, inventory_file)
            host_vars_dir.mkdir(parents=True, exist_ok=True)

            host_file = host_vars_dir / f'{host}.yml'
            _log().info(f"Saving host_vars for {host} to {host_file} (inventory_file: {inventory_file})")

            try:
                backup_param = backup_host_vars if backup_host_vars is not None else None
                if not save_yaml_with_comments(host_file, vars_data, create_backup_file=backup_param):
                    _log().error(f"Error saving host_vars for {host}")
                    return jsonify({'success': False, 'message': f'Error saving host_vars for {host}'}), 500
            except ValueError as e:
                _log().error(f"YAML validation error for host_vars/{host}: {e}")
                return jsonify({'success': False, 'message': f'Invalid YAML in host_vars for {host}: {str(e)}'}), 400

        _log().info("Variables saved successfully")
        return jsonify({
            'success': True,
            'message': 'All variables saved successfully'
        })
    except Exception as e:
        _log().error(f"Error saving variables: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/hosts')
@require_auth
def get_hosts():
    """Return names of all hosts across the inventory."""
    hosts = _app_module().get_inventory_hosts()
    return jsonify({'hosts': [h['name'] for h in hosts]})


@bp.route('/api/inventory/hosts', methods=['GET'])
@require_auth
def api_get_inventory_hosts():
    """API: return hosts for the selected inventory files (or all)."""
    try:
        m = _app_module()
        project_id = m.get_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400

        selected_files = request.args.getlist('inventory_files')
        project_dir = get_project_dir(project_id)
        project_inventory_file = get_project_inventory_file(project_id)

        inventory_files_to_use = []

        if selected_files:
            inventories_dir = get_project_inventories_dir(project_id)
            layout = m.get_repo_layout(project_id)
            inventories_layout_path = layout.get('inventories', 'inventories')

            for file_path_param in selected_files:
                file_path = None
                if file_path_param.startswith(f'{inventories_layout_path}/'):
                    rel_path = file_path_param[len(inventories_layout_path) + 1:]
                    file_path = inventories_dir / rel_path
                elif file_path_param.startswith('inventories/'):
                    rel_path = file_path_param[len('inventories/'):]
                    file_path = inventories_dir / rel_path
                elif file_path_param in ('inventory.yml', 'inventory.yaml', 'hosts.yml', 'hosts.yaml', 'hosts', 'hosts.ini'):
                    file_path = inventories_dir / file_path_param
                    if not file_path.exists():
                        file_path = project_dir / 'repo' / file_path_param
                elif '/' in file_path_param and not file_path_param.startswith('inventory/'):
                    file_path = inventories_dir / file_path_param
                else:
                    file_path = inventories_dir / file_path_param

                if file_path and file_path.exists():
                    inventory_files_to_use.append(str(file_path))
        else:
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

        hosts = m.get_inventory_hosts(inventory_files_to_use) if inventory_files_to_use else []
        host_names = [h['name'] for h in hosts]

        return jsonify({
            'success': True,
            'hosts': host_names
        })
    except Exception as e:
        _log().error(f"Error getting inventory hosts: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/dashboard_stats')
@require_auth
def get_dashboard_stats():
    """Dashboard summary counts (hosts, roles, tasks, variables)."""
    try:
        m = _app_module()
        hosts = m.get_inventory_hosts()
        hosts_count = len(hosts)

        roles_dir = m.BASE_DIR / 'roles'
        roles_count = 0
        if roles_dir.exists():
            for item in roles_dir.iterdir():
                if item.is_dir() and not item.name.startswith('_') and item.name != 'disabled':
                    tasks_dir = item / 'tasks'
                    if tasks_dir.exists():
                        if (tasks_dir / 'main.yaml').exists() or (tasks_dir / 'main.yml').exists():
                            roles_count += 1

        tasks_count = 0
        if roles_dir.exists():
            for task_file in roles_dir.rglob('tasks/main.*'):
                if 'disabled' not in str(task_file):
                    if task_file.name in ['main.yaml', 'main.yml']:
                        tasks_count += 1

        project_id = m.get_project_id_from_request()
        variables_count = 0
        if project_id:
            project_group_vars_dir = m.get_project_group_vars_dir(project_id)
            group_vars_file = project_group_vars_dir / 'all.yml'
            if group_vars_file.exists():
                group_vars = parse_yaml_with_comments(group_vars_file)
                variables_count = len([k for k in group_vars.keys() if not k.startswith('_')])

        return jsonify({
            'success': True,
            'stats': {
                'hosts': hosts_count,
                'roles': roles_count,
                'tasks': tasks_count,
                'variables': variables_count
            }
        })
    except Exception as e:
        _log().error(f'Error in get_dashboard_stats: {str(e)}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500
