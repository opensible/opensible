"""
Policy-as-code gate for Cloud Provisioning stacks (opt-in per stack).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

from flask import jsonify, request

POLICY_RULE_TYPES = {
    "deny_destroy",
    "denied_resource_types",
    "require_tags",
    "deny_public_ingress",
    "max_created",
}

SEVERITIES = ("warn", "deny")
CUSTOM_SEVERITIES = ("info", "warn", "deny")


ENFORCEMENTS = ("inherit", "block", "report")

# Custom (user-defined) rules -------------------------------------------------
CUSTOM_OPERATORS = (
    "matches",       # regex match against the attribute value
    "not_matches",
    "equals",
    "not_equals",
    "exists",
    "not_exists",
    "gt",
    "lt",
)
CUSTOM_ACTIONS = ("create", "update", "delete", "read", "no-op")
MAX_CUSTOM_RULES = 100

_SLUG_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789-_"


def _slugify(value: str, fallback: str) -> str:
    s = "".join(c if c in _SLUG_CHARS else "-" for c in str(value or "").strip().lower())
    s = "-".join(p for p in s.split("-") if p)
    return s[:64] or fallback


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
        # User-defined rules (YAML/DSL in the UI, JSON on the wire).
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
        custom = stored.get("custom_rules")
        if isinstance(custom, list):
            cfg["custom_rules"] = sanitize_custom_rules(custom)
    return cfg


def sanitize_custom_rules(items: Any) -> list:
    """Validate user-defined rules. Anything malformed is dropped rather than
    silently turned into a rule that matches everything."""
    if not isinstance(items, list):
        return []
    out: list = []
    seen_ids = set()
    for idx, raw in enumerate(items[:MAX_CUSTOM_RULES]):
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or raw.get("id") or "").strip()[:120]
        rid = _slugify(raw.get("id") or name, f"rule-{idx + 1}")
        base = rid
        n = 2
        while rid in seen_ids:
            rid = f"{base}-{n}"
            n += 1
        seen_ids.add(rid)

        op = str(raw.get("operator") or "matches").strip().lower()
        if op not in CUSTOM_OPERATORS:
            op = "matches"
        sev = str(raw.get("severity") or "warn").strip().lower()
        if sev not in CUSTOM_SEVERITIES:
            sev = "warn"
        enf = str(raw.get("enforcement") or "inherit").strip().lower()
        if enf not in ENFORCEMENTS:
            enf = "inherit"

        def _regex_list(value: Any, limit: int = 50) -> list:
            if isinstance(value, str):
                value = [value]
            if not isinstance(value, list):
                return []
            clean = []
            for v in value[:limit]:
                s = str(v).strip()
                if not s:
                    continue
                try:
                    re.compile(s)
                except re.error:
                    continue
                clean.append(s[:400])
            return clean

        actions = raw.get("actions")
        if isinstance(actions, str):
            actions = [actions]
        actions = [
            str(a).strip().lower()
            for a in (actions or [])
            if str(a).strip().lower() in CUSTOM_ACTIONS
        ]

        value = raw.get("value")
        if value is None:
            value = raw.get("pattern")
        if isinstance(value, (dict, list)):
            value = ""
        value = "" if value is None else str(value)[:400]
        if op in ("matches", "not_matches") and value:
            try:
                re.compile(value)
            except re.error:
                continue  # invalid regex -> drop the rule

        attribute = str(raw.get("attribute") or "").strip()[:200]
        if op not in ("exists", "not_exists") and not attribute and not value:
            # Nothing to compare against — a rule like this would match all.
            if not raw.get("resource_types"):
                continue

        out.append(
            {
                "id": rid,
                "name": name or rid,
                "description": str(raw.get("description") or "").strip()[:400],
                "enabled": bool(raw.get("enabled", True)),
                "severity": sev,
                "enforcement": enf,
                "resource_types": _regex_list(raw.get("resource_types")),
                "addresses": _regex_list(raw.get("addresses")),
                "actions": actions,
                "attribute": attribute,
                "operator": op,
                "value": value,
                "message": str(raw.get("message") or "").strip()[:400],
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
            "infos": pol.get("infos"),

            "blocked": bool(pol.get("blocked")),
            "blocked_by": pol.get("blocked_by") or [],
            "violations": pol.get("violations") or [],
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
