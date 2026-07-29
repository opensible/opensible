"""Template: RabbitMQ cluster (distro packages + systemd).

Deploys a RabbitMQ cluster with a shared Erlang cookie, management +
prometheus plugins, quorum-queue defaults, and an admin user. Uses
distro packages (rabbitmq-server) on Debian/Ubuntu and RHEL family —
no Docker, no Bitnami.

Topology (driven entirely by the ``nodes`` list):

  * The **first** node is the seed / bootstrap broker.
  * All other nodes join the seed via ``rabbitmqctl join_cluster``.
  * The Erlang cookie (a shared secret) is written identically on
    every node so nodes can talk to each other.

For HA you want at least **3 nodes** so quorum queues can tolerate a
single-node failure. Single-node runs are supported for dev.

Marker: 2026-07-rabbitmq-cluster-v1
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
    "id": "rabbitmq-cluster",
    "name": "RabbitMQ Cluster",
    "category": "Messaging",
    "icon": "rabbit",
    "description": (
        "RabbitMQ cluster with management UI, Prometheus metrics and quorum "
        "queue defaults. Installs from distro packages and manages "
        "rabbitmq-server via systemd. Add each node under 'Cluster nodes' — "
        "the first entry is the bootstrap/seed node, the rest join it. Use "
        "3+ nodes so quorum queues stay available on a single-node failure."
    ),
    "tags": ["rabbitmq", "amqp", "cluster", "systemd", "quorum-queues"],
    "variables": [
        # ---------- Cluster identity ----------
        {"name": "cluster_id", "label": "Cluster name",
         "type": "string", "default": "opensible-rabbit",
         "help": "Free-form label used for filenames and the Ansible cluster group."},
        {"name": "erlang_cookie", "label": "Erlang cookie (shared secret)",
         "type": "password", "required": False, "default": "",
         "help": "Shared secret Erlang nodes use to authenticate each other. Leave blank to auto-derive a stable cookie from the cluster name."},

        # ---------- Hosts / HA ----------
        {"name": "ssh_user_default", "label": "Default SSH user",
         "type": "string", "default": "root"},
        {"name": "ssh_port_default", "label": "Default SSH port",
         "type": "number", "default": 22},
        {"name": "nodes", "label": "Cluster nodes (first = seed)",
         "type": "nodes", "required": False,
         "help": "First entry is the bootstrap/seed node. Add 2 more (3 total) for HA quorum queues. Leave blank to use the generic host picker.",
         "default": [
             {"name": "rabbit-1", "ip": "", "ssh_user": "", "ssh_port": ""},
             {"name": "rabbit-2", "ip": "", "ssh_user": "", "ssh_port": ""},
             {"name": "rabbit-3", "ip": "", "ssh_user": "", "ssh_port": ""},
         ]},

        # ---------- Networking ----------
        {"name": "amqp_port", "label": "AMQP port",
         "type": "number", "default": 5672},
        {"name": "mgmt_port", "label": "Management UI port",
         "type": "number", "default": 15672},
        {"name": "prometheus_port", "label": "Prometheus metrics port",
         "type": "number", "default": 15692},
        {"name": "dist_port", "label": "Inter-node (Erlang dist) port",
         "type": "number", "default": 25672},
        {"name": "epmd_port", "label": "epmd port",
         "type": "number", "default": 4369},

        # ---------- Admin user ----------
        {"name": "admin_user", "label": "Admin username",
         "type": "string", "default": "admin"},
        {"name": "admin_password", "label": "Admin password",
         "type": "password", "required": False, "default": "",
         "help": "Leave blank to auto-generate a strong password (printed at the end of the run)."},
        {"name": "delete_guest_user", "label": "Delete the default 'guest' user",
         "type": "boolean", "default": True,
         "help": "Recommended. The built-in guest user only works from localhost anyway."},

        # ---------- Tuning ----------
        {"name": "default_queue_type", "label": "Default queue type",
         "type": "select", "default": "quorum",
         "options": [
             {"label": "Quorum (recommended for HA)", "value": "quorum"},
             {"label": "Classic (single-node)", "value": "classic"},
             {"label": "Stream", "value": "stream"},
         ]},
        {"name": "vm_memory_high_watermark", "label": "vm_memory_high_watermark",
         "type": "string", "default": "0.6",
         "help": "Fraction of RAM (e.g. 0.6) or absolute value (e.g. 2GB)."},
        {"name": "disk_free_limit", "label": "disk_free_limit",
         "type": "string", "default": "2GB"},

        # ---------- Ops ----------
        {"name": "open_firewall", "label": "Open ports in UFW/firewalld",
         "type": "boolean", "default": True},
        {"name": "become", "label": "Run as sudo (become)",
         "type": "boolean", "default": True},
        VAULT_FILES_VARIABLE,
    ],
}


def suggested_filename(values: Dict[str, Any]) -> str:
    stem = slugify(values.get("cluster_id"), "rabbit")
    return f"{stem}-rabbitmq-cluster.yml"


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
        name = str(n.get("name") or f"rabbit-{i+1}").strip() or f"rabbit-{i+1}"
        user = str(n.get("ssh_user") or default_user or "root").strip() or "root"
        try:
            port = int(n.get("ssh_port") or default_port or 22)
        except Exception:
            port = 22
        # RabbitMQ node names must be a valid Erlang atom; use the sanitized
        # slug as the short-name portion of rabbit@<slug>.
        node_slug = slugify(name, f"rabbit-{i+1}") or f"rabbit-{i+1}"
        out.append({
            "name": name,
            "node_slug": node_slug,
            "ip": ip,
            "ssh_user": user,
            "ssh_port": port,
            "index": i + 1,
        })
    return out


def _derive_cookie(cluster_id: str) -> str:
    """Deterministic 32-char cookie from the cluster id (upper hex)."""
    digest = hashlib.sha256(f"opensible-rabbitmq::{cluster_id}".encode("utf-8")).hexdigest()
    return digest[:32].upper()


def render(values: Dict[str, Any], targets: Dict[str, Any]) -> str:
    become = "true" if values.get("become", True) else "false"

    cluster_id = values.get("cluster_id") or "opensible-rabbit"
    cookie = str(values.get("erlang_cookie") or "").strip() or _derive_cookie(cluster_id)

    amqp_port = int(values.get("amqp_port") or 5672)
    mgmt_port = int(values.get("mgmt_port") or 15672)
    prometheus_port = int(values.get("prometheus_port") or 15692)
    dist_port = int(values.get("dist_port") or 25672)
    epmd_port = int(values.get("epmd_port") or 4369)

    admin_user = (values.get("admin_user") or "admin").strip() or "admin"
    admin_password = str(values.get("admin_password") or "").strip()
    delete_guest = bool(values.get("delete_guest_user", True))

    default_queue_type = (values.get("default_queue_type") or "quorum").strip().lower()
    if default_queue_type not in ("quorum", "classic", "stream"):
        default_queue_type = "quorum"

    vm_watermark = str(values.get("vm_memory_high_watermark") or "0.6").strip()
    disk_free_limit = str(values.get("disk_free_limit") or "2GB").strip()

    open_firewall = bool(values.get("open_firewall", True))

    nodes = _norm_nodes(
        values.get("nodes"),
        values.get("ssh_user_default") or "root",
        values.get("ssh_port_default") or 22,
    )

    cluster_group = slugify(cluster_id, "rabbit") + "_nodes"

    parts: List[str] = ["---"]
    parts.append(f"# OpenSible rabbitmq-cluster template generation: 2026-07-rabbitmq-cluster-v1")
    parts.append(f"# Rendered from template: {TEMPLATE['name']}")
    parts.append(f"# Cluster: {cluster_id} | nodes: {len(nodes) if nodes else 'from targets'}")
    parts.append("")

    # ------------------------------------------------------------------ #
    # PLAY 0 — dynamic inventory (if nodes provided)
    # ------------------------------------------------------------------ #
    if nodes:
        parts += [
            "- name: Register RabbitMQ nodes into a dynamic inventory group",
            "  hosts: localhost",
            "  gather_facts: false",
            "  connection: local",
            "  tasks:",
            "    - name: add_host each node",
            "      ansible.builtin.add_host:",
            "        name: \"{{ item.ip }}\"",
            f"        groups: {cluster_group}",
            "        ansible_host: \"{{ item.ip }}\"",
            "        ansible_user: \"{{ item.ssh_user }}\"",
            "        ansible_port: \"{{ item.ssh_port }}\"",
            "        rmq_node_slug: \"{{ item.node_slug }}\"",
            "        rmq_node_index: \"{{ item.index }}\"",
            "      loop:",
        ]
        for n in nodes:
            parts.append(
                "        - { "
                f"name: {yaml_str(n['name'])}, "
                f"node_slug: {yaml_str(n['node_slug'])}, "
                f"ip: {yaml_str(n['ip'])}, "
                f"ssh_user: {yaml_str(n['ssh_user'])}, "
                f"ssh_port: {n['ssh_port']}, "
                f"index: {n['index']}"
                " }"
            )
        parts.append("")
        play_hosts = cluster_group
        seed_ip = nodes[0]["ip"]
        seed_slug = nodes[0]["node_slug"]
    else:
        play_hosts = render_hosts(targets)
        seed_ip = "{{ ansible_play_hosts[0] }}"
        seed_slug = "rabbit-1"

    # ------------------------------------------------------------------ #
    # PLAY 1 — install + configure on every node (serial to avoid race)
    # ------------------------------------------------------------------ #
    parts += [
        "- name: Deploy RabbitMQ on every node",
        f"  hosts: {play_hosts}",
        f"  become: {become}",
        "  gather_facts: true",
        "  any_errors_fatal: true",
        *vars_files_lines(parse_vault_files(values.get("vault_files"))),
        "  vars:",
        f"    rmq_cluster_id: {yaml_str(cluster_id)}",
        f"    rmq_erlang_cookie: {yaml_str(cookie)}",
        f"    rmq_amqp_port: {amqp_port}",
        f"    rmq_mgmt_port: {mgmt_port}",
        f"    rmq_prometheus_port: {prometheus_port}",
        f"    rmq_dist_port: {dist_port}",
        f"    rmq_epmd_port: {epmd_port}",
        f"    rmq_admin_user: {yaml_str(admin_user)}",
        f"    rmq_admin_password_input: {yaml_str(admin_password)}",
        f"    rmq_delete_guest: {'true' if delete_guest else 'false'}",
        f"    rmq_default_queue_type: {yaml_str(default_queue_type)}",
        f"    rmq_vm_watermark: {yaml_str(vm_watermark)}",
        f"    rmq_disk_free_limit: {yaml_str(disk_free_limit)}",
        f"    rmq_seed_ip: {yaml_str(seed_ip)}",
        f"    rmq_seed_slug: {yaml_str(seed_slug)}",
        f"    rmq_open_firewall: {'true' if open_firewall else 'false'}",
        "  tasks:",

        # ---------- Preflight ----------
        "    - name: Resolve node index (fallback to play index)",
        "      ansible.builtin.set_fact:",
        "        rmq_node_index: \"{{ rmq_node_index | default(ansible_play_hosts.index(inventory_hostname) + 1) | int }}\"",
        "    - name: Derive node slug when not provided by dynamic inventory",
        "      when: rmq_node_slug is not defined or (rmq_node_slug | length) == 0",
        "      ansible.builtin.set_fact:",
        "        rmq_node_slug: \"rabbit-{{ rmq_node_index }}\"",
        "    - name: Decide whether this host is the seed / bootstrap node",
        "      ansible.builtin.set_fact:",
        "        rmq_is_seed: \"{{ (ansible_host | default(inventory_hostname)) == rmq_seed_ip }}\"",
        "    - name: Generate an admin password when none was provided",
        "      run_once: true",
        "      delegate_to: localhost",
        "      become: false",
        "      ansible.builtin.set_fact:",
        "        rmq_admin_password_generated: \"{{ lookup('password', '/dev/null length=24 chars=ascii_letters,digits') }}\"",
        "      when: (rmq_admin_password_input | length) == 0",
        "    - name: Share admin password across the play",
        "      ansible.builtin.set_fact:",
        "        rmq_admin_password: \"{{ rmq_admin_password_input if (rmq_admin_password_input | length) > 0 else hostvars[groups['all'][0]].rmq_admin_password_generated | default(rmq_admin_password_generated) }}\"",

        # ---------- Refresh package metadata ----------
        "    - name: Refresh apt package metadata (Debian/Ubuntu)",
        "      when: ansible_os_family == 'Debian'",
        "      ansible.builtin.apt:",
        "        update_cache: true",
        "        cache_valid_time: 300",
        "      failed_when: false",

        # ---------- /etc/hosts so nodes can resolve each other by short name ----------
        "    - name: Ensure every cluster peer is resolvable via /etc/hosts",
        "      ansible.builtin.blockinfile:",
        "        path: /etc/hosts",
        "        marker: \"# {mark} OPENSIBLE RABBITMQ CLUSTER\"",
        "        block: |",
        "          {% for h in ansible_play_hosts %}",
        "          {{ hostvars[h].ansible_host | default(h) }} {{ hostvars[h].rmq_node_slug | default('rabbit-' ~ (loop.index)) }}",
        "          {% endfor %}",
        "      failed_when: false",

        # ---------- Install packages ----------
        "    - name: Install rabbitmq-server (Debian/Ubuntu)",
        "      when: ansible_os_family == 'Debian'",
        "      ansible.builtin.apt:",
        "        name: rabbitmq-server",
        "        state: present",
        "        install_recommends: false",
        "        update_cache: true",
        "        cache_valid_time: 300",
        "      register: _rmq_pkg_deb",
        "      until: _rmq_pkg_deb is succeeded",
        "      retries: 3",
        "      delay: 10",
        "    - name: Install rabbitmq-server (RHEL/Rocky/Alma)",
        "      when: ansible_os_family == 'RedHat'",
        "      ansible.builtin.dnf:",
        "        name: rabbitmq-server",
        "        state: present",
        "      register: _rmq_pkg_rh",
        "      until: _rmq_pkg_rh is succeeded",
        "      retries: 3",
        "      delay: 10",

        # ---------- Stop before configuration (must be stopped to change node name / cookie) ----------
        "    - name: Stop rabbitmq-server before configuration",
        "      ansible.builtin.systemd:",
        "        name: rabbitmq-server",
        "        state: stopped",
        "      failed_when: false",
        "    - name: Also stop epmd so a new NODENAME can register cleanly",
        "      ansible.builtin.shell: |",
        "        set +e",
        "        (systemctl stop epmd.socket 2>/dev/null; systemctl stop epmd.service 2>/dev/null)",
        "        pkill -9 -x epmd 2>/dev/null",
        "        exit 0",
        "      changed_when: false",

        # ---------- Ensure /var/lib/rabbitmq exists, then write cookie ----------
        "    - name: Ensure /var/lib/rabbitmq exists and is owned by rabbitmq",
        "      ansible.builtin.file:",
        "        path: /var/lib/rabbitmq",
        "        state: directory",
        "        owner: rabbitmq",
        "        group: rabbitmq",
        "        mode: '0750'",
        "    - name: Write Erlang cookie for rabbitmq user",
        "      ansible.builtin.copy:",
        "        dest: /var/lib/rabbitmq/.erlang.cookie",
        "        content: \"{{ rmq_erlang_cookie }}\"",
        "        owner: rabbitmq",
        "        group: rabbitmq",
        "        mode: '0400'",
        "    - name: Write Erlang cookie for root (rabbitmqctl invoked as root)",
        "      ansible.builtin.copy:",
        "        dest: /root/.erlang.cookie",
        "        content: \"{{ rmq_erlang_cookie }}\"",
        "        owner: root",
        "        group: root",
        "        mode: '0400'",

        # ---------- Configure NODENAME + ports via /etc/rabbitmq/rabbitmq-env.conf ----------
        "    - name: Ensure /etc/rabbitmq exists",
        "      ansible.builtin.file:",
        "        path: /etc/rabbitmq",
        "        state: directory",
        "        owner: rabbitmq",
        "        group: rabbitmq",
        "        mode: '0750'",
        "    - name: Write rabbitmq-env.conf (NODENAME per host, dist port)",
        "      ansible.builtin.copy:",
        "        dest: /etc/rabbitmq/rabbitmq-env.conf",
        "        owner: rabbitmq",
        "        group: rabbitmq",
        "        mode: '0644'",
        "        content: |",
        "          NODENAME=rabbit@{{ rmq_node_slug }}",
        "          NODE_IP_ADDRESS=0.0.0.0",
        "          NODE_PORT={{ rmq_amqp_port }}",
        "          DIST_PORT={{ rmq_dist_port }}",
        "          USE_LONGNAME=false",

        # ---------- Main rabbitmq.conf ----------
        "    - name: Write rabbitmq.conf",
        "      ansible.builtin.copy:",
        "        dest: /etc/rabbitmq/rabbitmq.conf",
        "        owner: rabbitmq",
        "        group: rabbitmq",
        "        mode: '0644'",
        "        content: |",
        "          # Managed by OpenSible - do not edit by hand",
        "          listeners.tcp.default = {{ rmq_amqp_port }}",
        "          management.tcp.port = {{ rmq_mgmt_port }}",
        "          prometheus.tcp.port = {{ rmq_prometheus_port }}",
        "          vm_memory_high_watermark.relative = {{ rmq_vm_watermark }}",
        "          disk_free_limit.absolute = {{ rmq_disk_free_limit }}",
        "          default_queue_type = {{ rmq_default_queue_type }}",
        "          cluster_partition_handling = pause_minority",
        "          cluster_formation.peer_discovery_backend = classic_config",
        "          {% for h in ansible_play_hosts %}",
        "          cluster_formation.classic_config.nodes.{{ loop.index }} = rabbit@{{ hostvars[h].rmq_node_slug | default('rabbit-' ~ loop.index) }}",
        "          {% endfor %}",

        # ---------- Enabled plugins ----------
        "    - name: Enable management + prometheus plugins on disk",
        "      ansible.builtin.copy:",
        "        dest: /etc/rabbitmq/enabled_plugins",
        "        owner: rabbitmq",
        "        group: rabbitmq",
        "        mode: '0644'",
        "        content: |",
        "          [rabbitmq_management,rabbitmq_prometheus,rabbitmq_peer_discovery_common].",

        # ---------- Firewall ----------
        "    - name: Open RabbitMQ ports in UFW (Debian/Ubuntu)",
        "      when: rmq_open_firewall and ansible_os_family == 'Debian'",
        "      ansible.builtin.shell: |",
        "        set +e",
        "        command -v ufw >/dev/null 2>&1 || exit 0",
        "        for p in {{ rmq_amqp_port }} {{ rmq_mgmt_port }} {{ rmq_prometheus_port }} {{ rmq_dist_port }} {{ rmq_epmd_port }}; do",
        "          ufw allow ${p}/tcp || true",
        "        done",
        "        exit 0",
        "      changed_when: false",
        "    - name: Open RabbitMQ ports in firewalld (RHEL family)",
        "      when: rmq_open_firewall and ansible_os_family == 'RedHat'",
        "      ansible.builtin.shell: |",
        "        set +e",
        "        command -v firewall-cmd >/dev/null 2>&1 || exit 0",
        "        for p in {{ rmq_amqp_port }} {{ rmq_mgmt_port }} {{ rmq_prometheus_port }} {{ rmq_dist_port }} {{ rmq_epmd_port }}; do",
        "          firewall-cmd --permanent --add-port=${p}/tcp || true",
        "        done",
        "        firewall-cmd --reload || true",
        "        exit 0",
        "      changed_when: false",

        # ---------- Start rabbitmq-server ----------
        "    - name: Enable and start rabbitmq-server",
        "      ansible.builtin.systemd:",
        "        name: rabbitmq-server",
        "        enabled: true",
        "        state: started",
        "        daemon_reload: true",
        "    - name: Wait for RabbitMQ epmd port (local)",
        "      ansible.builtin.wait_for:",
        "        host: 127.0.0.1",
        "        port: \"{{ rmq_epmd_port }}\"",
        "        timeout: 60",
        "    - name: Wait for rabbitmqctl to report a running node",
        "      ansible.builtin.shell: |",
        "        rabbitmqctl -q status >/dev/null 2>&1",
        "      register: _rmq_status",
        "      retries: 30",
        "      delay: 4",
        "      until: _rmq_status.rc == 0",
        "      changed_when: false",

        # ---------- Enable feature flags (needed for quorum queues on older releases) ----------
        "    - name: Enable all available feature flags (idempotent)",
        "      ansible.builtin.shell: |",
        "        set +e",
        "        rabbitmqctl enable_feature_flag all",
        "        exit 0",
        "      changed_when: false",
    ]

    # ------------------------------------------------------------------ #
    # PLAY 2 — join non-seed nodes into the cluster
    # ------------------------------------------------------------------ #
    parts += [
        "",
        "- name: Join non-seed nodes to the RabbitMQ cluster",
        f"  hosts: {play_hosts}",
        f"  become: {become}",
        "  gather_facts: false",
        "  any_errors_fatal: true",
        "  serial: 1",
        "  vars:",
        f"    rmq_seed_ip: {yaml_str(seed_ip)}",
        f"    rmq_seed_slug: {yaml_str(seed_slug)}",
        "  tasks:",
        "    - name: Skip on seed node",
        "      ansible.builtin.meta: end_host",
        "      when: (ansible_host | default(inventory_hostname)) == rmq_seed_ip",
        "    - name: Stop application on this node",
        "      ansible.builtin.command: rabbitmqctl stop_app",
        "      changed_when: true",
        "      failed_when: false",
        "    - name: Reset local state",
        "      ansible.builtin.command: rabbitmqctl reset",
        "      changed_when: true",
        "      failed_when: false",
        "    - name: Join cluster via seed node",
        "      ansible.builtin.command: \"rabbitmqctl join_cluster rabbit@{{ rmq_seed_slug }}\"",
        "      register: _rmq_join",
        "      changed_when: \"'already_member' not in (_rmq_join.stdout | default('') + _rmq_join.stderr | default(''))\"",
        "      failed_when:",
        "        - _rmq_join.rc != 0",
        "        - \"'already_member' not in (_rmq_join.stdout | default('') + _rmq_join.stderr | default(''))\"",
        "      retries: 20",
        "      delay: 5",
        "      until: _rmq_join.rc == 0 or ('already_member' in (_rmq_join.stdout | default('') + _rmq_join.stderr | default('')))",
        "    - name: Start application again",
        "      ansible.builtin.command: rabbitmqctl start_app",
        "      changed_when: true",
    ]

    # ------------------------------------------------------------------ #
    # PLAY 3 — provision admin user, delete guest, print summary (run once)
    # ------------------------------------------------------------------ #
    parts += [
        "",
        "- name: Configure RabbitMQ users, permissions and summary",
        f"  hosts: {play_hosts}",
        f"  become: {become}",
        "  gather_facts: false",
        "  run_once: true",
        "  tasks:",
        "    - name: Create or update admin user",
        "      ansible.builtin.shell: |",
        "        set -e",
        "        if rabbitmqctl list_users -q | awk '{print $1}' | grep -qx '{{ rmq_admin_user }}'; then",
        "          rabbitmqctl change_password '{{ rmq_admin_user }}' '{{ rmq_admin_password }}'",
        "        else",
        "          rabbitmqctl add_user '{{ rmq_admin_user }}' '{{ rmq_admin_password }}'",
        "        fi",
        "        rabbitmqctl set_user_tags '{{ rmq_admin_user }}' administrator",
        "        rabbitmqctl set_permissions -p / '{{ rmq_admin_user }}' '.*' '.*' '.*'",
        "      no_log: true",
        "      changed_when: true",
        "    - name: Delete built-in 'guest' user",
        "      when: rmq_delete_guest",
        "      ansible.builtin.shell: |",
        "        set +e",
        "        rabbitmqctl list_users -q | awk '{print $1}' | grep -qx 'guest' && rabbitmqctl delete_user guest",
        "        exit 0",
        "      changed_when: false",
        "    - name: Fetch cluster status",
        "      ansible.builtin.command: rabbitmqctl -q cluster_status",
        "      register: _rmq_cluster_status",
        "      changed_when: false",
        "    - name: Show cluster status",
        "      ansible.builtin.debug:",
        "        var: _rmq_cluster_status.stdout_lines",
        "    - name: Show admin credentials + management URL",
        "      ansible.builtin.debug:",
        "        msg:",
        "          - \"RabbitMQ cluster '{{ rmq_cluster_id }}' is ready.\"",
        "          - \"Admin user: {{ rmq_admin_user }}\"",
        "          - \"Admin password: {{ rmq_admin_password }}\"",
        "          - \"Management UI: http://<any-node>:{{ rmq_mgmt_port }}\"",
        "          - \"Prometheus metrics: http://<any-node>:{{ rmq_prometheus_port }}/metrics\"",
        "",
    ]

    return "\n".join(parts)
