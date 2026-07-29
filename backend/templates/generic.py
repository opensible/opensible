"""Template: Generic / General-purpose Ansible play.

A flexible "swiss-army-knife" template for installing, updating, deploying, or
configuring any service. Every section is optional — supply only the pieces you
need and the renderer stitches them into a single well-formed play.

Sections (all optional):
  * OS packages (install/remove/upgrade — Debian & RedHat)
  * Systemd services (state + enabled)
  * File drops (inline content + mode)
  * Directories (ensure present)
  * Git checkouts
  * Pre / Post shell commands
  * Environment variables (exported before tasks)
  * Raw extra tasks   (appended verbatim under tasks:)
  * Raw extra handlers (appended verbatim under handlers:)
"""
from __future__ import annotations

from typing import Any, Dict, List

import yaml as _yaml

from ._common import (
    render_hosts,
    indent_block,
    VAULT_FILES_VARIABLE,
    parse_vault_files,
    vars_files_lines,
)


TEMPLATE = {
    "id": "generic",
    "name": "Generic / General-purpose",
    "category": "General",
    "icon": "wand-2",
    "description": (
        "Fully general-purpose runner. Paste any complete Ansible playbook "
        "into the Raw playbook field to save & run it verbatim, or leave it "
        "empty and use the structured sections (packages, services, files, "
        "shell steps, raw tasks) to compose a play."
    ),
    "tags": ["generic", "raw", "custom", "install", "deploy", "config", "shell"],
    "variables": [
        {"name": "raw_playbook", "label": "Raw playbook YAML (pass-through — overrides all fields below)",
         "type": "code", "language": "yaml", "rows": 16,
         "default": (
             "# Paste or write a complete Ansible playbook here.\n"
             "# When this field is non-empty, ALL other fields below are\n"
             "# ignored and this YAML is saved & run verbatim — so this\n"
             "# template can execute ANY playbook you provide.\n"
             "#\n"
             "# Example:\n"
             "# ---\n"
             "# - name: My custom play\n"
             "#   hosts: all\n"
             "#   become: true\n"
             "#   tasks:\n"
             "#     - name: ping\n"
             "#       ansible.builtin.ping:\n"
         ),
         "help": (
             "Full playbook mode. Provide a complete Ansible playbook (one or "
             "more plays). It will be saved and executed exactly as written. "
             "Leave empty to use the structured fields below instead."
         )},

        {"name": "play_name", "label": "Play name",
         "type": "string", "default": "Generic deployment",
         "help": "Human-readable name shown in the play header."},

        {"name": "become", "label": "Run as sudo (become)",
         "type": "boolean", "default": True},
        {"name": "gather_facts", "label": "Gather facts",
         "type": "boolean", "default": True},
        {"name": "any_errors_fatal", "label": "Any errors fatal",
         "type": "boolean", "default": False,
         "help": "Abort the whole play on the first host failure."},
        {"name": "serial", "label": "Serial / rolling batch size (optional)",
         "type": "string", "default": "",
         "help": "e.g. '1', '25%', '2' — leave blank to run on all hosts in parallel."},

        {"name": "env_vars", "label": "Environment variables (YAML mapping, optional)",
         "type": "code", "language": "yaml", "rows": 4,
         "default": "# HTTP_PROXY: http://proxy:3128\n# APP_ENV: production\n",
         "help": "Exposed to every task via the play-level `environment:` key."},

        {"name": "packages", "label": "Packages to manage (YAML list)",
         "type": "code", "language": "yaml", "rows": 5,
         "default": "# - curl\n# - jq\n# - htop\n",
         "help": "Managed with apt on Debian/Ubuntu and dnf on RedHat/CentOS."},
        {"name": "packages_state", "label": "Package state",
         "type": "string", "default": "present",
         "help": "One of: present, latest, absent."},
        {"name": "update_cache", "label": "Refresh package cache first",
         "type": "boolean", "default": True},

        {"name": "directories", "label": "Directories to ensure (YAML list)",
         "type": "code", "language": "yaml", "rows": 4,
         "default": (
             "# - path: /opt/app\n"
             "#   owner: root\n"
             "#   group: root\n"
             "#   mode: '0755'\n"
         )},

        {"name": "files", "label": "Files to drop (YAML list)",
         "type": "code", "language": "yaml", "rows": 6,
         "default": (
             "# - dest: /etc/myapp/config.yml\n"
             "#   mode: '0644'\n"
             "#   owner: root\n"
             "#   group: root\n"
             "#   content: |\n"
             "#     hello: world\n"
         ),
         "help": "Each item needs `dest` and `content`. Optional: owner, group, mode."},

        {"name": "git_repos", "label": "Git checkouts (YAML list)",
         "type": "code", "language": "yaml", "rows": 4,
         "default": (
             "# - repo: https://github.com/example/app.git\n"
             "#   dest: /opt/app\n"
             "#   version: main\n"
         )},

        {"name": "services", "label": "Systemd services (YAML list)",
         "type": "code", "language": "yaml", "rows": 4,
         "default": (
             "# - name: nginx\n"
             "#   state: restarted\n"
             "#   enabled: true\n"
         ),
         "help": "Each item accepts: name, state (started/stopped/restarted/reloaded), enabled, daemon_reload."},

        {"name": "pre_shell", "label": "Pre-shell commands (one per line)",
         "type": "code", "language": "shell", "rows": 4,
         "default": "# echo 'preparing…'\n",
         "help": "Runs before packages/files/services."},
        {"name": "post_shell", "label": "Post-shell commands (one per line)",
         "type": "code", "language": "shell", "rows": 4,
         "default": "# systemctl status myapp --no-pager || true\n",
         "help": "Runs after everything else."},

        {"name": "extra_tasks", "label": "Extra tasks (raw YAML, appended verbatim)",
         "type": "code", "language": "yaml", "rows": 6,
         "default": (
             "# - name: My custom task\n"
             "#   ansible.builtin.debug:\n"
             "#     msg: hello from generic template\n"
         ),
         "help": "Full Ansible task blocks. Written inside `tasks:` without modification."},
        {"name": "extra_handlers", "label": "Handlers (raw YAML, optional)",
         "type": "code", "language": "yaml", "rows": 4,
         "default": (
             "# - name: reload nginx\n"
             "#   ansible.builtin.service:\n"
             "#     name: nginx\n"
             "#     state: reloaded\n"
         )},

        VAULT_FILES_VARIABLE,
    ],
}


# --------------------------------------------------------------------------- #
# Filename & YAML helpers
# --------------------------------------------------------------------------- #

def suggested_filename(values: Dict[str, Any]) -> str:
    from ._common import slugify
    name = values.get("play_name") or "generic"
    return f"tmpl-generic-{slugify(name, 'generic')}.yml"


def _parse_yaml(raw: Any, expect: str = "list") -> Any:
    """Parse a YAML blob from the UI. Comments-only → empty container."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return [] if expect == "list" else {}
    if isinstance(raw, (list, dict)):
        return raw
    try:
        val = _yaml.safe_load(str(raw))
    except Exception:
        return [] if expect == "list" else {}
    if val is None:
        return [] if expect == "list" else {}
    return val


def _shell_lines(raw: Any) -> List[str]:
    if not raw:
        return []
    out = []
    for ln in str(raw).splitlines():
        s = ln.rstrip()
        if not s.strip() or s.lstrip().startswith("#"):
            continue
        out.append(s)
    return out


def _dump_block(obj: Any, indent: str) -> str:
    """Dump obj as YAML and re-indent every line with ``indent``."""
    text = _yaml.safe_dump(obj, sort_keys=False, default_flow_style=False).rstrip()
    return indent_block(text, indent)


# --------------------------------------------------------------------------- #
# Render
# --------------------------------------------------------------------------- #

def render(values: Dict[str, Any], targets: Dict[str, Any]) -> str:
    # Pass-through mode: if the user supplied a full playbook, save/run it verbatim.
    raw_pb = values.get("raw_playbook")
    if isinstance(raw_pb, str):
        # Ignore blocks that are only comments/whitespace (the default placeholder).
        meaningful = "\n".join(
            ln for ln in raw_pb.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ).strip()
        if meaningful:
            text = raw_pb.rstrip() + "\n"
            if not text.lstrip().startswith("---"):
                text = "---\n" + text
            return text

    hosts = render_hosts(targets)
    become = "true" if values.get("become", True) else "false"
    gather = "true" if values.get("gather_facts", True) else "false"
    play_name = values.get("play_name") or "Generic deployment"


    pkg_state = str(values.get("packages_state") or "present").strip() or "present"
    update_cache = bool(values.get("update_cache", True))
    packages = _parse_yaml(values.get("packages"), "list") or []
    directories = _parse_yaml(values.get("directories"), "list") or []
    files = _parse_yaml(values.get("files"), "list") or []
    git_repos = _parse_yaml(values.get("git_repos"), "list") or []
    services = _parse_yaml(values.get("services"), "list") or []
    env_vars = _parse_yaml(values.get("env_vars"), "dict") or {}
    extra_tasks = _parse_yaml(values.get("extra_tasks"), "list") or []
    extra_handlers = _parse_yaml(values.get("extra_handlers"), "list") or []
    pre_shell = _shell_lines(values.get("pre_shell"))
    post_shell = _shell_lines(values.get("post_shell"))

    lines: List[str] = [
        "---",
        f"# Rendered from template: {TEMPLATE['name']}",
        f"- name: {play_name}",
        f"  hosts: {hosts}",
        f"  become: {become}",
        f"  gather_facts: {gather}",
    ]
    if values.get("any_errors_fatal"):
        lines.append("  any_errors_fatal: true")
    serial = str(values.get("serial") or "").strip()
    if serial:
        lines.append(f"  serial: {serial}")
    if isinstance(env_vars, dict) and env_vars:
        lines.append("  environment:")
        lines.append(_dump_block(env_vars, "    "))
    lines += vars_files_lines(parse_vault_files(values.get("vault_files")))
    lines.append("  tasks:")

    tasks: List[str] = []

    # Pre-shell
    for cmd in pre_shell:
        tasks += [
            "    - name: pre-shell — " + cmd[:60],
            "      ansible.builtin.shell: |",
            f"        {cmd}",
            "      changed_when: false",
        ]

    # Packages
    if isinstance(packages, list) and packages:
        if update_cache:
            tasks += [
                "    - name: apt update cache",
                "      ansible.builtin.apt:",
                "        update_cache: true",
                "      when: ansible_os_family == 'Debian'",
            ]
        tasks += [
            "    - name: install/upgrade packages (Debian)",
            "      ansible.builtin.apt:",
            "        name:",
            *[f"          - {p}" for p in packages],
            f"        state: {pkg_state}",
            "      when: ansible_os_family == 'Debian'",
            "    - name: install/upgrade packages (RedHat)",
            "      ansible.builtin.dnf:",
            "        name:",
            *[f"          - {p}" for p in packages],
            f"        state: {pkg_state}",
            "      when: ansible_os_family == 'RedHat'",
        ]

    # Directories
    for d in directories or []:
        if not isinstance(d, dict) or not d.get("path"):
            continue
        tasks += [
            f"    - name: ensure directory {d['path']}",
            "      ansible.builtin.file:",
            f"        path: {d['path']}",
            "        state: directory",
        ]
        for k in ("owner", "group", "mode"):
            if d.get(k) is not None:
                tasks.append(f"        {k}: '{d[k]}'" if k == "mode" else f"        {k}: {d[k]}")

    # Files
    for f in files or []:
        if not isinstance(f, dict) or not f.get("dest"):
            continue
        content = str(f.get("content", ""))
        tasks += [
            f"    - name: write file {f['dest']}",
            "      ansible.builtin.copy:",
            f"        dest: {f['dest']}",
            "        content: |",
            *[f"          {ln}" for ln in content.splitlines() or [""]],
        ]
        for k in ("owner", "group"):
            if f.get(k) is not None:
                tasks.append(f"        {k}: {f[k]}")
        if f.get("mode") is not None:
            tasks.append(f"        mode: '{f['mode']}'")

    # Git checkouts
    for g in git_repos or []:
        if not isinstance(g, dict) or not g.get("repo") or not g.get("dest"):
            continue
        tasks += [
            f"    - name: git clone {g['repo']}",
            "      ansible.builtin.git:",
            f"        repo: {g['repo']}",
            f"        dest: {g['dest']}",
            f"        version: {g.get('version', 'HEAD')}",
            "        force: true",
        ]

    # Services
    for s in services or []:
        if not isinstance(s, dict) or not s.get("name"):
            continue
        tasks += [
            f"    - name: manage service {s['name']}",
            "      ansible.builtin.systemd:",
            f"        name: {s['name']}",
        ]
        if s.get("state"):
            tasks.append(f"        state: {s['state']}")
        if s.get("enabled") is not None:
            tasks.append(f"        enabled: {'true' if s['enabled'] else 'false'}")
        if s.get("daemon_reload"):
            tasks.append("        daemon_reload: true")

    # Extra raw tasks
    if isinstance(extra_tasks, list) and extra_tasks:
        tasks.append(_dump_block(extra_tasks, "    "))

    # Post-shell
    for cmd in post_shell:
        tasks += [
            "    - name: post-shell — " + cmd[:60],
            "      ansible.builtin.shell: |",
            f"        {cmd}",
            "      changed_when: false",
        ]

    if not tasks:
        tasks = ["    - name: no-op (empty generic template)",
                 "      ansible.builtin.debug:",
                 "        msg: 'Generic template rendered with no tasks.'"]

    lines += tasks

    if isinstance(extra_handlers, list) and extra_handlers:
        lines.append("  handlers:")
        lines.append(_dump_block(extra_handlers, "    "))

    lines.append("")
    return "\n".join(lines)
