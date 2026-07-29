"""Shared helpers for template rendering."""
from __future__ import annotations

import re
from typing import Any, Dict, List


def slugify(text: str, fallback: str = "template") -> str:
    s = re.sub(r"[^a-zA-Z0-9\-_]+", "-", str(text or "").strip().lower())
    s = re.sub(r"-+", "-", s).strip("-")
    return s or fallback


def yaml_str(value: str) -> str:
    """Render a string as a safely-quoted YAML scalar."""
    if value is None:
        return '""'
    v = str(value)
    if not v:
        return '""'
    # Force double-quoted for anything with special chars, otherwise plain.
    if re.search(r"[:#\-\?\[\]\{\},&\*!\|>'\"%@`\s]", v) or v.lower() in ("true", "false", "null", "yes", "no", "on", "off"):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return v


def indent_block(text: str, prefix: str) -> str:
    """Indent every line of text with prefix."""
    return "\n".join(prefix + line for line in (text or "").splitlines())


def render_hosts(targets: Dict[str, Any]) -> str:
    """Ansible 'hosts:' value from targets dict.

    Precedence: if explicit hosts are provided, they win and groups are
    ignored — selecting a specific host must not fan the play out to every
    other member of a group that also happens to be selected. When only
    groups are provided they are joined with ':'. Falls back to 'all'.
    """
    def _norm(v: Any) -> List[str]:
        if isinstance(v, str):
            v = [v]
        return [str(x).strip() for x in (v or []) if str(x).strip()]

    hosts = _norm(targets.get("hosts"))
    if hosts:
        return ":".join(hosts)
    groups = _norm(targets.get("groups"))
    if groups:
        return ":".join(groups)
    return "all"


# --------------------------------------------------------------------------- #
# Ansible-vault helpers — shared across all templates
# --------------------------------------------------------------------------- #

VAULT_FILES_VARIABLE: Dict[str, Any] = {
    "name": "vault_files",
    "label": "Vault files (one path per line, optional)",
    "type": "code",
    "language": "yaml",
    "rows": 4,
    "help": (
        "Paths to ansible-vault encrypted vars files, relative to the project "
        "root (e.g. group_vars/all/vault.yml). Loaded via vars_files so you can "
        "reference secrets with Jinja like \"{{ my_secret }}\" anywhere in this "
        "template's fields. Requires the matching vault key attached in "
        "Infrastructure > Vaults."
    ),
    "default": "# - group_vars/all/vault.yml\n",
}


def parse_vault_files(raw: Any) -> List[str]:
    """Accept a YAML list or one path per line (with #comments). Returns [str]."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(p).strip() for p in raw if str(p).strip()]
    try:
        import yaml as _yaml  # type: ignore
        parsed = _yaml.safe_load(str(raw))
        if isinstance(parsed, list):
            return [str(p).strip() for p in parsed if str(p).strip()]
    except Exception:
        pass
    out: List[str] = []
    for ln in str(raw).splitlines():
        s = ln.strip().lstrip("-").strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def vars_files_lines(vault_files: List[str], indent: str = "  ") -> List[str]:
    """Return the play-level ``vars_files:`` block, or [] when nothing to load."""
    if not vault_files:
        return []
    return [f"{indent}vars_files:"] + [f"{indent}  - {vf}" for vf in vault_files]
