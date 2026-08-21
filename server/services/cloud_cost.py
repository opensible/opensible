"""Cost estimation on plan — ships the pricing catalog to the worker.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from flask import jsonify, request

try:  # storage is importable both as package and flat module
    from storage import cost_store
except Exception:  # pragma: no cover
    from backend.storage import cost_store  # type: ignore


HOURS_PER_MONTH = 730.0


def cost_enabled_from_meta(meta: Dict[str, Any]) -> bool:
    """Cost estimation is opt-out: enabled unless explicitly disabled."""
    return (meta or {}).get("cost_estimate_enabled") is not False


def cost_config_from_meta(meta: Dict[str, Any], provider: str) -> Dict[str, Any]:
    """Payload shipped to the worker for `plan`/`apply`/`destroy` runs."""
    provider = (provider or (meta or {}).get("provider") or "bytedc").lower()
    if provider not in cost_store.SUPPORTED_PROVIDERS:
        provider = "bytedc"
    catalog = cost_store.get_pricing(provider)
    return {
        "provider": provider,
        "currency": catalog.get("currency", "USD"),
        "hours_per_month": HOURS_PER_MONTH,
        "pricing": {
            "compute": catalog.get("compute", {}),
            "storage": catalog.get("storage", {}),
            "network": catalog.get("network", {}),
            "managed": catalog.get("managed", {}),
        },
        "catalog_version": catalog.get("version"),
    }


def latest_cost_result(ex_dir: Path, name: str) -> Optional[Dict[str, Any]]:
    """Most recent cost estimate recorded by a worker for this stack."""
    if not ex_dir or not ex_dir.exists():
        return None
    files = sorted(ex_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files[:300]:
        try:
            exe = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        rp = exe.get("runParams") or {}
        if rp.get("execution_type") != "TOFU_RUN" or rp.get("stack_name") != name:
            continue
        res = exe.get("result") or exe.get("stats") or {}
        cost = res.get("cost") if isinstance(res, dict) else None
        if not isinstance(cost, dict):
            continue
        return {
            "run_id": exe.get("id"),
            "action": rp.get("tofu_action"),
            "estimated_at": int(exe.get("finishedAt") or exe.get("startedAt") or 0) or None,
            **cost,
        }
    return None


def register_cost_routes(
    bp,
    *,
    require_auth,
    get_project_id,
    valid_name,
    stack_dir,
    read_meta,
    save_meta,
    project_executions_dir,
    read_stack_provider,
):
    """Attach GET/PUT /stacks/<name>/cost-estimate to the cloud blueprint."""

    def _payload(pid, name):
        meta = read_meta(pid, name)
        provider = read_stack_provider(pid, name) or meta.get("provider") or "bytedc"
        return {
            "enabled": cost_enabled_from_meta(meta),
            "provider": provider,
            "currency": cost_config_from_meta(meta, provider)["currency"],
            "last_result": latest_cost_result(project_executions_dir(pid), name),
        }

    @bp.route("/stacks/<name>/cost-estimate", methods=["GET"])
    @require_auth
    def cost_estimate_get(name):
        pid = get_project_id()
        if not valid_name(name) or not stack_dir(pid, name).exists():
            return jsonify({"error": "Not found"}), 404
        return jsonify(_payload(pid, name))

    @bp.route("/stacks/<name>/cost-estimate", methods=["PUT"])
    @require_auth
    def cost_estimate_set(name):
        pid = get_project_id()
        if not valid_name(name) or not stack_dir(pid, name).exists():
            return jsonify({"error": "Not found"}), 404
        body = request.get_json(silent=True) or {}
        enabled = body.get("enabled")
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() in ("1", "true", "yes", "on")
        if not isinstance(enabled, bool):
            return jsonify({"error": "'enabled' must be a boolean."}), 400
        save_meta(pid, name, cost_estimate_enabled=enabled)
        return jsonify({"ok": True, **_payload(pid, name)})

    return bp
