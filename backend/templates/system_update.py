"""Template: OS package update / upgrade across hosts."""
from __future__ import annotations

from typing import Any, Dict

from ._common import render_hosts, VAULT_FILES_VARIABLE, parse_vault_files, vars_files_lines


TEMPLATE = {
    "id": "system-update",
    "name": "System Package Update",
    "category": "Maintenance",
    "icon": "refresh-cw",
    "description": "Update package cache and upgrade all OS packages across the selected hosts. Optional reboot if required.",
    "tags": ["apt", "yum", "patch", "maintenance"],
    "variables": [
        {"name": "reboot_if_needed", "label": "Reboot if kernel updated", "type": "boolean", "default": False},
        {"name": "autoremove", "label": "Remove unused packages", "type": "boolean", "default": True},
        {"name": "become", "label": "Run as sudo (become)", "type": "boolean", "default": True},
        VAULT_FILES_VARIABLE,
    ],
}


def suggested_filename(values: Dict[str, Any]) -> str:
    return "tmpl-system-update.yml"


def render(values: Dict[str, Any], targets: Dict[str, Any]) -> str:
    hosts = render_hosts(targets)
    become = "true" if values.get("become", True) else "false"
    autoremove = values.get("autoremove", True)
    reboot = values.get("reboot_if_needed", False)

    parts = [
        "---",
        f"# Rendered from template: {TEMPLATE['name']}",
        f"- name: System package update",
        f"  hosts: {hosts}",
        f"  become: {become}",
        "  gather_facts: true",
        *vars_files_lines(parse_vault_files(values.get("vault_files"))),
        "  tasks:",
        "    - name: apt update & upgrade",
        "      ansible.builtin.apt:",
        "        update_cache: true",
        "        upgrade: dist",
        f"        autoremove: {'true' if autoremove else 'false'}",
        "      when: ansible_os_family == 'Debian'",
        "    - name: dnf upgrade",
        "      ansible.builtin.dnf:",
        "        name: '*'",
        "        state: latest",
        "      when: ansible_os_family == 'RedHat'",
    ]
    if reboot:
        parts += [
            "    - name: Check if reboot required (Debian)",
            "      ansible.builtin.stat:",
            "        path: /var/run/reboot-required",
            "      register: reboot_required",
            "      when: ansible_os_family == 'Debian'",
            "    - name: Reboot if needed",
            "      ansible.builtin.reboot:",
            "        msg: Reboot after package upgrade",
            "        reboot_timeout: 600",
            "      when: ansible_os_family == 'Debian' and reboot_required.stat.exists | default(false)",
        ]
    parts.append("")
    return "\n".join(parts)
