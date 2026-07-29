"""Filesystem path helpers.

Extracted from app.py during Phase 1 of the refactor. Contains only
pure, dependency-free helpers. Higher-level helpers that depend on
project state (get_project_dir, resolve_repo_path, etc.) still live
in app.py until Phase 2 introduces the shared context.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Tuple


def slugify_secret_name(name):
    """Sanitize a user-supplied secret name for use as a filesystem name.

    Strips anything outside ``[A-Za-z0-9_-]``, rejects traversal, and
    enforces a 100-character limit. Raises ``ValueError`` on invalid input.
    """
    if not name or not isinstance(name, str):
        raise ValueError("Secret name must be a non-empty string")
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '', name)
    if '..' in safe_name or '/' in safe_name or '\\' in safe_name:
        raise ValueError("Secret name contains invalid characters")
    if not safe_name:
        raise ValueError("Secret name cannot be empty after sanitization")
    if len(safe_name) > 100:
        raise ValueError("Secret name is too long (max 100 characters)")
    return safe_name


def safe_child_path(base_dir, user_segment, suffix: str = ''):
    """Join ``base_dir / (user_segment + suffix)`` refusing traversal.

    Use for any filesystem path built from URL params, request body, or
    other untrusted input. Raises ``ValueError`` on traversal attempts
    (``..``, absolute paths, ``/`` or ``\\`` in the segment, NUL bytes,
    or a resolved path that escapes ``base_dir``).
    """
    if user_segment is None:
        raise ValueError("path segment is required")
    seg = str(user_segment)
    if not seg or '\x00' in seg or '/' in seg or '\\' in seg or seg in ('.', '..'):
        raise ValueError(f"invalid path segment: {seg!r}")
    base_resolved = Path(base_dir).resolve()
    candidate = (base_resolved / f"{seg}{suffix}").resolve()
    try:
        candidate.relative_to(base_resolved)
    except ValueError:
        raise ValueError(f"path traversal blocked: {seg!r}")
    return candidate


def validate_repo_layout_path(path: str) -> Tuple[bool, Optional[str]]:
    """Validate a user-supplied repo layout path.

    Rules:
      * Must be relative (not absolute).
      * Cannot contain ``..`` (path traversal).
      * Cannot start or end with ``/``.
      * Cannot be empty.
      * Rejects Windows-style absolute paths (``C:...``).
    """
    if not path:
        return False, 'Path cannot be empty'

    if Path(path).is_absolute():
        return False, 'Path must be relative, not absolute'

    if '..' in path:
        return False, 'Path cannot contain .. (path traversal)'

    if path.startswith('/') or path.endswith('/'):
        return False, 'Path cannot start or end with /'

    if len(path) >= 2 and path[1] == ':':
        return False, 'Path must be relative, not absolute (Windows path detected)'

    return True, None
