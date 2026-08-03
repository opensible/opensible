"""
PlaybookScheduler: Background scheduler for automatic playbook execution.

Runs playbooks based on cron schedules configured in playbook metadata.
No OS cron used - all scheduling happens inside the application.
"""

import time
import threading
import logging
import fcntl
import os
import json
from pathlib import Path
from typing import Dict, Optional, List, Tuple
from datetime import datetime
import pytz
from croniter import croniter

logger = logging.getLogger(__name__)


class PlaybookScheduler:
    """
    Scheduler for automatic playbook execution based on cron schedules.
    
    Docker-aware architecture:
    - Runs as background thread inside backend process
    - Polls database every N seconds
    - Evaluates cron expressions with timezone support
    - Creates runs when cron matches current time
    """
    
    def __init__(self, playbook_storage, api_run_playbook_func, lock_file_dir=None):
        """
        Args:
            playbook_storage: PlaybookStorage instance
            api_run_playbook_func: Function to create run (project_id, playbook_id) -> (success, error)
            lock_file_dir: Directory for lock files (for cross-process synchronization)
        """
        self.playbook_storage = playbook_storage
        self.api_run_playbook = api_run_playbook_func
        
        self._running = False
        self._scheduler_thread = None
        self._lock = threading.Lock()  # Lock for scheduler loop
        self._run_locks = {}  # Per-schedule locks to prevent duplicate runs
        self._run_locks_lock = threading.Lock()  # Lock for managing _run_locks dict
        
        # Lock file directory for cross-process synchronization
        if lock_file_dir is None:
            # Default to data directory
            base_dir = Path(__file__).parent.parent.parent
            lock_file_dir = base_dir / 'data' / 'locks'
        self._lock_file_dir = Path(lock_file_dir)
        self._lock_file_dir.mkdir(parents=True, exist_ok=True)
        
        # Check interval (seconds) - configurable at runtime via System Settings.
        self.CHECK_INTERVAL = self._resolve_interval()
        
        # Track last execution time per schedule to avoid duplicate runs
        # Persist to file so it survives process restarts
        self._last_execution_file = self._lock_file_dir / 'last_execution.json'
        self._last_execution = self._load_last_execution()
        
        # Track when scheduler started to prevent immediate runs after restart
        self._start_time = time.time()
        # Grace period after startup: don't run tasks immediately after restart
        # This prevents catch-up runs when scheduler restarts
        self.STARTUP_GRACE_PERIOD = 60  # Wait 60 seconds after startup before running tasks

    def _resolve_interval(self) -> int:
        try:
            from utils.scheduler_settings import get_interval
            return get_interval('playbook_scheduler_interval')
        except Exception:
            try:
                return max(15, int(os.environ.get('PLAYBOOK_SCHEDULER_INTERVAL') or 60))
            except (TypeError, ValueError):
                return 60

    
    def _load_last_execution(self) -> Dict:
        """Load last execution timestamps from file"""
        try:
            if self._last_execution_file.exists():
                with open(self._last_execution_file, 'r') as f:
                    data = json.load(f)
                    # Convert string keys back to tuples
                    result = {}
                    for key_str, timestamp in data.items():
                        # Parse "(project_id, playbook_id)" back to tuple
                        if key_str.startswith('(') and key_str.endswith(')'):
                            key_str = key_str[1:-1]  # Remove parentheses
                            parts = [p.strip().strip("'\"") for p in key_str.split(',')]
                            if len(parts) == 2:
                                result[(parts[0], parts[1])] = timestamp
                    logger.info(f"[PlaybookScheduler] Loaded {len(result)} last execution timestamps from file")
                    return result
        except Exception as e:
            logger.warning(f"[PlaybookScheduler] Error loading last execution file: {e}")
        return {}
    
    def _save_last_execution(self):
        """Save last execution timestamps to file"""
        try:
            # Convert tuple keys to strings for JSON
            data = {str(k): v for k, v in self._last_execution.items()}
            with open(self._last_execution_file, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            logger.error(f"[PlaybookScheduler] Error saving last execution file: {e}", exc_info=True)
    
    def start(self):
        """Start the scheduler"""
        with self._lock:
            if self._running:
                logger.warning("Playbook scheduler is already running, skipping start")
                return
            
            self._running = True
            self._scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True, name="PlaybookScheduler")
            self._scheduler_thread.start()
            logger.info(f"Playbook scheduler started (thread: {self._scheduler_thread.name}, id: {self._scheduler_thread.ident})")
    
    def stop(self):
        """Stop the scheduler"""
        with self._lock:
            self._running = False
            if self._scheduler_thread:
                self._scheduler_thread.join(timeout=5)
            logger.info("Playbook scheduler stopped")
    
    def _scheduler_loop(self):
        """Main scheduler loop"""
        logger.info(f"[PlaybookScheduler] Scheduler loop started, checking every {self.CHECK_INTERVAL}s")
        while self._running:
            try:
                self._check_and_run_schedules()
            except Exception as e:
                logger.error(f"[PlaybookScheduler] Error in scheduler loop: {e}", exc_info=True)
            
            # Sleep until next check (re-resolved so UI changes apply live)
            if self._running:
                self.CHECK_INTERVAL = self._resolve_interval()
                time.sleep(self.CHECK_INTERVAL)
    
    def _check_and_run_schedules(self):
        """Check all enabled schedules and run playbooks if cron matches"""
        # Don't lock the entire check - let _create_scheduled_run handle duplicate prevention
        # This allows multiple checks to run concurrently, but only one run will be created
        try:
            # Get current time in UTC first
            current_utc = datetime.utcnow()
            
            # Get all enabled schedules
            schedules = self.playbook_storage.list_all_schedules()
            
            if not schedules:
                logger.debug(f"[PlaybookScheduler] No enabled schedules found")
                return
            
            # Log at INFO level so we can see scheduler is working
            logger.info(f"[PlaybookScheduler] 🔍 Checking {len(schedules)} enabled schedule(s) at {current_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC")
            
            checked_count = 0
            triggered_count = 0
            
            for schedule_info in schedules:
                try:
                    project_id = schedule_info['project_id']
                    playbook_id = schedule_info['playbook_id']
                    schedule = schedule_info['schedule']
                    
                    # Check if schedule is enabled
                    if not schedule.get('enabled', False):
                        continue
                    
                    cron_expr = schedule.get('cron', '')
                    timezone_str = schedule.get('timezone', 'UTC')
                    
                    if not cron_expr:
                        continue
                    
                    checked_count += 1
                    
                    # Check if cron matches current time
                    if self._should_run_now(cron_expr, timezone_str, current_utc, project_id, playbook_id):
                        # Create run (with built-in duplicate prevention via lock)
                        logger.info(f"[PlaybookScheduler] 🚀 Triggering scheduled run for playbook {playbook_id} in project {project_id} (cron: {cron_expr}, tz: {timezone_str})")
                        # _create_scheduled_run will handle duplicate prevention with per-schedule lock
                        self._create_scheduled_run(project_id, playbook_id, schedule)
                        triggered_count += 1
                
                except Exception as e:
                    logger.error(f"Error checking schedule for playbook {schedule_info.get('playbook_id')}: {e}", exc_info=True)
                    continue
            
            if checked_count > 0:
                if triggered_count > 0:
                    logger.info(f"[PlaybookScheduler] ✅ Checked {checked_count} schedule(s), triggered {triggered_count} run(s)")
                else:
                    logger.debug(f"[PlaybookScheduler] Checked {checked_count} schedule(s), triggered {triggered_count} run(s)")
        
        except Exception as e:
            logger.error(f"Error in _check_and_run_schedules: {e}", exc_info=True)
    
    def _should_run_now(self, cron_expr: str, timezone_str: str, current_utc: datetime, 
                       project_id: str, playbook_id: str) -> bool:
        """
        Check if cron expression matches current time (with timezone)
        
        Args:
            cron_expr: Cron expression (e.g., "0 3 * * *")
            timezone_str: Timezone string (e.g., "UTC", "Europe/Moscow")
            current_utc: Current UTC time
            project_id: Project ID (for tracking)
            playbook_id: Playbook ID (for tracking)
        
        Returns:
            True if should run now
        """
        try:
            # Get timezone
            try:
                tz = pytz.timezone(timezone_str)
            except pytz.exceptions.UnknownTimeZoneError:
                logger.warning(f"Unknown timezone {timezone_str}, using UTC")
                tz = pytz.UTC
            
            # Convert current UTC to target timezone
            current_in_tz = current_utc.replace(tzinfo=pytz.UTC).astimezone(tz)
            
            # Create croniter with timezone-aware datetime
            # croniter needs naive datetime in the target timezone
            current_naive = current_in_tz.replace(tzinfo=None)
            
            # Get previous and next execution times
            cron = croniter(cron_expr, current_naive)
            prev_time = cron.get_prev(datetime)
            next_time = cron.get_next(datetime)
            
            time_since_prev = (current_naive - prev_time).total_seconds()
            time_until_next = (next_time - current_naive).total_seconds()
            
            # Calculate the expected interval for this cron (for "*/5 * * * *" it's 5 minutes = 300 seconds)
            cron_interval = (next_time - prev_time).total_seconds()
            
            # Log timing info for debugging (use info level for "now" cases to help debugging)
            if time_until_next <= 10 or time_until_next < 0:
                logger.info(f"[PlaybookScheduler] ⏰ Timing check (URGENT): cron={cron_expr}, time_since_prev={time_since_prev:.1f}s, time_until_next={time_until_next:.1f}s, cron_interval={cron_interval:.1f}s")
            else:
                logger.debug(f"[PlaybookScheduler] Timing check: cron={cron_expr}, time_since_prev={time_since_prev:.1f}s, time_until_next={time_until_next:.1f}s, cron_interval={cron_interval:.1f}s")
            
            # Determine execution window: use CHECK_INTERVAL * 2 to account for polling delay
            # For frequent schedules, use a window based on the cron interval to ensure we catch all executions
            execution_window = self.CHECK_INTERVAL * 2  # Default: 60 seconds
            if cron_interval > 0:
                # For frequent schedules (interval <= 10 minutes), use a larger window
                # to ensure we don't miss executions due to polling delay
                if cron_interval <= 600:  # 10 minutes or less
                    # Use 50% of interval, but at least CHECK_INTERVAL * 2
                    # This ensures we catch executions even if we check slightly after the scheduled time
                    # For "*/5 * * * *" (300s interval), window = 150 seconds (2.5 minutes)
                    execution_window = max(self.CHECK_INTERVAL * 2, cron_interval * 0.5)
                # For less frequent schedules, CHECK_INTERVAL * 2 (60s) is sufficient
            
            # Check if we should run based on timing
            should_run = False
            reason = ""
            is_urgent = False  # Flag for "now" or very soon cases
            
            # Case 1: Next execution time has already passed (catch missed executions - HIGHEST PRIORITY)
            # If time_until_next is negative, the scheduled time has passed - run immediately
            if time_until_next < 0:
                # Allow catch-up if we're within 2x the cron interval (to avoid running very old schedules)
                max_catchup = cron_interval * 2 if cron_interval > 0 else 600
                if abs(time_until_next) <= max_catchup:
                    should_run = True
                    is_urgent = True  # Time has passed - urgent!
                    reason = f"Time has passed ({abs(time_until_next):.1f}s ago, max_catchup={max_catchup:.1f}s)"
                else:
                    logger.debug(f"[PlaybookScheduler] Time passed too long ago ({abs(time_until_next):.1f}s > {max_catchup:.1f}s), skipping")
            # Case 2: We're very close to the next scheduled time (within 5 seconds = "now")
            # This catches executions that are about to happen RIGHT NOW
            elif 0 <= time_until_next <= 5:
                should_run = True
                is_urgent = True  # Very soon - urgent!
                reason = f"Next execution is NOW ({time_until_next:.1f}s)"
            # Case 3: We're close to the next scheduled time (within CHECK_INTERVAL)
            # This catches executions that are about to happen soon
            elif 5 < time_until_next <= self.CHECK_INTERVAL:
                should_run = True
                reason = f"Next execution is soon ({time_until_next:.1f}s)"
            # Case 4: Previous execution was recent (within execution_window)
            # This catches executions that just happened or we missed due to polling delay
            elif 0 <= time_since_prev <= execution_window:
                should_run = True
                reason = f"Previous execution was recent ({time_since_prev:.1f}s ago, window={execution_window:.1f}s)"
            
            if should_run:
                # Check if we already executed this recently (avoid duplicates)
                schedule_key = (project_id, playbook_id)
                last_exec = self._last_execution.get(schedule_key, 0)
                # Use UTC timestamp for consistency (same as in _create_scheduled_run)
                current_timestamp = time.time()
                
                # Calculate minimum interval between executions to avoid duplicates
                # For urgent cases (time passed or very soon), use very small interval (5-10 seconds)
                # For normal cases, use 70% of cron interval, but at least 30 seconds
                if is_urgent:
                    # When time has passed or is very soon, allow execution even if recently executed
                    # Use very small interval to catch up immediately
                    min_interval = 5  # Only 5 seconds for urgent cases
                else:
                    min_interval = max(30, cron_interval * 0.7) if cron_interval > 0 else 60
                
                time_since_last_exec = current_timestamp - last_exec
                if time_since_last_exec >= min_interval:
                    logger.info(f"[PlaybookScheduler] ✅ Should run: {reason}, cron={cron_expr}, time_since_prev={time_since_prev:.1f}s, time_until_next={time_until_next:.1f}s, last_exec={time_since_last_exec:.1f}s ago, min_interval={min_interval:.1f}s, urgent={is_urgent}")
                    return True
                else:
                    # For urgent cases, log at info level to help debugging
                    log_level = logger.info if is_urgent else logger.debug
                    log_level(f"[PlaybookScheduler] ⏸️ Skipping duplicate: {reason}, but last_exec was {time_since_last_exec:.1f}s ago, min_interval={min_interval:.1f}s, urgent={is_urgent}")
            
            return False
        
        except Exception as e:
            logger.error(f"Error evaluating cron {cron_expr} with timezone {timezone_str}: {e}")
            return False
    
    def _create_scheduled_run(self, project_id: str, playbook_id: str, schedule: Dict):
        """
        Create a run for scheduled playbook execution
        
        Args:
            project_id: Project ID
            playbook_id: Playbook ID
            schedule: Schedule configuration
        """
        schedule_key = (project_id, playbook_id)
        current_timestamp = time.time()
        
        # CRITICAL: Don't run tasks immediately after scheduler startup
        # This prevents catch-up runs when backend restarts
        time_since_startup = current_timestamp - self._start_time
        if time_since_startup < self.STARTUP_GRACE_PERIOD:
            logger.info(f"[PlaybookScheduler] ⏸️ Skipping run during startup grace period: {time_since_startup:.1f}s since startup (grace period: {self.STARTUP_GRACE_PERIOD}s)")
            return
        
        # CRITICAL: Use per-schedule lock to prevent duplicate runs when scheduler checks happen simultaneously
        # This ensures that if two threads/processes check at the same time, only one will proceed
        # Each schedule has its own lock, so different schedules can run in parallel
        
        # Use file-based lock for cross-process synchronization
        # This prevents duplicate runs even if multiple backend processes are running
        lock_file_name = f"scheduler_{project_id}_{playbook_id}.lock"
        lock_file_path = self._lock_file_dir / lock_file_name
        
        lock_file = None
        lock_fd = None
        try:
            # CRITICAL: Use atomic file creation with O_EXCL flag to prevent race condition
            # This ensures only one process can create the lock file at a time
            # O_EXCL requires O_CREAT and will fail if file already exists
            try:
                # Try to create lock file atomically (fails if file exists)
                lock_fd = os.open(str(lock_file_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                lock_file = os.fdopen(lock_fd, 'w')
                # Write PID to lock file for debugging
                lock_file.write(f"{os.getpid()}\n")
                lock_file.flush()
                # Now acquire exclusive lock on the file descriptor
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                logger.info(f"[PlaybookScheduler] 🔒 File lock acquired for {schedule_key} (PID: {os.getpid()})")
            except (IOError, OSError) as e:
                # File already exists (another process has the lock) or other error
                if lock_file:
                    try:
                        lock_file.close()
                    except:
                        pass
                elif lock_fd is not None:
                    try:
                        os.close(lock_fd)
                    except:
                        pass
                lock_file = None
                lock_fd = None
                logger.info(f"[PlaybookScheduler] ⏸️ File lock already held for {schedule_key}, skipping (another process is running): {e}")
                return
            except Exception as e:
                # Unexpected error with file lock
                logger.error(f"[PlaybookScheduler] Unexpected error acquiring file lock for {schedule_key}: {e}", exc_info=True)
                if lock_file:
                    try:
                        lock_file.close()
                    except:
                        pass
                elif lock_fd is not None:
                    try:
                        os.close(lock_fd)
                    except:
                        pass
                lock_file = None
                lock_fd = None
                return
            
            # File lock acquired - now check if we should run
            # CRITICAL: Load last_exec from file while holding lock to get latest value
            # This ensures we see updates from other processes
            self._last_execution = self._load_last_execution()
            
            # CRITICAL: Update last_exec FIRST, then check interval
            # This ensures that even if two threads acquire lock simultaneously (unlikely but possible),
            # the second one will see updated last_exec and skip
            old_last_exec = self._last_execution.get(schedule_key, 0)
            time_since_last_exec = current_timestamp - old_last_exec
            
            # Calculate minimum interval (same logic as in _should_run_now)
            cron_expr = schedule.get('cron', '')
            cron_interval = 300  # Default 5 minutes for */5 * * * *
            if cron_expr:
                try:
                    from croniter import croniter
                    from datetime import datetime
                    cron = croniter(cron_expr, datetime.utcnow())
                    next_time = cron.get_next(datetime)
                    prev_time = cron.get_prev(datetime)
                    cron_interval = (next_time - prev_time).total_seconds()
                except:
                    pass
            
            min_interval = max(30, cron_interval * 0.7) if cron_interval > 0 else 60
            
            logger.info(f"[PlaybookScheduler] 🔒 Lock acquired for {schedule_key}, checking: old_last_exec={old_last_exec}, time_since_last_exec={time_since_last_exec:.1f}s, min_interval={min_interval:.1f}s")
            
            if time_since_last_exec < min_interval:
                logger.info(f"[PlaybookScheduler] ⏸️ Skipping duplicate run (race condition protection): last_exec was {time_since_last_exec:.1f}s ago, min_interval={min_interval:.1f}s, schedule_key={schedule_key}")
                return
            
            # Update last execution time IMMEDIATELY to prevent duplicate runs
            # This must be done inside the lock BEFORE calling API
            self._last_execution[schedule_key] = current_timestamp
            # Save to file immediately so other processes can see it
            self._save_last_execution()
            logger.info(f"[PlaybookScheduler] 🔒 Updated last_exec for {schedule_key} to {current_timestamp} (was {old_last_exec})")
            
            # Call API while holding the lock to prevent another thread from starting
            # This ensures only one run is created even if API call takes time
            logger.info(f"[PlaybookScheduler] 🚀 Calling API to run playbook {playbook_id} in project {project_id} (cron: {schedule.get('cron')}, tz: {schedule.get('timezone')}) - simulating Run button click")
            
            # Call API endpoint (like clicking Run button)
            # This calls the same API endpoint that the Run button uses
            success, error = self.api_run_playbook(project_id, playbook_id)
            
            if success:
                logger.info(f"[PlaybookScheduler] ✓ Successfully created scheduled run for playbook {playbook_id} in project {project_id}")
            else:
                logger.error(f"[PlaybookScheduler] ✗ Failed to create scheduled run for playbook {playbook_id} in project {project_id}: {error}")
                # If API call failed, we should NOT reset last_execution, so it can retry
                # But we already updated it, so we need to handle this case
                # Actually, it's OK to leave it updated - if it failed, we want to wait before retrying anyway
        
        except Exception as e:
            logger.error(f"[PlaybookScheduler] Error creating scheduled run for playbook {playbook_id} in project {project_id}: {e}", exc_info=True)
        finally:
            # Always release the file lock and remove lock file
            if lock_file:
                try:
                    # Get file descriptor before closing
                    fd = lock_file.fileno()
                    # Unlock the file
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    lock_file.close()
                    logger.info(f"[PlaybookScheduler] 🔓 File lock released for {schedule_key} (PID: {os.getpid()})")
                except:
                    pass
                # Clean up lock file (must be done after closing)
                try:
                    if lock_file_path.exists():
                        lock_file_path.unlink()
                except:
                    pass
            # On exception, we could reset last_execution, but it's safer to leave it
            # to prevent rapid retries on persistent errors
    
    @staticmethod
    def validate_cron(cron_expr: str) -> Tuple[bool, Optional[str]]:
        """
        Validate cron expression
        
        Args:
            cron_expr: Cron expression to validate
        
        Returns:
            (is_valid, error_message)
        """
        try:
            if not cron_expr or not cron_expr.strip():
                return False, "Cron expression is required"
            
            # Try to create croniter instance
            test_time = datetime.now()
            cron = croniter(cron_expr, test_time)
            
            # Try to get next execution time
            next_time = cron.get_next(datetime)
            
            if next_time:
                return True, None
            else:
                return False, "Invalid cron expression"
        
        except Exception as e:
            return False, f"Invalid cron expression: {str(e)}"
