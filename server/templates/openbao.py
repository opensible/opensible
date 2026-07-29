"""Template: Install & run OpenBao as a systemd service (no Docker)."""
from __future__ import annotations

from typing import Any, Dict

from ._common import (
    render_hosts,
    yaml_str,
    slugify,
    VAULT_FILES_VARIABLE,
    parse_vault_files,
    vars_files_lines,
)
from ._vault_health import health_tasks as _vault_health_tasks



def _norm_nodes(raw, default_user, default_port):
    out = []
    if not isinstance(raw, list):
        return out
    for i, n in enumerate(raw):
        if not isinstance(n, dict):
            continue
        ip = str(n.get("ip") or "").strip()
        if not ip:
            continue
        name = str(n.get("name") or f"bao-{i+1}").strip() or f"bao-{i+1}"
        user = str(n.get("ssh_user") or default_user or "root").strip() or "root"
        try:
            port = int(n.get("ssh_port") or default_port or 22)
        except Exception:
            port = 22
        out.append({
            "name": name,
            "node_slug": slugify(name, f"bao-{i+1}") or f"bao-{i+1}",
            "ip": ip,
            "ssh_user": user,
            "ssh_port": port,
            "index": i + 1,
        })
    return out


TEMPLATE = {
    "id": "openbao-systemd",
    "name": "OpenBao (systemd)",
    "category": "Secrets",
    "icon": "package",
    "description": (
        "Install OpenBao from pinned official GitHub release assets and run it as a "
        "systemd service. Renders /etc/openbao/openbao.hcl with a configurable "
        "storage backend, listener, cluster address, UI and mlock settings."
    ),
    "tags": ["openbao", "vault", "secrets", "systemd"],
    "variables": [
        {"name": "cluster_id", "label": "Cluster ID",
         "type": "string", "default": "opensible-bao",
         "help": "Used to name the dynamic inventory group when 'Cluster nodes' is filled in."},
        {"name": "nodes", "label": "Cluster nodes (first = initial leader)",
         "type": "nodes", "required": False,
         "help": "List each OpenBao node with its IP and SSH credentials. Leave empty to fall back to the selected targets/inventory hosts. Use 3+ nodes for real Raft HA."},
        {"name": "ssh_user_default", "label": "Default SSH user for nodes",
         "type": "string", "default": "root"},
        {"name": "ssh_port_default", "label": "Default SSH port for nodes",
         "type": "number", "default": 22},
        {"name": "version", "label": "OpenBao version (empty = stable pinned)",
         "type": "string", "default": "2.6.0", "help": "Pinned by default so installs do not depend on the GitHub API / rate limits."},
        {"name": "listen_address", "label": "Listener address:port",
         "type": "string", "default": "0.0.0.0:8200"},
        {"name": "cluster_address", "label": "Cluster address (advertised)",
         "type": "string", "default": "", "help": "e.g. https://bao01.orb.local:8201 — leave empty to auto-derive"},
        {"name": "api_address", "label": "API address (advertised)",
         "type": "string", "default": "", "help": "e.g. https://bao01.orb.local:8200 — leave empty to auto-derive"},
        {"name": "tls_disable", "label": "Disable TLS on listener (dev only)",
         "type": "boolean", "default": True},
        {"name": "tls_cert_file", "label": "TLS cert file path",
         "type": "string", "default": "/etc/openbao/tls/tls.crt"},
        {"name": "tls_key_file", "label": "TLS key file path",
         "type": "string", "default": "/etc/openbao/tls/tls.key"},
        {"name": "storage_backend", "label": "Storage backend",
         "type": "select", "default": "raft",
         "options": [
             {"value": "raft", "label": "Integrated Raft (recommended)"},
             {"value": "file", "label": "File (single node, dev)"},
         ]},
        {"name": "raft_node_id", "label": "Raft node_id (unique per node)",
         "type": "string", "default": "{{ inventory_hostname }}"},
        {"name": "data_dir", "label": "Data directory",
         "type": "string", "default": "/opt/openbao/data"},
        {"name": "ui", "label": "Enable Web UI",
         "type": "boolean", "default": True},
        {"name": "disable_mlock", "label": "Disable mlock (set true on Raft/no-swap)",
         "type": "boolean", "default": True},
        {"name": "log_level", "label": "Log level",
         "type": "select", "default": "info",
         "options": [
             {"value": "trace", "label": "trace"},
             {"value": "debug", "label": "debug"},
             {"value": "info", "label": "info"},
             {"value": "warn", "label": "warn"},
             {"value": "error", "label": "error"},
         ]},
        {"name": "auto_init", "label": "Auto-init & persistent auto-unseal",
         "type": "boolean", "default": True,
         "help": "Runs `bao operator init` on the first host once, unseals every node, and installs openbao-autounseal.service + .timer on all nodes so OpenBao re-unseals automatically on every boot/restart. Unseal keys are stored at /etc/openbao/unseal.keys (root:root 0600). Full bundle in /opt/openbao/cluster-info/."},
        {"name": "health_http_enabled", "label": "Enable HTTP health dashboard",
         "type": "boolean", "default": True,
         "help": "Install a small HTTP service on each node exposing / (HTML dashboard), /health.json and /live. Shows initialized/sealed/standby/leader state."},
        {"name": "health_http_port", "label": "Health HTTP port",
         "type": "number", "default": 8280,
         "help": "Port for the health dashboard. Visit http://<node-ip>:<port>/ from your browser."},
        {"name": "become", "label": "Run as sudo (become)",
         "type": "boolean", "default": True},
        VAULT_FILES_VARIABLE,
    ],
}



def suggested_filename(values: Dict[str, Any]) -> str:
    return "tmpl-openbao-systemd.yml"


def _render_hcl(values: Dict[str, Any]) -> str:
    """Render /etc/openbao/openbao.hcl content as a single string."""
    storage = values.get("storage_backend", "raft")
    data_dir = values.get("data_dir", "/opt/openbao/data")
    node_id = values.get("raft_node_id", "{{ inventory_hostname }}")
    listen_address = values.get("listen_address", "0.0.0.0:8200")
    tls_disable = bool(values.get("tls_disable", True))
    tls_cert = values.get("tls_cert_file", "/etc/openbao/tls/tls.crt")
    tls_key = values.get("tls_key_file", "/etc/openbao/tls/tls.key")
    ui = bool(values.get("ui", True))
    disable_mlock = bool(values.get("disable_mlock", True))
    api_addr = values.get("api_address") or ""
    cluster_addr = values.get("cluster_address") or ""

    lines = []
    lines.append(f'ui           = {"true" if ui else "false"}')
    lines.append(f'disable_mlock = {"true" if disable_mlock else "false"}')
    lines.append("")
    if storage == "raft":
        scheme_r = "http" if tls_disable else "https"
        port_r = listen_address.split(":")[-1] or "8200"
        lines += [
            'storage "raft" {',
            f'  path    = "{data_dir}"',
            f'  node_id = "{node_id}"',
            '{% for _h in (ansible_play_hosts_all | default([inventory_hostname])) if _h != inventory_hostname %}',
            '  retry_join {',
            f'    leader_api_addr = "{scheme_r}://{{{{ hostvars[_h].ansible_host | default(_h) }}}}:{port_r}"',
            f'    leader_tls_servername = ""',
            '  }',
            '{% endfor %}',
            "}",
            "",
        ]
    else:
        lines += [
            'storage "file" {',
            f'  path = "{data_dir}"',
            "}",
            "",
        ]
    lines += [
        'listener "tcp" {',
        f'  address     = "{listen_address}"',
        f'  tls_disable = {"true" if tls_disable else "false"}',
    ]
    if not tls_disable:
        lines += [
            f'  tls_cert_file = "{tls_cert}"',
            f'  tls_key_file  = "{tls_key}"',
        ]
    lines += ["}", ""]

    if api_addr:
        lines.append(f'api_addr     = "{api_addr}"')
    else:
        scheme = "http" if tls_disable else "https"
        port = listen_address.split(":")[-1] or "8200"
        lines.append(f'api_addr     = "{scheme}://{{{{ ansible_host | default(inventory_hostname) }}}}:{port}"')
    if storage == "raft":
        if cluster_addr:
            lines.append(f'cluster_addr = "{cluster_addr}"')
        else:
            scheme = "http" if tls_disable else "https"
            lines.append(f'cluster_addr = "{scheme}://{{{{ ansible_host | default(inventory_hostname) }}}}:8201"')
    return "\n".join(lines) + "\n"


def render(values: Dict[str, Any], targets: Dict[str, Any]) -> str:
    become = "true" if values.get("become", True) else "false"
    version = (values.get("version") or "2.6.0").strip() or "2.6.0"
    log_level = values.get("log_level", "info")
    data_dir = values.get("data_dir", "/opt/openbao/data")
    auto_init = bool(values.get("auto_init", False))
    listen_address = values.get("listen_address", "0.0.0.0:8200")
    port = listen_address.split(":")[-1] or "8200"
    scheme = "http" if values.get("tls_disable", True) else "https"

    cluster_id = values.get("cluster_id") or "opensible-bao"
    nodes = _norm_nodes(
        values.get("nodes"),
        values.get("ssh_user_default") or "root",
        values.get("ssh_port_default") or 22,
    )
    cluster_group = slugify(cluster_id, "openbao") + "_nodes"

    hcl = _render_hcl(values)
    hcl_indented = "\n".join("          " + ln if ln else "" for ln in hcl.splitlines())

    parts: list = ["---",
        f"# Rendered from template: {TEMPLATE['name']}",
        f"# Cluster: {cluster_id} | nodes: {len(nodes) if nodes else 'from targets'}",
    ]

    if nodes:
        parts += [
            "- name: Register OpenBao nodes into a dynamic inventory group",
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
            "        openbao_node_slug: \"{{ item.node_slug }}\"",
            "        openbao_node_index: \"{{ item.index }}\"",
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

    parts += [
        f"- name: Install and configure OpenBao (systemd)",
        f"  hosts: {play_hosts}",
        f"  become: {become}",
        "  gather_facts: true",
        *vars_files_lines(parse_vault_files(values.get("vault_files"))),
        "  vars:",
        f"    openbao_log_level: {yaml_str(log_level)}",
        f"    openbao_data_dir: {yaml_str(data_dir)}",
        f"    openbao_port: {yaml_str(port)}",
        f"    openbao_scheme: {yaml_str(scheme)}",
        "  tasks:",

        "    - name: Ensure prerequisites (Debian)",
        "      ansible.builtin.apt:",
        "        name: [ca-certificates, curl, gnupg, lsb-release]",
        "        state: present",
        "        update_cache: true",
        "      when: ansible_os_family == 'Debian'",

        "    - name: Ensure prerequisites (RedHat)",
        "      ansible.builtin.package:",
        "        name: [ca-certificates, curl, tar]",
        "        state: present",
        "      when: ansible_os_family == 'RedHat'",

        # ---------- Resolve target version (empty = pinned stable; no GitHub API) ----------
        f"    - name: Set requested OpenBao version",
        f"      ansible.builtin.set_fact:",
        f"        openbao_requested_version: {yaml_str(version or '')}",
        "    - name: Compute effective OpenBao version",
        "      ansible.builtin.set_fact:",
        "        openbao_version: \"{{ (openbao_requested_version | default('2.6.0', true)) | regex_replace('^v','') }}\"",
        "    - name: Compute package architecture",
        "      ansible.builtin.set_fact:",
        "        openbao_arch: \"{{ 'amd64' if ansible_architecture in ['x86_64','amd64'] else ('arm64' if ansible_architecture in ['aarch64','arm64'] else ansible_architecture) }}\"",

        # ---------- Install from GitHub release assets ----------
        "    - name: Check if bao binary is already present",
        "      ansible.builtin.command: bao version",
        "      register: openbao_installed",
        "      changed_when: false",
        "      failed_when: false",

        "    - name: Download OpenBao .deb (Debian family)",
        "      ansible.builtin.get_url:",
        "        url: \"https://github.com/openbao/openbao/releases/download/v{{ openbao_version }}/openbao_{{ openbao_version }}_linux_{{ openbao_arch }}.deb\"",
        "        dest: \"/tmp/openbao_{{ openbao_version }}_linux_{{ openbao_arch }}.deb\"",
        "        mode: '0644'",
        "      retries: 3",
        "      delay: 5",
        "      register: openbao_deb_download",
        "      until: openbao_deb_download is succeeded",
        "      when:",
        "        - ansible_os_family == 'Debian'",
        "        - openbao_installed.rc != 0 or (openbao_version not in (openbao_installed.stdout | default(''))",
        "          )",
        "    - name: Install OpenBao .deb (Debian family)",
        "      ansible.builtin.apt:",
        "        deb: \"/tmp/openbao_{{ openbao_version }}_linux_{{ openbao_arch }}.deb\"",
        "        state: present",
        "      when:",
        "        - ansible_os_family == 'Debian'",
        "        - openbao_installed.rc != 0 or (openbao_version not in (openbao_installed.stdout | default(''))",
        "          )",

        "    - name: Download OpenBao .rpm (RedHat family)",
        "      ansible.builtin.get_url:",
        "        url: \"https://github.com/openbao/openbao/releases/download/v{{ openbao_version }}/openbao_{{ openbao_version }}_linux_{{ openbao_arch }}.rpm\"",
        "        dest: \"/tmp/openbao_{{ openbao_version }}_linux_{{ openbao_arch }}.rpm\"",
        "        mode: '0644'",
        "      retries: 3",
        "      delay: 5",
        "      register: openbao_rpm_download",
        "      until: openbao_rpm_download is succeeded",
        "      when:",
        "        - ansible_os_family == 'RedHat'",
        "        - openbao_installed.rc != 0 or (openbao_version not in (openbao_installed.stdout | default(''))",
        "          )",
        "    - name: Install OpenBao .rpm (RedHat family)",
        "      ansible.builtin.dnf:",
        "        name: \"/tmp/openbao_{{ openbao_version }}_linux_{{ openbao_arch }}.rpm\"",
        "        state: present",
        "        disable_gpg_check: true",
        "      when:",
        "        - ansible_os_family == 'RedHat'",
        "        - openbao_installed.rc != 0 or (openbao_version not in (openbao_installed.stdout | default(''))",
        "          )",


        "    - name: Ensure openbao group exists",
        "      ansible.builtin.group:",
        "        name: openbao",
        "        system: true",
        "        state: present",

        "    - name: Ensure openbao user exists",
        "      ansible.builtin.user:",
        "        name: openbao",
        "        group: openbao",
        "        system: true",
        "        shell: /usr/sbin/nologin",
        "        home: /etc/openbao",
        "        create_home: false",

        "    - name: Ensure config & data directories",
        "      ansible.builtin.file:",
        "        path: \"{{ item }}\"",
        "        state: directory",
        "        owner: openbao",
        "        group: openbao",
        "        mode: '0750'",
        "      loop:",
        "        - /etc/openbao",
        "        - /etc/openbao/tls",
        "        - \"{{ openbao_data_dir }}\"",

        "    - name: Render /etc/openbao/openbao.hcl",
        "      ansible.builtin.copy:",
        "        dest: /etc/openbao/openbao.hcl",
        "        owner: openbao",
        "        group: openbao",
        "        mode: '0640'",
        "        content: |",
        hcl_indented.rstrip(),
        "      notify: Restart openbao",

        "    - name: Install systemd unit for OpenBao",
        "      ansible.builtin.copy:",
        "        dest: /etc/systemd/system/openbao.service",
        "        owner: root",
        "        group: root",
        "        mode: '0644'",
        "        content: |",
        "          [Unit]",
        "          Description=OpenBao",
        "          Documentation=https://openbao.org/docs/",
        "          Requires=network-online.target",
        "          After=network-online.target",
        "          ConditionFileNotEmpty=/etc/openbao/openbao.hcl",
        "",
        "          [Service]",
        "          Type=notify",
        "          User=openbao",
        "          Group=openbao",
        "          ProtectSystem=full",
        "          ProtectHome=read-only",
        "          PrivateTmp=yes",
        "          PrivateDevices=yes",
        "          SecureBits=keep-caps",
        "          AmbientCapabilities=CAP_IPC_LOCK",
        "          CapabilityBoundingSet=CAP_SYSLOG CAP_IPC_LOCK",
        "          NoNewPrivileges=yes",
        "          ExecStart=/usr/bin/bao server -config=/etc/openbao/openbao.hcl -log-level={{ openbao_log_level }}",
        "          ExecReload=/bin/kill --signal HUP $MAINPID",
        "          KillMode=process",
        "          KillSignal=SIGINT",
        "          Restart=on-failure",
        "          RestartSec=5",
        "          TimeoutStopSec=30",
        "          LimitNOFILE=65536",
        "          LimitMEMLOCK=infinity",
        "",
        "          [Install]",
        "          WantedBy=multi-user.target",
        "      notify:",
        "        - Reload systemd",
        "        - Restart openbao",

        "    - name: Enable and start openbao",
        "      ansible.builtin.systemd:",
        "        name: openbao",
        "        enabled: true",
        "        state: started",
        "        daemon_reload: true",

        "    - name: Ensure /opt/openbao/cluster-info exists",
        "      ansible.builtin.file:",
        "        path: /opt/openbao/cluster-info",
        "        state: directory",
        "        owner: openbao",
        "        group: openbao",
        "        mode: '0750'",

        "    - name: Compute leader API address",
        "      ansible.builtin.set_fact:",
        "        openbao_leader_api_addr: \"{{ openbao_scheme }}://{{ hostvars[ansible_play_hosts_all[0]].ansible_host | default(ansible_play_hosts_all[0]) }}:{{ openbao_port }}\"",

        "    - name: Write leader-info.txt on every node (always)",
        "      ansible.builtin.copy:",
        "        dest: /opt/openbao/cluster-info/leader-info.txt",
        "        owner: openbao",
        "        group: openbao",
        "        mode: '0640'",
        "        content: |",
        "          # OpenBao cluster join info",
        "          Leader API Address : {{ openbao_leader_api_addr }}",
        "          This node API addr : {{ openbao_scheme }}://{{ ansible_host | default(inventory_hostname) }}:{{ openbao_port }}",
        "          Cluster peers      : {{ ansible_play_hosts_all | join(', ') }}",
        "          TLS enabled        : {{ 'no (tls_disable=true)' if (openbao_scheme == 'http') else 'yes' }}",
        "          Note: with retry_join configured in openbao.hcl, follower nodes auto-join at boot;",
        "          the UI Raft join form is only needed for out-of-band joins.",
    ]

    if auto_init:
        parts += [
            "    - name: Wait for OpenBao API on this node",
            "      ansible.builtin.wait_for:",
            "        host: 127.0.0.1",
            "        port: \"{{ openbao_port }}\"",
            "        timeout: 120",

            "    - name: Check OpenBao init status (leader)",
            "      ansible.builtin.command: bao status -format=json",
            "      environment:",
            "        BAO_ADDR: \"{{ openbao_scheme }}://127.0.0.1:{{ openbao_port }}\"",
            "      register: bao_status_leader",
            "      failed_when: false",
            "      changed_when: false",
            "      run_once: true",

            "    - name: Initialize OpenBao (first node only)",
            "      ansible.builtin.command: >",
            "        bao operator init -key-shares=5 -key-threshold=3 -format=json",
            "      environment:",
            "        BAO_ADDR: \"{{ openbao_scheme }}://127.0.0.1:{{ openbao_port }}\"",
            "      register: bao_init",
            "      run_once: true",
            "      when: (bao_status_leader.stdout | default('{}') | from_json).initialized | default(false) == false",

            "    - name: Persist init output to /etc/openbao/init.json (leader)",
            "      ansible.builtin.copy:",
            "        dest: /etc/openbao/init.json",
            "        content: \"{{ bao_init.stdout }}\"",
            "        owner: openbao",
            "        group: openbao",
            "        mode: '0600'",
            "      run_once: true",
            "      when: bao_init is defined and bao_init.stdout is defined and (bao_init.stdout | length) > 0",

            "    - name: Share unseal keys and root token across the play",
            "      ansible.builtin.set_fact:",
            "        bao_unseal_keys: \"{{ (bao_init.stdout | from_json).unseal_keys_b64 }}\"",
            "        bao_root_token: \"{{ (bao_init.stdout | from_json).root_token }}\"",
            "      run_once: true",
            "      when: bao_init is defined and bao_init.stdout is defined and (bao_init.stdout | length) > 0",

            "    - name: Give raft followers time to retry_join the leader",
            "      ansible.builtin.pause:",
            "        seconds: 15",
            "      when: hostvars[ansible_play_hosts_all[0]].bao_unseal_keys is defined",

            "    - name: Wait for OpenBao API on every node (post-init)",
            "      ansible.builtin.wait_for:",
            "        host: 127.0.0.1",
            "        port: \"{{ openbao_port }}\"",
            "        timeout: 60",

            "    - name: Unseal every node with the shared keys (retries until sealed=false)",
            "      ansible.builtin.shell: |",
            "        set +e",
            "        export BAO_ADDR={{ openbao_scheme }}://127.0.0.1:{{ openbao_port }}",
            "        for k in {{ (hostvars[ansible_play_hosts_all[0]].bao_unseal_keys | default([])) | join(' ') }}; do",
            "          bao operator unseal \"$k\" >/dev/null 2>&1 || true",
            "        done",
            "        bao status -format=json 2>/dev/null | grep -q '\"sealed\": false'",
            "      register: bao_unseal_result",
            "      retries: 12",
            "      delay: 5",
            "      until: bao_unseal_result.rc == 0",
            "      changed_when: false",
            "      when: hostvars[ansible_play_hosts_all[0]].bao_unseal_keys is defined",


            "    - name: Write full cluster-info bundle to /opt/openbao/cluster-info on every node",
            "      ansible.builtin.copy:",
            "        dest: /opt/openbao/cluster-info/cluster-info.txt",
            "        owner: openbao",
            "        group: openbao",
            "        mode: '0600'",
            "        content: |",
            "          # OpenBao cluster bootstrap bundle — STORE SECURELY, delete after distribution",
            "          Leader API Address : {{ openbao_leader_api_addr }}",
            "          Root Token         : {{ hostvars[ansible_play_hosts_all[0]].bao_root_token | default('(already initialized before)') }}",
            "          Unseal Keys (b64)  :",
            "          {% for k in (hostvars[ansible_play_hosts_all[0]].bao_unseal_keys | default([])) %}",
            "            - {{ k }}",
            "          {% endfor %}",
            "          Full init JSON     : /etc/openbao/init.json (leader, mode 0600)",
            "      when: hostvars[ansible_play_hosts_all[0]].bao_unseal_keys is defined",

            "    - name: Stage TLS bundle under /opt/openbao/cluster-info/tls (if TLS enabled)",
            "      ansible.builtin.shell: |",
            "        set -e",
            "        mkdir -p /opt/openbao/cluster-info/tls",
            "        for f in \"{{ openbao_tls_ca | default('/etc/openbao/tls/ca.crt') }}\" \"{{ openbao_tls_cert | default('/etc/openbao/tls/tls.crt') }}\" \"{{ openbao_tls_key | default('/etc/openbao/tls/tls.key') }}\"; do",
            "          if [ -f \"$f\" ]; then cp -f \"$f\" /opt/openbao/cluster-info/tls/; fi",
            "        done",
            "        chown -R openbao:openbao /opt/openbao/cluster-info/tls",
            "        chmod 600 /opt/openbao/cluster-info/tls/* 2>/dev/null || true",
            "      when: openbao_scheme == 'https'",
            "      changed_when: false",

            # -------- Persistent auto-unseal (runs on every boot/restart) --------
            "    - name: Persist unseal keys on every node for auto-unseal (root:root 0600)",
            "      ansible.builtin.copy:",
            "        dest: /etc/openbao/unseal.keys",
            "        owner: root",
            "        group: root",
            "        mode: '0600'",
            "        content: |",
            "          {% for k in (hostvars[ansible_play_hosts_all[0]].bao_unseal_keys | default([])) %}",
            "          {{ k }}",
            "          {% endfor %}",
            "      when: hostvars[ansible_play_hosts_all[0]].bao_unseal_keys is defined",

            "    - name: Install auto-unseal helper script",
            "      ansible.builtin.copy:",
            "        dest: /usr/local/sbin/openbao-autounseal.sh",
            "        owner: root",
            "        group: root",
            "        mode: '0750'",
            "        content: |",
            "          #!/usr/bin/env bash",
            "          # Persistent auto-unseal for OpenBao (Shamir seal).",
            "          # Reads unseal keys from /etc/openbao/unseal.keys and submits them",
            "          # to the local API until the node reports sealed=false.",
            "          set -u",
            "          export BAO_ADDR=\"{{ openbao_scheme }}://127.0.0.1:{{ openbao_port }}\"",
            "          KEYS_FILE=/etc/openbao/unseal.keys",
            "          [ -r \"$KEYS_FILE\" ] || { echo \"no keys file, nothing to do\"; exit 0; }",
            "          # Wait for API",
            "          for i in $(seq 1 60); do",
            "            if bao status -format=json >/tmp/bao-status.json 2>/dev/null; then break; fi",
            "            sleep 2",
            "          done",
            "          # If not initialized yet (fresh follower waiting for retry_join), exit clean",
            "          if ! grep -q '\"initialized\": true' /tmp/bao-status.json 2>/dev/null; then",
            "            echo \"node not initialized yet; skipping\"; exit 0",
            "          fi",
            "          # Try unsealing until sealed=false",
            "          for attempt in $(seq 1 30); do",
            "            if bao status -format=json 2>/dev/null | grep -q '\"sealed\": false'; then",
            "              echo \"already unsealed\"; exit 0",
            "            fi",
            "            while IFS= read -r k; do",
            "              [ -z \"$k\" ] && continue",
            "              bao operator unseal \"$k\" >/dev/null 2>&1 || true",
            "            done < \"$KEYS_FILE\"",
            "            if bao status -format=json 2>/dev/null | grep -q '\"sealed\": false'; then",
            "              echo \"unsealed on attempt $attempt\"; exit 0",
            "            fi",
            "            sleep 3",
            "          done",
            "          echo \"failed to unseal after retries\" >&2",
            "          exit 1",

            "    - name: Install openbao-autounseal.service",
            "      ansible.builtin.copy:",
            "        dest: /etc/systemd/system/openbao-autounseal.service",
            "        owner: root",
            "        group: root",
            "        mode: '0644'",
            "        content: |",
            "          [Unit]",
            "          Description=OpenBao auto-unseal helper",
            "          After=openbao.service network-online.target",
            "          Wants=openbao.service network-online.target",
            "          PartOf=openbao.service",
            "          [Service]",
            "          Type=oneshot",
            "          RemainAfterExit=yes",
            "          ExecStart=/usr/local/sbin/openbao-autounseal.sh",
            "          # Retry a couple of times if API is slow to come up",
            "          Restart=on-failure",
            "          RestartSec=10s",
            "          [Install]",
            "          WantedBy=multi-user.target",
            "      register: bao_autounseal_unit",

            "    - name: Install openbao-autounseal.timer (safety net every 2min)",
            "      ansible.builtin.copy:",
            "        dest: /etc/systemd/system/openbao-autounseal.timer",
            "        owner: root",
            "        group: root",
            "        mode: '0644'",
            "        content: |",
            "          [Unit]",
            "          Description=Periodically ensure OpenBao is unsealed",
            "          [Timer]",
            "          OnBootSec=30s",
            "          OnUnitActiveSec=2min",
            "          Unit=openbao-autounseal.service",
            "          [Install]",
            "          WantedBy=timers.target",

            "    - name: Reload systemd (auto-unseal units)",
            "      ansible.builtin.systemd:",
            "        daemon_reload: true",

            "    - name: Enable and start openbao-autounseal.service",
            "      ansible.builtin.systemd:",
            "        name: openbao-autounseal.service",
            "        enabled: true",
            "        state: started",
            "      failed_when: false",

            "    - name: Enable and start openbao-autounseal.timer",
            "      ansible.builtin.systemd:",
            "        name: openbao-autounseal.timer",
            "        enabled: true",
            "        state: started",

            "    - name: Root token (STORE SECURELY, first run only)",
            "      ansible.builtin.debug:",
            "        msg:",
            "          - \"Leader API Address: {{ openbao_leader_api_addr }}\"",
            "          - \"Root token: {{ hostvars[ansible_play_hosts_all[0]].bao_root_token | default('(already initialized on a previous run)') }}\"",
            "          - \"Bundle on every node : /opt/openbao/cluster-info/  (cluster-info.txt, leader-info.txt, tls/ if https)\"",
            "          - \"Full init JSON on leader: /etc/openbao/init.json (mode 0600).\"",
            "          - \"Auto-unseal        : enabled via openbao-autounseal.service + .timer (keys at /etc/openbao/unseal.keys, root:root 0600)\"",
            "      run_once: true",
        ]

    if bool(values.get("health_http_enabled", True)):
        _hport = int(values.get("health_http_port") or 8280)
        parts += _vault_health_tasks(
            cluster_id=str(cluster_id),
            product="OpenBao",
            script_path="/usr/local/bin/opensible-openbao-health.py",
            service_name="opensible-openbao-health.service",
            unit_after="openbao.service",
            http_port=_hport,
            api_port=int(port),
            scheme=scheme,
        )



    parts += [
        "  handlers:",
        "    - name: Reload systemd",
        "      ansible.builtin.systemd:",
        "        daemon_reload: true",
        "    - name: Restart openbao",
        "      ansible.builtin.systemd:",
        "        name: openbao",
        "        state: restarted",
        "",
    ]
    return "\n".join(parts)
