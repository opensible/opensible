"""
AutosyncScheduler: Background scheduler for automatic source synchronization.

Runs periodic sync operations based on project autosync configuration.
"""

import os
import time
import threading
import logging
from typing import Dict, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class AutosyncScheduler:
    """
    Scheduler for automatic source synchronization.
    """
    
    def __init__(self, source_sync_service, load_project_config_func, get_project_dir_func):
        """
        Args:
            source_sync_service: SourceSyncService instance
            load_project_config_func: Function to load project config (project_id) -> dict
            get_project_dir_func: Function to get project directory (project_id) -> Path
        """
        self.source_sync_service = source_sync_service
        self.load_project_config = load_project_config_func
        self.get_project_dir = get_project_dir_func
        
        self._running = False
        self._scheduler_thread = None
        self._lock = threading.Lock()
        
        # Check interval (seconds). Configurable at runtime via System Settings.
        self.CHECK_INTERVAL = self._resolve_interval()

    def _resolve_interval(self) -> int:
        try:
            from utils.scheduler_settings import get_interval
            return get_interval('autosync_check_interval')
        except Exception:
            try:
                return max(10, int(os.environ.get('AUTOSYNC_CHECK_INTERVAL') or 60))
            except (TypeError, ValueError):
                return 60
    
    def start(self):
        """Start the scheduler"""
        with self._lock:
            if self._running:
                return
            
            self._running = True
            self._scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
            self._scheduler_thread.start()
            logger.info("Autosync scheduler started")
    
    def stop(self):
        """Stop the scheduler"""
        with self._lock:
            self._running = False
            if self._scheduler_thread:
                self._scheduler_thread.join(timeout=5)
            logger.info("Autosync scheduler stopped")
    
    def _scheduler_loop(self):
        """Main scheduler loop"""
        while self._running:
            try:
                self._check_and_run_autosync()
            except Exception as e:
                logger.error(f"Error in autosync scheduler loop: {e}", exc_info=True)
            
            # Sleep until next check (re-resolved so UI changes apply live)
            self.CHECK_INTERVAL = self._resolve_interval()
            time.sleep(self.CHECK_INTERVAL)
    
    def _check_and_run_autosync(self):
        """Check all projects for autosync and run if needed"""
        try:
            # Get all project directories
            projects_dir = self.get_project_dir('').parent  # Get parent of any project dir
            
            if not projects_dir.exists():
                return
            
            # Iterate through all project directories
            for project_dir in projects_dir.iterdir():
                if not project_dir.is_dir():
                    continue
                
                project_id = project_dir.name
                
                try:
                    # Load project config
                    config = self.load_project_config(project_id)
                    autosync = config.get('autosync', {})
                    
                    # Check if autosync is enabled
                    if not autosync.get('enabled', False):
                        continue
                    
                    # Check if it's time to run
                    next_run_at = autosync.get('nextRunAt')
                    if not next_run_at:
                        # Initialize next run
                        interval = autosync.get('intervalSeconds', 3600)
                        next_run_at = int(time.time()) + interval
                        autosync['nextRunAt'] = next_run_at
                        # Save updated config
                        self._save_project_config(project_id, config)
                        continue
                    
                    current_time = int(time.time())
                    if current_time < next_run_at:
                        continue  # Not time yet
                    
                    # Time to run autosync
                    self._run_autosync(project_id, autosync)
                    
                except Exception as e:
                    logger.error(f"Error checking autosync for project {project_id}: {e}", exc_info=True)
        
        except Exception as e:
            logger.error(f"Error in _check_and_run_autosync: {e}", exc_info=True)
    
    def _run_autosync(self, project_id: str, autosync_config: dict):
        """Run autosync for a project"""
        try:
            direction = autosync_config.get('direction', 'pull')
            source_keys = autosync_config.get('sourceKeys', [])
            interval = autosync_config.get('intervalSeconds', 3600)
            
            if not source_keys:
                logger.warning(f"Autosync enabled for project {project_id} but no sources selected")
                return
            
            # Update last run time
            autosync_config['lastRunAt'] = int(time.time())
            autosync_config['lastStatus'] = 'running'
            autosync_config['lastError'] = None
            autosync_config['nextRunAt'] = int(time.time()) + interval
            
            # Save config
            config = self.load_project_config(project_id)
            config['autosync'] = autosync_config
            self._save_project_config(project_id, config)
            
            # Load source configs
            sources = config.get('sources', {})
            # Import normalize_sources dynamically to avoid circular dependency
            import sys
            if 'app' in sys.modules:
                app_module = sys.modules['app']
                if hasattr(app_module, 'normalize_sources'):
                    normalized_sources = app_module.normalize_sources(sources, project_id)
                else:
                    # Fallback: create minimal normalized sources
                    normalized_sources = {}
                    for key in source_keys:
                        normalized_sources[key] = sources.get(key, {})
            else:
                # Fallback: create minimal normalized sources
                normalized_sources = {}
                for key in source_keys:
                    normalized_sources[key] = sources.get(key, {})
            
            # Run sync for each source
            all_success = True
            errors = []
            
            for source_key in source_keys:
                if source_key not in normalized_sources:
                    errors.append(f"{source_key}: Source not configured")
                    all_success = False
                    continue
                
                source_config = normalized_sources[source_key]
                
                # Check if source is already syncing
                sync_state = self.source_sync_service.get_sync_state(project_id, source_key)
                if sync_state.get('lastPushStatus') == 'running' or sync_state.get('lastPullStatus') == 'running':
                    logger.info(f"Skipping {source_key} for project {project_id}: already syncing")
                    continue
                
                # Start sync
                try:
                    # start_sync requires source_config parameter
                    # start_sync returns (job_id, success, error_code, error_msg)
                    result = self.source_sync_service.start_sync(
                        project_id=project_id,
                        source_key=source_key,
                        source_config=source_config,
                        direction=direction,
                        actor='system'
                    )
                    
                    if isinstance(result, tuple) and len(result) == 4:
                        job_id, success, error_code, error_msg = result
                    else:
                        # Fallback if signature changed
                        job_id = result[0] if isinstance(result, tuple) else None
                        success = False
                        error_code = 'UNKNOWN_ERROR'
                        error_msg = 'Unexpected return value from start_sync'
                    
                    if not success:
                        errors.append(f"{source_key}: {error_msg or error_code}")
                        all_success = False
                    else:
                        logger.info(f"Autosync started for {project_id}/{source_key}: {job_id}")
                
                except Exception as e:
                    logger.error(f"Error starting autosync for {project_id}/{source_key}: {e}", exc_info=True)
                    errors.append(f"{source_key}: {str(e)}")
                    all_success = False
            
            # Update status
            autosync_config['lastStatus'] = 'ok' if all_success else 'failed'
            if errors:
                autosync_config['lastError'] = '; '.join(errors)
            else:
                autosync_config.pop('lastError', None)
            
            # Save updated config
            config = self.load_project_config(project_id)
            config['autosync'] = autosync_config
            self._save_project_config(project_id, config)
            
        except Exception as e:
            logger.error(f"Error running autosync for project {project_id}: {e}", exc_info=True)
            # Update status with error
            autosync_config['lastStatus'] = 'failed'
            autosync_config['lastError'] = str(e)
            config = self.load_project_config(project_id)
            config['autosync'] = autosync_config
            self._save_project_config(project_id, config)
    
    def _save_project_config(self, project_id: str, config: dict):
        """Save project config (import from app to avoid circular dependency)"""
        # This will be set from app.py
        pass

