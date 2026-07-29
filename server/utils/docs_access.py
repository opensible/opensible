#!/usr/bin/env python3
"""IP allowlist for public API docs (/api/docs and /api/openapi.json).

Config file: <DATA_DIR>/auth/docs_access.json
{
  "enabled": false,
  "allowlist": ["127.0.0.1", "10.0.0.0/24", "::1"]
}

When `enabled` is false (default), docs are reachable by anyone (no change
from prior behaviour). When `enabled` is true, only requests whose client IP
matches one of the allowlist entries (single IP or CIDR, v4 or v6) are
permitted. All other requests get HTTP 403.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from flask import request

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_data_dir: Optional[Path] = None


def set_data_dir(data_dir: Path) -> None:
    global _data_dir
    _data_dir = Path(data_dir)
    (_data_dir / 'auth').mkdir(parents=True, exist_ok=True)


def _config_path() -> Path:
    if _data_dir is None:
        raise RuntimeError("docs_access.set_data_dir() not called")
    return _data_dir / 'auth' / 'docs_access.json'


DEFAULT_CONFIG: Dict[str, Any] = {"enabled": False, "allowlist": []}


def load_config() -> Dict[str, Any]:
    path = _config_path()
    with _lock:
        if not path.exists():
            return dict(DEFAULT_CONFIG)
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except Exception as e:
            logger.warning("Failed to read docs_access.json: %s", e)
            return dict(DEFAULT_CONFIG)
    return {
        "enabled": bool(data.get("enabled", False)),
        "allowlist": [str(x) for x in (data.get("allowlist") or [])],
    }


def _validate_entry(entry: str) -> str:
    entry = (entry or "").strip()
    if not entry:
        raise ValueError("empty entry")
    # Accept single IP or CIDR for v4/v6
    try:
        if '/' in entry:
            ipaddress.ip_network(entry, strict=False)
        else:
            ipaddress.ip_address(entry)
    except ValueError as e:
        raise ValueError(f"invalid IP/CIDR: {entry}") from e
    return entry


def save_config(enabled: bool, allowlist: List[str]) -> Dict[str, Any]:
    cleaned: List[str] = []
    seen = set()
    for raw in allowlist or []:
        v = _validate_entry(raw)
        if v not in seen:
            cleaned.append(v)
            seen.add(v)
    cfg = {"enabled": bool(enabled), "allowlist": cleaned}
    path = _config_path()
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(cfg, indent=2), encoding='utf-8')
        tmp.replace(path)
    return cfg


def _client_ip() -> str:
    # Trust last hop in X-Forwarded-For when present (set by reverse proxy).
    xff = request.headers.get('X-Forwarded-For', '')
    if xff:
        ip = xff.split(',')[0].strip()
        if ip:
            return ip
    return request.remote_addr or ''


def is_allowed(ip: str, allowlist: List[str]) -> bool:
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for entry in allowlist:
        try:
            if '/' in entry:
                if addr in ipaddress.ip_network(entry, strict=False):
                    return True
            else:
                if addr == ipaddress.ip_address(entry):
                    return True
        except ValueError:
            continue
    return False


def check_request() -> Tuple[bool, str]:
    """Return (allowed, client_ip). Allowed when feature disabled or IP matches."""
    cfg = load_config()
    ip = _client_ip()
    if not cfg.get("enabled"):
        return True, ip
    return is_allowed(ip, cfg.get("allowlist", [])), ip