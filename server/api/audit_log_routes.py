"""Audit log inspection and read routes.

Backed by ``storage.auth_db.audit_log`` — the append-only trail written by
UserService / RoleService / PermissionService mutations. Read-only endpoints
for admins to review who did what and when.

Endpoints:
    GET /api/audit-log?limit=&offset=&target_type=&target_id=&actor_user_id=&action=&since=&until=
    GET /api/audit-log/summary
    GET /api/audit-log/<int:entry_id>
"""
from __future__ import annotations

import sys

from flask import Blueprint, current_app, jsonify, request

try:
    from auth.middleware import require_admin, require_auth
except ImportError:  # pragma: no cover
    from ..auth.middleware import require_admin, require_auth

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
@require_admin
def api_list_audit_log():
    data_dir = _data_dir()
    if not data_dir:
        return jsonify({"success": False, "error": "data dir unavailable"}), 500
    try:
        limit = max(1, min(int(request.args.get("limit", 100)), 1000))
    except ValueError:
        limit = 100
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        offset = 0

    try:
        entries = auth_db.list_audit(
            data_dir,
            limit=limit,
            offset=offset,
            target_type=request.args.get("target_type") or None,
            target_id=request.args.get("target_id") or None,
            actor_user_id=request.args.get("actor_user_id") or None,
            action=request.args.get("action") or None,
            since=request.args.get("since") or None,
            until=request.args.get("until") or None,
        )
        return jsonify({
            "success": True,
            "entries": entries,
            "count": len(entries),
            "limit": limit,
            "offset": offset,
        })
    except Exception as e:
        current_app.logger.error(f"Error listing audit log: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error reading audit log"}), 500


@bp.route("/api/audit-log/summary", methods=["GET"])
@require_admin
def api_get_audit_log_summary():
    data_dir = _data_dir()
    if not data_dir:
        return jsonify({"success": False, "error": "data dir unavailable"}), 500
    try:
        summary = auth_db.get_audit_summary(data_dir)
        return jsonify({"success": True, "summary": summary})
    except Exception as e:
        current_app.logger.error(f"Error fetching audit log summary: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error building audit log summary"}), 500


@bp.route("/api/audit-log/<int:entry_id>", methods=["GET"])
@require_admin
def api_get_audit_log_entry(entry_id: int):
    data_dir = _data_dir()
    if not data_dir:
        return jsonify({"success": False, "error": "data dir unavailable"}), 500
    try:
        entry = auth_db.get_audit_by_id(data_dir, entry_id)
        if not entry:
            return jsonify({"success": False, "error": "Audit log entry not found"}), 404
        return jsonify({"success": True, "entry": entry})
    except Exception as e:
        current_app.logger.error(f"Error reading audit log entry {entry_id}: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error reading audit log entry"}), 500

