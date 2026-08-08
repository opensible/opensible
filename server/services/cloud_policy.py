"""
Policy-as-code gate for Cloud Provisioning stacks (opt-in per stack).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import jsonify, request

POLICY_RULE_TYPES = {
    "deny_destroy",
    "denied_resource_types",
    "require_tags",
    "deny_public_ingress",
    "max_created",
}

SEVERITIES = ("warn", "deny")
ENFORCEMENTS = ("inherit", "block", "report")

# Custom (user-defined) rules: richer severities and a YAML/JSON check DSL.
#   "info"           -> report only, never blocks
#   "warning"        -> blocks only when the gate runs in enforce mode
#   "error"          -> deny-severity: blocks in enforce mode, or always with
#                       enforcement "block"
CUSTOM_SEVERITIES = ("info", "warning", "error")
CUSTOM_ENFORCEMENTS = ("inherit", "block", "report")
CUSTOM_TARGETS = ("resource", "output", "config")
CUSTOM_OPERATORS = frozenset(
    {
        "exists",
        "not_exists",
        "equals",
        "not_equals",
        "contains",
        "not_contains",
        "matches",
        "not_matches",
        "gt",
        "gte",
        "lt",
        "lte",
    }
)
MAX_CUSTOM_RULES = 50
_CUSTOM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def default_policy() -> Dict[str, Any]:
    """Conservative starting point: everything off except the two cheap,
    universally-agreed guardrails, and `mode` starts in warn."""
    return {
        "mode": "warn",  # warn | enforce
        "rules": {
            "deny_destroy": {"enabled": False, "severity": "deny", "enforcement": "inherit", "max_destroy": 0},
            "denied_resource_types": {"enabled": False, "severity": "deny", "enforcement": "inherit", "types": []},
            "require_tags": {"enabled": False, "severity": "warn", "enforcement": "inherit", "keys": ["environment", "owner"]},
            "deny_public_ingress": {"enabled": True, "severity": "deny", "enforcement": "inherit", "ports": [22, 3389]},
            "max_created": {"enabled": False, "severity": "warn", "enforcement": "inherit", "limit": 50},
        },
        "custom_rules": [],
    }


def policy_enabled_from_meta(meta: Dict[str, Any]) -> bool:
    """Policy gate is OFF unless the stack explicitly opted in."""
    return bool((meta or {}).get("policy_enabled") is True)


def policy_config_from_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    stored = (meta or {}).get("policy_rules")
    cfg = default_policy()
    if isinstance(stored, dict):
        if stored.get("mode") in ("warn", "enforce"):
            cfg["mode"] = stored["mode"]
        for rid, rule in (stored.get("rules") or {}).items():
            if rid in cfg["rules"] and isinstance(rule, dict):
                cfg["rules"][rid].update({k: v for k, v in rule.items() if k != "id"})
        if isinstance(stored.get("custom_rules"), list):
            cfg["custom_rules"] = sanitize_custom_rules(stored["custom_rules"])
    return cfg


def _clean_str(value: Any, max_len: int = 500) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_len]


def sanitize_custom_rules(rules: Any) -> List[Dict[str, Any]]:
    """Whitelist the custom-rule schema. Structure is validated here; regex
    patterns and path resolution are validated by the worker (RE2), whose
    findings surface in the policy result's `errors` field."""
    if not isinstance(rules, list):
        return []
    out: List[Dict[str, Any]] = []
    seen_ids: set = set()
    for rule in rules[:MAX_CUSTOM_RULES]:
        if not isinstance(rule, dict):
            continue
        rid = _clean_str(rule.get("id"), 64)
        if not _CUSTOM_ID_RE.match(rid) or rid in seen_ids:
            continue
        seen_ids.add(rid)

        severity = _clean_str(rule.get("severity"), 32).lower()
        if severity not in CUSTOM_SEVERITIES:
            severity = "warning"
        enforcement = _clean_str(rule.get("enforcement"), 32).lower()
        if enforcement not in CUSTOM_ENFORCEMENTS:
            enforcement = "inherit"
        if severity == "info":
            enforcement = "report"  # info-severity rules can never block
        target = _clean_str(rule.get("target"), 32).lower()
        if target not in CUSTOM_TARGETS:
            target = "resource"

        match: Dict[str, Any] = {"types": [], "addresses": [], "actions": []}
        if isinstance(rule.get("match"), dict):
            incoming_match = rule["match"]
            for key in ("types", "addresses"):
                items = incoming_match.get(key)
                if isinstance(items, list):
                    match[key] = [
                        _clean_str(x, 200) for x in items if isinstance(x, str) and x.strip()
                    ][:100]
            actions = incoming_match.get("actions")
            if isinstance(actions, list):
                match["actions"] = [
                    _clean_str(x, 50).lower() for x in actions if isinstance(x, str) and x.strip()
                ][:50]

        checks: List[Dict[str, Any]] = []
        incoming_checks = rule.get("checks")
        if isinstance(incoming_checks, list):
            for c in incoming_checks[:100]:
                if not isinstance(c, dict):
                    continue
                op = _clean_str(c.get("operator"), 32).lower()
                if op not in CUSTOM_OPERATORS:
                    continue
                checks.append({"path": _clean_str(c.get("path"), 300), "operator": op, "value": c.get("value")})
        if not checks:
            continue

        out.append(
            {
                "id": rid,
                "name": _clean_str(rule.get("name"), 200),
                "description": _clean_str(rule.get("description"), 500),
                "category": _clean_str(rule.get("category"), 100),
                "enabled": bool(rule.get("enabled")),
                "severity": severity,
                "enforcement": enforcement,
                "target": target,
                "match": match,
                "checks": checks,
                "message": _clean_str(rule.get("message"), 500),
            }
        )
    return out


def sanitize_policy(body: Dict[str, Any]) -> Dict[str, Any]:
    cfg = default_policy()
    mode = (body.get("mode") or "").strip().lower()
    if mode in ("warn", "enforce"):
        cfg["mode"] = mode
    cfg["custom_rules"] = sanitize_custom_rules(body.get("custom_rules"))
    rules = body.get("rules") or {}
    if not isinstance(rules, dict):
        return cfg
    for rid, incoming in rules.items():
        if rid not in POLICY_RULE_TYPES or not isinstance(incoming, dict):
            continue
        target = cfg["rules"][rid]
        target["enabled"] = bool(incoming.get("enabled"))
        sev = (incoming.get("severity") or "").strip().lower()
        if sev in SEVERITIES:
            target["severity"] = sev
        enf = (incoming.get("enforcement") or "").strip().lower()
        if enf in ENFORCEMENTS:
            target["enforcement"] = enf
        if rid == "deny_destroy":
            try:
                target["max_destroy"] = max(0, int(incoming.get("max_destroy", 0)))
            except (TypeError, ValueError):
                pass
        elif rid == "denied_resource_types":
            types = incoming.get("types")
            if isinstance(types, list):
                target["types"] = [str(t).strip() for t in types if str(t).strip()][:100]
        elif rid == "require_tags":
            keys = incoming.get("keys")
            if isinstance(keys, list):
                target["keys"] = [str(k).strip() for k in keys if str(k).strip()][:50]
        elif rid == "deny_public_ingress":
            ports = incoming.get("ports")
            if isinstance(ports, list):
                clean = []
                for p in ports[:100]:
                    try:
                        n = int(p)
                    except (TypeError, ValueError):
                        continue
                    if 0 < n < 65536:
                        clean.append(n)
                target["ports"] = clean
        elif rid == "max_created":
            try:
                target["limit"] = max(1, int(incoming.get("limit", 50)))
            except (TypeError, ValueError):
                pass
    return cfg


def latest_policy_result(ex_dir: Path, name: str) -> Optional[Dict[str, Any]]:
    """Most recent policy verdict recorded by a worker for this stack."""
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
        pol = res.get("policy") if isinstance(res, dict) else None
        if not isinstance(pol, dict):
            continue
        return {
            "run_id": exe.get("id"),
            "action": rp.get("tofu_action"),
            "checked_at": int(exe.get("finishedAt") or exe.get("startedAt") or 0) or None,
            "verdict": pol.get("verdict"),
            "denies": pol.get("denies"),
            "warns": pol.get("warns"),
            "blocked": bool(pol.get("blocked")),
            "blocked_by": pol.get("blocked_by") or [],
            "violations": pol.get("violations") or [],
            "errors": pol.get("errors") or [],
        }
    return None


def register_policy_routes(
    bp,
    *,
    require_auth,
    get_project_id,
    valid_name,
    stack_dir,
    read_meta,
    save_meta,
    project_executions_dir,
):
    """Attach GET/PUT /stacks/<name>/policy to the cloud blueprint."""

    def _payload(pid, name):
        meta = read_meta(pid, name)
        return {
            "enabled": policy_enabled_from_meta(meta),
            "policy": policy_config_from_meta(meta),
            "last_result": latest_policy_result(project_executions_dir(pid), name),
        }

    @bp.route("/stacks/<name>/policy", methods=["GET"])
    @require_auth
    def policy_get(name):
        pid = get_project_id()
        if not valid_name(name) or not stack_dir(pid, name).exists():
            return jsonify({"error": "Not found"}), 404
        return jsonify(_payload(pid, name))

    @bp.route("/stacks/<name>/policy", methods=["PUT"])
    @require_auth
    def policy_set(name):
        """Enable/disable and configure the policy gate for one stack."""
        pid = get_project_id()
        if not valid_name(name) or not stack_dir(pid, name).exists():
            return jsonify({"error": "Not found"}), 404
        body = request.get_json(silent=True) or {}
        patch: Dict[str, Any] = {}
        if "enabled" in body:
            enabled = body.get("enabled")
            if isinstance(enabled, str):
                enabled = enabled.strip().lower() in ("1", "true", "yes", "on")
            if not isinstance(enabled, bool):
                return jsonify({"error": "'enabled' must be a boolean."}), 400
            patch["policy_enabled"] = enabled
        if "policy" in body and isinstance(body.get("policy"), dict):
            patch["policy_rules"] = sanitize_policy(body["policy"])
        if not patch:
            return jsonify({"error": "Nothing to update."}), 400
        save_meta(pid, name, **patch)
        return jsonify({"ok": True, **_payload(pid, name)})

    return bp
