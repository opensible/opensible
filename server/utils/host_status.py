"""In-memory host-status cache with TTL, extracted from app.py (Phase 2).

Holds the per-project host reachability cache used by inventory pages. Reads
the on-disk ``host_status.json`` as a fallback when the in-process cache is
cold, and writes back with a file lock so concurrent workers stay consistent.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from utils.json_io import safe_write_json_with_lock
from utils.project_paths import get_project_host_status_file

_logger = logging.getLogger(__name__)

HOST_STATUS_TTL_DEFAULT = 300  # seconds (5 minutes)

# key: (project_id, host_name) -> value: {'status', 'last_checked_ts', 'expires_at_ts'}
HOST_STATUS_CACHE: Dict[Tuple[str, str], Dict[str, Any]] = {}


def get_host_status_ttl_seconds(project_id: Optional[str] = None) -> int:
    """Resolve the TTL (seconds) in this priority order:

    1. ``host_status.ttl_seconds`` (or legacy ``host_status_ttl_seconds``) in
       the project's ``project.json``
    2. ``HOST_STATUS_TTL`` environment variable
    3. :data:`HOST_STATUS_TTL_DEFAULT`
    """
    if project_id:
        try:
            from app import load_project_config  # lazy: app imports us
            config = load_project_config(project_id) or {}
            host_status_cfg = config.get('host_status', {})
            ttl = host_status_cfg.get('ttl_seconds')
            if ttl is None:
                ttl = config.get('host_status_ttl_seconds')
            if isinstance(ttl, (int, float)) and ttl > 0:
                return int(ttl)
        except Exception:
            pass

    env_ttl = os.environ.get('HOST_STATUS_TTL')
    if env_ttl:
        try:
            ttl_env = int(env_ttl)
            if ttl_env > 0:
                return ttl_env
        except ValueError:
            pass

    return HOST_STATUS_TTL_DEFAULT


def set_host_check_status(project_id: str, host: str, status: str) -> None:
    """Record a host status in the in-memory cache and persist to disk.

    ``status`` is one of ``'online'``, ``'offline'``, ``'checking'``, ``'unknown'``.
    """
    if not project_id or not host:
        return

    ttl = get_host_status_ttl_seconds(project_id)
    now_ts = time.time()
    expires_at_ts = now_ts + ttl if ttl > 0 else now_ts

    key = (str(project_id), str(host))
    HOST_STATUS_CACHE[key] = {
        'status': status,
        'last_checked_ts': now_ts,
        'expires_at_ts': expires_at_ts,
    }

    try:
        status_file = get_project_host_status_file(project_id)
        existing = {}
        if status_file.exists():
            try:
                with open(status_file, 'r', encoding='utf-8') as f:
                    existing = json.load(f) or {}
            except Exception:
                existing = {}
        hosts_map = existing.get('hosts') or {}
        hosts_map[host] = {
            'status': status,
            'last_checked_at': datetime.utcfromtimestamp(now_ts).isoformat() + 'Z',
            'status_expires_at': datetime.utcfromtimestamp(expires_at_ts).isoformat() + 'Z',
        }
        safe_write_json_with_lock(status_file, {'hosts': hosts_map})
    except Exception as e:
        _logger.warning(f"Failed to persist host status for project {project_id}, host {host}: {e}")


def get_host_check_status(project_id: str, host: str):
    """Return the cached status for ``(project_id, host)`` or ``None`` if unknown.

    Rehydrates from ``host_status.json`` when the in-memory cache is cold. TTL
    is not enforced by removal — expired entries are returned with
    ``expired=True`` so callers can decide whether to re-check.
    """
    if not project_id or not host:
        return None

    key = (str(project_id), str(host))
    data = HOST_STATUS_CACHE.get(key)

    if not data:
        try:
            status_file = get_project_host_status_file(project_id)
            if status_file.exists():
                with open(status_file, 'r', encoding='utf-8') as f:
                    file_data = json.load(f) or {}
                host_entry = (file_data.get('hosts') or {}).get(host)
                if host_entry:
                    last_checked_at_str = host_entry.get('last_checked_at')
                    status_expires_at_str = host_entry.get('status_expires_at')
                    last_checked_ts = None
                    expires_at_ts = None
                    if last_checked_at_str:
                        try:
                            last_checked_ts = datetime.fromisoformat(last_checked_at_str.replace('Z', '+00:00')).timestamp()
                        except Exception:
                            last_checked_ts = None
                    if status_expires_at_str:
                        try:
                            expires_at_ts = datetime.fromisoformat(status_expires_at_str.replace('Z', '+00:00')).timestamp()
                        except Exception:
                            expires_at_ts = None
                    if last_checked_ts is None:
                        last_checked_ts = time.time()
                    if expires_at_ts is None:
                        ttl = get_host_status_ttl_seconds(project_id)
                        expires_at_ts = last_checked_ts + ttl

                    data = {
                        'status': host_entry.get('status', 'unknown'),
                        'last_checked_ts': last_checked_ts,
                        'expires_at_ts': expires_at_ts,
                    }
                    HOST_STATUS_CACHE[key] = data
        except Exception as e:
            _logger.warning(f"Failed to load host status from file for project {project_id}, host {host}: {e}")

    if not data:
        return None

    now_ts = time.time()
    expires_at_ts = data.get('expires_at_ts') or 0
    expired = expires_at_ts <= now_ts

    last_checked_ts = data.get('last_checked_ts') or now_ts
    last_checked_at = datetime.utcfromtimestamp(last_checked_ts).isoformat() + 'Z'
    status_expires_at = datetime.utcfromtimestamp(expires_at_ts).isoformat() + 'Z'

    out = {
        'status': data.get('status', 'unknown'),
        'last_checked_at': last_checked_at,
        'status_expires_at': status_expires_at,
    }
    if expired:
        out['expired'] = True
    return out
