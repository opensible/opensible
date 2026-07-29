"""Template: Grafana OSS from the official apt/dnf repository (systemd).

Adds the official Grafana repo, installs the pinned grafana version and
seeds the admin credentials + listen port via a systemd drop-in (env vars
override grafana.ini). Optionally pre-provisions a Prometheus /
VictoriaMetrics datasource.

Marker: 2026-07-grafana-oss-v1
"""
from __future__ import annotations

import hashlib
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
    "id": "grafana-oss",
    "name": "Grafana OSS",
    "category": "Observability",
    "icon": "gauge",
    "description": (
        "Install Grafana OSS from the official apt/dnf repository and manage "
        "it via systemd. Admin username, password and listen port are "
        "injected through a systemd drop-in (environment variables override "
        "grafana.ini). Can optionally pre-provision a Prometheus / "
        "VictoriaMetrics datasource so dashboards work out of the box."
    ),
    "tags": ["grafana", "dashboards", "observability", "systemd"],
    "variables": [
        {"name": "cluster_id", "label": "Deployment name",
         "type": "string", "default": "opensible-grafana"},
        {"name": "grafana_version", "label": "Grafana version",
         "type": "string", "default": "11.3.0",
         "help": "Any version tag from https://grafana.com/grafana/download. Use 'latest' to always pull the newest."},
        {"name": "listen_port", "label": "HTTP listen port",
         "type": "number", "default": 3000},
        {"name": "admin_user", "label": "Admin username",
         "type": "string", "default": "admin"},
        {"name": "admin_password", "label": "Admin password",
         "type": "password", "required": False, "default": "",
         "help": "Leave blank to derive a stable password from the deployment name (printed at end of run)."},
        {"name": "anonymous_enabled", "label": "Allow anonymous read-only access",
         "type": "boolean", "default": False},
        {"name": "root_url", "label": "Root URL (behind a reverse proxy)",
         "type": "string", "default": "",
         "placeholder": "https://grafana.example.com/",
         "help": "Sets GF_SERVER_ROOT_URL. Leave blank for direct access on http://host:port/."},
        {"name": "datasource_url", "label": "Prometheus / VictoriaMetrics URL",
         "type": "string", "default": "",
         "placeholder": "http://127.0.0.1:8428",
         "help": "Leave blank to skip datasource provisioning. Any Prometheus-compatible endpoint (Prometheus, VictoriaMetrics, Thanos) works."},
        {"name": "datasource_name", "label": "Datasource name",
         "type": "string", "default": "Prometheus"},
        {"name": "open_firewall", "label": "Open the listen port in UFW/firewalld",
         "type": "boolean", "default": True},
        {"name": "become", "label": "Run as sudo (become)",
         "type": "boolean", "default": True},
        VAULT_FILES_VARIABLE,
    ],
}


def suggested_filename(values: Dict[str, Any]) -> str:
    stem = slugify(values.get("cluster_id"), "grafana")
    return f"{stem}-grafana.yml"


def _derive_password(cluster_id: str) -> str:
    return hashlib.sha256(f"grafana-admin::{cluster_id}".encode("utf-8")).hexdigest()[:20]


def render(values: Dict[str, Any], targets: Dict[str, Any]) -> str:
    cluster_id = str(values.get("cluster_id") or "opensible-grafana").strip() or "opensible-grafana"
    version = str(values.get("grafana_version") or "11.3.0").strip().lstrip("v") or "11.3.0"
    try:
        port = int(values.get("listen_port") or 3000)
    except Exception:
        port = 3000
    admin_user = str(values.get("admin_user") or "admin").strip() or "admin"
    admin_pw = str(values.get("admin_password") or "").strip() or _derive_password(cluster_id)
    anon = "true" if values.get("anonymous_enabled") else "false"
    root_url = str(values.get("root_url") or "").strip()
    ds_url = str(values.get("datasource_url") or "").strip()
    ds_name = str(values.get("datasource_name") or "Prometheus").strip() or "Prometheus"
    open_fw = bool(values.get("open_firewall", True))
    become = "true" if values.get("become", True) else "false"
    hosts = render_hosts(targets)

    provision_ds = bool(ds_url)

    parts: List[str] = [
        "---",
        f"# OpenSible grafana-oss template generation: 2026-07-grafana-oss-v1",
        f"# Deployment: {cluster_id}",
        "",
        "- name: Deploy Grafana OSS",
        f"  hosts: {hosts}",
        f"  become: {become}",
        "  gather_facts: true",
        *vars_files_lines(parse_vault_files(values.get("vault_files"))),
        "  vars:",
        f"    gf_cluster_id: {yaml_str(cluster_id)}",
        f"    gf_version: {yaml_str(version)}",
        f"    gf_port: {port}",
        f"    gf_admin_user: {yaml_str(admin_user)}",
        f"    gf_admin_password: {yaml_str(admin_pw)}",
        f"    gf_anon_enabled: {anon}",
        f"    gf_root_url: {yaml_str(root_url)}",
        f"    gf_ds_url: {yaml_str(ds_url)}",
        f"    gf_ds_name: {yaml_str(ds_name)}",
        f"    gf_open_firewall: {'true' if open_fw else 'false'}",
        "  tasks:",
        "    - name: Install base packages",
        "      ansible.builtin.package:",
        "        name:",
        "          - curl",
        "          - ca-certificates",
        "          - gnupg",
        "        state: present",
        "      failed_when: false",

        # ---- Debian/Ubuntu repo ----
        "    - name: Ensure /etc/apt/keyrings exists (Debian/Ubuntu)",
        "      when: ansible_os_family == 'Debian'",
        "      ansible.builtin.file:",
        "        path: /etc/apt/keyrings",
        "        state: directory",
        "        mode: '0755'",
        "    - name: Import Grafana apt signing key (dearmored)",
        "      when: ansible_os_family == 'Debian'",
        "      ansible.builtin.shell: |",
        "        set -euo pipefail",
        "        curl -fsSL https://apt.grafana.com/gpg.key | gpg --dearmor --yes -o /etc/apt/keyrings/grafana.gpg",
        "        chmod 0644 /etc/apt/keyrings/grafana.gpg",
        "      args:",
        "        executable: /bin/bash",
        "        creates: /etc/apt/keyrings/grafana.gpg",
        "    - name: Configure Grafana apt repository",
        "      when: ansible_os_family == 'Debian'",
        "      ansible.builtin.apt_repository:",
        "        repo: 'deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main'",
        "        filename: grafana",
        "        state: present",
        "        update_cache: true",
        "    - name: Install Grafana OSS (Debian/Ubuntu)",
        "      when: ansible_os_family == 'Debian'",
        "      ansible.builtin.apt:",
        "        name: \"{{ 'grafana' if gf_version == 'latest' else 'grafana=' ~ gf_version }}\"",
        "        state: present",
        "        allow_downgrade: true",
        "      register: _gf_pkg_deb",
        "      until: _gf_pkg_deb is succeeded",
        "      retries: 3",
        "      delay: 10",

        # ---- RHEL family repo ----
        "    - name: Configure Grafana dnf repo (RHEL/Rocky/Alma)",
        "      when: ansible_os_family == 'RedHat'",
        "      ansible.builtin.yum_repository:",
        "        name: grafana",
        "        description: Grafana OSS",
        "        baseurl: https://rpm.grafana.com",
        "        gpgcheck: true",
        "        gpgkey: https://rpm.grafana.com/gpg.key",
        "        enabled: true",
        "    - name: Install Grafana OSS (RHEL/Rocky/Alma)",
        "      when: ansible_os_family == 'RedHat'",
        "      ansible.builtin.dnf:",
        "        name: \"{{ 'grafana' if gf_version == 'latest' else 'grafana-' ~ gf_version }}\"",
        "        state: present",
        "        allow_downgrade: true",
        "      register: _gf_pkg_rh",
        "      until: _gf_pkg_rh is succeeded",
        "      retries: 3",
        "      delay: 10",

        # ---- systemd drop-in with admin creds + port ----
        "    - name: Systemd drop-in dir for grafana-server",
        "      ansible.builtin.file:",
        "        path: /etc/systemd/system/grafana-server.service.d",
        "        state: directory",
        "        mode: '0755'",
        "    - name: Grafana drop-in (admin creds + listen port)",
        "      ansible.builtin.copy:",
        "        dest: /etc/systemd/system/grafana-server.service.d/override.conf",
        "        mode: '0640'",
        "        owner: root",
        "        group: grafana",
        "        content: |",
        "          [Service]",
        "          Environment=\"GF_SECURITY_ADMIN_USER={{ gf_admin_user }}\"",
        "          Environment=\"GF_SECURITY_ADMIN_PASSWORD={{ gf_admin_password }}\"",
        "          Environment=\"GF_AUTH_ANONYMOUS_ENABLED={{ gf_anon_enabled }}\"",
        "          Environment=\"GF_SERVER_HTTP_PORT={{ gf_port }}\"",
        "          {% if gf_root_url | length > 0 %}Environment=\"GF_SERVER_ROOT_URL={{ gf_root_url }}\"{% endif %}",
        "      no_log: true",
    ]

    if provision_ds:
        parts += [
            "    - name: Ensure Grafana provisioning dir exists",
            "      ansible.builtin.file:",
            "        path: /etc/grafana/provisioning/datasources",
            "        state: directory",
            "        owner: root",
            "        group: grafana",
            "        mode: '0750'",
            "    - name: Provision Prometheus-compatible datasource",
            "      ansible.builtin.copy:",
            "        dest: /etc/grafana/provisioning/datasources/opensible.yaml",
            "        owner: root",
            "        group: grafana",
            "        mode: '0640'",
            "        content: |",
            "          apiVersion: 1",
            "          datasources:",
            "            - name: {{ gf_ds_name }}",
            "              type: prometheus",
            "              access: proxy",
            "              url: {{ gf_ds_url }}",
            "              isDefault: true",
            "              editable: true",
        ]

    parts += [
        "    - name: Enable & (re)start grafana-server",
        "      ansible.builtin.systemd:",
        "        name: grafana-server",
        "        enabled: true",
        "        state: restarted",
        "        daemon_reload: true",
        "    - name: Open port {{ gf_port }}/tcp (UFW)",
        "      when: gf_open_firewall and ansible_os_family == 'Debian'",
        "      ansible.builtin.shell: |",
        "        if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q 'Status: active'; then",
        "          ufw allow {{ gf_port }}/tcp || true",
        "        fi",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Open port {{ gf_port }}/tcp (firewalld)",
        "      when: gf_open_firewall and ansible_os_family == 'RedHat'",
        "      ansible.builtin.shell: |",
        "        if systemctl is-active --quiet firewalld; then",
        "          firewall-cmd --permanent --add-port={{ gf_port }}/tcp || true",
        "          firewall-cmd --reload || true",
        "        fi",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Wait for Grafana HTTP endpoint",
        "      ansible.builtin.wait_for:",
        "        host: 127.0.0.1",
        "        port: \"{{ gf_port }}\"",
        "        timeout: 120",
        "    - name: Report Grafana URL and admin credentials",
        "      ansible.builtin.debug:",
        "        msg:",
        "          - \"Grafana:    http://{{ ansible_default_ipv4.address | default('127.0.0.1') }}:{{ gf_port }}/\"",
        "          - \"Username:   {{ gf_admin_user }}\"",
        "          - \"Password:   {{ gf_admin_password }}\"",
        "",
    ]
    return "\n".join(parts)
