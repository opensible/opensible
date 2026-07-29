"""Request-context helpers.

Extracted from app.py during Phase 1 of the refactor. Uses only Flask's
``request`` object plus a caller-supplied default-project resolver.
"""
from __future__ import annotations

from typing import Callable, Optional

from flask import request


def project_id_from_request_sources() -> Optional[str]:
    """Resolve ``project_id`` from header (preferred), query string, or JSON body."""
    project_id = request.headers.get('X-Project-Id') or request.headers.get('x-project-id')
    if not project_id:
        project_id = request.args.get('project_id')
    if not project_id and request.is_json:
        json_data = request.get_json(silent=True)
        if json_data:
            project_id = json_data.get('project_id')
    return project_id


def get_project_id_from_request(default_project_resolver: Callable[[], Optional[str]]) -> Optional[str]:
    """Return the request's ``project_id`` or fall back to the default project.

    ``default_project_resolver`` is invoked only when no project id is present
    on the request. WARNING: prefer :func:`require_project_id_from_request`
    for project-scoped endpoints — silent fallback to the default project can
    mask missing-scoping bugs.
    """
    project_id = project_id_from_request_sources()
    if not project_id:
        project_id = default_project_resolver()
    return project_id


def require_project_id_from_request() -> Optional[str]:
    """Return the request's ``project_id`` or ``None`` — no fallback.

    Callers should treat ``None`` as a client error (missing scoping).
    """
    return project_id_from_request_sources()
