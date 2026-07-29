"""Project sources / repo-layout / sync service (Phase 3 extraction)."""
from __future__ import annotations

import importlib
import logging
import os
import shutil
import sys
import time
from pathlib import Path

from utils.paths import validate_repo_layout_path
from utils.project_paths import get_project_dir  # noqa: F401

logger = logging.getLogger("app")

PROJECT_SOURCES_ENABLED = os.environ.get('PROJECT_SOURCES_ENABLED', 'true').lower() == 'true'


def _app():
    for module_name in ("__main__", "app"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "load_project_config"):
            return module
    return importlib.import_module("app")


def load_project_config(project_id):
    return _app().load_project_config(project_id)


def get_default_sources(project_id):
    """
 .
 .
    """
    project_dir = get_project_dir(project_id)
    
    # : repo/ (Ansible), stacks/ (Cloud Provisioning / OpenTofu)
    defaults = {
        'repo': {
            'mode': 'local',
            'localPath': 'repo'
        },
        'stacks': {
            'mode': 'local',
            'localPath': 'stacks'
        }
    }
    
    return defaults


def normalize_sources(sources, project_id):
    """
 sources, .
 source binding .
    """
    defaults = get_default_sources(project_id)
    normalized = {}
    
    # : repo/
    resource_types = ['repo', 'stacks']
    
    for resource_type in resource_types:
        if resource_type in sources:
            source = sources[resource_type].copy()
            # mode , default
            if 'mode' not in source:
                source['mode'] = defaults[resource_type]['mode']
            # mode local localPath, default
            if source['mode'] == 'local' and 'localPath' not in source:
                default_local_path = defaults[resource_type].get('localPath')
                # default None, 
                if default_local_path is None:
                    # Fallback to standard names if default is None
                    standard_paths = {
                        'repo': 'repo',
                        'stacks': 'stacks',
                        'roles_playbooks': 'roles-playbooks',
                        'inventory': 'inventory',
                        'ansible_config': 'ansible.cfg',
                        'group_vars_storage': 'group_vars',
                        'host_vars_storage': 'host_vars',
                        'secrets_storage': 'secrets-storage'
                    }
                    source['localPath'] = standard_paths.get(resource_type, resource_type)
                else:
                    source['localPath'] = default_local_path
            # mode git, git 
            if source['mode'] == 'git':
                if 'git' not in source:
                    source['git'] = {}
                git_config = source['git']
                if 'ref' not in git_config:
                    git_config['ref'] = 'main'
                if 'subdir' not in git_config:
                    git_config['subdir'] = ''
                if 'authSecretId' not in git_config:
                    git_config['authSecretId'] = None
            
            # Source binding: 
            if 'syncDirection' not in source:
                source['syncDirection'] = 'pull'  # : Pull only (Git → Project Storage). : none, push, pull, both
            if 'syncStatus' not in source:
                source['syncStatus'] = {
                    'push': {'status': 'idle', 'lastSyncAt': None, 'error': None},
                    'pull': {'status': 'idle', 'lastSyncAt': None, 'error': None}
                }
            
            normalized[resource_type] = source
        else:
            # defaults 
            normalized[resource_type] = defaults[resource_type].copy()
            # default localPath is None, 
            if normalized[resource_type].get('mode') == 'local':
                default_local_path = normalized[resource_type].get('localPath')
                if default_local_path is None:
                    # Fallback to standard names if default is None
                    standard_paths = {
                        'repo': 'repo',
                        'stacks': 'stacks',
                        'roles_playbooks': 'roles-playbooks',
                        'inventory': 'inventory',
                        'ansible_config': 'ansible.cfg',
                        'group_vars_storage': 'group_vars',
                        'host_vars_storage': 'host_vars',
                        'secrets_storage': 'secrets-storage'
                    }
                    normalized[resource_type]['localPath'] = standard_paths.get(resource_type, resource_type)
            # default sync binding ( : Pull only)
            normalized[resource_type]['syncDirection'] = 'pull'
            normalized[resource_type]['syncStatus'] = {
                'push': {'status': 'idle', 'lastSyncAt': None, 'error': None},
                'pull': {'status': 'idle', 'lastSyncAt': None, 'error': None}
            }
    
    return normalized


def validate_repo_layout(repo_layout):
    """
 repoLayout .
 (is_valid, error_code, error_message)
    """
    if repo_layout is None:
        return True, None, None  # repoLayout is optional
    
    if not isinstance(repo_layout, dict):
        return False, 'INVALID_REPO_LAYOUT_FORMAT', 'repoLayout must be a dictionary'
    
    valid_keys = ['playbooks', 'roles', 'inventories', 'vars']
    for key, value in repo_layout.items():
        if key not in valid_keys:
            return False, 'INVALID_REPO_LAYOUT_KEY', f'Invalid repoLayout key: {key}. Must be one of {valid_keys}'
        
        if not isinstance(value, str):
            return False, 'INVALID_REPO_LAYOUT_VALUE', f'repoLayout.{key} must be a string'
        
        # Validate path
        is_valid, error_msg = validate_repo_layout_path(value)
        if not is_valid:
            return False, 'INVALID_REPO_LAYOUT_PATH', f'repoLayout.{key}: {error_msg}'
    
    return True, None, None


def validate_sources(sources):
    """
 sources .
 (is_valid, error_code, error_message)
    """
    if not isinstance(sources, dict):
        return False, 'INVALID_FORMAT', 'Sources must be a dictionary'
    
    valid_modes = ['local', 'git']
    valid_resource_types = ['repo', 'stacks']
    
    for resource_type, source_config in sources.items():
        if resource_type not in valid_resource_types:
            return False, 'INVALID_RESOURCE_TYPE', f'Invalid resource type: {resource_type}'
        
        if not isinstance(source_config, dict):
            return False, 'INVALID_SOURCE_CONFIG', f'Source config for {resource_type} must be a dictionary'
        
        mode = source_config.get('mode')
        if mode not in valid_modes:
            return False, 'UNSUPPORTED_MODE', f'Unsupported mode for {resource_type}: {mode}. Must be "local" or "git"'
        
        if mode == 'local':
            local_path = source_config.get('localPath')
            if not local_path:
                return False, 'MISSING_LOCAL_PATH', f'localPath is required for {resource_type} in local mode'
            if not isinstance(local_path, str):
                return False, 'INVALID_LOCAL_PATH', f'localPath for {resource_type} must be a string'
            # path traversal ( )
            if '..' in local_path:
                return False, 'INVALID_LOCAL_PATH', f'localPath for {resource_type} contains path traversal (..)'
        
        elif mode == 'git':
            git_config = source_config.get('git')
            if not isinstance(git_config, dict):
                return False, 'MISSING_GIT_CONFIG', f'git configuration is required for {resource_type} in git mode'
            
            repo = git_config.get('repo')
            if not repo:
                return False, 'MISSING_GIT_REPO', f'git.repo is required for {resource_type} in git mode'
            if not isinstance(repo, str):
                return False, 'INVALID_GIT_REPO', f'git.repo for {resource_type} must be a string'
            # URL
            if not (repo.startswith('http://') or repo.startswith('https://') or repo.startswith('git@') or repo.startswith('git://')):
                return False, 'INVALID_GIT_REPO', f'git.repo for {resource_type} must be a valid Git URL'
            
            ref = git_config.get('ref')
            if ref and not isinstance(ref, str):
                return False, 'INVALID_GIT_REF', f'git.ref for {resource_type} must be a string'
            
            subdir = git_config.get('subdir', '')
            if subdir:
                if not isinstance(subdir, str):
                    return False, 'INVALID_GIT_SUBDIR', f'git.subdir for {resource_type} must be a string'
                # path traversal subdir
                if '..' in subdir or subdir.startswith('/'):
                    return False, 'INVALID_GIT_SUBDIR', f'git.subdir for {resource_type} contains invalid characters (path traversal detected)'
            
            auth_secret_id = git_config.get('authSecretId')
            if auth_secret_id is not None and not isinstance(auth_secret_id, str):
                return False, 'INVALID_AUTH_SECRET_ID', f'git.authSecretId for {resource_type} must be a string or null'
    
    return True, None, None


def resolve_project_source(project_id: str, source_key: str) -> dict:
    """
    Resolve project source configuration and return Project Storage path.
    
    CRITICAL: This function ALWAYS returns a Project Storage path.
    External paths (Local Path / Git cache) are NEVER returned.
    They are only accessed inside SourceSyncService during sync operations.
    
    Args:
        project_id: Project ID
        source_key: Source key ('repo')
    
    Returns:
        dict with keys:
            - mode: 'local' or 'git' (from source config, if available)
            - rootPath: Path to Project Storage (ALWAYS projects/<projectId>/...)
            - meta: Additional metadata (repo_url, ref, subdir for git mode, if available)
    
    Raises:
        ValueError: If source_key is invalid
    """
    # ALWAYS return Project Storage path
    root_path = get_project_storage_path(project_id, source_key)
    
    # Ensure directory exists (for directories, not files like ansible.cfg)
    if source_key != 'ansible_config':
        root_path.mkdir(parents=True, exist_ok=True)
    else:
        # For ansible.cfg, ensure parent directory exists
        root_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Try to get source configuration for metadata (if available)
    mode = 'local'  # Default
    meta = {}
    
    try:
        if PROJECT_SOURCES_ENABLED:
            config = load_project_config(project_id)
            sources = config.get('sources', {})
            normalized_sources = normalize_sources(sources, project_id)
            
            if source_key in normalized_sources:
                source_config = normalized_sources[source_key]
                mode = source_config.get('mode', 'local')
                
                if mode == 'local':
                    local_path = source_config.get('localPath')
                    if local_path:
                        meta = {
                            'localPath': local_path,
                            'projectDir': str(get_project_dir(project_id))
                        }
                elif mode == 'git':
                    git_config = source_config.get('git', {})
                    repo_url = git_config.get('repo')
                    if repo_url:
                        meta = {
                            'repoUrl': repo_url,
                            'ref': git_config.get('ref', 'main'),
                            'subdir': git_config.get('subdir', ''),
                            'authSecretId': git_config.get('authSecretId')  # Note: never log this value
                        }
    except Exception as e:
        # If we can't load config, still return Project Storage path
        logger.warning(f"[resolveProjectSource] Could not load source config for {source_key}: {e}, using defaults")
    
    return {
        'mode': mode,
        'rootPath': root_path.resolve(),
        'meta': meta
    }


def _resolve_legacy_source(project_id: str, source_key: str) -> dict:
    """
    DEPRECATED: This function is kept for backward compatibility but now
    always returns Project Storage paths (no legacy fallbacks).
    
    Use resolve_project_source() instead, which always returns Project Storage.
    """
    # Always return Project Storage path (no legacy fallbacks)
    return resolve_project_source(project_id, source_key)


# ============================================================================
# Phase 3 refactor: 6 project-sources routes (status/revert/list/analyze/update/test) moved to backend/api/sources_routes.py
# (registered via api.register_blueprints).
# ============================================================================

def get_project_storage_path(project_id, source_key):
    """
 Project Storage source.
 Project Storage - .
    
    Args:
 project_id: ID 
 source_key: ('repo', 'inventory', 'roles_playbooks', 'ansible_config', 
                   'group_vars_storage', 'host_vars_storage', 'secrets_storage')
    
    Returns:
 Path / Project Storage
    
    Note:
 source_key / 'repo' workspace,
 'repo' workspace.
    """
    project_dir = get_project_dir(project_id)
    repo_dir = project_dir / 'repo'
    
    # source_key / Project Storage
    # 'repo' workspace
    storage_mapping = {
        'repo': repo_dir,  # workspace (Ansible)
        'stacks': project_dir / 'stacks',  # Cloud Provisioning / OpenTofu stacks workspace
        'inventory': repo_dir / 'inventories',  # Inventory files
        'roles_playbooks': repo_dir,  # Roles playbooks repo
        'ansible_config': project_dir / 'ansible-config' / 'ansible.cfg',  # Ansible config file
        'group_vars_storage': repo_dir / 'group_vars',  # Group vars
        'host_vars_storage': repo_dir / 'host_vars',  # Host vars
        'secrets_storage': project_dir / 'secrets-storage'  # Secrets storage ( repo )
    }
    
    if source_key not in storage_mapping:
        raise ValueError(f"Unknown source_key: {source_key}. Supported keys: {', '.join(storage_mapping.keys())}")
    
    return storage_mapping[source_key]


def sync_source_push(project_id, source_key, external_path):
    """
 Push: Project Storage → External Source
    
    Args:
 project_id: ID 
 source_key: 
 external_path: Path (Local Path Git cache)
    
    Returns:
 dict 
    """
    try:
        project_storage = get_project_storage_path(project_id, source_key)
        external = Path(external_path)
        
        if not project_storage.exists():
            return {
                'success': False,
                'error': f'Project Storage does not exist: {project_storage}',
                'errorCode': 'STORAGE_NOT_FOUND'
            }
        
            # Project Storage External
            if project_storage.is_file():
                # (ansible.cfg)
                external.parent.mkdir(parents=True, exist_ok=True)
                import shutil
                shutil.copy2(project_storage, external)
                logger.info(f"[sync_push] Copied file {project_storage} → {external}")
            else:
                if not external.exists():
                    external.mkdir(parents=True, exist_ok=True)
                
                import shutil
                # external ( .git )
                if external.exists() and external.is_dir():
                    for item in external.iterdir():
                        if item.name == '.git':
                            continue
                        if item.is_dir():
                            shutil.rmtree(item)
                        else:
                            item.unlink()
                
                # Project Storage External
                for item in project_storage.iterdir():
                    if item.is_dir():
                        shutil.copytree(item, external / item.name, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, external / item.name)
                
                logger.info(f"[sync_push] Synced directory {project_storage} → {external}")
        
        return {
            'success': True,
            'message': f'Successfully pushed {source_key} to external source',
            'syncedAt': int(time.time())
        }
    except Exception as e:
        logger.error(f"Error in sync_push for {source_key}: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e),
            'errorCode': 'SYNC_PUSH_FAILED'
        }


def sync_source_pull(project_id, source_key, external_path):
    """
 Pull: External Source → Project Storage
    
    Args:
 project_id: ID 
 source_key: 
 external_path: Path (Local Path Git cache)
    
    Returns:
 dict 
    """
    try:
        project_storage = get_project_storage_path(project_id, source_key)
        external = Path(external_path)
        
        if not external.exists():
            return {
                'success': False,
                'error': f'External source does not exist: {external}',
                'errorCode': 'EXTERNAL_NOT_FOUND'
            }
        
            # External Project Storage
            if external.is_file():
                # (ansible.cfg)
                project_storage.parent.mkdir(parents=True, exist_ok=True)
                import shutil
                shutil.copy2(external, project_storage)
                logger.info(f"[sync_pull] Copied file {external} → {project_storage}")
            else:
                project_storage.mkdir(parents=True, exist_ok=True)
                
                import shutil
                # Project Storage
                if project_storage.exists() and project_storage.is_dir():
                    for item in project_storage.iterdir():
                        if item.is_dir():
                            shutil.rmtree(item)
                        else:
                            item.unlink()
                
                # External Project Storage
                for item in external.iterdir():
                    if item.is_dir():
                        shutil.copytree(item, project_storage / item.name, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, project_storage / item.name)
            
            logger.info(f"[sync_pull] Synced directory {external} → {project_storage}")
        
        return {
            'success': True,
            'message': f'Successfully pulled {source_key} from external source',
            'syncedAt': int(time.time())
        }
    except Exception as e:
        logger.error(f"Error in sync_pull for {source_key}: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e),
            'errorCode': 'SYNC_PULL_FAILED'
        }


