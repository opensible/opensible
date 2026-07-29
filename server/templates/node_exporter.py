"""Template: Prometheus node_exporter (distro package + systemd).

Deploys prometheus-node-exporter on one or more hosts from the distro
package repositories (apt/dnf) and exposes /metrics on :9100. Optionally
opens the port in UFW/firewalld and enables the textfile collector.

Marker: 2026-07-node-exporter-v1
"""
from __future__ import annotations

from typing import Any, Dict, List

from ._common import (
    render_hosts,
    yaml_str,
    slugify,
    VAULT_FILES_VARIABLE,
    parse_vault_files,
    vars_files_lines,
)


TEMPLATE = {
    "id": "node-exporter",
    "name": "Prometheus node_exporter",
    "category": "Observability",
    "icon": "activity",
    "description": (
        "Install prometheus-node-exporter on every selected host from the "
        "distro package repository (works on Debian/Ubuntu and RHEL/Rocky/"
        "Alma via EPEL). Exposes host metrics on :9100/metrics for Prometheus "
        "or VictoriaMetrics to scrape."
    ),
    "tags": ["prometheus", "node-exporter", "metrics", "systemd"],
    "variables": [
        {"name": "cluster_id", "label": "Deployment name",
         "type": "string", "default": "opensible-node-exporter"},
        {"name": "listen_address", "label": "Listen address",
         "type": "string", "default": "0.0.0.0:9100"},
        {"name": "textfile_dir", "label": "Textfile collector directory",
         "type": "string", "default": "/var/lib/node_exporter/textfile_collector",
         "help": "Enables the textfile collector. Set blank to disable."},
        {"name": "open_firewall", "label": "Open port 9100 in UFW/firewalld",
         "type": "boolean", "default": True},
        {"name": "become", "label": "Run as sudo (become)",
         "type": "boolean", "default": True},
        VAULT_FILES_VARIABLE,
    ],
}


def suggested_filename(values: Dict[str, Any]) -> str:
    stem = slugify(values.get("cluster_id"), "node-exporter")
    return f"{stem}-node-exporter.yml"


def render(values: Dict[str, Any], targets: Dict[str, Any]) -> str:
    cluster_id = str(values.get("cluster_id") or "opensible-node-exporter").strip() or "opensible-node-exporter"
    listen = str(values.get("listen_address") or "0.0.0.0:9100").strip() or "0.0.0.0:9100"
    textfile = str(values.get("textfile_dir") or "").strip()
    open_fw = bool(values.get("open_firewall", True))
    become = "true" if values.get("become", True) else "false"
    hosts = render_hosts(targets)

    try:
        port = int(listen.rsplit(":", 1)[-1])
    except Exception:
        port = 9100

    extra_args: List[str] = [f"--web.listen-address={listen}"]
    if textfile:
        extra_args.append(f"--collector.textfile.directory={textfile}")
    args_join = " ".join(extra_args)

    parts: List[str] = [
        "---",
        f"# OpenSible node-exporter template generation: 2026-07-node-exporter-v1",
        f"# Deployment: {cluster_id}",
        "",
        "- name: Deploy Prometheus node_exporter",
        f"  hosts: {hosts}",
        f"  become: {become}",
        "  gather_facts: true",
        *vars_files_lines(parse_vault_files(values.get("vault_files"))),
        "  vars:",
        f"    ne_cluster_id: {yaml_str(cluster_id)}",
        f"    ne_listen: {yaml_str(listen)}",
        f"    ne_port: {port}",
        f"    ne_textfile: {yaml_str(textfile)}",
        f"    ne_open_firewall: {'true' if open_fw else 'false'}",
        f"    ne_extra_args: {yaml_str(args_join)}",
        "  tasks:",

        # ---- install ----
        "    - name: Enable EPEL (RHEL family) — required for node_exporter",
        "      when: ansible_os_family == 'RedHat'",
        "      ansible.builtin.dnf:",
        "        name: epel-release",
        "        state: present",
        "      failed_when: false",
        "    - name: Refresh apt metadata (Debian/Ubuntu)",
        "      when: ansible_os_family == 'Debian'",
        "      ansible.builtin.apt:",
        "        update_cache: true",
        "        cache_valid_time: 300",
        "      failed_when: false",
        "    - name: Install prometheus-node-exporter (Debian/Ubuntu)",
        "      when: ansible_os_family == 'Debian'",
        "      ansible.builtin.apt:",
        "        name: prometheus-node-exporter",
        "        state: present",
        "        install_recommends: false",
        "      register: _ne_pkg_deb",
        "      until: _ne_pkg_deb is succeeded",
        "      retries: 3",
        "      delay: 10",
        "    - name: Install golang-github-prometheus-node-exporter (RHEL/Rocky/Alma)",
        "      when: ansible_os_family == 'RedHat'",
        "      ansible.builtin.dnf:",
        "        name: golang-github-prometheus-node-exporter",
        "        state: present",
        "      register: _ne_pkg_rh",
        "      until: _ne_pkg_rh is succeeded",
        "      retries: 3",
        "      delay: 10",

        # ---- textfile dir ----
        "    - name: Ensure textfile collector dir exists",
        "      when: ne_textfile | length > 0",
        "      ansible.builtin.file:",
        "        path: \"{{ ne_textfile }}\"",
        "        state: directory",
        "        owner: prometheus",
        "        group: prometheus",
        "        mode: '0755'",
        "      failed_when: false",

        # ---- systemd drop-in with our args ----
        "    - name: Detect installed node_exporter unit name",
        "      ansible.builtin.shell: |",
        "        set -e",
        "        for u in prometheus-node-exporter node_exporter; do",
        "          if systemctl list-unit-files --type=service --no-legend --no-pager | awk '{print $1}' | grep -qx \"$u.service\"; then",
        "            echo \"$u\"; exit 0",
        "          fi",
        "        done",
        "        echo prometheus-node-exporter",
        "      register: _ne_unit",
        "      changed_when: false",
        "    - name: Systemd drop-in dir for node_exporter",
        "      ansible.builtin.file:",
        "        path: \"/etc/systemd/system/{{ _ne_unit.stdout }}.service.d\"",
        "        state: directory",
        "        mode: '0755'",
        "    - name: Override ExecStart with our listen address / collectors",
        "      ansible.builtin.copy:",
        "        dest: \"/etc/systemd/system/{{ _ne_unit.stdout }}.service.d/override.conf\"",
        "        mode: '0644'",
        "        content: |",
        "          [Service]",
        "          ExecStart=",
        "          ExecStart=/usr/bin/prometheus-node-exporter {{ ne_extra_args }}",
        "      register: _ne_override",

        # ---- firewall ----
        "    - name: Open port {{ ne_port }}/tcp (UFW, Debian/Ubuntu)",
        "      when: ne_open_firewall and ansible_os_family == 'Debian'",
        "      ansible.builtin.shell: |",
        "        if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q 'Status: active'; then",
        "          ufw allow {{ ne_port }}/tcp || true",
        "        fi",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Open port {{ ne_port }}/tcp (firewalld, RHEL family)",
        "      when: ne_open_firewall and ansible_os_family == 'RedHat'",
        "      ansible.builtin.shell: |",
        "        if systemctl is-active --quiet firewalld; then",
        "          firewall-cmd --permanent --add-port={{ ne_port }}/tcp || true",
        "          firewall-cmd --reload || true",
        "        fi",
        "      changed_when: false",
        "      failed_when: false",

        # ---- enable + start ----
        "    - name: Enable and (re)start node_exporter",
        "      ansible.builtin.systemd:",
        "        name: \"{{ _ne_unit.stdout }}\"",
        "        enabled: true",
        "        state: restarted",
        "        daemon_reload: true",
        "    - name: Wait for /metrics endpoint",
        "      ansible.builtin.wait_for:",
        "        host: 127.0.0.1",
        "        port: \"{{ ne_port }}\"",
        "        timeout: 30",
        "    - name: Report node_exporter status",
        "      ansible.builtin.debug:",
        "        msg: \"node_exporter listening on {{ ne_listen }} (unit {{ _ne_unit.stdout }})\"",
        "",
    ]
    return "\n".join(parts)
