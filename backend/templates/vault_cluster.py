"""Template: HashiCorp Vault HA cluster on Docker containers with Raft storage.

Deploys N Vault nodes (recommended 3 or 5) running the official
`hashicorp/vault` Docker image. Each node runs one container managed by a
dedicated systemd unit (`vault-docker.service`) so the container survives
host reboots and Docker daemon restarts.

Highlights:

  * Docker Engine ensured (installed via convenience script if missing).
  * Per-node systemd unit runs `docker run` against the pinned image tag.
  * Rendered /etc/vault/vault.hcl with integrated Raft storage backend and
    cross-node retry_join, so followers join the cluster automatically.
  * Optional first-run auto-init + persistent auto-unseal via a host-side
    helper script + systemd oneshot + timer (uses `docker exec`).
  * Docker bridge / stale container self-healing shared with the ELK
    stack blueprints.

Marker: 2026-07-vault-cluster-v1
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
from ._vault_health import health_tasks as _vault_health_tasks



TEMPLATE = {
    "id": "vault-cluster",
    "name": "HashiCorp Vault HA (Docker cluster)",
    "category": "Secrets",
    "icon": "shield",
    "description": (
        "HashiCorp Vault HA cluster across N nodes running the official "
        "hashicorp/vault Docker image with integrated Raft storage. Each "
        "node runs one container managed by a dedicated systemd unit "
        "(vault-docker.service); followers auto-join via retry_join. "
        "Optional first-run auto-init and persistent auto-unseal keep the "
        "cluster unsealed across reboots."
    ),
    "tags": ["vault", "hashicorp", "secrets", "docker", "cluster", "ha", "raft"],
    "variables": [
        # ---------- Cluster identity ----------
        {"name": "cluster_id", "label": "Cluster name",
         "type": "string", "default": "opensible-vault",
         "help": "Free-form label. Used in the systemd unit description, container name, and filenames."},
        {"name": "vault_version", "label": "Vault image tag",
         "type": "string", "default": "1.18.1",
         "help": "Docker Hub tag under hashicorp/vault (e.g. 1.18.1, 1.17.6)."},

        # ---------- Hosts / HA ----------
        {"name": "ssh_user_default", "label": "Default SSH user",
         "type": "string", "default": "root"},
        {"name": "ssh_port_default", "label": "Default SSH port",
         "type": "number", "default": 22},
        {"name": "nodes", "label": "Cluster nodes (first = initial leader)",
         "type": "nodes", "required": False,
         "help": "List each Vault node with its IP and SSH credentials. Leave empty to fall back to the selected targets/inventory hosts. Use 3 or 5 nodes for real Raft HA.",
         "default": [
             {"name": "vault-1", "ip": "", "ssh_user": "", "ssh_port": ""},
             {"name": "vault-2", "ip": "", "ssh_user": "", "ssh_port": ""},
             {"name": "vault-3", "ip": "", "ssh_user": "", "ssh_port": ""},
         ]},

        # ---------- Networking ----------
        {"name": "api_port", "label": "Vault API port",
         "type": "number", "default": 8200},
        {"name": "cluster_port", "label": "Vault cluster (raft) port",
         "type": "number", "default": 8201},
        {"name": "api_address", "label": "API address (advertised, optional)",
         "type": "string", "default": "",
         "help": "Full URL, e.g. https://vault.example.com:8200. Leave empty to auto-derive per node."},
        {"name": "cluster_address", "label": "Cluster address (advertised, optional)",
         "type": "string", "default": "",
         "help": "Full URL. Leave empty to auto-derive per node."},

        # ---------- TLS ----------
        {"name": "tls_disable", "label": "Disable TLS on listener (dev only)",
         "type": "boolean", "default": True},
        {"name": "tls_cert_file", "label": "TLS cert file (host path)",
         "type": "string", "default": "/etc/vault/tls/tls.crt",
         "help": "Mounted read-only into the container. Ignored when TLS is disabled."},
        {"name": "tls_key_file", "label": "TLS key file (host path)",
         "type": "string", "default": "/etc/vault/tls/tls.key"},

        # ---------- Storage ----------
        {"name": "data_dir", "label": "Raft data directory (host path)",
         "type": "string", "default": "/var/lib/vault-data",
         "help": "Persistent Raft storage mounted into the container at /vault/file."},

        # ---------- Options ----------
        {"name": "ui", "label": "Enable Web UI",
         "type": "boolean", "default": True},
        {"name": "disable_mlock", "label": "Disable mlock (recommended on Raft/no-swap)",
         "type": "boolean", "default": True,
         "help": "When true, container runs with SKIP_SETCAP=true. When false, container gets --cap-add=IPC_LOCK."},
        {"name": "log_level", "label": "Log level",
         "type": "select", "default": "info",
         "options": [
             {"value": "trace", "label": "trace"},
             {"value": "debug", "label": "debug"},
             {"value": "info", "label": "info"},
             {"value": "warn", "label": "warn"},
             {"value": "error", "label": "error"},
         ]},

        # ---------- Ops ----------
        {"name": "auto_init", "label": "Auto-init & persistent auto-unseal",
         "type": "boolean", "default": True,
         "help": "Runs `vault operator init` on the first host once, unseals every node, and installs vault-autounseal.service + .timer on all nodes so Vault re-unseals automatically on every boot/restart. Unseal keys are stored at /etc/vault/unseal.keys (root:root 0600). Full bundle in /opt/vault/cluster-info/."},
        {"name": "health_http_enabled", "label": "Enable HTTP health dashboard",
         "type": "boolean", "default": True,
         "help": "Install a small HTTP service on each node exposing / (HTML dashboard), /health.json and /live. Shows initialized/sealed/standby/leader state."},
        {"name": "health_http_port", "label": "Health HTTP port",
         "type": "number", "default": 8280,
         "help": "Port for the health dashboard. Visit http://<node-ip>:<port>/ from your browser."},
        {"name": "open_firewall", "label": "Open ports in UFW/firewalld",
         "type": "boolean", "default": True},
        {"name": "become", "label": "Run as sudo (become)",
         "type": "boolean", "default": True},
        VAULT_FILES_VARIABLE,
    ],
}



def suggested_filename(values: Dict[str, Any]) -> str:
    stem = slugify(values.get("cluster_id"), "vault")
    return f"{stem}-vault-cluster.yml"


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
        name = str(n.get("name") or f"vault-{i+1}").strip() or f"vault-{i+1}"
        user = str(n.get("ssh_user") or default_user or "root").strip() or "root"
        try:
            port = int(n.get("ssh_port") or default_port or 22)
        except Exception:
            port = 22
        out.append({
            "name": name,
            "node_slug": slugify(name, f"vault-{i+1}") or f"vault-{i+1}",
            "ip": ip,
            "ssh_user": user,
            "ssh_port": port,
            "index": i + 1,
        })
    return out


def _render_hcl(values: Dict[str, Any], api_port: int, cluster_port: int) -> str:
    """Render /etc/vault/vault.hcl content (mounted read-only into the container)."""
    data_dir_in_container = "/vault/file"
    tls_disable = bool(values.get("tls_disable", True))
    ui = bool(values.get("ui", True))
    disable_mlock = bool(values.get("disable_mlock", True))
    api_addr = values.get("api_address") or ""
    cluster_addr = values.get("cluster_address") or ""
    scheme = "http" if tls_disable else "https"

    lines: List[str] = []
    lines.append(f'ui            = {"true" if ui else "false"}')
    lines.append(f'disable_mlock = {"true" if disable_mlock else "false"}')
    lines.append("")
    lines += [
        'storage "raft" {',
        f'  path    = "{data_dir_in_container}"',
        '  node_id = "{{ vault_node_slug }}"',
        '{% for _h in (ansible_play_hosts_all | default([inventory_hostname])) if _h != inventory_hostname %}',
        '  retry_join {',
        f'    leader_api_addr = "{scheme}://{{{{ hostvars[_h].ansible_host | default(_h) }}}}:{api_port}"',
        '    leader_tls_servername = ""',
        '  }',
        '{% endfor %}',
        '}',
        '',
    ]
    lines += [
        'listener "tcp" {',
        f'  address         = "0.0.0.0:{api_port}"',
        f'  cluster_address = "0.0.0.0:{cluster_port}"',
        f'  tls_disable     = {"true" if tls_disable else "false"}',
    ]
    if not tls_disable:
        lines += [
            '  tls_cert_file = "/vault/tls/tls.crt"',
            '  tls_key_file  = "/vault/tls/tls.key"',
        ]
    lines += ['}', '']

    if api_addr:
        lines.append(f'api_addr     = "{api_addr}"')
    else:
        lines.append(
            f'api_addr     = "{scheme}://{{{{ ansible_host | default(inventory_hostname) }}}}:{api_port}"'
        )
    if cluster_addr:
        lines.append(f'cluster_addr = "{cluster_addr}"')
    else:
        lines.append(
            f'cluster_addr = "{scheme}://{{{{ ansible_host | default(inventory_hostname) }}}}:{cluster_port}"'
        )
    return "\n".join(lines) + "\n"


def render(values: Dict[str, Any], targets: Dict[str, Any]) -> str:
    become = "true" if values.get("become", True) else "false"

    cluster_id = str(values.get("cluster_id") or "opensible-vault").strip() or "opensible-vault"
    vault_version = str(values.get("vault_version") or "1.18.1").strip().lstrip("v") or "1.18.1"
    api_port = int(values.get("api_port") or 8200)
    cluster_port = int(values.get("cluster_port") or 8201)
    tls_disable = bool(values.get("tls_disable", True))
    disable_mlock = bool(values.get("disable_mlock", True))
    log_level = str(values.get("log_level") or "info").strip() or "info"
    data_dir = str(values.get("data_dir") or "/var/lib/vault-data").strip() or "/var/lib/vault-data"
    tls_cert_file = str(values.get("tls_cert_file") or "/etc/vault/tls/tls.crt")
    tls_key_file = str(values.get("tls_key_file") or "/etc/vault/tls/tls.key")
    auto_init = bool(values.get("auto_init", True))
    open_firewall = bool(values.get("open_firewall", True))
    health_http_enabled = bool(values.get("health_http_enabled", True))
    health_http_port = int(values.get("health_http_port") or 8280)


    scheme = "http" if tls_disable else "https"

    nodes = _norm_nodes(
        values.get("nodes"),
        values.get("ssh_user_default") or "root",
        values.get("ssh_port_default") or 22,
    )

    cluster_slug = slugify(cluster_id, "opensible-vault")
    cluster_group = cluster_slug.replace("-", "_") + "_nodes"
    container_name = f"vault-{cluster_slug}"

    hcl = _render_hcl(values, api_port, cluster_port)
    hcl_indented = "\n".join("          " + ln if ln else "" for ln in hcl.splitlines())

    parts: List[str] = ["---"]
    parts.append("# OpenSible vault-cluster template generation: 2026-07-vault-cluster-v1")
    parts.append(f"# Rendered from template: {TEMPLATE['name']}")
    parts.append(
        f"# Cluster: {cluster_id} | image tag: {vault_version} | "
        f"nodes: {len(nodes) if nodes else 'from targets'}"
    )
    parts.append("")

    # ---------------- PLAY 0 — dynamic inventory ----------------
    if nodes:
        parts += [
            "- name: Register Vault nodes into a dynamic inventory group",
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
            "        vault_node_name: \"{{ item.name }}\"",
            "        vault_node_slug: \"{{ item.node_slug }}\"",
            "        vault_node_index: \"{{ item.index }}\"",
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
    else:
        play_hosts = render_hosts(targets)

    # ---------------- PLAY 1 — install docker + run Vault per node ----------------
    # Build docker run body as a list of lines (each ends with " \\" except last)
    _docker_lines: List[str] = ["--name {{ vault_container_name }}"]
    if not disable_mlock:
        _docker_lines.append("--cap-add=IPC_LOCK")
    _docker_lines += [
        f"-e SKIP_SETCAP={'true' if disable_mlock else 'false'}",
        "-e VAULT_ADDR=http://127.0.0.1:{{ vault_api_port }}",
        "-e VAULT_API_ADDR={{ vault_scheme }}://{{ ansible_host | default(inventory_hostname) }}:{{ vault_api_port }}",
        "-e VAULT_CLUSTER_ADDR={{ vault_scheme }}://{{ ansible_host | default(inventory_hostname) }}:{{ vault_cluster_port }}",
        "-v /etc/vault/vault.hcl:/etc/vault.d/vault.hcl:ro",
        "-v {{ vault_data_dir }}:/vault/file",
    ]
    if not tls_disable:
        _docker_lines.append(f"-v {tls_cert_file}:/vault/tls/tls.crt:ro")
        _docker_lines.append(f"-v {tls_key_file}:/vault/tls/tls.key:ro")
    _docker_lines += [
        f"-p {api_port}:{api_port}",
        f"-p {cluster_port}:{cluster_port}",
        "{{ vault_image }}",
        "vault server -config=/etc/vault.d/vault.hcl -log-level={{ vault_log_level }}",
    ]
    _docker_run_lines: List[str] = ["          ExecStart=/usr/bin/docker run \\"]
    for i, arg in enumerate(_docker_lines):
        suffix = " \\" if i < len(_docker_lines) - 1 else ""
        _docker_run_lines.append(f"            {arg}{suffix}")


    parts += [
        f"- name: Deploy HashiCorp Vault {vault_version} (Docker + Raft) on every node",
        f"  hosts: {play_hosts}",
        f"  become: {become}",
        "  gather_facts: true",
        "  any_errors_fatal: true",
        *vars_files_lines(parse_vault_files(values.get("vault_files"))),
        "  vars:",
        f"    vault_cluster_id: {yaml_str(cluster_id)}",
        f"    vault_cluster_slug: {yaml_str(cluster_slug)}",
        f"    vault_container_name: {yaml_str(container_name)}",
        f"    vault_image: \"hashicorp/vault:{vault_version}\"",
        f"    vault_api_port: {api_port}",
        f"    vault_cluster_port: {cluster_port}",
        f"    vault_scheme: {yaml_str(scheme)}",
        f"    vault_log_level: {yaml_str(log_level)}",
        f"    vault_data_dir: {yaml_str(data_dir)}",
        "  tasks:",

        # ---- Preflight ----
        "    - name: Resolve node slug for Vault raft node_id",
        "      ansible.builtin.set_fact:",
        "        vault_node_slug: \"{{ vault_node_slug | default(inventory_hostname_short) | default(inventory_hostname) }}\"",
        "    - name: Refresh apt package metadata when available",
        "      ansible.builtin.shell: |",
        "        set -e",
        "        command -v apt-get >/dev/null 2>&1 || exit 0",
        "        apt-get update -y",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Install base prerequisite packages",
        "      ansible.builtin.package:",
        "        name:",
        "          - ca-certificates",
        "          - curl",
        "          - python3",
        "          - psmisc",
        "        state: present",
        "      failed_when: false",

        # ---- Docker Engine ----
        "    - name: Detect if docker is already installed",
        "      ansible.builtin.command: docker --version",
        "      register: _docker_have",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Install Docker Engine via the convenience script when missing",
        "      when: _docker_have.rc != 0",
        "      ansible.builtin.shell: |",
        "        set -e",
        "        curl -fsSL https://get.docker.com | sh",
        "      args:",
        "        executable: /bin/bash",
        "    - name: Ensure containerd is enabled and running",
        "      ansible.builtin.systemd:",
        "        name: containerd",
        "        enabled: true",
        "        state: started",
        "      failed_when: false",
        "    - name: Ensure docker service is enabled and running",
        "      ansible.builtin.systemd:",
        "        name: docker",
        "        enabled: true",
        "        state: started",
        "    - name: Stop existing vault-docker unit before Docker maintenance",
        "      ansible.builtin.systemd:",
        "        name: vault-docker",
        "        state: stopped",
        "      failed_when: false",
        "    - name: Reset failed vault-docker unit state before Docker maintenance",
        "      ansible.builtin.command: systemctl reset-failed vault-docker",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Self-heal docker daemon if unresponsive",
        "      ansible.builtin.shell: |",
        "        set +e",
        "        if ! docker info >/dev/null 2>&1; then",
        "          systemctl restart containerd || true",
        "          systemctl restart docker || true",
        "          for i in $(seq 1 15); do docker info >/dev/null 2>&1 && exit 0; sleep 2; done",
        "          exit 1",
        "        fi",
        "        exit 0",
        "      args:",
        "        executable: /bin/bash",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Repair Docker bridge network if docker0 is missing",
        "      ansible.builtin.shell: |",
        "        set +e",
        "        command -v docker >/dev/null 2>&1 || exit 0",
        "        command -v ip >/dev/null 2>&1 || exit 0",
        "        _needs_restart=0",
        "        docker network inspect bridge >/dev/null 2>&1 || _needs_restart=1",
        "        ip link show docker0 >/dev/null 2>&1 || _needs_restart=1",
        "        if [ \"$_needs_restart\" = \"1\" ]; then",
        "          echo 'Docker bridge network is stale or docker0 is missing; restarting docker/containerd'",
        "          systemctl stop docker docker.socket 2>/dev/null || true",
        "          ip link delete docker0 2>/dev/null || true",
        "          systemctl restart containerd 2>/dev/null || true",
        "          systemctl start docker 2>/dev/null || systemctl restart docker 2>/dev/null || true",
        "          for i in $(seq 1 20); do",
        "            docker info >/dev/null 2>&1 && docker network inspect bridge >/dev/null 2>&1 && ip link show docker0 >/dev/null 2>&1 && exit 0",
        "            sleep 2",
        "          done",
        "        fi",
        "        exit 0",
        "      args:",
        "        executable: /bin/bash",
        "      changed_when: false",
        "      failed_when: false",

        # ---- Pull image ----
        "    - name: Pull Vault image",
        "      ansible.builtin.command: docker pull {{ vault_image }}",
        "      register: _vault_pull",
        "      retries: 3",
        "      delay: 5",
        "      until: _vault_pull.rc == 0",
        "      changed_when: \"'Downloaded newer image' in _vault_pull.stdout or 'Pull complete' in _vault_pull.stdout\"",

        # ---- Cleanup stale containers ----
        "    - name: Remove stale Vault Docker containers",
        "      ansible.builtin.shell: |",
        "        set +e",
        "        command -v docker >/dev/null 2>&1 || exit 0",
        "        docker rm -f {{ vault_container_name }} 2>/dev/null || true",
        "        for c in $(docker ps -aq --filter 'name=^/vault-' 2>/dev/null); do",
        "          docker rm -f \"$c\" 2>/dev/null || true",
        "        done",
        "        exit 0",
        "      args:",
        "        executable: /bin/bash",
        "      changed_when: false",
        "      failed_when: false",

        "    - name: Stop conflicting host Vault/OpenBao services before Docker start",
        "      ansible.builtin.shell: |",
        "        set +e",
        "        for svc in vault vault.service openbao openbao.service bao bao.service; do",
        "          systemctl stop \"$svc\" 2>/dev/null || true",
        "          systemctl disable \"$svc\" 2>/dev/null || true",
        "          systemctl reset-failed \"$svc\" 2>/dev/null || true",
        "        done",
        "        exit 0",
        "      args:",
        "        executable: /bin/bash",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Free Vault API and raft ports before Docker start",
        "      ansible.builtin.shell: |",
        "        set +e",
        "        command -v fuser >/dev/null 2>&1 || exit 0",
        "        for p in {{ vault_api_port }} {{ vault_cluster_port }}; do",
        "          fuser -k -TERM \"${p}/tcp\" 2>/dev/null || true",
        "        done",
        "        sleep 1",
        "        for p in {{ vault_api_port }} {{ vault_cluster_port }}; do",
        "          fuser -k -KILL \"${p}/tcp\" 2>/dev/null || true",
        "        done",
        "        exit 0",
        "      args:",
        "        executable: /bin/bash",
        "      changed_when: false",
        "      failed_when: false",

        # ---- Config + data dirs ----
        "    - name: Ensure Vault host directories exist",
        "      ansible.builtin.file:",
        "        path: \"{{ item }}\"",
        "        state: directory",
        "        owner: root",
        "        group: root",
        "        mode: '0755'",
        "      loop:",
        "        - /etc/vault",
        "        - /etc/vault/tls",
        "        - /opt/vault/cluster-info",
        "        - \"{{ vault_data_dir }}\"",
        "    - name: Ensure Raft data dir is writable by the vault container user (UID 100)",
        "      ansible.builtin.file:",
        "        path: \"{{ vault_data_dir }}\"",
        "        state: directory",
        "        owner: '100'",
        "        group: '1000'",
        "        mode: '0770'",
        "      failed_when: false",

        "    - name: Render /etc/vault/vault.hcl",
        "      ansible.builtin.copy:",
        "        dest: /etc/vault/vault.hcl",
        "        owner: root",
        "        group: root",
        "        mode: '0644'",
        "        content: |",
        hcl_indented.rstrip(),
        "      register: _vault_config",

        # ---- Systemd unit ----
        "    - name: Write vault-docker systemd unit",
        "      ansible.builtin.copy:",
        "        dest: /etc/systemd/system/vault-docker.service",
        "        mode: '0644'",
        "        content: |",
        "          [Unit]",
        f"          Description=OpenSible HashiCorp Vault ({cluster_id}) via Docker",
        "          Requires=docker.service",
        "          After=docker.service network-online.target",
        "          Wants=network-online.target",
        "",
        "          [Service]",
        "          Type=simple",
        "          Restart=always",
        "          RestartSec=5",
        "          TimeoutStartSec=0",
        "          ExecStartPre=-/usr/bin/docker stop {{ vault_container_name }}",
        "          ExecStartPre=-/usr/bin/docker rm {{ vault_container_name }}",
        *_docker_run_lines,
        "          ExecStop=/usr/bin/docker stop {{ vault_container_name }}",
        "",
        "          [Install]",
        "          WantedBy=multi-user.target",
        "      register: _vault_unit",
        "    - name: Reload systemd for vault-docker unit",
        "      ansible.builtin.systemd:",
        "        daemon_reload: true",
        "      when: _vault_unit.changed",
        "    - name: Enable + start vault-docker",
        "      ansible.builtin.systemd:",
        "        name: vault-docker",
        "        enabled: true",
        "        state: started",
        "    - name: Restart vault-docker if unit or config changed",
        "      when: _vault_unit.changed or _vault_config.changed",
        "      ansible.builtin.systemd:",
        "        name: vault-docker",
        "        state: restarted",
    ]

    # ---- Firewall ----
    if open_firewall:
        parts += [
            "    - name: Open Vault ports (ufw, if active)",
            "      ansible.builtin.shell: |",
            "        set -e",
            "        command -v ufw >/dev/null 2>&1 || exit 0",
            "        ufw status | grep -q 'Status: active' || exit 0",
            f"        ufw allow {api_port}/tcp || true",
            f"        ufw allow {cluster_port}/tcp || true",
            *([f"        ufw allow {health_http_port}/tcp || true"] if health_http_enabled else []),
            "      changed_when: false",
            "      failed_when: false",
            "    - name: Open Vault ports (firewalld, if active)",
            "      ansible.builtin.shell: |",
            "        set -e",
            "        command -v firewall-cmd >/dev/null 2>&1 || exit 0",
            "        firewall-cmd --state >/dev/null 2>&1 || exit 0",
            f"        firewall-cmd --permanent --add-port={api_port}/tcp || true",
            f"        firewall-cmd --permanent --add-port={cluster_port}/tcp || true",
            *([f"        firewall-cmd --permanent --add-port={health_http_port}/tcp || true"] if health_http_enabled else []),
            "        firewall-cmd --reload || true",

            "      changed_when: false",
            "      failed_when: false",
        ]

    # ---- Wait for API to open (sealed is OK) ----
    parts += [
        f"    - name: Wait for Vault API port {api_port} to open locally",
        "      ansible.builtin.wait_for:",
        "        host: 127.0.0.1",
        f"        port: {api_port}",
        "        timeout: 300",
        "      register: _vault_wait",
        "      ignore_errors: true",
        "    - name: Wait for Vault Docker container to stay running",
        "      ansible.builtin.shell: |",
        "        set +e",
        "        ok=0",
        "        for i in $(seq 1 90); do",
        "          state=$(docker inspect -f '{{ '{{' }}.State.Running{{ '}}' }}' {{ vault_container_name }} 2>/dev/null)",
        "          if [ \"$state\" = \"true\" ]; then",
        "            ok=$((ok + 1))",
        "            [ $ok -ge 5 ] && exit 0",
        "          else",
        "            ok=0",
        "          fi",
        "          sleep 2",
        "        done",
        "        exit 1",
        "      args:",
        "        executable: /bin/bash",
        "      register: _vault_container_wait",
        "      changed_when: false",
        "      ignore_errors: true",
        "    - name: Wait for Vault status from inside the Docker container",
        "      ansible.builtin.shell: |",
        "        set +e",
        "        for i in $(seq 1 120); do",
        "          state=$(docker inspect -f '{{ '{{' }}.State.Running{{ '}}' }}' {{ vault_container_name }} 2>/dev/null)",
        "          if [ \"$state\" = \"true\" ]; then",
        "            out=$(docker exec -e VAULT_ADDR=http://127.0.0.1:{{ vault_api_port }} {{ vault_container_name }} vault status -format=json 2>/tmp/vault-status.err)",
        "            rc=$?",
        "            if [ -n \"$out\" ]; then printf '%s\\n' \"$out\"; exit 0; fi",
        "            [ $rc -eq 0 ] || [ $rc -eq 2 ] || cat /tmp/vault-status.err >&2 || true",
        "          fi",
        "          sleep 2",
        "        done",
        "        echo 'Vault did not return status JSON from inside the Docker container.' >&2",
        "        exit 1",
        "      args:",
        "        executable: /bin/bash",
        "      register: _vault_status_wait",
        "      changed_when: false",
        "      ignore_errors: true",
        "    - name: Collect Vault startup diagnostics when API did not open",
        "      when: _vault_wait is failed or _vault_container_wait is failed or _vault_status_wait is failed",
        "      ansible.builtin.shell: |",
        "        set +e",
        "        echo '== systemctl status vault-docker =='",
        "        systemctl status vault-docker --no-pager -l 2>&1 || true",
        "        echo '== recent vault-docker journal =='",
        "        journalctl -u vault-docker -n 200 --no-pager 2>&1 || true",
        "        echo '== docker ps -a =='",
        "        docker ps -a 2>&1 || true",
        "        echo '== vault container inspect =='",
        "        docker inspect {{ vault_container_name }} 2>&1 || true",
        "        echo '== vault container logs =='",
        "        docker logs --tail 200 {{ vault_container_name }} 2>&1 || true",
        "        echo '== docker bridge network =='",
        "        docker network inspect bridge 2>&1 || true",
        "        ip link show docker0 2>&1 || true",
        "        echo '== local listeners on Vault ports =='",
        "        ss -tulnp 2>&1 | grep -E ':(8200|8201)\\b' || true",
        "      args:",
        "        executable: /bin/bash",
        "      register: _vault_startup_diag",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Show Vault startup diagnostics",
        "      when: _vault_wait is failed or _vault_container_wait is failed or _vault_status_wait is failed",
        "      ansible.builtin.debug:",
        "        var: _vault_startup_diag.stdout_lines",
        "    - name: Fail if Vault API did not open",
        "      when: _vault_wait is failed",
        "      ansible.builtin.fail:",
        f"        msg: Vault did not open local API port {api_port}; see diagnostics above.",
        "    - name: Fail if Vault Docker container is not running",
        "      when: _vault_container_wait is failed",
        "      ansible.builtin.fail:",
        "        msg: Vault API port opened but the Docker container is not running; see diagnostics above.",
        "    - name: Fail if Vault status is unavailable inside Docker",
        "      when: _vault_status_wait is failed",
        "      ansible.builtin.fail:",
        "        msg: Vault container is running but Vault did not return status JSON; see diagnostics above.",

        # ---- Leader-info bundle (always) ----
        "    - name: Compute leader API address",
        "      ansible.builtin.set_fact:",
        "        vault_leader_api_addr: \"{{ vault_scheme }}://{{ hostvars[ansible_play_hosts_all[0]].ansible_host | default(ansible_play_hosts_all[0]) }}:{{ vault_api_port }}\"",
        "    - name: Write leader-info.txt on every node",
        "      ansible.builtin.copy:",
        "        dest: /opt/vault/cluster-info/leader-info.txt",
        "        owner: root",
        "        group: root",
        "        mode: '0640'",
        "        content: |",
        "          # HashiCorp Vault (Docker) cluster join info",
        "          Leader API Address : {{ vault_leader_api_addr }}",
        "          This node API addr : {{ vault_scheme }}://{{ ansible_host | default(inventory_hostname) }}:{{ vault_api_port }}",
        "          Cluster peers      : {{ ansible_play_hosts_all | join(', ') }}",
        "          TLS enabled        : {{ 'no (tls_disable=true)' if (vault_scheme == 'http') else 'yes' }}",
        "          Container name     : {{ vault_container_name }}",
        "          Image              : {{ vault_image }}",
    ]

    # ---- Auto init + unseal ----
    if auto_init:
        parts += [
            "    - name: Wait for Vault CLI status JSON on leader",
            "      ansible.builtin.shell: |",
            "        set +e",
            "        for i in $(seq 1 90); do",
            "          state=$(docker inspect -f '{{ '{{' }}.State.Running{{ '}}' }}' {{ vault_container_name }} 2>/dev/null)",
            "          if [ \"$state\" = \"true\" ]; then",
            "            out=$(docker exec -e VAULT_ADDR=http://127.0.0.1:{{ vault_api_port }} {{ vault_container_name }} vault status -format=json 2>/tmp/vault-status.err)",
            "            rc=$?",
            "            if [ -n \"$out\" ]; then printf '%s\\n' \"$out\"; exit 0; fi",
            "            [ $rc -eq 0 ] || [ $rc -eq 2 ] || cat /tmp/vault-status.err >&2 || true",
            "          fi",
            "          sleep 2",
            "        done",
            "        echo 'Vault container did not return status JSON before init.' >&2",
            "        systemctl status vault-docker --no-pager -l >&2 || true",
            "        docker ps -a >&2 || true",
            "        docker logs --tail 200 {{ vault_container_name }} >&2 || true",
            "        exit 1",
            "      args:",
            "        executable: /bin/bash",
            "      register: _vault_status_ready",
            "      changed_when: false",
            "      run_once: true",

            "    - name: Check Vault init status (leader)",
            "      ansible.builtin.shell: |",
            "        set +e",
            "        out=$(docker exec -e VAULT_ADDR=http://127.0.0.1:{{ vault_api_port }} {{ vault_container_name }} vault status -format=json 2>/tmp/vault-status.err)",
            "        rc=$?",
            "        if [ -n \"$out\" ]; then printf '%s\\n' \"$out\"; exit 0; fi",
            "        cat /tmp/vault-status.err >&2 || true",
            "        exit $rc",
            "      args:",
            "        executable: /bin/bash",
            "      register: _vault_status_leader",
            "      changed_when: false",
            "      run_once: true",

            "    - name: Initialize Vault (first node only)",
            "      ansible.builtin.shell: |",
            "        set +e",
            "        docker exec -e VAULT_ADDR=http://127.0.0.1:{{ vault_api_port }} {{ vault_container_name }} vault operator init -key-shares=5 -key-threshold=3 -format=json",
            "      register: _vault_init",
            "      retries: 6",
            "      delay: 5",
            "      until: _vault_init.rc == 0",
            "      failed_when: false",
            "      run_once: true",
            "      when: (_vault_status_leader.stdout | default('', true) | trim | length) == 0 or (_vault_status_leader.stdout | default('{}', true) | from_json).initialized | default(false) == false",

            "    - name: Collect Vault init diagnostics when initialization failed",
            "      when: _vault_init is defined and _vault_init.rc is defined and _vault_init.rc != 0",
            "      ansible.builtin.shell: |",
            "        set +e",
            "        echo '== init stderr =='",
            "        printf '%s\\n' {{ _vault_init.stderr | default('') | quote }}",
            "        echo '== init stdout =='",
            "        printf '%s\\n' {{ _vault_init.stdout | default('') | quote }}",
            "        echo '== systemctl status vault-docker =='",
            "        systemctl status vault-docker --no-pager -l 2>&1 || true",
            "        echo '== recent vault-docker journal =='",
            "        journalctl -u vault-docker -n 200 --no-pager 2>&1 || true",
            "        echo '== docker ps -a =='",
            "        docker ps -a 2>&1 || true",
            "        echo '== vault container inspect =='",
            "        docker inspect {{ vault_container_name }} 2>&1 || true",
            "        echo '== vault container logs =='",
            "        docker logs --tail 200 {{ vault_container_name }} 2>&1 || true",
            "      args:",
            "        executable: /bin/bash",
            "      register: _vault_init_diag",
            "      changed_when: false",
            "      failed_when: false",

            "    - name: Show Vault init diagnostics",
            "      when: _vault_init_diag is defined",
            "      ansible.builtin.debug:",
            "        var: _vault_init_diag.stdout_lines",

            "    - name: Fail if Vault initialization failed",
            "      when: _vault_init is defined and _vault_init.rc is defined and _vault_init.rc != 0",
            "      ansible.builtin.fail:",
            "        msg: Vault initialization failed inside the Docker container; see diagnostics above.",


            "    - name: Persist init output to /etc/vault/init.json (leader)",
            "      ansible.builtin.copy:",
            "        dest: /etc/vault/init.json",
            "        content: \"{{ _vault_init.stdout }}\"",
            "        owner: root",
            "        group: root",
            "        mode: '0600'",
            "      run_once: true",
            "      when: _vault_init is defined and _vault_init.stdout is defined and (_vault_init.stdout | length) > 0",

            "    - name: Share unseal keys and root token across the play",
            "      ansible.builtin.set_fact:",
            "        vault_unseal_keys: \"{{ (_vault_init.stdout | from_json).unseal_keys_b64 }}\"",
            "        vault_root_token: \"{{ (_vault_init.stdout | from_json).root_token }}\"",
            "      run_once: true",
            "      when: _vault_init is defined and _vault_init.stdout is defined and (_vault_init.stdout | length) > 0",

            "    - name: Give raft followers time to retry_join the leader",
            "      ansible.builtin.pause:",
            "        seconds: 15",
            "      when: hostvars[ansible_play_hosts_all[0]].vault_unseal_keys is defined",

            "    - name: Wait for Vault API on every node (post-init)",
            "      ansible.builtin.wait_for:",
            "        host: 127.0.0.1",
            f"        port: {api_port}",
            "        timeout: 120",

            "    - name: Unseal every node with the shared keys (retries until sealed=false)",
            "      ansible.builtin.shell: |",
            "        set +e",
            "        for k in {{ (hostvars[ansible_play_hosts_all[0]].vault_unseal_keys | default([])) | join(' ') }}; do",
            "          docker exec -e VAULT_ADDR=http://127.0.0.1:{{ vault_api_port }} {{ vault_container_name }} vault operator unseal \"$k\" >/dev/null 2>&1 || true",
            "        done",
            "        docker exec -e VAULT_ADDR=http://127.0.0.1:{{ vault_api_port }} {{ vault_container_name }} vault status -format=json 2>/dev/null | grep -q '\"sealed\": false'",
            "      args:",
            "        executable: /bin/bash",
            "      register: _vault_unseal_result",
            "      retries: 12",
            "      delay: 5",
            "      until: _vault_unseal_result.rc == 0",
            "      changed_when: false",
            "      when: hostvars[ansible_play_hosts_all[0]].vault_unseal_keys is defined",

            "    - name: Write full cluster-info bundle on every node",
            "      ansible.builtin.copy:",
            "        dest: /opt/vault/cluster-info/cluster-info.txt",
            "        owner: root",
            "        group: root",
            "        mode: '0600'",
            "        content: |",
            "          # HashiCorp Vault (Docker) bootstrap bundle — STORE SECURELY",
            "          Leader API Address : {{ vault_leader_api_addr }}",
            "          Root Token         : {{ hostvars[ansible_play_hosts_all[0]].vault_root_token | default('(already initialized before)') }}",
            "          Unseal Keys (b64)  :",
            "          {% for k in (hostvars[ansible_play_hosts_all[0]].vault_unseal_keys | default([])) %}",
            "            - {{ k }}",
            "          {% endfor %}",
            "          Full init JSON     : /etc/vault/init.json (leader, mode 0600)",
            "      when: hostvars[ansible_play_hosts_all[0]].vault_unseal_keys is defined",

            # ---- Persistent auto-unseal (systemd oneshot + timer) ----
            "    - name: Persist unseal keys on every node for auto-unseal (root:root 0600)",
            "      ansible.builtin.copy:",
            "        dest: /etc/vault/unseal.keys",
            "        owner: root",
            "        group: root",
            "        mode: '0600'",
            "        content: |",
            "          {% for k in (hostvars[ansible_play_hosts_all[0]].vault_unseal_keys | default([])) %}",
            "          {{ k }}",
            "          {% endfor %}",
            "      when: hostvars[ansible_play_hosts_all[0]].vault_unseal_keys is defined",

            "    - name: Install vault-autounseal helper script",
            "      ansible.builtin.copy:",
            "        dest: /usr/local/sbin/vault-autounseal.sh",
            "        owner: root",
            "        group: root",
            "        mode: '0750'",
            "        content: |",
            "          #!/usr/bin/env bash",
            "          # Persistent auto-unseal for HashiCorp Vault running in Docker.",
            "          # Reads unseal keys from /etc/vault/unseal.keys and submits them",
            "          # via `docker exec` until the node reports sealed=false.",
            "          set -u",
            f"          CONTAINER={container_name}",
            f"          VAULT_ADDR_LOCAL=\"http://127.0.0.1:{api_port}\"",
            "          KEYS_FILE=/etc/vault/unseal.keys",
            "          [ -r \"$KEYS_FILE\" ] || { echo \"no keys file, nothing to do\"; exit 0; }",
            "          # Wait for container + API",
            "          for i in $(seq 1 60); do",
            "            if docker exec -e VAULT_ADDR=\"$VAULT_ADDR_LOCAL\" \"$CONTAINER\" vault status -format=json >/tmp/vault-status.json 2>/dev/null; then break; fi",
            "            sleep 2",
            "          done",
            "          if ! grep -q '\"initialized\": true' /tmp/vault-status.json 2>/dev/null; then",
            "            echo \"node not initialized yet; skipping\"; exit 0",
            "          fi",
            "          for attempt in $(seq 1 30); do",
            "            if docker exec -e VAULT_ADDR=\"$VAULT_ADDR_LOCAL\" \"$CONTAINER\" vault status -format=json 2>/dev/null | grep -q '\"sealed\": false'; then",
            "              echo \"already unsealed\"; exit 0",
            "            fi",
            "            while IFS= read -r k; do",
            "              [ -z \"$k\" ] && continue",
            "              docker exec -e VAULT_ADDR=\"$VAULT_ADDR_LOCAL\" \"$CONTAINER\" vault operator unseal \"$k\" >/dev/null 2>&1 || true",
            "            done < \"$KEYS_FILE\"",
            "            if docker exec -e VAULT_ADDR=\"$VAULT_ADDR_LOCAL\" \"$CONTAINER\" vault status -format=json 2>/dev/null | grep -q '\"sealed\": false'; then",
            "              echo \"unsealed on attempt $attempt\"; exit 0",
            "            fi",
            "            sleep 3",
            "          done",
            "          echo \"failed to unseal after retries\" >&2",
            "          exit 1",

            "    - name: Install vault-autounseal.service",
            "      ansible.builtin.copy:",
            "        dest: /etc/systemd/system/vault-autounseal.service",
            "        owner: root",
            "        group: root",
            "        mode: '0644'",
            "        content: |",
            "          [Unit]",
            "          Description=HashiCorp Vault auto-unseal helper (Docker)",
            "          After=vault-docker.service network-online.target",
            "          Wants=vault-docker.service network-online.target",
            "          PartOf=vault-docker.service",
            "          [Service]",
            "          Type=oneshot",
            "          RemainAfterExit=yes",
            "          ExecStart=/usr/local/sbin/vault-autounseal.sh",
            "          Restart=on-failure",
            "          RestartSec=10s",
            "          [Install]",
            "          WantedBy=multi-user.target",
            "    - name: Install vault-autounseal.timer (safety net every 2min)",
            "      ansible.builtin.copy:",
            "        dest: /etc/systemd/system/vault-autounseal.timer",
            "        owner: root",
            "        group: root",
            "        mode: '0644'",
            "        content: |",
            "          [Unit]",
            "          Description=Periodically ensure HashiCorp Vault is unsealed",
            "          [Timer]",
            "          OnBootSec=30s",
            "          OnUnitActiveSec=2min",
            "          Unit=vault-autounseal.service",
            "          [Install]",
            "          WantedBy=timers.target",
            "    - name: Reload systemd (auto-unseal units)",
            "      ansible.builtin.systemd:",
            "        daemon_reload: true",
            "    - name: Enable and start vault-autounseal.service",
            "      ansible.builtin.systemd:",
            "        name: vault-autounseal.service",
            "        enabled: true",
            "        state: started",
            "      failed_when: false",
            "    - name: Enable and start vault-autounseal.timer",
            "      ansible.builtin.systemd:",
            "        name: vault-autounseal.timer",
            "        enabled: true",
            "        state: started",

            "    - name: Root token (STORE SECURELY, first run only)",
            "      ansible.builtin.debug:",
            "        msg:",
            "          - \"Leader API Address : {{ vault_leader_api_addr }}\"",
            "          - \"Root token         : {{ hostvars[ansible_play_hosts_all[0]].vault_root_token | default('(already initialized on a previous run)') }}\"",
            "          - \"Bundle on every node : /opt/vault/cluster-info/  (cluster-info.txt, leader-info.txt)\"",
            "          - \"Full init JSON on leader: /etc/vault/init.json (mode 0600).\"",
            "          - \"Auto-unseal        : enabled via vault-autounseal.service + .timer (keys at /etc/vault/unseal.keys, root:root 0600)\"",
            "      run_once: true",
        ]

    # ---- Health dashboard ----
    if health_http_enabled:
        parts += _vault_health_tasks(
            cluster_id=cluster_id,
            product="Vault",
            script_path="/usr/local/bin/opensible-vault-health.py",
            service_name="opensible-vault-health.service",
            unit_after="vault-docker.service",
            http_port=health_http_port,
            api_port=api_port,
            scheme=scheme,
        )

    # ---------------- PLAY 2 — verification ----------------

    parts += [
        "- name: Verify Vault cluster availability",
        f"  hosts: {play_hosts}",
        f"  become: {become}",
        "  gather_facts: false",
        "  run_once: true",
        "  tasks:",
        "    - name: Query Vault /v1/sys/health (200 healthy, 429 standby, 472 DR standby, 473 perf standby, 501 not-init, 503 sealed)",
        "      ansible.builtin.uri:",
        f"        url: \"http://127.0.0.1:{api_port}/v1/sys/health\"",
        "        status_code: [200, 429, 472, 473, 501, 503]",
        "        return_content: true",
        "      register: _vault_health",
        "      retries: 30",
        "      delay: 5",
        "      until: _vault_health.status in [200, 429, 472, 473, 501, 503]",
        "    - name: Vault cluster summary",
        "      ansible.builtin.debug:",
        "        msg:",
        f"          - \"Vault cluster '{cluster_id}' is reachable.\"",
        "          - \"Health code: {{ _vault_health.status }} (200=active-unsealed, 429=standby, 501=uninitialized, 503=sealed)\"",
        f"          - \"UI / API: {scheme}://<node-ip>:{api_port}\"",
        "",
    ]

    return "\n".join(parts)
