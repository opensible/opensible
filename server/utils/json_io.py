"""JSON file I/O and safe-log helpers.

Extracted from app.py during Phase 1 of the refactor. These are
self-contained utilities; they use the stdlib `logging` module rather
than Flask's `app.logger` so they can be imported from anywhere.
"""
from __future__ import annotations

import fcntl
import json
import logging
import re
import time
from pathlib import Path

logger = logging.getLogger(__name__)


_SENSITIVE_KEYS = [
    'password', 'privateKey', 'private_key', 'passphrase', 'secret',
    'api_key', 'apiKey', 'token', 'access_token', 'refresh_token',
]


def safe_log_error(message, exception=None, context=None):
    """Return a log string with sensitive fields redacted.

    Behavior preserved from the original app.py implementation.
    """
    safe_message = str(message)

    if context and isinstance(context, dict):
        safe_context = {}
        for key, value in context.items():
            if any(sensitive in key.lower() for sensitive in _SENSITIVE_KEYS):
                safe_context[key] = '***REDACTED***'
            else:
                safe_context[key] = value
        safe_message = safe_message.replace(str(context), str(safe_context))

    for sensitive_key in _SENSITIVE_KEYS:
        patterns = [
            rf'{sensitive_key}\s*[:=]\s*["\']?([^"\'\s,}}]+)["\']?',
            rf'"{sensitive_key}"\s*:\s*"([^"]+)"',
            rf"'{sensitive_key}'\s*:\s*'([^']+)'",
        ]
        for pattern in patterns:
            safe_message = re.sub(
                pattern, f'{sensitive_key}=***REDACTED***',
                safe_message, flags=re.IGNORECASE,
            )

    if exception:
        error_type = type(exception).__name__
        error_msg = str(exception)
        for sensitive_key in _SENSITIVE_KEYS:
            error_msg = re.sub(
                rf'{sensitive_key}\s*[:=]\s*["\']?([^"\'\s,}}]+)["\']?',
                f'{sensitive_key}=***REDACTED***',
                error_msg, flags=re.IGNORECASE,
            )
        return f"{safe_message}: {error_type}: {error_msg}"

    return safe_message


def safe_write_json_with_lock(file_path, data, timeout=5):
    """Write JSON with an exclusive fcntl advisory lock.

    Raises IOError if the lock can't be acquired within `timeout` seconds.
    """
    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    lock_file_path = file_path.with_suffix(file_path.suffix + '.lock')
    start_time = time.time()

    lock_acquired = False
    lock_file = None

    try:
        lock_file_path.touch(exist_ok=True)
        lock_file = open(lock_file_path, 'w')

        while time.time() - start_time < timeout:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                lock_acquired = True
                break
            except IOError:
                time.sleep(0.1)

        if not lock_acquired:
            raise IOError(f"Could not acquire lock for {file_path} within {timeout} seconds")

        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        return True

    except Exception as e:
        logger.error(f"Error writing file with lock {file_path}: {e}")
        raise
    finally:
        if lock_file and lock_acquired:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            lock_file.close()


def safe_read_json_with_lock(file_path, timeout=5):
    """Read JSON with a shared fcntl advisory lock. Returns None on error/missing."""
    file_path = Path(file_path)

    if not file_path.exists():
        return None

    lock_file_path = file_path.with_suffix(file_path.suffix + '.lock')
    start_time = time.time()

    lock_acquired = False
    lock_file = None

    try:
        if lock_file_path.exists():
            lock_file = open(lock_file_path, 'r')

            while time.time() - start_time < timeout:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
                    lock_acquired = True
                    break
                except IOError:
                    time.sleep(0.1)

        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        return data

    except Exception as e:
        logger.error(f"Error reading file with lock {file_path}: {e}")
        return None
    finally:
        if lock_file and lock_acquired:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            lock_file.close()
