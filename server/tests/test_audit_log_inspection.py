"""Unit and integration tests for Audit Log Inspection API endpoints and storage helpers."""
from __future__ import annotations

import pytest
from auth.service import generate_token
from storage import auth_db


@pytest.fixture
def admin_token(data_dir):
    return generate_token(
        user_id="admin-user-001",
        username="admin_alice",
        roles=["admin"],
        data_dir=data_dir,
    )


@pytest.fixture
def viewer_token(data_dir):
    return generate_token(
        user_id="viewer-user-002",
        username="viewer_bob",
        roles=["viewer"],
        data_dir=data_dir,
    )


@pytest.fixture
def seeded_audit_logs(data_dir):
    """Seed sample audit log entries for testing."""
    auth_db.audit(
        data_dir,
        action="user.create",
        target_type="user",
        target_id="usr-101",
        actor_user_id="admin-user-001",
        meta={"username": "john_doe"},
    )
    auth_db.audit(
        data_dir,
        action="user.delete",
        target_type="user",
        target_id="usr-102",
        actor_user_id="admin-user-001",
        meta={"reason": "cleanup"},
    )
    auth_db.audit(
        data_dir,
        action="role.assign",
        target_type="role",
        target_id="role-admin",
        actor_user_id="admin-user-001",
        meta={"role": "admin"},
    )


def test_auth_db_audit_helpers_direct(data_dir, seeded_audit_logs):
    # Test list_audit with action filter
    user_creates = auth_db.list_audit(data_dir, action="user.create")
    assert len(user_creates) == 1
    assert user_creates[0]["target_id"] == "usr-101"
    assert user_creates[0]["meta"] == {"username": "john_doe"}

    # Test get_audit_by_id
    first_entry = user_creates[0]
    entry = auth_db.get_audit_by_id(data_dir, first_entry["id"])
    assert entry is not None
    assert entry["id"] == first_entry["id"]
    assert entry["action"] == "user.create"

    # Test get_audit_by_id non-existent
    assert auth_db.get_audit_by_id(data_dir, 999999) is None

    # Test get_audit_summary
    summary = auth_db.get_audit_summary(data_dir)
    assert summary["total_entries"] == 3
    assert summary["actions"]["user.create"] == 1
    assert summary["actions"]["user.delete"] == 1
    assert summary["actions"]["role.assign"] == 1
    assert summary["unique_actors"] == 1


def test_api_list_audit_log_filtered(test_app, admin_token, seeded_audit_logs):
    with test_app.test_client() as client:
        res = client.get(
            "/api/audit-log?action=user.delete",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert data["count"] == 1
        assert data["entries"][0]["action"] == "user.delete"
        assert data["entries"][0]["target_id"] == "usr-102"


def test_api_list_audit_log_pagination(test_app, admin_token, seeded_audit_logs):
    with test_app.test_client() as client:
        res = client.get(
            "/api/audit-log?limit=2&offset=1",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert len(data["entries"]) == 2
        assert data["limit"] == 2
        assert data["offset"] == 1


def test_api_get_audit_log_entry_success(test_app, admin_token, seeded_audit_logs):
    with test_app.test_client() as client:
        # First list to get a valid entry ID
        res_list = client.get(
            "/api/audit-log",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        entries = res_list.get_json()["entries"]
        entry_id = entries[0]["id"]

        # Fetch single entry
        res = client.get(
            f"/api/audit-log/{entry_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert data["entry"]["id"] == entry_id


def test_api_get_audit_log_entry_not_found(test_app, admin_token):
    with test_app.test_client() as client:
        res = client.get(
            "/api/audit-log/999999",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 404
        data = res.get_json()
        assert data["success"] is False
        assert "not found" in data["error"].lower()


def test_api_get_audit_log_summary(test_app, admin_token, seeded_audit_logs):
    with test_app.test_client() as client:
        res = client.get(
            "/api/audit-log/summary",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        summary = data["summary"]
        assert summary["total_entries"] >= 3
        assert "user.create" in summary["actions"]


def test_api_audit_log_inspection_non_admin_forbidden(test_app, viewer_token):
    with test_app.test_client() as client:
        # List forbidden
        res = client.get(
            "/api/audit-log",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert res.status_code == 403

        # Single entry inspection forbidden
        res = client.get(
            "/api/audit-log/1",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert res.status_code == 403

        # Summary forbidden
        res = client.get(
            "/api/audit-log/summary",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert res.status_code == 403
