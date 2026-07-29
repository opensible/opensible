"""Backup / execution-settings helpers.

Extracted from app.py during Phase 3 of the refactor. All settings files
live under ``app_context.get_data_dir()``. Behaviour is unchanged from the
original in-file helpers; app.py re-exports these so every existing caller
keeps working without modification.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

from app_context import get_data_dir
from utils.host_status import HOST_STATUS_TTL_DEFAULT
from storage import config_db

logger = logging.getLogger(__name__)


VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


# Legacy file paths kept only so tooling that inspects them still resolves.
# All reads/writes go through config_db (SQLite + WAL).

def _execution_settings_file() -> Path:
    return get_data_dir() / "execution_settings.json"


def _backup_settings_file() -> Path:
    return get_data_dir() / "backup_settings.json"


# ---------- execution history settings ----------

def load_execution_settings() -> Dict[str, Any]:
    """Load execution-history settings, merging stored values over defaults."""
    default_settings: Dict[str, Any] = {
        "save_history": True,
        "retention_mode": "count",  # 'count' or 'size'
        "retention_count": 200,
        "retention_size_mb": 200,
        "debug_mode": False,
        "log_level": "INFO",
        "max_upload_size_mb": 10,
        "max_log_size_mb": 10,
        "host_status_ttl_seconds": HOST_STATUS_TTL_DEFAULT,
    }
    try:
        config_db.migrate_from_json_if_needed(get_data_dir())
        stored = config_db.get_setting(get_data_dir(), "execution_settings", None)
        if isinstance(stored, dict):
            return {**default_settings, **stored}
    except Exception as e:
        logger.error(f"Error loading execution history settings: {e}")
    return default_settings


def save_execution_settings(settings: Dict[str, Any]) -> bool:
    """Persist execution-history settings to the config DB."""
    try:
        return config_db.set_setting(get_data_dir(), "execution_settings", settings)
    except Exception as e:
        logger.error(f"Error saving execution history settings: {e}")
        return False


# ---------- backup settings ----------

def load_backup_settings() -> Dict[str, Any]:
    """Load per-project backup settings, merging stored values over defaults."""
    default_settings: Dict[str, Any] = {
        "max_depth": 100,
        "projects": {},  # {project_id: {'enabled': bool}}
    }
    try:
        config_db.migrate_from_json_if_needed(get_data_dir())
        stored = config_db.get_setting(get_data_dir(), "backup_settings", None)
        if isinstance(stored, dict):
            result = {**default_settings, **stored}
            if "projects" not in result:
                result["projects"] = {}
            return result
    except Exception as e:
        logger.error(f"Error loading backup settings: {e}")
    return default_settings


def save_backup_settings(settings: Dict[str, Any]) -> bool:
    try:
        return config_db.set_setting(get_data_dir(), "backup_settings", settings)
    except Exception as e:
        logger.error(f"Error saving backup settings: {e}")
        return False



def get_project_backup_enabled(project_id: str) -> bool:
    settings = load_backup_settings()
    project_settings = settings.get("projects", {}).get(project_id, {})
    return bool(project_settings.get("enabled", False))


def should_create_backup(file_path: Any, project_id: Optional[str] = None) -> bool:
    """Return True if backups are enabled for the project owning ``file_path``.

    If ``project_id`` is not given, try to derive it from the path.
    """
    if not project_id:
        file_path_str = str(file_path)
        m = re.search(r"/projects/([^/]+)/", file_path_str)
        if m:
            project_id = m.group(1)
    if not project_id:
        return False
    return get_project_backup_enabled(project_id)


# ---------- pruning helpers ----------

def cleanup_old_backups(backup_dir: Path, max_depth: int) -> None:
    """Keep only the newest ``max_depth`` ``*.yml`` files in ``backup_dir``."""
    try:
        if not backup_dir.exists():
            return
        backup_files = sorted(
            backup_dir.glob("*.yml"),
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        )
        if len(backup_files) > max_depth:
            for file_to_delete in backup_files[max_depth:]:
                try:
                    file_to_delete.unlink()
                    logger.debug(f"Deleted old backup: {file_to_delete}")
                except Exception as e:
                    logger.error(f"Error deleting old backup {file_to_delete}: {e}")
    except Exception as e:
        logger.error(f"Error cleaning old backups in {backup_dir}: {e}")


def cleanup_old_archives(archive_dir: Path, max_depth: int) -> None:
    """Keep only the newest ``max_depth`` ``*.tar.gz`` files in ``archive_dir``."""
    try:
        if not archive_dir.exists():
            return
        archive_files = sorted(
            archive_dir.glob("*.tar.gz"),
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        )
        if len(archive_files) > max_depth:
            for f in archive_files[max_depth:]:
                try:
                    f.unlink()
                    logger.debug(f"Deleted old archive: {f}")
                except Exception as e:
                    logger.error(f"Error deleting old archive {f}: {e}")
    except Exception as e:
        logger.error(f"Error cleaning old archives in {archive_dir}: {e}")


def validate_log_level(level_name: str) -> bool:
    """Return True when ``level_name`` names a valid Python logging level."""
    return str(level_name or "").upper() in VALID_LOG_LEVELS
