"""Auth routes blueprint (Phase 2).

Moved from app.py: /api/auth/login, /logout, /refresh, /me.
"""
from __future__ import annotations

import threading
import time

from flask import Blueprint, current_app, jsonify, request

from auth import add_token_to_blacklist, generate_token, verify_token
from auth.middleware import require_auth
from auth.validators import validate_username

bp = Blueprint("auth_api", __name__)

# --- Login brute-force rate limiting (module-local state) -------------------
_login_attempts: dict = {}
_login_attempts_lock = threading.Lock()
_LOGIN_WINDOW_SECONDS = 60
_LOGIN_MAX_FAILURES = 5


def _login_rate_limit_allow(key: str) -> bool:
    now = time.time()
    cutoff = now - _LOGIN_WINDOW_SECONDS
    with _login_attempts_lock:
        arr = [t for t in _login_attempts.get(key, []) if t > cutoff]
        _login_attempts[key] = arr
        return len(arr) < _LOGIN_MAX_FAILURES


def _login_rate_limit_record_failure(key: str) -> None:
    with _login_attempts_lock:
        _login_attempts.setdefault(key, []).append(time.time())


def _login_rate_limit_reset(key: str) -> None:
    with _login_attempts_lock:
        _login_attempts.pop(key, None)


def _services():
    """Pull singletons from app.py at call-time (avoids circular import)."""
    import sys
    app_mod = next(
        (m for m in (sys.modules.get("__main__"), sys.modules.get("app")) if getattr(m, "user_service", None)),
        None,
    )
    return (
        app_mod.user_service,
        app_mod.role_service,
        app_mod.access_control_service,
        app_mod.DATA_DIR,
    )


@bp.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    user_service, role_service, _, DATA_DIR = _services()
    try:
        data = request.json or {}
        username = data.get("username", "").strip()
        password = data.get("password", "")

        client_ip = (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                     or request.remote_addr or "unknown")
        rl_key = f"{client_ip}|{username.lower()}"
        if not _login_rate_limit_allow(rl_key):
            current_app.logger.warning(f"Login rate limit hit for {rl_key}")
            return jsonify({"success": False, "error": "Too many login attempts. Please wait a minute and try again."}), 429

        is_valid, error_msg = validate_username(username)
        if not is_valid:
            _login_rate_limit_record_failure(rl_key)
            return jsonify({"success": False, "error": error_msg}), 400

        if not password or not isinstance(password, str):
            _login_rate_limit_record_failure(rl_key)
            return jsonify({"success": False, "error": "Password is required"}), 400

        user = user_service.authenticate(username, password)
        if not user:
            _login_rate_limit_record_failure(rl_key)
            current_app.logger.warning(f"Failed login attempt for username: {username} from {client_ip}")
            return jsonify({"success": False, "error": "Incorrect username or password"}), 401

        _login_rate_limit_reset(rl_key)

        role_names = []
        for role_id in user.roles:
            role = role_service.get_role_by_id(role_id)
            if role:
                role_names.append(role.name)

        access_token = generate_token(user_id=user.id, username=user.username, roles=role_names,
                                      data_dir=DATA_DIR, token_type="access")
        refresh_token = generate_token(user_id=user.id, username=user.username, roles=role_names,
                                       data_dir=DATA_DIR, token_type="refresh")

        current_app.logger.info(f"User {username} logged in successfully")
        return jsonify({
            "success": True,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "user": {"id": user.id, "username": user.username, "email": user.email, "roles": role_names},
        })
    except Exception as e:
        current_app.logger.error(f"Error in login: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Login error"}), 500


@bp.route("/api/auth/logout", methods=["POST"])
@require_auth
def api_auth_logout():
    _, _, _, DATA_DIR = _services()
    try:
        token = request.token
        if token:
            add_token_to_blacklist(DATA_DIR, token)
            current_app.logger.info(f"User {request.current_user.get('username')} logged out")
        return jsonify({"success": True, "message": "Logged out"})
    except Exception as e:
        current_app.logger.error(f"Error in logout: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Logout error"}), 500


@bp.route("/api/auth/refresh", methods=["POST"])
def api_auth_refresh():
    user_service, role_service, _, DATA_DIR = _services()
    try:
        data = request.json or {}
        refresh_token = data.get("refresh_token", "").strip()
        if not refresh_token:
            return jsonify({"success": False, "error": "Refresh token required"}), 400

        payload = verify_token(refresh_token, DATA_DIR, token_type="refresh")
        if not payload:
            return jsonify({"success": False, "error": "Invalid refresh token"}), 401

        user_id = payload.get("user_id")
        user = user_service.get_user_by_id(user_id)
        if not user or not user.is_active:
            return jsonify({"success": False, "error": "User not found or inactive"}), 401

        role_names = []
        for role_id in user.roles:
            role = role_service.get_role_by_id(role_id)
            if role:
                role_names.append(role.name)

        access_token = generate_token(user_id=user.id, username=user.username, roles=role_names,
                                      data_dir=DATA_DIR, token_type="access")
        return jsonify({"success": True, "access_token": access_token})
    except Exception as e:
        current_app.logger.error(f"Error in refresh: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Token refresh error"}), 500


@bp.route("/api/auth/me", methods=["GET"])
@require_auth
def api_auth_me():
    user_service, role_service, access_control_service, _ = _services()
    try:
        user_id = request.current_user.get("user_id")
        user = user_service.get_user_by_id(user_id)
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404

        role_names, role_details = [], []
        for role_id in user.roles:
            role = role_service.get_role_by_id(role_id)
            if role:
                role_names.append(role.name)
                role_details.append({"id": role.id, "name": role.name, "description": role.description})

        permissions = access_control_service.get_user_permissions(user_id)
        return jsonify({
            "success": True,
            "user": {
                "id": user.id, "username": user.username, "email": user.email,
                "roles": role_names, "role_details": role_details,
                "permissions": list(permissions),
                "is_active": user.is_active,
                "created_at": user.created_at, "last_login": user.last_login,
            },
        })
    except Exception as e:
        current_app.logger.error(f"Error in /api/auth/me: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error getting user information"}), 500
