"""Users / Roles / Permissions IAM routes (Phase 3).

Moved from app.py:
  /api/users[/<id>][/roles]
  /api/roles[/<id>]
  /api/permissions[/<id>][/by-resource/<resource>]
"""
from __future__ import annotations

import sys

from flask import Blueprint, current_app, jsonify, request

from auth.middleware import require_auth
from auth.validators import (
    validate_email,
    validate_password,
    validate_permission_name,
    validate_role_name,
    validate_username,
)

bp = Blueprint("users_api", __name__)


def _svc():
    app_mod = next(
        (m for m in (sys.modules.get("__main__"), sys.modules.get("app")) if getattr(m, "user_service", None)),
        None,
    )
    return app_mod.user_service, app_mod.role_service, app_mod.permission_service


# =========================== USERS ===========================

@bp.route("/api/users", methods=["GET"])
@require_auth
def api_list_users():
    user_service, role_service, _ = _svc()
    try:
        users = user_service.get_all_users()
        users_data = [user.to_dict() for user in users]
        for user_data in users_data:
            role_names, role_details = [], []
            for role_id in user_data.get("roles", []):
                role = role_service.get_role_by_id(role_id)
                if role:
                    role_names.append(role.name)
                    role_details.append({"id": role.id, "name": role.name, "description": role.description})
            user_data["role_names"] = role_names
            user_data["role_details"] = role_details
        return jsonify({"success": True, "users": users_data})
    except Exception as e:
        current_app.logger.error(f"Error listing users: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error getting users list"}), 500


@bp.route("/api/users", methods=["POST"])
@require_auth
def api_create_user():
    user_service, role_service, _ = _svc()
    try:
        data = request.json or {}
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        email = (data.get("email") or "").strip() or None
        roles = data.get("roles") or []

        is_valid, error_msg = validate_username(username)
        if not is_valid:
            return jsonify({"success": False, "error": error_msg}), 400

        is_valid, error_msg = validate_password(password)
        if not is_valid:
            return jsonify({"success": False, "error": error_msg}), 400

        if email:
            is_valid, error_msg = validate_email(email)
            if not is_valid:
                return jsonify({"success": False, "error": error_msg}), 400

        role_ids = []
        if roles:
            for r in roles:
                rid = r.get("id", r) if isinstance(r, dict) else r
                rid = str(rid).strip() if rid is not None else None
                if not rid:
                    continue
                role = role_service.get_role_by_id(rid)
                if not role:
                    return jsonify({"success": False, "error": f"Role with ID {rid} not found"}), 400
                role_ids.append(rid)

        user = user_service.create_user(username=username, password=password, email=email, roles=role_ids)
        current_app.logger.info(f"User {username} created by {request.current_user.get('username')}")
        return jsonify({"success": True, "user": user.to_dict()}), 201
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        current_app.logger.error(f"Error creating user: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error creating user: " + str(e)}), 500


@bp.route("/api/users/<user_id>", methods=["GET"])
@require_auth
def api_get_user(user_id):
    user_service, role_service, _ = _svc()
    try:
        user = user_service.get_user_by_id(user_id)
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404
        user_data = user.to_dict()
        role_names, role_details = [], []
        for role_id in user.roles:
            role = role_service.get_role_by_id(role_id)
            if role:
                role_names.append(role.name)
                role_details.append({"id": role.id, "name": role.name, "description": role.description})
        user_data["role_names"] = role_names
        user_data["role_details"] = role_details
        return jsonify({"success": True, "user": user_data})
    except Exception as e:
        current_app.logger.error(f"Error getting user: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error getting user"}), 500


@bp.route("/api/users/<user_id>", methods=["PUT"])
@require_auth
def api_update_user(user_id):
    user_service, role_service, _ = _svc()
    try:
        data = request.json or {}
        username = (data.get("username") or "").strip() or None
        email = (data.get("email") or "").strip() or None
        roles = data.get("roles")
        is_active = data.get("is_active")
        current_password = data.get("current_password")
        new_password = data.get("password")

        current_user_id = request.current_user.get("user_id")
        if current_password is not None and new_password is not None:
            if not new_password or len(new_password) < 6:
                return jsonify({"success": False, "error": "New password must be at least 6 characters"}), 400
            ok = user_service.change_password(user_id, current_password, new_password)
            if not ok:
                return jsonify({"success": False, "error": "Current password is incorrect"}), 400
        elif new_password is not None:
            if user_id == current_user_id:
                return jsonify({"success": False, "error": "To change your own password, use Account settings and provide your current password"}), 400
            if not new_password or len(new_password) < 6:
                return jsonify({"success": False, "error": "New password must be at least 6 characters"}), 400
            ok = user_service.set_password(user_id, new_password)
            if not ok:
                return jsonify({"success": False, "error": "User not found"}), 404

        if username is not None:
            is_valid, error_msg = validate_username(username)
            if not is_valid:
                return jsonify({"success": False, "error": error_msg}), 400

        if email is not None:
            is_valid, error_msg = validate_email(email)
            if not is_valid:
                return jsonify({"success": False, "error": error_msg}), 400

        if roles is not None:
            for role_id in roles:
                role = role_service.get_role_by_id(role_id)
                if not role:
                    return jsonify({"success": False, "error": f"Role with ID {role_id} not found"}), 400

        user = user_service.update_user(
            user_id=user_id, username=username, email=email, roles=roles, is_active=is_active,
        )
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404

        current_app.logger.info(f"User {user.username} updated by {request.current_user.get('username')}")
        user_data = user.to_dict()
        role_names, role_details = [], []
        for role_id in user.roles:
            role = role_service.get_role_by_id(role_id)
            if role:
                role_names.append(role.name)
                role_details.append({"id": role.id, "name": role.name, "description": role.description})
        user_data["role_names"] = role_names
        user_data["role_details"] = role_details
        return jsonify({"success": True, "user": user_data})
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        current_app.logger.error(f"Error updating user: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error updating user"}), 500


@bp.route("/api/users/<user_id>", methods=["DELETE"])
@require_auth
def api_delete_user(user_id):
    user_service, _, _ = _svc()
    try:
        current_user_id = request.current_user.get("user_id")
        if user_id == current_user_id:
            return jsonify({"success": False, "error": "You cannot delete your own account"}), 400
        deleted = user_service.delete_user(user_id)
        if not deleted:
            return jsonify({"success": False, "error": "User not found"}), 404
        current_app.logger.info(f"User {user_id} deleted by {request.current_user.get('username')}")
        return jsonify({"success": True, "message": "User deleted successfully"})
    except Exception as e:
        current_app.logger.error(f"Error deleting user: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error deleting user"}), 500


@bp.route("/api/users/<user_id>/roles", methods=["POST"])
@require_auth
def api_assign_roles_to_user(user_id):
    user_service, role_service, _ = _svc()
    try:
        data = request.json or {}
        role_ids = data.get("roles", [])
        if not isinstance(role_ids, list):
            return jsonify({"success": False, "error": "Roles must be an array"}), 400
        user = user_service.get_user_by_id(user_id)
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404
        for role_id in role_ids:
            role = role_service.get_role_by_id(role_id)
            if not role:
                return jsonify({"success": False, "error": f"Role with ID {role_id} not found"}), 400
        user = user_service.update_user(user_id=user_id, roles=role_ids)
        if not user:
            return jsonify({"success": False, "error": "Error updating roles"}), 500
        current_app.logger.info(f"Roles assigned to user {user.username} by {request.current_user.get('username')}")
        role_names, role_details = [], []
        for role_id in user.roles:
            role = role_service.get_role_by_id(role_id)
            if role:
                role_names.append(role.name)
                role_details.append({"id": role.id, "name": role.name, "description": role.description})
        return jsonify({"success": True, "user": user.to_dict(), "role_names": role_names, "role_details": role_details})
    except Exception as e:
        current_app.logger.error(f"Error assigning roles to user: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error assigning roles"}), 500


# =========================== ROLES ===========================

def _role_with_perms(role, permission_service):
    role_dict = role.to_dict()
    permission_names, permission_details = [], []
    for perm_id in role.permissions:
        perm = permission_service.get_permission_by_id(perm_id)
        if perm:
            permission_names.append(perm.name)
            permission_details.append({
                "id": perm.id, "name": perm.name, "description": perm.description,
                "resource": perm.resource, "action": perm.action,
            })
    role_dict["permission_names"] = permission_names
    role_dict["permission_details"] = permission_details
    return role_dict


@bp.route("/api/roles", methods=["GET"])
@require_auth
def api_list_roles():
    _, role_service, permission_service = _svc()
    try:
        roles = role_service.get_all_roles()
        roles_data = [_role_with_perms(r, permission_service) for r in roles]
        return jsonify({"success": True, "roles": roles_data})
    except Exception as e:
        current_app.logger.error(f"Error listing roles: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error getting roles list"}), 500


@bp.route("/api/roles", methods=["POST"])
@require_auth
def api_create_role():
    _, role_service, permission_service = _svc()
    try:
        data = request.json or {}
        name = (data.get("name") or "").strip()
        description = (data.get("description") or "").strip() or ""
        raw_permissions = data.get("permissions") or []
        permissions = [str(p.get("id", p) if isinstance(p, dict) else p).strip()
                       for p in raw_permissions if p is not None]
        permissions = [p for p in permissions if p]

        is_valid, error_msg = validate_role_name(name)
        if not is_valid:
            return jsonify({"success": False, "error": error_msg}), 400

        if permissions:
            for perm_id in permissions:
                if not permission_service.get_permission_by_id(str(perm_id)):
                    return jsonify({"success": False, "error": f"Permission ID {perm_id} not found"}), 400

        role = role_service.create_role(name=name, description=description, permissions=permissions)
        current_app.logger.info(f"Role {name} created by {request.current_user.get('username')}")
        return jsonify({"success": True, "role": _role_with_perms(role, permission_service)}), 201
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        current_app.logger.error(f"Error creating role: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error creating role"}), 500


@bp.route("/api/roles/<role_id>", methods=["GET"])
@require_auth
def api_get_role(role_id):
    _, role_service, permission_service = _svc()
    try:
        role = role_service.get_role_by_id(role_id)
        if not role:
            return jsonify({"success": False, "error": "Role not found"}), 404
        return jsonify({"success": True, "role": _role_with_perms(role, permission_service)})
    except Exception as e:
        current_app.logger.error(f"Error getting role: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error getting role"}), 500


@bp.route("/api/roles/<role_id>", methods=["PUT"])
@require_auth
def api_update_role(role_id):
    _, role_service, permission_service = _svc()
    try:
        data = request.json or {}
        name = data.get("name", "").strip() if data.get("name") else None
        description = data.get("description", "").strip() if data.get("description") else None
        permissions = data.get("permissions")

        if name is not None:
            is_valid, error_msg = validate_role_name(name)
            if not is_valid:
                return jsonify({"success": False, "error": error_msg}), 400

        if permissions is not None:
            for perm_id in permissions:
                if not permission_service.get_permission_by_id(perm_id):
                    return jsonify({"success": False, "error": f"Permission ID {perm_id} not found"}), 400

        role = role_service.update_role(role_id=role_id, name=name, description=description, permissions=permissions)
        if not role:
            return jsonify({"success": False, "error": "Role not found"}), 404

        current_app.logger.info(f"Role {role.name} updated by {request.current_user.get('username')}")
        return jsonify({"success": True, "role": _role_with_perms(role, permission_service)})
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        current_app.logger.error(f"Error updating role: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error updating role"}), 500


@bp.route("/api/roles/<role_id>", methods=["DELETE"])
@require_auth
def api_delete_role(role_id):
    _, role_service, _ = _svc()
    try:
        deleted = role_service.delete_role(role_id)
        if not deleted:
            return jsonify({"success": False, "error": "Role not found"}), 404
        current_app.logger.info(f"Role {role_id} deleted by {request.current_user.get('username')}")
        return jsonify({"success": True, "message": "Role deleted"})
    except Exception as e:
        current_app.logger.error(f"Error deleting role: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error deleting role"}), 500


# =========================== PERMISSIONS ===========================

@bp.route("/api/permissions", methods=["GET"])
@require_auth
def api_list_permissions():
    _, _, permission_service = _svc()
    try:
        permissions = permission_service.get_all_permissions()
        return jsonify({"success": True, "permissions": [p.to_dict() for p in permissions]})
    except Exception as e:
        current_app.logger.error(f"Error listing permissions: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error getting permissions list"}), 500


@bp.route("/api/permissions", methods=["POST"])
@require_auth
def api_create_permission():
    _, _, permission_service = _svc()
    try:
        data = request.json or {}
        name = data.get("name", "").strip()
        description = data.get("description", "").strip()
        resource = data.get("resource", "").strip()
        action = data.get("action", "").strip()

        is_valid, error_msg = validate_permission_name(name)
        if not is_valid:
            return jsonify({"success": False, "error": error_msg}), 400
        if not resource:
            return jsonify({"success": False, "error": "Resource is required"}), 400
        if not action:
            return jsonify({"success": False, "error": "Action is required"}), 400

        permission = permission_service.create_permission(
            name=name, description=description, resource=resource, action=action,
        )
        current_app.logger.info(f"Permission {name} created by {request.current_user.get('username')}")
        return jsonify({"success": True, "permission": permission.to_dict()}), 201
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        current_app.logger.error(f"Error creating permission: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error creating permission"}), 500


@bp.route("/api/permissions/<perm_id>", methods=["GET"])
@require_auth
def api_get_permission(perm_id):
    _, _, permission_service = _svc()
    try:
        permission = permission_service.get_permission_by_id(perm_id)
        if not permission:
            return jsonify({"success": False, "error": "Permission not found"}), 404
        return jsonify({"success": True, "permission": permission.to_dict()})
    except Exception as e:
        current_app.logger.error(f"Error getting permission: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error getting permission"}), 500


@bp.route("/api/permissions/<perm_id>", methods=["PUT"])
@require_auth
def api_update_permission(perm_id):
    _, _, permission_service = _svc()
    try:
        data = request.json or {}
        name = data.get("name", "").strip() if data.get("name") else None
        description = data.get("description", "").strip() if data.get("description") else None
        resource = data.get("resource", "").strip() if data.get("resource") else None
        action = data.get("action", "").strip() if data.get("action") else None

        if name is not None:
            is_valid, error_msg = validate_permission_name(name)
            if not is_valid:
                return jsonify({"success": False, "error": error_msg}), 400

        permission = permission_service.update_permission(
            perm_id=perm_id, name=name, description=description, resource=resource, action=action,
        )
        if not permission:
            return jsonify({"success": False, "error": "Permission not found"}), 404

        current_app.logger.info(f"Permission {permission.name} updated by {request.current_user.get('username')}")
        return jsonify({"success": True, "permission": permission.to_dict()})
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        current_app.logger.error(f"Error updating permission: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error updating permission"}), 500


@bp.route("/api/permissions/<perm_id>", methods=["DELETE"])
@require_auth
def api_delete_permission(perm_id):
    _, _, permission_service = _svc()
    try:
        deleted = permission_service.delete_permission(perm_id)
        if not deleted:
            return jsonify({"success": False, "error": "Permission not found"}), 404
        current_app.logger.info(f"Permission {perm_id} deleted by {request.current_user.get('username')}")
        return jsonify({"success": True, "message": "Permission deleted"})
    except Exception as e:
        current_app.logger.error(f"Error deleting permission: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error deleting permission"}), 500


@bp.route("/api/permissions/by-resource/<resource>", methods=["GET"])
@require_auth
def api_get_permissions_by_resource(resource):
    _, _, permission_service = _svc()
    try:
        permissions = permission_service.get_permissions_by_resource(resource)
        return jsonify({"success": True, "permissions": [p.to_dict() for p in permissions]})
    except Exception as e:
        current_app.logger.error(f"Error getting permissions by resource: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error getting permissions"}), 500
