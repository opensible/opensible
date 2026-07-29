"""Template: Redis / Valkey primary-replica cluster with Sentinel failover.

Deploys a Redis (or Valkey) primary-replica topology plus a co-located
Redis Sentinel process on every node for automatic failover. Uses
distro packages and systemd — no Docker, no Bitnami.

Topology (driven entirely by the ``nodes`` list):

  * The **first** node becomes the initial primary.
  * All other nodes are configured with ``replicaof <primary>``.
  * Every node also runs ``redis-sentinel`` monitoring the primary
    (quorum = ceil(N/2)+1 by default; overridable).

For HA you need at least **3 nodes** (Sentinel needs a majority to
agree on failover). Single-node runs are supported for dev.

Marker: 2026-07-redis-sentinel-v4
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


# Python 3 stdlib-only health dashboard. Written verbatim into a systemd
# unit on every Redis/Sentinel node. Keep this Jinja-safe: NO "{{", "{%"
# or "{#" sequences anywhere in this string (Ansible `copy` still runs
# template expansion on the `content:` field).
_HEALTH_SCRIPT = r'''#!/usr/bin/env python3
"""OpenSible Redis/Sentinel HTTP health dashboard.

Endpoints:
  /            HTML dashboard (auto-refresh 5s)
  /health.json JSON payload (200 healthy / 503 degraded)
  /live        liveness (always 200 if process is up)
"""
import json, os, subprocess, html, socket, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

CFG = {
    "redis_port": int(os.environ.get("REDIS_PORT", "6379")),
    "sentinel_port": int(os.environ.get("SENTINEL_PORT", "26379")),
    "primary_name": os.environ.get("PRIMARY_NAME", "mymaster"),
    "requirepass": os.environ.get("REDIS_PASS", ""),
    "username": os.environ.get("REDIS_USER", ""),
    "cli": os.environ.get("REDIS_CLI", "redis-cli"),
    "cluster_id": os.environ.get("CLUSTER_ID", ""),
}


def run(args, timeout=3):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return -1, "", str(e)


def cli_args(host, port, use_user=True):
    a = [CFG["cli"], "-h", host, "-p", str(port)]
    if use_user and CFG["username"]:
        a += ["--user", CFG["username"]]
    if CFG["requirepass"]:
        a += ["-a", CFG["requirepass"], "--no-auth-warning"]
    return a


def sentinel_cli(host, port):
    # Sentinel typically has no ACL users; only send password.
    return cli_args(host, port, use_user=False)


def redis_info(host, port, section=None):
    args = cli_args(host, port) + ["INFO"]
    if section:
        args.append(section)
    rc, out, _ = run(args)
    if rc != 0:
        return {}
    d = {}
    for line in out.splitlines():
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            d[k.strip()] = v.strip()
    return d


def _clean(s):
    s = s.strip()
    if ") " in s:
        s = s.split(") ", 1)[1]
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1]
    return s


def sentinel_list(host, port, cmd):
    rc, out, _ = run(sentinel_cli(host, port) + cmd)
    if rc != 0:
        return []
    entries = []
    kv = []
    for raw in out.splitlines():
        if not raw.strip():
            if kv:
                d = {kv[j]: kv[j + 1] for j in range(0, len(kv) - 1, 2)}
                entries.append(d)
                kv = []
            continue
        kv.append(_clean(raw))
    if kv:
        d = {kv[j]: kv[j + 1] for j in range(0, len(kv) - 1, 2)}
        entries.append(d)
    return entries


def gather():
    data = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()),
        "hostname": socket.gethostname(),
        "cluster_id": CFG["cluster_id"],
        "primary_name": CFG["primary_name"],
    }
    local = redis_info("127.0.0.1", CFG["redis_port"], "replication")
    data["local_redis"] = local
    data["local_role"] = local.get("role", "unknown")
    rc, out, _ = run(sentinel_cli("127.0.0.1", CFG["sentinel_port"]) + [
        "SENTINEL", "get-master-addr-by-name", CFG["primary_name"],
    ])
    primary = None
    if rc == 0:
        cleaned = [_clean(l) for l in out.splitlines() if l.strip()]
        if len(cleaned) >= 2:
            try:
                primary = {"ip": cleaned[0], "port": int(cleaned[1])}
            except ValueError:
                primary = None
    data["primary"] = primary
    rc, out, err = run(sentinel_cli("127.0.0.1", CFG["sentinel_port"]) + [
        "SENTINEL", "ckquorum", CFG["primary_name"],
    ])
    data["ckquorum"] = {"ok": rc == 0, "text": (out or err).strip()}
    data["replicas"] = sentinel_list("127.0.0.1", CFG["sentinel_port"], [
        "SENTINEL", "replicas", CFG["primary_name"],
    ])
    data["sentinels"] = sentinel_list("127.0.0.1", CFG["sentinel_port"], [
        "SENTINEL", "sentinels", CFG["primary_name"],
    ])
    data["master_info"] = sentinel_list("127.0.0.1", CFG["sentinel_port"], [
        "SENTINEL", "master", CFG["primary_name"],
    ])
    if primary:
        data["primary_replication"] = redis_info(primary["ip"], primary["port"], "replication")
    else:
        data["primary_replication"] = {}
    data["healthy"] = (
        data["local_role"] in ("master", "slave")
        and primary is not None
        and data["ckquorum"]["ok"]
    )
    return data


def _esc(v):
    return html.escape(str(v))


def _kv_table(d):
    if not d:
        return "<em>no data</em>"
    rows = "".join("<tr><td>" + _esc(k) + "</td><td>" + _esc(v) + "</td></tr>" for k, v in d.items())
    return "<table>" + rows + "</table>"


def _list_table(items, cols):
    if not items:
        return "<em>none reported</em>"
    head = "".join("<th>" + _esc(c) + "</th>" for c in cols)
    rows = []
    for it in items:
        rows.append("<tr>" + "".join("<td>" + _esc(it.get(c, "")) + "</td>" for c in cols) + "</tr>")
    return "<table><thead><tr>" + head + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"


CSS = (
    "body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#0f172a;color:#e2e8f0}"
    "header{padding:16px 24px;background:#111827;border-bottom:1px solid #1f2937;display:flex;align-items:center;gap:16px;flex-wrap:wrap}"
    ".badge{color:#fff;padding:6px 14px;border-radius:999px;font-weight:600;font-size:14px}"
    "h1{font-size:18px;margin:0} h2{color:#93c5fd;font-size:15px;margin:24px 0 8px}"
    "main{padding:24px;max-width:1200px;margin:0 auto}"
    ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px;margin-bottom:8px}"
    ".card{background:#1e293b;padding:16px;border-radius:8px;border:1px solid #334155}"
    ".card small{color:#94a3b8;text-transform:uppercase;letter-spacing:.05em;font-size:11px}"
    ".card .big{font-size:20px;font-weight:600;margin-top:6px;color:#f1f5f9;word-break:break-all}"
    "table{width:100%;border-collapse:collapse;font-size:13px;background:#1e293b;border-radius:8px;overflow:hidden;margin-bottom:8px}"
    "th,td{padding:8px 12px;text-align:left;border-bottom:1px solid #334155;vertical-align:top}"
    "th{background:#0f172a;color:#94a3b8;font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.04em}"
    "tr:last-child td{border-bottom:0} code{color:#fbbf24}"
    "footer{text-align:center;padding:16px;color:#64748b;font-size:12px}"
    "a{color:#93c5fd}"
)


def render_html(d):
    status = "HEALTHY" if d["healthy"] else "DEGRADED"
    color = "#16a34a" if d["healthy"] else "#dc2626"
    primary = d.get("primary")
    primary_str = (primary["ip"] + ":" + str(primary["port"])) if primary else "unknown"
    master_html = _kv_table(d["master_info"][0]) if d["master_info"] else "<em>no data</em>"
    replicas_html = _list_table(
        d["replicas"],
        ["name", "ip", "port", "flags", "link-refcount", "last-ping-sent",
         "last-ok-ping-reply", "down-after-milliseconds", "role-reported"],
    )
    sentinels_html = _list_table(
        d["sentinels"],
        ["name", "ip", "port", "flags", "last-ok-ping-reply"],
    )
    pr = d.get("primary_replication", {}) or {}
    connected = pr.get("connected_slaves", "n/a")
    parts = []
    parts.append("<!doctype html><html><head><meta charset=\"utf-8\">")
    parts.append("<title>Redis/Sentinel Health - " + _esc(d["hostname"]) + "</title>")
    parts.append("<meta http-equiv=\"refresh\" content=\"5\">")
    parts.append("<style>" + CSS + "</style></head><body>")
    parts.append("<header>")
    parts.append("<h1>Redis / Sentinel Health &mdash; <code>" + _esc(d["hostname"]) + "</code></h1>")
    parts.append("<span class=\"badge\" style=\"background:" + color + "\">" + status + "</span>")
    parts.append("<span style=\"margin-left:auto;color:#94a3b8;font-size:13px\">Cluster: <code>"
                 + _esc(d["cluster_id"] or "n/a") + "</code> &middot; primary-name: <code>"
                 + _esc(d["primary_name"]) + "</code> &middot; auto-refresh 5s</span>")
    parts.append("</header><main>")
    parts.append("<div class=\"grid\">")
    parts.append("<div class=\"card\"><small>This node role</small><div class=\"big\">" + _esc(d["local_role"]) + "</div></div>")
    parts.append("<div class=\"card\"><small>Sentinel-elected primary</small><div class=\"big\">" + _esc(primary_str) + "</div></div>")
    parts.append("<div class=\"card\"><small>Sentinel quorum</small><div class=\"big\">" + _esc(d["ckquorum"]["text"] or "unknown") + "</div></div>")
    parts.append("<div class=\"card\"><small>Replicas seen by Sentinel</small><div class=\"big\">" + str(len(d["replicas"])) + "</div></div>")
    parts.append("<div class=\"card\"><small>Peer sentinels seen</small><div class=\"big\">" + str(len(d["sentinels"])) + "</div></div>")
    parts.append("<div class=\"card\"><small>Connected slaves (from primary)</small><div class=\"big\">" + _esc(connected) + "</div></div>")
    parts.append("</div>")
    parts.append("<h2>Sentinel view of primary</h2>" + master_html)
    parts.append("<h2>Replicas (from Sentinel)</h2>" + replicas_html)
    parts.append("<h2>Peer Sentinels</h2>" + sentinels_html)
    parts.append("<h2>This node &mdash; INFO replication</h2>" + _kv_table(d["local_redis"]))
    parts.append("<h2>Primary &mdash; INFO replication</h2>" + _kv_table(pr))
    parts.append("</main><footer>Generated at " + _esc(d["generated_at"])
                 + " &middot; JSON at <a href=\"/health.json\">/health.json</a></footer>")
    parts.append("</body></html>")
    return "".join(parts)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a, **kw):
        pass

    def _send(self, code, body, ctype):
        b = body if isinstance(body, (bytes, bytearray)) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(b)
        except Exception:
            pass

    def do_GET(self):
        if self.path.startswith("/live"):
            self._send(200, "ok", "text/plain")
            return
        try:
            data = gather()
        except Exception as e:
            self._send(500, "error: " + str(e), "text/plain")
            return
        code = 200 if data["healthy"] else 503
        if self.path.startswith("/health.json") or self.path.startswith("/json"):
            self._send(code, json.dumps(data, indent=2), "application/json")
            return
        self._send(code, render_html(data), "text/html; charset=utf-8")


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    port = int(os.environ.get("HTTP_PORT", "8080"))
    ThreadedHTTPServer(("0.0.0.0", port), Handler).serve_forever()
'''





TEMPLATE = {
    "id": "redis-sentinel",
    "name": "Redis / Valkey + Sentinel",
    "category": "Databases",
    "icon": "database",
    "description": (
        "Redis (or Valkey) primary/replica with Sentinel for automatic "
        "failover. Installs from distro packages and manages redis-server "
        "+ redis-sentinel via systemd. Add each node under 'Cluster "
        "nodes' — the first entry is the initial primary, the rest are "
        "replicas. Use 3+ nodes for HA."
    ),
    "tags": ["redis", "valkey", "sentinel", "ha", "systemd", "failover"],
    "variables": [
        # ---------- Flavor ----------
        {"name": "flavor", "label": "Flavor",
         "type": "select", "default": "redis",
         "options": [
             {"label": "Redis (distro package)", "value": "redis"},
             {"label": "Valkey (distro package, Ubuntu 24.04+ / EPEL)", "value": "valkey"},
         ],
         "help": "Valkey is the open-source Redis fork. Redis is the safe default; pick Valkey only if your distro ships a valkey package."},

        # ---------- Cluster identity ----------
        {"name": "cluster_id", "label": "Cluster name",
         "type": "string", "default": "opensible-redis",
         "help": "Free-form label used for filenames and the Sentinel primary name."},
        {"name": "primary_name", "label": "Sentinel primary name",
         "type": "string", "default": "mymaster",
         "help": "Symbolic name used by clients: sentinel monitor <name> ..."},

        # ---------- Hosts / HA ----------
        {"name": "ssh_user_default", "label": "Default SSH user",
         "type": "string", "default": "root"},
        {"name": "ssh_port_default", "label": "Default SSH port",
         "type": "number", "default": 22},
        {"name": "nodes", "label": "Cluster nodes (first = initial primary)",
         "type": "nodes", "required": False,
         "help": "First entry is the initial primary. Add 2 more (3 total) for real HA — Sentinel needs a majority to fail over. Leave blank to use the generic host picker.",
         "default": [
             {"name": "redis-1", "ip": "", "ssh_user": "", "ssh_port": ""},
             {"name": "redis-2", "ip": "", "ssh_user": "", "ssh_port": ""},
             {"name": "redis-3", "ip": "", "ssh_user": "", "ssh_port": ""},
         ]},

        # ---------- Networking ----------
        {"name": "bind", "label": "Bind address",
         "type": "string", "default": "0.0.0.0",
         "help": "Space-separated list of interfaces to bind. Use 0.0.0.0 for all, or a private IP."},
        {"name": "port", "label": "Redis port",
         "type": "number", "default": 6379},
        {"name": "sentinel_port", "label": "Sentinel port",
         "type": "number", "default": 26379},
        {"name": "announce_host", "label": "Advertised host (per node)",
         "type": "string",
         "default": "{{ ansible_host | default(inventory_hostname) }}",
         "help": "Jinja expression evaluated per host. Used for replica-announce-ip and sentinel announce-ip."},

        # ---------- Auth ----------
        {"name": "requirepass", "label": "Auth password (requirepass, default user)",
         "type": "password", "required": False, "default": "",
         "help": "If set, both Redis auth and Sentinel masterauth use this password for the built-in 'default' user. Leave blank to disable default-user auth."},
        {"name": "acl_username", "label": "Additional ACL username (optional)",
         "type": "string", "required": False, "default": "",
         "help": "If set, a Redis 6+ ACL user is created on every node with the password below and full access (~* &* +@all). Clients (and the health dashboard) can then authenticate as this user instead of 'default'."},
        {"name": "acl_password", "label": "ACL user password",
         "type": "password", "required": False, "default": "",
         "help": "Password for the ACL username above. Ignored if the username is blank."},

        # ---------- Sentinel tuning ----------
        {"name": "quorum", "label": "Sentinel quorum",
         "type": "number", "default": 2,
         "help": "How many Sentinels must agree the primary is down. For 3 nodes use 2; for 5 use 3."},
        {"name": "down_after_ms", "label": "down-after-milliseconds",
         "type": "number", "default": 5000},
        {"name": "failover_timeout_ms", "label": "failover-timeout (ms)",
         "type": "number", "default": 10000},
        {"name": "parallel_syncs", "label": "parallel-syncs",
         "type": "number", "default": 1},

        # ---------- Redis tuning ----------
        {"name": "maxmemory", "label": "maxmemory (optional, e.g. 512mb)",
         "type": "string", "default": "",
         "help": "Leave blank to use system default (no cap)."},
        {"name": "maxmemory_policy", "label": "maxmemory-policy",
         "type": "string", "default": "noeviction",
         "help": "e.g. noeviction, allkeys-lru, volatile-lru."},
        {"name": "appendonly", "label": "Enable AOF persistence",
         "type": "boolean", "default": True},

        # ---------- HA / replication safety ----------
        {"name": "min_replicas_to_write", "label": "min-replicas-to-write",
         "type": "number", "default": 0,
         "help": "Refuse writes on primary if fewer than N replicas are connected. Set to 1 on a 3-node cluster to avoid split-brain writes. 0 disables."},
        {"name": "min_replicas_max_lag", "label": "min-replicas-max-lag (sec)",
         "type": "number", "default": 10,
         "help": "Replicas lagging more than this many seconds do not count toward min-replicas-to-write."},
        {"name": "repl_diskless_sync", "label": "Diskless replication sync",
         "type": "boolean", "default": True,
         "help": "Stream RDB directly to replicas over the socket instead of via a temp file. Faster resync after failover."},
        {"name": "repl_timeout", "label": "repl-timeout (sec)",
         "type": "number", "default": 60},
        {"name": "tcp_keepalive", "label": "tcp-keepalive (sec)",
         "type": "number", "default": 60,
         "help": "Detect dead peers faster. 60s is a safe default."},
        {"name": "sentinel_deny_scripts_reconfig", "label": "sentinel deny-scripts-reconfig",
         "type": "boolean", "default": True,
         "help": "Prevent runtime changes of notification/client-reconfig scripts via SENTINEL SET (security)."},

        # ---------- Health HTTP endpoint ----------
        {"name": "health_http_enabled", "label": "Enable HTTP health dashboard",
         "type": "boolean", "default": True,
         "help": "Install a small HTTP service on each node exposing / (HTML dashboard) and /health.json with full cluster/replica/Sentinel status."},
        {"name": "health_http_port", "label": "Health HTTP port",
         "type": "number", "default": 8080,
         "help": "Port for the health dashboard. Visit http://<node-ip>:<port>/ from your browser."},

        # ---------- Ops ----------
        {"name": "open_firewall", "label": "Open ports in UFW/firewalld",
         "type": "boolean", "default": True},
        {"name": "become", "label": "Run as sudo (become)",
         "type": "boolean", "default": True},
        VAULT_FILES_VARIABLE,
    ],
}



def suggested_filename(values: Dict[str, Any]) -> str:
    stem = slugify(values.get("cluster_id"), "redis")
    return f"{stem}-redis-sentinel.yml"


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
        name = str(n.get("name") or f"redis-{i+1}").strip() or f"redis-{i+1}"
        user = str(n.get("ssh_user") or default_user or "root").strip() or "root"
        try:
            port = int(n.get("ssh_port") or default_port or 22)
        except Exception:
            port = 22
        out.append({
            "name": name,
            "node_slug": slugify(name, f"redis-{i+1}") or f"redis-{i+1}",
            "ip": ip,
            "ssh_user": user,
            "ssh_port": port,
            "index": i + 1,
        })
    return out


def render(values: Dict[str, Any], targets: Dict[str, Any]) -> str:
    become = "true" if values.get("become", True) else "false"

    flavor = (values.get("flavor") or "redis").strip().lower()
    if flavor not in ("redis", "valkey"):
        flavor = "redis"

    cluster_id = values.get("cluster_id") or "opensible-redis"
    primary_name = values.get("primary_name") or "mymaster"
    bind = values.get("bind") or "0.0.0.0"
    port = int(values.get("port") or 6379)
    sentinel_port = int(values.get("sentinel_port") or 26379)
    announce_host = values.get("announce_host") or "{{ ansible_host | default(inventory_hostname) }}"
    requirepass = str(values.get("requirepass") or "")
    acl_username = str(values.get("acl_username") or "").strip()
    acl_password = str(values.get("acl_password") or "")
    quorum = int(values.get("quorum") or 2)
    down_after = int(values.get("down_after_ms") or 5000)
    failover_timeout = int(values.get("failover_timeout_ms") or 10000)
    parallel_syncs = int(values.get("parallel_syncs") or 1)
    maxmemory = str(values.get("maxmemory") or "").strip()
    maxmemory_policy = values.get("maxmemory_policy") or "noeviction"
    appendonly = "yes" if values.get("appendonly", True) else "no"
    min_replicas_to_write = int(values.get("min_replicas_to_write") or 0)
    min_replicas_max_lag = int(values.get("min_replicas_max_lag") or 10)
    repl_diskless_sync = "yes" if values.get("repl_diskless_sync", True) else "no"
    repl_timeout = int(values.get("repl_timeout") or 60)
    tcp_keepalive = int(values.get("tcp_keepalive") or 60)
    sentinel_deny_scripts_reconfig = "yes" if values.get("sentinel_deny_scripts_reconfig", True) else "no"
    open_firewall = bool(values.get("open_firewall", True))
    health_http_enabled = bool(values.get("health_http_enabled", True))
    health_http_port = int(values.get("health_http_port") or 8080)


    nodes = _norm_nodes(
        values.get("nodes"),
        values.get("ssh_user_default") or "root",
        values.get("ssh_port_default") or 22,
    )

    cluster_group = slugify(cluster_id, "redis") + "_nodes"

    # Flavor-specific package + service names.
    if flavor == "valkey":
        pkg_deb = "valkey-server valkey-sentinel valkey-tools"
        pkg_rhel = "valkey"
        svc_server = "valkey-server"
        svc_sentinel = "valkey-sentinel"
        conf_dir = "/etc/valkey"
        server_conf = "/etc/valkey/valkey.conf"
        sentinel_conf = "/etc/valkey/sentinel.conf"
        run_user = "valkey"
        data_dir = "/var/lib/valkey"
        log_dir = "/var/log/valkey"
        run_dir = "/run/valkey"
        server_pidfile = "/run/valkey/valkey-server.pid"
        server_logfile = "/var/log/valkey/valkey-server.log"
        sentinel_pidfile = "/run/valkey/valkey-sentinel.pid"
        sentinel_logfile = "/var/log/valkey/valkey-sentinel.log"
        server_bin = "valkey-server"
        cli_bin = "valkey-cli"
    else:
        pkg_deb = "redis-server redis-sentinel redis-tools"
        pkg_rhel = "redis"
        svc_server = "redis-server"
        svc_sentinel = "redis-sentinel"
        conf_dir = "/etc/redis"
        server_conf = "/etc/redis/redis.conf"
        sentinel_conf = "/etc/redis/sentinel.conf"
        run_user = "redis"
        data_dir = "/var/lib/redis"
        log_dir = "/var/log/redis"
        run_dir = "/run/redis"
        server_pidfile = "/run/redis/redis-server.pid"
        server_logfile = "/var/log/redis/redis-server.log"
        sentinel_pidfile = "/run/redis/redis-sentinel.pid"
        sentinel_logfile = "/var/log/redis/redis-sentinel.log"
        server_bin = "redis-server"
        cli_bin = "redis-cli"

    parts: List[str] = ["---"]
    parts.append(f"# OpenSible redis-sentinel template generation: 2026-07-redis-sentinel-v4")
    parts.append(f"# Rendered from template: {TEMPLATE['name']}")
    parts.append(f"# Cluster: {cluster_id} | flavor: {flavor} | nodes: {len(nodes) if nodes else 'from targets'}")
    parts.append("")

    # ------------------------------------------------------------------ #
    # PLAY 0 — dynamic inventory (if nodes provided)
    # ------------------------------------------------------------------ #
    if nodes:
        parts += [
            "- name: Register Redis/Valkey nodes into a dynamic inventory group",
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
            "        redis_node_slug: \"{{ item.node_slug }}\"",
            "        redis_node_index: \"{{ item.index }}\"",
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
        # Initial primary is the first node's IP; used before any failover.
        initial_primary_ip = nodes[0]["ip"]
    else:
        play_hosts = render_hosts(targets)
        initial_primary_ip = "{{ hostvars[ansible_play_hosts[0]].ansible_host | default(ansible_play_hosts[0]) }}"

    # ------------------------------------------------------------------ #
    # PLAY 1 — install + configure on every node
    # ------------------------------------------------------------------ #
    parts += [
        f"- name: Deploy {flavor} + Sentinel on every node",
        f"  hosts: {play_hosts}",
        f"  become: {become}",
        "  gather_facts: true",
        "  any_errors_fatal: true",
        *vars_files_lines(parse_vault_files(values.get("vault_files"))),
        "  vars:",
        f"    redis_flavor: {yaml_str(flavor)}",
        f"    redis_cluster_id: {yaml_str(cluster_id)}",
        f"    redis_primary_name: {yaml_str(primary_name)}",
        f"    redis_bind: {yaml_str(bind)}",
        f"    redis_port: {port}",
        f"    redis_sentinel_port: {sentinel_port}",
        f"    redis_announce_host: \"{announce_host}\"",
        f"    redis_requirepass: {yaml_str(requirepass)}",
        f"    redis_acl_username: {yaml_str(acl_username)}",
        f"    redis_acl_password: {yaml_str(acl_password)}",
        f"    redis_quorum: {quorum}",
        f"    redis_down_after_ms: {down_after}",
        f"    redis_failover_timeout_ms: {failover_timeout}",
        f"    redis_parallel_syncs: {parallel_syncs}",
        f"    redis_maxmemory: {yaml_str(maxmemory)}",
        f"    redis_maxmemory_policy: {yaml_str(maxmemory_policy)}",
        f"    redis_appendonly: {yaml_str(appendonly)}",
        f"    redis_min_replicas_to_write: {min_replicas_to_write}",
        f"    redis_min_replicas_max_lag: {min_replicas_max_lag}",
        f"    redis_repl_diskless_sync: {yaml_str(repl_diskless_sync)}",
        f"    redis_repl_timeout: {repl_timeout}",
        f"    redis_tcp_keepalive: {tcp_keepalive}",
        f"    redis_sentinel_deny_scripts_reconfig: {yaml_str(sentinel_deny_scripts_reconfig)}",
        f"    redis_expected_node_count: {len(nodes) if nodes else 0}",
        f"    redis_initial_primary_ip: {yaml_str(initial_primary_ip)}",
        f"    redis_conf_dir: {yaml_str(conf_dir)}",
        f"    redis_server_conf: {yaml_str(server_conf)}",
        f"    redis_sentinel_conf: {yaml_str(sentinel_conf)}",
        f"    redis_run_user: {yaml_str(run_user)}",
        f"    redis_data_dir: {yaml_str(data_dir)}",
        f"    redis_log_dir: {yaml_str(log_dir)}",
        f"    redis_run_dir: {yaml_str(run_dir)}",
        f"    redis_server_pidfile: {yaml_str(server_pidfile)}",
        f"    redis_sentinel_pidfile: {yaml_str(sentinel_pidfile)}",
        f"    redis_server_logfile: {yaml_str(server_logfile)}",
        f"    redis_sentinel_logfile: {yaml_str(sentinel_logfile)}",
        f"    redis_server_bin: {yaml_str(server_bin)}",
        f"    redis_cli_bin: {yaml_str(cli_bin)}",
        f"    redis_svc_server: {yaml_str(svc_server)}",
        f"    redis_svc_sentinel: {yaml_str(svc_sentinel)}",
        f"    redis_packages_debian: [{', '.join(yaml_str(p) for p in pkg_deb.split())}]",
        f"    redis_packages_redhat: [{yaml_str(pkg_rhel)}]",
        "  tasks:",

        # ---------- Determine per-host role ----------
        "    - name: Resolve node index (fallback to play index)",
        "      ansible.builtin.set_fact:",
        "        redis_node_index: \"{{ redis_node_index | default(ansible_play_hosts.index(inventory_hostname) + 1) | int }}\"",
        # If a Sentinel is already running from a previous deploy, ask it for
        # the *current* primary. This lets us re-run the playbook safely after
        # a failover without demoting the promoted node back to the initial one.
        "    - name: Detect current Sentinel primary (for safe re-runs after failover)",
        "      ansible.builtin.shell: |",
        "        set +e",
        "        command -v {{ redis_cli_bin }} >/dev/null 2>&1 || exit 0",
        "        out=$({{ redis_cli_bin }} -h 127.0.0.1 -p {{ redis_sentinel_port }} SENTINEL get-master-addr-by-name {{ redis_primary_name }} 2>/dev/null)",
        "        [ -n \"$out\" ] && echo \"$out\" | head -n1",
        "        exit 0",
        "      register: _redis_current_primary_probe",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Adopt current Sentinel primary if one is already elected",
        "      when:",
        "        - (_redis_current_primary_probe.stdout | default('') | trim) | length > 0",
        "        - (_redis_current_primary_probe.stdout | trim) not in ['127.0.0.1', 'localhost', '::1', '0.0.0.0']",
        "      ansible.builtin.set_fact:",
        "        redis_initial_primary_ip: \"{{ _redis_current_primary_probe.stdout | trim }}\"",
        "    - name: Share current primary IP across the play",
        "      run_once: true",
        "      ansible.builtin.set_fact:",
        "        redis_cluster_primary_ip: \"{{ redis_initial_primary_ip }}\"",
        "    - name: Broadcast primary IP to all hosts",
        "      ansible.builtin.set_fact:",
        "        redis_initial_primary_ip: \"{{ hostvars[ansible_play_hosts[0]].redis_cluster_primary_ip | default(redis_initial_primary_ip) }}\"",
        "    - name: Decide whether this host is the initial/current primary",
        "      ansible.builtin.set_fact:",
        "        redis_is_initial_primary: \"{{ (ansible_host | default(inventory_hostname)) == redis_initial_primary_ip }}\"",



        # ---------- Install packages ----------
        "    - name: Refresh apt package metadata when available",
        "      ansible.builtin.shell: |",
        "        set -e",
        "        command -v apt-get >/dev/null 2>&1 || exit 0",
        "        apt-get update -y",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Ensure swap exists on low-memory Debian/Ubuntu hosts (avoid apt OOM)",
        "      when: ansible_os_family == 'Debian' and (ansible_memtotal_mb | int) < 1400",
        "      ansible.builtin.shell: |",
        "        set -e",
        "        if ! swapon --show=NAME --noheadings | grep -q '/swapfile'; then",
        "          (fallocate -l 1G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=1024)",
        "          chmod 600 /swapfile",
        "          mkswap /swapfile",
        "          swapon /swapfile",
        "          grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab",
        "        fi",
        "      changed_when: false",
        "      failed_when: false",
        f"    - name: Enable backports repo for Valkey on Debian (bullseye/bookworm)",
        "      when:",
        "        - ansible_os_family == 'Debian'",
        "        - redis_flavor == 'valkey'",
        "        - ansible_distribution == 'Debian'",
        "        - ansible_distribution_release in ['bullseye','bookworm']",
        "      ansible.builtin.shell: |",
        "        set -e",
        "        release={{ ansible_distribution_release }}",
        "        list=/etc/apt/sources.list.d/${release}-backports.list",
        "        if ! grep -Rqs \"${release}-backports\" /etc/apt/sources.list /etc/apt/sources.list.d/; then",
        "          echo \"deb http://deb.debian.org/debian ${release}-backports main\" > \"$list\"",
        "        fi",
        "        apt-get update -y",
        "      changed_when: false",
        "      failed_when: false",
        f"    - name: Install {flavor} + Sentinel packages (Debian/Ubuntu, no recommends)",
        "      when: ansible_os_family == 'Debian'",
        "      ansible.builtin.shell: |",
        "        set -e",
        "        export DEBIAN_FRONTEND=noninteractive",
        "        pkgs=\"{{ redis_packages_debian | join(' ') }}\"",
        "        # Prefer backports on Debian for Valkey (older releases lack it in main).",
        "        if [ \"{{ redis_flavor }}\" = \"valkey\" ] \\",
        "           && [ \"{{ ansible_distribution | default('') }}\" = \"Debian\" ] \\",
        "           && echo \"{{ ansible_distribution_release | default('') }}\" | grep -qE '^(bullseye|bookworm)$'; then",
        "          apt-get install -y --no-install-recommends -t {{ ansible_distribution_release }}-backports $pkgs \\",
        "            || apt-get install -y --no-install-recommends $pkgs",
        "        else",
        "          apt-get install -y --no-install-recommends $pkgs",
        "        fi",
        "      register: _redis_package_install_deb",
        "      changed_when: \"'Setting up' in (_redis_package_install_deb.stdout | default(''))\"",
        "      failed_when: false",
        f"    - name: Build {flavor} from source when distro package is unavailable (Debian/Ubuntu)",
        "      when:",
        "        - ansible_os_family == 'Debian'",
        "        - redis_flavor == 'valkey'",
        "        - (_redis_package_install_deb.rc | default(1)) != 0",
        "      block:",
        "        - name: Install Valkey build dependencies",
        "          ansible.builtin.shell: |",
        "            set -e",
        "            export DEBIAN_FRONTEND=noninteractive",
        "            apt-get install -y --no-install-recommends \\",
        "              build-essential pkg-config ca-certificates curl tar make gcc libc6-dev libsystemd-dev",
        "          changed_when: false",
        "        - name: Create valkey system user",
        "          ansible.builtin.user:",
        "            name: \"{{ redis_run_user }}\"",
        "            system: true",
        "            create_home: false",
        "            shell: /usr/sbin/nologin",
        "            home: \"{{ redis_data_dir }}\"",
        "        - name: Download Valkey source tarball",
        "          ansible.builtin.get_url:",
        "            url: \"https://github.com/valkey-io/valkey/archive/refs/tags/{{ valkey_source_version | default('8.0.1') }}.tar.gz\"",
        "            dest: \"/tmp/valkey-src.tar.gz\"",
        "            mode: '0644'",
        "            force: true",
        "        - name: Extract Valkey source",
        "          ansible.builtin.unarchive:",
        "            src: /tmp/valkey-src.tar.gz",
        "            dest: /tmp/",
        "            remote_src: true",
        "        - name: Build Valkey binaries",
        "          ansible.builtin.shell: |",
        "            set -e",
        "            cd /tmp/valkey-{{ valkey_source_version | default('8.0.1') }}",
        "            make BUILD_TLS=no USE_SYSTEMD=yes -j$(nproc) 2>&1 | tail -n 200",
        "            make PREFIX=/usr install",
        "            # Sentinel is a symlinked mode of the server binary.",
        "            [ -e /usr/bin/valkey-sentinel ] || ln -sf /usr/bin/valkey-server /usr/bin/valkey-sentinel",
        "          args:",
        "            creates: /usr/bin/valkey-server",
        "        - name: Install valkey-server systemd unit",
        "          ansible.builtin.copy:",
        "            dest: /etc/systemd/system/valkey-server.service",
        "            owner: root",
        "            group: root",
        "            mode: '0644'",
        "            content: |",
        "              [Unit]",
        "              Description=Valkey persistent key-value database (server)",
        "              After=network-online.target",
        "              Wants=network-online.target",
        "              [Service]",
        "              Type=notify",
        "              User={{ redis_run_user }}",
        "              Group={{ redis_run_user }}",
        "              ExecStart=/usr/bin/valkey-server {{ redis_server_conf }} --supervised systemd --daemonize no",
        "              ExecStop=/bin/kill -s TERM $MAINPID",
        "              Restart=always",
        "              RuntimeDirectory=valkey",
        "              RuntimeDirectoryMode=0755",
        "              LimitNOFILE=65535",
        "              [Install]",
        "              WantedBy=multi-user.target",
        "        - name: Install valkey-sentinel systemd unit",
        "          ansible.builtin.copy:",
        "            dest: /etc/systemd/system/valkey-sentinel.service",
        "            owner: root",
        "            group: root",
        "            mode: '0644'",
        "            content: |",
        "              [Unit]",
        "              Description=Valkey Sentinel",
        "              After=network-online.target",
        "              Wants=network-online.target",
        "              [Service]",
        "              Type=notify",
        "              User={{ redis_run_user }}",
        "              Group={{ redis_run_user }}",
        "              ExecStart=/usr/bin/valkey-sentinel {{ redis_sentinel_conf }} --supervised systemd --daemonize no",
        "              ExecStop=/bin/kill -s TERM $MAINPID",
        "              Restart=always",
        "              RuntimeDirectory=valkey",
        "              RuntimeDirectoryMode=0755",
        "              LimitNOFILE=65535",
        "              [Install]",
        "              WantedBy=multi-user.target",
        "        - name: Reload systemd after installing valkey units",
        "          ansible.builtin.systemd:",
        "            daemon_reload: true",
        "        - name: Mark valkey install as succeeded via source build",
        "          ansible.builtin.set_fact:",
        "            _redis_package_install_deb:",
        "              rc: 0",
        "              changed: true",
        "              stdout: 'Setting up valkey (source build)'",
        "    - name: Fail if Debian package install did not succeed",
        "      when:",
        "        - ansible_os_family == 'Debian'",
        "        - (_redis_package_install_deb.rc | default(1)) != 0",
        "      ansible.builtin.fail:",
        "        msg: |",
        "          Failed to install {{ redis_flavor }} packages on {{ inventory_hostname }}.",
        "          stderr: {{ _redis_package_install_deb.stderr | default('') }}",
        f"    - name: Install {flavor} + Sentinel packages (RHEL family)",
        "      when: ansible_os_family != 'Debian'",
        "      ansible.builtin.package:",
        "        name: \"{{ redis_packages_redhat }}\"",
        "        state: present",
        "      register: _redis_package_install_rh",
        "    - name: Unify package-install result across families",
        "      ansible.builtin.set_fact:",
        "        _redis_package_install:",
        "          changed: \"{{ (_redis_package_install_deb.changed | default(false)) or (_redis_package_install_rh.changed | default(false)) }}\"",
        "    - name: Stop Sentinel before Redis topology changes",
        "      ansible.builtin.systemd:",
        "        name: \"{{ redis_svc_sentinel }}\"",
        "        state: stopped",
        "        enabled: true",
        "      failed_when: false",
        "    - name: Stop redis/valkey server before replacing managed config",
        "      ansible.builtin.systemd:",
        "        name: \"{{ redis_svc_server }}\"",
        "        state: stopped",
        "        enabled: true",
        "      failed_when: false",
        "    - name: Remove stale runtime pid files",
        "      ansible.builtin.file:",
        "        path: \"{{ item }}\"",
        "        state: absent",
        "      loop:",
        "        - \"{{ redis_server_pidfile }}\"",
        "        - \"{{ redis_sentinel_pidfile }}\"",
        "    - name: Ensure config directory exists",
        "      ansible.builtin.file:",
        "        path: \"{{ redis_conf_dir }}\"",
        "        state: directory",
        "        owner: \"{{ redis_run_user }}\"",
        "        group: \"{{ redis_run_user }}\"",
        "        mode: '0750'",
        "    - name: Ensure data directory exists",
        "      ansible.builtin.file:",
        "        path: \"{{ redis_data_dir }}\"",
        "        state: directory",
        "        owner: \"{{ redis_run_user }}\"",
        "        group: \"{{ redis_run_user }}\"",
        "        mode: '0750'",
        "        recurse: true",
        f"    - name: Ensure log directory exists",
        "      ansible.builtin.file:",
        f"        path: {yaml_str(log_dir)}",
        "        state: directory",
        "        owner: \"{{ redis_run_user }}\"",
        "        group: \"{{ redis_run_user }}\"",
        "        mode: '0750'",
        "        recurse: true",
        f"    - name: Ensure runtime directory exists",
        "      ansible.builtin.file:",
        f"        path: {yaml_str(run_dir)}",
        "        state: directory",
        "        owner: \"{{ redis_run_user }}\"",
        "        group: \"{{ redis_run_user }}\"",
        "        mode: '0755'",
        "    - name: Ensure systemd override directories exist",
        "      ansible.builtin.file:",
        "        path: \"/etc/systemd/system/{{ item }}.service.d\"",
        "        state: directory",
        "        owner: root",
        "        group: root",
        "        mode: '0755'",
        "      loop:",
        "        - \"{{ redis_svc_server }}\"",
        "        - \"{{ redis_svc_sentinel }}\"",
        "    - name: Install systemd write-path override for Redis/Valkey services",
        "      ansible.builtin.copy:",
        "        dest: \"/etc/systemd/system/{{ item }}.service.d/opensible.conf\"",
        "        owner: root",
        "        group: root",
        "        mode: '0644'",
        "        content: |",
        "          [Service]",
        "          ReadWritePaths={{ redis_conf_dir }} {{ redis_data_dir }} {{ redis_log_dir }} {{ redis_run_dir }}",
        "      loop:",
        "        - \"{{ redis_svc_server }}\"",
        "        - \"{{ redis_svc_sentinel }}\"",
        "      register: _redis_systemd_override",

        # Recover cleanly from older/bad generated runs that seeded Sentinel
        # with a loopback master (127.0.0.1). Keeping that file would make
        # Sentinel monitor itself and all Redis nodes could become replicas of
        # localhost, which is exactly the failure shown in the user logs.
        "    - name: Detect unsafe loopback Sentinel monitor target",
        "      ansible.builtin.shell: |",
        "        set +e",
        "        [ -f {{ redis_sentinel_conf }} ] || exit 0",
        "        awk -v name=\"{{ redis_primary_name }}\" '$1==\"sentinel\" && $2==\"monitor\" && $3==name {print $4; exit}' {{ redis_sentinel_conf }}",
        "        exit 0",
        "      register: _redis_existing_sentinel_monitor",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Remove unsafe loopback Sentinel config so it can be reseeded",
        "      when: (_redis_existing_sentinel_monitor.stdout | default('') | trim) in ['127.0.0.1', 'localhost', '::1', '0.0.0.0']",
        "      ansible.builtin.file:",
        "        path: \"{{ redis_sentinel_conf }}\"",
        "        state: absent",

        # ---------- redis.conf ----------
        "    - name: Write server config",
        "      ansible.builtin.copy:",
        "        dest: \"{{ redis_server_conf }}\"",
        "        owner: \"{{ redis_run_user }}\"",
        "        group: \"{{ redis_run_user }}\"",
        "        mode: '0640'",
        "        content: |",
        "          # Managed by OpenSible - do not edit by hand",
        "          bind {{ redis_bind }}",
        "          port {{ redis_port }}",
        "          protected-mode no",
        "          daemonize no",
        "          supervised systemd",
        f"          pidfile {server_pidfile}",
        f"          logfile {server_logfile}",
        "          dir {{ redis_data_dir }}",
        "          appendonly {{ redis_appendonly }}",
        "          maxmemory-policy {{ redis_maxmemory_policy }}",
        "          {{ 'maxmemory ' ~ redis_maxmemory if redis_maxmemory else '# maxmemory disabled' }}",
        "          {{ 'requirepass ' ~ redis_requirepass if redis_requirepass else '# requirepass disabled' }}",
        "          {{ 'masterauth ' ~ redis_requirepass if redis_requirepass else '# masterauth disabled' }}",
        "          replica-announce-ip {{ redis_announce_host }}",
        "          replica-announce-port {{ redis_port }}",
        "          replica-read-only yes",
        "          replica-serve-stale-data yes",
        "          replica-priority {{ 100 if redis_is_initial_primary else (90 + (redis_node_index | int)) }}",
        "          repl-diskless-sync {{ redis_repl_diskless_sync }}",
        "          repl-diskless-sync-delay 5",
        "          repl-timeout {{ redis_repl_timeout }}",
        "          repl-ping-replica-period 5",
        "          tcp-keepalive {{ redis_tcp_keepalive }}",
        "          min-replicas-to-write {{ redis_min_replicas_to_write }}",
        "          min-replicas-max-lag {{ redis_min_replicas_max_lag }}",
        "          {{ 'replicaof ' ~ redis_initial_primary_ip ~ ' ' ~ (redis_port | string) if not redis_is_initial_primary else '# initial primary - no replicaof' }}",

        "      register: _redis_server_config",

        # ---------- Detect server version for feature gating ----------
        "    - name: Detect redis/valkey server version",
        "      ansible.builtin.shell: |",
        "        set -e",
        "        \"{{ redis_server_bin }}\" --version | grep -oE 'v=[0-9]+\\.[0-9]+\\.[0-9]+' | head -n1 | cut -d= -f2",
        "      register: _redis_version_out",
        "      changed_when: false",
        "    - name: Set redis feature flags",
        "      ansible.builtin.set_fact:",
        "        redis_detected_version: \"{{ _redis_version_out.stdout | trim }}\"",
        "        redis_supports_hostnames: \"{{ (_redis_version_out.stdout | trim) is version('6.2.0', '>=') }}\"",

        # ---------- sentinel.conf ----------
        # Only write if missing. Sentinel rewrites this file in place at
        # runtime (recording epoch, known-replica, known-sentinel, myid, and
        # the currently elected master). Overwriting on re-run would wipe
        # that state and could trigger a needless failover flap.
        "    - name: Write sentinel config (initial only - Sentinel rewrites at runtime)",
        "      ansible.builtin.copy:",
        "        dest: \"{{ redis_sentinel_conf }}\"",
        "        owner: \"{{ redis_run_user }}\"",
        "        group: \"{{ redis_run_user }}\"",
        "        mode: '0640'",
        "        force: false",
        "        content: |",
        "          # Managed by OpenSible - initial seed; Sentinel appends runtime state",
        "          port {{ redis_sentinel_port }}",
        "          bind {{ redis_bind }}",
        "          protected-mode no",
        "          daemonize no",
        "          supervised systemd",
        f"          pidfile {sentinel_pidfile}",
        f"          logfile {sentinel_logfile}",
        "          dir {{ redis_data_dir }}",
        "          sentinel deny-scripts-reconfig {{ redis_sentinel_deny_scripts_reconfig }}",
        "          {% if redis_supports_hostnames %}sentinel resolve-hostnames yes",
        "          sentinel announce-hostnames yes",
        "          {% endif %}sentinel announce-ip {{ redis_announce_host }}",
        "          sentinel announce-port {{ redis_sentinel_port }}",
        "          sentinel monitor {{ redis_primary_name }} {{ redis_initial_primary_ip }} {{ redis_port }} {{ redis_quorum }}",
        "          sentinel down-after-milliseconds {{ redis_primary_name }} {{ redis_down_after_ms }}",
        "          sentinel failover-timeout {{ redis_primary_name }} {{ redis_failover_timeout_ms }}",
        "          sentinel parallel-syncs {{ redis_primary_name }} {{ redis_parallel_syncs }}",
        "          {{ 'sentinel auth-pass ' ~ redis_primary_name ~ ' ' ~ redis_requirepass if redis_requirepass else '# sentinel auth-pass disabled' }}",
        "      register: _redis_sentinel_config",


    ]

    # Firewall
    if open_firewall:
        ufw_extra = f"        ufw allow {health_http_port}/tcp || true\n" if health_http_enabled else ""
        fw_extra = f"        firewall-cmd --permanent --add-port={health_http_port}/tcp || true\n" if health_http_enabled else ""
        parts += [
            "    - name: Open Redis + Sentinel ports (ufw, if active)",
            "      ansible.builtin.shell: |",
            "        set -e",
            "        command -v ufw >/dev/null 2>&1 || exit 0",
            "        ufw status | grep -q 'Status: active' || exit 0",
            f"        ufw allow {port}/tcp || true",
            f"        ufw allow {sentinel_port}/tcp || true",
            *([f"        ufw allow {health_http_port}/tcp || true"] if health_http_enabled else []),
            "      changed_when: false",
            "      failed_when: false",
            "    - name: Open Redis + Sentinel ports (firewalld, if active)",
            "      ansible.builtin.shell: |",
            "        set -e",
            "        command -v firewall-cmd >/dev/null 2>&1 || exit 0",
            "        firewall-cmd --state >/dev/null 2>&1 || exit 0",
            f"        firewall-cmd --permanent --add-port={port}/tcp || true",
            f"        firewall-cmd --permanent --add-port={sentinel_port}/tcp || true",
            *([f"        firewall-cmd --permanent --add-port={health_http_port}/tcp || true"] if health_http_enabled else []),
            "        firewall-cmd --reload || true",
            "      changed_when: false",
            "      failed_when: false",
        ]


    # Enable + start Redis first, let replicas attach, then start Sentinel.
    # Starting Sentinel while Redis processes are being restarted can trigger
    # a false failover and the Redis log line '-failover-abort-no-good-slave'.
    parts += [
        "    - name: Ensure systemd knows about updated unit state",
        "      ansible.builtin.systemd:",
        "        daemon_reload: true",
        "    - name: Preflight redis/valkey config parse",
        "      ansible.builtin.shell: |",
        "        set -o pipefail",
        "        rm -f /tmp/opensible-redis-config-check.pid /tmp/opensible-redis-config-check.sock",
        "        timeout 5s {{ redis_server_bin }} {{ redis_server_conf }} --supervised no --daemonize no --port 0 --unixsocket /tmp/opensible-redis-config-check.sock --unixsocketperm 700 --save \"\" --appendonly no --dir /tmp --pidfile /tmp/opensible-redis-config-check.pid --logfile \"\"",
        "        rc=$?",
        "        rm -f /tmp/opensible-redis-config-check.pid /tmp/opensible-redis-config-check.sock",
        "        if [ \"$rc\" = \"124\" ]; then exit 0; fi",
        "        exit \"$rc\"",
        "      args:",
        "        executable: /bin/bash",
        "      register: _redis_config_check",
        "      changed_when: false",
        "    - name: Enable and start redis/valkey server",
        "      block:",
        "        - name: Start redis/valkey server",
        "          ansible.builtin.systemd:",
        "            name: \"{{ redis_svc_server }}\"",
        "            enabled: true",
        "            state: started",
        "            daemon_reload: true",
        "      rescue:",
        "        - name: Dump redis/valkey service status on start failure",
        "          ansible.builtin.command: systemctl status {{ redis_svc_server }} --no-pager",
        "          register: _redis_start_status",
        "          changed_when: false",
        "          failed_when: false",
        "        - name: Dump redis/valkey journal on start failure",
        "          ansible.builtin.command: journalctl -u {{ redis_svc_server }} -n 120 --no-pager",
        "          register: _redis_start_journal",
        "          changed_when: false",
        "          failed_when: false",
        "        - name: Dump redis/valkey log file on start failure",
        "          ansible.builtin.shell: tail -n 120 {{ redis_server_logfile }} 2>/dev/null || true",
        "          register: _redis_start_logfile",
        "          changed_when: false",
        "          failed_when: false",
        "        - name: Show redis/valkey start diagnostics",
        "          ansible.builtin.debug:",
        "            msg:",
        "              - \"systemctl status:\"",
        "              - \"{{ _redis_start_status.stdout_lines | default([]) }}\"",
        "              - \"journalctl:\"",
        "              - \"{{ _redis_start_journal.stdout_lines | default([]) }}\"",
        "              - \"log file:\"",
        "              - \"{{ _redis_start_logfile.stdout_lines | default([]) }}\"",
        "        - name: Re-raise start failure",
        "          ansible.builtin.fail:",
        "            msg: \"Redis/Valkey service failed to start - see diagnostics above.\"",
        "    - name: Restart redis/valkey server after package or config changes",
        "      block:",
        "        - name: Restart redis/valkey server",
        "          ansible.builtin.systemd:",
        "            name: \"{{ redis_svc_server }}\"",
        "            state: restarted",
        "            daemon_reload: true",
        "          when: _redis_package_install.changed or _redis_server_config.changed or _redis_systemd_override.changed",
        "      rescue:",
        "        - name: Dump redis-server journal on failure",
        "          ansible.builtin.command: journalctl -u {{ redis_svc_server }} -n 80 --no-pager",
        "          register: _redis_journal",
        "          changed_when: false",
        "          failed_when: false",
        "        - name: Show redis-server diagnostics",
        "          ansible.builtin.debug:",
        "            msg:",
        "              - \"redis-server journal:\"",
        "              - \"{{ _redis_journal.stdout_lines | default([]) }}\"",
        "        - name: Re-raise failure",
        "          ansible.builtin.fail:",
        "            msg: \"Redis restart failed after config change - see journal output above.\"",
        "    - name: Wait for Redis port to accept connections",
        "      ansible.builtin.wait_for:",
        "        host: 127.0.0.1",
        "        port: \"{{ redis_port }}\"",
        "        timeout: 60",
        "    - name: Wait for configured primary Redis to be reachable",
        "      ansible.builtin.wait_for:",
        "        host: \"{{ redis_initial_primary_ip }}\"",
        "        port: \"{{ redis_port }}\"",
        "        timeout: 90",
        "    - name: Wait for replicas to attach before starting Sentinel",
        "      ansible.builtin.command: >-",
        "        {{ redis_cli_bin }} -h 127.0.0.1 -p {{ redis_port }}",
        "        {% if redis_requirepass %}-a {{ redis_requirepass }} --no-auth-warning{% endif %}",
        "        INFO replication",
        "      register: _redis_replication_ready",
        "      changed_when: false",
        "      retries: 18",
        "      delay: 5",
        "      until: >-",
        "        ('role:master' in _redis_replication_ready.stdout) or",
        "        ('master_link_status:up' in _redis_replication_ready.stdout)",
        "    - name: Start Sentinel after Redis topology is healthy",
        "      block:",
        "        - name: Enable and start sentinel",
        "          ansible.builtin.systemd:",
        "            name: \"{{ redis_svc_sentinel }}\"",
        "            enabled: true",
        "            state: started",
        "            daemon_reload: true",
        "        - name: Restart sentinel if sentinel config changed",
        "          ansible.builtin.systemd:",
        "            name: \"{{ redis_svc_sentinel }}\"",
        "            state: restarted",
        "            daemon_reload: true",
        "          when: _redis_sentinel_config.changed",
        "      rescue:",
        "        - name: Dump sentinel journal on failure",
        "          ansible.builtin.command: journalctl -u {{ redis_svc_sentinel }} -n 120 --no-pager",
        "          register: _sentinel_journal",
        "          changed_when: false",
        "          failed_when: false",
        "        - name: Show sentinel diagnostics",
        "          ansible.builtin.debug:",
        "            msg:",
        "              - \"sentinel journal:\"",
        "              - \"{{ _sentinel_journal.stdout_lines | default([]) }}\"",
        "        - name: Re-raise failure",
        "          ansible.builtin.fail:",
        "            msg: \"Sentinel start/restart failed after Redis became healthy - see journal output above.\"",
        "    - name: Wait for Sentinel port to accept connections",
        "      ansible.builtin.wait_for:",
        "        host: 127.0.0.1",
        "        port: \"{{ redis_sentinel_port }}\"",
        "        timeout: 60",
        "    - name: PING Redis",
        "      ansible.builtin.command: >-",
        "        {{ redis_cli_bin }} -h 127.0.0.1 -p {{ redis_port }}",
        "        {% if redis_requirepass %}-a {{ redis_requirepass }} --no-auth-warning{% endif %}",
        "        PING",
        "      register: redis_ping",
        "      changed_when: false",
        "      retries: 6",
        "      delay: 5",
        "      until: redis_ping.rc == 0 and 'PONG' in redis_ping.stdout",
        "    - name: Show replication role",
        "      ansible.builtin.command: >-",
        "        {{ redis_cli_bin }} -h 127.0.0.1 -p {{ redis_port }}",
        "        {% if redis_requirepass %}-a {{ redis_requirepass }} --no-auth-warning{% endif %}",
        "        INFO replication",
        "      register: redis_info",
        "      changed_when: false",
        "    - name: Report role",
        "      ansible.builtin.debug:",
        "        msg: \"{{ inventory_hostname }} -> {{ (redis_info.stdout_lines | select('match','^role:') | list | first) | default('role:unknown') }}\"",
        # ---------- Optional ACL user ----------
        "    - name: Create/update Redis ACL user (idempotent, all nodes)",
        "      when: redis_acl_username | length > 0",
        "      ansible.builtin.command: >-",
        "        {{ redis_cli_bin }} -h 127.0.0.1 -p {{ redis_port }}",
        "        {% if redis_requirepass %}-a {{ redis_requirepass }} --no-auth-warning{% endif %}",
        "        ACL SETUSER {{ redis_acl_username }} on >{{ redis_acl_password }} ~* &* +@all",
        "      register: _redis_acl_setuser",
        "      changed_when: \"'OK' in (_redis_acl_setuser.stdout | default(''))\"",
        "      no_log: true",
        "    - name: Persist ACL to disk",
        "      when: redis_acl_username | length > 0",
        "      ansible.builtin.command: >-",
        "        {{ redis_cli_bin }} -h 127.0.0.1 -p {{ redis_port }}",
        "        {% if redis_requirepass %}-a {{ redis_requirepass }} --no-auth-warning{% endif %}",
        "        ACL SAVE",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Report ACL user",
        "      when: redis_acl_username | length > 0",
        "      ansible.builtin.debug:",
        "        msg: \"ACL user '{{ redis_acl_username }}' ready on {{ inventory_hostname }} (full access ~* &* +@all)\"",
        "",
    ]


    # ------------------------------------------------------------------ #
    # Health HTTP dashboard (per-node)
    # ------------------------------------------------------------------ #
    if health_http_enabled:
        script_lines = _HEALTH_SCRIPT.splitlines()
        parts += [
            "    - name: Install redis-health dashboard script",
            "      ansible.builtin.copy:",
            "        dest: /usr/local/bin/opensible-redis-health.py",
            "        owner: root",
            "        group: root",
            "        mode: '0755'",
            "        content: |",
        ]
        for line in script_lines:
            parts.append("          " + line if line else "          ")
        parts += [
            "    - name: Install redis-health systemd unit",
            "      ansible.builtin.copy:",
            "        dest: /etc/systemd/system/opensible-redis-health.service",
            "        owner: root",
            "        group: root",
            "        mode: '0644'",
            "        content: |",
            "          [Unit]",
            f"          Description=OpenSible Redis/Sentinel HTTP health dashboard ({cluster_id})",
            "          After=network-online.target {{ redis_svc_server }} {{ redis_svc_sentinel }}",
            "          Wants=network-online.target",
            "          [Service]",
            "          Type=simple",
            f"          Environment=HTTP_PORT={health_http_port}",
            f"          Environment=REDIS_PORT={port}",
            f"          Environment=SENTINEL_PORT={sentinel_port}",
            f"          Environment=PRIMARY_NAME={primary_name}",
            f"          Environment=CLUSTER_ID={cluster_id}",
            f"          Environment=REDIS_CLI={cli_bin}",
            *([f"          Environment=REDIS_USER={acl_username}"] if acl_username else []),
            *([f"          Environment=REDIS_PASS={acl_password if acl_username else requirepass}"] if (acl_password if acl_username else requirepass) else []),
            "          ExecStart=/usr/bin/env python3 /usr/local/bin/opensible-redis-health.py",
            "          Restart=on-failure",
            "          RestartSec=3",
            "          User=root",
            "          [Install]",
            "          WantedBy=multi-user.target",
            "      register: _redis_health_unit",
            "    - name: Ensure python3 is present for health service",
            "      ansible.builtin.package:",
            "        name: python3",
            "        state: present",
            "      failed_when: false",
            "    - name: Enable and start redis-health service",
            "      ansible.builtin.systemd:",
            "        name: opensible-redis-health.service",
            "        enabled: true",
            "        state: restarted",
            "        daemon_reload: true",
            f"    - name: Wait for health HTTP port {health_http_port} to accept connections",
            "      ansible.builtin.wait_for:",
            "        host: 127.0.0.1",
            f"        port: {health_http_port}",
            "        timeout: 30",
            "    - name: Report health dashboard URL",
            "      ansible.builtin.debug:",
            f"        msg: \"Health dashboard: http://{{{{ ansible_host | default(inventory_hostname) }}}}:{health_http_port}/  (JSON: /health.json)\"",
            "",
        ]

    # ------------------------------------------------------------------ #
    # PLAY 2 — Sentinel-side verification (query current primary)
    # ------------------------------------------------------------------ #
    parts += [
        "- name: Verify Sentinel view of the cluster",
        f"  hosts: {play_hosts}",
        f"  become: {become}",
        "  gather_facts: false",
        "  run_once: true",
        "  vars:",
        f"    redis_primary_name: {yaml_str(primary_name)}",
        f"    redis_sentinel_port: {sentinel_port}",
        f"    redis_cli_bin: {yaml_str(cli_bin)}",
        f"    redis_requirepass: {yaml_str(requirepass)}",
        f"    redis_expected_node_count: {len(nodes) if nodes else 0}",
        f"    redis_svc_server: {yaml_str(svc_server)}",
        f"    redis_down_after_ms: {down_after}",
        f"    redis_failover_timeout_ms: {failover_timeout}",
        "  tasks:",
        "    - name: Ask Sentinel who the primary is",
        "      ansible.builtin.command: >-",
        "        {{ redis_cli_bin }} -h 127.0.0.1 -p {{ redis_sentinel_port }}",
        "        SENTINEL get-master-addr-by-name {{ redis_primary_name }}",
        "      register: sentinel_primary",
        "      changed_when: false",
        "      retries: 6",
        "      delay: 5",
        "      until: sentinel_primary.rc == 0 and sentinel_primary.stdout | length > 0",
        "    - name: Ask Sentinel for quorum status (ckquorum)",
        "      ansible.builtin.command: >-",
        "        {{ redis_cli_bin }} -h 127.0.0.1 -p {{ redis_sentinel_port }}",
        "        SENTINEL ckquorum {{ redis_primary_name }}",
        "      register: sentinel_ckquorum",
        "      changed_when: false",
        "      retries: 12",
        "      delay: 5",
        "      until: sentinel_ckquorum.rc == 0 and 'OK' in (sentinel_ckquorum.stdout | default(''))",
        "      failed_when: false",
        "    - name: Ask Sentinel for replica list",
        "      ansible.builtin.command: >-",
        "        {{ redis_cli_bin }} -h 127.0.0.1 -p {{ redis_sentinel_port }}",
        "        SENTINEL replicas {{ redis_primary_name }}",
        "      register: sentinel_replicas",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Ask Sentinel for peer sentinel list",
        "      ansible.builtin.command: >-",
        "        {{ redis_cli_bin }} -h 127.0.0.1 -p {{ redis_sentinel_port }}",
        "        SENTINEL sentinels {{ redis_primary_name }}",
        "      register: sentinel_peers",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Verify primary sees the expected replicas connected",
        "      when: (redis_expected_node_count | default(0) | int) > 1",
        "      ansible.builtin.shell: |",
        "        set +e",
        "        primary=\"{{ sentinel_primary.stdout_lines[0] }}\"",
        "        port=\"{{ sentinel_primary.stdout_lines[1] }}\"",
        "        {{ redis_cli_bin }} -h \"$primary\" -p \"$port\" \\",
        "          {% if redis_requirepass %}-a {{ redis_requirepass }} --no-auth-warning{% endif %} \\",
        "          INFO replication | tr -d '\\r'",
        "      register: _redis_primary_info",
        "      changed_when: false",
        "      retries: 24",
        "      delay: 5",
        "      until: >-",
        "        _redis_primary_info.rc == 0 and",
        "        ('role:master' in _redis_primary_info.stdout) and",
        "        ((_redis_primary_info.stdout | regex_search('connected_slaves:([0-9]+)', '\\\\1') | first | default('0') | int)",
        "          >= ((redis_expected_node_count | int) - 1))",
        "    - name: Cluster HA summary",
        "      ansible.builtin.debug:",
        "        msg:",
        f"          - \"{flavor} + Sentinel cluster '{cluster_id}' is up.\"",
        "          - \"Current primary: {{ sentinel_primary.stdout_lines }}\"",
        "          - \"Sentinel ckquorum: {{ sentinel_ckquorum.stdout | default('n/a') | trim }}\"",
        "          - \"Peer Sentinels seen: {{ (sentinel_peers.stdout_lines | default([]) | select('match','^name$') | list | length) }}\"",
        "          - \"Replicas seen by Sentinel: {{ (sentinel_replicas.stdout_lines | default([]) | select('match','^ip$') | list | length) }}\"",
        "          - \"Primary INFO replication:\"",
        "          - \"{{ (_redis_primary_info.stdout_lines | default([])) | select('match','^(role|connected_slaves|min_slaves_good_slaves|master_repl_offset):') | list }}\"",
        "          - \"Clients should connect via Sentinel on port {{ redis_sentinel_port }}, master name '{{ redis_primary_name }}'.\"",
        "          - \"Failover test: sudo systemctl stop {{ redis_svc_server | default('redis-server') }} on the current primary; watch a peer's Sentinel take over within {{ redis_down_after_ms }}ms + failover-timeout.\"",
        "",
    ]


    return "\n".join(parts)
