"""Audit log read routes.

Backed by ``storage.auth_db.audit_log`` — the append-only trail written by
UserService / RoleService / PermissionService mutations. Read-only endpoint
for admins to review who did what and when.

Endpoints:
    GET /api/audit-log?limit=&target_type=&target_id=&actor_user_id=
"""
from __future__ import annotations

import sys

from flask import Blueprint, current_app, jsonify, request

from auth.middleware import require_auth

try:
    from storage import auth_db
except ImportError:  # pragma: no cover
    from backend.storage import auth_db  # type: ignore

bp = Blueprint("audit_log_api", __name__)


def _data_dir():
    app_mod = next(
        (m for m in (sys.modules.get("__main__"), sys.modules.get("app")) if getattr(m, "DATA_DIR", None)),
        None,
    )
    return app_mod.DATA_DIR if app_mod else None


@bp.route("/api/audit-log", methods=["GET"])
@require_auth
def api_list_audit_log():
    data_dir = _data_dir()
    if not data_dir:
        return jsonify({"success": False, "error": "data dir unavailable"}), 500
    try:
        limit = max(1, min(int(request.args.get("limit", 100)), 1000))
    except ValueError:
        limit = 100
    try:
        entries = auth_db.list_audit(
            data_dir,
            limit=limit,
            target_type=request.args.get("target_type") or None,
            target_id=request.args.get("target_id") or None,
            actor_user_id=request.args.get("actor_user_id") or None,
        )
        return jsonify({"success": True, "entries": entries, "count": len(entries)})
    except Exception as e:
        current_app.logger.error(f"Error listing audit log: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error reading audit log"}), 500
