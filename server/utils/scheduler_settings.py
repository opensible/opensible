"""Runtime-configurable background scheduler intervals.

Values are persisted in the config DB (``scheduler_settings`` key) so they can
be edited from the web console without a restart. Resolution order for each
interval is: stored setting -> environment variable -> built-in default.

Schedulers read these on every loop iteration, so changes take effect after at
most one tick.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict

from app_context import get_data_dir
from storage import config_db

logger = logging.getLogger(__name__)

SETTINGS_KEY = "scheduler_settings"

# name -> (env var, default seconds, min seconds, max seconds)
SCHEDULER_SPECS: Dict[str, tuple] = {
    "autosync_check_interval": ("AUTOSYNC_CHECK_INTERVAL", 60, 10, 86400),
    "playbook_scheduler_interval": ("PLAYBOOK_SCHEDULER_INTERVAL", 60, 15, 86400),
    "execution_recovery_interval": ("EXECUTION_RECOVERY_INTERVAL", 300, 60, 86400),
    "host_status_scheduler_interval": ("HOST_STATUS_SCHEDULER_INTERVAL", 300, 30, 86400),
}


def _clamp(name: str, value: Any) -> int:
    _env, default, lo, hi = SCHEDULER_SPECS[name]
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _env_default(name: str) -> int:
    env_var, default, _lo, _hi = SCHEDULER_SPECS[name]
    raw = os.environ.get(env_var)
    if raw:
        return _clamp(name, raw)
    return default


def load_scheduler_settings() -> Dict[str, int]:
    """Return all intervals (seconds), resolved and clamped."""
    stored: Dict[str, Any] = {}
    try:
        data = config_db.get_setting(get_data_dir(), SETTINGS_KEY, None)
        if isinstance(data, dict):
            stored = data
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Failed to load scheduler settings: %s", e)

    out: Dict[str, int] = {}
    for name in SCHEDULER_SPECS:
        if name in stored and stored[name] is not None:
            out[name] = _clamp(name, stored[name])
        else:
            out[name] = _env_default(name)
    return out


def save_scheduler_settings(values: Dict[str, Any]) -> Dict[str, int]:
    """Persist the provided intervals (partial updates allowed). Returns the
    full resolved settings map."""
    current = load_scheduler_settings()
    merged = dict(current)
    for name in SCHEDULER_SPECS:
        if name in (values or {}):
            merged[name] = _clamp(name, values[name])
    try:
        config_db.set_setting(get_data_dir(), SETTINGS_KEY, merged)
    except Exception as e:
        logger.error("Failed to save scheduler settings: %s", e)
        raise
    return merged


def get_interval(name: str) -> int:
    """Resolve a single interval by name (seconds)."""
    if name not in SCHEDULER_SPECS:
        raise KeyError(name)
    try:
        return load_scheduler_settings()[name]
    except Exception:
        return _env_default(name)


def get_defaults() -> Dict[str, Dict[str, int]]:
    """Bounds + defaults, for UI validation hints."""
    return {
        name: {
            "default": _env_default(name),
            "min": spec[2],
            "max": spec[3],
        }
        for name, spec in SCHEDULER_SPECS.items()
    }
