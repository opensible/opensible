"""YAML I/O helpers.

Extracted from app.py during Phase 1 of the refactor. Uses its own
ruamel YAML loader configured identically to the one in app.py
(``preserve_quotes=True``, ``width=4096``) so round-tripping is
byte-for-byte compatible.
"""
from __future__ import annotations

import logging
from io import StringIO
from pathlib import Path

from ruamel.yaml import YAML

logger = logging.getLogger(__name__)


yaml_loader = YAML()
yaml_loader.preserve_quotes = True
yaml_loader.width = 4096


def validate_yaml_content(content):
    """Validate that ``content`` is legal YAML.

    Accepts either a YAML source string or an in-memory Python object
    (dict/list) — in the latter case the object is dumped and re-parsed
    to catch structural issues. Returns ``(is_valid, error_message)``.
    """
    try:
        if isinstance(content, str):
            if not content.strip():
                return True, None
            test_loader = YAML()
            test_loader.preserve_quotes = True
            test_loader.width = 4096
            test_loader.load(StringIO(content))
            return True, None
        else:
            test_loader = YAML()
            test_loader.preserve_quotes = True
            test_loader.width = 4096
            stream = StringIO()
            test_loader.dump(content, stream)
            stream.seek(0)
            test_loader.load(stream)
            return True, None
    except Exception as e:
        error_msg = str(e)
        if hasattr(e, 'problem_mark'):
            mark = e.problem_mark
            error_msg = f"YAML syntax error at line {mark.line + 1}, column {mark.column + 1}: {error_msg}"
        elif hasattr(e, 'context_mark'):
            mark = e.context_mark
            error_msg = f"YAML syntax error at line {mark.line + 1}, column {mark.column + 1}: {error_msg}"
        elif 'line' in error_msg.lower() or 'column' in error_msg.lower():
            pass
        else:
            error_msg = f"YAML validation error: {error_msg}"
        return False, error_msg


def parse_yaml_with_comments(file_path):
    """Load a YAML file preserving comments. Returns ``{}`` when missing."""
    try:
        if not file_path.exists():
            return {}
        with open(file_path, 'r', encoding='utf-8') as f:
            data = yaml_loader.load(f)
        return data if data is not None else {}
    except Exception as e:
        return {'_error': str(e)}


def get_variable_descriptions():
    """Return an empty descriptions map.

    The legacy ``backend/variable_descriptions.yml`` file was removed; the API
    still exposes this function for backward compatibility but returns no data.
    """
    return {}


import re as _re
import shutil as _shutil
from datetime import datetime as _datetime


def create_backup(file_path, force=False):
    """Copy ``file_path`` to a timestamped backup under ``data/backups/<bucket>/``.

    Determines the bucket (``group_vars``/``host_vars``/``inventory``/``playbooks``/
    ``other``) from the source path, per-project when the path contains
    ``/projects/<pid>/``. Unless ``force`` is true, honors
    :func:`app.should_create_backup` gating. Cleans up beyond
    ``max_depth`` from :func:`app.load_backup_settings`.
    """
    try:
        if not file_path.exists():
            return False

        # Lazy imports (avoid circular). Prefer utils.backup_settings — importing
        # from `app` breaks when the runtime resolves `app` to a different package
        # (e.g. Flask app factory at /app/__init__.py).
        try:
            from utils.backup_settings import should_create_backup, load_backup_settings, cleanup_old_backups
        except Exception:
            from app import should_create_backup, load_backup_settings, cleanup_old_backups
        from app_context import get_backups_dir
        backups_dir = get_backups_dir()

        if not force:
            project_id = None
            projects_match = _re.search(r'/projects/([^/]+)/', str(file_path))
            if projects_match:
                project_id = projects_match.group(1)
            if project_id and not should_create_backup(file_path, project_id):
                return False

        timestamp = _datetime.now().strftime('%Y%m%d_%H%M%S')
        file_path_str = str(file_path)
        project_id = None
        projects_match = _re.search(r'/projects/([^/]+)/', file_path_str)
        if projects_match:
            project_id = projects_match.group(1)

        if 'group_vars' in file_path_str:
            backup_subdir = backups_dir / 'group_vars' / project_id if project_id else backups_dir / 'group_vars'
        elif 'host_vars' in file_path_str:
            backup_subdir = backups_dir / 'host_vars' / project_id if project_id else backups_dir / 'host_vars'
        elif 'inventory' in file_path_str:
            backup_subdir = backups_dir / 'inventory' / project_id if project_id else backups_dir / 'inventory'
        elif 'playbooks' in file_path_str or file_path.suffix == '.json':
            backup_subdir = backups_dir / 'playbooks' / project_id if project_id else backups_dir / 'playbooks'
        else:
            backup_subdir = backups_dir / 'other'

        backup_subdir.mkdir(parents=True, exist_ok=True)
        backup_file = backup_subdir / f"{file_path.stem}_{timestamp}{file_path.suffix}"
        _shutil.copy2(file_path, backup_file)

        if project_id:
            settings = load_backup_settings()
            max_depth = settings.get('max_depth', 100)
            cleanup_old_backups(backup_subdir, max_depth)

        return True
    except Exception as e:
        logger.error(f"Error creating backup for {file_path}: {e}")
        return False


def save_yaml_with_comments(file_path, data, create_backup_file=None):
    """Dump ``data`` to ``file_path`` preserving comments; optionally backup first.

    ``create_backup_file``: ``True`` = always backup, ``False`` = never,
    ``None`` = defer to :func:`app.should_create_backup`. Strips keys starting
    with ``_`` before writing. Validates the YAML and raises ``ValueError`` on
    structural errors.
    """
    try:
        if not data:
            data = {}
        clean_data = {k: v for k, v in data.items() if not k.startswith('_')}

        is_valid, error_msg = validate_yaml_content(clean_data)
        if not is_valid:
            logger.error(f"YAML validation failed for {file_path}: {error_msg}")
            raise ValueError(f"Invalid YAML: {error_msg}")

        should_backup = False
        if create_backup_file is True:
            should_backup = True
        elif create_backup_file is None:
            try:
                try:
                    from utils.backup_settings import should_create_backup
                except Exception:
                    from app import should_create_backup
                should_backup = should_create_backup(file_path)
            except Exception:
                should_backup = False

        if should_backup and file_path.exists():
            create_backup(file_path)

        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            yaml_loader.dump(clean_data, f)

        return True
    except ValueError:
        raise
    except Exception as e:
        logger.error(f"Error saving {file_path}: {e}")
        return False
