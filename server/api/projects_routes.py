"""Project CRUD + host status/settings routes (Phase 3)."""
from __future__ import annotations

import importlib
import json
import sys
import time
import uuid
from flask import Blueprint, current_app, jsonify, request

from auth.middleware import require_auth
from utils.host_status import HOST_STATUS_TTL_DEFAULT, get_host_check_status

bp = Blueprint("projects_api", __name__)


def _app_module():
    for module_name in ("__main__", "app"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "load_projects"):
            return module
    return importlib.import_module("app")


class _AppLogger:
    def __getattr__(self, name):
        return getattr(current_app.logger, name)


app_logger = _AppLogger()


_APP_NAMES = {
    "load_projects",
    "save_projects",
    "load_project_config",
    "save_project_config",
    "get_project_dir",
    "get_project_hosts_list",
    "create_minimal_ansible_config",
    "BASE_DIR",
    "HOST_STATUS_TTL_DEFAULT",
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


for _name in _APP_NAMES - {"HOST_STATUS_TTL_DEFAULT"}:
    globals()[_name] = _LazyAppProxy(_name)


# ============================================================================

@bp.route('/api/projects', methods=['GET'])
@require_auth
def api_list_projects():
    """API: """
    try:
        projects = load_projects()
        # ( include_archived)
        include_archived = request.args.get('include_archived', 'false').lower() == 'true'
        if not include_archived:
            projects = [p for p in projects if not p.get('isArchived', False)]
        
        return jsonify({'success': True, 'projects': projects})
    except Exception as e:
        app_logger.error(f"Error listing projects: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects', methods=['POST'])
@require_auth
def api_create_project():
    """API: 
    
    Creates a truly empty project with:
    - All required directories (mkdir only, no content copied)
    - project.json with explicit defaults for sources (local mode)
    - Empty .sync_state.json
    - No fallback to BASE_DIR or Default Project data
    """
    try:
        data = request.json or {}
        
        name = data.get('name', '').strip()
        if not name:
            return jsonify({'success': False, 'error': 'Project name is required'}), 400
        
        projects = load_projects()
        if any(p.get('name') == name and not p.get('isArchived', False) for p in projects):
            return jsonify({'success': False, 'error': 'Project with this name already exists'}), 400
        
        project = {
            'id': str(uuid.uuid4()),
            'name': name,
            'description': data.get('description', '').strip(),
            'createdAt': time.time(),
            'updatedAt': time.time(),
            'createdBy': data.get('createdBy', 'current_user'),  # TODO: 
            'isArchived': False
        }
        
        projects.append(project)
        save_projects(projects)
        
        project_dir = get_project_dir(project['id'])
        project_dir.mkdir(exist_ok=True)
        
        # Create new structure directories
        # repo/ - Git-synced workspace ( )
        repo_dir = project_dir / 'repo'
        repo_dir.mkdir(exist_ok=True)
        (repo_dir / 'roles').mkdir(exist_ok=True)
        (repo_dir / 'playbooks').mkdir(exist_ok=True)
        (repo_dir / 'inventories').mkdir(exist_ok=True)
        (repo_dir / 'group_vars').mkdir(exist_ok=True)  # optional shared
        (repo_dir / 'host_vars').mkdir(exist_ok=True)    # optional shared
        (repo_dir / 'scripts').mkdir(exist_ok=True)
        
        # prod/stage - Git
        
        # ansible.cfg: ansible-config/
        ansible_config_dir = project_dir / 'ansible-config'
        ansible_config_dir.mkdir(parents=True, exist_ok=True)
        default_ansible_cfg = ansible_config_dir / 'ansible.cfg'
        if not default_ansible_cfg.exists():
            create_minimal_ansible_config(default_ansible_cfg)
            app_logger.info(f"Created ansible-config/ansible.cfg for project {project['id']}")
        
        # ui/ - UI definitions ( Ansible)
        ui_dir = project_dir / 'ui'
        ui_dir.mkdir(exist_ok=True)
        (ui_dir / 'playbooks').mkdir(exist_ok=True)
        # Create empty folders.json
        folders_json = ui_dir / 'folders.json'
        if not folders_json.exists():
            with open(folders_json, 'w', encoding='utf-8') as f:
                json.dump({}, f, indent=2, ensure_ascii=False)
        
        # runtime/ - generated / ephemeral
        runtime_dir = project_dir / 'runtime'
        runtime_dir.mkdir(exist_ok=True)
        (runtime_dir / 'generated_playbooks').mkdir(exist_ok=True)
        (runtime_dir / 'inventory_snapshots').mkdir(exist_ok=True)
        (runtime_dir / 'artifacts').mkdir(exist_ok=True)
        
        # history/ - immutable execution history
        history_dir = project_dir / 'history'
        history_dir.mkdir(exist_ok=True)
        (history_dir / 'executions').mkdir(exist_ok=True)
        (history_dir / 'logs').mkdir(exist_ok=True)
        
        # secrets/ - credentials store ( Git)
        secrets_dir = project_dir / 'secrets'
        secrets_dir.mkdir(exist_ok=True)
        (secrets_dir / 'ssh_keys').mkdir(exist_ok=True)
        (secrets_dir / 'vault').mkdir(exist_ok=True)
        (secrets_dir / 'vault_keys').mkdir(exist_ok=True)
        (secrets_dir / 'git_auth').mkdir(exist_ok=True)
        
        # Initialize project.json with new structure: single repo source
        # repo/ - Git-synced workspace
        initial_config = {
            'sources': {
                'repo': {
                    'mode': 'local',
                    'localPath': 'repo',
                    'syncDirection': 'pull',  # : Pull only (Git → Project Storage)
                    'syncStatus': {
                        'push': {'status': 'idle', 'lastSyncAt': None, 'error': None},
                        'pull': {'status': 'idle', 'lastSyncAt': None, 'error': None}
                    },
                    'syncState': {
                        'lastPullAt': None,
                        'lastPullStatus': 'idle',
                        'lastPullRevision': None,
                        'lastPullError': None,
                        'lastPushAt': None,
                        'lastPushStatus': 'idle',
                        'lastPushRevision': None,
                        'lastPushError': None
                    }
                }
            },
            'syncTimestamps': {},
            'syncStatus': {}
        }
        save_project_config(project['id'], initial_config)
        
        # Initialize .sync_state.json as empty file
        sync_state_file = project_dir / '.sync_state.json'
        if not sync_state_file.exists():
            with open(sync_state_file, 'w', encoding='utf-8') as f:
                json.dump({}, f, indent=2, ensure_ascii=False)
        
        app_logger.info(f"Project created: {project['id']} - {project['name']} (empty, deterministic)")
        return jsonify({'success': True, 'project': project})
    except Exception as e:
        app_logger.error(f"Error creating project: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>', methods=['GET'])
@require_auth
def api_get_project(project_id):
    """API: ID"""
    try:
        projects = load_projects()
        project = next((p for p in projects if p.get('id') == project_id), None)
        
        if not project:
            return jsonify({'success': False, 'error': 'Project not found'}), 404
        
        return jsonify({'success': True, 'project': project})
    except Exception as e:
        app_logger.error(f"Error getting project: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>', methods=['PUT'])
@require_auth
def api_update_project(project_id):
    """API: """
    try:
        data = request.json or {}
        projects = load_projects()
        
        project = next((p for p in projects if p.get('id') == project_id), None)
        if not project:
            return jsonify({'success': False, 'error': 'Project not found'}), 404
        
        if 'name' in data:
            new_name = data['name'].strip()
            if not new_name:
                return jsonify({'success': False, 'error': 'Project name cannot be empty'}), 400
            
            # ( )
            if any(p.get('id') != project_id and p.get('name') == new_name and not p.get('isArchived', False) for p in projects):
                return jsonify({'success': False, 'error': 'Project with this name already exists'}), 400
            
            project['name'] = new_name
        
        if 'description' in data:
            project['description'] = data['description'].strip()
        
        if 'isArchived' in data:
            project['isArchived'] = bool(data['isArchived'])
        
        project['updatedAt'] = time.time()
        
        save_projects(projects)
        app_logger.info(f"Project updated: {project_id}")
        return jsonify({'success': True, 'project': project})
    except Exception as e:
        app_logger.error(f"Error updating project: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500




@bp.route('/api/projects/<project_id>/hosts_status', methods=['GET'])
@require_auth
def api_get_project_hosts_status(project_id):
    """ API ( get_all_data)."""
    try:
        hosts = get_project_hosts_list(project_id)
        host_statuses = {}
        for host_name in hosts:
            st = get_host_check_status(project_id, host_name)
            if st:
                host_statuses[host_name] = {
                    'status': st.get('status', 'unknown'),
                    'last_checked_at': st.get('last_checked_at'),
                    'status_expires_at': st.get('status_expires_at'),
                }
            else:
                host_statuses[host_name] = {'status': 'unknown', 'last_checked_at': None, 'status_expires_at': None}
        return jsonify({'success': True, 'hosts_status': host_statuses})
    except Exception as e:
        app_logger.warning(f"[api_get_project_hosts_status] {project_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/host_settings', methods=['GET'])
@require_auth
def api_get_project_host_settings(project_id):
    """API: (TTL, -)."""
    try:
        config = load_project_config(project_id) or {}
        host_status_cfg = config.get('host_status', {})
        ttl = host_status_cfg.get('ttl_seconds')
        if ttl is None:
            # : 
            ttl = config.get('host_status_ttl_seconds', HOST_STATUS_TTL_DEFAULT)
        auto_check = bool(host_status_cfg.get('auto_check_all_hosts', False))
        return jsonify({
            'success': True,
            'settings': {
                'ttl_seconds': ttl,
                'auto_check_all_hosts': auto_check,
            }
        })
    except Exception as e:
        app_logger.error(f"Error getting host settings for project {project_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/host_settings', methods=['PUT'])
@require_auth
def api_update_project_host_settings(project_id):
    """API: (TTL, -)."""
    try:
        data = request.json or {}
        config = load_project_config(project_id) or {}
        host_status_cfg = config.get('host_status', {})

        if 'ttl_seconds' in data:
            try:
                ttl_seconds = int(data['ttl_seconds'])
            except (TypeError, ValueError):
                ttl_seconds = HOST_STATUS_TTL_DEFAULT
            if ttl_seconds < 30:
                ttl_seconds = 30
            elif ttl_seconds > 24 * 60 * 60:
                ttl_seconds = 24 * 60 * 60
            host_status_cfg['ttl_seconds'] = ttl_seconds

        if 'auto_check_all_hosts' in data:
            host_status_cfg['auto_check_all_hosts'] = bool(data['auto_check_all_hosts'])

        config['host_status'] = host_status_cfg
        save_project_config(project_id, config)

        return jsonify({'success': True, 'settings': host_status_cfg})
    except Exception as e:
        app_logger.error(f"Error updating host settings for project {project_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500
    except Exception as e:
        app_logger.error(f"Unexpected error saving project config for {project_id}: {e}", exc_info=True)
        raise



@bp.route('/api/projects/<project_id>', methods=['DELETE'])
@require_auth
def api_delete_project(project_id):
    """API: (hard delete)"""
    try:
        projects = load_projects()
        
        project = next((p for p in projects if p.get('id') == project_id), None)
        if not project:
            return jsonify({'success': False, 'error': 'Project not found'}), 404
        
        # Default Project ( , )
        # frontend
        
        # Hard delete: 
        projects = [p for p in projects if p.get('id') != project_id]
        save_projects(projects)
        
        # (, )
        project_dir = get_project_dir(project_id)
        if project_dir.exists():
            import shutil
            try:
                shutil.rmtree(project_dir)
                app_logger.info(f"Project directory deleted: {project_dir}")
            except Exception as e:
                app_logger.warning(f"Failed to delete project directory {project_dir}: {e}")
        
        app_logger.info(f"Project deleted permanently: {project_id}")
        return jsonify({'success': True, 'message': 'Project deleted permanently'})
    except Exception as e:
        app_logger.error(f"Error deleting project: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/restore', methods=['POST'])
@require_auth
def api_restore_project(project_id):
    """API: """
    try:
        projects = load_projects()
        
        project = next((p for p in projects if p.get('id') == project_id), None)
        if not project:
            return jsonify({'success': False, 'error': 'Project not found'}), 404
        
        # , 
        if not project.get('isArchived', False):
            return jsonify({'success': False, 'error': 'Project is not archived'}), 400
        
        project['isArchived'] = False
        project['updatedAt'] = time.time()
        
        save_projects(projects)
        app_logger.info(f"Project restored: {project_id}")
        return jsonify({'success': True, 'project': project})
    except Exception as e:
        app_logger.error(f"Error restoring project: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/switch', methods=['POST'])
@require_auth
def api_switch_project(project_id):
    """API: ( )"""
    try:
        projects = load_projects()
        project = next((p for p in projects if p.get('id') == project_id and not p.get('isArchived', False)), None)
        
        if not project:
            return jsonify({'success': False, 'error': 'Project not found or archived'}), 404
        
        return jsonify({'success': True, 'project': project})
    except Exception as e:
        app_logger.error(f"Error switching project: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


