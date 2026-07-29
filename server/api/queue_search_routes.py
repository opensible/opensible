"""Queue / search / capabilities / project queue-stats routes (Phase 3)."""
from __future__ import annotations

import importlib
import json
import sys
from flask import Blueprint, current_app, jsonify, request

from auth.middleware import require_auth
from utils.project_paths import get_project_dir, get_project_inventory_file
from utils.yaml_io import parse_yaml_with_comments

bp = Blueprint("queue_search_api", __name__)


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
    "get_inventory_groups",
    "get_inventory_hosts",
    "get_project_group_vars_dir",
    "get_project_host_vars_dir",
    "list_executions",
    "load_projects",
    "load_roles_config",
    "BASE_DIR",
    "PROJECTS_DIR",
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


# ============================================================================


@bp.route('/api/queue', methods=['GET'])
@require_auth
def api_get_queue():
    """API: runs (QUEUED)"""
    try:
        project_id = request.args.get('project_id')
        playbook_id = request.args.get('playbook_id')
        limit = request.args.get('limit', 100, type=int)
        
        # QUEUED executions
        if project_id:
            executions = list_executions(
                limit=limit,
                playbook_id=playbook_id,
                project_id=project_id
            )
            queued = [e for e in executions if e.get('status') == 'QUEUED']
        else:
            # project_id , ( : history/executions/)
            queued = []
            for proj_dir in PROJECTS_DIR.iterdir():
                if not proj_dir.is_dir():
                    continue
                project_id_from_dir = proj_dir.name
                # executions ( )
                executions_dir = proj_dir / 'history' / 'executions'
                if not executions_dir.exists():
                    executions_dir = proj_dir / 'executions'
                if not executions_dir.exists():
                    continue
                try:
                    execs = list_executions(
                        limit=limit,
                        playbook_id=playbook_id,
                        project_id=project_id_from_dir
                    )
                    queued.extend([e for e in execs if e.get('status') == 'QUEUED'])
                except:
                    pass
        
        # queuedAt ( )
        queued.sort(key=lambda x: x.get('queuedAt', x.get('createdAt', 0)))
        
        return jsonify({
            'success': True,
            'queued': queued[:limit],
            'count': len(queued)
        })
    except Exception as e:
        app_logger.error(f"Error getting queue: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# GLOBAL SEARCH API
# ============================================================================

def _search_in_text(text, query):
    """, (case-insensitive)"""
    if not text:
        return False
    return query.lower() in str(text).lower()


def _search_global(query, entity_types=None, project_ids=None, limit=50):
    """
 
 entity_types: ['project', 'host', 'group', 'role', 'playbook', 'variable', 'execution', 'draft']
 project_ids: ID (None = )
    """
    results = {
        'projects': [],
        'hosts': [],
        'groups': [],
        'roles': [],
        'playbooks': [],
        'variables': [],
        'executions': [],
        'drafts': []
    }
    
    query_lower = query.lower() if query else ''
    if not query_lower:
        return results
    
    search_project_ids = project_ids if project_ids else [p.get('id') for p in load_projects()]
    
    # , 
    if entity_types is None:
        entity_types = ['project', 'host', 'group', 'role', 'playbook', 'variable', 'execution']
    
    # 2. Hosts, Groups, Variables ( inventory vars)
    if any(t in entity_types for t in ['host', 'group', 'variable']):
        for project_id in search_project_ids:
                try:
                    # inventory
                    project_inventory_file = get_project_inventory_file(project_id)
                    inventory_files = [str(project_inventory_file)] if project_inventory_file.exists() else []
                    
                    if inventory_files:
                        hosts = get_inventory_hosts(inventory_files)
                        groups = get_inventory_groups(inventory_files)
                        
                        # Hosts
                        if 'host' in entity_types:
                            for host in hosts:
                                host_name = host.get('name', '')
                                if _search_in_text(host_name, query):
                                    results['hosts'].append({
                                        'id': host_name,
                                        'name': host_name,
                                        'type': 'host',
                                        'project_id': project_id,
                                        'inventory_file': host.get('inventory_file', '')
                                    })
                        
                        # Groups
                        if 'group' in entity_types:
                            for group_name, group_data in groups.items():
                                if _search_in_text(group_name, query):
                                    results['groups'].append({
                                        'id': group_name,
                                        'name': group_name,
                                        'type': 'group',
                                        'project_id': project_id,
                                        'hosts_count': len(group_data.get('hosts', [])) if isinstance(group_data, dict) else 0
                                    })
                        
                        # Variables
                        if 'variable' in entity_types:
                            project_group_vars_dir = get_project_group_vars_dir(project_id)
                            project_host_vars_dir = get_project_host_vars_dir(project_id)
                            
                            # Group vars
                            group_vars_file = project_group_vars_dir / 'all.yml'
                            if group_vars_file.exists():
                                group_vars = parse_yaml_with_comments(group_vars_file)
                                for var_name, var_value in group_vars.items():
                                    if (not var_name.startswith('_') and 
                                        (_search_in_text(var_name, query) or _search_in_text(var_value, query))):
                                        results['variables'].append({
                                            'id': f"{project_id}:group:{var_name}",
                                            'name': var_name,
                                            'value': str(var_value),
                                            'type': 'variable',
                                            'scope': 'group',
                                            'project_id': project_id
                                        })
                            
                            # Host vars
                            if project_host_vars_dir.exists():
                                for host_file in project_host_vars_dir.glob('*.yml'):
                                    host_name = host_file.stem
                                    host_vars = parse_yaml_with_comments(host_file)
                                    for var_name, var_value in host_vars.items():
                                        if (not var_name.startswith('_') and 
                                            (_search_in_text(var_name, query) or 
                                             _search_in_text(var_value, query) or
                                             _search_in_text(host_name, query))):
                                            results['variables'].append({
                                                'id': f"{project_id}:host:{host_name}:{var_name}",
                                                'name': var_name,
                                                'value': str(var_value),
                                                'type': 'variable',
                                                'scope': 'host',
                                                'host': host_name,
                                                'project_id': project_id
                                            })
                except Exception as e:
                    app_logger.warning(f"Error searching in project {project_id}: {e}")
        
        # 3. Roles & Playbooks ( roles-storage)
        if 'role' in entity_types or 'playbook' in entity_types:
            try:
                roles_config = load_roles_config()
                roles_storage_root = BASE_DIR / roles_config.get('storageRoot', 'roles-storage')
                
                if roles_storage_root.exists():
                    for pack_dir in roles_storage_root.iterdir():
                        if not pack_dir.is_dir() or pack_dir.name.startswith('.'):
                            continue
                        
                        roles_dir = pack_dir / 'roles'
                        if roles_dir.exists():
                            for role_dir in roles_dir.iterdir():
                                if role_dir.is_dir() and _search_in_text(role_dir.name, query):
                                    # Get project_dir for this project_id
                                    project_dir = get_project_dir(project_id)
                                    role_path_relative = str(role_dir.relative_to(project_dir)) if role_dir.is_relative_to(project_dir) else f'roles-playbooks/{pack_dir.name}/{role_dir.name}'
                                    results['roles'].append({
                                        'id': f"{pack_dir.name}:{role_dir.name}",
                                        'name': role_dir.name,
                                        'pack': pack_dir.name,
                                        'type': 'role',
                                        'path': role_path_relative
                                    })
                        
                        # playbooks
                        playbooks_dir = pack_dir / 'playbooks'
                        if playbooks_dir.exists():
                            for playbook_file in playbooks_dir.glob('*.yml'):
                                if _search_in_text(playbook_file.stem, query):
                                    # Get project_dir for this project_id
                                    project_dir = get_project_dir(project_id)
                                    playbook_path_relative = str(playbook_file.relative_to(project_dir)) if playbook_file.is_relative_to(project_dir) else f'roles-playbooks/{playbook_file.name}'
                                    results['playbooks'].append({
                                        'id': f"{pack_dir.name}:{playbook_file.stem}",
                                        'name': playbook_file.stem,
                                        'pack': pack_dir.name,
                                        'type': 'playbook',
                                        'path': playbook_path_relative
                                    })
            except Exception as e:
                app_logger.warning(f"Error searching roles and playbooks: {e}")
        
        # 4. Executions
        if 'execution' in entity_types:
            for project_id in search_project_ids:
                try:
                    executions = list_executions(limit=100, search_query=query, project_id=project_id)
                    for execution in executions[:limit]:
                        results['executions'].append({
                            'id': execution.get('id'),
                            'name': f"Execution {execution.get('id', '')[:8]}",
                            'type': 'execution',
                            'project_id': project_id,
                            'status': execution.get('status', 'unknown'),
                            'createdAt': execution.get('createdAt'),
                            'hostsTargeted': execution.get('stats', {}).get('hostsTargeted', 0)
                        })
                except Exception as e:
                    app_logger.warning(f"Error searching executions in project {project_id}: {e}")
        
        for key in results:
            results[key] = results[key][:limit]
    
    
    return results


@bp.route('/api/search', methods=['POST'])
@require_auth
def api_global_search():
    """API: """
    try:
        data = request.get_json(silent=True) or {}
        query = data.get('query', '').strip()
        entity_types = data.get('entity_types')
        project_ids = data.get('project_ids')  # ID 
        limit = data.get('limit', 50)
        
        if not query:
            return jsonify({
                'success': True,
                'results': {
                    'projects': [],
                    'hosts': [],
                    'groups': [],
                    'roles': [],
                    'playbooks': [],
                    'variables': [],
                    'executions': [],
                    'drafts': []
                },
                'total': 0
            })
        
        results = _search_global(query, entity_types, project_ids, limit)
        
        total = sum(len(v) for v in results.values())
        
        return jsonify({
            'success': True,
            'results': results,
            'total': total,
            'query': query
        })
    
    except Exception as e:
        app_logger.error(f"Error in global search: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# ADMIN WORKER MANAGEMENT ENDPOINTS
# ============================================================================

def check_admin_auth():
    """
 workers
 TODO: 
    """
    # Placeholder - production / 
    # dev mode True
    return True


def _is_worker_online(worker_data: dict, heartbeat_ttl_seconds: int = 60) -> bool:
    """, worker ( lastSeenAt)"""
    last_seen = worker_data.get('lastSeenAt')
    if not last_seen:
        return False
    import time
    return (time.time() - last_seen) < heartbeat_ttl_seconds


# Phase 3 refactor: admin worker list moved to backend/api/admin_routes.py.



@bp.route('/api/capabilities', methods=['GET'])
@require_auth
def api_capabilities():
    """API: Capabilities available for playbook runs (e.g. Mitogen for strategy plugins)."""
    try:
        try:
            from services.worker_registry import load_all_workers
        except ImportError:
            from services.worker_registry import load_all_workers
        workers = load_all_workers()
        mitogen_available = False
        for worker_data in workers.values():
            if not worker_data.get('enabled', True):
                continue
            sinfo = worker_data.get('systemInfo') or {}
            mitogen = sinfo.get('mitogen') or {}
            version = mitogen.get('version') if isinstance(mitogen, dict) else None
            if version and str(version).lower() not in ('not installed', 'none', ''):
                mitogen_available = True
                break
        return jsonify({
            'success': True,
            'mitogen_available': mitogen_available
        })
    except Exception as e:
        app_logger.error(f"Error getting capabilities: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


# Phase 3 refactor: admin worker create/get/patch/delete/rotate-token moved
# to backend/api/admin_routes.py.




# Phase 4 refactor: api-tokens + openapi/docs + docs-access routes moved
# to backend/api/api_tokens_docs_routes.py.



# Phase 3 refactor: admin worker enable/disable/request-info/runs moved
# to backend/api/admin_routes.py.



@bp.route('/api/projects/<project_id>/queue-stats', methods=['GET'])
@require_auth
def api_project_queue_stats(project_id):
    """API: """
    try:
        try:
            from services.worker_registry import load_all_workers, is_worker_online
        except ImportError:
            from services.worker_registry import load_all_workers, is_worker_online
        
        project_dir = PROJECTS_DIR / project_id
        if not project_dir.exists() or not project_dir.is_dir():
            return jsonify({'success': False, 'error': 'Project not found'}), 404
        
        executions_dir = project_dir / 'executions'
        if not executions_dir.exists():
            return jsonify({
                'success': True,
                'projectId': project_id,
                'queuedCount': 0,
                'runningCount': 0,
                'workersOnline': 0
            })
        
        queued_count = 0
        running_count = 0
        running_worker_ids = set()
        
        # executions
        for exec_file in executions_dir.glob('*.json'):
            try:
                with open(exec_file, 'r', encoding='utf-8') as f:
                    execution = json.load(f)
                    status = execution.get('status')
                    if status == 'QUEUED':
                        queued_count += 1
                    elif status == 'RUNNING':
                        running_count += 1
                        worker_id = execution.get('workerId')
                        if worker_id:
                            running_worker_ids.add(worker_id)
            except:
                pass
        
        # workers (, )
        workers = load_all_workers()
        workers_online = sum(1 for w_id in workers.keys() if is_worker_online(w_id, ttl_seconds=60))
        
        return jsonify({
            'success': True,
            'projectId': project_id,
            'queuedCount': queued_count,
            'runningCount': running_count,
            'workersOnline': workers_online,
            'activeWorkersForProject': len(running_worker_ids)
        })
    except Exception as e:
        app_logger.error(f"Error getting queue stats: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# WORKER API ENDPOINTS
# ============================================================================

