"""Unit and integration tests for User & IAM authorization enforcement."""
from __future__ import annotations

import pytest
from flask import Flask
from auth.service import generate_token
from auth.middleware import get_internal_call_secret


@pytest.fixture
def test_app(data_dir):
    """Create test Flask application with auth and API blueprints registered."""
    import app as main_app_mod
    app = main_app_mod.app
    app.config["TESTING"] = True
    return app


@pytest.fixture
def admin_token(data_dir):
    return generate_token(
        user_id="user-admin-999",
        username="admin_alice",
        roles=["admin"],
        data_dir=data_dir,
    )


@pytest.fixture
def viewer_token(data_dir):
    return generate_token(
        user_id="user-viewer-100",
        username="viewer_bob",
        roles=["viewer"],
        data_dir=data_dir,
    )


# ---------------------------------------------------------------------------
# 1. User Management Endpoint Authorization Tests
# ---------------------------------------------------------------------------

def test_api_list_users_non_admin_forbidden(test_app, viewer_token):
    with test_app.test_client() as client:
        res = client.get(
            "/api/users",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert res.status_code == 403
        data = res.get_json()
        assert data["success"] is False
        assert "Permission denied" in data["error"] or "Permission denied" in data.get("message", "")


def test_api_list_users_admin_allowed(test_app, admin_token):
    with test_app.test_client() as client:
        res = client.get(
            "/api/users",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert "users" in data


def test_api_create_user_non_admin_forbidden(test_app, viewer_token):
    with test_app.test_client() as client:
        res = client.post(
            "/api/users",
            headers={"Authorization": f"Bearer {viewer_token}"},
            json={"username": "hacker", "password": "Password123!"},
        )
        assert res.status_code == 403


def test_api_get_other_user_non_admin_forbidden(test_app, viewer_token):
    with test_app.test_client() as client:
        res = client.get(
            "/api/users/user-admin-999",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert res.status_code == 403


def test_api_get_self_user_allowed(test_app, viewer_token):
    with test_app.test_client() as client:
        res = client.get(
            "/api/users/user-viewer-100",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert res.status_code in (200, 404)


def test_api_update_user_non_admin_role_escalation_forbidden(test_app, viewer_token):
    with test_app.test_client() as client:
        res = client.put(
            "/api/users/user-viewer-100",
            headers={"Authorization": f"Bearer {viewer_token}"},
            json={"roles": ["admin"]},
        )
        assert res.status_code == 403


def test_api_delete_user_non_admin_forbidden(test_app, viewer_token):
    with test_app.test_client() as client:
        res = client.delete(
            "/api/users/user-admin-999",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert res.status_code == 403


def test_api_assign_roles_non_admin_forbidden(test_app, viewer_token):
    with test_app.test_client() as client:
        res = client.post(
            "/api/users/user-viewer-100/roles",
            headers={"Authorization": f"Bearer {viewer_token}"},
            json={"roles": ["admin"]},
        )
        assert res.status_code == 403


# ---------------------------------------------------------------------------
# 2. Roles & Permissions Endpoint Authorization Tests
# ---------------------------------------------------------------------------

def test_api_create_role_non_admin_forbidden(test_app, viewer_token):
    with test_app.test_client() as client:
        res = client.post(
            "/api/roles",
            headers={"Authorization": f"Bearer {viewer_token}"},
            json={"name": "super_admin"},
        )
        assert res.status_code == 403


def test_api_create_permission_non_admin_forbidden(test_app, viewer_token):
    with test_app.test_client() as client:
        res = client.post(
            "/api/permissions",
            headers={"Authorization": f"Bearer {viewer_token}"},
            json={"name": "root.access", "resource": "all", "action": "all"},
        )
        assert res.status_code == 403


# ---------------------------------------------------------------------------
# 3. Audit Log & Settings Authorization Tests
# ---------------------------------------------------------------------------

def test_api_audit_log_non_admin_forbidden(test_app, viewer_token):
    with test_app.test_client() as client:
        res = client.get(
            "/api/audit-log",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert res.status_code == 403


def test_api_audit_log_admin_allowed(test_app, admin_token):
    with test_app.test_client() as client:
        res = client.get(
            "/api/audit-log",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200


def test_api_scheduler_settings_put_non_admin_forbidden(test_app, viewer_token):
    with test_app.test_client() as client:
        res = client.put(
            "/api/scheduler_settings",
            headers={"Authorization": f"Bearer {viewer_token}"},
            json={"host_check_interval": 10},
        )
        assert res.status_code == 403
