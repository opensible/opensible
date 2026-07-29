"""Template: Deploy a docker-compose project to target hosts."""
from __future__ import annotations

from typing import Any, Dict

from ._common import (
    slugify, yaml_str, render_hosts, indent_block,
    VAULT_FILES_VARIABLE, parse_vault_files, vars_files_lines,
)


TEMPLATE = {
    "id": "docker-compose",
    "name": "Docker Compose Project",
    "category": "Containers",
    "icon": "container",
    "description": "Ship a docker-compose.yml (and optional .env) to target hosts and bring the stack up with `docker compose up -d`.",
    "tags": ["docker", "compose", "containers"],
    "variables": [
        {"name": "project_name", "label": "Project name", "type": "string", "required": True,
         "placeholder": "my-app", "help": "Used as remote directory name (/opt/<name>) and Compose project name."},
        {"name": "dest_dir", "label": "Remote directory", "type": "string", "default": "/opt",
         "help": "Parent directory on target hosts. Project files land in <dest_dir>/<project_name>."},
        {"name": "compose_yaml", "label": "docker-compose.yml", "type": "code", "language": "yaml",
         "required": True, "rows": 18,
         "default": "services:\n  web:\n    image: nginx:alpine\n    restart: unless-stopped\n    ports:\n      - \"80:80\"\n"},
        {"name": "env_file", "label": ".env (optional)", "type": "code", "language": "ini",
         "rows": 6, "default": ""},
        {"name": "pull_before_up", "label": "Pull images before up", "type": "boolean", "default": True},
        {"name": "recreate", "label": "Force recreate containers", "type": "boolean", "default": False},
        {"name": "become", "label": "Run as sudo (become)", "type": "boolean", "default": True},
        VAULT_FILES_VARIABLE,
    ],
}


def suggested_filename(values: Dict[str, Any]) -> str:
    return f"tmpl-compose-{slugify(values.get('project_name'), 'app')}.yml"


def render(values: Dict[str, Any], targets: Dict[str, Any]) -> str:
    project = slugify(values.get("project_name") or "app", "app")
    dest_parent = (values.get("dest_dir") or "/opt").rstrip("/")
    dest = f"{dest_parent}/{project}"
    hosts = render_hosts(targets)
    become = "true" if values.get("become", True) else "false"
    pull = values.get("pull_before_up", True)
    recreate = values.get("recreate", False)
    compose_yaml = values.get("compose_yaml") or "services: {}\n"
    env_file = values.get("env_file") or ""

    up_cmd = "docker compose"
    up_args = " pull && docker compose up -d" if pull else " up -d"
    if recreate:
        up_args += " --force-recreate"

    parts = []
    parts.append(f"---\n# Rendered from template: {TEMPLATE['name']}")
    parts.append(f"# Project: {project}")
    parts.append("- name: Deploy {p} (docker-compose)".format(p=project))
    parts.append(f"  hosts: {hosts}")
    parts.append(f"  become: {become}")
    parts.append("  gather_facts: false")
    for _l in vars_files_lines(parse_vault_files(values.get("vault_files"))):
        parts.append(_l)
    parts.append("  vars:")
    parts.append(f"    compose_project: {yaml_str(project)}")
    parts.append(f"    compose_dir: {yaml_str(dest)}")
    parts.append("  tasks:")
    parts.append("    - name: Ensure project directory exists")
    parts.append("      ansible.builtin.file:")
    parts.append("        path: \"{{ compose_dir }}\"")
    parts.append("        state: directory")
    parts.append("        mode: '0755'")
    parts.append("    - name: Write docker-compose.yml")
    parts.append("      ansible.builtin.copy:")
    parts.append("        dest: \"{{ compose_dir }}/docker-compose.yml\"")
    parts.append("        mode: '0644'")
    parts.append("        content: |")
    parts.append(indent_block(compose_yaml.rstrip("\n"), "          "))
    if env_file.strip():
        parts.append("    - name: Write .env")
        parts.append("      ansible.builtin.copy:")
        parts.append("        dest: \"{{ compose_dir }}/.env\"")
        parts.append("        mode: '0640'")
        parts.append("        content: |")
        parts.append(indent_block(env_file.rstrip("\n"), "          "))
    parts.append("    - name: Bring the stack up")
    parts.append("      ansible.builtin.shell: |")
    parts.append(f"        cd {{{{ compose_dir }}}} && {up_cmd}{up_args}")
    parts.append("      args:")
    parts.append("        executable: /bin/bash")
    parts.append("      register: compose_up")
    parts.append("      changed_when: \"'Recreated' in compose_up.stdout or 'Started' in compose_up.stdout or 'Created' in compose_up.stdout\"")
    parts.append("    - name: Show compose status")
    parts.append("      ansible.builtin.command: \"docker compose -p {{ compose_project }} ps\"")
    parts.append("      args:")
    parts.append("        chdir: \"{{ compose_dir }}\"")
    parts.append("      changed_when: false")
    parts.append("      register: compose_ps")
    parts.append("    - name: Compose ps output")
    parts.append("      ansible.builtin.debug:")
    parts.append("        var: compose_ps.stdout_lines")
    return "\n".join(parts) + "\n"
