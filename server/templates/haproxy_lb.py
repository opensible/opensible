"""Template: HAProxy load balancer (distro packages + systemd).

Deploys HAProxy on one or more hosts, renders a professional
/etc/haproxy/haproxy.cfg with sane defaults (timeouts, logging,
stats socket, hardened stats page) and a single frontend/backend
pair driven by form inputs.

Optional keepalived companion provides a VRRP virtual IP so a
2+ node HAProxy pair behaves as an HA endpoint.

Marker: 2026-07-haproxy-lb-v1
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
    "id": "haproxy-lb",
    "name": "HAProxy Load Balancer",
    "category": "Networking",
    "icon": "network",
    "description": (
        "HAProxy load balancer installed via distro packages and managed "
        "by systemd. Renders a hardened /etc/haproxy/haproxy.cfg with one "
        "frontend + backend pool, health checks, a protected stats page "
        "and — optionally — a keepalived VRRP virtual IP for HA."
    ),
    "tags": ["haproxy", "load-balancer", "systemd", "keepalived", "ha"],
    "variables": [
        # ---------- Identity ----------
        {"name": "cluster_id", "label": "Deployment name",
         "type": "string", "default": "opensible-haproxy",
         "help": "Used for filenames and comments in the rendered config."},

        # ---------- Listener ----------
        {"name": "mode", "label": "Proxy mode",
         "type": "select", "default": "http",
         "options": [
             {"label": "HTTP (layer 7)", "value": "http"},
             {"label": "TCP  (layer 4)", "value": "tcp"},
         ]},
        {"name": "frontend_bind", "label": "Frontend bind address",
         "type": "string", "default": "0.0.0.0",
         "help": "Interface HAProxy listens on. Use 0.0.0.0 to listen on all interfaces."},
        {"name": "frontend_port", "label": "Frontend port",
         "type": "number", "default": 80},
        {"name": "balance_algorithm", "label": "Balance algorithm",
         "type": "select", "default": "roundrobin",
         "options": [
             {"label": "roundrobin", "value": "roundrobin"},
             {"label": "leastconn", "value": "leastconn"},
             {"label": "source (client IP hash)", "value": "source"},
             {"label": "static-rr", "value": "static-rr"},
         ]},

        # ---------- Backends ----------
        {"name": "backends", "label": "Backend servers",
         "type": "nodes", "required": True,
         "help": "Each entry becomes a `server` line in the backend pool. `ip` is the upstream host/IP, `ssh_port` is reused as the upstream service port.",
         "default": [
             {"name": "app-1", "ip": "", "ssh_user": "", "ssh_port": 8080},
             {"name": "app-2", "ip": "", "ssh_user": "", "ssh_port": 8080},
         ]},
        {"name": "backend_check", "label": "Enable active health checks",
         "type": "boolean", "default": True},
        {"name": "http_check_path", "label": "HTTP health-check path",
         "type": "string", "default": "/",
         "help": "Only used in HTTP mode. Sends `OPTIONS <path> HTTP/1.0` to each server."},

        # ---------- TLS (optional) ----------
        {"name": "tls_enabled", "label": "Terminate TLS on the frontend",
         "type": "boolean", "default": False},
        {"name": "tls_bind_port", "label": "TLS bind port",
         "type": "number", "default": 443},
        {"name": "tls_cert_path", "label": "Combined PEM path on host (fullchain+key)",
         "type": "string", "default": "/etc/haproxy/certs/site.pem",
         "help": "HAProxy expects one file containing the certificate chain followed by the private key."},
        {"name": "tls_redirect_http", "label": "Redirect HTTP → HTTPS",
         "type": "boolean", "default": True},

        # ---------- Stats page ----------
        {"name": "stats_enabled", "label": "Enable stats page",
         "type": "boolean", "default": True},
        {"name": "stats_port", "label": "Stats port",
         "type": "number", "default": 8404},
        {"name": "stats_uri", "label": "Stats URI",
         "type": "string", "default": "/stats"},
        {"name": "stats_user", "label": "Stats username",
         "type": "string", "default": "admin"},
        {"name": "stats_password", "label": "Stats password",
         "type": "password", "required": False, "default": "",
         "help": "Leave blank to auto-derive from the deployment name (still printed at end of run)."},

        # ---------- Tuning ----------
        {"name": "maxconn", "label": "Global maxconn",
         "type": "number", "default": 20000},
        {"name": "timeout_client", "label": "timeout client",
         "type": "string", "default": "30s"},
        {"name": "timeout_server", "label": "timeout server",
         "type": "string", "default": "30s"},
        {"name": "timeout_connect", "label": "timeout connect",
         "type": "string", "default": "5s"},
        {"name": "extra_global", "label": "Extra `global` directives",
         "type": "code", "language": "haproxy", "rows": 3, "default": ""},
        {"name": "extra_defaults", "label": "Extra `defaults` directives",
         "type": "code", "language": "haproxy", "rows": 3, "default": ""},

        # ---------- Keepalived HA ----------
        {"name": "keepalived_enabled", "label": "Install keepalived (VRRP VIP for HA)",
         "type": "boolean", "default": False,
         "help": "Recommended with 2+ HAProxy nodes. The first host is MASTER, the rest are BACKUP."},
        {"name": "vrrp_vip", "label": "Virtual IP (CIDR)",
         "type": "string", "default": "",
         "placeholder": "10.0.0.100/24"},
        {"name": "vrrp_interface", "label": "Network interface for VRRP",
         "type": "string", "default": "eth0"},
        {"name": "vrrp_router_id", "label": "VRRP router id (1-255)",
         "type": "number", "default": 51},
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
    stem = slugify(values.get("cluster_id"), "haproxy")
    return f"{stem}-haproxy.yml"


def _norm_backends(raw: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for i, n in enumerate(raw):
        if not isinstance(n, dict):
            continue
        ip = str(n.get("ip") or "").strip()
        if not ip:
            continue
        name = str(n.get("name") or f"srv-{i+1}").strip() or f"srv-{i+1}"
        try:
            port = int(n.get("ssh_port") or n.get("port") or 8080)
        except Exception:
            port = 8080
        out.append({
            "name": slugify(name, f"srv-{i+1}"),
            "ip": ip,
            "port": port,
        })
    return out


def _derive_secret(prefix: str, seed: str) -> str:
    return hashlib.sha256(f"{prefix}::{seed}".encode("utf-8")).hexdigest()[:16]


def render(values: Dict[str, Any], targets: Dict[str, Any]) -> str:
    cluster_id = str(values.get("cluster_id") or "opensible-haproxy").strip() or "opensible-haproxy"
    mode = str(values.get("mode") or "http").lower()
    if mode not in ("http", "tcp"):
        mode = "http"
    frontend_bind = str(values.get("frontend_bind") or "0.0.0.0").strip() or "0.0.0.0"
    try:
        frontend_port = int(values.get("frontend_port") or 80)
    except Exception:
        frontend_port = 80
    balance_algorithm = str(values.get("balance_algorithm") or "roundrobin").strip() or "roundrobin"

    backends = _norm_backends(values.get("backends"))
    check = bool(values.get("backend_check", True))
    hc_path = str(values.get("http_check_path") or "/").strip() or "/"

    tls_enabled = bool(values.get("tls_enabled", False))
    try:
        tls_port = int(values.get("tls_bind_port") or 443)
    except Exception:
        tls_port = 443
    tls_cert = str(values.get("tls_cert_path") or "/etc/haproxy/certs/site.pem").strip()
    tls_redirect = bool(values.get("tls_redirect_http", True))

    stats_enabled = bool(values.get("stats_enabled", True))
    try:
        stats_port = int(values.get("stats_port") or 8404)
    except Exception:
        stats_port = 8404
    stats_uri = str(values.get("stats_uri") or "/stats").strip() or "/stats"
    stats_user = str(values.get("stats_user") or "admin").strip() or "admin"
    stats_password = str(values.get("stats_password") or "").strip() \
        or _derive_secret("haproxy-stats", cluster_id)

    try:
        maxconn = int(values.get("maxconn") or 20000)
    except Exception:
        maxconn = 20000
    to_client = str(values.get("timeout_client") or "30s").strip()
    to_server = str(values.get("timeout_server") or "30s").strip()
    to_connect = str(values.get("timeout_connect") or "5s").strip()
    extra_global = str(values.get("extra_global") or "").rstrip()
    extra_defaults = str(values.get("extra_defaults") or "").rstrip()

    keepalived_enabled = bool(values.get("keepalived_enabled", False))
    vrrp_vip = str(values.get("vrrp_vip") or "").strip()
    vrrp_iface = str(values.get("vrrp_interface") or "eth0").strip() or "eth0"
    try:
        vrrp_router_id = int(values.get("vrrp_router_id") or 51)
    except Exception:
        vrrp_router_id = 51
    vrrp_password = str(values.get("vrrp_password") or "").strip() \
        or _derive_secret("haproxy-vrrp", cluster_id)[:8]

    open_firewall = bool(values.get("open_firewall", True))
    become = "true" if values.get("become", True) else "false"
    hosts = render_hosts(targets)

    # -------------------------------------------------------------- #
    # Render haproxy.cfg
    # -------------------------------------------------------------- #
    global_lines = [
        "global",
        "    log /dev/log local0",
        "    log /dev/log local1 notice",
        "    chroot /var/lib/haproxy",
        f"    maxconn {maxconn}",
        "    user haproxy",
        "    group haproxy",
        "    daemon",
        "    stats socket /run/haproxy/admin.sock mode 660 level admin expose-fd listeners",
        "    stats timeout 30s",
        "    ssl-default-bind-ciphers ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256",
        "    ssl-default-bind-options no-sslv3 no-tlsv10 no-tlsv11 no-tls-tickets",
    ]
    if extra_global:
        global_lines += ["    " + ln for ln in extra_global.splitlines()]

    defaults_lines = [
        "",
        "defaults",
        f"    mode {mode}",
        "    log global",
        "    option dontlognull",
        "    retries 3",
        f"    timeout connect {to_connect}",
        f"    timeout client {to_client}",
        f"    timeout server {to_server}",
        "    timeout http-request 10s",
        "    timeout queue 30s",
    ]
    if mode == "http":
        defaults_lines += [
            "    option httplog",
            "    option forwardfor",
            "    option http-server-close",
        ]
    if extra_defaults:
        defaults_lines += ["    " + ln for ln in extra_defaults.splitlines()]

    backend_name = slugify(cluster_id, "app") + "_backend"
    frontend_name = slugify(cluster_id, "app") + "_frontend"

    frontend_lines = ["", f"frontend {frontend_name}"]
    if tls_enabled and mode == "http":
        frontend_lines.append(f"    bind {frontend_bind}:{frontend_port}")
        frontend_lines.append(f"    bind {frontend_bind}:{tls_port} ssl crt {tls_cert} alpn h2,http/1.1")
        if tls_redirect:
            frontend_lines.append("    http-request redirect scheme https unless { ssl_fc }")
    else:
        frontend_lines.append(f"    bind {frontend_bind}:{frontend_port}")
    frontend_lines.append(f"    default_backend {backend_name}")

    backend_lines = ["", f"backend {backend_name}", f"    balance {balance_algorithm}"]
    if mode == "http" and check:
        backend_lines.append(f"    option httpchk GET {hc_path}")
        backend_lines.append("    http-check expect status 200-399")
    for b in backends:
        server_opts = "check" if check else ""
        backend_lines.append(
            f"    server {b['name']} {b['ip']}:{b['port']} {server_opts}".rstrip()
        )
    if not backends:
        backend_lines.append(
            "    # no backend servers were configured — add entries under 'Backend servers'"
        )

    stats_lines: List[str] = []
    if stats_enabled:
        stats_lines = [
            "",
            f"frontend {frontend_name}_stats",
            f"    bind *:{stats_port}",
            "    mode http",
            "    stats enable",
            f"    stats uri {stats_uri}",
            "    stats refresh 10s",
            "    stats show-legends",
            "    stats show-node",
            "    stats admin if TRUE",
            f"    stats auth {stats_user}:{stats_password}",
        ]

    haproxy_cfg = "\n".join(
        [f"# Managed by OpenSible — deployment: {cluster_id}"]
        + global_lines
        + defaults_lines
        + frontend_lines
        + backend_lines
        + stats_lines
    ) + "\n"

    # -------------------------------------------------------------- #
    # Ansible plays
    # -------------------------------------------------------------- #
    parts: List[str] = ["---"]
    parts.append("# OpenSible haproxy-lb template generation: 2026-07-haproxy-lb-v1")
    parts.append(f"# Rendered from template: {TEMPLATE['name']}")
    parts.append(f"# Deployment: {cluster_id} | mode: {mode} | backends: {len(backends)}")
    parts.append("")

    parts += [
        "- name: Deploy HAProxy load balancer",
        f"  hosts: {hosts}",
        f"  become: {become}",
        "  gather_facts: true",
        "  any_errors_fatal: false",
        *vars_files_lines(parse_vault_files(values.get("vault_files"))),
        "  vars:",
        f"    hap_cluster_id: {yaml_str(cluster_id)}",
        f"    hap_frontend_port: {frontend_port}",
        f"    hap_tls_enabled: {'true' if tls_enabled else 'false'}",
        f"    hap_tls_bind_port: {tls_port}",
        f"    hap_stats_enabled: {'true' if stats_enabled else 'false'}",
        f"    hap_stats_port: {stats_port}",
        f"    hap_open_firewall: {'true' if open_firewall else 'false'}",
        "  tasks:",

        # ---- install ----
        "    - name: Refresh apt package metadata (Debian/Ubuntu)",
        "      when: ansible_os_family == 'Debian'",
        "      ansible.builtin.apt:",
        "        update_cache: true",
        "        cache_valid_time: 300",
        "      failed_when: false",
        "    - name: Install haproxy (Debian/Ubuntu)",
        "      when: ansible_os_family == 'Debian'",
        "      ansible.builtin.apt:",
        "        name: haproxy",
        "        state: present",
        "        install_recommends: false",
        "      register: _hap_pkg_deb",
        "      until: _hap_pkg_deb is succeeded",
        "      retries: 3",
        "      delay: 10",
        "    - name: Install haproxy (RHEL/Rocky/Alma)",
        "      when: ansible_os_family == 'RedHat'",
        "      ansible.builtin.dnf:",
        "        name: haproxy",
        "        state: present",
        "      register: _hap_pkg_rh",
        "      until: _hap_pkg_rh is succeeded",
        "      retries: 3",
        "      delay: 10",

        # ---- runtime dirs (chroot + stats socket) ----
        "    - name: Ensure /var/lib/haproxy exists (chroot dir)",
        "      ansible.builtin.file:",
        "        path: /var/lib/haproxy",
        "        state: directory",
        "        owner: haproxy",
        "        group: haproxy",
        "        mode: '0755'",
        "    - name: Ensure /run/haproxy exists (stats socket)",
        "      ansible.builtin.file:",
        "        path: /run/haproxy",
        "        state: directory",
        "        owner: haproxy",
        "        group: haproxy",
        "        mode: '0755'",
        "    - name: Ensure /etc/haproxy/certs exists",
        "      ansible.builtin.file:",
        "        path: /etc/haproxy/certs",
        "        state: directory",
        "        owner: root",
        "        group: haproxy",
        "        mode: '0750'",

        # ---- config ----
        "    - name: Write /etc/haproxy/haproxy.cfg",
        "      ansible.builtin.copy:",
        "        dest: /etc/haproxy/haproxy.cfg",
        "        owner: root",
        "        group: haproxy",
        "        mode: '0640'",
        "        content: |",
        indent_block(haproxy_cfg.rstrip("\n"), "          "),
        "      register: _hap_cfg",

        # ---- validate before enabling / restarting ----
        "    - name: Validate haproxy configuration",
        "      ansible.builtin.command: haproxy -c -f /etc/haproxy/haproxy.cfg",
        "      changed_when: false",

        # ---- kernel: allow non-local bind (needed for VIP failover) ----
    ]
    if keepalived_enabled:
        parts += [
            "    - name: Allow HAProxy to bind to a non-local IP (VIP)",
            "      ansible.posix.sysctl:",
            "        name: net.ipv4.ip_nonlocal_bind",
            "        value: '1'",
            "        state: present",
            "        sysctl_set: true",
            "        reload: true",
            "      failed_when: false",
        ]

    parts += [
        # ---- service ----
        "    - name: Enable + (re)start haproxy",
        "      ansible.builtin.systemd:",
        "        name: haproxy",
        "        state: restarted",
        "        enabled: true",
        "        daemon_reload: true",
        "      when: _hap_cfg is changed",
        "    - name: Ensure haproxy is running",
        "      ansible.builtin.systemd:",
        "        name: haproxy",
        "        state: started",
        "        enabled: true",

        # ---- firewall ----
        "    - name: Open HAProxy ports (ufw)",
        "      when: hap_open_firewall and (ansible_facts.packages['ufw'] is defined or (ansible_os_family == 'Debian'))",
        "      ansible.builtin.shell: |",
        "        set +e",
        "        command -v ufw >/dev/null 2>&1 || exit 0",
        "        ufw status 2>/dev/null | grep -q 'Status: active' || exit 0",
        f"        ufw allow {frontend_port}/tcp 2>/dev/null || true",
        (f"        ufw allow {tls_port}/tcp 2>/dev/null || true" if tls_enabled else "        true"),
        (f"        ufw allow {stats_port}/tcp 2>/dev/null || true" if stats_enabled else "        true"),
        "        exit 0",
        "      changed_when: false",
        "    - name: Open HAProxy ports (firewalld)",
        "      when: hap_open_firewall and ansible_os_family == 'RedHat'",
        "      ansible.builtin.shell: |",
        "        set +e",
        "        command -v firewall-cmd >/dev/null 2>&1 || exit 0",
        "        systemctl is-active --quiet firewalld || exit 0",
        f"        firewall-cmd --permanent --add-port={frontend_port}/tcp 2>/dev/null || true",
        (f"        firewall-cmd --permanent --add-port={tls_port}/tcp 2>/dev/null || true" if tls_enabled else "        true"),
        (f"        firewall-cmd --permanent --add-port={stats_port}/tcp 2>/dev/null || true" if stats_enabled else "        true"),
        "        firewall-cmd --reload 2>/dev/null || true",
        "        exit 0",
        "      changed_when: false",

        # ---- summary ----
        "    - name: HAProxy endpoint summary",
        "      run_once: true",
        "      ansible.builtin.debug:",
        "        msg: |",
        f"          HAProxy deployed: {cluster_id}",
        f"          Frontend: {'https' if tls_enabled else mode}://<host>:{tls_port if tls_enabled else frontend_port}",
        (f"          Stats:   http://<host>:{stats_port}{stats_uri}  (user: {stats_user}  pass: {stats_password})" if stats_enabled else "          Stats:    disabled"),
        f"          Backends: {', '.join(b['ip']+':'+str(b['port']) for b in backends) if backends else '(none configured)'}",
    ]

    # -------------------------------------------------------------- #
    # Optional keepalived play (VRRP VIP)
    # -------------------------------------------------------------- #
    if keepalived_enabled and vrrp_vip:
        keepalived_cfg = (
            "global_defs {\n"
            f"    router_id {slugify(cluster_id, 'haproxy').upper()}_{{{{ inventory_hostname }}}}\n"
            "    enable_script_security\n"
            "    script_user root\n"
            "}\n"
            "\n"
            "vrrp_script chk_haproxy {\n"
            "    script \"/usr/bin/killall -0 haproxy\"\n"
            "    interval 2\n"
            "    weight 2\n"
            "    fall 2\n"
            "    rise 2\n"
            "}\n"
            "\n"
            "vrrp_instance VI_1 {\n"
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
            "        chk_haproxy\n"
            "    }\n"
            "}\n"
        )
        parts += [
            "",
            "- name: Deploy keepalived VRRP VIP for HAProxy HA",
            f"  hosts: {hosts}",
            f"  become: {become}",
            "  gather_facts: true",
            "  tasks:",
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
