"""
SourceSyncService: Bidirectional synchronization between Project Storage and External Sources.

Handles:
- Local Path sync (push/pull)
- Git sync (push with commit, pull with fetch)
- Conflict detection for bidirectional sync
- Async execution with job tracking
- State persistence
"""

import os
import time
import json
import shutil
import hashlib
import threading
import subprocess
import logging
from pathlib import Path
from typing import Dict, Optional, Tuple
from enum import Enum

# logger source_sync_service
# root logger, backend.log
logger = logging.getLogger(__name__)
# , logger root logger
logger.setLevel(logging.INFO)
logger.propagate = True  # root logger backend.log
# handlers - root logger

try:
    from services.git_source_manager import GitSourceManager, GitSourceError
except ImportError:
    from services.git_source_manager import GitSourceManager, GitSourceError


class SyncDirection(Enum):
    PUSH = 'push'
    PULL = 'pull'
    BOTH = 'both'


class SyncStatus(Enum):
    IDLE = 'idle'
    RUNNING = 'running'
    OK = 'ok'
    FAILED = 'failed'


class SyncErrorCode(Enum):
    SYNC_CONFLICT = 'SYNC_CONFLICT'
    SYNC_PATH_INVALID = 'SYNC_PATH_INVALID'
    SYNC_GIT_AUTH_FAILED = 'SYNC_GIT_AUTH_FAILED'
    SYNC_GIT_REF_NOT_FOUND = 'SYNC_GIT_REF_NOT_FOUND'
    SYNC_IO_ERROR = 'SYNC_IO_ERROR'
    SYNC_STORAGE_NOT_FOUND = 'SYNC_STORAGE_NOT_FOUND'
    SYNC_EXTERNAL_NOT_FOUND = 'SYNC_EXTERNAL_NOT_FOUND'
    SYNC_LOCKED = 'SYNC_LOCKED'


class SourceSyncService:
    """
    Service for bidirectional synchronization between Project Storage and External Sources.
    """
    
    def __init__(self, projects_dir: Path, git_source_manager: GitSourceManager):
        """
        Args:
            projects_dir: Base directory for projects (e.g., projects/)
            git_source_manager: GitSourceManager instance for Git operations
        """
        self.projects_dir = projects_dir
        self.git_source_manager = git_source_manager
        
        # Active sync jobs: { (project_id, source_key): job_info }
        self._active_jobs: Dict[Tuple[str, str], dict] = {}
        self._job_lock = threading.Lock()
        
        # Sync state file per project
        self._sync_state_file = lambda project_id: projects_dir / project_id / '.sync_state.json'
    
    def get_project_storage_path(self, project_id: str, source_key: str) -> Path:
        """
        Get Project Storage path for a source.
        Project Storage is the local storage managed by the application.
        
        Args:
            project_id: Project ID
            source_key: Source key (inventory, roles_playbooks, etc.)
        
        Returns:
            Path to Project Storage directory/file
        """
        project_dir = self.projects_dir / project_id
        
        storage_mapping = {
            'repo': project_dir / 'repo',     # workspace (Ansible)
            'stacks': project_dir / 'stacks'  # Cloud Provisioning / OpenTofu stacks workspace
        }
        
        if source_key not in storage_mapping:
            raise ValueError(f"Unknown source_key: {source_key}")
        
        return storage_mapping[source_key]
    
    def _get_sync_state_file(self, project_id: str) -> Path:
        """Get path to sync state file for project"""
        return self._sync_state_file(project_id)
    
    def _load_project_config(self, project_id: str) -> dict:
        """Load project configuration from project.json"""
        project_dir = self.projects_dir / project_id
        config_file = project_dir / 'project.json'
        
        if not config_file.exists():
            return {}
        
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load project config for {project_id}: {e}")
            return {}
    
    def _get_repo_layout(self, project_id: str) -> dict:
        """
        Get repo layout configuration with defaults.
        
        Returns layout paths for Ansible entities. If repoLayout is not configured,
        returns default values.
        
        Returns:
            dict with keys: 'playbooks', 'roles', 'inventories', 'vars'
            Default values: 'playbooks', 'roles', 'inventories', 'vars'
        """
        config = self._load_project_config(project_id)
        layout = config.get('repoLayout', {})
        
        return {
            'playbooks': layout.get('playbooks', 'playbooks'),
            'roles': layout.get('roles', 'roles'),
            'inventories': layout.get('inventories', 'inventories'),
            'vars': layout.get('vars', 'vars')
        }
    
    def _load_sync_state(self, project_id: str) -> dict:
        """Load sync state for project"""
        state_file = self._get_sync_state_file(project_id)
        if not state_file.exists():
            return {}
        
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            # Log error but return empty state
            return {}
    
    def _save_sync_state(self, project_id: str, state: dict):
        """Save sync state for project"""
        state_file = self._get_sync_state_file(project_id)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    
    def get_sync_state(self, project_id: str, source_key: str) -> dict:
        """
        Get sync state for a specific source.
        
        Returns:
            dict with keys:
                - lastPushAt: timestamp or None
                - lastPullAt: timestamp or None
                - lastPushStatus: idle|running|ok|failed
                - lastPullStatus: idle|running|ok|failed
                - lastPushError: error message or None
                - lastPullError: error message or None
                - lastPushRevision: revision hash or None
                - lastPullRevision: revision hash or None
        """
        state = self._load_sync_state(project_id)
        source_state = state.get(source_key, {})
        
        # Check if there's an active job
        job_key = (project_id, source_key)
        with self._job_lock:
            if job_key in self._active_jobs:
                job = self._active_jobs[job_key]
                # Update status from active job
                if 'push' in job.get('direction', []):
                    source_state['lastPushStatus'] = 'running'
                if 'pull' in job.get('direction', []):
                    source_state['lastPullStatus'] = 'running'
        
        # Return with defaults
        return {
            'lastPushAt': source_state.get('lastPushAt'),
            'lastPullAt': source_state.get('lastPullAt'),
            'lastPushStatus': source_state.get('lastPushStatus', 'idle'),
            'lastPullStatus': source_state.get('lastPullStatus', 'idle'),
            'lastPushError': source_state.get('lastPushError'),
            'lastPullError': source_state.get('lastPullError'),
            'lastPushRevision': source_state.get('lastPushRevision'),
            'lastPullRevision': source_state.get('lastPullRevision')
        }
    
    def _update_sync_state(self, project_id: str, source_key: str, direction: str, 
                          status: str, error: Optional[str] = None, revision: Optional[str] = None):
        """Update sync state for a direction"""
        state = self._load_sync_state(project_id)
        
        if source_key not in state:
            state[source_key] = {}
        
        now = int(time.time())
        
        if direction == 'push':
            state[source_key]['lastPushAt'] = now if status == 'ok' else state[source_key].get('lastPushAt')
            state[source_key]['lastPushStatus'] = status
            if error:
                state[source_key]['lastPushError'] = error
            elif status == 'ok':
                state[source_key].pop('lastPushError', None)
            if revision:
                state[source_key]['lastPushRevision'] = revision
        elif direction == 'pull':
            state[source_key]['lastPullAt'] = now if status == 'ok' else state[source_key].get('lastPullAt')
            state[source_key]['lastPullStatus'] = status
            if error:
                state[source_key]['lastPullError'] = error
            elif status == 'ok':
                state[source_key].pop('lastPullError', None)
            if revision:
                state[source_key]['lastPullRevision'] = revision
        
        self._save_sync_state(project_id, state)
    
    def _compute_revision(self, path: Path) -> str:
        """
        Compute revision hash for a path (directory or file).
        Uses file mtimes and sizes for fast comparison.
        """
        if not path.exists():
            return ''
        
        if path.is_file():
            stat = path.stat()
            content = f"{path}:{stat.st_mtime}:{stat.st_size}"
            return hashlib.md5(content.encode()).hexdigest()
        else:
            # For directories, hash all files recursively
            hashes = []
            for item in sorted(path.rglob('*')):
                if item.is_file():
                    stat = item.stat()
                    rel_path = item.relative_to(path)
                    hashes.append(f"{rel_path}:{stat.st_mtime}:{stat.st_size}")
            content = '\n'.join(hashes)
            return hashlib.md5(content.encode()).hexdigest() if content else ''
    
    def _detect_conflict(self, project_id: str, source_key: str, 
                        storage_path: Path, external_path: Path) -> Tuple[bool, Optional[str], Optional[dict]]:
        """
        Detect if both sides changed since last sync (conflict).
        
        Returns:
            (has_conflict, error_message, conflict_details)
            conflict_details: {
                'storageChanged': bool,
                'externalChanged': bool,
                'lastPushAt': timestamp or None,
                'lastPullAt': timestamp or None
            }
        """
        state = self.get_sync_state(project_id, source_key)
        
        # If never synced, no conflict
        if not state.get('lastPushAt') and not state.get('lastPullAt'):
            return False, None, None
        
        # Compute current revisions
        storage_rev = self._compute_revision(storage_path)
        external_rev = self._compute_revision(external_path)
        
        # Get last known revisions
        last_push_rev = state.get('lastPushRevision')
        last_pull_rev = state.get('lastPullRevision')
        
        # Conflict if:
        # - Storage changed since last push (storage_rev != last_push_rev)
        # - AND External changed since last pull (external_rev != last_pull_rev)
        storage_changed = last_push_rev and storage_rev != last_push_rev
        external_changed = last_pull_rev and external_rev != last_pull_rev
        
        conflict_details = {
            'storageChanged': storage_changed,
            'externalChanged': external_changed,
            'lastPushAt': state.get('lastPushAt'),
            'lastPullAt': state.get('lastPullAt')
        }
        
        if storage_changed and external_changed:
            msg = "Both Project Storage and External Source have changed since last sync"
            return True, msg, conflict_details
        
        return False, None, conflict_details
    
    def check_conflict(self, project_id: str, source_key: str, source_config: dict) -> dict:
        """
        Check for conflicts without performing sync.
        
        Returns:
            {
                'hasConflict': bool,
                'message': str or None,
                'details': dict or None
            }
        """
        try:
            storage_path = self.get_project_storage_path(project_id, source_key)
            mode = source_config.get('mode', 'local')
            
            # Resolve external path
            external_path = None
            if mode == 'local':
                local_path = source_config.get('localPath', '')
                if local_path:
                    external_path = Path(local_path)
            elif mode == 'git':
                try:
                    git_config = source_config.get('git', {})
                    resolved = self.git_source_manager.resolve_path(
                        project_id=project_id,
                        source_key=source_key,
                        repo_url=git_config.get('repo', ''),
                        ref=git_config.get('ref', 'main'),
                        subdir=git_config.get('subdir', ''),
                        auth_secret_id=git_config.get('authSecretId'),
                        force_refresh=False
                    )
                    external_path = resolved  # resolve_path returns Path directly, not a dict
                except Exception:
                    external_path = None
            
            if not external_path:
                return {
                    'hasConflict': False,
                    'message': None,
                    'details': None
                }
            
            has_conflict, msg, details = self._detect_conflict(
                project_id, source_key, storage_path, external_path
            )
            
            return {
                'hasConflict': has_conflict,
                'message': msg,
                'details': details
            }
        except Exception as e:
            return {
                'hasConflict': False,
                'message': f"Error checking conflict: {str(e)}",
                'details': None
            }
    
    def _sync_local_push(self, project_id: str, source_key: str, 
                        storage_path: Path, external_path: Path) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Sync Local: Push from Project Storage to External Path.
        
        Returns:
            (success, error_code, error_message)
        """
        try:
            # Validate external path
            if not external_path.is_absolute():
                return False, SyncErrorCode.SYNC_PATH_INVALID.value, "External path must be absolute"
            
            if '..' in str(external_path):
                return False, SyncErrorCode.SYNC_PATH_INVALID.value, "Path traversal detected"
            
            if not storage_path.exists():
                return False, SyncErrorCode.SYNC_STORAGE_NOT_FOUND.value, f"Project Storage not found: {storage_path}", None
            
            # Ensure external path parent exists
            external_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Copy files
            if storage_path.is_file():
                # For files
                shutil.copy2(storage_path, external_path)
            else:
                # For directories: clear destination (except .git) and copy
                if external_path.exists() and external_path.is_dir():
                    for item in external_path.iterdir():
                        if item.name == '.git':
                            continue
                        if item.is_dir():
                            shutil.rmtree(item)
                        else:
                            item.unlink()
                
                # Copy contents
                for item in storage_path.iterdir():
                    if item.is_dir():
                        shutil.copytree(item, external_path / item.name, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, external_path / item.name)
            
            # Compute revision
            revision = self._compute_revision(external_path)
            
            return True, None, None, revision
            
        except IOError as e:
            return False, SyncErrorCode.SYNC_IO_ERROR.value, f"IO error: {str(e)}", None
        except Exception as e:
            return False, SyncErrorCode.SYNC_IO_ERROR.value, f"Unexpected error: {str(e)}", None
    
    def _sync_local_pull(self, project_id: str, source_key: str,
                         storage_path: Path, external_path: Path) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        """
        Sync Local: Pull from External Path to Project Storage.
        
        Returns:
            (success, error_code, error_message, revision)
        """
        try:
            # Validate external path
            if not external_path.is_absolute():
                return False, SyncErrorCode.SYNC_PATH_INVALID.value, "External path must be absolute", None
            
            if '..' in str(external_path):
                return False, SyncErrorCode.SYNC_PATH_INVALID.value, "Path traversal detected", None
            
            if not external_path.exists():
                return False, SyncErrorCode.SYNC_EXTERNAL_NOT_FOUND.value, f"External source not found: {external_path}", None
            
            # Ensure storage path exists
            storage_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Copy files
            if external_path.is_file():
                # For files
                shutil.copy2(external_path, storage_path)
            else:
                # For directories: clear storage and copy
                if storage_path.exists() and storage_path.is_dir():
                    for item in storage_path.iterdir():
                        if item.is_dir():
                            shutil.rmtree(item)
                        else:
                            item.unlink()
                
                # Copy contents
                for item in external_path.iterdir():
                    if item.is_dir():
                        shutil.copytree(item, storage_path / item.name, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, storage_path / item.name)
            
            # Compute revision
            revision = self._compute_revision(storage_path)
            
            return True, None, None, revision
            
        except IOError as e:
            return False, SyncErrorCode.SYNC_IO_ERROR.value, f"IO error: {str(e)}", None
        except Exception as e:
            return False, SyncErrorCode.SYNC_IO_ERROR.value, f"Unexpected error: {str(e)}", None
    
    def _sync_git_push(self, project_id: str, source_key: str, source_config: dict,
                      storage_path: Path) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        """
        Sync Git: Push from Project Storage to Git repository.
        
 (repo/):
 - (playbooks, roles, inventories, vars) 
 - (repo/playbooks, repo/roles ..) Git
 - repoLayout , 
 - subdir , Git (, subdir/test-inv/ inventories)
 - subdir , 
        
 (legacy):
 - 
        
        Returns:
            (success, error_code, error_message, revision)
        """
        try:
            # Stacks (Cloud Provisioning / OpenTofu) uses a generic mirror sync,
            # not the Ansible entity-aware sync. Delegate early.
            if source_key == 'stacks':
                return self._sync_stacks_git_push(project_id, source_config, storage_path)
            git_config = source_config.get('git', {})
            repo_url = git_config.get('repo', '').strip()
            ref = git_config.get('ref', 'main').strip()
            subdir = git_config.get('subdir', '').strip()
            auth_secret_id = git_config.get('authSecretId')
            
            if not repo_url:
                return False, SyncErrorCode.SYNC_GIT_REF_NOT_FOUND.value, "Repository URL not configured", None
            
            if not storage_path.exists():
                return False, SyncErrorCode.SYNC_STORAGE_NOT_FOUND.value, f"Project Storage not found: {storage_path}", None
            
            # (repo/): , 
            is_repo_source = (source_key == 'repo')
            project_dir = self.projects_dir / project_id
            repo_dir = project_dir / 'repo'
            
            # repoLayout , 
            repo_layout = self._get_repo_layout(project_id)
            default_entities = ['playbooks', 'roles', 'inventories', 'vars']
            has_custom_layout = any(
                repo_layout.get(entity) != entity 
                for entity in default_entities
            )
            
            if is_repo_source and repo_dir.exists():
                # : repo/
                # subdir Git, 
                source_subdir = repo_dir
            else:
                # : storage_path 
                source_subdir = storage_path
                effective_subdir = subdir
            
            # repo source - 
            # subdir Git (, subdir/test-inv/ inventories)
            if is_repo_source:
                # Git (subdir, , )
                try:
                    git_base = self.git_source_manager.resolve_path(
                        project_id=project_id,
                        source_key=source_key,
                        repo_url=repo_url,
                        ref=ref,
                        subdir=subdir if subdir else '',  # subdir , 
                        auth_secret_id=auth_secret_id,
                        force_refresh=False
                    )
                except GitSourceError as e:
                    if hasattr(e, 'error_code') and e.error_code == 'SECRET_NOT_FOUND' and auth_secret_id:
                        try:
                            git_base = self.git_source_manager.resolve_path(
                                project_id=project_id,
                                source_key=source_key,
                                repo_url=repo_url,
                                ref=ref,
                                subdir=subdir if subdir else '',
                                auth_secret_id=None,
                                force_refresh=False
                            )
                        except Exception:
                            return False, SyncErrorCode.SYNC_GIT_AUTH_FAILED.value, f"Authentication secret not found and repository requires authentication: {str(e)}", None
                    else:
                        raise
                
                # git_work_dir ( git )
                # resolve_path subdir='' 
                logger.info(f"[PUSH] Resolving git repository path: repo_url={repo_url}, ref={ref}, subdir='' (root)")
                git_root = self.git_source_manager.resolve_path(
                    project_id=project_id,
                    source_key=source_key,
                    repo_url=repo_url,
                    ref=ref,
                    subdir='',
                    auth_secret_id=auth_secret_id,
                    force_refresh=False
                )
                logger.info(f"[PUSH] Resolved git_root: {git_root}")
                
                # git_root - 
                # git 
                # , git 
                git_work_dir = git_root
                if not (git_root / '.git').exists():
                    # .git git_root, git_root subdir
                    # .git 
                    logger.warning(f"[PUSH] .git not found in {git_root}, searching parent directories")
                    current = git_root
                    while current != current.parent:
                        if (current / '.git').exists():
                            git_work_dir = current
                            logger.info(f"[PUSH] Found .git in parent directory: {git_work_dir}")
                            break
                        current = current.parent
                    else:
                        # .git, git_root 
                        git_work_dir = git_root
                        logger.warning(f"[PUSH] .git not found in any parent, using git_root as work dir: {git_work_dir}")
                else:
                    logger.info(f"[PUSH] .git found in git_root: {git_root}")
                
                # , git_work_dir 
                logger.info(f"[PUSH] Git work directory: {git_work_dir}")
                if (git_work_dir / '.git').exists():
                    # remote URL
                    try:
                        remote_check = subprocess.run(['git', 'remote', 'get-url', 'origin'], cwd=str(git_work_dir), capture_output=True, text=True)
                        if remote_check.returncode == 0:
                            actual_repo_url = remote_check.stdout.strip()
                            logger.info(f"[PUSH] Actual repository URL in .git: {actual_repo_url}")
                            if actual_repo_url != repo_url:
                                logger.warning(f"[PUSH] Repository URL mismatch! Expected: {repo_url}, Actual: {actual_repo_url}")
                        else:
                            logger.warning(f"[PUSH] Could not get remote URL: {remote_check.stderr}")
                    except Exception as e:
                        logger.warning(f"[PUSH] Error checking remote URL: {e}")
                else:
                    logger.error(f"[PUSH] .git directory not found in git_work_dir: {git_work_dir}")
                
                logger.info(f"[PUSH] Using git_base: {git_base} (subdir: {subdir})")
                logger.info(f"[PUSH] Git root: {git_root}, Git work dir: {git_work_dir}")
                logger.info(f"[PUSH] Repository URL: {repo_url}, Branch: {ref}, Subdirectory: {subdir if subdir else 'root'}")
                logger.info(f"[PUSH] RepoLayout paths: {repo_layout}")
                logger.info(f"[PUSH] Will push folders: roles, playbooks, inventories, vars")
                
                # push Git
                # subdir, git_root / subdir
                # subdir , git_root ( )
                git_push_base = git_root / subdir if subdir else git_root
                logger.info(f"[PUSH] Git push base path: {git_push_base}")
                
                # (roles, playbooks, inventories, vars)
                logger.info(f"[PUSH] Starting to copy entities to Git repository")
                for entity_type in default_entities:
                    # repoLayout ( Ansible Entity Paths) 
                    git_entity_path = repo_layout.get(entity_type, entity_type)
                    local_entity_path = entity_type  # (repo/roles, repo/playbooks ..)
                    
                    # ( repo/)
                    local_source_path = repo_dir / local_entity_path
                    
                    # Git: git_push_base / git_entity_path
                    # subdir : git_root / subdir / git_entity_path
                    # subdir : git_root / git_entity_path
                    # git_entity_path repoLayout (, "test-inv" inventories)
                    git_target_path = git_push_base / git_entity_path
                    
                    logger.info(f"[PUSH] Entity: {entity_type}")
                    logger.info(f"[PUSH]   Local source: {local_source_path}")
                    logger.info(f"[PUSH]   Git target: {git_target_path} (base: {git_push_base}, entity_path: {git_entity_path})")
                    
                    # Git
                    if local_source_path.exists():
                        # Git 
                        if git_target_path.exists():
                            logger.info(f"[PUSH]   Removing existing Git path: {git_target_path}")
                            if git_target_path.is_dir():
                                shutil.rmtree(git_target_path)
                            else:
                                git_target_path.unlink()
                        
                        # Git
                        if local_source_path.is_dir():
                            git_target_path.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copytree(local_source_path, git_target_path, dirs_exist_ok=True)
                            logger.info(f"[PUSH]   ✓ Copied {entity_type} directory from '{local_source_path}' to Git path '{git_target_path}'")
                        else:
                            git_target_path.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(local_source_path, git_target_path)
                            logger.info(f"[PUSH]   ✓ Copied {entity_type} file from '{local_source_path}' to Git path '{git_target_path}'")
                    else:
                        logger.warning(f"[PUSH]   ✗ Local path '{local_source_path}' for {entity_type} does not exist. Skipping.")
                
                # repo/ (ansible.cfg ..) Git
                # subdir, git_root / subdir, git_root
                logger.info(f"[PUSH] Copying other files from repo/ root to Git (base: {git_push_base})")
                for item in repo_dir.iterdir():
                    if item.name == '.git':
                        continue
                    # Never push the templates folder to the Git server —
                    # template instance configs are managed in Project Storage only.
                    if item.name == 'templates':
                        logger.info(f"[PUSH] Skipping 'templates' directory (excluded from git sync by policy)")
                        continue
                    # , (roles, playbooks, inventories, vars)
                    if item.name in default_entities:
                        logger.debug(f"[PUSH] Skipping {item.name} (already processed as entity)")
                        continue
                    
                    # repo/ git_push_base ( subdir)
                    git_item_path = git_push_base / item.name
                    logger.info(f"[PUSH] Copying file/dir '{item.name}' from repo/ to Git path '{git_item_path}'")
                    if item.is_dir():
                        if git_item_path.exists():
                            shutil.rmtree(git_item_path)
                        shutil.copytree(item, git_item_path, dirs_exist_ok=True)
                        logger.info(f"[PUSH] Copied directory {item.name} to Git root")
                    else:
                        git_item_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(item, git_item_path)
                        logger.info(f"[PUSH] Copied file {item.name} to Git root")
                
                logger.info(f"[PUSH] Finished copying files (roles, playbooks, inventories, vars) to Git repository")
                logger.info(f"[PUSH] Summary:")
                logger.info(f"[PUSH]   - Repository: {repo_url}")
                logger.info(f"[PUSH]   - Branch: {ref}")
                logger.info(f"[PUSH]   - Subdirectory: {subdir if subdir else 'root'}")
                logger.info(f"[PUSH]   - Git push base: {git_push_base}")
                logger.info(f"[PUSH]   - RepoLayout paths: {repo_layout}")
                logger.info(f"[PUSH] Starting git add/commit/push sequence")
                
                # Commit and push
                original_cwd = os.getcwd()
                try:
                    os.chdir(str(git_work_dir))
                    logger.info(f"[PUSH] Starting git push for project {project_id}, source {source_key}, branch {ref}")
                    logger.info(f"[PUSH] Working directory: {git_work_dir}")
                    
                    # Force git user identity for commit (overwrite legacy values)
                    for cfg_key, cfg_val in [('user.email', 'git@opensible-local'), ('user.name', 'OpenSible Sync')]:
                        subprocess.run(['git', 'config', cfg_key, cfg_val], cwd=str(git_work_dir), capture_output=True, check=True)
                        logger.info(f"[PUSH] Set git config {cfg_key} = {cfg_val}")
                    
                    # Check current branch
                    logger.info(f"[PUSH] Running: git rev-parse --abbrev-ref HEAD")
                    current_branch_result = subprocess.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], capture_output=True, text=True)
                    current_branch = current_branch_result.stdout.strip() if current_branch_result.returncode == 0 else None
                    logger.info(f"[PUSH] Current branch: {current_branch}")
                    if current_branch_result.stderr:
                        logger.info(f"[PUSH] git rev-parse stderr: {current_branch_result.stderr}")
                    
                    # Check if target branch exists
                    logger.info(f"[PUSH] Running: git rev-parse --verify {ref}")
                    branch_check = subprocess.run(['git', 'rev-parse', '--verify', ref], capture_output=True, text=True)
                    logger.info(f"[PUSH] Branch check result: returncode={branch_check.returncode}, stdout={branch_check.stdout.strip()}, stderr={branch_check.stderr.strip() if branch_check.stderr else 'none'}")
                    if branch_check.returncode != 0:
                        # Branch doesn't exist, create it
                        logger.info(f"[PUSH] Branch {ref} doesn't exist, creating it")
                        logger.info(f"[PUSH] Running: git checkout -b {ref}")
                        checkout_result = subprocess.run(['git', 'checkout', '-b', ref], capture_output=True, text=True)
                        logger.info(f"[PUSH] Checkout result: returncode={checkout_result.returncode}, stdout={checkout_result.stdout.strip()}, stderr={checkout_result.stderr.strip() if checkout_result.stderr else 'none'}")
                        if checkout_result.returncode != 0:
                            logger.error(f"[PUSH] Failed to create branch {ref}: {checkout_result.stderr}")
                    elif current_branch != ref:
                        # Switch to target branch
                        logger.info(f"[PUSH] Switching to branch {ref}")
                        logger.info(f"[PUSH] Running: git checkout {ref}")
                        checkout_result = subprocess.run(['git', 'checkout', ref], capture_output=True, text=True)
                        logger.info(f"[PUSH] Checkout result: returncode={checkout_result.returncode}, stdout={checkout_result.stdout.strip()}, stderr={checkout_result.stderr.strip() if checkout_result.stderr else 'none'}")
                        if checkout_result.returncode != 0:
                            logger.error(f"[PUSH] Failed to checkout branch {ref}: {checkout_result.stderr}")
                    
                    # Check if there are any commits
                    logger.info(f"[PUSH] Running: git rev-list --count HEAD")
                    commit_check = subprocess.run(['git', 'rev-list', '--count', 'HEAD'], capture_output=True, text=True)
                    has_commits = commit_check.returncode == 0 and int(commit_check.stdout.strip()) > 0
                    logger.info(f"[PUSH] Repository has {commit_check.stdout.strip() if has_commits else '0'} commits")
                    if commit_check.stderr:
                        logger.info(f"[PUSH] git rev-list stderr: {commit_check.stderr}")
                    
                    # Add all changes ( roles, playbooks, inventories)
                    logger.info(f"[PUSH] Running: git add -A (adding all files including roles, playbooks, inventories)")
                    add_result = subprocess.run(['git', 'add', '-A'], capture_output=True, text=True)
                    logger.info(f"[PUSH] git add result: returncode={add_result.returncode}, stdout={add_result.stdout.strip() if add_result.stdout else 'none'}, stderr={add_result.stderr.strip() if add_result.stderr else 'none'}")
                    if add_result.returncode != 0:
                        logger.error(f"[PUSH] git add failed: {add_result.stderr}")
                        raise subprocess.CalledProcessError(add_result.returncode, ['git', 'add', '-A'], add_result.stdout, add_result.stderr)
                    
                    # , 
                    logger.info(f"[PUSH] Running: git status --porcelain")
                    status_result = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True)
                    has_changes = bool(status_result.stdout.strip())
                    logger.info(f"[PUSH] Has changes to commit: {has_changes}")
                    if status_result.stdout.strip():
                        logger.info(f"[PUSH] Files to commit:\n{status_result.stdout.strip()}")
                    else:
                        logger.info(f"[PUSH] No changes detected after git add")
                    if status_result.stderr:
                        logger.info(f"[PUSH] git status stderr: {status_result.stderr}")
                    
                    # Commit only if there are changes
                    if has_changes:
                        commit_msg = f"Sync {source_key} from Project Storage"
                        logger.info(f"[PUSH] Running: git commit -m '{commit_msg}'")
                        commit_result = subprocess.run(['git', 'commit', '-m', commit_msg], capture_output=True, text=True)
                        logger.info(f"[PUSH] git commit result: returncode={commit_result.returncode}, stdout={commit_result.stdout.strip() if commit_result.stdout else 'none'}, stderr={commit_result.stderr.strip() if commit_result.stderr else 'none'}")
                        if commit_result.returncode != 0:
                            logger.error(f"[PUSH] git commit failed: {commit_result.stderr}")
                            raise subprocess.CalledProcessError(commit_result.returncode, ['git', 'commit', '-m', commit_msg], commit_result.stdout, commit_result.stderr)
                        has_commits = True
                    else:
                        logger.info(f"[PUSH] No changes to commit")
                    
                    # , 
                    if not has_commits:
                        logger.info(f"[PUSH] No commits found, checking if files exist for initial commit")
                        # , ( untracked)
                        status_result = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True)
                        ls_result = subprocess.run(['git', 'ls-files'], capture_output=True, text=True)
                        has_files = bool(status_result.stdout.strip()) or bool(ls_result.stdout.strip())
                        
                        if not has_files:
                            logger.error(f"[PUSH] Repository is empty, nothing to push")
                            return False, SyncErrorCode.SYNC_IO_ERROR.value, f"Repository is empty. Add files to repository before pushing.", None
                        
                        logger.info(f"[PUSH] Adding all files for initial commit")
                        logger.info(f"[PUSH] Running: git add -A")
                        add_result = subprocess.run(['git', 'add', '-A'], capture_output=True, text=True)
                        logger.info(f"[PUSH] git add result: returncode={add_result.returncode}, stdout={add_result.stdout.strip() if add_result.stdout else 'none'}, stderr={add_result.stderr.strip() if add_result.stderr else 'none'}")
                        if add_result.returncode != 0:
                            logger.error(f"[PUSH] git add failed: {add_result.stderr}")
                            raise subprocess.CalledProcessError(add_result.returncode, ['git', 'add', '-A'], add_result.stdout, add_result.stderr)
                        
                        commit_msg = f"Initial commit: Sync {source_key} from Project Storage"
                        logger.info(f"[PUSH] Running: git commit -m '{commit_msg}' (initial commit)")
                        commit_result = subprocess.run(['git', 'commit', '-m', commit_msg], capture_output=True, text=True)
                        logger.info(f"[PUSH] git commit result: returncode={commit_result.returncode}, stdout={commit_result.stdout.strip() if commit_result.stdout else 'none'}, stderr={commit_result.stderr.strip() if commit_result.stderr else 'none'}")
                        if commit_result.returncode != 0:
                            logger.error(f"[PUSH] git commit failed: {commit_result.stderr}")
                            raise subprocess.CalledProcessError(commit_result.returncode, ['git', 'commit', '-m', commit_msg], commit_result.stdout, commit_result.stderr)
                        has_commits = True
                    
                    # Push (if auth configured)
                    if not auth_secret_id:
                        logger.error(f"[PUSH] No auth_secret_id configured, cannot push")
                        return False, SyncErrorCode.SYNC_GIT_AUTH_FAILED.value, "Git authentication not configured. Please configure authSecretId in source settings.", None
                    
                    try:
                        git_env = self.git_source_manager._get_auth_env(project_id, auth_secret_id, repo_url)
                    except GitSourceError as e:
                        return False, SyncErrorCode.SYNC_GIT_AUTH_FAILED.value, str(e), None
                    if not git_env.get('GIT_SSH_COMMAND'):
                        git_env['GIT_SSH_COMMAND'] = 'ssh -o StrictHostKeyChecking=accept-new'
                        logger.info(f"[PUSH] Set GIT_SSH_COMMAND for host key bypass")
                    
                    # , push
                    if not has_commits:
                        logger.error(f"[PUSH] Cannot push: branch {ref} has no commits")
                        return False, SyncErrorCode.SYNC_IO_ERROR.value, f"Cannot push: branch {ref} has no commits. Make at least one commit first.", None
                    
                    # , - push ( )
                    # , origin
                    logger.info(f"[PUSH] Running: git rev-list --count HEAD...origin/{ref}")
                    ahead_check = subprocess.run(['git', 'rev-list', '--count', f'HEAD...origin/{ref}'], capture_output=True, text=True)
                    commits_ahead = ahead_check.returncode == 0 and int(ahead_check.stdout.strip()) > 0
                    logger.info(f"[PUSH] Commits ahead of origin/{ref}: {ahead_check.stdout.strip() if commits_ahead else '0'}")
                    if ahead_check.stderr:
                        logger.info(f"[PUSH] git rev-list stderr: {ahead_check.stderr}")
                    
                    # Push ( remote)
                    # --set-upstream push, 
                    logger.info(f"[PUSH] Running: git rev-parse --abbrev-ref --symbolic-full-name @{{u}}")
                    tracking_check = subprocess.run(['git', 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}'], capture_output=True, text=True)
                    has_tracking = tracking_check.returncode == 0
                    logger.info(f"[PUSH] Branch tracking check: returncode={tracking_check.returncode}, has_tracking={has_tracking}")
                    if tracking_check.stderr:
                        logger.info(f"[PUSH] git rev-parse tracking stderr: {tracking_check.stderr}")
                    
                    if not has_tracking:
                        logger.info(f"[PUSH] Branch doesn't track remote, using --set-upstream")
                        logger.info(f"[PUSH] Running: git push --set-upstream origin {ref}")
                        push_result = subprocess.run(['git', 'push', '--set-upstream', 'origin', ref], capture_output=True, text=True, env=git_env)
                        logger.info(f"[PUSH] git push result: returncode={push_result.returncode}, stdout={push_result.stdout.strip() if push_result.stdout else 'none'}, stderr={push_result.stderr.strip() if push_result.stderr else 'none'}")
                        if push_result.returncode != 0:
                            logger.error(f"[PUSH] git push failed: {push_result.stderr}")
                            raise subprocess.CalledProcessError(push_result.returncode, ['git', 'push', '--set-upstream', 'origin', ref], push_result.stdout, push_result.stderr)
                    else:
                        logger.info(f"[PUSH] Running: git push origin {ref}")
                        push_result = subprocess.run(['git', 'push', 'origin', ref], capture_output=True, text=True, env=git_env)
                        logger.info(f"[PUSH] git push result: returncode={push_result.returncode}, stdout={push_result.stdout.strip() if push_result.stdout else 'none'}, stderr={push_result.stderr.strip() if push_result.stderr else 'none'}")
                        if push_result.returncode != 0:
                            logger.error(f"[PUSH] git push failed: {push_result.stderr}")
                            raise subprocess.CalledProcessError(push_result.returncode, ['git', 'push', 'origin', ref], push_result.stdout, push_result.stderr)
                    
                    # Compute revision
                    logger.info(f"[PUSH] Running: git rev-parse HEAD")
                    result = subprocess.run(['git', 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True)
                    revision = result.stdout.strip()
                    logger.info(f"[PUSH] Push successful, revision: {revision}")
                    
                    return True, None, None, revision
                except subprocess.CalledProcessError as e:
                    error_msg = e.stderr.decode('utf-8') if isinstance(e.stderr, bytes) else str(e.stderr) if e.stderr else str(e)
                    stdout_msg = e.stdout.decode('utf-8') if isinstance(e.stdout, bytes) else str(e.stdout) if e.stdout else ""
                    logger.error(f"[PUSH] Git command failed: {' '.join(e.cmd)}")
                    logger.error(f"[PUSH] Exit code: {e.returncode}")
                    logger.error(f"[PUSH] stdout: {stdout_msg}")
                    logger.error(f"[PUSH] stderr: {error_msg}")
                    raise
                finally:
                    os.chdir(original_cwd)
            
            # ( , repo source)
            # Resolve git path (working copy)
            resolved = self.git_source_manager.resolve_path(
                project_id=project_id,
                source_key=source_key,
                repo_url=repo_url,
                ref=ref,
                subdir=subdir,  # subdir 
                auth_secret_id=auth_secret_id,
                force_refresh=False  # Use existing cache
            )
            
            git_work_dir = resolved.parent  # Git cache directory
            git_target = resolved  # resolve_path returns Path directly
            
            # Copy from Project Storage to git working copy
            if source_subdir.is_file():
                # For files
                git_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_subdir, git_target)
            else:
                # For directories
                if effective_subdir:
                    # Clear subdir in git repo
                    if git_target.exists() and git_target.is_dir():
                        for item in git_target.iterdir():
                            if item.name == '.git':
                                continue
                            if item.is_dir():
                                shutil.rmtree(item)
                            else:
                                item.unlink()
                    
                    # Copy contents from source_subdir to git_target
                    for item in source_subdir.iterdir():
                        if item.name == 'templates':
                            logger.info(f"[PUSH] Skipping 'templates' directory (excluded from git sync by policy)")
                            continue
                        if item.is_dir():
                            shutil.copytree(item, git_target / item.name, dirs_exist_ok=True)
                        else:
                            shutil.copy2(item, git_target / item.name)
                else:
                    # Root of repo - copy into repo root (but preserve .git)
                    for item in source_subdir.iterdir():
                        if item.name == '.git':
                            continue
                        if item.name == 'templates':
                            logger.info(f"[PUSH] Skipping 'templates' directory (excluded from git sync by policy)")
                            continue
                        if item.is_dir():
                            shutil.copytree(item, git_work_dir / item.name, dirs_exist_ok=True)
                        else:
                            shutil.copy2(item, git_work_dir / item.name)
            
            # Commit and push (using git commands)
            # Change to git work dir
            original_cwd = os.getcwd()
            try:
                os.chdir(str(git_work_dir))
                logger.info(f"[PUSH] Starting git push for project {project_id}, source {source_key}, branch {ref} (legacy format)")
                logger.info(f"[PUSH] Working directory: {git_work_dir}")
                
                # Force git user identity for commit (overwrite legacy values)
                for cfg_key, cfg_val in [('user.email', 'git@opensible-local'), ('user.name', 'OpenSible Sync')]:
                    subprocess.run(['git', 'config', cfg_key, cfg_val], capture_output=True, check=True)
                    logger.info(f"[PUSH] Set git config {cfg_key} = {cfg_val} (legacy format)")
                
                # Check current branch
                logger.info(f"[PUSH] Running: git rev-parse --abbrev-ref HEAD (legacy format)")
                current_branch_result = subprocess.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], capture_output=True, text=True)
                current_branch = current_branch_result.stdout.strip() if current_branch_result.returncode == 0 else None
                logger.info(f"[PUSH] Current branch: {current_branch} (legacy format)")
                if current_branch_result.stderr:
                    logger.info(f"[PUSH] git rev-parse stderr: {current_branch_result.stderr} (legacy format)")
                
                # Check if target branch exists
                logger.info(f"[PUSH] Running: git rev-parse --verify {ref} (legacy format)")
                branch_check = subprocess.run(['git', 'rev-parse', '--verify', ref], capture_output=True, text=True)
                logger.info(f"[PUSH] Branch check result: returncode={branch_check.returncode}, stdout={branch_check.stdout.strip()}, stderr={branch_check.stderr.strip() if branch_check.stderr else 'none'} (legacy format)")
                if branch_check.returncode != 0:
                    # Branch doesn't exist, create it
                    logger.info(f"[PUSH] Branch {ref} doesn't exist, creating it (legacy format)")
                    logger.info(f"[PUSH] Running: git checkout -b {ref} (legacy format)")
                    checkout_result = subprocess.run(['git', 'checkout', '-b', ref], capture_output=True, text=True)
                    logger.info(f"[PUSH] Checkout result: returncode={checkout_result.returncode}, stdout={checkout_result.stdout.strip()}, stderr={checkout_result.stderr.strip() if checkout_result.stderr else 'none'} (legacy format)")
                    if checkout_result.returncode != 0:
                        logger.error(f"[PUSH] Failed to create branch {ref}: {checkout_result.stderr} (legacy format)")
                elif current_branch != ref:
                    # Switch to target branch
                    logger.info(f"[PUSH] Switching to branch {ref} (legacy format)")
                    logger.info(f"[PUSH] Running: git checkout {ref} (legacy format)")
                    checkout_result = subprocess.run(['git', 'checkout', ref], capture_output=True, text=True)
                    logger.info(f"[PUSH] Checkout result: returncode={checkout_result.returncode}, stdout={checkout_result.stdout.strip()}, stderr={checkout_result.stderr.strip() if checkout_result.stderr else 'none'} (legacy format)")
                    if checkout_result.returncode != 0:
                        logger.error(f"[PUSH] Failed to checkout branch {ref}: {checkout_result.stderr} (legacy format)")
                
                # Check if there are any commits
                logger.info(f"[PUSH] Running: git rev-list --count HEAD (legacy format)")
                commit_check = subprocess.run(['git', 'rev-list', '--count', 'HEAD'], capture_output=True, text=True)
                has_commits = commit_check.returncode == 0 and int(commit_check.stdout.strip()) > 0
                logger.info(f"[PUSH] Repository has {commit_check.stdout.strip() if has_commits else '0'} commits (legacy format)")
                if commit_check.stderr:
                    logger.info(f"[PUSH] git rev-list stderr: {commit_check.stderr} (legacy format)")
                
                # Add all changes
                logger.info(f"[PUSH] Running: git add -A (legacy format)")
                add_result = subprocess.run(['git', 'add', '-A'], capture_output=True, text=True)
                logger.info(f"[PUSH] git add result: returncode={add_result.returncode}, stdout={add_result.stdout.strip() if add_result.stdout else 'none'}, stderr={add_result.stderr.strip() if add_result.stderr else 'none'} (legacy format)")
                if add_result.returncode != 0:
                    logger.error(f"[PUSH] git add failed: {add_result.stderr} (legacy format)")
                    raise subprocess.CalledProcessError(add_result.returncode, ['git', 'add', '-A'], add_result.stdout, add_result.stderr)
                
                # Check if there are changes to commit
                logger.info(f"[PUSH] Running: git status --porcelain (legacy format)")
                status_result = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True)
                has_changes = bool(status_result.stdout.strip())
                logger.info(f"[PUSH] Has changes to commit: {has_changes} (legacy format)")
                if status_result.stdout.strip():
                    logger.info(f"[PUSH] Files to commit:\n{status_result.stdout.strip()} (legacy format)")
                if status_result.stderr:
                    logger.info(f"[PUSH] git status stderr: {status_result.stderr} (legacy format)")
                
                # Commit only if there are changes
                if has_changes:
                    commit_msg = f"Sync {source_key} from Project Storage"
                    logger.info(f"[PUSH] Running: git commit -m '{commit_msg}' (legacy format)")
                    commit_result = subprocess.run(['git', 'commit', '-m', commit_msg], capture_output=True, text=True)
                    logger.info(f"[PUSH] git commit result: returncode={commit_result.returncode}, stdout={commit_result.stdout.strip() if commit_result.stdout else 'none'}, stderr={commit_result.stderr.strip() if commit_result.stderr else 'none'} (legacy format)")
                    if commit_result.returncode != 0:
                        logger.error(f"[PUSH] git commit failed: {commit_result.stderr} (legacy format)")
                        raise subprocess.CalledProcessError(commit_result.returncode, ['git', 'commit', '-m', commit_msg], commit_result.stdout, commit_result.stderr)
                    has_commits = True
                else:
                    logger.info(f"[PUSH] No changes to commit (legacy format)")
                
                # , 
                if not has_commits:
                    logger.info(f"[PUSH] No commits found, checking if files exist for initial commit (legacy format)")
                    # , ( untracked)
                    status_result = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True)
                    ls_result = subprocess.run(['git', 'ls-files'], capture_output=True, text=True)
                    has_files = bool(status_result.stdout.strip()) or bool(ls_result.stdout.strip())
                    
                    if not has_files:
                        logger.error(f"[PUSH] Repository is empty, nothing to push (legacy format)")
                        return False, SyncErrorCode.SYNC_IO_ERROR.value, f"Repository is empty. Add files to repository before pushing.", None
                    
                    logger.info(f"[PUSH] Adding all files for initial commit (legacy format)")
                    logger.info(f"[PUSH] Running: git add -A (legacy format)")
                    add_result = subprocess.run(['git', 'add', '-A'], capture_output=True, text=True)
                    logger.info(f"[PUSH] git add result: returncode={add_result.returncode}, stdout={add_result.stdout.strip() if add_result.stdout else 'none'}, stderr={add_result.stderr.strip() if add_result.stderr else 'none'} (legacy format)")
                    if add_result.returncode != 0:
                        logger.error(f"[PUSH] git add failed: {add_result.stderr} (legacy format)")
                        raise subprocess.CalledProcessError(add_result.returncode, ['git', 'add', '-A'], add_result.stdout, add_result.stderr)
                    
                    commit_msg = f"Initial commit: Sync {source_key} from Project Storage"
                    logger.info(f"[PUSH] Running: git commit -m '{commit_msg}' (initial commit, legacy format)")
                    commit_result = subprocess.run(['git', 'commit', '-m', commit_msg], capture_output=True, text=True)
                    logger.info(f"[PUSH] git commit result: returncode={commit_result.returncode}, stdout={commit_result.stdout.strip() if commit_result.stdout else 'none'}, stderr={commit_result.stderr.strip() if commit_result.stderr else 'none'} (legacy format)")
                    if commit_result.returncode != 0:
                        logger.error(f"[PUSH] git commit failed: {commit_result.stderr} (legacy format)")
                        raise subprocess.CalledProcessError(commit_result.returncode, ['git', 'commit', '-m', commit_msg], commit_result.stdout, commit_result.stderr)
                    has_commits = True
                
                # Push (if auth configured)
                if not auth_secret_id:
                    logger.error(f"[PUSH] No auth_secret_id configured, cannot push (legacy format)")
                    return False, SyncErrorCode.SYNC_GIT_AUTH_FAILED.value, "Git authentication not configured. Please configure authSecretId in source settings.", None
                
                try:
                    git_env = self.git_source_manager._get_auth_env(project_id, auth_secret_id, repo_url)
                except GitSourceError as e:
                    return False, SyncErrorCode.SYNC_GIT_AUTH_FAILED.value, str(e), None
                if not git_env.get('GIT_SSH_COMMAND'):
                    git_env['GIT_SSH_COMMAND'] = 'ssh -o StrictHostKeyChecking=accept-new'
                    logger.info(f"[PUSH] Set GIT_SSH_COMMAND for host key bypass (legacy format)")
                
                # , push
                if not has_commits:
                    logger.error(f"[PUSH] Cannot push: branch {ref} has no commits (legacy format)")
                    return False, SyncErrorCode.SYNC_IO_ERROR.value, f"Cannot push: branch {ref} has no commits. Make at least one commit first.", None
                
                # , - push ( )
                logger.info(f"[PUSH] Running: git rev-list --count HEAD...origin/{ref} (legacy format)")
                ahead_check = subprocess.run(['git', 'rev-list', '--count', f'HEAD...origin/{ref}'], capture_output=True, text=True)
                commits_ahead = ahead_check.returncode == 0 and int(ahead_check.stdout.strip()) > 0
                logger.info(f"[PUSH] Commits ahead of origin/{ref}: {ahead_check.stdout.strip() if commits_ahead else '0'} (legacy format)")
                if ahead_check.stderr:
                    logger.info(f"[PUSH] git rev-list stderr: {ahead_check.stderr} (legacy format)")
                
                # Push ( remote)
                # --set-upstream push, 
                logger.info(f"[PUSH] Running: git rev-parse --abbrev-ref --symbolic-full-name @{{u}} (legacy format)")
                tracking_check = subprocess.run(['git', 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}'], capture_output=True, text=True)
                has_tracking = tracking_check.returncode == 0
                logger.info(f"[PUSH] Branch tracking check: returncode={tracking_check.returncode}, has_tracking={has_tracking} (legacy format)")
                if tracking_check.stderr:
                    logger.info(f"[PUSH] git rev-parse tracking stderr: {tracking_check.stderr} (legacy format)")
                
                if not has_tracking:
                    logger.info(f"[PUSH] Branch doesn't track remote, using --set-upstream (legacy format)")
                    logger.info(f"[PUSH] Running: git push --set-upstream origin {ref} (legacy format)")
                    push_result = subprocess.run(['git', 'push', '--set-upstream', 'origin', ref], capture_output=True, text=True, env=git_env)
                    logger.info(f"[PUSH] git push result: returncode={push_result.returncode}, stdout={push_result.stdout.strip() if push_result.stdout else 'none'}, stderr={push_result.stderr.strip() if push_result.stderr else 'none'} (legacy format)")
                    if push_result.returncode != 0:
                        logger.error(f"[PUSH] git push failed: {push_result.stderr} (legacy format)")
                        raise subprocess.CalledProcessError(push_result.returncode, ['git', 'push', '--set-upstream', 'origin', ref], push_result.stdout, push_result.stderr)
                else:
                    logger.info(f"[PUSH] Running: git push origin {ref} (legacy format)")
                    push_result = subprocess.run(['git', 'push', 'origin', ref], capture_output=True, text=True, env=git_env)
                    logger.info(f"[PUSH] git push result: returncode={push_result.returncode}, stdout={push_result.stdout.strip() if push_result.stdout else 'none'}, stderr={push_result.stderr.strip() if push_result.stderr else 'none'} (legacy format)")
                    if push_result.returncode != 0:
                        logger.error(f"[PUSH] git push failed: {push_result.stderr} (legacy format)")
                        raise subprocess.CalledProcessError(push_result.returncode, ['git', 'push', 'origin', ref], push_result.stdout, push_result.stderr)
                
                # Compute revision (commit hash)
                logger.info(f"[PUSH] Running: git rev-parse HEAD")
                result = subprocess.run(['git', 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True)
                revision = result.stdout.strip()
                logger.info(f"[PUSH] Push successful, revision: {revision}")
                
                return True, None, None, revision
            except subprocess.CalledProcessError as e:
                error_msg = e.stderr.decode('utf-8') if isinstance(e.stderr, bytes) else str(e.stderr) if e.stderr else str(e)
                stdout_msg = e.stdout.decode('utf-8') if isinstance(e.stdout, bytes) else str(e.stdout) if e.stdout else ""
                logger.error(f"[PUSH] Git command failed: {' '.join(e.cmd)}")
                logger.error(f"[PUSH] Exit code: {e.returncode}")
                logger.error(f"[PUSH] stdout: {stdout_msg}")
                logger.error(f"[PUSH] stderr: {error_msg}")
                raise
            finally:
                os.chdir(original_cwd)
            
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.decode('utf-8') if isinstance(e.stderr, bytes) else str(e.stderr) if e.stderr else str(e)
            stdout_msg = e.stdout.decode('utf-8') if isinstance(e.stdout, bytes) else str(e.stdout) if e.stdout else ""
            full_error = f"Command: {' '.join(e.cmd)}, Exit code: {e.returncode}"
            if stdout_msg:
                full_error += f", stdout: {stdout_msg}"
            if error_msg:
                full_error += f", stderr: {error_msg}"
            
            logger.error(f"[PUSH] Git operation failed: {full_error}")
            
            if 'authentication' in error_msg.lower() or 'auth' in error_msg.lower() or 'permission' in error_msg.lower():
                return False, SyncErrorCode.SYNC_GIT_AUTH_FAILED.value, f"Git authentication failed: {error_msg}", None
            return False, SyncErrorCode.SYNC_IO_ERROR.value, f"Git operation failed: {full_error}", None
        except GitSourceError as e:
            error_str = str(e)
            # Check error code if available
            if hasattr(e, 'error_code'):
                if e.error_code == 'SECRET_NOT_FOUND':
                    return False, SyncErrorCode.SYNC_GIT_AUTH_FAILED.value, f"Authentication secret not found: {error_str}", None
                elif e.error_code == 'SECRET_DECRYPT_FAILED':
                    return False, SyncErrorCode.SYNC_GIT_AUTH_FAILED.value, f"Failed to decrypt authentication secret: {error_str}", None
                elif e.error_code == 'GIT_AUTH_FAILED':
                    return False, SyncErrorCode.SYNC_GIT_AUTH_FAILED.value, f"Git authentication failed: {error_str}", None
            # Fallback to string matching
            if 'secret' in error_str.lower() and 'not found' in error_str.lower():
                return False, SyncErrorCode.SYNC_GIT_AUTH_FAILED.value, f"Authentication secret not found: {error_str}", None
            if 'authentication' in error_str.lower() or 'auth' in error_str.lower():
                return False, SyncErrorCode.SYNC_GIT_AUTH_FAILED.value, f"Git authentication failed: {error_str}", None
            if 'not found' in error_str.lower() and 'ref' in error_str.lower():
                return False, SyncErrorCode.SYNC_GIT_REF_NOT_FOUND.value, f"Git ref not found: {error_str}", None
            return False, SyncErrorCode.SYNC_IO_ERROR.value, f"Git source error: {error_str}", None
        except Exception as e:
            return False, SyncErrorCode.SYNC_IO_ERROR.value, f"Unexpected error: {str(e)}", None
    
    def _sync_git_pull(self, project_id: str, source_key: str, source_config: dict,
                      storage_path: Path) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        """
        Sync Git: Pull from Git repository to Project Storage.
        
 (repo/):
 - (playbooks, roles, inventories, vars) 
 - Git (repo/playbooks, repo/roles ..)
 - repoLayout , Git 
 - subdir , Git (, subdir/test-inv/ inventories)
 - subdir , 
 - Git (ansible.cfg ..) repo/
        
 (legacy):
 - 
        
        Returns:
            (success, error_code, error_message, revision)
        """
        try:
            # Stacks (Cloud Provisioning / OpenTofu) uses a generic mirror sync,
            # not the Ansible entity-aware sync. Delegate early.
            if source_key == 'stacks':
                return self._sync_stacks_git_pull(project_id, source_config, storage_path)
            git_config = source_config.get('git', {})
            repo_url = git_config.get('repo', '').strip()
            ref = git_config.get('ref', 'main').strip()
            subdir = git_config.get('subdir', '').strip()
            auth_secret_id = git_config.get('authSecretId')
            
            if not repo_url:
                return False, SyncErrorCode.SYNC_GIT_REF_NOT_FOUND.value, "Repository URL not configured", None
            
            # (repo/): , 
            is_repo_source = (source_key == 'repo')
            project_dir = self.projects_dir / project_id
            repo_dir = project_dir / 'repo'
            
            # repoLayout , 
            repo_layout = self._get_repo_layout(project_id)
            default_entities = ['playbooks', 'roles', 'inventories', 'vars']
            has_custom_layout = any(
                repo_layout.get(entity) != entity 
                for entity in default_entities
            )
            
            if is_repo_source:
                # : repo/
                # subdir Git, 
                # repo_dir 
                repo_dir.mkdir(parents=True, exist_ok=True)
                target_subdir = repo_dir
            else:
                # : storage_path 
                target_subdir = storage_path
                effective_subdir = subdir
            
            # repo source - playbooks, roles, inventories, vars
            # ( repoLayout , )
            # repoLayout - Git 
            # subdir - Git (, subdir/test-inv/ inventories)
            if is_repo_source:
                # (playbooks, roles, inventories, vars)
                # repoLayout 
                # subdir Git
                logger.info(f"[PULL] Syncing only default entities: {default_entities}")
                if subdir:
                    logger.info(f"[PULL] Using subdir '{subdir}' as base path for entity search in Git")
                
                # Git- ( subdir, )
                try:
                    git_base = self.git_source_manager.resolve_path(
                        project_id=project_id,
                        source_key=source_key,
                        repo_url=repo_url,
                        ref=ref,
                        subdir=subdir if subdir else '',  # subdir , 
                        auth_secret_id=auth_secret_id,
                        force_refresh=True
                    )
                    logger.info(f"[PULL] Git base resolved: {git_base} (subdir: {subdir})")
                except GitSourceError as e:
                    if hasattr(e, 'error_code') and e.error_code == 'SECRET_NOT_FOUND' and auth_secret_id:
                        try:
                            git_base = self.git_source_manager.resolve_path(
                                project_id=project_id,
                                source_key=source_key,
                                repo_url=repo_url,
                                ref=ref,
                                subdir=subdir if subdir else '',
                                auth_secret_id=None,
                                force_refresh=True
                            )
                        except Exception:
                            return False, SyncErrorCode.SYNC_GIT_AUTH_FAILED.value, f"Authentication secret not found and repository requires authentication: {str(e)}", None
                    else:
                        raise
                
                if not git_base.exists():
                    return False, SyncErrorCode.SYNC_EXTERNAL_NOT_FOUND.value, f"Git base path not found: {git_base}", None
                
                # repo_dir
                repo_dir.mkdir(parents=True, exist_ok=True)
                
                logger.info(f"Starting entity-by-entity sync. Git base: {git_base}")
                for entity_type in default_entities:
                    # repoLayout ( ) 
                    # git_base ( subdir, )
                    git_entity_path = repo_layout.get(entity_type, entity_type)
                    local_entity_path = entity_type
                    
                    # Git ( git_base, subdir)
                    git_source_path = git_base / git_entity_path
                    # ( repo/, subdir)
                    local_target_path = repo_dir / local_entity_path
                    
                    logger.info(f"Processing {entity_type}: Git path='{git_entity_path}' (base: {git_base.name if subdir else 'root'}) -> Local path='{local_entity_path}'")
                    logger.info(f"  Git source: {git_source_path} (exists: {git_source_path.exists()})")
                    logger.info(f"  Local target: {local_target_path}")
                    
                    # Git 
                    if git_source_path.exists():
                        if local_target_path.exists():
                            if local_target_path.is_dir():
                                shutil.rmtree(local_target_path)
                            else:
                                local_target_path.unlink()
                        
                        # Git 
                        if git_source_path.is_dir():
                            shutil.copytree(git_source_path, local_target_path, dirs_exist_ok=True)
                            logger.info(f"Synced {entity_type} from Git path '{git_entity_path}' to local '{local_entity_path}' (directory)")
                        else:
                            local_target_path.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(git_source_path, local_target_path)
                            logger.info(f"Synced {entity_type} from Git path '{git_entity_path}' to local '{local_entity_path}' (file)")
                    else:
                        logger.warning(f"Git path '{git_entity_path}' for {entity_type} does not exist in repository (base: {git_base}). Skipping.")
                        # , 
                        if not local_target_path.exists():
                            local_target_path.mkdir(parents=True, exist_ok=True)
                
                # git_base (ansible.cfg ..), 
                # : subdir , subdir 
                # git_base, 
                for item in git_base.iterdir():
                    if item.name == '.git':
                        continue
                    # , 
                    if item.name in [repo_layout.get(e, e) for e in default_entities]:
                        continue
                    # ( repoLayout)
                    if item.is_dir() and item.name in default_entities:
                        continue
                    
                    # ( ), subdir 
                    if item.is_file():
                        local_item_path = repo_dir / item.name
                        shutil.copy2(item, local_item_path)
                        logger.info(f"Copied file from Git base: {item.name}")
                
                # Compute revision ( commit hash)
                original_cwd = os.getcwd()
                try:
                    # git rev-parse
                    git_root = self.git_source_manager.resolve_path(
                        project_id=project_id,
                        source_key=source_key,
                        repo_url=repo_url,
                        ref=ref,
                        subdir='',
                        auth_secret_id=auth_secret_id,
                        force_refresh=False  # , 
                    )
                    os.chdir(str(git_root))
                    logger.info(f"[PULL] Running: git rev-parse HEAD")
                    result = subprocess.run(['git', 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True)
                    revision = result.stdout.strip()
                    logger.info(f"[PULL] Pull successful, revision: {revision}")
                    return True, None, None, revision
                except subprocess.CalledProcessError as e:
                    error_msg = e.stderr.decode('utf-8') if isinstance(e.stderr, bytes) else str(e.stderr) if e.stderr else str(e)
                    logger.error(f"[PULL] git rev-parse failed: {error_msg}")
                    raise
                finally:
                    os.chdir(original_cwd)
            
            # subdir repo source - 
            # Try to resolve git path with auth_secret_id
            # If secret not found, try without auth (for public repos)
            resolved = None
            auth_failed = False
            try:
                resolved = self.git_source_manager.resolve_path(
                    project_id=project_id,
                    source_key=source_key,
                    repo_url=repo_url,
                    ref=ref,
                    subdir=effective_subdir,
                    auth_secret_id=auth_secret_id,
                    force_refresh=True  # Fetch latest
                )
            except GitSourceError as e:
                # If secret not found and we have auth_secret_id, try without auth
                if hasattr(e, 'error_code') and e.error_code == 'SECRET_NOT_FOUND' and auth_secret_id:
                    logger.warning(f"Secret {auth_secret_id} not found for {source_key}, trying without authentication (public repo)")
                    auth_failed = True
                    # Try without auth (set auth_secret_id to None)
                    try:
                        resolved = self.git_source_manager.resolve_path(
                            project_id=project_id,
                            source_key=source_key,
                            repo_url=repo_url,
                            ref=ref,
                            subdir=effective_subdir,
                            auth_secret_id=None,  # Try without auth
                            force_refresh=True
                        )
                    except Exception as e2:
                        # If still fails, return original secret error
                        return False, SyncErrorCode.SYNC_GIT_AUTH_FAILED.value, f"Authentication secret not found and repository requires authentication: {str(e)}", None
                else:
                    # Re-raise other GitSourceError
                    raise
            
            if not resolved:
                return False, SyncErrorCode.SYNC_EXTERNAL_NOT_FOUND.value, "Failed to resolve git repository", None
            
            git_source = resolved  # resolve_path returns Path directly, not a dict
            
            if not git_source.exists():
                return False, SyncErrorCode.SYNC_EXTERNAL_NOT_FOUND.value, f"Git source not found: {git_source}", None
            
            # Ensure target path exists
            target_subdir.parent.mkdir(parents=True, exist_ok=True)
            
            # Copy from git source to Project Storage
            if git_source.is_file():
                # For files
                shutil.copy2(git_source, target_subdir)
            else:
                # For directories
                if effective_subdir:
                    # : 
                    if target_subdir.exists() and target_subdir.is_dir():
                        for item in target_subdir.iterdir():
                            if item.is_dir():
                                shutil.rmtree(item)
                            else:
                                item.unlink()
                    
                    # Copy contents from git_source to target_subdir
                    for item in git_source.iterdir():
                        if item.name == 'templates':
                            logger.info(f"[PULL] Skipping 'templates' directory (excluded from git sync by policy)")
                            continue
                        if item.is_dir():
                            shutil.copytree(item, target_subdir / item.name, dirs_exist_ok=True)
                        else:
                            shutil.copy2(item, target_subdir / item.name)
                else:
                    # repo/: repo/ 
                    if target_subdir.exists() and target_subdir.is_dir():
                        for item in target_subdir.iterdir():
                            if item.name == '.git':
                                continue
                            # Preserve local templates — they are not tracked in git.
                            if item.name == 'templates':
                                continue
                            if item.is_dir():
                                shutil.rmtree(item)
                            else:
                                item.unlink()
                    
                    # Copy contents from git_source to target_subdir
                    for item in git_source.iterdir():
                        if item.name == '.git':
                            continue
                        if item.name == 'templates':
                            logger.info(f"[PULL] Skipping 'templates' directory (excluded from git sync by policy)")
                            continue
                        if item.is_dir():
                            shutil.copytree(item, target_subdir / item.name, dirs_exist_ok=True)
                        else:
                            shutil.copy2(item, target_subdir / item.name)
            
            # Compute revision (commit hash from git)
            original_cwd = os.getcwd()
            try:
                git_work_dir = resolved.parent  # resolve_path returns Path directly
                os.chdir(str(git_work_dir))
                logger.info(f"[PULL] Running: git rev-parse HEAD (legacy format)")
                result = subprocess.run(['git', 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True)
                revision = result.stdout.strip()
                logger.info(f"[PULL] Pull successful, revision: {revision}")
                
                return True, None, None, revision
            except subprocess.CalledProcessError as e:
                error_msg = e.stderr.decode('utf-8') if isinstance(e.stderr, bytes) else str(e.stderr) if e.stderr else str(e)
                logger.error(f"[PULL] git rev-parse failed: {error_msg}")
                raise
            finally:
                os.chdir(original_cwd)
            
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.decode('utf-8') if isinstance(e.stderr, bytes) else str(e.stderr) if e.stderr else str(e)
            stdout_msg = e.stdout.decode('utf-8') if isinstance(e.stdout, bytes) else str(e.stdout) if e.stdout else ""
            full_error = f"Command: {' '.join(e.cmd)}, Exit code: {e.returncode}"
            if stdout_msg:
                full_error += f", stdout: {stdout_msg}"
            if error_msg:
                full_error += f", stderr: {error_msg}"
            
            logger.error(f"[PULL] Git operation failed: {full_error}")
            
            if 'authentication' in error_msg.lower() or 'auth' in error_msg.lower() or 'permission' in error_msg.lower():
                return False, SyncErrorCode.SYNC_GIT_AUTH_FAILED.value, f"Git authentication failed: {error_msg}", None
            if 'not found' in error_msg.lower() or 'ref' in error_msg.lower():
                return False, SyncErrorCode.SYNC_GIT_REF_NOT_FOUND.value, f"Git ref not found: {error_msg}", None
            return False, SyncErrorCode.SYNC_IO_ERROR.value, f"Git operation failed: {full_error}", None
        except GitSourceError as e:
            error_str = str(e)
            # Check error code if available
            if hasattr(e, 'error_code'):
                if e.error_code == 'SECRET_NOT_FOUND':
                    return False, SyncErrorCode.SYNC_GIT_AUTH_FAILED.value, f"Authentication secret not found: {error_str}", None
                elif e.error_code == 'SECRET_DECRYPT_FAILED':
                    return False, SyncErrorCode.SYNC_GIT_AUTH_FAILED.value, f"Failed to decrypt authentication secret: {error_str}", None
                elif e.error_code == 'GIT_AUTH_FAILED':
                    return False, SyncErrorCode.SYNC_GIT_AUTH_FAILED.value, f"Git authentication failed: {error_str}", None
            # Fallback to string matching
            if 'secret' in error_str.lower() and 'not found' in error_str.lower():
                return False, SyncErrorCode.SYNC_GIT_AUTH_FAILED.value, f"Authentication secret not found: {error_str}", None
            if 'authentication' in error_str.lower() or 'auth' in error_str.lower():
                return False, SyncErrorCode.SYNC_GIT_AUTH_FAILED.value, f"Git authentication failed: {error_str}", None
            if 'not found' in error_str.lower() and 'ref' in error_str.lower():
                return False, SyncErrorCode.SYNC_GIT_REF_NOT_FOUND.value, f"Git ref not found: {error_str}", None
            return False, SyncErrorCode.SYNC_IO_ERROR.value, f"Git source error: {error_str}", None
        except Exception as e:
            return False, SyncErrorCode.SYNC_IO_ERROR.value, f"Unexpected error: {str(e)}", None
    
    def execute_sync(self, project_id: str, source_key: str, source_config: dict,
                    direction: str = 'push') -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Execute sync operation synchronously.
        
        Args:
            project_id: Project ID
            source_key: Source key
            source_config: Source configuration dict (from project.json)
            direction: 'push', 'pull', or 'both'
        
        Returns:
            (success, error_code, error_message)
        """
        mode = source_config.get('mode', 'local')
        storage_path = self.get_project_storage_path(project_id, source_key)
        
        if direction == 'both':
            # Check for conflicts first
            # Need to resolve external path
            external_path = None
            if mode == 'local':
                local_path = source_config.get('localPath', '')
                if local_path:
                    external_path = Path(local_path)
            elif mode == 'git':
                # For conflict detection, we need the git path
                # We'll resolve it temporarily
                try:
                    git_config = source_config.get('git', {})
                    resolved = self.git_source_manager.resolve_path(
                        project_id=project_id,
                        source_key=source_key,
                        repo_url=git_config.get('repo', ''),
                        ref=git_config.get('ref', 'main'),
                        subdir=git_config.get('subdir', ''),
                        auth_secret_id=git_config.get('authSecretId'),
                        force_refresh=False
                    )
                    external_path = resolved['rootPath']
                except Exception:
                    external_path = None
            
            if external_path:
                has_conflict, conflict_msg, conflict_details = self._detect_conflict(
                    project_id, source_key, storage_path, external_path
                )
                if has_conflict:
                    self._update_sync_state(project_id, source_key, 'push', 'failed', conflict_msg)
                    self._update_sync_state(project_id, source_key, 'pull', 'failed', conflict_msg)
                    return False, SyncErrorCode.SYNC_CONFLICT.value, conflict_msg
            
            # Execute pull then push
            # Pull first
            pull_success, pull_error_code, pull_error_msg, pull_revision = self._execute_sync_direction(
                project_id, source_key, source_config, 'pull', storage_path
            )
            if not pull_success:
                return False, pull_error_code, pull_error_msg
            
            # Then push
            push_success, push_error_code, push_error_msg, push_revision = self._execute_sync_direction(
                project_id, source_key, source_config, 'push', storage_path
            )
            if not push_success:
                return False, push_error_code, push_error_msg
            
            return True, None, None
        
        else:
            # Single direction
            success, error_code, error_msg, revision = self._execute_sync_direction(
                project_id, source_key, source_config, direction, storage_path
            )
            return success, error_code, error_msg
    
    def _execute_sync_direction(self, project_id: str, source_key: str, source_config: dict,
                                direction: str, storage_path: Path) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        """Execute sync in a single direction"""
        mode = source_config.get('mode', 'local')
        
        logger.info(f"[SYNC] Executing sync direction: project={project_id}, source={source_key}, direction={direction}, mode={mode}")
        
        # Update status to running
        self._update_sync_state(project_id, source_key, direction, 'running')
        
        try:
            if mode == 'local':
                local_path = source_config.get('localPath', '')
                if not local_path:
                    error_msg = "Local path not configured"
                    self._update_sync_state(project_id, source_key, direction, 'failed', error_msg)
                    return False, SyncErrorCode.SYNC_PATH_INVALID.value, error_msg, None
                
                external_path = Path(local_path)
                
                if direction == 'push':
                    success, error_code, error_msg, revision = self._sync_local_push(
                        project_id, source_key, storage_path, external_path
                    )
                else:  # pull
                    success, error_code, error_msg, revision = self._sync_local_pull(
                        project_id, source_key, storage_path, external_path
                    )
            
            elif mode == 'git':
                if direction == 'push':
                    logger.info(f"[SYNC] Calling _sync_git_push for project={project_id}, source={source_key}")
                    success, error_code, error_msg, revision = self._sync_git_push(
                        project_id, source_key, source_config, storage_path
                    )
                    logger.info(f"[SYNC] _sync_git_push result: success={success}, error_code={error_code}, error_msg={error_msg}, revision={revision}")
                else:  # pull
                    logger.info(f"[SYNC] Calling _sync_git_pull for project={project_id}, source={source_key}")
                    success, error_code, error_msg, revision = self._sync_git_pull(
                        project_id, source_key, source_config, storage_path
                    )
                    logger.info(f"[SYNC] _sync_git_pull result: success={success}, error_code={error_code}, error_msg={error_msg}, revision={revision}")
            else:
                error_msg = f"Unknown mode: {mode}"
                self._update_sync_state(project_id, source_key, direction, 'failed', error_msg)
                return False, SyncErrorCode.SYNC_IO_ERROR.value, error_msg, None
            
            # Update state
            if success:
                self._update_sync_state(project_id, source_key, direction, 'ok', None, revision)
                
                # git (pull) inventory group_vars/host_vars
                if direction == 'pull' and (source_key == 'repo' or source_config.get('git', {}).get('subdir', '') == '' or 'inventor' in source_config.get('git', {}).get('subdir', '').lower()):
                    try:
                        # app.py ( )
                        import sys
                        app_module = sys.modules.get('app')
                        if app_module and hasattr(app_module, 'ensure_all_inventory_dirs'):
                            app_module.ensure_all_inventory_dirs(project_id)
                            logger.info(f"Ensured inventory directories after pull for project {project_id}")
                    except Exception as e:
                        logger.warning(f"Failed to ensure inventory directories after pull: {e}")
            else:
                self._update_sync_state(project_id, source_key, direction, 'failed', error_msg)
            
            return success, error_code, error_msg, revision
            
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            logger.error(f"[SYNC] Unexpected error in _execute_sync_direction: {error_msg}", exc_info=True)
            self._update_sync_state(project_id, source_key, direction, 'failed', error_msg)
            return False, SyncErrorCode.SYNC_IO_ERROR.value, error_msg, None

    # ------------------------------------------------------------------
    # Stacks (Cloud Provisioning / OpenTofu) sync — generic mirror, not
    # Ansible entity-aware. The local workspace lives at
    #   <projects_dir>/<project_id>/stacks/
    # and mirrors the Git repo (optionally narrowed by `git.subdir`).
    # ------------------------------------------------------------------
    GIT_EXCLUDE_DIRS = {'.git', '.terraform', '.terragrunt-cache'}
    GIT_EXCLUDE_FILE_SUFFIXES = ('.tfstate', '.tfstate.backup', '.tfplan')

    @classmethod
    def _is_git_excluded(cls, name: str, is_dir: bool) -> bool:
        if name in cls.GIT_EXCLUDE_DIRS:
            return True
        if not is_dir and name.endswith(cls.GIT_EXCLUDE_FILE_SUFFIXES):
            return True
        return False

    def _mirror_copy(self, src: Path, dst: Path, exclude_heavy: bool = False):
        """Mirror src directory tree into dst, excluding .git (and, when
        exclude_heavy, OpenTofu caches/state that cannot be committed)."""
        dst.mkdir(parents=True, exist_ok=True)

        def skip(name: str, is_dir: bool) -> bool:
            if name == '.git':
                return True
            return exclude_heavy and self._is_git_excluded(name, is_dir)

        # Wipe existing contents (except .git, if any — shouldn't be present in storage)
        for item in list(dst.iterdir()):
            if item.name == '.git':
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        if not src.exists():
            return

        ignore = None
        if exclude_heavy:
            def ignore(dirpath, names):  # noqa: F811
                return [
                    n for n in names
                    if self._is_git_excluded(n, (Path(dirpath) / n).is_dir())
                ]

        for item in src.iterdir():
            if skip(item.name, item.is_dir()):
                continue
            target = dst / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True, ignore=ignore)
            else:
                shutil.copy2(item, target)

    def _sync_stacks_git_pull(self, project_id: str, source_config: dict,
                              storage_path: Path) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        try:
            git_config = source_config.get('git', {})
            repo_url = git_config.get('repo', '').strip()
            ref = git_config.get('ref', 'main').strip()
            subdir = git_config.get('subdir', '').strip()
            auth_secret_id = git_config.get('authSecretId')

            if not repo_url:
                return False, SyncErrorCode.SYNC_GIT_REF_NOT_FOUND.value, "Repository URL not configured", None

            try:
                git_root = self.git_source_manager.resolve_path(
                    project_id=project_id, source_key='stacks',
                    repo_url=repo_url, ref=ref, subdir='',
                    auth_secret_id=auth_secret_id, force_refresh=True
                )
            except GitSourceError as e:
                if getattr(e, 'error_code', None) == 'SECRET_NOT_FOUND' and auth_secret_id:
                    git_root = self.git_source_manager.resolve_path(
                        project_id=project_id, source_key='stacks',
                        repo_url=repo_url, ref=ref, subdir='',
                        auth_secret_id=None, force_refresh=True
                    )
                else:
                    return False, SyncErrorCode.SYNC_GIT_AUTH_FAILED.value, str(e), None

            git_source = (git_root / subdir) if subdir else git_root
            if not git_source.exists():
                return False, SyncErrorCode.SYNC_EXTERNAL_NOT_FOUND.value, f"Git source not found: {git_source}", None

            storage_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"[STACKS PULL] Mirror {git_source} -> {storage_path}")
            self._mirror_copy(git_source, storage_path)

            original_cwd = os.getcwd()
            try:
                os.chdir(str(git_root))
                result = subprocess.run(['git', 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True)
                revision = result.stdout.strip()
                return True, None, None, revision
            finally:
                os.chdir(original_cwd)
        except subprocess.CalledProcessError as e:
            msg = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or str(e))
            return False, SyncErrorCode.SYNC_IO_ERROR.value, f"git command failed: {msg}", None
        except GitSourceError as e:
            return False, SyncErrorCode.SYNC_IO_ERROR.value, f"Git source error: {str(e)}", None
        except Exception as e:
            logger.error(f"[STACKS PULL] Unexpected: {e}", exc_info=True)
            return False, SyncErrorCode.SYNC_IO_ERROR.value, f"Unexpected error: {str(e)}", None

    def _sync_stacks_git_push(self, project_id: str, source_config: dict,
                              storage_path: Path) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        try:
            git_config = source_config.get('git', {})
            repo_url = git_config.get('repo', '').strip()
            ref = git_config.get('ref', 'main').strip()
            subdir = git_config.get('subdir', '').strip()
            auth_secret_id = git_config.get('authSecretId')

            if not repo_url:
                return False, SyncErrorCode.SYNC_GIT_REF_NOT_FOUND.value, "Repository URL not configured", None
            if not auth_secret_id:
                return False, SyncErrorCode.SYNC_GIT_AUTH_FAILED.value, "Git authentication not configured. Please configure authSecretId in source settings.", None

            storage_path.mkdir(parents=True, exist_ok=True)

            try:
                git_root = self.git_source_manager.resolve_path(
                    project_id=project_id, source_key='stacks',
                    repo_url=repo_url, ref=ref, subdir='',
                    auth_secret_id=auth_secret_id, force_refresh=False
                )
            except GitSourceError as e:
                return False, SyncErrorCode.SYNC_GIT_AUTH_FAILED.value, str(e), None

            git_target = (git_root / subdir) if subdir else git_root
            git_target.mkdir(parents=True, exist_ok=True)

            logger.info(f"[STACKS PUSH] Mirror {storage_path} -> {git_target} (excluding .terraform/state)")
            self._mirror_copy(storage_path, git_target, exclude_heavy=True)

            # Belt and braces: make sure the repo ignores provider caches/state
            try:
                gitignore = git_root / '.gitignore'
                needed = ['.terraform/', '.terragrunt-cache/', '*.tfstate', '*.tfstate.backup', '*.tfplan']
                existing = gitignore.read_text().splitlines() if gitignore.exists() else []
                missing = [p for p in needed if p not in existing]
                if missing:
                    lines = existing + ([''] if existing and existing[-1].strip() else []) + missing
                    gitignore.write_text('\n'.join(lines).strip() + '\n')
            except Exception as e:
                logger.warning(f"[STACKS PUSH] Could not update .gitignore: {e}")

            try:
                git_env = self.git_source_manager._get_auth_env(project_id, auth_secret_id, repo_url)
            except GitSourceError as e:
                return False, SyncErrorCode.SYNC_GIT_AUTH_FAILED.value, str(e), None
            git_env.setdefault('GIT_SSH_COMMAND', 'ssh -o StrictHostKeyChecking=accept-new')

            original_cwd = os.getcwd()
            try:
                os.chdir(str(git_root))
                for k, v in [('user.email', 'git@opensible-local'), ('user.name', 'OpenSible Sync')]:
                    subprocess.run(['git', 'config', k, v], check=True, capture_output=True)

                # Ensure target branch exists / checked out
                branch_check = subprocess.run(['git', 'rev-parse', '--verify', ref], capture_output=True, text=True)
                if branch_check.returncode != 0:
                    subprocess.run(['git', 'checkout', '-b', ref], capture_output=True, text=True)
                else:
                    cur = subprocess.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], capture_output=True, text=True).stdout.strip()
                    if cur != ref:
                        subprocess.run(['git', 'checkout', ref], capture_output=True, text=True)

                upstream = subprocess.run(['git', 'rev-parse', '--verify', f'origin/{ref}'], capture_output=True, text=True)
                if upstream.returncode == 0:
                    ahead = subprocess.run(['git', 'rev-list', '--count', f'origin/{ref}..HEAD'], capture_output=True, text=True)
                    if (ahead.stdout or '0').strip() not in ('', '0'):
                        subprocess.run(['git', 'reset', '--soft', f'origin/{ref}'], capture_output=True, text=True)

                subprocess.run(['git', 'add', '-A'], check=True, capture_output=True, text=True)
                status = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True)
                if status.stdout.strip():
                    commit_msg = "Sync stacks from Project Storage"
                    c = subprocess.run(['git', 'commit', '-m', commit_msg], capture_output=True, text=True)
                    if c.returncode != 0:
                        return False, SyncErrorCode.SYNC_IO_ERROR.value, f"git commit failed: {c.stderr.strip()}", None

                tracking = subprocess.run(['git', 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}'], capture_output=True, text=True)
                if tracking.returncode != 0:
                    p = subprocess.run(['git', 'push', '--set-upstream', 'origin', ref], capture_output=True, text=True, env=git_env)
                else:
                    p = subprocess.run(['git', 'push', 'origin', ref], capture_output=True, text=True, env=git_env)
                if p.returncode != 0:
                    err = (p.stderr or '').strip()
                    if 'exceed' in err and 'limit' in err:
                        err = (
                            "git push rejected: the remote refuses files over its blob size limit. "
                            "This is usually a .terraform provider cache or a large state file already present "
                            "in the remote branch history. OpenSible no longer pushes those, but existing history "
                            "must be cleaned on the remote (or Git LFS enabled).\n\n" + err
                        )
                    return False, SyncErrorCode.SYNC_IO_ERROR.value, f"git push failed: {err}", None

                rev = subprocess.run(['git', 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()
                return True, None, None, rev
            finally:
                os.chdir(original_cwd)
        except subprocess.CalledProcessError as e:
            msg = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or str(e))
            return False, SyncErrorCode.SYNC_IO_ERROR.value, f"git command failed: {msg}", None
        except Exception as e:
            logger.error(f"[STACKS PUSH] Unexpected: {e}", exc_info=True)
            return False, SyncErrorCode.SYNC_IO_ERROR.value, f"Unexpected error: {str(e)}", None


    
    def start_sync(self, project_id: str, source_key: str, source_config: dict,
                   direction: str = 'push', actor: str = 'user') -> Tuple[str, bool, Optional[str], Optional[str]]:
        """
        Start async sync operation.
        
        Args:
            project_id: Project ID
            source_key: Source key
            source_config: Source configuration dict
            direction: 'push', 'pull', or 'both'
            actor: 'user' or 'system'
        
        Returns:
            (job_id, success, error_code, error_message)
        """
        job_key = (project_id, source_key)
        
        # Check if already running
        with self._job_lock:
            if job_key in self._active_jobs:
                job = self._active_jobs[job_key]
                # Only block if job is actually running
                if job.get('status') == 'running':
                    return None, False, SyncErrorCode.SYNC_LOCKED.value, "Sync already in progress"
                # If job is completed (ok/failed), remove it and allow new sync
                else:
                    del self._active_jobs[job_key]
            
            # Create job
            job_id = f"{project_id}_{source_key}_{int(time.time() * 1000)}"
            job = {
                'jobId': job_id,
                'projectId': project_id,
                'sourceKey': source_key,
                'direction': direction,
                'actor': actor,
                'startedAt': int(time.time()),
                'status': 'running'
            }
            self._active_jobs[job_key] = job
        
        # Start async thread
        thread = threading.Thread(
            target=self._sync_worker,
            args=(job_id, project_id, source_key, source_config, direction, actor),
            daemon=True
        )
        thread.start()
        
        return job_id, True, None, None
    
    def _sync_worker(self, job_id: str, project_id: str, source_key: str, source_config: dict,
                    direction: str, actor: str):
        """Worker thread for async sync execution"""
        job_key = (project_id, source_key)
        
        logger.info(f"[SYNC] Starting sync worker: job_id={job_id}, project={project_id}, source={source_key}, direction={direction}, actor={actor}")
        
        try:
            # Execute sync
            logger.info(f"[SYNC] Executing sync: project={project_id}, source={source_key}, direction={direction}")
            success, error_code, error_msg = self.execute_sync(
                project_id, source_key, source_config, direction
            )
            logger.info(f"[SYNC] Sync completed: success={success}, error_code={error_code}, error_msg={error_msg}")
            
            # Update job status
            with self._job_lock:
                if job_key in self._active_jobs:
                    self._active_jobs[job_key]['status'] = 'ok' if success else 'failed'
                    self._active_jobs[job_key]['completedAt'] = int(time.time())
                    if error_code:
                        self._active_jobs[job_key]['errorCode'] = error_code
                    if error_msg:
                        self._active_jobs[job_key]['error'] = error_msg
            
        except Exception as e:
            # Log error
            logger.error(f"[SYNC] Sync worker error: {e}", exc_info=True)
            # Update job with error
            with self._job_lock:
                if job_key in self._active_jobs:
                    self._active_jobs[job_key]['status'] = 'failed'
                    self._active_jobs[job_key]['completedAt'] = int(time.time())
                    self._active_jobs[job_key]['error'] = str(e)
        finally:
            # Remove completed job from active_jobs after a short delay
            # This allows UI to query job status, but prevents blocking new syncs
            def cleanup_job():
                time.sleep(5)  # Keep job for 5 seconds for status queries
                with self._job_lock:
                    if job_key in self._active_jobs:
                        job = self._active_jobs[job_key]
                        # Only remove if job is completed (not running)
                        if job.get('status') != 'running':
                            del self._active_jobs[job_key]
            
            # Start cleanup thread
            cleanup_thread = threading.Thread(target=cleanup_job, daemon=True)
            cleanup_thread.start()

