"""Template: Prometheus or VictoriaMetrics single-node server (systemd).

Downloads the pinned release from GitHub, installs binaries under /usr/local/bin,
creates a dedicated system user, renders a config with configurable scrape
targets (for Prometheus) and manages the service via systemd.

Marker: 2026-07-metrics-server-v1
"""
from __future__ import annotations

from typing import Any, Dict, List

from ._common import (
    render_hosts,
    yaml_str,
    slugify,
    indent_block,
    VAULT_FILES_VARIABLE,
    parse_vault_files,
    vars_files_lines,
)


TEMPLATE = {
    "id": "metrics-server",
    "name": "Prometheus / VictoriaMetrics",
    "category": "Observability",
    "icon": "line-chart",
    "description": (
        "Install a single-node time-series metrics server on the selected host. "
        "Choose Prometheus (with a rendered scrape config) or VictoriaMetrics "
        "(drop-in Prometheus-compatible endpoint, better compression and lower "
        "RAM). Runs under a dedicated system user via systemd — no Docker."
    ),
    "tags": ["prometheus", "victoriametrics", "metrics", "systemd", "observability"],
    "variables": [
        {"name": "cluster_id", "label": "Deployment name",
         "type": "string", "default": "opensible-metrics"},
        {"name": "backend", "label": "Backend",
         "type": "select", "default": "victoriametrics",
         "options": [
             {"label": "VictoriaMetrics (recommended)", "value": "victoriametrics"},
             {"label": "Prometheus", "value": "prometheus"},
         ]},
        {"name": "vm_version", "label": "VictoriaMetrics version",
         "type": "string", "default": "1.145.0"},
        {"name": "prometheus_version", "label": "Prometheus version",
         "type": "string", "default": "2.55.1"},
        {"name": "listen_port", "label": "HTTP listen port",
         "type": "number", "default": 8428,
         "help": "8428 is the VictoriaMetrics default; use 9090 for Prometheus if you prefer."},
        {"name": "retention", "label": "Retention",
         "type": "string", "default": "90d",
         "help": "VictoriaMetrics: 30d / 90d / 1y. Prometheus: 15d / 90d. Passed via CLI flags."},
        {"name": "data_dir", "label": "Data directory",
         "type": "string", "default": "/srv/metrics"},
        {"name": "scrape_interval", "label": "Global scrape interval (Prometheus)",
         "type": "string", "default": "15s"},
        {"name": "node_exporter_targets", "label": "node_exporter targets (Prometheus)",
         "type": "code", "language": "yaml", "rows": 4,
         "default": "# - 10.0.0.10:9100\n# - 10.0.0.11:9100\n",
         "help": "One host:port per line — added as a scrape job named 'node'. VictoriaMetrics itself does not scrape; use vmagent or Prometheus in remote_write mode."},
        {"name": "open_firewall", "label": "Open the listen port in UFW/firewalld",
         "type": "boolean", "default": True},
        {"name": "become", "label": "Run as sudo (become)",
         "type": "boolean", "default": True},
        VAULT_FILES_VARIABLE,
    ],
}


def suggested_filename(values: Dict[str, Any]) -> str:
    stem = slugify(values.get("cluster_id"), "metrics")
    backend = str(values.get("backend") or "victoriametrics")
    return f"{stem}-{backend}.yml"


def _parse_targets(raw: Any) -> List[str]:
    if not raw:
        return []
    out: List[str] = []
    for ln in str(raw).splitlines():
        s = ln.strip().lstrip("-").strip().strip("'\"")
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def render(values: Dict[str, Any], targets: Dict[str, Any]) -> str:
    cluster_id = str(values.get("cluster_id") or "opensible-metrics").strip() or "opensible-metrics"
    backend = str(values.get("backend") or "victoriametrics").lower()
    if backend not in ("prometheus", "victoriametrics"):
        backend = "victoriametrics"
    vm_version = str(values.get("vm_version") or "1.145.0").strip().lstrip("v") or "1.145.0"
    prom_version = str(values.get("prometheus_version") or "2.55.1").strip().lstrip("v") or "2.55.1"
    try:
        port = int(values.get("listen_port") or (8428 if backend == "victoriametrics" else 9090))
    except Exception:
        port = 8428 if backend == "victoriametrics" else 9090
    retention = str(values.get("retention") or "90d").strip() or "90d"
    data_dir = str(values.get("data_dir") or "/srv/metrics").strip() or "/srv/metrics"
    scrape_interval = str(values.get("scrape_interval") or "15s").strip() or "15s"
    scrape_targets = _parse_targets(values.get("node_exporter_targets"))
    open_fw = bool(values.get("open_firewall", True))
    become = "true" if values.get("become", True) else "false"
    hosts = render_hosts(targets)

    # ---- Prometheus config ---------------------------------------------------
    prom_cfg_lines = [
        f"global:",
        f"  scrape_interval: {scrape_interval}",
        f"  evaluation_interval: {scrape_interval}",
        f"  external_labels:",
        f"    cluster: {cluster_id}",
        "scrape_configs:",
        "  - job_name: 'prometheus'",
        "    static_configs:",
        f"      - targets: ['127.0.0.1:{port}']",
    ]
    if scrape_targets:
        prom_cfg_lines += [
            "  - job_name: 'node'",
            "    static_configs:",
            "      - targets: [" + ", ".join(f"'{t}'" for t in scrape_targets) + "]",
        ]
    prometheus_yml = "\n".join(prom_cfg_lines) + "\n"

    parts: List[str] = [
        "---",
        f"# OpenSible metrics-server template generation: 2026-07-metrics-server-v1",
        f"# Deployment: {cluster_id} | backend: {backend}",
        "",
        f"- name: Deploy {backend} single-node server",
        f"  hosts: {hosts}",
        f"  become: {become}",
        "  gather_facts: true",
        *vars_files_lines(parse_vault_files(values.get("vault_files"))),
        "  vars:",
        f"    ms_cluster_id: {yaml_str(cluster_id)}",
        f"    ms_backend: {yaml_str(backend)}",
        f"    ms_vm_version: {yaml_str(vm_version)}",
        f"    ms_prom_version: {yaml_str(prom_version)}",
        f"    ms_port: {port}",
        f"    ms_retention: {yaml_str(retention)}",
        f"    ms_data_dir: {yaml_str(data_dir)}",
        f"    ms_open_firewall: {'true' if open_fw else 'false'}",
        "  tasks:",
        "    - name: Install base packages",
        "      ansible.builtin.package:",
        "        name:",
        "          - curl",
        "          - tar",
        "          - ca-certificates",
        "        state: present",
        "      failed_when: false",
        "    - name: Detect CPU architecture (amd64/arm64)",
        "      ansible.builtin.set_fact:",
        "        ms_arch: \"{{ 'arm64' if ansible_architecture in ['aarch64','arm64'] else 'amd64' }}\"",
        "    - name: Create service system user",
        "      ansible.builtin.user:",
        "        name: \"{{ ms_backend }}\"",
        "        system: true",
        "        shell: /usr/sbin/nologin",
        "        home: \"{{ ms_data_dir }}\"",
        "        create_home: false",
        "    - name: Ensure data directory exists",
        "      ansible.builtin.file:",
        "        path: \"{{ ms_data_dir }}\"",
        "        state: directory",
        "        owner: \"{{ ms_backend }}\"",
        "        group: \"{{ ms_backend }}\"",
        "        mode: '0750'",

        # ---- VictoriaMetrics branch ----
        "    - name: Download VictoriaMetrics tarball",
        "      when: ms_backend == 'victoriametrics'",
        "      ansible.builtin.get_url:",
        "        url: \"https://github.com/VictoriaMetrics/VictoriaMetrics/releases/download/v{{ ms_vm_version }}/victoria-metrics-linux-{{ ms_arch }}-v{{ ms_vm_version }}.tar.gz\"",
        "        dest: \"/tmp/victoria-metrics-v{{ ms_vm_version }}.tar.gz\"",
        "        mode: '0644'",
        "      register: _vm_dl",
        "      until: _vm_dl is succeeded",
        "      retries: 3",
        "      delay: 10",
        "    - name: Extract VictoriaMetrics binary",
        "      when: ms_backend == 'victoriametrics'",
        "      ansible.builtin.unarchive:",
        "        src: \"/tmp/victoria-metrics-v{{ ms_vm_version }}.tar.gz\"",
        "        dest: /usr/local/bin/",
        "        remote_src: true",
        "        owner: root",
        "        group: root",
        "        mode: '0755'",
        "    - name: Write VictoriaMetrics systemd unit",
        "      when: ms_backend == 'victoriametrics'",
        "      ansible.builtin.copy:",
        "        dest: /etc/systemd/system/victoriametrics.service",
        "        mode: '0644'",
        "        content: |",
        "          [Unit]",
        "          Description=VictoriaMetrics time-series database",
        "          After=network.target",
        "          [Service]",
        "          Type=simple",
        "          User=victoriametrics",
        "          Group=victoriametrics",
        "          ExecStart=/usr/local/bin/victoria-metrics-prod \\",
        "            -storageDataPath={{ ms_data_dir }} \\",
        "            -retentionPeriod={{ ms_retention }} \\",
        "            -httpListenAddr=0.0.0.0:{{ ms_port }}",
        "          Restart=always",
        "          RestartSec=5",
        "          LimitNOFILE=65535",
        "          [Install]",
        "          WantedBy=multi-user.target",
        "    - name: Enable & start VictoriaMetrics",
        "      when: ms_backend == 'victoriametrics'",
        "      ansible.builtin.systemd:",
        "        name: victoriametrics",
        "        enabled: true",
        "        state: restarted",
        "        daemon_reload: true",

        # ---- Prometheus branch ----
        "    - name: Download Prometheus tarball",
        "      when: ms_backend == 'prometheus'",
        "      ansible.builtin.get_url:",
        "        url: \"https://github.com/prometheus/prometheus/releases/download/v{{ ms_prom_version }}/prometheus-{{ ms_prom_version }}.linux-{{ ms_arch }}.tar.gz\"",
        "        dest: \"/tmp/prometheus-{{ ms_prom_version }}.tar.gz\"",
        "        mode: '0644'",
        "      register: _prom_dl",
        "      until: _prom_dl is succeeded",
        "      retries: 3",
        "      delay: 10",
        "    - name: Extract Prometheus release",
        "      when: ms_backend == 'prometheus'",
        "      ansible.builtin.unarchive:",
        "        src: \"/tmp/prometheus-{{ ms_prom_version }}.tar.gz\"",
        "        dest: /tmp/",
        "        remote_src: true",
        "    - name: Install prometheus + promtool binaries",
        "      when: ms_backend == 'prometheus'",
        "      ansible.builtin.copy:",
        "        src: \"/tmp/prometheus-{{ ms_prom_version }}.linux-{{ ms_arch }}/{{ item }}\"",
        "        dest: \"/usr/local/bin/{{ item }}\"",
        "        remote_src: true",
        "        owner: root",
        "        group: root",
        "        mode: '0755'",
        "      loop:",
        "        - prometheus",
        "        - promtool",
        "    - name: Ensure /etc/prometheus exists",
        "      when: ms_backend == 'prometheus'",
        "      ansible.builtin.file:",
        "        path: /etc/prometheus",
        "        state: directory",
        "        owner: prometheus",
        "        group: prometheus",
        "        mode: '0755'",
        "    - name: Write /etc/prometheus/prometheus.yml",
        "      when: ms_backend == 'prometheus'",
        "      ansible.builtin.copy:",
        "        dest: /etc/prometheus/prometheus.yml",
        "        owner: prometheus",
        "        group: prometheus",
        "        mode: '0644'",
        "        content: |",
        indent_block(prometheus_yml.rstrip("\n"), "          "),
        "    - name: Write Prometheus systemd unit",
        "      when: ms_backend == 'prometheus'",
        "      ansible.builtin.copy:",
        "        dest: /etc/systemd/system/prometheus.service",
        "        mode: '0644'",
        "        content: |",
        "          [Unit]",
        "          Description=Prometheus monitoring server",
        "          After=network.target",
        "          [Service]",
        "          Type=simple",
        "          User=prometheus",
        "          Group=prometheus",
        "          ExecStart=/usr/local/bin/prometheus \\",
        "            --config.file=/etc/prometheus/prometheus.yml \\",
        "            --storage.tsdb.path={{ ms_data_dir }} \\",
        "            --storage.tsdb.retention.time={{ ms_retention }} \\",
        "            --web.listen-address=0.0.0.0:{{ ms_port }}",
        "          Restart=always",
        "          RestartSec=5",
        "          LimitNOFILE=65535",
        "          [Install]",
        "          WantedBy=multi-user.target",
        "    - name: Enable & start Prometheus",
        "      when: ms_backend == 'prometheus'",
        "      ansible.builtin.systemd:",
        "        name: prometheus",
        "        enabled: true",
        "        state: restarted",
        "        daemon_reload: true",

        # ---- firewall + health ----
        "    - name: Open port {{ ms_port }}/tcp (UFW)",
        "      when: ms_open_firewall and ansible_os_family == 'Debian'",
        "      ansible.builtin.shell: |",
        "        if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q 'Status: active'; then",
        "          ufw allow {{ ms_port }}/tcp || true",
        "        fi",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Open port {{ ms_port }}/tcp (firewalld)",
        "      when: ms_open_firewall and ansible_os_family == 'RedHat'",
        "      ansible.builtin.shell: |",
        "        if systemctl is-active --quiet firewalld; then",
        "          firewall-cmd --permanent --add-port={{ ms_port }}/tcp || true",
        "          firewall-cmd --reload || true",
        "        fi",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Wait for metrics server HTTP endpoint",
        "      ansible.builtin.wait_for:",
        "        host: 127.0.0.1",
        "        port: \"{{ ms_port }}\"",
        "        timeout: 90",
        "    - name: Report metrics server URL",
        "      ansible.builtin.debug:",
        "        msg: \"{{ ms_backend }} listening on http://{{ ansible_default_ipv4.address | default('127.0.0.1') }}:{{ ms_port }}\"",
        "",
    ]
    return "\n".join(parts)
