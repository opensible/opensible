"""Project sources / autosync API routes blueprint.

Moved from ``backend/app.py``. URLs, methods, view-function names,
response shapes, and auth decorators are preserved verbatim. App.py
globals are resolved lazily via ``_LazyProxy`` so this module can be
imported before app.py finishes initializing.
"""
from __future__ import annotations

import os
import re
import sys
import json
import uuid
import shutil
from pathlib import Path
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

try:
    from auth.middleware import require_auth
except ImportError:  # pragma: no cover
    from ..auth.middleware import require_auth

try:
    from utils.request_ctx import require_project_id_from_request
except ImportError:  # pragma: no cover
    from ..utils.request_ctx import require_project_id_from_request


bp = Blueprint("sources_api", __name__)


def _app_module():
    for name in ("__main__", "app"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "source_sync_service"):
            return mod
    import app as _a  # noqa
    return _a


class _LazyProxy:
    __slots__ = ("_lp_name",)

    def __init__(self, name: str) -> None:
        object.__setattr__(self, "_lp_name", name)

    def _lp_real(self):
        return getattr(_app_module(), object.__getattribute__(self, "_lp_name"))

    def __call__(self, *args, **kwargs):
        return self._lp_real()(*args, **kwargs)

    def __getattr__(self, item):
        return getattr(self._lp_real(), item)

    def __setattr__(self, item, value):
        setattr(self._lp_real(), item, value)

    def __getitem__(self, item):
        return self._lp_real()[item]

    def __iter__(self):
        return iter(self._lp_real())

    def __bool__(self):
        try:
            return bool(self._lp_real())
        except Exception:
            return False

    def __repr__(self):
        return f"<_LazyProxy {object.__getattribute__(self, '_lp_name')}>"


_APP_NAMES = (
    "app",
    "load_projects",
    "load_project_config",
    "save_project_config",
    "resolve_project_source",
    "get_project_dir",
    "get_project_storage_path",
    "normalize_sources",
    "validate_sources",
    "validate_repo_layout",
    "ensure_all_inventory_dirs",
    "source_sync_service",
    "git_source_manager",
    "GitSourceError",
    "GitSourceManager",
    "SourceSyncService",
)

for _n in _APP_NAMES:
    globals().setdefault(_n, _LazyProxy(_n))
del _n


# ---------------------------------------------------------------------------
# Routes (verbatim from app.py, @app.route -> @bp.route)
# ---------------------------------------------------------------------------
@bp.route('/api/projects/<project_id>/sources/status', methods=['GET'])
@require_auth
def api_get_project_sources_status(project_id):
    """API: Get health status of all project sources"""
    try:
        # Check project exists
        projects = load_projects()
        project = next((p for p in projects if p.get('id') == project_id), None)
        if not project:
            return jsonify({'success': False, 'error': 'Project not found', 'errorCode': 'PROJECT_NOT_FOUND'}), 404
        
        # Load sources configuration
        config = load_project_config(project_id)
        sources = config.get('sources', {})
        normalized_sources = normalize_sources(sources, project_id)
        
        # Define source requirements
        required_sources = ['inventory', 'group_vars_storage', 'host_vars_storage', 'roles_playbooks', 'secrets_storage']
        optional_sources = ['ansible_config']
        
        status = {
            'overall': 'ok',  # ok, warning, error
            'sources': {}
        }
        
        has_errors = False
        has_warnings = False
        
        # Check each source
        for source_key in ['inventory', 'roles_playbooks', 'ansible_config', 'group_vars_storage', 'host_vars_storage', 'secrets_storage']:
            source_config = normalized_sources.get(source_key, {})
            mode = source_config.get('mode', 'local')
            
            source_status = {
                'configured': source_key in sources,  # Has custom config (not just defaults)
                'mode': mode,
                'status': 'ok',  # ok, warning, error
                'message': '',
                'resolved': False,
                'path': None
            }
            
            try:
                # Try to resolve the source
                resolved = resolve_project_source(project_id, source_key)
                source_status['resolved'] = True
                source_status['path'] = str(resolved['rootPath'])
                
                # Check if path exists and is accessible
                root_path = resolved['rootPath']
                if not root_path.exists():
                    if source_key in required_sources:
                        source_status['status'] = 'error'
                        source_status['message'] = f'Path does not exist: {root_path}'
                        has_errors = True
                    else:
                        source_status['status'] = 'warning'
                        source_status['message'] = f'Path does not exist: {root_path}'
                        has_warnings = True
                elif not os.access(root_path, os.R_OK):
                    source_status['status'] = 'error'
                    source_status['message'] = f'Path is not readable: {root_path}'
                    has_errors = True
                else:
                    # Additional checks based on source type
                    if source_key == 'inventory':
                        # Check for inventory files
                        inv_files = []
                        if root_path.is_file() and root_path.suffix in ['.yml', '.yaml']:
                            inv_files = [root_path]
                        elif root_path.is_dir():
                            inv_files = list(root_path.glob('*.yml')) + list(root_path.glob('*.yaml'))
                        
                        if not inv_files:
                            source_status['status'] = 'warning'
                            source_status['message'] = 'No inventory files found'
                            has_warnings = True
                    
                    elif source_key == 'roles_playbooks':
                        # Check for expected structure
                        if root_path.is_dir():
                            has_roles = (root_path / 'roles').exists() or any(
                                (item / 'tasks' / 'main.yml').exists() or (item / 'tasks' / 'main.yaml').exists()
                                for item in root_path.iterdir() if item.is_dir()
                            )
                            has_playbooks = (root_path / 'playbooks').exists()
                            
                            if not has_roles and not has_playbooks:
                                source_status['status'] = 'warning'
                                source_status['message'] = 'Expected structure (roles/playbooks) not found'
                                has_warnings = True
                    
                    elif source_key == 'ansible_config':
                        # ansible_config - , 
                        if root_path.is_file():
                            pass
                        elif root_path.parent.is_dir():
                            # , 
                            source_status['status'] = 'warning'
                            source_status['message'] = 'ansible.cfg file not found'
                            has_warnings = True
                        else:
                            source_status['status'] = 'warning'
                            source_status['message'] = 'ansible-config directory not found'
                            has_warnings = True
                    
                    elif source_key in ['group_vars_storage', 'host_vars_storage', 'secrets_storage']:
                        if root_path.is_dir():
                            files = list(root_path.glob('*.yml')) + list(root_path.glob('*.yaml')) + list(root_path.glob('*.json'))
                            if not files:
                                source_status['status'] = 'warning'
                                source_status['message'] = 'Directory is empty'
                                has_warnings = True
                
            except Exception as e:
                app.logger.warning(f"Error resolving source {source_key}: {e}")
                source_status['resolved'] = False
                source_status['status'] = 'error' if source_key in required_sources else 'warning'
                source_status['message'] = f'Failed to resolve: {str(e)}'
                if source_key in required_sources:
                    has_errors = True
                else:
                    has_warnings = True
            
            status['sources'][source_key] = source_status
        
        # Set overall status
        if has_errors:
            status['overall'] = 'error'
        elif has_warnings:
            status['overall'] = 'warning'
        else:
            status['overall'] = 'ok'
        
        return jsonify({
            'success': True,
            'status': status
        })
        
    except Exception as e:
        app.logger.error(f"Error getting sources status: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e),
            'errorCode': 'INTERNAL_ERROR'
        }), 500


@bp.route('/api/projects/<project_id>/sources/revert', methods=['POST'])
@require_auth
def api_revert_source_to_defaults(project_id):
    """API: Revert a source to legacy local defaults"""
    try:
        # Check project exists
        projects = load_projects()
        project = next((p for p in projects if p.get('id') == project_id), None)
        if not project:
            return jsonify({'success': False, 'error': 'Project not found', 'errorCode': 'PROJECT_NOT_FOUND'}), 404
        
        data = request.json or {}
        source_key = data.get('sourceKey')
        
        if not source_key:
            return jsonify({'success': False, 'error': 'sourceKey is required', 'errorCode': 'MISSING_SOURCE_KEY'}), 400
        
        # Load current config
        config = load_project_config(project_id)
        sources = config.get('sources', {})
        
        # Remove the source key (revert to defaults)
        if source_key in sources:
            del sources[source_key]
            config['sources'] = sources
            save_project_config(project_id, config)
        
        # Return normalized sources (will use defaults)
        normalized_sources = normalize_sources(sources, project_id)
        
        app.logger.info(f"Source reverted to defaults: {project_id}/{source_key}")
        return jsonify({
            'success': True,
            'sources': normalized_sources
        })
    except Exception as e:
        app.logger.error(f"Error reverting source: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/sources', methods=['GET'])
@require_auth
def api_get_project_sources(project_id):
    """API: sources """
    try:
        projects = load_projects()
        project = next((p for p in projects if p.get('id') == project_id), None)
        if not project:
            return jsonify({'success': False, 'error': 'Project not found'}), 404
        
        config = load_project_config(project_id)
        sources = config.get('sources', {})
        sync_timestamps = config.get('syncTimestamps', {})
        repo_layout = config.get('repoLayout')  # Optional repoLayout
        
        # sources defaults
        normalized_sources = normalize_sources(sources, project_id)
        
        # Add sync timestamps and status to response
        sync_status = config.get('syncStatus', {})
        sources_with_sync = {}
        for key, source in normalized_sources.items():
            source_copy = source.copy()
            # Legacy sync timestamp (backward compatibility)
            if key in sync_timestamps:
                source_copy['lastSyncedAt'] = sync_timestamps[key]
            # New sync status (bidirectional)
            if key in sync_status:
                source_copy['syncStatus'] = sync_status[key]
            else:
                source_copy['syncStatus'] = {
                    'push': {'status': 'idle', 'lastSyncAt': None, 'error': None},
                    'pull': {'status': 'idle', 'lastSyncAt': None, 'error': None}
                }
            # Sync direction
            if 'syncDirection' in source:
                source_copy['syncDirection'] = source['syncDirection']
            else:
                source_copy['syncDirection'] = 'none'
            
            # Get sync state from SourceSyncService
            try:
                sync_state = source_sync_service.get_sync_state(project_id, key)
                source_copy['syncState'] = sync_state
            except Exception as e:
                app.logger.warning(f"Error getting sync state for {key}: {e}")
                source_copy['syncState'] = {
                    'lastPushAt': None,
                    'lastPullAt': None,
                    'lastPushStatus': 'idle',
                    'lastPullStatus': 'idle',
                    'lastPushError': None,
                    'lastPullError': None,
                    'lastPushRevision': None,
                    'lastPullRevision': None
                }
            
            sources_with_sync[key] = source_copy
        
        return jsonify({
            'success': True,
            'sources': sources_with_sync,
            'repoLayout': repo_layout,  # Return repoLayout if exists
            'hasCustomSources': bool(sources),  # , sources defaults
            'syncTimestamps': sync_timestamps,  # Legacy
            'syncStatus': sync_status  # New bidirectional status
        })
    except Exception as e:
        app.logger.error(f"Error getting project sources: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/sources/analyze', methods=['POST'])
@require_auth
def api_analyze_source_impact(project_id):
    """API: Analyze impact of source configuration change"""
    try:
        # Check project exists
        projects = load_projects()
        project = next((p for p in projects if p.get('id') == project_id), None)
        if not project:
            return jsonify({'success': False, 'error': 'Project not found', 'errorCode': 'PROJECT_NOT_FOUND'}), 404
        
        data = request.json or {}
        source_key = data.get('sourceKey')
        config = data.get('config', {})
        
        if not source_key:
            return jsonify({'success': False, 'error': 'sourceKey is required', 'errorCode': 'MISSING_SOURCE_KEY'}), 400
        
        mode = config.get('mode', 'local')
        issues = []  # List of warnings/errors
        has_errors = False
        
        # Resolve path based on mode
        if mode == 'local':
            local_path = config.get('localPath', '').strip()
            if not local_path:
                issues.append({
                    'type': 'error',
                    'message': 'Local path is required',
                    'code': 'MISSING_LOCAL_PATH'
                })
                has_errors = True
            else:
                # Hard check for path traversal
                if '..' in local_path:
                    issues.append({
                        'type': 'error',
                        'message': 'Path contains traversal (..) - not allowed',
                        'code': 'PATH_TRAVERSAL'
                    })
                    has_errors = True
                else:
                    # Resolve path
                    path_obj = Path(local_path)
                    if not path_obj.is_absolute():
                        # Relative path: resolve relative to project directory
                        project_dir = get_project_dir(project_id)
                        path_obj = (project_dir / local_path).resolve()
                        # Ensure it's within project directory
                        try:
                            path_obj.relative_to(project_dir.resolve())
                        except ValueError:
                            issues.append({
                                'type': 'error',
                                'message': 'Path resolves outside project directory',
                                'code': 'PATH_TRAVERSAL'
                            })
                            has_errors = True
                            path_obj = None
                    else:
                        # Absolute path: validate it's a valid external path (for sync only)
                        # Note: absolute paths are used for external sources during sync
                        # Runtime always uses Project Storage, not this path
                        if not path_obj.exists():
                            issues.append({
                                'type': 'warning',
                                'message': f'Absolute path does not exist: {path_obj}. It will be created during sync if needed.',
                                'code': 'PATH_NOT_FOUND'
                            })
                    
                    if path_obj:
                        # Check if path exists
                        if not path_obj.exists():
                            if source_key in ['group_vars_storage', 'host_vars_storage', 'secrets_storage']:
                                issues.append({
                                    'type': 'error',
                                    'message': f'Path does not exist: {path_obj}',
                                    'code': 'PATH_NOT_FOUND'
                                })
                                has_errors = True
                            else:
                                issues.append({
                                    'type': 'warning',
                                    'message': f'Path does not exist: {path_obj}',
                                    'code': 'PATH_NOT_FOUND'
                                })
                        else:
                            # Run source-specific checks
                            if source_key == 'roles_playbooks':
                                # Check for expected structure (roles/playbooks)
                                has_roles = False
                                has_playbooks = False
                                
                                if path_obj.is_dir():
                                    # Check for roles directory or role-like structure
                                    roles_dir = path_obj / 'roles'
                                    if roles_dir.exists() and roles_dir.is_dir():
                                        has_roles = True
                                    else:
                                        # Check if direct children are role directories (tasks/main.yml)
                                        for item in path_obj.iterdir():
                                            if item.is_dir():
                                                tasks_main = (item / 'tasks' / 'main.yml').exists() or (item / 'tasks' / 'main.yaml').exists()
                                                if tasks_main:
                                                    has_roles = True
                                                    break
                                    
                                    # Check for playbooks directory
                                    playbooks_dir = path_obj / 'playbooks'
                                    if playbooks_dir.exists() and playbooks_dir.is_dir():
                                        has_playbooks = True
                                    
                                    if not has_roles and not has_playbooks:
                                        issues.append({
                                            'type': 'warning',
                                            'message': 'Directory does not contain expected structure (roles/playbooks). This may affect executions.',
                                            'code': 'UNEXPECTED_STRUCTURE'
                                        })
                            
                            elif source_key == 'inventory':
                                # Ensure at least one inventory file detected
                                inv_files = []
                                if path_obj.is_file() and path_obj.suffix in ['.yml', '.yaml']:
                                    inv_files = [path_obj]
                                elif path_obj.is_dir():
                                    inv_files = list(path_obj.glob('*.yml')) + list(path_obj.glob('*.yaml'))
                                
                                if not inv_files:
                                    issues.append({
                                        'type': 'warning',
                                        'message': 'No inventory files (.yml/.yaml) detected. This may affect executions.',
                                        'code': 'NO_INVENTORY_FILES'
                                    })
                            
                            elif source_key == 'ansible_config':
                                # If ansible.cfg missing, warn
                                if path_obj.is_dir():
                                    cfg_file = path_obj / 'ansible.cfg'
                                    if not cfg_file.exists():
                                        issues.append({
                                            'type': 'warning',
                                            'message': 'ansible.cfg file not found in directory. Default Ansible config will be used.',
                                            'code': 'ANSIBLE_CFG_MISSING'
                                        })
                                elif path_obj.is_file() and path_obj.name != 'ansible.cfg':
                                    issues.append({
                                        'type': 'warning',
                                        'message': 'File is not named ansible.cfg. This may not be recognized as Ansible config.',
                                        'code': 'ANSIBLE_CFG_NAME_MISMATCH'
                                    })
                            
                            elif source_key in ['group_vars_storage', 'host_vars_storage', 'secrets_storage']:
                                if path_obj.is_dir():
                                    # Check if empty
                                    files = list(path_obj.glob('*.yml')) + list(path_obj.glob('*.yaml')) + list(path_obj.glob('*.json'))
                                    if not files:
                                        issues.append({
                                            'type': 'warning',
                                            'message': f'Directory is empty. No {source_key.replace("_", " ")} files found.',
                                            'code': 'EMPTY_STORAGE'
                                        })
                                elif path_obj.is_file():
                                    issues.append({
                                        'type': 'warning',
                                        'message': f'Path is a file, but {source_key.replace("_", " ")} expects a directory.',
                                        'code': 'EXPECTED_DIRECTORY'
                                    })
                            
                            # Check readability
                            if path_obj.exists() and not os.access(path_obj, os.R_OK):
                                issues.append({
                                    'type': 'error',
                                    'message': f'Path is not readable: {path_obj}',
                                    'code': 'PATH_NOT_READABLE'
                                })
                                has_errors = True
        
        elif mode == 'git':
            git_config = config.get('git', {})
            repo_url = git_config.get('repo', '').strip()
            
            if not repo_url:
                issues.append({
                    'type': 'error',
                    'message': 'Repository URL is required',
                    'code': 'MISSING_REPO_URL'
                })
                has_errors = True
            else:
                # Hard check for unsafe URLs
                if '..' in repo_url or repo_url.startswith('file://'):
                    issues.append({
                        'type': 'error',
                        'message': 'Unsafe repository URL detected',
                        'code': 'UNSAFE_REPO_URL'
                    })
                    has_errors = True
                
                # Check subdir for path traversal
                subdir = git_config.get('subdir', '').strip()
                if subdir and '..' in subdir:
                    issues.append({
                        'type': 'error',
                        'message': 'Subdirectory contains path traversal (..) - not allowed',
                        'code': 'SUBDIR_TRAVERSAL'
                    })
                    has_errors = True
        
        return jsonify({
            'success': True,
            'hasErrors': has_errors,
            'hasWarnings': len([i for i in issues if i['type'] == 'warning']) > 0,
            'issues': issues,
            'breakingChange': has_errors or len([i for i in issues if i['type'] == 'warning']) > 0
        })
        
    except Exception as e:
        app.logger.error(f"Error analyzing source impact: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e),
            'errorCode': 'INTERNAL_ERROR'
        }), 500


@bp.route('/api/projects/<project_id>/sources', methods=['PUT'])
@require_auth
def api_update_project_sources(project_id):
    """API: sources """
    try:
        projects = load_projects()
        project = next((p for p in projects if p.get('id') == project_id), None)
        if not project:
            return jsonify({'success': False, 'error': 'Project not found'}), 404
        
        data = request.json or {}
        sources = data.get('sources', {})
        repo_layout = data.get('repoLayout')  # Optional repoLayout
        skip_validation = data.get('skipValidation', False)  # Allow skipping if user confirmed
        
        # Normalize sources before validation (adds defaults for missing fields)
        # This ensures all sources have required fields like localPath when mode is 'local'
        sources = normalize_sources(sources, project_id)
        
        # sources
        is_valid, error_code, error_message = validate_sources(sources)
        if not is_valid:
            return jsonify({
                'success': False,
                'error': error_message,
                'errorCode': error_code
            }), 400
        
        # repoLayout ( )
        if repo_layout is not None:
            is_valid, error_code, error_message = validate_repo_layout(repo_layout)
            if not is_valid:
                return jsonify({
                    'success': False,
                    'error': error_message,
                    'errorCode': error_code
                }), 400
        
        # Additional hard security checks (always run, even if skip_validation)
        for source_key, source_config in sources.items():
            mode = source_config.get('mode', 'local')
            if mode == 'local':
                local_path = source_config.get('localPath', '')
                if local_path and '..' in local_path:
                    return jsonify({
                        'success': False,
                        'error': f'Path traversal detected in {source_key}',
                        'errorCode': 'PATH_TRAVERSAL'
                    }), 400
            elif mode == 'git':
                git_config = source_config.get('git', {})
                repo_url = git_config.get('repo', '')
                if repo_url and ('..' in repo_url or repo_url.startswith('file://')):
                    return jsonify({
                        'success': False,
                        'error': f'Unsafe repository URL in {source_key}',
                        'errorCode': 'UNSAFE_REPO_URL'
                    }), 400
                subdir = git_config.get('subdir', '')
                if subdir and '..' in subdir:
                    return jsonify({
                        'success': False,
                        'error': f'Path traversal in subdir for {source_key}',
                        'errorCode': 'SUBDIR_TRAVERSAL'
                    }), 400
        
        config = load_project_config(project_id)
        
        # sources
        config['sources'] = sources
        
        # repoLayout ( )
        if repo_layout is not None:
            # , 
            default_layout = {
                'playbooks': 'playbooks',
                'roles': 'roles',
                'inventories': 'inventories'
            }
            
            # , repoLayout ( )
            is_default = all(
                repo_layout.get(key, default_value) == default_value
                for key, default_value in default_layout.items()
            )
            
            if is_default:
                # repoLayout , 
                config.pop('repoLayout', None)
            else:
                # layout
                config['repoLayout'] = repo_layout
        # repo_layout is None, repoLayout
        
        save_project_config(project_id, config)
        
        # Pull ( Project Storage )
        # , / 
        for source_key, source_config in sources.items():
            mode = source_config.get('mode', 'local')
            if mode == 'local':
                local_path = source_config.get('localPath', '')
                if local_path:
                    external_path = Path(local_path)
                    # ( )
                    if external_path.is_absolute() and external_path.exists():
                        # Project Storage
                        storage_path = get_project_storage_path(project_id, source_key)
                        # Project Storage , Pull
                        if not storage_path.exists() or (storage_path.is_dir() and not any(storage_path.iterdir())):
                            try:
                                app.logger.info(f"Auto-syncing external source: {source_key} from {external_path} to {storage_path}")
                                success, error_code, error_msg = source_sync_service.execute_sync(
                                    project_id=project_id,
                                    source_key=source_key,
                                    source_config=source_config,
                                    direction='pull'
                                )
                                if success:
                                    app.logger.info(f"Auto-sync successful for {source_key}")
                                else:
                                    app.logger.warning(f"Auto-sync failed for {source_key}: {error_msg}")
                            except Exception as e:
                                app.logger.warning(f"Auto-sync error for {source_key}: {e}")
                                # , 
        
        normalized_sources = normalize_sources(sources, project_id)
        
        app.logger.info(f"Project sources updated: {project_id}")
        return jsonify({
            'success': True,
            'sources': normalized_sources
        })
    except Exception as e:
        app.logger.error(f"Error updating project sources: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/sources/test', methods=['POST'])
@require_auth
def api_test_project_source(project_id):
    """API: Test a source configuration (Local or Git)"""
    try:
        # Check project exists
        projects = load_projects()
        project = next((p for p in projects if p.get('id') == project_id), None)
        if not project:
            return jsonify({'success': False, 'error': 'Project not found', 'errorCode': 'PROJECT_NOT_FOUND'}), 404
        
        data = request.json or {}
        source_key = data.get('sourceKey')
        config = data.get('config', {})
        
        if not source_key:
            return jsonify({'success': False, 'error': 'sourceKey is required', 'errorCode': 'MISSING_SOURCE_KEY'}), 400
        
        mode = config.get('mode', 'local')
        details = {}
        
        if mode == 'local':
            # Test Local source
            local_path = config.get('localPath', '').strip()
            if not local_path:
                return jsonify({
                    'success': False,
                    'error': 'Local path is required',
                    'errorCode': 'MISSING_LOCAL_PATH',
                    'details': {}
                }), 400
            
            # Resolve path
            try:
                path_obj = Path(local_path)
                if not path_obj.is_absolute():
                    # Relative path: resolve relative to project directory
                    project_dir = get_project_dir(project_id)
                    path_obj = (project_dir / local_path).resolve()
                    # Ensure it's within project directory
                    try:
                        path_obj.relative_to(project_dir.resolve())
                    except ValueError:
                        return jsonify({
                            'success': False,
                            'error': 'Path resolves outside project directory',
                            'errorCode': 'PATH_TRAVERSAL',
                            'details': {}
                        }), 400
                else:
                    # Absolute path: validate it's a valid external path (for sync only)
                    # Note: absolute paths are used for external sources during sync
                    # Runtime always uses Project Storage, not this path
                    pass  # Absolute paths are allowed for external sources
                
                # Check if path exists
                if not path_obj.exists():
                    return jsonify({
                        'success': False,
                        'error': f'Path does not exist: {path_obj}',
                        'errorCode': 'PATH_NOT_FOUND',
                        'details': {'path': str(path_obj)}
                    }), 400
                
                # Check if readable
                if not os.access(path_obj, os.R_OK):
                    return jsonify({
                        'success': False,
                        'error': f'Path is not readable: {path_obj}',
                        'errorCode': 'PATH_NOT_READABLE',
                        'details': {'path': str(path_obj)}
                    }), 400
                
                # Light content check based on source type
                content_hint = ''
                if source_key == 'inventory':
                    # Check for inventory files
                    if path_obj.is_file() and (path_obj.suffix in ['.yml', '.yaml']):
                        content_hint = 'Contains inventory file'
                    elif path_obj.is_dir():
                        inv_files = list(path_obj.glob('*.yml')) + list(path_obj.glob('*.yaml'))
                        if inv_files:
                            content_hint = f'Contains {len(inv_files)} inventory file(s)'
                        else:
                            content_hint = 'Directory exists but no inventory files found'
                elif source_key == 'roles_playbooks':
                    if path_obj.is_dir():
                        # Check for roles structure
                        role_dirs = [d for d in path_obj.iterdir() if d.is_dir()]
                        if role_dirs:
                            content_hint = f'Contains {len(role_dirs)} potential role directory(ies)'
                        else:
                            content_hint = 'Directory exists but no role directories found'
                elif source_key in ['group_vars_storage', 'host_vars_storage', 'secrets_storage']:
                    if path_obj.is_dir():
                        files = list(path_obj.glob('*.yml')) + list(path_obj.glob('*.yaml')) + list(path_obj.glob('*.json'))
                        if files:
                            content_hint = f'Contains {len(files)} variable/secret file(s)'
                        else:
                            content_hint = 'Directory exists but no variable/secret files found'
                
                details = {
                    'path': str(path_obj),
                    'exists': True,
                    'isFile': path_obj.is_file(),
                    'isDir': path_obj.is_dir(),
                    'readable': True,
                    'contentHint': content_hint
                }
                
                return jsonify({
                    'success': True,
                    'ok': True,
                    'details': details
                })
                
            except Exception as e:
                app.logger.error(f"Error testing local source: {e}", exc_info=True)
                return jsonify({
                    'success': False,
                    'error': f'Error testing path: {str(e)}',
                    'errorCode': 'TEST_ERROR',
                    'details': {}
                }), 500
        
        elif mode == 'git':
            # Test Git source
            git_config = config.get('git', {})
            repo_url = git_config.get('repo', '').strip()
            ref = git_config.get('ref', 'main').strip()
            subdir = git_config.get('subdir', '').strip()
            auth_secret_id = git_config.get('authSecretId')
            
            if not repo_url:
                return jsonify({
                    'success': False,
                    'error': 'Repository URL is required',
                    'errorCode': 'MISSING_REPO_URL',
                    'details': {}
                }), 400
            
            # Test using GitSourceManager
            try:
                # Force refresh for test
                resolved_path = git_source_manager.resolve_path(
                    project_id=project_id,
                    source_key=source_key,
                    repo_url=repo_url,
                    ref=ref,
                    subdir=subdir,
                    auth_secret_id=auth_secret_id,
                    force_refresh=True
                )
                
                # Check if resolved path exists
                if not resolved_path.exists():
                    return jsonify({
                        'success': False,
                        'error': f'Resolved path does not exist: {resolved_path}',
                        'errorCode': 'SUBDIR_NOT_FOUND',
                        'details': {'resolvedPath': str(resolved_path)}
                    }), 400
                
                details = {
                    'repo': repo_url,
                    'ref': ref,
                    'subdir': subdir,
                    'resolvedPath': str(resolved_path),
                    'exists': True,
                    'isFile': resolved_path.is_file(),
                    'isDir': resolved_path.is_dir()
                }
                
                return jsonify({
                    'success': True,
                    'ok': True,
                    'details': details
                })
                
            except GitSourceError as e:
                return jsonify({
                    'success': False,
                    'error': e.message,
                    'errorCode': e.error_code,
                    'details': {}
                }), 400
            except Exception as e:
                app.logger.error(f"Error testing git source: {e}", exc_info=True)
                return jsonify({
                    'success': False,
                    'error': f'Error testing git source: {str(e)}',
                    'errorCode': 'TEST_ERROR',
                    'details': {}
                }), 500
        
        else:
            return jsonify({
                'success': False,
                'error': f'Invalid mode: {mode}',
                'errorCode': 'INVALID_MODE',
                'details': {}
            }), 400
            
    except Exception as e:
        app.logger.error(f"Error in api_test_project_source: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e),
            'errorCode': 'INTERNAL_ERROR',
            'details': {}
        }), 500

@bp.route('/api/projects/<project_id>/sources/<source_key>/sync', methods=['POST'])
@require_auth
def api_sync_project_source_bidirectional(project_id, source_key):
    """
    API: Start async bidirectional sync operation (Push/Pull/Both)
    
    Request body:
    {
 "direction": "push|pull|both" // 
    }
    
    Returns:
    {
        "success": true,
        "jobId": "project_id_source_key_1234567890",
        "message": "Sync started"
    }
    """
    try:
        # Check project exists
        projects = load_projects()
        project = next((p for p in projects if p.get('id') == project_id), None)
        if not project:
            return jsonify({'success': False, 'error': 'Project not found', 'errorCode': 'PROJECT_NOT_FOUND'}), 404
        
        data = request.json or {}
        direction = data.get('direction', 'push')  # push, pull, both
        
        if direction not in ['push', 'pull', 'both']:
            return jsonify({
                'success': False,
                'error': 'Invalid direction. Must be push, pull, or both',
                'errorCode': 'INVALID_DIRECTION'
            }), 400
        
        # Load project config and source config
        config = load_project_config(project_id)
        sources = config.get('sources', {})
        normalized_sources = normalize_sources(sources, project_id)
        
        if source_key not in normalized_sources:
            return jsonify({
                'success': False,
                'error': f'Source {source_key} not configured',
                'errorCode': 'SOURCE_NOT_CONFIGURED'
            }), 404
        
        source_config = normalized_sources[source_key]
        
        # Start async sync
        job_id, success, error_code, error_msg = source_sync_service.start_sync(
            project_id=project_id,
            source_key=source_key,
            source_config=source_config,
            direction=direction,
            actor='user'
        )
        
        if not success:
            return jsonify({
                'success': False,
                'error': error_msg,
                'errorCode': error_code
            }), 400
        
        # git inventory group_vars/host_vars
        # , sync 
        # ensure_all_inventory_dirs sync callback endpoint
        
        return jsonify({
            'success': True,
            'jobId': job_id,
            'message': f'Sync started: {direction}'
        })
        
    except Exception as e:
        app.logger.error(f"Error starting sync: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e),
            'errorCode': 'INTERNAL_ERROR'
        }), 500


@bp.route('/api/projects/<project_id>/sources/<source_key>/sync/state', methods=['GET'])
@require_auth
def api_get_sync_state(project_id, source_key):
    """
    API: Get sync state for a source
    
    Returns:
    {
        "success": true,
        "state": {
            "lastPushAt": 1234567890,
            "lastPullAt": 1234567890,
            "lastPushStatus": "ok|running|failed|idle",
            "lastPullStatus": "ok|running|failed|idle",
            "lastPushError": null,
            "lastPullError": null,
            "lastPushRevision": "abc123...",
            "lastPullRevision": "def456..."
        }
    }
    """
    try:
        # Check project exists
        projects = load_projects()
        project = next((p for p in projects if p.get('id') == project_id), None)
        if not project:
            return jsonify({'success': False, 'error': 'Project not found', 'errorCode': 'PROJECT_NOT_FOUND'}), 404
        
        # Get sync state
        state = source_sync_service.get_sync_state(project_id, source_key)
        
        return jsonify({
            'success': True,
            'state': state
        })
        
    except Exception as e:
        app.logger.error(f"Error getting sync state: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e),
            'errorCode': 'INTERNAL_ERROR'
        }), 500


@bp.route('/api/projects/<project_id>/sources/<source_key>/sync/check-conflict', methods=['GET'])
@require_auth
def api_check_sync_conflict(project_id, source_key):
    """
    API: Check for sync conflicts without performing sync
    
    Returns:
    {
        "success": true,
        "hasConflict": bool,
        "message": str or null,
        "details": {
            "storageChanged": bool,
            "externalChanged": bool,
            "lastPushAt": timestamp or null,
            "lastPullAt": timestamp or null
        } or null
    }
    """
    try:
        # Check project exists
        projects = load_projects()
        project = next((p for p in projects if p.get('id') == project_id), None)
        if not project:
            return jsonify({'success': False, 'error': 'Project not found', 'errorCode': 'PROJECT_NOT_FOUND'}), 404
        
        # Load source config
        config = load_project_config(project_id)
        sources = config.get('sources', {})
        normalized_sources = normalize_sources(sources, project_id)
        
        if source_key not in normalized_sources:
            return jsonify({
                'success': False,
                'error': 'Source not found',
                'errorCode': 'SOURCE_NOT_FOUND'
            }), 404
        
        source_config = normalized_sources[source_key]
        
        # Check for conflicts
        conflict_result = source_sync_service.check_conflict(project_id, source_key, source_config)
        
        return jsonify({
            'success': True,
            'hasConflict': conflict_result['hasConflict'],
            'message': conflict_result['message'],
            'details': conflict_result['details']
        })
        
    except Exception as e:
        app.logger.error(f"Error checking sync conflict: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e),
            'errorCode': 'INTERNAL_ERROR'
        }), 500


@bp.route('/api/projects/<project_id>/sources/sync', methods=['POST'])
@require_auth
def api_sync_project_source(project_id):
    """API: Sync a git source (fetch/clone and checkout) - Legacy endpoint for backward compatibility"""
    try:
        # Check project exists
        projects = load_projects()
        project = next((p for p in projects if p.get('id') == project_id), None)
        if not project:
            return jsonify({'success': False, 'error': 'Project not found', 'errorCode': 'PROJECT_NOT_FOUND'}), 404
        
        data = request.json or {}
        source_key = data.get('sourceKey')
        
        if not source_key:
            return jsonify({'success': False, 'error': 'sourceKey is required', 'errorCode': 'MISSING_SOURCE_KEY'}), 400
        
        # Load project config
        config = load_project_config(project_id)
        sources = config.get('sources', {})
        
        # Normalize sources
        normalized_sources = normalize_sources(sources, project_id)
        
        if source_key not in normalized_sources:
            return jsonify({
                'success': False,
                'error': f'Source {source_key} not found',
                'errorCode': 'SOURCE_NOT_FOUND'
            }), 404
        
        source_config = normalized_sources[source_key]
        mode = source_config.get('mode', 'local')
        
        if mode != 'git':
            return jsonify({
                'success': False,
                'error': f'Source {source_key} is not a git source (mode: {mode})',
                'errorCode': 'NOT_GIT_SOURCE'
            }), 400
        
        git_config = source_config.get('git', {})
        repo_url = git_config.get('repo')
        ref = git_config.get('ref', 'main')
        subdir = git_config.get('subdir', '')
        auth_secret_id = git_config.get('authSecretId')
        
        if not repo_url:
            return jsonify({
                'success': False,
                'error': 'Repository URL is required',
                'errorCode': 'MISSING_REPO_URL'
            }), 400
        
        # Sync using SourceSyncService (supports repoLayout mapping)
        try:
            # Use source_sync_service for proper repoLayout support
            success, error_code, error_message = source_sync_service.execute_sync(
                project_id=project_id,
                source_key=source_key,
                source_config=source_config,
                direction='pull'
            )
            
            if not success:
                return jsonify({
                    'success': False,
                    'error': error_message or 'Sync failed',
                    'errorCode': error_code or 'SYNC_FAILED'
                }), 500
            
            # Update last synced timestamp in project config
            if 'syncTimestamps' not in config:
                config['syncTimestamps'] = {}
            config['syncTimestamps'][source_key] = int(time.time())
            save_project_config(project_id, config)
            
            # Get resolved path for response (for backward compatibility)
            try:
                resolved_path = git_source_manager.resolve_path(
                    project_id=project_id,
                    source_key=source_key,
                    repo_url=repo_url,
                    ref=ref,
                    subdir=subdir,
                    auth_secret_id=auth_secret_id,
                    force_refresh=False  # Use cached version
                )
            except Exception as e:
                app.logger.warning(f"Could not resolve path for response: {e}")
                resolved_path = None
            
            app.logger.info(f"Source synced: {project_id}/{source_key}")
            
            # git inventory group_vars/host_vars
            if source_key == 'repo' or subdir == '' or 'inventor' in subdir.lower():
                try:
                    ensure_all_inventory_dirs(project_id)
                except Exception as e:
                    app.logger.warning(f"Failed to ensure inventory directories after sync: {e}")
            
            return jsonify({
                'success': True,
                'ok': True,
                'syncedAt': config['syncTimestamps'][source_key],
                'details': {
                    'repo': repo_url,
                    'ref': ref,
                    'subdir': subdir,
                    'resolvedPath': str(resolved_path) if resolved_path else None,
                    'exists': resolved_path.exists() if resolved_path else False,
                    'isFile': resolved_path.is_file() if resolved_path else False,
                    'isDir': resolved_path.is_dir() if resolved_path else False
                }
            })
            
        except GitSourceError as e:
            app.logger.error(f"Git sync error for {source_key}: {e.error_code} - {e.message}")
            return jsonify({
                'success': False,
                'error': e.message,
                'errorCode': e.error_code,
                'details': {}
            }), 400
        except Exception as e:
            app.logger.error(f"Error syncing source {source_key}: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': f'Error syncing source: {str(e)}',
                'errorCode': 'SYNC_ERROR',
                'details': {}
            }), 500
            
    except Exception as e:
        app.logger.error(f"Error in api_sync_project_source: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e),
            'errorCode': 'INTERNAL_ERROR',
            'details': {}
        }), 500


@bp.route('/api/inventory/ensure-dirs', methods=['POST'])
@require_auth
def api_ensure_inventory_dirs():
    """API: inventory group_vars/host_vars """
    try:
        project_id = require_project_id_from_request()
        if not project_id:
            return jsonify({'success': False, 'error': 'Project ID is required'}), 400
        
        ensure_all_inventory_dirs(project_id)
        
        return jsonify({
            'success': True,
            'message': 'Inventory directories checked and created if needed'
        })
    except Exception as e:
        app.logger.error(f"Error ensuring inventory directories: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/projects/<project_id>/autosync', methods=['GET'])
@require_auth
def api_get_autosync_config(project_id):
    """
    API: Get autosync configuration for a project
    
    Returns:
    {
        "success": true,
        "autosync": {
            "enabled": false,
            "direction": "pull",
            "sourceKeys": [],
            "intervalSeconds": 3600,
            "lastRunAt": null,
            "nextRunAt": null,
            "lastStatus": null,
            "lastError": null
        }
    }
    """
    try:
        # Check project exists
        projects = load_projects()
        project = next((p for p in projects if p.get('id') == project_id), None)
        if not project:
            return jsonify({'success': False, 'error': 'Project not found', 'errorCode': 'PROJECT_NOT_FOUND'}), 404
        
        # Load project config
        config = load_project_config(project_id)
        autosync = config.get('autosync', {})
        
        # Return default structure if autosync not configured
        default_autosync = {
            'enabled': False,
            'direction': 'pull',
            'sourceKeys': [],
            'intervalSeconds': 3600,
            'lastRunAt': None,
            'nextRunAt': None,
            'lastStatus': None,
            'lastError': None
        }
        
        # Merge with defaults
        result_autosync = {**default_autosync, **autosync}
        
        return jsonify({
            'success': True,
            'autosync': result_autosync
        })
        
    except Exception as e:
        app.logger.error(f"Error getting autosync config: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e),
            'errorCode': 'INTERNAL_ERROR'
        }), 500


@bp.route('/api/projects/<project_id>/autosync', methods=['PUT'])
@require_auth
def api_update_autosync_config(project_id):
    """
    API: Update autosync configuration for a project
    
    Request body:
    {
        "enabled": true,
        "direction": "pull|push|both",
        "sourceKeys": ["inventory", "roles_playbooks"],
        "intervalSeconds": 3600
    }
    
    Returns:
    {
        "success": true,
        "autosync": { ... }
    }
    """
    try:
        # Check project exists
        projects = load_projects()
        project = next((p for p in projects if p.get('id') == project_id), None)
        if not project:
            return jsonify({'success': False, 'error': 'Project not found', 'errorCode': 'PROJECT_NOT_FOUND'}), 404
        
        data = request.json or {}
        
        # Validate direction
        direction = data.get('direction', 'pull')
        if direction not in ['push', 'pull', 'both']:
            return jsonify({
                'success': False,
                'error': 'Invalid direction. Must be push, pull, or both',
                'errorCode': 'INVALID_DIRECTION'
            }), 400
        
        # Validate intervalSeconds
        interval_seconds = data.get('intervalSeconds', 3600)
        if not isinstance(interval_seconds, int) or interval_seconds < 60:
            return jsonify({
                'success': False,
                'error': 'intervalSeconds must be an integer >= 60',
                'errorCode': 'INVALID_INTERVAL'
            }), 400
        
        # Validate sourceKeys
        source_keys = data.get('sourceKeys', [])
        if not isinstance(source_keys, list):
            return jsonify({
                'success': False,
                'error': 'sourceKeys must be a list',
                'errorCode': 'INVALID_SOURCE_KEYS'
            }), 400
        
        # Load project config
        config = load_project_config(project_id)
        
        # Get existing autosync config or create new
        autosync = config.get('autosync', {})
        
        # Update autosync config
        enabled = data.get('enabled', False)
        autosync['enabled'] = enabled
        autosync['direction'] = direction
        autosync['sourceKeys'] = source_keys
        autosync['intervalSeconds'] = interval_seconds
        
        # Calculate nextRunAt if enabled
        import time
        if enabled:
            current_time = int(time.time())
            # If nextRunAt is not set or in the past, schedule for now + interval
            next_run_at = autosync.get('nextRunAt')
            if not next_run_at or next_run_at <= current_time:
                autosync['nextRunAt'] = current_time + interval_seconds
        else:
            # Clear nextRunAt if disabled
            autosync['nextRunAt'] = None
        
        # Save updated config
        config['autosync'] = autosync
        save_project_config(project_id, config)
        
        app.logger.info(f"Autosync config updated for project {project_id}: enabled={enabled}, direction={direction}, sources={source_keys}")
        
        return jsonify({
            'success': True,
            'autosync': autosync
        })
        
    except Exception as e:
        app.logger.error(f"Error updating autosync config: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e),
            'errorCode': 'INTERNAL_ERROR'
        }), 500


@bp.route('/api/projects/<project_id>/sources/resolve', methods=['POST'])
@require_auth
def api_resolve_source_path(project_id):
    """API: Resolve path for a git source (INTERNAL USE ONLY - for sync operations)
    
    WARNING: This endpoint returns git cache paths which are internal implementation details.
    UI should NOT rely on these paths. Use Project Storage paths instead.
    """
    try:
        # Check project exists
        projects = load_projects()
        project = next((p for p in projects if p.get('id') == project_id), None)
        if not project:
            return jsonify({'success': False, 'error': 'Project not found'}), 404
        
        data = request.json or {}
        source_key = data.get('sourceKey')
        repo_url = data.get('repoUrl')
        ref = data.get('ref', 'main')
        subdir = data.get('subdir', '')
        auth_secret_id = data.get('authSecretId')
        force_refresh = data.get('forceRefresh', False)
        
        if not source_key:
            return jsonify({'success': False, 'error': 'sourceKey is required'}), 400
        if not repo_url:
            return jsonify({'success': False, 'error': 'repoUrl is required'}), 400
        
        # Resolve path using GitSourceManager (returns git cache path - internal only)
        try:
            resolved_path = git_source_manager.resolve_path(
                project_id=project_id,
                source_key=source_key,
                repo_url=repo_url,
                ref=ref,
                subdir=subdir,
                auth_secret_id=auth_secret_id,
                force_refresh=force_refresh
            )
            
            # Return logical identifier instead of absolute path
            # UI should use Project Storage paths, not git cache paths
            return jsonify({
                'success': True,
                'sourceKey': source_key,
                'repoUrl': repo_url,
                'ref': ref,
                'subdir': subdir,
                'exists': resolved_path.exists(),
                'isFile': resolved_path.is_file(),
                'isDir': resolved_path.is_dir(),
                # Internal path - should not be used by UI
                '_internalPath': str(resolved_path) if resolved_path.exists() else None
            })
        except GitSourceError as e:
            return jsonify({
                'success': False,
                'error': e.message,
                'errorCode': e.error_code
            }), 400
    except Exception as e:
        app.logger.error(f"Error resolving source path: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


