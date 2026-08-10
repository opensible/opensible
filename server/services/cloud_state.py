"""
State management for Cloud Provisioning stacks.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from flask import Response, jsonify, request

# Actions that mutate state and therefore take the lock + snapshot first.
MUTATING_ACTIONS = ("apply", "destroy", "refresh")
# Terminal execution statuses — a lock held by such a run is stale.
TERMINAL_STATUSES = {"SUCCESS", "FAILED", "CANCELED", "ERROR", "TIMEOUT", "STALLED"}

MAX_VERSIONS = 50
_VERSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

BACKEND_FIELDS = ("bucket", "key", "region", "endpoint", "profile", "prefix")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(p: Path, default: Any) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(p: Path, data: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(p)


def _state_source(sd: Path) -> Optional[Path]:
    """Local state, falling back to the `tofu state pull` snapshot written by
    the worker when the stack uses a remote backend."""
    for candidate in ("terraform.tfstate", "terraform.tfstate.json"):
        p = sd / candidate
        if p.exists():
            return p
    return None


def _summarize_state(raw: str) -> Dict[str, Any]:
    try:
        state = json.loads(raw)
    except Exception:
        return {"serial": None, "lineage": None, "resource_count": 0, "tofu_version": None}
    count = 0
    for res in (state.get("resources") or []):
        count += len(res.get("instances") or [])
    return {
        "serial": state.get("serial"),
        "lineage": state.get("lineage"),
        "resource_count": count,
        "tofu_version": state.get("terraform_version"),
    }


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

def _audit_file(dd: Path) -> Path:
    return dd / "state-audit.jsonl"


def append_audit(dd: Path, event: str, actor: str, **fields: Any) -> None:
    entry = {"at": _now(), "event": event, "actor": actor or "unknown", **fields}
    try:
        dd.mkdir(parents=True, exist_ok=True)
        with _audit_file(dd).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def read_audit(dd: Path, limit: int = 100) -> List[Dict[str, Any]]:
    f = _audit_file(dd)
    if not f.exists():
        return []
    try:
        lines = f.read_text(encoding="utf-8").strip().splitlines()
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    out.reverse()
    return out


# ---------------------------------------------------------------------------
# Locking
# ---------------------------------------------------------------------------

def _lock_file(dd: Path) -> Path:
    return dd / "state-lock.json"


def read_lock(dd: Path, get_execution: Optional[Callable] = None,
              project_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Return the active lock, auto-releasing it when the owning run finished."""
    f = _lock_file(dd)
    if not f.exists():
        return None
    lock = _read_json(f, None)
    if not isinstance(lock, dict):
        return None
    run_id = lock.get("run_id")
    if run_id and get_execution:
        try:
            exe = get_execution(run_id, project_id=project_id) or {}
            status = str(exe.get("status") or "").upper()
            if status in TERMINAL_STATUSES:
                release_lock(dd, lock_id=lock.get("id"), actor="system",
                             reason=f"run {status.lower()}")
                return None
        except Exception:
            pass
    held = 0
    try:
        held = max(0, int(time.time() - float(lock.get("created_ts") or 0)))
    except Exception:
        held = 0
    lock["held_seconds"] = held
    return lock


def acquire_lock(dd: Path, *, actor: str, operation: str,
                 run_id: Optional[str] = None, note: str = "",
                 get_execution: Optional[Callable] = None,
                 project_id: Optional[str] = None) -> Dict[str, Any]:
    """Take the lock. Returns {"ok": True, "lock": ...} or {"ok": False, "lock": existing}."""
    existing = read_lock(dd, get_execution, project_id)
    if existing:
        return {"ok": False, "lock": existing}
    lock = {
        "id": uuid.uuid4().hex[:16],
        "who": actor or "unknown",
        "operation": operation,
        "run_id": run_id,
        "note": note or "",
        "created_at": _now(),
        "created_ts": time.time(),
    }
    _write_json(_lock_file(dd), lock)
    append_audit(dd, "lock.acquired", actor, operation=operation, run_id=run_id,
                 lock_id=lock["id"], note=note or "")
    return {"ok": True, "lock": lock}


def release_lock(dd: Path, *, lock_id: Optional[str] = None, actor: str = "",
                 force: bool = False, reason: str = "") -> Dict[str, Any]:
    f = _lock_file(dd)
    if not f.exists():
        return {"ok": True, "released": False}
    lock = _read_json(f, {}) or {}
    if lock_id and lock.get("id") and lock_id != lock.get("id") and not force:
        return {"ok": False, "error": "Lock id mismatch — pass force to break the lock."}
    try:
        f.unlink()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    append_audit(dd, "lock.forced" if force else "lock.released", actor,
                 lock_id=lock.get("id"), previous_owner=lock.get("who"),
                 operation=lock.get("operation"), reason=reason)
    return {"ok": True, "released": True, "previous": lock}


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------

def _versions_dir(dd: Path) -> Path:
    return dd / "state-versions"


def _index_file(dd: Path) -> Path:
    return _versions_dir(dd) / "index.json"


def list_versions(dd: Path) -> List[Dict[str, Any]]:
    idx = _read_json(_index_file(dd), [])
    return idx if isinstance(idx, list) else []


def _save_index(dd: Path, versions: List[Dict[str, Any]]) -> None:
    _write_json(_index_file(dd), versions)


def snapshot_state(sd: Path, dd: Path, *, actor: str, reason: str,
                   run_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Capture the current state file as a new version.

    No-ops when there is no state on disk, or when the content is identical to
    the newest version (so repeated plans don't spam the history).
    """
    src = _state_source(sd)
    if not src:
        return None
    try:
        raw = src.read_text(encoding="utf-8")
    except Exception:
        return None
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    versions = list_versions(dd)
    if versions and versions[0].get("sha256") == digest:
        # Same bytes as the newest version — don't duplicate it, but do record
        # that this run started from that snapshot so the history can offer
        # "Rollback to here" for the run.
        if run_id:
            ids = list(versions[0].get("run_ids") or [])
            if versions[0].get("run_id") and versions[0]["run_id"] not in ids:
                ids.append(versions[0]["run_id"])
            if run_id not in ids:
                ids.append(run_id)
            versions[0]["run_ids"] = ids
            _save_index(dd, versions)
        return versions[0] if run_id else None


    vid = f"{int(time.time() * 1000)}-{digest[:8]}"
    vdir = _versions_dir(dd)
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / f"{vid}.json").write_text(raw, encoding="utf-8")

    entry = {
        "id": vid,
        "created_at": _now(),
        "actor": actor or "system",
        "reason": reason,
        "run_id": run_id,
        "run_ids": [run_id] if run_id else [],
        "size_bytes": len(raw.encode("utf-8")),
        "sha256": digest,
        "source": src.name,
        **_summarize_state(raw),
    }


    versions.insert(0, entry)
    # Prune oldest beyond the retention window.
    for old in versions[MAX_VERSIONS:]:
        try:
            (vdir / f"{old['id']}.json").unlink()
        except Exception:
            pass
    versions = versions[:MAX_VERSIONS]
    _save_index(dd, versions)
    append_audit(dd, "state.snapshot", actor, version_id=vid, reason=reason,
                 run_id=run_id, serial=entry.get("serial"),
                 resource_count=entry.get("resource_count"))
    return entry


def rollback_state(sd: Path, dd: Path, version_id: str, *, actor: str) -> Dict[str, Any]:
    if not _VERSION_ID_RE.match(version_id or ""):
        return {"ok": False, "error": "Invalid version id"}
    vfile = _versions_dir(dd) / f"{version_id}.json"
    if not vfile.exists():
        return {"ok": False, "error": "Version not found"}

    # Always snapshot what we are about to overwrite, so a rollback is itself
    # reversible.
    snapshot_state(sd, dd, actor=actor, reason="pre-rollback")

    target = sd / "terraform.tfstate"
    try:
        raw = vfile.read_text(encoding="utf-8")
        if target.exists():
            shutil.copy2(target, sd / "terraform.tfstate.rollback-backup")
        target.write_text(raw, encoding="utf-8")
    except Exception as e:
        return {"ok": False, "error": f"Restore failed: {e}"}

    summary = _summarize_state(raw)
    append_audit(dd, "state.rollback", actor, version_id=version_id, **summary)
    return {"ok": True, "version_id": version_id, **summary}


# ---------------------------------------------------------------------------
# Remote backend configuration (backend.hcl)
# ---------------------------------------------------------------------------

_HCL_LINE = re.compile(r'^\s*([A-Za-z0-9_]+)\s*=\s*"?([^"\n]*)"?\s*$')


def read_backend_config(sd: Path) -> Dict[str, Any]:
    f = sd / "backend.hcl"
    values: Dict[str, str] = {}
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("#"):
                continue
            m = _HCL_LINE.match(line)
            if m:
                values[m.group(1)] = m.group(2)
    placeholder = any(str(v).startswith("REPLACE_ME") for v in values.values())
    btf = sd / "backend.tf"
    backend_type = "local"
    if btf.exists():
        text = btf.read_text(encoding="utf-8")
        m = re.search(r'backend\s+"([a-z0-9]+)"', text)
        if m:
            backend_type = m.group(1)
    return {
        "backend_type": backend_type,
        "configured": bool(values) and not placeholder,
        "placeholder": placeholder,
        "values": {k: values.get(k, "") for k in BACKEND_FIELDS if k in values} or values,
        "raw": (f.read_text(encoding="utf-8") if f.exists() else ""),
    }


def write_backend_config(sd: Path, dd: Path, values: Dict[str, Any], *, actor: str) -> Dict[str, Any]:
    clean: Dict[str, str] = {}
    for k in BACKEND_FIELDS:
        v = values.get(k)
        if v is None:
            continue
        v = str(v).strip()
        if not v:
            continue
        if "\n" in v or '"' in v:
            return {"ok": False, "error": f"Invalid value for {k}"}
        clean[k] = v
    if not clean:
        return {"ok": False, "error": "Provide at least one backend field (bucket, key, region, ...)."}
    lines = ["# OpenTofu backend config — managed from the OpenSible console.", ""]
    lines += [f'{k} = "{v}"' for k, v in clean.items()]
    (sd / "backend.hcl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    append_audit(dd, "backend.updated", actor, fields=sorted(clean.keys()))
    return {"ok": True, **read_backend_config(sd)}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def register_state_routes(
    bp,
    *,
    require_auth,
    get_project_id,
    valid_name,
    stack_dir,
    stack_data_dir,
    current_actor,
    get_execution=None,
):
    """Attach /stacks/<name>/state/* endpoints to the cloud blueprint."""

    def _resolve(name):
        pid = get_project_id()
        if not valid_name(name) or not stack_dir(pid, name).exists():
            return None, None, None
        return pid, stack_dir(pid, name), stack_data_dir(pid, name)

    def _overview(pid, sd, dd):
        src = _state_source(sd)
        summary = {"serial": None, "lineage": None, "resource_count": 0, "tofu_version": None}
        if src:
            try:
                summary = _summarize_state(src.read_text(encoding="utf-8"))
            except Exception:
                pass
        versions = list_versions(dd)
        return {
            "state_present": src is not None,
            "state_source": src.name if src else None,
            **summary,
            "lock": read_lock(dd, get_execution, pid),
            "versions": versions[:20],
            "version_count": len(versions),
            "backend": read_backend_config(sd),
        }

    @bp.route("/stacks/<name>/state/overview", methods=["GET"])
    @require_auth
    def state_overview(name):
        pid, sd, dd = _resolve(name)
        if sd is None:
            return jsonify({"error": "Not found"}), 404
        return jsonify(_overview(pid, sd, dd))

    # -- lock ---------------------------------------------------------------
    @bp.route("/stacks/<name>/state/lock", methods=["GET"])
    @require_auth
    def state_lock_get(name):
        pid, sd, dd = _resolve(name)
        if sd is None:
            return jsonify({"error": "Not found"}), 404
        return jsonify({"lock": read_lock(dd, get_execution, pid)})

    @bp.route("/stacks/<name>/state/lock", methods=["POST"])
    @require_auth
    def state_lock_post(name):
        pid, sd, dd = _resolve(name)
        if sd is None:
            return jsonify({"error": "Not found"}), 404
        body = request.get_json(silent=True) or {}
        res = acquire_lock(
            dd, actor=current_actor(), operation=str(body.get("operation") or "manual")[:40],
            note=str(body.get("note") or "")[:300], get_execution=get_execution, project_id=pid,
        )
        if not res["ok"]:
            return jsonify({"error": "Stack state is already locked.", **res}), 409
        return jsonify(res), 201

    @bp.route("/stacks/<name>/state/lock", methods=["DELETE"])
    @require_auth
    def state_lock_delete(name):
        pid, sd, dd = _resolve(name)
        if sd is None:
            return jsonify({"error": "Not found"}), 404
        force = str(request.args.get("force", "")).lower() in ("1", "true", "yes")
        res = release_lock(dd, lock_id=request.args.get("lock_id") or None,
                           actor=current_actor(), force=force,
                           reason=request.args.get("reason") or "")
        if not res.get("ok"):
            return jsonify(res), 409
        return jsonify(res)

    # -- versions -----------------------------------------------------------
    @bp.route("/stacks/<name>/state/versions", methods=["GET"])
    @require_auth
    def state_versions_get(name):
        pid, sd, dd = _resolve(name)
        if sd is None:
            return jsonify({"error": "Not found"}), 404
        versions = list_versions(dd)
        return jsonify({"versions": versions, "count": len(versions), "max": MAX_VERSIONS})

    @bp.route("/stacks/<name>/state/versions", methods=["POST"])
    @require_auth
    def state_versions_post(name):
        pid, sd, dd = _resolve(name)
        if sd is None:
            return jsonify({"error": "Not found"}), 404
        entry = snapshot_state(sd, dd, actor=current_actor(), reason="manual")
        if not entry:
            return jsonify({
                "ok": False,
                "error": "Nothing to snapshot — no state file on disk, or it is identical to the latest version.",
            }), 409
        return jsonify({"ok": True, "version": entry}), 201

    @bp.route("/stacks/<name>/state/versions/<version_id>", methods=["GET"])
    @require_auth
    def state_version_get(name, version_id):
        pid, sd, dd = _resolve(name)
        if sd is None:
            return jsonify({"error": "Not found"}), 404
        if not _VERSION_ID_RE.match(version_id):
            return jsonify({"error": "Invalid version id"}), 400
        f = _versions_dir(dd) / f"{version_id}.json"
        if not f.exists():
            return jsonify({"error": "Version not found"}), 404
        raw = f.read_text(encoding="utf-8")
        if str(request.args.get("download", "")).lower() in ("1", "true", "yes"):
            append_audit(dd, "state.downloaded", current_actor(), version_id=version_id)
            return Response(raw, mimetype="application/json", headers={
                "Content-Disposition": f'attachment; filename="{name}-{version_id}.tfstate.json"',
            })
        return jsonify({"id": version_id, "state": json.loads(raw) if raw.strip() else {},
                        **_summarize_state(raw)})

    @bp.route("/stacks/<name>/state/versions/<version_id>/rollback", methods=["POST"])
    @require_auth
    def state_version_rollback(name, version_id):
        pid, sd, dd = _resolve(name)
        if sd is None:
            return jsonify({"error": "Not found"}), 404
        body = request.get_json(silent=True) or {}
        if str(body.get("confirm") or "").strip() != name:
            return jsonify({"error": f'Type the stack name "{name}" in "confirm" to roll back state.'}), 400
        lock = read_lock(dd, get_execution, pid)
        if lock:
            return jsonify({"error": "Stack state is locked — a run is in progress.", "lock": lock}), 409
        res = rollback_state(sd, dd, version_id, actor=current_actor())
        if not res.get("ok"):
            return jsonify(res), 400
        res["warning"] = ("State was restored, but no cloud resources changed. "
                          "Run a plan now to reconcile the restored state with reality.")
        return jsonify(res)

    # -- audit --------------------------------------------------------------
    @bp.route("/stacks/<name>/state/audit", methods=["GET"])
    @require_auth
    def state_audit_get(name):
        pid, sd, dd = _resolve(name)
        if sd is None:
            return jsonify({"error": "Not found"}), 404
        try:
            limit = max(1, min(int(request.args.get("limit", 100)), 500))
        except ValueError:
            limit = 100
        entries = read_audit(dd, limit=limit)
        return jsonify({"entries": entries, "count": len(entries)})

    # -- remote backend -----------------------------------------------------
    @bp.route("/stacks/<name>/state/backend", methods=["GET"])
    @require_auth
    def state_backend_get(name):
        pid, sd, dd = _resolve(name)
        if sd is None:
            return jsonify({"error": "Not found"}), 404
        return jsonify(read_backend_config(sd))

    @bp.route("/stacks/<name>/state/backend", methods=["PUT"])
    @require_auth
    def state_backend_put(name):
        pid, sd, dd = _resolve(name)
        if sd is None:
            return jsonify({"error": "Not found"}), 404
        body = request.get_json(silent=True) or {}
        values = body.get("values") if isinstance(body.get("values"), dict) else body
        res = write_backend_config(sd, dd, values, actor=current_actor())
        if not res.get("ok"):
            return jsonify(res), 400
        res["message"] = "backend.hcl updated. Run `init` so OpenTofu picks up the new backend."
        return jsonify(res)
