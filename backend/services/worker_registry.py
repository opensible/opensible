#!/usr/bin/env python3
"""
 workers 
"""
import json
import os
import uuid
import time
import logging
import hashlib
import secrets
from pathlib import Path
from typing import Optional, Dict, Tuple

logger = logging.getLogger(__name__)

# DATA_DIR app ( ).
# : DATA_DIR ( Docker = /app/data),
# workers volume .
def _get_data_dir():
    """ DATA_DIR, backend/app.py"""
    env_dir = os.environ.get('DATA_DIR')
    if env_dir:
        return Path(env_dir)
    # Fallback: <repo_root>/data (backend/worker_registry.py -> repo root = parents[1])
    return Path(__file__).resolve().parent.parent / 'data'

DATA_DIR = _get_data_dir()
logger.info(f"worker_registry using DATA_DIR={DATA_DIR}")

# workers ( worker)
WORKERS_DIR = DATA_DIR / 'workers'
WORKERS_DIR.mkdir(parents=True, exist_ok=True)


def _hash_token(token: str, salt: str) -> str:
    """ salt"""
    return hashlib.sha256((token + salt).encode('utf-8')).hexdigest()


def _generate_token() -> str:
    """ """
    return secrets.token_urlsafe(32)


def _generate_salt() -> str:
    """ salt """
    return secrets.token_urlsafe(16)


def load_worker(worker_id: str) -> Optional[Dict]:
    """ worker ID"""
    worker_file = WORKERS_DIR / f'{worker_id}.json'
    if not worker_file.exists():
        return None

    try:
        # Skip zero-byte/whitespace files silently — they're transient during writes.
        if worker_file.stat().st_size == 0:
            return None
        with open(worker_file, 'r', encoding='utf-8') as f:
            content = f.read().strip()
        if not content:
            return None
        return json.loads(content)
    except json.JSONDecodeError:
        logger.debug(f"Worker file {worker_id} is not valid JSON yet (mid-write?), skipping")
        return None
    except Exception as e:
        logger.error(f"Error loading worker {worker_id}: {e}")
        return None


def save_worker(worker_data: Dict) -> bool:
    """ worker"""
    worker_id = worker_data.get('id')
    if not worker_id:
        return False
    
    worker_file = WORKERS_DIR / f'{worker_id}.json'
    try:
        with open(worker_file, 'w', encoding='utf-8') as f:
            json.dump(worker_data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error(f"Error saving worker {worker_id}: {e}")
        return False


def load_all_workers() -> Dict[str, Dict]:
    """ workers"""
    workers = {}
    for worker_file in WORKERS_DIR.glob('*.json'):
        try:
            with open(worker_file, 'r', encoding='utf-8') as f:
                worker_data = json.load(f)
                worker_id = worker_data.get('id')
                if worker_id:
                    workers[worker_id] = worker_data
        except Exception as e:
            logger.warning(f"Error loading worker file {worker_file}: {e}")
    return workers


def create_worker(name: str, capabilities: Optional[Dict] = None, tags: Optional[list] = None) -> Tuple[str, str]:
    """
 worker (worker_id, plaintext_token)
 !
    """
    worker_id = str(uuid.uuid4())
    plaintext_token = _generate_token()
    salt = _generate_salt()
    token_hash = _hash_token(plaintext_token, salt)
    
    worker_data = {
        'id': worker_id,
        'name': name,
        'tokenHash': token_hash,
        'tokenSalt': salt,
        'capabilities': capabilities or {},
        'tags': tags or [],
        'enabled': True,
        'createdAt': time.time(),
        'lastSeenAt': None,
        'currentExecutionId': None
    }
    
    if save_worker(worker_data):
        logger.info(f"Created worker {worker_id} ({name})")
        try:
            from storage import index_db as _index_db
            _index_db.upsert_worker_token(worker_id, token_hash, salt)
        except Exception:
            pass
        return worker_id, plaintext_token
    else:
        raise Exception("Failed to save worker")


# Small in-process cache: token -> (worker_id, expires_at). Cuts DB / file reads
# for the heartbeat storm since the same token is re-verified every ~2s per worker.
_TOKEN_CACHE: Dict[str, Tuple[str, float]] = {}
_TOKEN_CACHE_TTL = 60.0  # seconds


def verify_token(token: str) -> Optional[Tuple[str, Dict]]:
    """
 (worker_id, worker_data) 
 Returns None worker disabled
    """
    if not token:
        return None

    # 1) In-process cache (fastest)
    now = time.time()
    cached = _TOKEN_CACHE.get(token)
    if cached and cached[1] > now:
        worker_data = load_worker(cached[0])
        if worker_data and worker_data.get('enabled', True):
            return cached[0], worker_data
        # Cache stale
        _TOKEN_CACHE.pop(token, None)

    # 2) SQLite index (indexed lookup on token_hash across all known salts)
    try:
        from storage import index_db as _index_db
        for wid, salt, th in _index_db.all_worker_salts():
            if _hash_token(token, salt) == th:
                worker_data = load_worker(wid)
                if worker_data and worker_data.get('enabled', True):
                    _TOKEN_CACHE[token] = (wid, now + _TOKEN_CACHE_TTL)
                    return wid, worker_data
    except Exception:
        pass

    # 3) Legacy fallback: scan worker JSON files (also refreshes the index)
    workers = load_all_workers()
    for worker_id, worker_data in workers.items():
        if not worker_data.get('enabled', True):
            continue
        token_hash = worker_data.get('tokenHash')
        token_salt = worker_data.get('tokenSalt')
        if not token_hash or not token_salt:
            continue
        if _hash_token(token, token_salt) == token_hash:
            try:
                from storage import index_db as _index_db
                _index_db.upsert_worker_token(worker_id, token_hash, token_salt)
            except Exception:
                pass
            _TOKEN_CACHE[token] = (worker_id, now + _TOKEN_CACHE_TTL)
            return worker_id, worker_data

    return None


def rotate_worker_token(worker_id: str) -> str:
    """
 worker plaintext_token
 !
    """
    worker_data = load_worker(worker_id)
    if not worker_data:
        raise ValueError(f"Worker {worker_id} not found")
    
    plaintext_token = _generate_token()
    salt = _generate_salt()
    token_hash = _hash_token(plaintext_token, salt)
    
    worker_data['tokenHash'] = token_hash
    worker_data['tokenSalt'] = salt
    worker_data['lastTokenRotatedAt'] = time.time()
    
    if save_worker(worker_data):
        logger.info(f"Rotated token for worker {worker_id}")
        try:
            from storage import index_db as _index_db
            _index_db.upsert_worker_token(worker_id, token_hash, salt)
            # Purge any cached entries for this worker (old tokens no longer valid).
            for k in [k for k, v in _TOKEN_CACHE.items() if v[0] == worker_id]:
                _TOKEN_CACHE.pop(k, None)
        except Exception:
            pass
        return plaintext_token
    else:
        raise Exception("Failed to save worker")


def update_worker(worker_id: str, name: Optional[str] = None, description: Optional[str] = None, 
                 tags: Optional[list] = None, tagColors: Optional[dict] = None) -> bool:
    """
 worker (name, description, tags, tagColors)
    
    Args:
        worker_id: ID worker
 name: ()
 description: (, )
 tags: ()
 tagColors: {tag_name: color} ()
    
    Returns:
 True 
    """
    worker_data = load_worker(worker_id)
    if not worker_data:
        return False
    
    if name is not None:
        worker_data['name'] = name.strip()
    
    if description is not None:
        # description
        if description.strip():
            worker_data['description'] = description.strip()
        else:
            worker_data.pop('description', None)
    
    if tags is not None:
        worker_data['tags'] = tags
    
    if tagColors is not None:
        if 'tagColors' not in worker_data:
            worker_data['tagColors'] = {}
        worker_data['tagColors'].update(tagColors)
    
    worker_data['updatedAt'] = time.time()
    
    return save_worker(worker_data)


_UNCHANGED = object()
_HEARTBEAT_WRITE_CACHE: Dict[str, Tuple[float, object]] = {}
_HEARTBEAT_WRITE_MIN_INTERVAL = 15.0


def update_worker_heartbeat(worker_id: str, current_execution_id=_UNCHANGED) -> bool:
    """Update worker lastSeenAt and, when provided, its current execution.

    Passing ``None`` intentionally clears currentExecutionId. Omitting the
    argument leaves it unchanged. The old implementation could never clear the
    field, leaving stale executions attached to an idle restarted worker.
    """
    worker_data = load_worker(worker_id)
    if not worker_data:
        return False
    
    now = time.time()
    existing_current = worker_data.get('currentExecutionId')
    next_current = existing_current if current_execution_id is _UNCHANGED else current_execution_id
    cached = _HEARTBEAT_WRITE_CACHE.get(worker_id)
    if cached and (now - cached[0]) < _HEARTBEAT_WRITE_MIN_INTERVAL and cached[1] == next_current:
        return True

    worker_data['lastSeenAt'] = now
    if current_execution_id is not _UNCHANGED:
        worker_data['currentExecutionId'] = current_execution_id

    ok = save_worker(worker_data)
    if ok:
        _HEARTBEAT_WRITE_CACHE[worker_id] = (now, next_current)
    return ok


def get_worker_active_runs_count(worker_id: str) -> int:
    """ (RUNNING) runs worker"""
    worker_data = load_worker(worker_id)
    if not worker_data:
        return 0
    
    # Fast path: indexed RUNNING count. This endpoint is called on every worker
    # claim, so scanning every execution JSON here can keep the backend at
    # 50-100% CPU even when no jobs are queued.
    try:
        from storage import index_db as _index_db
        indexed_count = _index_db.running_count_for_worker(worker_id)
        if indexed_count is not None:
            return indexed_count
    except Exception:
        pass
    
    # RUNNING executions workerId
    count = 0
    try:
        try:
            from app_context import get_projects_dir
            projects_dir = get_projects_dir()
        except Exception:
            projects_dir = DATA_DIR / 'projects'

        for proj_dir in projects_dir.iterdir():
            if not proj_dir.is_dir():
                continue

            # Project Storage layout: history/executions/ (with legacy fallback to executions/)
            executions_dir = proj_dir / 'history' / 'executions'
            if not executions_dir.exists():
                legacy_dir = proj_dir / 'executions'
                if legacy_dir.exists():
                    executions_dir = legacy_dir
                else:
                    continue
            
            for exec_file in executions_dir.glob('*.json'):
                try:
                    with open(exec_file, 'r', encoding='utf-8') as f:
                        execution = json.load(f)
                        if (execution.get('status') == 'RUNNING' and 
                            execution.get('workerId') == worker_id):
                            count += 1
                            try:
                                from storage import index_db as _index_db
                                _index_db.mark_running_execution(
                                    execution.get('id') or exec_file.stem,
                                    proj_dir.name,
                                    worker_id,
                                    execution.get('startedAt') or execution.get('createdAt') or time.time(),
                                )
                            except Exception:
                                pass
                except:
                    pass
    except:
        pass
    
    return count


def is_worker_online(worker_id: str, ttl_seconds: int = 60) -> bool:
    """ worker (heartbeat TTL)"""
    worker_data = load_worker(worker_id)
    if not worker_data or not worker_data.get('enabled', True):
        return False
    
    last_seen = worker_data.get('lastSeenAt')
    if not last_seen:
        return False
    
    return (time.time() - last_seen) <= ttl_seconds


def enable_worker(worker_id: str) -> bool:
    """ worker"""
    worker_data = load_worker(worker_id)
    if not worker_data:
        return False
    
    worker_data['enabled'] = True
    return save_worker(worker_data)


def disable_worker(worker_id: str) -> bool:
    """ worker"""
    worker_data = load_worker(worker_id)
    if not worker_data:
        return False
    
    worker_data['enabled'] = False
    return save_worker(worker_data)


def delete_worker(worker_id: str) -> bool:
    """ worker"""
    worker_file = WORKERS_DIR / f'{worker_id}.json'
    if worker_file.exists():
        try:
            worker_file.unlink()
            logger.info(f"Deleted worker {worker_id}")
            try:
                from storage import index_db as _index_db
                _index_db.remove_worker_token(worker_id)
                for k in [k for k, v in _TOKEN_CACHE.items() if v[0] == worker_id]:
                    _TOKEN_CACHE.pop(k, None)
            except Exception:
                pass
            return True
        except Exception as e:
            logger.error(f"Error deleting worker {worker_id}: {e}")
            return False
    return False


def get_stale_workers(max_age_seconds: int = 300) -> list:
    """ workers, heartbeat max_age_seconds"""
    workers = load_all_workers()
    now = time.time()
    stale = []
    
    for worker_id, worker_data in workers.items():
        if not worker_data.get('enabled', True):
            continue
        
        last_seen = worker_data.get('lastSeenAt')
        if last_seen and (now - last_seen > max_age_seconds):
            stale.append(worker_id)
    
    return stale


def migrate_legacy_workers():
    """ workers.json ( worker)"""
    legacy_file = DATA_DIR / 'workers.json'
    if not legacy_file.exists():
        return
    
    try:
        with open(legacy_file, 'r', encoding='utf-8') as f:
            legacy_workers = json.load(f)
        
        migrated = 0
        for worker_id, worker_data in legacy_workers.items():
            # worker , 
            if load_worker(worker_id):
                continue
            
            if 'token' in worker_data:
                # plaintext - 
                plaintext_token = worker_data.pop('token')
                salt = _generate_salt()
                token_hash = _hash_token(plaintext_token, salt)
                worker_data['tokenHash'] = token_hash
                worker_data['tokenSalt'] = salt
                logger.warning(f"Migrated worker {worker_id}: old plaintext token replaced with hash")
            
            # defaults
            worker_data.setdefault('enabled', True)
            worker_data.setdefault('capabilities', {})
            worker_data.setdefault('tags', [])
            
            if save_worker(worker_data):
                migrated += 1
        
        if migrated > 0:
            logger.info(f"Migrated {migrated} workers from legacy format")
            # backup
            legacy_file.rename(legacy_file.with_suffix('.json.backup'))
    
    except Exception as e:
        logger.error(f"Error migrating legacy workers: {e}")


def ensure_default_worker():
    """ default worker dev mode workers storage """
    workers = load_all_workers()
    if workers:
        return None  # workers
    
    try:
        worker_id, plaintext_token = create_worker(
            name='local-worker',
            capabilities={},
            tags=['default', 'local']
        )
        logger.warning("=" * 80)
        logger.warning("DEV MODE: Created default worker 'local-worker'")
        logger.warning(f"Worker ID: {worker_id}")
        logger.warning(f"Worker Token: {plaintext_token}")
        logger.warning("=" * 80)
        logger.warning("IMPORTANT: Save this token! It will not be shown again.")
        logger.warning("Set it in worker/.token file or WORKER_TOKEN environment variable.")
        logger.warning("=" * 80)
        return worker_id, plaintext_token
    except Exception as e:
        logger.error(f"Error creating default worker: {e}")
        return None
