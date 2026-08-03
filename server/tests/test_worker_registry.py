"""Unit tests for worker registration, token verify, and heartbeat."""
from __future__ import annotations

import time


def test_create_worker_returns_id_and_plaintext_token(workers_env):
    wr = workers_env
    worker_id, token = wr.create_worker(name="ci-worker", tags=["go"])
    assert worker_id
    assert token
    loaded = wr.load_worker(worker_id)
    assert loaded is not None
    assert loaded["name"] == "ci-worker"
    assert "token" not in loaded
    assert loaded["tokenHash"]
    assert loaded["tokenSalt"]


def test_verify_token_roundtrip(workers_env):
    wr = workers_env
    worker_id, token = wr.create_worker(name="verify-me")
    result = wr.verify_token(token)
    assert result is not None
    got_id, data = result
    assert got_id == worker_id
    assert data["name"] == "verify-me"


def test_verify_token_rejects_unknown(workers_env):
    wr = workers_env
    assert wr.verify_token("totally-unknown-token") is None
    assert wr.verify_token("") is None


def test_disabled_worker_token_rejected(workers_env):
    wr = workers_env
    worker_id, token = wr.create_worker(name="soon-disabled")
    assert wr.verify_token(token) is not None
    assert wr.disable_worker(worker_id) is True
    assert wr.verify_token(token) is None
    assert wr.enable_worker(worker_id) is True
    assert wr.verify_token(token) is not None


def test_rotate_token_invalidates_old(workers_env):
    wr = workers_env
    worker_id, old_token = wr.create_worker(name="rotate-me")
    new_token = wr.rotate_worker_token(worker_id)
    assert new_token != old_token
    assert wr.verify_token(old_token) is None
    assert wr.verify_token(new_token) is not None


def test_heartbeat_marks_online(workers_env):
    wr = workers_env
    worker_id, _ = wr.create_worker(name="hb")
    assert wr.is_worker_online(worker_id, ttl_seconds=60) is False
    assert wr.update_worker_heartbeat(worker_id) is True
    assert wr.is_worker_online(worker_id, ttl_seconds=60) is True


def test_heartbeat_can_clear_current_execution(workers_env):
    wr = workers_env
    worker_id, _ = wr.create_worker(name="clear-exec")
    assert wr.update_worker_heartbeat(worker_id, current_execution_id="exec-1")
    assert wr.load_worker(worker_id)["currentExecutionId"] == "exec-1"
    # Force a write past the coalesce window.
    wr._HEARTBEAT_WRITE_CACHE.pop(worker_id, None)
    assert wr.update_worker_heartbeat(worker_id, current_execution_id=None)
    assert wr.load_worker(worker_id)["currentExecutionId"] is None


def test_stale_workers(workers_env):
    wr = workers_env
    worker_id, _ = wr.create_worker(name="stale")
    data = wr.load_worker(worker_id)
    data["lastSeenAt"] = time.time() - 9999
    wr.save_worker(data)
    assert worker_id in wr.get_stale_workers(max_age_seconds=60)
