#!/usr/bin/env python3
"""
Authentication middleware for protecting API endpoints with JWT.

Security notes
--------------
* `INTERNAL_CALL_SECRET`: NEVER falls back to a known string. If unset, the
  module generates a random secret at process start and exposes it via
  `get_internal_call_secret()` so in-process callers (e.g. the host-status
  scheduler using `app.test_client()`) can still pass the check. External
  callers cannot know this value. In production you may also set it via env.
* Access tokens are accepted in the query string ONLY for a small allow-list
  of streaming endpoints (SSE), where the browser EventSource API cannot set
  Authorization headers.
"""
from functools import wraps
from flask import request, jsonify
from typing import Optional, Callable
from pathlib import Path
import logging
import os
import secrets as _secrets

try:
    from .auth import verify_token, get_token_from_header
except ImportError:
    from auth import verify_token, get_token_from_header

logger = logging.getLogger(__name__)


_data_dir: Optional[Path] = None
_access_control_service = None


# ---------------------------------------------------------------------------
# Internal-call secret
# ---------------------------------------------------------------------------
_env_internal = os.environ.get('INTERNAL_CALL_SECRET') or ''
_flask_env = (os.environ.get('FLASK_ENV') or '').lower()
_is_production = _flask_env == 'production'

if _env_internal and len(_env_internal) < 32:
    if _is_production:
        raise RuntimeError(
            "INTERNAL_CALL_SECRET must be at least 32 characters long in production."
        )
    logger.warning("INTERNAL_CALL_SECRET is shorter than 32 chars — insecure.")

if not _env_internal:
    # Random per-process value. In-process callers (test_client) read it via
    # get_internal_call_secret(); no external caller can guess it.
    _env_internal = _secrets.token_urlsafe(48)
    logger.info(
        "INTERNAL_CALL_SECRET not provided; using a random per-process value. "
        "External X-Internal-Call requests will be rejected."
    )

_INTERNAL_CALL_SECRET: str = _env_internal


def get_internal_call_secret() -> str:
    """Return the in-process internal-call secret (use only for app.test_client)."""
    return _INTERNAL_CALL_SECRET


# ---------------------------------------------------------------------------
# Query-string token allow-list (SSE endpoints only)
# ---------------------------------------------------------------------------
def _path_allows_query_token(path: str) -> bool:
    # Only GET endpoints that browsers consume via EventSource need this.
    return (
        '/stream' in path
        or '/sse' in path
        or '/events' in path
        or path.endswith('/logs')
    )


def set_data_dir(data_dir: Path):
    global _data_dir
    _data_dir = data_dir


def get_data_dir() -> Path:
    global _data_dir
    if _data_dir is None:
        _data_dir = Path(__file__).parent.parent.parent / 'data'
    return _data_dir


def set_access_control_service(access_control_service):
    global _access_control_service
    _access_control_service = access_control_service


def get_access_control_service():
    global _access_control_service
    if _access_control_service is None:
        try:
            from services.permission_service import AccessControlService
            _access_control_service = AccessControlService(get_data_dir())
        except ImportError:
            from services.permission_service import AccessControlService
            _access_control_service = AccessControlService(get_data_dir())
    return _access_control_service


def require_auth(f: Callable) -> Callable:
    """Require valid JWT, worker token, or API token."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # In-process internal call (scheduler, background jobs via test_client).
        provided_internal = request.headers.get('X-Internal-Call')
        if provided_internal and _secrets.compare_digest(
            provided_internal, _INTERNAL_CALL_SECRET
        ):
            request.current_user = {
                'username': 'internal',
                'user_id': '__internal__',
                'roles': ['admin'],
            }
            request.token = None
            return f(*args, **kwargs)

        auth_header = request.headers.get('Authorization')
        token = get_token_from_header(auth_header)
        # Query-string token only for SSE-style streaming endpoints.
        if not token and request.method == 'GET' and _path_allows_query_token(request.path):
            token = request.args.get('access_token') or request.args.get('token')
        if not token:
            return jsonify({
                'error': 'Authentication required',
                'message': 'Access token missing',
            }), 401

        try:
            from services.worker_registry import verify_token as verify_worker_token
        except ImportError:
            from services.worker_registry import verify_token as verify_worker_token
        is_worker_path = request.path.startswith('/api/worker/')
        is_execution_get = request.path.startswith('/api/executions/') and request.method == 'GET'
        if is_worker_path or is_execution_get:
            result = verify_worker_token(token)
            if result:
                worker_id, _ = result
                request.current_user = {
                    'worker_id': worker_id,
                    'username': f'worker:{worker_id[:8]}',
                    'roles': [],
                }
                request.token = token
                return f(*args, **kwargs)
            if is_worker_path:
                return jsonify({
                    'error': 'Invalid token',
                    'message': 'Worker token is invalid or expired. Re-register the worker or set WORKER_TOKEN.',
                }), 401

        data_dir = get_data_dir()
        payload = verify_token(token, data_dir, token_type='access')
        if payload:
            request.current_user = {
                'user_id': payload.get('user_id'),
                'username': payload.get('username'),
                'roles': payload.get('roles', []),
            }
            request.token = token
            return f(*args, **kwargs)

        # API token (long-lived, programmatic)
        try:
            from storage.api_tokens_store import verify_api_token
        except ImportError:
            from storage.api_tokens_store import verify_api_token
        api_result = verify_api_token(token)
        if api_result:
            api_user_id, token_entry = api_result
            roles = []
            try:
                acs = get_access_control_service()
                if hasattr(acs, 'get_user_roles'):
                    roles = acs.get_user_roles(api_user_id) or []
            except Exception:
                pass
            request.current_user = {
                'user_id': api_user_id,
                'username': token_entry.get('username', f'api:{api_user_id[:8]}'),
                'roles': roles,
            }
            request.token = token
            return f(*args, **kwargs)

        return jsonify({
            'error': 'Invalid token',
            'message': 'Access token is invalid or expired',
        }), 401
    return decorated_function


def require_optional_auth(f: Callable) -> Callable:
    """Auth is optional; current_user is None when missing or invalid."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        token = get_token_from_header(auth_header)
        if not token:
            request.current_user = None
            request.token = None
            return f(*args, **kwargs)
        data_dir = get_data_dir()
        payload = verify_token(token, data_dir, token_type='access')
        if not payload:
            request.current_user = None
            request.token = None
            return f(*args, **kwargs)
        request.current_user = {
            'user_id': payload.get('user_id'),
            'username': payload.get('username'),
            'roles': payload.get('roles', []),
        }
        request.token = token
        return f(*args, **kwargs)
    return decorated_function


def require_permission(permission_name: str):
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        @require_auth
        def decorated_function(*args, **kwargs):
            user_id = request.current_user.get('user_id')
            if not user_id:
                return jsonify({
                    'error': 'Authentication error',
                    'message': 'Failed to identify user',
                }), 401
            access_control = get_access_control_service()
            if not access_control.has_permission(user_id, permission_name):
                logger.warning(
                    f"User {request.current_user.get('username')} (ID: {user_id}) "
                    f"denied access to {permission_name}"
                )
                return jsonify({
                    'error': 'Access denied',
                    'message': f'Insufficient permissions. Required: {permission_name}',
                }), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def require_any_role(*role_names: str):
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        @require_auth
        def decorated_function(*args, **kwargs):
            user_roles = request.current_user.get('roles', [])
            if not any(role in role_names for role in user_roles):
                return jsonify({
                    'error': 'Access denied',
                    'message': f'One of the following roles is required: {", ".join(role_names)}',
                }), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def require_all_roles(*role_names: str):
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        @require_auth
        def decorated_function(*args, **kwargs):
            user_roles = request.current_user.get('roles', [])
            if not all(role in user_roles for role in role_names):
                return jsonify({
                    'error': 'Access denied',
                    'message': f'All of the following roles are required: {", ".join(role_names)}',
                }), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator
