"""Unit tests for worker claim requirement matching (queue eligibility)."""
from __future__ import annotations

from services.execution_dispatcher import _check_execution_requirements


def _exec(requirements=None, target_worker_id=None):
    run_params = {"requirements": requirements or {}}
    if target_worker_id is not None:
        run_params["target_worker_id"] = target_worker_id
    return {"runParams": run_params}


def test_no_requirements_always_matches():
    assert _check_execution_requirements(_exec(), {}, [], worker_id="w1") is True


def test_worker_id_pin():
    execution = _exec(requirements={"worker_id": "w-target"})
    assert _check_execution_requirements(execution, {}, [], worker_id="w-target") is True
    assert _check_execution_requirements(execution, {}, [], worker_id="w-other") is False


def test_target_worker_id_alias():
    execution = _exec(target_worker_id="w-pinned")
    assert _check_execution_requirements(execution, {}, [], worker_id="w-pinned") is True
    assert _check_execution_requirements(execution, {}, [], worker_id="w-other") is False


def test_required_tags_must_be_subset():
    execution = _exec(requirements={"tags": ["go", "prod"]})
    assert _check_execution_requirements(
        execution, {}, ["go", "prod", "extra"], worker_id="w1"
    ) is True
    assert _check_execution_requirements(
        execution, {}, ["go"], worker_id="w1"
    ) is False


def test_required_capabilities_must_match():
    execution = _exec(requirements={"capabilities": {"opentofu": True, "arch": "amd64"}})
    assert _check_execution_requirements(
        execution,
        {"opentofu": True, "arch": "amd64"},
        [],
        worker_id="w1",
    ) is True
    assert _check_execution_requirements(
        execution,
        {"opentofu": True, "arch": "arm64"},
        [],
        worker_id="w1",
    ) is False
