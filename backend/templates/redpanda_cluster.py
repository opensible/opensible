"""Template: Redpanda Self-Managed cluster + Redpanda Console (web UI).

Deploys a production-grade Redpanda cluster from the **official
Redpanda apt/dnf repository** (no Docker, no Bitnami) managed by
**systemd**, plus the **Redpanda Console** web UI on the first node.

Topology
--------
Each host runs the ``redpanda`` service. One host = dev; 3+ hosts = HA.
Seed servers, advertised Kafka/RPC listeners, and node IDs are computed
from the inventory. The first broker also runs ``redpanda-console``
(browser UI on the configured HTTP port).

Two ways to select hosts:

1. **Broker nodes** (recommended). Provide ``{name, ip, ssh_user,
   ssh_port}`` rows in the UI. The playbook builds its inventory via
   ``add_host``.
2. **Legacy targets.** If no brokers are provided, fall back to the
   generic ``hosts:``/``groups:`` picker (single-node only).

Marker: 2026-07-redpanda-v1
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
    "id": "redpanda-cluster",
    "name": "Redpanda Cluster (Self-Managed) + Console",
    "category": "Data & Streaming",
    "icon": "activity",
    "description": (
        "Install a production-grade Redpanda Self-Managed cluster from "
        "the official Redpanda apt/dnf repo on systemd (no Docker, no "
        "Bitnami). Add each broker under 'Broker nodes' — one host for "
        "dev, 3+ for HA. Seed servers, advertised listeners and node "
        "IDs are computed from the inventory. Redpanda Console (web UI) "
        "is deployed on the first broker."
    ),
    "tags": ["redpanda", "kafka-api", "streaming", "systemd", "ha", "console"],
    "variables": [
        # ---------- Cluster identity ----------
        {"name": "cluster_id", "label": "Cluster name",
         "type": "string", "default": "opensible-redpanda",
         "help": "Free-form cluster name used for filenames and logs."},
        {"name": "redpanda_channel", "label": "Redpanda release channel",
         "type": "select",
         "options": [
             {"label": "Stable (recommended)", "value": "redpanda"},
             {"label": "Unstable (preview)", "value": "redpanda-unstable"},
         ],
         "default": "redpanda",
         "help": "Which Redpanda apt/dnf repository to enable."},

        # ---------- Hosts / HA ----------
        {"name": "ssh_user_default", "label": "Default SSH user for nodes",
         "type": "string", "default": "root"},
        {"name": "ssh_port_default", "label": "Default SSH port",
         "type": "number", "default": 22},
        {"name": "brokers", "label": "Broker nodes",
         "type": "nodes", "required": False,
         "help": "One entry per host. First entry also runs Redpanda Console. Use 1 host for dev, 3 or 5 for HA.",
         "default": [{"name": "redpanda-1", "ip": "", "ssh_user": "", "ssh_port": ""}]},

        # ---------- Listeners ----------
        {"name": "kafka_port", "label": "Kafka API port",
         "type": "number", "default": 9092},
        {"name": "rpc_port", "label": "Internal RPC port",
         "type": "number", "default": 33145},
        {"name": "admin_port", "label": "Admin API port",
         "type": "number", "default": 9644},
        {"name": "schema_registry_port", "label": "Schema Registry port",
         "type": "number", "default": 8081},
        {"name": "pandaproxy_port", "label": "Pandaproxy (REST) port",
         "type": "number", "default": 8082},

        # ---------- Console (Web UI) ----------
        {"name": "console_enabled", "label": "Install Redpanda Console (Web UI)",
         "type": "boolean", "default": True,
         "help": "Deploys the Redpanda Console on the first broker."},
        {"name": "console_port", "label": "Console HTTP port",
         "type": "number", "default": 8080},

        # ---------- Tuning ----------
        {"name": "developer_mode", "label": "Developer mode (skip tuning)",
         "type": "boolean", "default": False,
         "help": "Enable for VMs / small hosts. Disables kernel tuning. Recommended off for production bare-metal."},
        {"name": "seastar_smp", "label": "SMP core count (0 = auto)",
         "type": "number", "default": 0,
         "help": "Number of cores Redpanda pins to. 0 leaves it to auto-detect."},
        {"name": "seastar_memory", "label": "Memory reservation (e.g. 4G, 0 = auto)",
         "type": "string", "default": "0",
         "help": "Amount of RAM Redpanda reserves. '0' or empty lets Redpanda auto-detect."},

        # ---------- Layout ----------
        {"name": "data_dir", "label": "Data directory",
         "type": "string", "default": "/var/lib/redpanda/data",
         "help": "Persisted on the host so logs and metadata survive restarts."},
        {"name": "open_firewall", "label": "Open ports in UFW/firewalld",
         "type": "boolean", "default": True},
        {"name": "become", "label": "Run as sudo (become)",
         "type": "boolean", "default": True},
        VAULT_FILES_VARIABLE,
    ],
}


def suggested_filename(values: Dict[str, Any]) -> str:
    stem = slugify(values.get("cluster_id"), "redpanda")
    return f"{stem}-redpanda-cluster.yml"


def _norm_nodes(raw: Any, default_user: str, default_port: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for i, n in enumerate(raw):
        if not isinstance(n, dict):
            continue
        ip = str(n.get("ip") or "").strip()
        if not ip:
            continue
        name = str(n.get("name") or f"redpanda-{i+1}").strip() or f"redpanda-{i+1}"
        user = str(n.get("ssh_user") or default_user or "root").strip() or "root"
        try:
            port = int(n.get("ssh_port") or default_port or 22)
        except Exception:
            port = 22
        out.append({
            "name": name,
            "broker_name": slugify(name, f"redpanda-{i+1}") or f"redpanda-{i+1}",
            "ip": ip,
            "ssh_user": user,
            "ssh_port": port,
            "node_index": i,
        })
    return out


def render(values: Dict[str, Any], targets: Dict[str, Any]) -> str:
    become = "true" if values.get("become", True) else "false"

    cluster_id = values.get("cluster_id") or "opensible-redpanda"
    channel = values.get("redpanda_channel") or "redpanda"
    if channel not in ("redpanda", "redpanda-unstable"):
        channel = "redpanda"

    kafka_port = int(values.get("kafka_port") or 9092)
    rpc_port = int(values.get("rpc_port") or 33145)
    admin_port = int(values.get("admin_port") or 9644)
    sr_port = int(values.get("schema_registry_port") or 8081)
    pp_port = int(values.get("pandaproxy_port") or 8082)

    console_enabled = bool(values.get("console_enabled", True))
    console_port = int(values.get("console_port") or 8080)

    developer_mode = "true" if values.get("developer_mode", False) else "false"
    smp = int(values.get("seastar_smp") or 0)
    mem = str(values.get("seastar_memory") or "0").strip() or "0"

    data_dir = values.get("data_dir") or "/var/lib/redpanda/data"
    open_firewall = bool(values.get("open_firewall", True))

    brokers = _norm_nodes(
        values.get("brokers"),
        values.get("ssh_user_default") or "root",
        values.get("ssh_port_default") or 22,
    )

    cluster_group = slugify(cluster_id, "redpanda") + "_brokers"
    seed_ips = [b["ip"] for b in brokers] if brokers else []
    first_ip = seed_ips[0] if seed_ips else ""

    parts: List[str] = ["---"]
    parts.append(f"# Rendered from template: {TEMPLATE['name']} (systemd, official repo)")
    parts.append(f"# Cluster: {cluster_id} | brokers: {len(brokers) if brokers else 'from targets'} | channel: {channel}")
    parts.append("")

    # ------------------------------------------------------------------ #
    # PLAY 0 — Build dynamic inventory from the broker list (if provided)
    # ------------------------------------------------------------------ #
    if brokers:
        parts += [
            "- name: Register Redpanda brokers into a dynamic inventory group",
            "  hosts: localhost",
            "  gather_facts: false",
            "  connection: local",
            "  tasks:",
            "    - name: add_host each broker",
            "      ansible.builtin.add_host:",
            "        name: \"{{ item.ip }}\"",
            f"        groups: {cluster_group}",
            "        ansible_host: \"{{ item.ip }}\"",
            "        ansible_user: \"{{ item.ssh_user }}\"",
            "        ansible_port: \"{{ item.ssh_port }}\"",
            "        broker_name: \"{{ item.broker_name }}\"",
            "        redpanda_node_index: \"{{ item.node_index }}\"",
            "        redpanda_is_first: \"{{ item.node_index == 0 }}\"",
            "      loop:",
        ]
        for b in brokers:
            parts.append(
                "        - { "
                f"name: {yaml_str(b['name'])}, "
                f"broker_name: {yaml_str(b['broker_name'])}, "
                f"ip: {yaml_str(b['ip'])}, "
                f"ssh_user: {yaml_str(b['ssh_user'])}, "
                f"ssh_port: {b['ssh_port']}, "
                f"node_index: {b['node_index']}"
                " }"
            )
        parts.append("")
        play_hosts = cluster_group
    else:
        play_hosts = render_hosts(targets)

    # Seed servers YAML block for Ansible vars.
    seeds_yaml = "[]"
    if seed_ips:
        seed_items = [f"{{host: {{address: {yaml_str(ip)}, port: {rpc_port}}}}}" for ip in seed_ips]
        seeds_yaml = "[" + ", ".join(seed_items) + "]"

    # Broker list for Console UI
    console_brokers_yaml = "[]"
    if seed_ips:
        console_brokers_yaml = "[" + ", ".join(yaml_str(f"{ip}:{kafka_port}") for ip in seed_ips) + "]"
    bootstrap_servers_csv = ",".join(f"{ip}:{kafka_port}" for ip in seed_ips)
    first_seed_server_yaml = "{}"
    if first_ip:
        first_seed_server_yaml = f"{{host: {{address: {yaml_str(first_ip)}, port: {rpc_port}}}}}"
    first_seed_host_yaml = yaml_str(first_ip)

    # ------------------------------------------------------------------ #
    # PLAY 1 — Deploy Redpanda on every broker
    # ------------------------------------------------------------------ #
    parts += [
        "- name: Deploy Redpanda Self-Managed cluster",
        f"  hosts: {play_hosts}",
        f"  become: {become}",
        "  gather_facts: true",
        "  any_errors_fatal: true",
        *vars_files_lines(parse_vault_files(values.get("vault_files"))),
        "  vars:",
        f"    redpanda_channel: {yaml_str(channel)}",
        f"    redpanda_cluster_name: {yaml_str(cluster_id)}",
        f"    redpanda_data_dir: {yaml_str(data_dir)}",
        f"    redpanda_kafka_port: {kafka_port}",
        f"    redpanda_rpc_port: {rpc_port}",
        f"    redpanda_admin_port: {admin_port}",
        f"    redpanda_sr_port: {sr_port}",
        f"    redpanda_pp_port: {pp_port}",
        f"    redpanda_developer_mode: {developer_mode}",
        f"    redpanda_smp: {smp}",
        f"    redpanda_memory: {yaml_str(mem)}",
        f"    redpanda_seed_servers: {seeds_yaml}",
        f"    redpanda_first_seed_server: {first_seed_server_yaml}",
        f"    redpanda_first_seed_host: {first_seed_host_yaml}",
        f"    redpanda_seed_ip_csv: {yaml_str(','.join(seed_ips))}",
        f"    redpanda_bootstrap_servers: {yaml_str(bootstrap_servers_csv)}",
        "    redpanda_advertised_host: \"{{ ansible_host | default(inventory_hostname) }}\"",
        "  tasks:",

        # ---------- Low-memory guard (Ubuntu/Debian small VMs get OOM-killed during apt) ----------
        "    - name: Gather memory facts",
        "      ansible.builtin.setup:",
        "        gather_subset: hardware",
        "      when: ansible_os_family == 'Debian'",
        "    - name: Detect memory available to Redpanda",
        "      ansible.builtin.shell: |",
        "        set -e",
        "        mem_kb=$(awk '/MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)",
        "        mem_bytes=$((mem_kb * 1024))",
        "        cgroup_max=''",
        "        if [ -f /sys/fs/cgroup/memory.max ]; then",
        "          cgroup_max=$(cat /sys/fs/cgroup/memory.max 2>/dev/null || true)",
        "        elif [ -f /sys/fs/cgroup/memory/memory.limit_in_bytes ]; then",
        "          cgroup_max=$(cat /sys/fs/cgroup/memory/memory.limit_in_bytes 2>/dev/null || true)",
        "        fi",
        "        if [ -n \"$cgroup_max\" ] && [ \"$cgroup_max\" != \"max\" ] && [ \"$cgroup_max\" -gt 0 ] 2>/dev/null; then",
        "          [ \"$cgroup_max\" -lt \"$mem_bytes\" ] && mem_bytes=\"$cgroup_max\"",
        "        fi",
        "        echo \"$mem_bytes\"",
        "      args:",
        "        executable: /bin/bash",
        "      register: rp_available_memory_bytes_cmd",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Resolve Redpanda low-memory runtime mode",
        "      ansible.builtin.set_fact:",
        "        rp_available_memory_bytes: \"{{ rp_available_memory_bytes_cmd.stdout | default('0') | int }}\"",
        "        rp_low_memory_host: \"{{ (rp_available_memory_bytes_cmd.stdout | default('0') | int) < 1073741824 }}\"",
        "    - name: Ensure /swapfile exists on low-memory Debian/Ubuntu hosts",
        "      ansible.builtin.shell: |",
        "        set -e",
        "        if [ ! -f /swapfile ]; then",
        "          fallocate -l 1G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=1024",
        "          chmod 600 /swapfile",
        "          mkswap /swapfile",
        "          swapon /swapfile",
        "          grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab",
        "        fi",
        "      args:",
        "        executable: /bin/bash",
        "      when:",
        "        - ansible_os_family == 'Debian'",
        "        - (ansible_memtotal_mb | default(4096)) < 1400",
        "      failed_when: false",

        # ---------- Prerequisites ----------
        "    - name: Install prerequisites (Debian/Ubuntu)",
        "      ansible.builtin.shell: |",
        "        set -e",
        "        export DEBIAN_FRONTEND=noninteractive",
        "        apt-get update -y",
        "        apt-get install -y --no-install-recommends curl gnupg ca-certificates apt-transport-https",
        "      args:",
        "        executable: /bin/bash",
        "      when: ansible_os_family == 'Debian'",
        "    - name: Install prerequisites (RHEL family)",
        "      ansible.builtin.yum:",
        "        name: [curl, ca-certificates]",
        "        state: present",
        "      when: ansible_os_family == 'RedHat'",

        # ---------- Repository setup via official script ----------
        "    - name: Register Redpanda apt repo (Debian/Ubuntu)",
        "      ansible.builtin.shell: |",
        "        set -e",
        "        if [ ! -f /etc/apt/sources.list.d/redpanda.list ]; then",
        "          curl -1sLf 'https://dl.redpanda.com/nzc4ZYQK3WRGd9sy/{{ redpanda_channel }}/setup.deb.sh' | bash",
        "        fi",
        "      args:",
        "        executable: /bin/bash",
        "        creates: /etc/apt/sources.list.d/redpanda.list",
        "      when: ansible_os_family == 'Debian'",
        "    - name: Register Redpanda yum repo (RHEL family)",
        "      ansible.builtin.shell: |",
        "        set -e",
        "        if [ ! -f /etc/yum.repos.d/redpanda.repo ]; then",
        "          curl -1sLf 'https://dl.redpanda.com/nzc4ZYQK3WRGd9sy/{{ redpanda_channel }}/setup.rpm.sh' | bash",
        "        fi",
        "      args:",
        "        executable: /bin/bash",
        "        creates: /etc/yum.repos.d/redpanda.repo",
        "      when: ansible_os_family == 'RedHat'",

        # ---------- Install Redpanda ----------
        "    - name: Install redpanda (Debian/Ubuntu)",
        "      ansible.builtin.shell: |",
        "        set -e",
        "        export DEBIAN_FRONTEND=noninteractive",
        "        apt-get update -y",
        "        apt-get install -y --no-install-recommends redpanda",
        "      args:",
        "        executable: /bin/bash",
        "        creates: /usr/bin/rpk",
        "      when: ansible_os_family == 'Debian'",

        "    - name: Install redpanda (RHEL family)",
        "      ansible.builtin.yum:",
        "        name: redpanda",
        "        state: present",
        "      when: ansible_os_family == 'RedHat'",

        # ---------- Ensure directories ----------
        "    - name: Ensure data directory exists",
        "      ansible.builtin.file:",
        "        path: \"{{ redpanda_data_dir }}\"",
        "        state: directory",
        "        owner: redpanda",
        "        group: redpanda",
        "        mode: '0750'",

        # ---------- Configure Redpanda ----------
        # Keep redpanda.yaml to node-local settings only. Redpanda rejects
        # cluster-level properties (auto_create_topics_enabled,
        # group_topic_partitions, write_caching_default, etc.) in this file on
        # newer releases, which prevents redpanda.service from starting.
        # Cluster-level defaults are applied later through `rpk cluster config`.
        "    - name: Compute single-node flag",
        "      ansible.builtin.set_fact:",
        "        rp_is_single_node: \"{{ (redpanda_seed_servers | length) <= 1 }}\"",
        "    - name: Ensure /etc/redpanda exists",
        "      ansible.builtin.file:",
        "        path: /etc/redpanda",
        "        state: directory",
        "        owner: redpanda",
        "        group: redpanda",
        "        mode: '0755'",
        "    - name: Check existing redpanda.yaml",
        "      ansible.builtin.stat:",
        "        path: /etc/redpanda/redpanda.yaml",
        "      register: rp_yaml_stat",
        "    - name: Preserve existing Redpanda node_id from config",
        "      ansible.builtin.shell: |",
        "        set -e",
        "        sed -n 's/^[[:space:]]*node_id:[[:space:]]*//p' /etc/redpanda/redpanda.yaml | head -1 | tr -d '\"' | grep -v '^null$' || true",
        "      args:",
        "        executable: /bin/bash",
        "      register: rp_existing_node_id_cmd",
        "      changed_when: false",
        "      failed_when: false",
        "      when: rp_yaml_stat.stat.exists | bool",
        "    - name: Resolve existing Redpanda node identity",
        "      ansible.builtin.set_fact:",
        "        rp_existing_node_id: \"{{ rp_existing_node_id_cmd.stdout | default('') | trim }}\"",
        "    - name: Resolve Redpanda node_id for this broker",
        "      ansible.builtin.set_fact:",
        "        rp_effective_node_id: \"{{ (rp_existing_node_id | default('') | trim) if ((rp_existing_node_id | default('') | trim) | length > 0) else (redpanda_node_index | default(0) | int) }}\"",
        "    - name: Resolve Redpanda startup safety settings",
        "      ansible.builtin.set_fact:",
        "        rp_runtime_developer_mode: \"{{ (redpanda_developer_mode | bool) or (rp_low_memory_host | bool) }}\"",
        "        rp_runtime_start_mode: \"{{ 'dev-container' if ((rp_is_single_node | bool) and ((rp_low_memory_host | bool) or (redpanda_developer_mode | bool))) else ('low-memory-cluster' if ((rp_low_memory_host | bool) or (redpanda_developer_mode | bool)) else 'production-auto') }}\"",
        "    - name: Resolve Redpanda effective runtime sizing",
        "      ansible.builtin.set_fact:",
        "        rp_effective_smp: \"{{ (redpanda_smp | int) if ((redpanda_smp | int) > 0) else (1 if (rp_runtime_developer_mode | bool) else 0) }}\"",
        "        rp_effective_memory: \"{{ (redpanda_memory | string) if ((redpanda_memory | string | trim) not in ['', '0']) else ('512M' if (rp_runtime_developer_mode | bool) else '0') }}\"",
        "    - name: Resolve node-local seed servers",
        "      ansible.builtin.set_fact:",
        "        rp_node_seed_servers: \"{{ [] if ((rp_is_single_node | bool) or (redpanda_is_first | default(false) | bool)) else [redpanda_first_seed_server] }}\"",
        "        rp_empty_seed_starts_cluster: \"{{ (rp_is_single_node | bool) or (redpanda_is_first | default(false) | bool) }}\"",
        "    - name: Write Redpanda node config",
        "      ansible.builtin.copy:",
        "        dest: /etc/redpanda/redpanda.yaml",
        "        owner: redpanda",
        "        group: redpanda",
        "        mode: '0644'",
        "        content: |",
        "          # Managed by OpenSible - do not edit by hand",
        "          redpanda:",
        "            data_directory: {{ redpanda_data_dir }}",
        "            node_id: {{ rp_effective_node_id | int }}",
        "            developer_mode: {{ rp_runtime_developer_mode | bool | lower }}",
        "            empty_seed_starts_cluster: {{ rp_empty_seed_starts_cluster | bool | lower }}",
        "            rpc_server:",
        "              address: 0.0.0.0",
        "              port: {{ redpanda_rpc_port }}",
        "            advertised_rpc_api:",
        "              address: {{ redpanda_advertised_host }}",
        "              port: {{ redpanda_rpc_port }}",
        "            kafka_api:",
        "              - address: 0.0.0.0",
        "                port: {{ redpanda_kafka_port }}",
        "            advertised_kafka_api:",
        "              - address: {{ redpanda_advertised_host }}",
        "                port: {{ redpanda_kafka_port }}",
        "            admin:",
        "              - address: 0.0.0.0",
        "                port: {{ redpanda_admin_port }}",
        "            seed_servers: {{ rp_node_seed_servers | to_json }}",
        "          rpk:",
        "            coredump_dir: /var/lib/redpanda/coredump",
        "          pandaproxy:",
        "            pandaproxy_api:",
        "              - address: 0.0.0.0",
        "                port: {{ redpanda_pp_port }}",
        "            advertised_pandaproxy_api:",
        "              - address: {{ redpanda_advertised_host }}",
        "                port: {{ redpanda_pp_port }}",
        "          schema_registry:",
        "            schema_registry_api:",
        "              - address: 0.0.0.0",
        "                port: {{ redpanda_sr_port }}",
        "      changed_when: true",
        "    - name: Ensure redpanda owns config + data dir",
        "      ansible.builtin.file:",
        "        path: \"{{ item }}\"",
        "        state: directory",
        "        owner: redpanda",
        "        group: redpanda",
        "        recurse: true",
        "      loop:",
        "        - /etc/redpanda",
        "        - \"{{ redpanda_data_dir }}\"",
        "        - /var/lib/redpanda/coredump",

        "    - name: Read rendered redpanda.yaml for syntax validation",
        "      ansible.builtin.slurp:",
        "        src: /etc/redpanda/redpanda.yaml",
        "      register: rp_yaml_slurp",
        "      changed_when: false",
        "    - name: Validate rendered redpanda.yaml parses as YAML",
        "      ansible.builtin.set_fact:",
        "        rp_rendered_config: \"{{ rp_yaml_slurp.content | b64decode | from_yaml }}\"",
        "      changed_when: false",
        "    - name: Validate rendered redpanda.yaml has required node-local sections",
        "      ansible.builtin.assert:",
        "        that:",
        "          - rp_rendered_config is mapping",
        "          - rp_rendered_config.redpanda is mapping",
        "          - rp_rendered_config.redpanda.data_directory is defined",
        "          - rp_rendered_config.redpanda.node_id is defined",
        "          - rp_rendered_config.redpanda.kafka_api is defined",
        "          - rp_rendered_config.redpanda.admin is defined",
        "        fail_msg: 'Rendered /etc/redpanda/redpanda.yaml is missing required Redpanda node-local settings.'",
        "        success_msg: 'Rendered /etc/redpanda/redpanda.yaml is valid YAML with required Redpanda settings.'",

        # ---------- systemd drop-in: raise limits ----------
        "    - name: Ensure redpanda systemd drop-in dir",
        "      ansible.builtin.file:",
        "        path: /etc/systemd/system/redpanda.service.d",
        "        state: directory",
        "        mode: '0755'",
        "    - name: Install Redpanda startup wrapper",
        "      ansible.builtin.copy:",
        "        dest: /usr/local/sbin/opensible-redpanda-start",
        "        owner: root",
        "        group: root",
        "        mode: '0755'",
        "        content: |",
        "          #!/usr/bin/env bash",
        "          set -euo pipefail",
        "          cfg=/etc/redpanda/redpanda.yaml",
        "          help=$(/usr/bin/rpk redpanda start --help 2>&1 || true)",
        "          args=(redpanda start --config \"$cfg\")",
        "          has_flag() { grep -q -- \"$1\" <<<\"$help\"; }",
        "          start_mode=\"${OPENSIBLE_REDPANDA_START_MODE:-production-auto}\"",
        "          if [ \"$start_mode\" = \"dev-container\" ] || [ \"$start_mode\" = \"low-memory-cluster\" ]; then",
        "            if has_flag '--mode'; then",
        "              args+=(--mode dev-container)",
        "            else",
        "              has_flag '--overprovisioned' && args+=(--overprovisioned)",
        "            fi",
        "            has_flag '--check' && args+=(--check=false)",
        "            has_flag '--reserve-memory' && args+=(--reserve-memory 0M)",
        "            has_flag '--unsafe-bypass-fsync' && args+=(--unsafe-bypass-fsync)",
        "          elif [ \"${OPENSIBLE_REDPANDA_DEV_MODE:-false}\" = \"true\" ]; then",
        "            has_flag '--overprovisioned' && args+=(--overprovisioned)",
        "            has_flag '--check' && args+=(--check=false)",
        "            has_flag '--reserve-memory' && args+=(--reserve-memory 0M)",
        "            has_flag '--unsafe-bypass-fsync' && args+=(--unsafe-bypass-fsync)",
        "          else",
        "            has_flag '--check' && args+=(--check=false)",
        "          fi",
        "          if [ \"{{ rp_effective_smp | int }}\" -gt 0 ] && has_flag '--smp'; then",
        "            args+=(--smp \"{{ rp_effective_smp | int }}\")",
        "          fi",
        "          if [ \"{{ rp_effective_memory | string }}\" != \"0\" ] && [ -n \"{{ rp_effective_memory | string }}\" ] && has_flag '--memory'; then",
        "            args+=(--memory \"{{ rp_effective_memory | string }}\")",
        "          fi",
        "          echo \"Starting Redpanda with: /usr/bin/rpk ${args[*]}\" >&2",
        "          exec /usr/bin/rpk \"${args[@]}\"",
        "    - name: Write limits and startup drop-in",
        "      ansible.builtin.copy:",
        "        dest: /etc/systemd/system/redpanda.service.d/opensible.conf",
        "        mode: '0644'",
        "        content: |",
        "          [Service]",
        "          LimitMEMLOCK=infinity",
        "          LimitNOFILE=1048576",
        "          TimeoutStartSec=300",
        "          Environment=OPENSIBLE_REDPANDA_DEV_MODE={{ rp_runtime_developer_mode | bool | lower }}",
        "          Environment=OPENSIBLE_REDPANDA_START_MODE={{ rp_runtime_start_mode }}",
        "          Environment=OPENSIBLE_REDPANDA_EFFECTIVE_SMP={{ rp_effective_smp }}",
        "          Environment=OPENSIBLE_REDPANDA_EFFECTIVE_MEMORY={{ rp_effective_memory }}",
        "          WorkingDirectory=/var/lib/redpanda",
        "          ExecStart=",
        "          ExecStart=/usr/local/sbin/opensible-redpanda-start",
        # NOTE: intentionally NOT notifying a handler here — we force-restart
        # below with failed_when:false so the diagnostic block can actually
        # run and surface the real journal on failure.

        # ---------- Tuning (skip in developer mode; never fatal) ----------
        "    - name: Run rpk redpanda tune all (production, best-effort)",
        "      ansible.builtin.command: rpk redpanda tune all",
        "      when: not rp_runtime_developer_mode | bool",
        "      changed_when: false",
        "      failed_when: false",

        "    - name: Reload systemd",
        "      ansible.builtin.systemd:",
        "        daemon_reload: true",
    ]




    if open_firewall:
        parts += [
            "    - name: Open Redpanda ports (ufw, if active)",
            "      ansible.builtin.shell: |",
            "        set -e",
            "        command -v ufw >/dev/null 2>&1 || exit 0",
            "        ufw status | grep -q 'Status: active' || exit 0",
            f"        for p in {kafka_port} {rpc_port} {admin_port} {sr_port} {pp_port}; do ufw allow ${{p}}/tcp || true; done",
            "      changed_when: false",
            "      failed_when: false",
            "    - name: Open Redpanda ports (firewalld, if active)",
            "      ansible.builtin.shell: |",
            "        set -e",
            "        command -v firewall-cmd >/dev/null 2>&1 || exit 0",
            "        firewall-cmd --state >/dev/null 2>&1 || exit 0",
            f"        for p in {kafka_port} {rpc_port} {admin_port} {sr_port} {pp_port}; do firewall-cmd --permanent --add-port=${{p}}/tcp || true; done",
            "        firewall-cmd --reload || true",
            "      changed_when: false",
            "      failed_when: false",
        ]

    parts += [
        # apt install may have started redpanda before we set config via rpk.
        # For HA, bootstrap the first broker before peers join; otherwise all
        # nodes can fail together while no seed is yet accepting RPC traffic.
        "    - name: Start bootstrap Redpanda broker first",
        "      ansible.builtin.systemd:",
        "        name: redpanda",
        "        enabled: true",
        "        state: restarted",
        "        daemon_reload: true",
        "      register: rp_start",
        "      failed_when: false",
        "      when: (rp_is_single_node | bool) or (redpanda_is_first | default(false) | bool)",
        "    - name: Wait for bootstrap Redpanda broker service",
        "      ansible.builtin.command: systemctl is-active redpanda.service",
        "      register: rp_bootstrap_service_active",
        "      changed_when: false",
        "      failed_when: false",
        "      retries: 45",
        "      delay: 2",
        "      until: rp_bootstrap_service_active.stdout | default('') == 'active'",
        "      when: (rp_is_single_node | bool) or (redpanda_is_first | default(false) | bool)",
        "    - name: Diagnose bootstrap Redpanda broker before peers wait",
        "      when:",
        "        - (rp_is_single_node | bool) or (redpanda_is_first | default(false) | bool)",
        "        - (rp_bootstrap_service_active.stdout | default('')) != 'active'",
        "      block:",
        "        - name: Dump bootstrap redpanda diagnostics",
        "          ansible.builtin.shell: |",
        "            set +e",
        "            echo '===== systemctl restart result ====='",
        "            printf '%s\\n' '{{ rp_start | default({}) | to_nice_json }}'",
        "            echo '===== systemctl status redpanda ====='",
        "            systemctl status redpanda.service --no-pager -l || true",
        "            echo '===== journalctl -u redpanda (last 260 lines) ====='",
        "            journalctl -u redpanda.service --no-pager -n 260 || true",
        "            echo '===== startup wrapper ====='",
        "            sed -n '1,220p' /usr/local/sbin/opensible-redpanda-start || true",
        "            echo '===== rendered /etc/redpanda/redpanda.yaml ====='",
        "            cat /etc/redpanda/redpanda.yaml || true",
        "            echo '===== listening TCP ports ====='",
        "            ss -lntp || netstat -lntp || true",
        "            echo '===== memory/start mode ====='",
        "            echo \"available_memory_bytes={{ rp_available_memory_bytes | default('unknown') }} low_memory={{ rp_low_memory_host | default(false) }} dev_mode={{ rp_runtime_developer_mode | default(false) }} start_mode={{ rp_runtime_start_mode | default('') }} effective_smp={{ rp_effective_smp | default('') }} effective_memory={{ rp_effective_memory | default('') }}\"",
        "          args:",
        "            executable: /bin/bash",
        "          register: rp_bootstrap_diag",
        "          changed_when: false",
        "          failed_when: false",
        "        - name: Show bootstrap redpanda diagnostics",
        "          ansible.builtin.debug:",
        "            var: rp_bootstrap_diag.stdout_lines",
        "        - name: Fail because bootstrap redpanda did not become active",
        "          ansible.builtin.fail:",
        "            msg: 'Bootstrap redpanda.service did not become active. See diagnostics above; peer brokers were not started to avoid hiding the real bootstrap error.'",
        "    - name: Wait for bootstrap broker local RPC before peers wait",
        "      when: (rp_is_single_node | bool) or (redpanda_is_first | default(false) | bool)",
        "      block:",
        "        - name: wait_for bootstrap local RPC port",
        "          ansible.builtin.wait_for:",
        "            host: 127.0.0.1",
        "            port: \"{{ redpanda_rpc_port }}\"",
        "            timeout: 240",
        "      rescue:",
        "        - name: Dump bootstrap RPC diagnostics",
        "          ansible.builtin.shell: |",
        "            set +e",
        "            echo '===== systemctl status redpanda ====='",
        "            systemctl status redpanda.service --no-pager -l || true",
        "            echo '===== journalctl -u redpanda (last 260 lines) ====='",
        "            journalctl -u redpanda.service --no-pager -n 260 || true",
        "            echo '===== listening TCP ports ====='",
        "            ss -lntp || netstat -lntp || true",
        "            echo '===== rpk cluster info ====='",
        "            rpk cluster info --brokers 127.0.0.1:{{ redpanda_kafka_port }} || true",
        "            echo '===== rendered /etc/redpanda/redpanda.yaml ====='",
        "            cat /etc/redpanda/redpanda.yaml || true",
        "          args:",
        "            executable: /bin/bash",
        "          register: rp_bootstrap_rpc_diag",
        "          changed_when: false",
        "          failed_when: false",
        "        - name: Show bootstrap RPC diagnostics",
        "          ansible.builtin.debug:",
        "            var: rp_bootstrap_rpc_diag.stdout_lines",
        "        - name: Fail because bootstrap RPC did not open",
        "          ansible.builtin.fail:",
        "            msg: 'Bootstrap redpanda.service is active but RPC port {{ redpanda_rpc_port }} did not open locally. See diagnostics above.'",
        "    - name: Wait for bootstrap broker RPC before starting peer brokers",
        "      ansible.builtin.wait_for:",
        "        host: \"{{ redpanda_first_seed_host }}\"",
        "        port: \"{{ redpanda_rpc_port }}\"",
        "        timeout: 240",
        "      when:",
        "        - not rp_is_single_node | bool",
        "        - not redpanda_is_first | default(false) | bool",
        "    - name: Start Redpanda peer brokers after bootstrap broker is reachable",
        "      ansible.builtin.systemd:",
        "        name: redpanda",
        "        enabled: true",
        "        state: restarted",
        "        daemon_reload: true",
        "      register: rp_start",
        "      failed_when: false",
        "      when:",
        "        - not rp_is_single_node | bool",
        "        - not redpanda_is_first | default(false) | bool",
        "    - name: Wait for redpanda service to report active",
        "      ansible.builtin.command: systemctl is-active redpanda.service",
        "      register: rp_service_active",
        "      changed_when: false",
        "      failed_when: false",
        "      retries: 30",
        "      delay: 2",
        "      until: rp_service_active.stdout | default('') == 'active'",
        "    - name: Diagnose redpanda if it did not become active",
        "      when: (rp_service_active.stdout | default('')) != 'active'",
        "      block:",
        "        - name: Dump redpanda systemd status",
        "          ansible.builtin.command: systemctl status redpanda.service --no-pager -l",
        "          register: rp_status",
        "          changed_when: false",
        "          failed_when: false",
        "        - name: Dump redpanda journal (last 200 lines)",
        "          ansible.builtin.command: journalctl -xeu redpanda.service --no-pager -n 200",
        "          register: rp_journal",
        "          changed_when: false",
        "          failed_when: false",
        "        - name: Dump rendered redpanda.yaml",
        "          ansible.builtin.command: cat /etc/redpanda/redpanda.yaml",
        "          register: rp_yaml",
        "          changed_when: false",
        "          failed_when: false",
        "        - name: Show redpanda failure details",
        "          ansible.builtin.debug:",
        "            msg:",
        "              - '=== systemctl status ==='",
        "              - \"{{ rp_status.stdout_lines | default([]) }}\"",
        "              - '=== journalctl -xeu redpanda ==='",
        "              - \"{{ rp_journal.stdout_lines | default([]) }}\"",
        "              - '=== /etc/redpanda/redpanda.yaml ==='",
        "              - \"{{ rp_yaml.stdout_lines | default([]) }}\"",
        "              - '=== memory/start mode ==='",
        "              - \"available_memory_bytes={{ rp_available_memory_bytes | default('unknown') }}, low_memory={{ rp_low_memory_host | default(false) }}, mode={{ rp_runtime_start_mode | default('') }}, node_seed_servers={{ rp_node_seed_servers | default([]) }}\"",
        "        - name: Fail with actionable message",
        "          ansible.builtin.fail:",
        "            msg: 'redpanda.service failed to start — see journal + rendered config above.'",

        "    - name: Wait for Kafka API to accept connections (with diagnostics on failure)",
        "      block:",
        "        - name: wait_for redpanda Kafka API port",
        "          ansible.builtin.wait_for:",
        "            host: 127.0.0.1",
        f"            port: {kafka_port}",
        "            timeout: 240",
        "      rescue:",
        "        - name: Dump redpanda startup diagnostics",
        "          ansible.builtin.shell: |",
        "            set +e",
        "            echo '===== systemctl status redpanda ====='",
        "            systemctl status redpanda.service --no-pager -l | tail -n 80",
        "            echo '===== journalctl -u redpanda (last 240 lines) ====='",
        "            journalctl -u redpanda.service --no-pager -n 240 || true",
        "            echo '===== listening TCP ports ====='",
        "            ss -lntp || netstat -lntp || true",
        "            echo '===== /etc/redpanda/redpanda.yaml ====='",
        "            cat /etc/redpanda/redpanda.yaml || true",
        "            echo '===== data dir identity hints ====='",
        "            find '{{ redpanda_data_dir }}' -maxdepth 3 -type f \\( -name '*meta*' -o -name '*node*' -o -name '*uuid*' \\) -print 2>/dev/null | head -50 || true",
        "            echo '===== rpk cluster info ====='",
        "            rpk cluster info --brokers 127.0.0.1:{{ redpanda_kafka_port }} || true",
        "          args:",
        "            executable: /bin/bash",
        "          register: rp_wait_diag",
        "          changed_when: false",
        "          failed_when: false",
        "        - name: Show redpanda startup diagnostics",
        "          ansible.builtin.debug:",
        "            var: rp_wait_diag.stdout_lines",
        "        - name: Fail with clear message",
        "          ansible.builtin.fail:",
        "            msg: 'Redpanda service started but Kafka API did not open port {{ redpanda_kafka_port }} within 240s. See diagnostics above.'",
        "    - name: Wait for Admin API to accept connections",
        "      ansible.builtin.wait_for:",
        "        host: 127.0.0.1",
        f"        port: {admin_port}",
        "        timeout: 120",
        "    - name: Apply Redpanda cluster defaults after startup (best-effort)",
        "      run_once: true",
        "      ansible.builtin.shell: |",
        "        set +e",
        "        rpk cluster config set auto_create_topics_enabled true -X admin.hosts=127.0.0.1:{{ redpanda_admin_port }}",
        "        rpk cluster config set group_initial_rebalance_delay 0 -X admin.hosts=127.0.0.1:{{ redpanda_admin_port }}",
        "        rpk cluster config set group_topic_partitions 3 -X admin.hosts=127.0.0.1:{{ redpanda_admin_port }}",
        "        rpk cluster config set storage_min_free_bytes 10485760 -X admin.hosts=127.0.0.1:{{ redpanda_admin_port }}",
        "        rpk cluster config set topic_partitions_per_shard 1000 -X admin.hosts=127.0.0.1:{{ redpanda_admin_port }}",
        "        rpk cluster config set write_caching_default true -X admin.hosts=127.0.0.1:{{ redpanda_admin_port }}",
        "      args:",
        "        executable: /bin/bash",
        "      register: rp_cluster_defaults",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Smoke test - cluster info via rpk",
        "      ansible.builtin.command: rpk cluster info --brokers 127.0.0.1:{{ redpanda_kafka_port }}",
        "      changed_when: false",
        "      register: rpk_info",
        "      retries: 6",
        "      delay: 5",
        "      until: rpk_info.rc == 0",
        # ---------- HA verification: cluster health (multi-node only) ----------
        "    - name: Verify cluster health (HA)",
        "      run_once: true",
        "      when: (redpanda_seed_servers | length) > 1",
        "      ansible.builtin.command: rpk cluster health --exit-when-healthy",
        "      changed_when: false",
        "      register: rpk_health",
        "      retries: 12",
        "      delay: 5",
        "      until: rpk_health.rc == 0",
        # ---------- Cluster-info bundle (OpenBao-style) ----------
        "    - name: Ensure /opt/redpanda/cluster-info exists",
        "      ansible.builtin.file:",
        "        path: /opt/redpanda/cluster-info",
        "        state: directory",
        "        owner: redpanda",
        "        group: redpanda",
        "        mode: '0755'",
        "    - name: Write Redpanda cluster-info bundle on every broker",
        "      ansible.builtin.copy:",
        "        dest: /opt/redpanda/cluster-info/cluster-info.txt",
        "        owner: redpanda",
        "        group: redpanda",
        "        mode: '0644'",
        "        content: |",
        "          # Managed by OpenSible - Redpanda cluster info",
        "          cluster_name       : {{ redpanda_cluster_name }}",
        "          brokers            : {{ ansible_play_hosts | map('extract', hostvars) | map(attribute='ansible_host', default=inventory_hostname) | list | join(', ') }}",
        "          bootstrap_servers  : {% if redpanda_bootstrap_servers | length > 0 %}{{ redpanda_bootstrap_servers }}{% else %}{{ redpanda_advertised_host }}:{{ redpanda_kafka_port }}{% endif %}",
        f"          kafka_api          : :{kafka_port}",
        f"          admin_api          : :{admin_port}",
        f"          schema_registry    : :{sr_port}",
        f"          pandaproxy         : :{pp_port}",
        f"          rpc_port           : :{rpc_port}",
        "          data_dir           : {{ redpanda_data_dir }}",
        "          service            : systemctl status redpanda",
        "          logs               : journalctl -u redpanda -f",
        "          cluster_info_cmd   : rpk cluster info --brokers 127.0.0.1:{{ redpanda_kafka_port }}",
        "          cluster_health_cmd : rpk cluster health",
        "          topic_list_cmd     : rpk topic list --brokers 127.0.0.1:{{ redpanda_kafka_port }}",
        "    - name: Cluster summary",
        "      run_once: true",
        "      ansible.builtin.debug:",
        "        msg:",
        "          - \"Redpanda cluster is up ({{ redpanda_cluster_name }})\"",
        f"          - \"Kafka API (:{kafka_port}), Admin (:{admin_port}), Schema Registry (:{sr_port}), Pandaproxy (:{pp_port})\"",
        "          - \"Brokers: {{ ansible_play_hosts | map('extract', hostvars) | map(attribute='ansible_host', default=inventory_hostname) | list }}\"",
        "          - \"Cluster-info bundle: /opt/redpanda/cluster-info/cluster-info.txt (every node)\"",
        "          - \"Cluster info:\"",
        "          - \"{{ rpk_info.stdout_lines | default([]) }}\"",
        "          - \"Cluster health:\"",
        "          - \"{{ rpk_health.stdout_lines | default(['(single-node: health check skipped)']) }}\"",
        "  handlers:",
        "    - name: Restart redpanda",
        "      ansible.builtin.systemd:",
        "        name: redpanda",
        "        state: restarted",
        "        daemon_reload: true",
        "",
    ]

    # ------------------------------------------------------------------ #
    # PLAY 2 — Redpanda Console (Web UI) on first broker
    # ------------------------------------------------------------------ #
    if console_enabled:
        # Use the first broker's IP if we have one; otherwise fall back to
        # the first host in the play group.
        if first_ip:
            console_host = first_ip
        else:
            console_host = play_hosts
        parts += [
            "- name: Deploy Redpanda Console (Web UI) on first broker",
            f"  hosts: {console_host}",
            f"  become: {become}",
            "  gather_facts: true",
            *vars_files_lines(parse_vault_files(values.get("vault_files"))),
            "  vars:",
            f"    redpanda_channel: {yaml_str(channel)}",
            f"    console_port: {console_port}",
            "  tasks:",
            "    - name: Install redpanda-console (Debian/Ubuntu)",
            "      ansible.builtin.shell: |",
            "        set -e",
            "        export DEBIAN_FRONTEND=noninteractive",
            "        apt-get update -y",
            "        apt-get install -y --no-install-recommends redpanda-console",
            "      args:",
            "        executable: /bin/bash",
            "        creates: /usr/bin/redpanda-console",
            "      when: ansible_os_family == 'Debian'",

            "    - name: Install redpanda-console (RHEL family)",
            "      ansible.builtin.yum:",
            "        name: redpanda-console",
            "        state: present",
            "      when: ansible_os_family == 'RedHat'",
            "    - name: Ensure console config dir",
            "      ansible.builtin.file:",
            "        path: /etc/redpanda",
            "        state: directory",
            "        mode: '0755'",
            "    - name: Write /etc/redpanda/redpanda-console-config.yaml",
            "      ansible.builtin.copy:",
            "        dest: /etc/redpanda/redpanda-console-config.yaml",
            "        mode: '0644'",
            "        content: |",
            "          # Managed by OpenSible - do not edit by hand",
            "          kafka:",
            "            brokers:",
            *[f"              - {ip}:{kafka_port}" for ip in seed_ips],
            *(
                [
                    "            schemaRegistry:",
                    "              enabled: true",
                    "              urls:",
                    f"                - http://{first_ip}:{sr_port}",
                ] if first_ip else []
            ),
            *(
                [
                    "          redpanda:",
                    "            adminApi:",
                    "              enabled: true",
                    "              urls:",
                    f"                - http://{first_ip}:{admin_port}",
                ] if first_ip else []
            ),
            "          server:",
            f"            listenPort: {console_port}",
            "      notify: Restart redpanda-console",
            "    - name: Install redpanda-console systemd unit",
            "      ansible.builtin.copy:",
            "        dest: /etc/systemd/system/redpanda-console.service",
            "        mode: '0644'",
            "        content: |",
            "          [Unit]",
            "          Description=Redpanda Console (Web UI)",
            "          Documentation=https://docs.redpanda.com/current/console/",
            "          After=network-online.target",
            "          Wants=network-online.target",
            "          [Service]",
            "          Type=simple",
            "          ExecStart=/usr/bin/redpanda-console -config.filepath=/etc/redpanda/redpanda-console-config.yaml",
            "          Restart=on-failure",
            "          RestartSec=5",
            "          User=redpanda",
            "          Group=redpanda",
            "          [Install]",
            "          WantedBy=multi-user.target",
            "      notify: Restart redpanda-console",
        ]
        if open_firewall:
            parts += [
                "    - name: Open Console port (ufw)",
                "      ansible.builtin.shell: |",
                "        set -e",
                "        command -v ufw >/dev/null 2>&1 || exit 0",
                "        ufw status | grep -q 'Status: active' || exit 0",
                f"        ufw allow {console_port}/tcp || true",
                "      changed_when: false",
                "      failed_when: false",
                "    - name: Open Console port (firewalld)",
                "      ansible.builtin.shell: |",
                "        set -e",
                "        command -v firewall-cmd >/dev/null 2>&1 || exit 0",
                "        firewall-cmd --state >/dev/null 2>&1 || exit 0",
                f"        firewall-cmd --permanent --add-port={console_port}/tcp || true",
                "        firewall-cmd --reload || true",
                "      changed_when: false",
                "      failed_when: false",
            ]
        parts += [
            "    - name: Enable redpanda-console",
            "      ansible.builtin.systemd:",
            "        name: redpanda-console",
            "        enabled: true",
            "        daemon_reload: true",
            "    - name: Force restart redpanda-console (apply latest config)",
            "      ansible.builtin.systemd:",
            "        name: redpanda-console",
            "        state: restarted",
            "    - name: Wait for Console HTTP",
            "      ansible.builtin.wait_for:",
            "        host: 127.0.0.1",
            f"        port: {console_port}",
            "        timeout: 60",
            "      register: _console_wait",
            "      failed_when: false",
            "    - name: Dump redpanda-console journal on failure",
            "      when: _console_wait is failed or (_console_wait.state | default('') ) != 'started'",
            "      ansible.builtin.shell: |",
            "        set +e",
            "        systemctl status redpanda-console --no-pager -l | tail -n 40",
            "        echo '--- journal ---'",
            "        journalctl -u redpanda-console --no-pager -n 120 || true",
            "        echo '--- config ---'",
            "        cat /etc/redpanda/redpanda-console-config.yaml || true",
            "        echo '--- listening ---'",
            f"        ss -ltnp | grep ':{console_port}' || true",
            "      register: _console_diag",
            "      changed_when: false",
            "      failed_when: false",
            "    - name: Show redpanda-console diagnostics",
            "      when: _console_diag is defined and _console_diag.stdout is defined",
            "      ansible.builtin.debug:",
            "        var: _console_diag.stdout_lines",
            "    - name: Fail if Console did not come up",
            "      when: _console_wait is failed",
            "      ansible.builtin.fail:",
            f"        msg: \"Redpanda Console failed to listen on :{console_port}. See journal output above.\"",
            "    - name: Console summary",
            "      ansible.builtin.debug:",
            "        msg:",
            f"          - \"Redpanda Console is up: http://{{{{ ansible_host | default(inventory_hostname) }}}}:{console_port}\"",
            "  handlers:",
            "    - name: Restart redpanda-console",
            "      ansible.builtin.systemd:",
            "        name: redpanda-console",
            "        state: restarted",
            "        daemon_reload: true",
            "",

        ]

    return "\n".join(parts)
