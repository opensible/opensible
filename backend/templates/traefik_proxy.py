"""Template: Traefik Reverse Proxy / Load Balancer.

Two deployment modes:
  - single      one Traefik instance on the selected host(s)
  - cluster     same static config on every selected host, optional
                keepalived VRRP virtual IP for HA (2+ nodes)

Renders:
  /etc/traefik/traefik.yml           static configuration
  /etc/traefik/dynamic/default.yml   example dynamic file provider entry
  /etc/systemd/system/traefik.service

Marker: 2026-07-traefik-v1
"""
from __future__ import annotations

import hashlib
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
    "id": "traefik-proxy",
    "name": "Traefik Reverse Proxy / LB",
    "category": "Networking",
    "icon": "network",
    "description": (
        "Traefik v3 reverse proxy / load balancer installed from the official "
        "GitHub release tarball and managed by systemd. Ships a hardened "
        "static config, a file provider directory for dynamic routes, a "
        "protected dashboard and optional Let's Encrypt ACME. Deploy as a "
        "single instance or as an HA cluster with keepalived VRRP VIP."
    ),
    "tags": ["traefik", "reverse-proxy", "load-balancer", "systemd", "ha"],
    "variables": [
        # ---------- Identity + deploy mode ----------
        {"name": "cluster_id", "label": "Deployment name",
         "type": "string", "default": "opensible-traefik",
         "help": "Used for filenames and comments in the rendered config."},
        {"name": "deploy_mode", "label": "Deployment mode",
         "type": "select", "default": "single",
         "options": [
             {"label": "Single instance", "value": "single"},
             {"label": "HA / Cluster (keepalived VRRP VIP)", "value": "cluster"},
         ],
         "help": "Single = one node. Cluster = install on every selected host and (optionally) publish a VRRP VIP."},
        {"name": "traefik_version", "label": "Traefik version",
         "type": "string", "default": "3.2.1",
         "help": "Any tag published on github.com/traefik/traefik/releases (without the leading 'v')."},

        # ---------- Entrypoints ----------
        {"name": "http_port", "label": "HTTP entrypoint port (web)",
         "type": "number", "default": 80},
        {"name": "https_enabled", "label": "Enable HTTPS entrypoint (websecure)",
         "type": "boolean", "default": True},
        {"name": "https_port", "label": "HTTPS entrypoint port",
         "type": "number", "default": 443},
        {"name": "http_to_https_redirect", "label": "Redirect HTTP → HTTPS",
         "type": "boolean", "default": True},

        # ---------- Dashboard ----------
        {"name": "dashboard_enabled", "label": "Enable dashboard",
         "type": "boolean", "default": True},
        {"name": "dashboard_port", "label": "Dashboard / API port",
         "type": "number", "default": 8080},
        {"name": "dashboard_user", "label": "Dashboard basic-auth user",
         "type": "string", "default": "admin"},
        {"name": "dashboard_password", "label": "Dashboard basic-auth password",
         "type": "password", "required": False, "default": "",
         "help": "Leave blank to auto-derive a stable password from the deployment name (printed at end of run)."},

        # ---------- Providers ----------
        {"name": "docker_provider", "label": "Enable Docker provider",
         "type": "boolean", "default": False,
         "help": "Requires Docker on the target host. Traefik will watch containers with `traefik.enable=true` labels."},
        {"name": "docker_expose_by_default", "label": "Docker exposedByDefault",
         "type": "boolean", "default": False},
        {"name": "file_provider_dir", "label": "File provider directory",
         "type": "string", "default": "/etc/traefik/dynamic",
         "help": "Traefik will watch this directory for dynamic YAML/TOML configuration."},

        # ---------- Let's Encrypt (ACME) ----------
        {"name": "acme_enabled", "label": "Enable Let's Encrypt (ACME HTTP-01)",
         "type": "boolean", "default": False,
         "help": "Automatic TLS certificates via HTTP-01 challenge on the web entrypoint (port 80 must be reachable from the internet)."},
        {"name": "acme_email", "label": "ACME contact email",
         "type": "string", "default": ""},
        {"name": "acme_staging", "label": "Use ACME staging (avoid rate limits)",
         "type": "boolean", "default": False},

        # ---------- Access log / metrics ----------
        {"name": "access_log_enabled", "label": "Enable access log",
         "type": "boolean", "default": True},
        {"name": "metrics_enabled", "label": "Expose Prometheus metrics on dashboard entrypoint",
         "type": "boolean", "default": True},

        # ---------- Cluster / VRRP ----------
        {"name": "keepalived_enabled", "label": "Install keepalived (VRRP VIP for HA)",
         "type": "boolean", "default": False,
         "help": "Only used when deployment mode = Cluster. First host becomes MASTER, the rest BACKUP."},
        {"name": "vrrp_vip", "label": "Virtual IP (CIDR)",
         "type": "string", "default": "",
         "placeholder": "10.0.0.100/24"},
        {"name": "vrrp_interface", "label": "Network interface for VRRP",
         "type": "string", "default": "eth0"},
        {"name": "vrrp_router_id", "label": "VRRP router id (1-255)",
         "type": "number", "default": 52},
        {"name": "vrrp_password", "label": "VRRP auth password",
         "type": "password", "required": False, "default": "",
         "help": "Leave blank to derive a stable password from the deployment name."},

        # ---------- Ops ----------
        {"name": "open_firewall", "label": "Open ports in UFW/firewalld",
         "type": "boolean", "default": True},
        {"name": "become", "label": "Run as sudo (become)",
         "type": "boolean", "default": True},
        VAULT_FILES_VARIABLE,
    ],
}


def suggested_filename(values: Dict[str, Any]) -> str:
    stem = slugify(values.get("cluster_id"), "traefik")
    return f"{stem}-traefik.yml"


def _derive_secret(prefix: str, seed: str) -> str:
    return hashlib.sha256(f"{prefix}::{seed}".encode("utf-8")).hexdigest()[:20]


def _htpasswd_bcrypt_expr(user: str, password: str) -> str:
    """Return a Jinja expression producing a bcrypt htpasswd line at runtime.

    We generate the hash on the target host with `openssl passwd -apr1` (widely
    available) rather than shipping a hash from the controller. The rendered
    value is written into /etc/traefik/dynamic/dashboard-auth.yml.
    """
    return f"{user}:{{{{ _traefik_dashboard_hash.stdout }}}}"


def render(values: Dict[str, Any], targets: Dict[str, Any]) -> str:
    cluster_id = str(values.get("cluster_id") or "opensible-traefik").strip() or "opensible-traefik"
    deploy_mode = str(values.get("deploy_mode") or "single").lower()
    if deploy_mode not in ("single", "cluster"):
        deploy_mode = "single"
    version = str(values.get("traefik_version") or "3.2.1").strip().lstrip("v") or "3.2.1"

    try:
        http_port = int(values.get("http_port") or 80)
    except Exception:
        http_port = 80
    https_enabled = bool(values.get("https_enabled", True))
    try:
        https_port = int(values.get("https_port") or 443)
    except Exception:
        https_port = 443
    http_to_https = bool(values.get("http_to_https_redirect", True)) and https_enabled

    dashboard_enabled = bool(values.get("dashboard_enabled", True))
    try:
        dashboard_port = int(values.get("dashboard_port") or 8080)
    except Exception:
        dashboard_port = 8080
    dashboard_user = str(values.get("dashboard_user") or "admin").strip() or "admin"
    dashboard_password = str(values.get("dashboard_password") or "").strip() \
        or _derive_secret("traefik-dash", cluster_id)

    docker_provider = bool(values.get("docker_provider", False))
    docker_exposed = bool(values.get("docker_expose_by_default", False))
    file_provider_dir = str(values.get("file_provider_dir") or "/etc/traefik/dynamic").strip() \
        or "/etc/traefik/dynamic"

    acme_enabled = bool(values.get("acme_enabled", False)) and https_enabled
    acme_email = str(values.get("acme_email") or "").strip()
    acme_staging = bool(values.get("acme_staging", False))
    acme_ca = (
        "https://acme-staging-v02.api.letsencrypt.org/directory"
        if acme_staging
        else "https://acme-v02.api.letsencrypt.org/directory"
    )

    access_log_enabled = bool(values.get("access_log_enabled", True))
    metrics_enabled = bool(values.get("metrics_enabled", True))

    keepalived_enabled = bool(values.get("keepalived_enabled", False)) and deploy_mode == "cluster"
    vrrp_vip = str(values.get("vrrp_vip") or "").strip()
    vrrp_iface = str(values.get("vrrp_interface") or "eth0").strip() or "eth0"
    try:
        vrrp_router_id = int(values.get("vrrp_router_id") or 52)
    except Exception:
        vrrp_router_id = 52
    vrrp_password = str(values.get("vrrp_password") or "").strip() \
        or _derive_secret("traefik-vrrp", cluster_id)[:8]

    open_firewall = bool(values.get("open_firewall", True))
    become = "true" if values.get("become", True) else "false"
    hosts = render_hosts(targets)

    # -------------------------------------------------------------- #
    # Render traefik.yml (static)
    # -------------------------------------------------------------- #
    static_lines: List[str] = [
        f"# Managed by OpenSible — deployment: {cluster_id} ({deploy_mode})",
        "global:",
        "  checkNewVersion: false",
        "  sendAnonymousUsage: false",
        "",
        "log:",
        "  level: INFO",
        "  filePath: /var/log/traefik/traefik.log",
        "",
    ]
    if access_log_enabled:
        static_lines += [
            "accessLog:",
            "  filePath: /var/log/traefik/access.log",
            "  bufferingSize: 100",
            "",
        ]

    # entryPoints
    static_lines += ["entryPoints:", "  web:", f"    address: \":{http_port}\""]
    if http_to_https:
        static_lines += [
            "    http:",
            "      redirections:",
            "        entryPoint:",
            "          to: websecure",
            "          scheme: https",
            "          permanent: true",
        ]
    if https_enabled:
        static_lines += ["  websecure:", f"    address: \":{https_port}\""]
        if acme_enabled:
            static_lines += [
                "    http:",
                "      tls:",
                "        certResolver: le",
            ]
    if dashboard_enabled or metrics_enabled:
        static_lines += ["  traefik:", f"    address: \":{dashboard_port}\""]
    static_lines.append("")

    # api / dashboard
    if dashboard_enabled:
        static_lines += [
            "api:",
            "  dashboard: true",
            "  insecure: false",
            "",
        ]

    # metrics
    if metrics_enabled:
        static_lines += [
            "metrics:",
            "  prometheus:",
            "    entryPoint: traefik",
            "    addEntryPointsLabels: true",
            "    addServicesLabels: true",
            "",
        ]

    # providers
    static_lines += [
        "providers:",
        "  file:",
        f"    directory: {file_provider_dir}",
        "    watch: true",
    ]
    if docker_provider:
        static_lines += [
            "  docker:",
            "    endpoint: \"unix:///var/run/docker.sock\"",
            f"    exposedByDefault: {'true' if docker_exposed else 'false'}",
            "    watch: true",
        ]
    static_lines.append("")

    # certificates resolvers
    if acme_enabled:
        static_lines += [
            "certificatesResolvers:",
            "  le:",
            "    acme:",
            f"      email: {acme_email or 'admin@example.com'}",
            "      storage: /etc/traefik/acme.json",
            f"      caServer: {acme_ca}",
            "      httpChallenge:",
            "        entryPoint: web",
            "",
        ]

    traefik_yml = "\n".join(static_lines) + "\n"

    # -------------------------------------------------------------- #
    # Render dynamic dashboard-auth.yml (basic auth + host rule)
    # -------------------------------------------------------------- #
    dashboard_dynamic = ""
    if dashboard_enabled:
        dashboard_dynamic = "\n".join([
            "# Managed by OpenSible — Traefik dashboard middleware + router",
            "http:",
            "  middlewares:",
            "    dashboard-auth:",
            "      basicAuth:",
            "        users:",
            f"          - \"{_htpasswd_bcrypt_expr(dashboard_user, dashboard_password)}\"",
            "  routers:",
            "    dashboard:",
            "      rule: \"PathPrefix(`/api`) || PathPrefix(`/dashboard`)\"",
            "      entryPoints:",
            "        - traefik",
            "      service: api@internal",
            "      middlewares:",
            "        - dashboard-auth",
            "",
        ])

    # -------------------------------------------------------------- #
    # Systemd unit
    # -------------------------------------------------------------- #
    systemd_unit = "\n".join([
        "[Unit]",
        "Description=Traefik reverse proxy / load balancer",
        "Documentation=https://doc.traefik.io/traefik/",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=notify",
        "User=traefik",
        "Group=traefik",
        "ExecStart=/usr/local/bin/traefik --configFile=/etc/traefik/traefik.yml",
        "Restart=on-failure",
        "RestartSec=5s",
        "LimitNOFILE=1048576",
        "AmbientCapabilities=CAP_NET_BIND_SERVICE",
        "CapabilityBoundingSet=CAP_NET_BIND_SERVICE",
        "NoNewPrivileges=true",
        "ProtectSystem=full",
        "ProtectHome=true",
        "ReadWritePaths=/etc/traefik /var/log/traefik /var/lib/traefik",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ])

    # -------------------------------------------------------------- #
    # Ansible plays
    # -------------------------------------------------------------- #
    parts: List[str] = ["---"]
    parts.append("# OpenSible traefik-proxy template generation: 2026-07-traefik-v1")
    parts.append(f"# Rendered from template: {TEMPLATE['name']}")
    parts.append(f"# Deployment: {cluster_id} | mode: {deploy_mode} | version: {version}")
    parts.append("")

    firewall_ports: List[int] = [http_port]
    if https_enabled:
        firewall_ports.append(https_port)
    if dashboard_enabled or metrics_enabled:
        firewall_ports.append(dashboard_port)

    parts += [
        "- name: Deploy Traefik reverse proxy / load balancer",
        f"  hosts: {hosts}",
        f"  become: {become}",
        "  gather_facts: true",
        "  any_errors_fatal: false",
        *vars_files_lines(parse_vault_files(values.get("vault_files"))),
        "  vars:",
        f"    tr_cluster_id: {yaml_str(cluster_id)}",
        f"    tr_version: {yaml_str(version)}",
        f"    tr_deploy_mode: {yaml_str(deploy_mode)}",
        f"    tr_http_port: {http_port}",
        f"    tr_https_port: {https_port}",
        f"    tr_dashboard_port: {dashboard_port}",
        f"    tr_dashboard_user: {yaml_str(dashboard_user)}",
        f"    tr_dashboard_password: {yaml_str(dashboard_password)}",
        f"    tr_open_firewall: {'true' if open_firewall else 'false'}",
        "  tasks:",

        # ---- prerequisites ----
        "    - name: Refresh apt package metadata (Debian/Ubuntu)",
        "      when: ansible_os_family == 'Debian'",
        "      ansible.builtin.apt:",
        "        update_cache: true",
        "        cache_valid_time: 300",
        "      failed_when: false",
        "    - name: Install prerequisite packages (Debian/Ubuntu)",
        "      when: ansible_os_family == 'Debian'",
        "      ansible.builtin.apt:",
        "        name: [curl, ca-certificates, tar, openssl, apache2-utils]",
        "        state: present",
        "        install_recommends: false",
        "    - name: Install prerequisite packages (RHEL/Rocky/Alma)",
        "      when: ansible_os_family == 'RedHat'",
        "      ansible.builtin.dnf:",
        "        name: [curl, ca-certificates, tar, openssl, httpd-tools]",
        "        state: present",

        # ---- user + directories ----
        "    - name: Ensure traefik group exists",
        "      ansible.builtin.group:",
        "        name: traefik",
        "        system: true",
        "        state: present",
        "    - name: Ensure traefik system user exists",
        "      ansible.builtin.user:",
        "        name: traefik",
        "        group: traefik",
        "        system: true",
        "        shell: /usr/sbin/nologin",
        "        home: /var/lib/traefik",
        "        create_home: true",
        "        state: present",
        "    - name: Ensure traefik directories exist",
        "      loop:",
        "        - /etc/traefik",
        f"        - {file_provider_dir}",
        "        - /var/lib/traefik",
        "        - /var/log/traefik",
        "      ansible.builtin.file:",
        "        path: \"{{ item }}\"",
        "        state: directory",
        "        owner: traefik",
        "        group: traefik",
        "        mode: '0750'",

        # ---- detect architecture + download ----
        "    - name: Detect Traefik download architecture",
        "      ansible.builtin.set_fact:",
        "        _tr_arch: >-",
        "          {{ 'arm64' if ansible_architecture in ['aarch64','arm64']",
        "             else ('armv7' if ansible_architecture.startswith('armv7')",
        "             else 'amd64') }}",
        "    - name: Check installed Traefik version",
        "      ansible.builtin.command: /usr/local/bin/traefik version",
        "      register: _tr_installed",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Download Traefik release tarball",
        "      when: _tr_installed.rc != 0 or (tr_version not in (_tr_installed.stdout | default('')))",
        "      ansible.builtin.get_url:",
        f"        url: \"https://github.com/traefik/traefik/releases/download/v{{{{ tr_version }}}}/traefik_v{{{{ tr_version }}}}_linux_{{{{ _tr_arch }}}}.tar.gz\"",
        "        dest: \"/tmp/traefik_{{ tr_version }}_{{ _tr_arch }}.tar.gz\"",
        "        mode: '0644'",
        "        timeout: 60",
        "      register: _tr_dl",
        "      retries: 3",
        "      delay: 5",
        "      until: _tr_dl is succeeded",
        "    - name: Unpack Traefik binary to /usr/local/bin",
        "      when: _tr_installed.rc != 0 or (tr_version not in (_tr_installed.stdout | default('')))",
        "      ansible.builtin.unarchive:",
        "        src: \"/tmp/traefik_{{ tr_version }}_{{ _tr_arch }}.tar.gz\"",
        "        dest: /usr/local/bin",
        "        remote_src: true",
        "        include: [traefik]",
        "        mode: '0755'",
        "        owner: root",
        "        group: root",
        "      notify: Restart traefik",

        # ---- ACME storage (must be 0600) ----
    ]
    if acme_enabled:
        parts += [
            "    - name: Ensure /etc/traefik/acme.json exists with strict perms",
            "      ansible.builtin.file:",
            "        path: /etc/traefik/acme.json",
            "        state: touch",
            "        owner: traefik",
            "        group: traefik",
            "        mode: '0600'",
            "      changed_when: false",
        ]

    # ---- generate bcrypt hash on the target host ----
    if dashboard_enabled:
        parts += [
            "    - name: Generate bcrypt hash for Traefik dashboard user",
            "      ansible.builtin.command: >-",
            "        htpasswd -nbB -C 10 {{ tr_dashboard_user }} {{ tr_dashboard_password }}",
            "      register: _traefik_dashboard_htpasswd",
            "      changed_when: false",
            "      no_log: true",
            "    - name: Extract bcrypt digest (strip user: prefix)",
            "      ansible.builtin.set_fact:",
            "        _traefik_dashboard_hash:",
            "          stdout: \"{{ (_traefik_dashboard_htpasswd.stdout.split(':',1)[1]) | replace('$','$$') }}\"",
            "      no_log: true",
        ]

    parts += [
        # ---- write static config ----
        "    - name: Write /etc/traefik/traefik.yml (static config)",
        "      ansible.builtin.copy:",
        "        dest: /etc/traefik/traefik.yml",
        "        owner: traefik",
        "        group: traefik",
        "        mode: '0640'",
        "        content: |",
        indent_block(traefik_yml.rstrip("\n"), "          "),
        "      notify: Restart traefik",
    ]

    if dashboard_enabled:
        parts += [
            f"    - name: Write {file_provider_dir}/dashboard-auth.yml (dynamic)",
            "      ansible.builtin.copy:",
            f"        dest: {file_provider_dir}/dashboard-auth.yml",
            "        owner: traefik",
            "        group: traefik",
            "        mode: '0640'",
            "        content: |",
            indent_block(dashboard_dynamic.rstrip("\n"), "          "),
        ]

    parts += [
        # ---- systemd unit ----
        "    - name: Install traefik systemd unit",
        "      ansible.builtin.copy:",
        "        dest: /etc/systemd/system/traefik.service",
        "        owner: root",
        "        group: root",
        "        mode: '0644'",
        "        content: |",
        indent_block(systemd_unit.rstrip("\n"), "          "),
        "      notify: Restart traefik",
        "    - name: Enable + start traefik",
        "      ansible.builtin.systemd:",
        "        name: traefik",
        "        state: started",
        "        enabled: true",
        "        daemon_reload: true",

        # ---- firewall ----
        "    - name: Open Traefik ports (ufw)",
        "      when: tr_open_firewall and ansible_os_family == 'Debian'",
        "      ansible.builtin.shell: |",
        "        set +e",
        "        command -v ufw >/dev/null 2>&1 || exit 0",
        "        ufw status 2>/dev/null | grep -q 'Status: active' || exit 0",
        *[f"        ufw allow {p}/tcp 2>/dev/null || true" for p in firewall_ports],
        "        exit 0",
        "      changed_when: false",
        "    - name: Open Traefik ports (firewalld)",
        "      when: tr_open_firewall and ansible_os_family == 'RedHat'",
        "      ansible.builtin.shell: |",
        "        set +e",
        "        command -v firewall-cmd >/dev/null 2>&1 || exit 0",
        "        systemctl is-active --quiet firewalld || exit 0",
        *[f"        firewall-cmd --permanent --add-port={p}/tcp 2>/dev/null || true" for p in firewall_ports],
        "        firewall-cmd --reload 2>/dev/null || true",
        "        exit 0",
        "      changed_when: false",

        # ---- summary ----
        "    - name: Traefik endpoint summary",
        "      run_once: true",
        "      ansible.builtin.debug:",
        "        msg: |",
        f"          Traefik deployed: {cluster_id} ({deploy_mode})",
        f"          HTTP:      http://<host>:{http_port}",
        (f"          HTTPS:     https://<host>:{https_port}" if https_enabled else "          HTTPS:     disabled"),
        (f"          Dashboard: http://<host>:{dashboard_port}/dashboard/  (user: {dashboard_user}  pass: {dashboard_password})" if dashboard_enabled else "          Dashboard: disabled"),
        (f"          Metrics:   http://<host>:{dashboard_port}/metrics" if metrics_enabled else "          Metrics:   disabled"),
        f"          Dynamic config directory: {file_provider_dir}",
        "  handlers:",
        "    - name: Restart traefik",
        "      ansible.builtin.systemd:",
        "        name: traefik",
        "        state: restarted",
        "        daemon_reload: true",
    ]

    # -------------------------------------------------------------- #
    # Optional keepalived play (VRRP VIP) — cluster mode only
    # -------------------------------------------------------------- #
    if keepalived_enabled and vrrp_vip:
        keepalived_cfg = (
            "global_defs {\n"
            f"    router_id {slugify(cluster_id, 'traefik').upper()}_{{{{ inventory_hostname }}}}\n"
            "    enable_script_security\n"
            "    script_user root\n"
            "}\n"
            "\n"
            "vrrp_script chk_traefik {\n"
            "    script \"/usr/bin/pgrep -x traefik\"\n"
            "    interval 2\n"
            "    weight 2\n"
            "    fall 2\n"
            "    rise 2\n"
            "}\n"
            "\n"
            "vrrp_instance VI_TRAEFIK {\n"
            "    state {{ 'MASTER' if inventory_hostname == ansible_play_hosts[0] else 'BACKUP' }}\n"
            f"    interface {vrrp_iface}\n"
            f"    virtual_router_id {vrrp_router_id}\n"
            "    priority {{ 150 if inventory_hostname == ansible_play_hosts[0] else 100 }}\n"
            "    advert_int 1\n"
            "    authentication {\n"
            "        auth_type PASS\n"
            f"        auth_pass {vrrp_password}\n"
            "    }\n"
            "    virtual_ipaddress {\n"
            f"        {vrrp_vip}\n"
            "    }\n"
            "    track_script {\n"
            "        chk_traefik\n"
            "    }\n"
            "}\n"
        )
        parts += [
            "",
            "- name: Deploy keepalived VRRP VIP for Traefik HA",
            f"  hosts: {hosts}",
            f"  become: {become}",
            "  gather_facts: true",
            "  tasks:",
            "    - name: Allow Traefik/keepalived to bind non-local IP (VIP)",
            "      ansible.posix.sysctl:",
            "        name: net.ipv4.ip_nonlocal_bind",
            "        value: '1'",
            "        state: present",
            "        sysctl_set: true",
            "        reload: true",
            "      failed_when: false",
            "    - name: Install keepalived (Debian/Ubuntu)",
            "      when: ansible_os_family == 'Debian'",
            "      ansible.builtin.apt:",
            "        name: keepalived",
            "        state: present",
            "        install_recommends: false",
            "        update_cache: true",
            "        cache_valid_time: 300",
            "    - name: Install keepalived (RHEL/Rocky/Alma)",
            "      when: ansible_os_family == 'RedHat'",
            "      ansible.builtin.dnf:",
            "        name: keepalived",
            "        state: present",
            "    - name: Write /etc/keepalived/keepalived.conf",
            "      ansible.builtin.copy:",
            "        dest: /etc/keepalived/keepalived.conf",
            "        owner: root",
            "        group: root",
            "        mode: '0640'",
            "        content: |",
            indent_block(keepalived_cfg.rstrip("\n"), "          "),
            "      register: _ka_cfg",
            "    - name: Enable + restart keepalived",
            "      ansible.builtin.systemd:",
            "        name: keepalived",
            "        state: restarted",
            "        enabled: true",
            "        daemon_reload: true",
            "      when: _ka_cfg is changed",
            "    - name: Keepalived VIP summary",
            "      run_once: true",
            "      ansible.builtin.debug:",
            "        msg: |",
            f"          VRRP VIP: {vrrp_vip} on {vrrp_iface} (router_id {vrrp_router_id})",
            "          MASTER: {{ ansible_play_hosts[0] }}   BACKUP: {{ ansible_play_hosts[1:] | join(', ') if ansible_play_hosts | length > 1 else '(none — add more hosts)' }}",
        ]

    parts.append("")
    return "\n".join(parts)
