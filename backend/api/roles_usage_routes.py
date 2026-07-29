"""Roles usage: which playbooks / templates reference each role.

GET /api/roles/usage
  -> { "usage": { "<role_name>": { "playbooks": [ ... ], "templates": [ ... ] } } }
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)
bp = Blueprint("roles_usage_api", __name__, url_prefix="/api/roles")


def _project_id() -> Optional[str]:
    return (
        request.headers.get("X-Project-Id")
        or request.args.get("project_id")
        or (request.get_json(silent=True) or {}).get("project_id")
    )


def _repo_root() -> Optional[Path]:
    pid = _project_id()
    if not pid:
        return None
    app_mod = next(
        (m for m in (sys.modules.get("__main__"), sys.modules.get("app")) if getattr(m, "PROJECTS_DIR", None)),
        None,
    )
    pdir = getattr(app_mod, "PROJECTS_DIR", None) if app_mod else None
    if not pdir:
        return None
    return pdir / pid / "repo"


# YAML `roles:` sections in a playbook look like:
#   roles:
#     - docker-engine
#     - { role: nexus, tags: [nexus] }
#     - role: gitlab-ce
_ROLES_HEADER = re.compile(r"^\s*roles\s*:\s*$", re.MULTILINE)
_ROLE_ITEM = re.compile(r"^\s*-\s*(?:role\s*:\s*)?['\"]?([A-Za-z0-9_\-./]+)['\"]?", re.MULTILINE)
_INCLUDE_ROLE = re.compile(r"(?:include_role|import_role)\s*:\s*[\s\S]*?name\s*:\s*['\"]?([A-Za-z0-9_\-./]+)")


def _scan_playbook(text: str) -> List[str]:
    found: List[str] = []
    # roles: blocks
    for m in _ROLES_HEADER.finditer(text):
        # take a window of ~40 lines after the header
        tail = text[m.end(): m.end() + 4000]
        # Stop at the next top-level key (no leading whitespace + word + ':')
        stop = re.search(r"^\S+\s*:\s*$", tail, re.MULTILINE)
        block = tail[: stop.start()] if stop else tail
        for r in _ROLE_ITEM.finditer(block):
            name = r.group(1).strip().split("/")[-1]
            if name and name not in ("role",):
                found.append(name)
    # include_role / import_role
    for r in _INCLUDE_ROLE.finditer(text):
        name = r.group(1).strip().split("/")[-1]
        if name:
            found.append(name)
    return found


@bp.route("/usage", methods=["GET"])
def usage():
    repo = _repo_root()
    if repo is None:
        return jsonify({"usage": {}})

    usage_map: Dict[str, Dict[str, List[str]]] = {}

    def bump(role: str, kind: str, ref: str) -> None:
        entry = usage_map.setdefault(role, {"playbooks": [], "templates": []})
        if ref not in entry[kind]:
            entry[kind].append(ref)

    # ----- Playbooks in repo -----
    pb_dir = repo / "playbooks"
    if pb_dir.exists():
        for pb in sorted(pb_dir.rglob("*.y*ml")):
            try:
                text = pb.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            rel = str(pb.relative_to(repo))
            for role in _scan_playbook(text):
                bump(role, "playbooks", rel)

    # ----- Templates in catalog -----
    try:
        from templates import list_templates, get_template  # type: ignore
        for t in list_templates():
            tid = t["id"]
            detail = get_template(tid) or {}
            declared = detail.get("uses_roles") or []
            for role in declared:
                bump(str(role), "templates", tid)
    except Exception as e:
        logger.warning("template scan failed: %s", e)

    return jsonify({"usage": usage_map})
