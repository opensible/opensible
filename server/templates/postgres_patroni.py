"""Template: PostgreSQL HA cluster with Patroni + etcd (systemd).

Deploys a production-style PostgreSQL HA topology on N nodes (recommended
3 or 5):

  * etcd v3 cluster on every node (embedded DCS for Patroni)
  * PostgreSQL from the PGDG apt/yum repositories (major version selectable)
  * Patroni installed into an isolated Python venv at /opt/patroni,
    managed via systemd. Patroni owns postgres — the OS postgresql
    service is stopped and disabled so Patroni is the sole supervisor.
  * REST API on :8008 for cluster inspection (``patronictl list``).

Clients connect through Patroni's REST-driven leader endpoint (or an
external HAProxy / pgbouncer sitting in front). The first node is the
initial leader; failover is automatic once ``synchronous_mode`` /
replicas are in place.

Marker: 2026-07-postgres-patroni-v1
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


# ---------------------------------------------------------------------------
# Per-node HTTP health dashboard (installed to /usr/local/bin/opensible-patroni-health.py)
# Endpoints:
#   /            HTML dashboard (auto-refresh 5s) — cluster + local node + etcd
#   /health.json JSON payload (200 healthy / 503 degraded)
#   /live        liveness (always 200 if process is up)
# ---------------------------------------------------------------------------
_HEALTH_SCRIPT = r'''#!/usr/bin/env python3
"""OpenSible PostgreSQL HA (Patroni + etcd) HTTP health dashboard."""
import json, os, subprocess, html, socket, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib import request as _rq, error as _er

CFG = {
    "cluster_name": os.environ.get("CLUSTER_NAME", ""),
    "scope": os.environ.get("PATRONI_SCOPE", ""),
    "pg_port": int(os.environ.get("PG_PORT", "5432")),
    "patroni_port": int(os.environ.get("PATRONI_REST_PORT", "8008")),
    "etcd_client_port": int(os.environ.get("ETCD_CLIENT_PORT", "2379")),
    "http_port": int(os.environ.get("HTTP_PORT", "8080")),
    "patroni_config": os.environ.get("PATRONI_CONFIG", "/etc/patroni/patroni.yml"),
    "etcd_endpoints": os.environ.get("ETCD_ENDPOINTS", ""),
}


def run(args, timeout=6, env=None):
    try:
        r = subprocess.run(args, capture_output=True, text=True,
                           timeout=timeout, env=env)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return -1, "", str(e)


def _http_get(url, timeout=4):
    try:
        req = _rq.Request(url, headers={"Accept": "application/json"})
        with _rq.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except _er.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return e.code, body
    except Exception as e:
        return -1, str(e)


def patroni_local():
    code, body = _http_get(f"http://127.0.0.1:{CFG['patroni_port']}/patroni")
    data = {}
    try:
        data = json.loads(body) if body and body.startswith("{") else {}
    except Exception:
        data = {}
    return {"code": code, "raw": body[:4000], "data": data}


def patroni_cluster():
    code, body = _http_get(f"http://127.0.0.1:{CFG['patroni_port']}/cluster")
    data = {}
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}
    return {"code": code, "raw": body[:8000], "data": data}


def patronictl_list():
    rc, out, err = run(["patronictl", "-c", CFG["patroni_config"], "list"])
    return {"ok": rc == 0, "text": (out or err)[:4000]}


def etcd_status():
    endpoints = CFG["etcd_endpoints"] or f"http://127.0.0.1:{CFG['etcd_client_port']}"
    rc, out, err = run(["etcdctl", "--endpoints", endpoints,
                        "endpoint", "status", "--write-out=table"],
                       env={**os.environ, "ETCDCTL_API": "3"})
    return {"ok": rc == 0, "text": (out or err)[:4000]}


def etcd_health():
    endpoints = CFG["etcd_endpoints"] or f"http://127.0.0.1:{CFG['etcd_client_port']}"
    rc, out, err = run(["etcdctl", "--endpoints", endpoints,
                        "endpoint", "health", "--write-out=table"],
                       env={**os.environ, "ETCDCTL_API": "3"})
    return {"ok": rc == 0, "text": (out or err)[:4000]}


def pg_replication():
    # Runs only on the primary; will just error/empty on replicas — that's fine.
    rc, out, err = run([
        "sudo", "-u", "postgres", "psql", "-p", str(CFG["pg_port"]),
        "-Atc",
        "SELECT application_name || '|' || client_addr || '|' || state || '|' || "
        "sync_state || '|' || COALESCE(replay_lag::text,'0') FROM pg_stat_replication",
    ])
    rows = []
    if rc == 0 and out:
        for line in out.splitlines():
            parts = line.split("|")
            if len(parts) >= 5:
                rows.append({
                    "application_name": parts[0], "client_addr": parts[1],
                    "state": parts[2], "sync_state": parts[3], "replay_lag": parts[4],
                })
    return {"ok": rc == 0, "rows": rows, "error": (err or "")[:2000] if rc != 0 else ""}


def gather():
    data = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()),
        "hostname": socket.gethostname(),
        "cluster_name": CFG["cluster_name"],
        "scope": CFG["scope"],
        "pg_port": CFG["pg_port"],
    }
    local = patroni_local()
    cluster = patroni_cluster()
    data["patroni_local"] = local
    data["patroni_cluster"] = cluster
    data["patronictl"] = patronictl_list()
    data["etcd_status"] = etcd_status()
    data["etcd_health"] = etcd_health()

    role = (local.get("data") or {}).get("role") or ""
    state = (local.get("data") or {}).get("state") or ""
    data["role"] = role
    data["state"] = state

    if role in ("master", "primary", "leader"):
        data["pg_replication"] = pg_replication()
    else:
        data["pg_replication"] = {"ok": True, "rows": [], "skipped": True}

    members = (cluster.get("data") or {}).get("members") or []
    data["members"] = members
    leader = next((m for m in members if (m.get("role") in ("leader", "master", "primary"))), None)
    data["leader_name"] = (leader or {}).get("name", "")
    data["leader_host"] = (leader or {}).get("host", "")

    healthy = (
        local["code"] in (200, 503)  # patroni answered
        and cluster["code"] == 200
        and data["etcd_health"]["ok"]
        and state in ("running", "streaming", "in archive recovery", "")
    )
    data["healthy"] = healthy
    return data


HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>Patroni Health - {host}</title>
<meta http-equiv="refresh" content="5">
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0b0f14;color:#e6edf3;margin:0;padding:24px}}
 h1{{margin:0 0 4px 0}} h2{{margin:24px 0 8px;color:#7ee787;font-size:16px}}
 .card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px 18px;margin:10px 0}}
 pre{{background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:10px;overflow:auto;white-space:pre-wrap;font-size:12px;max-height:340px}}
 table{{width:100%;border-collapse:collapse;font-size:13px}}
 th,td{{padding:6px 10px;border-bottom:1px solid #30363d;text-align:left}}
 th{{color:#8b949e;font-weight:600}}
 .kv{{display:grid;grid-template-columns:200px 1fr;gap:4px 16px;font-size:14px}}
 .badge{{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;font-weight:600}}
 .b-ok{{background:#0f3320;color:#3fb950}} .b-bad{{background:#3a0f10;color:#f85149}}
 .b-warn{{background:#3a2a0f;color:#d29922}}
 code{{color:#79c0ff}}
</style></head><body>
<h1>PostgreSQL HA (Patroni) <span class="badge {bcls}">{bstat}</span></h1>
<div style="color:#8b949e">{host} - refreshed {ts}</div>

<div class="card">
 <h2>Cluster</h2>
 <div class="kv">
  <div>Cluster name</div><div><code>{cname}</code></div>
  <div>Patroni scope</div><div><code>{scope}</code></div>
  <div>This node role</div><div><span class="badge {rcls}">{role}</span> <span style="color:#8b949e">state: {state}</span></div>
  <div>Current leader</div><div><code>{leader_name}</code> @ <code>{leader_host}</code></div>
  <div>PostgreSQL port</div><div><code>{pg_port}</code></div>
 </div>
</div>

<div class="card">
 <h2>Members ({mcount})</h2>
 <table><thead><tr>
  <th>Name</th><th>Role</th><th>State</th><th>Host</th><th>Lag (MB)</th><th>Timeline</th>
 </tr></thead><tbody>
{members_rows}
 </tbody></table>
</div>

<div class="card"><h2>patronictl list</h2><pre>{pctl}</pre></div>
<div class="card"><h2>pg_stat_replication (from local primary)</h2><pre>{repl}</pre></div>
<div class="card"><h2>etcd endpoint health</h2><pre>{ehealth}</pre></div>
<div class="card"><h2>etcd endpoint status</h2><pre>{estatus}</pre></div>

<div style="color:#8b949e;font-size:12px;margin-top:20px">
 JSON: <a style="color:#79c0ff" href="/health.json">/health.json</a> - Liveness: <a style="color:#79c0ff" href="/live">/live</a>
</div>
</body></html>"""


def render_html(d):
    def esc(x):
        return html.escape(str(x)) if x is not None else ""

    def badge(ok):
        return ("b-ok", "OK") if ok else ("b-bad", "DEGRADED")

    def role_badge(role):
        r = (role or "").lower()
        if r in ("leader", "master", "primary"):
            return ("b-ok", role or "leader")
        if r in ("replica", "sync_standby", "standby"):
            return ("b-warn", role or "replica")
        return ("b-bad", role or "unknown")

    bcls, bstat = badge(d["healthy"])
    rcls, rlabel = role_badge(d.get("role"))

    rows = []
    for m in (d.get("members") or []):
        rows.append(
            "  <tr>"
            f"<td><code>{esc(m.get('name'))}</code></td>"
            f"<td>{esc(m.get('role'))}</td>"
            f"<td>{esc(m.get('state'))}</td>"
            f"<td><code>{esc(m.get('host'))}</code></td>"
            f"<td>{esc(m.get('lag') if m.get('lag') is not None else '-')}</td>"
            f"<td>{esc(m.get('timeline') if m.get('timeline') is not None else '-')}</td>"
            "</tr>"
        )
    members_rows = "\n".join(rows) or "  <tr><td colspan=6 style='color:#8b949e'>(no members)</td></tr>"

    repl = d.get("pg_replication") or {}
    if repl.get("skipped"):
        repl_text = "(not the primary — replication view only on leader)"
    elif repl.get("rows"):
        lines = [f"{r['application_name']:20s}  {r['client_addr']:20s}  {r['state']:12s}  {r['sync_state']:10s}  lag={r['replay_lag']}"
                 for r in repl["rows"]]
        repl_text = "\n".join(lines)
    else:
        repl_text = repl.get("error") or "(no connected replicas)"

    return HTML.format(
        host=esc(d["hostname"]),
        ts=esc(d["generated_at"]),
        cname=esc(d["cluster_name"] or "-"),
        scope=esc(d["scope"] or "-"),
        role=esc(rlabel),
        state=esc(d.get("state") or "-"),
        leader_name=esc(d.get("leader_name") or "-"),
        leader_host=esc(d.get("leader_host") or "-"),
        pg_port=esc(d.get("pg_port")),
        bcls=bcls, bstat=bstat,
        rcls=rcls,
        mcount=len(d.get("members") or []),
        members_rows=members_rows,
        pctl=esc(d["patronictl"]["text"] or "-"),
        repl=esc(repl_text),
        ehealth=esc(d["etcd_health"]["text"] or "-"),
        estatus=esc(d["etcd_status"]["text"] or "-"),
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a, **k):
        return

    def do_GET(self):
        if self.path == "/live":
            self.send_response(200); self.send_header("Content-Type","text/plain")
            self.end_headers(); self.wfile.write(b"ok\n"); return
        d = gather()
        code = 200 if d["healthy"] else 503
        if self.path.startswith("/health.json"):
            body = json.dumps(d, indent=2).encode()
            self.send_response(code); self.send_header("Content-Type","application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body); return
        body = render_html(d).encode()
        self.send_response(code); self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)


class TS(ThreadingMixIn, HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    TS(("0.0.0.0", CFG["http_port"]), Handler).serve_forever()
'''


def _health_tasks(cluster_id: str, scope: str, pg_port: int, rest_port: int,
                  etcd_client_port: int, http_port: int,
                  etcd_endpoints_expr: str) -> List[str]:
    """Ansible tasks installing the per-node HTTP health dashboard."""
    lines: List[str] = [
        "    - name: Ensure python3 is present for Patroni health service",
        "      ansible.builtin.package:",
        "        name: python3",
        "        state: present",
        "      failed_when: false",
        "    - name: Install patroni-health dashboard script",
        "      ansible.builtin.copy:",
        "        dest: /usr/local/bin/opensible-patroni-health.py",
        "        owner: root",
        "        group: root",
        "        mode: '0755'",
        "        content: |",
    ]
    script_lines = _HEALTH_SCRIPT.splitlines()
    for idx, l in enumerate(script_lines):
        prefix = "          "
        if idx == 0:
            lines.append(prefix + "{% raw %}" + l)
        else:
            lines.append(prefix + l if l else prefix)
    lines.append("          {% endraw %}")
    lines += [
        "    - name: Install patroni-health systemd unit",
        "      ansible.builtin.copy:",
        "        dest: /etc/systemd/system/opensible-patroni-health.service",
        "        owner: root",
        "        group: root",
        "        mode: '0644'",
        "        content: |",
        "          [Unit]",
        f"          Description=OpenSible Patroni HTTP health dashboard ({cluster_id})",
        "          After=network-online.target patroni.service etcd.service",
        "          Wants=network-online.target",
        "          [Service]",
        "          Type=simple",
        f"          Environment=HTTP_PORT={http_port}",
        f"          Environment=CLUSTER_NAME={cluster_id}",
        f"          Environment=PATRONI_SCOPE={scope}",
        f"          Environment=PG_PORT={pg_port}",
        f"          Environment=PATRONI_REST_PORT={rest_port}",
        f"          Environment=ETCD_CLIENT_PORT={etcd_client_port}",
        "          Environment=PATRONI_CONFIG=/etc/patroni/patroni.yml",
        f"          Environment=ETCD_ENDPOINTS={etcd_endpoints_expr}",
        "          ExecStart=/usr/bin/env python3 /usr/local/bin/opensible-patroni-health.py",
        "          Restart=on-failure",
        "          RestartSec=3",
        "          User=root",
        "          [Install]",
        "          WantedBy=multi-user.target",
        "      register: _patroni_health_unit",
        "    - name: Enable and start patroni-health service",
        "      ansible.builtin.systemd:",
        "        name: opensible-patroni-health.service",
        "        enabled: true",
        "        state: restarted",
        "        daemon_reload: true",
        f"    - name: Wait for Patroni health HTTP port {http_port}",
        "      ansible.builtin.wait_for:",
        "        host: 127.0.0.1",
        f"        port: {http_port}",
        "        timeout: 30",
        "    - name: Report Patroni health dashboard URL",
        "      ansible.builtin.debug:",
        f"        msg: \"Patroni health dashboard: http://{{{{ ansible_host | default(inventory_hostname) }}}}:{http_port}/  (JSON: /health.json)\"",
    ]
    return lines





TEMPLATE = {
    "id": "postgres-patroni",
    "name": "PostgreSQL HA (Patroni + etcd)",
    "category": "Databases",
    "icon": "database",
    "description": (
        "PostgreSQL HA cluster managed by Patroni with an embedded etcd v3 "
        "DCS. Installs PostgreSQL from PGDG, runs Patroni from a pinned "
        "Python venv, and manages everything via systemd. Add each node "
        "under 'Cluster nodes' — the first entry becomes the initial "
        "leader; the rest are streaming replicas. Use 3+ nodes for real HA."
    ),
    "tags": ["postgres", "patroni", "etcd", "ha", "systemd", "replication"],
    "variables": [
        # ---------- Cluster identity ----------
        {"name": "cluster_id", "label": "Cluster name",
         "type": "string", "default": "opensible-pg",
         "help": "Free-form label. Used for Patroni scope and filenames."},
        {"name": "pg_version", "label": "PostgreSQL major version",
         "type": "select", "default": "16",
         "options": [
             {"label": "PostgreSQL 17", "value": "17"},
             {"label": "PostgreSQL 16 (recommended)", "value": "16"},
             {"label": "PostgreSQL 15", "value": "15"},
             {"label": "PostgreSQL 14", "value": "14"},
         ]},

        # ---------- Hosts / HA ----------
        {"name": "ssh_user_default", "label": "Default SSH user",
         "type": "string", "default": "root"},
        {"name": "ssh_port_default", "label": "Default SSH port",
         "type": "number", "default": 22},
        {"name": "nodes", "label": "Cluster nodes (first = initial leader)",
         "type": "nodes", "required": False,
         "help": "Add 3 nodes for real HA (Patroni + etcd both need a quorum). The first entry becomes the initial leader.",
         "default": [
             {"name": "pg-1", "ip": "", "ssh_user": "", "ssh_port": ""},
             {"name": "pg-2", "ip": "", "ssh_user": "", "ssh_port": ""},
             {"name": "pg-3", "ip": "", "ssh_user": "", "ssh_port": ""},
         ]},

        # ---------- Networking ----------
        {"name": "listen_address", "label": "PostgreSQL listen address",
         "type": "string", "default": "0.0.0.0",
         "help": "0.0.0.0 to accept remote connections; 127.0.0.1 for localhost-only."},
        {"name": "pg_port", "label": "PostgreSQL port",
         "type": "number", "default": 5432},
        {"name": "patroni_rest_port", "label": "Patroni REST API port",
         "type": "number", "default": 8008},
        {"name": "etcd_client_port", "label": "etcd client port",
         "type": "number", "default": 2379},
        {"name": "etcd_peer_port", "label": "etcd peer port",
         "type": "number", "default": 2380},
        {"name": "announce_host", "label": "Advertised host (per node)",
         "type": "string",
         "default": "{{ ansible_host | default(inventory_hostname) }}",
         "help": "Jinja expression evaluated per host. Used for etcd peers and Patroni connect_address."},
        {"name": "health_http_enabled", "label": "Enable HTTP health dashboard",
         "type": "boolean", "default": True,
         "help": "Install a small HTTP service on each node exposing / (HTML dashboard), /health.json and /live with full Patroni + etcd + replication status."},
        {"name": "health_http_port", "label": "Health HTTP port",
         "type": "number", "default": 8080,
         "help": "Port for the health dashboard. Visit http://<node-ip>:<port>/ from your browser."},

        # ---------- Auth ----------
        {"name": "superuser_password", "label": "postgres superuser password",
         "type": "password", "required": True, "default": "",
         "help": "Used for the postgres role and Patroni bootstrap."},
        {"name": "replication_password", "label": "Replication user password",
         "type": "password", "required": True, "default": "",
         "help": "Used by streaming replicas (role: replicator)."},
        {"name": "rewind_password", "label": "pg_rewind user password",
         "type": "password", "required": False, "default": "",
         "help": "Optional — used by Patroni to fast-rewind a demoted primary. Falls back to superuser_password if blank."},

        # ---------- Patroni tuning ----------
        {"name": "synchronous_mode", "label": "Enable synchronous replication",
         "type": "boolean", "default": False,
         "help": "Slower writes but zero data loss on failover. Requires 3+ nodes to remain writable if one dies."},
        {"name": "maximum_lag_on_failover", "label": "maximum_lag_on_failover (bytes)",
         "type": "number", "default": 1048576,
         "help": "Replicas lagging more than this are ineligible to be promoted."},
        {"name": "ttl", "label": "Patroni leader TTL (seconds)",
         "type": "number", "default": 30},
        {"name": "loop_wait", "label": "Patroni loop_wait (seconds)",
         "type": "number", "default": 10},
        {"name": "retry_timeout", "label": "Patroni retry_timeout (seconds)",
         "type": "number", "default": 10},

        # ---------- PostgreSQL tuning ----------
        {"name": "max_connections", "label": "max_connections",
         "type": "number", "default": 200},
        {"name": "shared_buffers", "label": "shared_buffers",
         "type": "string", "default": "256MB"},
        {"name": "wal_level", "label": "wal_level",
         "type": "string", "default": "replica"},
        {"name": "extra_hba", "label": "Extra pg_hba.conf rules (one per line)",
         "type": "code", "language": "plaintext", "rows": 4,
         "default": "# host    all    all    10.0.0.0/8    scram-sha-256\n",
         "help": "Appended to Patroni-managed pg_hba entries. One rule per line."},

        # ---------- Ops ----------
        {"name": "etcd_version", "label": "etcd version",
         "type": "string", "default": "3.5.16",
         "help": "Downloaded from github.com/etcd-io/etcd releases (tarball)."},
        {"name": "patroni_version", "label": "Patroni pip version",
         "type": "string", "default": "3.3.2"},
        {"name": "open_firewall", "label": "Open ports in UFW/firewalld",
         "type": "boolean", "default": True},
        {"name": "become", "label": "Run as sudo (become)",
         "type": "boolean", "default": True},
        VAULT_FILES_VARIABLE,
    ],
}


def suggested_filename(values: Dict[str, Any]) -> str:
    stem = slugify(values.get("cluster_id"), "postgres")
    return f"{stem}-postgres-patroni.yml"


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
        name = str(n.get("name") or f"pg-{i+1}").strip() or f"pg-{i+1}"
        user = str(n.get("ssh_user") or default_user or "root").strip() or "root"
        try:
            port = int(n.get("ssh_port") or default_port or 22)
        except Exception:
            port = 22
        out.append({
            "name": name,
            "node_slug": slugify(name, f"pg-{i+1}") or f"pg-{i+1}",
            "ip": ip,
            "ssh_user": user,
            "ssh_port": port,
            "index": i + 1,
        })
    return out


def render(values: Dict[str, Any], targets: Dict[str, Any]) -> str:
    become = "true" if values.get("become", True) else "false"

    cluster_id = str(values.get("cluster_id") or "opensible-pg").strip() or "opensible-pg"
    pg_version = str(values.get("pg_version") or "16").strip() or "16"
    listen = values.get("listen_address") or "0.0.0.0"
    pg_port = int(values.get("pg_port") or 5432)
    rest_port = int(values.get("patroni_rest_port") or 8008)
    etcd_client_port = int(values.get("etcd_client_port") or 2379)
    etcd_peer_port = int(values.get("etcd_peer_port") or 2380)
    announce = values.get("announce_host") or "{{ ansible_host | default(inventory_hostname) }}"

    su_pw = str(values.get("superuser_password") or "")
    repl_pw = str(values.get("replication_password") or "")
    rewind_pw = str(values.get("rewind_password") or "") or su_pw

    sync = bool(values.get("synchronous_mode", False))
    max_lag = int(values.get("maximum_lag_on_failover") or 1048576)
    ttl = int(values.get("ttl") or 30)
    loop_wait = int(values.get("loop_wait") or 10)
    retry_timeout = int(values.get("retry_timeout") or 10)

    max_conn = int(values.get("max_connections") or 200)
    shared_buffers = values.get("shared_buffers") or "256MB"
    wal_level = values.get("wal_level") or "replica"
    extra_hba = str(values.get("extra_hba") or "")

    etcd_version = str(values.get("etcd_version") or "3.5.16").strip().lstrip("v")
    patroni_version = str(values.get("patroni_version") or "3.3.2").strip()
    open_firewall = bool(values.get("open_firewall", True))
    health_http_enabled = bool(values.get("health_http_enabled", True))
    health_http_port = int(values.get("health_http_port") or 8080)

    nodes = _norm_nodes(
        values.get("nodes"),
        values.get("ssh_user_default") or "root",
        values.get("ssh_port_default") or 22,
    )

    cluster_group = slugify(cluster_id, "pg") + "_nodes"
    scope = slugify(cluster_id, "opensible-pg")

    parts: List[str] = ["---"]
    parts.append(f"# OpenSible postgres-patroni template generation: 2026-07-postgres-patroni-v1")
    parts.append(f"# Rendered from template: {TEMPLATE['name']}")
    parts.append(f"# Cluster: {cluster_id} | pg: {pg_version} | nodes: {len(nodes) if nodes else 'from targets'}")
    parts.append("")

    # ------------------------------------------------------------------ #
    # PLAY 0 — dynamic inventory
    # ------------------------------------------------------------------ #
    if nodes:
        parts += [
            "- name: Register Patroni nodes into a dynamic inventory group",
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
            "        pg_node_name: \"{{ item.name }}\"",
            "        pg_node_slug: \"{{ item.node_slug }}\"",
            "        pg_node_index: \"{{ item.index }}\"",
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
        initial_leader_ip = nodes[0]["ip"]
        etcd_initial_cluster_expr = ",".join(
            f"{n['node_slug']}=http://{n['ip']}:{etcd_peer_port}" for n in nodes
        )
        etcd_endpoints_expr = ",".join(
            f"http://{n['ip']}:{etcd_client_port}" for n in nodes
        )
    else:
        play_hosts = render_hosts(targets)
        initial_leader_ip = "{{ ansible_play_hosts[0] }}"
        etcd_initial_cluster_expr = (
            "{% for h in ansible_play_hosts %}"
            f"{{{{ hostvars[h].inventory_hostname_short | default(h) }}}}=http://{{{{ h }}}}:{etcd_peer_port}"
            "{% if not loop.last %},{% endif %}{% endfor %}"
        )
        etcd_endpoints_expr = (
            "{% for h in ansible_play_hosts %}"
            f"http://{{{{ h }}}}:{etcd_client_port}"
            "{% if not loop.last %},{% endif %}{% endfor %}"
        )

    # ------------------------------------------------------------------ #
    # PLAY 1 — install prerequisites, PostgreSQL packages (no bootstrap),
    # etcd tarball + systemd unit, Patroni venv + systemd unit.
    # ------------------------------------------------------------------ #
    parts += [
        f"- name: Deploy PostgreSQL {pg_version} + Patroni + etcd on every node",
        f"  hosts: {play_hosts}",
        f"  become: {become}",
        "  gather_facts: true",
        "  any_errors_fatal: true",
        *vars_files_lines(parse_vault_files(values.get("vault_files"))),
        "  vars:",
        f"    pg_cluster_id: {yaml_str(cluster_id)}",
        f"    pg_scope: {yaml_str(scope)}",
        f"    pg_version: {yaml_str(pg_version)}",
        f"    pg_listen: {yaml_str(listen)}",
        f"    pg_port: {pg_port}",
        f"    patroni_rest_port: {rest_port}",
        f"    etcd_client_port: {etcd_client_port}",
        f"    etcd_peer_port: {etcd_peer_port}",
        f"    pg_announce_host: \"{announce}\"",
        f"    pg_initial_leader_ip: {yaml_str(initial_leader_ip)}",
        f"    pg_superuser_password: {yaml_str(su_pw)}",
        f"    pg_replication_password: {yaml_str(repl_pw)}",
        f"    pg_rewind_password: {yaml_str(rewind_pw)}",
        f"    pg_sync_mode: {'true' if sync else 'false'}",
        f"    pg_max_lag_on_failover: {max_lag}",
        f"    pg_ttl: {ttl}",
        f"    pg_loop_wait: {loop_wait}",
        f"    pg_retry_timeout: {retry_timeout}",
        f"    pg_max_connections: {max_conn}",
        f"    pg_shared_buffers: {yaml_str(shared_buffers)}",
        f"    pg_wal_level: {yaml_str(wal_level)}",
        f"    etcd_version: {yaml_str(etcd_version)}",
        f"    patroni_version: {yaml_str(patroni_version)}",
        f"    etcd_initial_cluster: \"{etcd_initial_cluster_expr}\"",
        f"    etcd_endpoints: \"{etcd_endpoints_expr}\"",
        "  tasks:",

        # ---------- Preflight / facts ----------
        "    - name: Resolve node slug for etcd + patroni identity",
        "      ansible.builtin.set_fact:",
        "        pg_node_slug: \"{{ pg_node_slug | default(inventory_hostname_short) | default(inventory_hostname) }}\"",
        "        pg_node_index: \"{{ pg_node_index | default(ansible_play_hosts.index(inventory_hostname) + 1) | int }}\"",
        "    - name: Decide whether this host is the initial leader",
        "      ansible.builtin.set_fact:",
        "        pg_is_initial_leader: \"{{ (ansible_host | default(inventory_hostname)) == pg_initial_leader_ip }}\"",

        # ---------- Refresh apt / package prereqs ----------
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
        "          - gnupg",
        "          - tar",
        "          - python3",
        "          - python3-venv",
        "          - python3-pip",
        "          - python3-psycopg2",
        "        state: present",
        "      failed_when: false",

        # ---------- PGDG repo (Debian/Ubuntu) ----------
        "    - name: Configure PGDG apt repo (Debian/Ubuntu)",
        "      when: ansible_os_family == 'Debian'",
        "      block:",
        "        - name: Ensure apt keyring dir",
        "          ansible.builtin.file:",
        "            path: /etc/apt/keyrings",
        "            state: directory",
        "            mode: '0755'",
        "        - name: Fetch PGDG signing key",
        "          ansible.builtin.get_url:",
        "            url: https://www.postgresql.org/media/keys/ACCC4CF8.asc",
        "            dest: /etc/apt/keyrings/pgdg.asc",
        "            mode: '0644'",
        "        - name: Write PGDG apt source",
        "          ansible.builtin.copy:",
        "            dest: /etc/apt/sources.list.d/pgdg.list",
        "            mode: '0644'",
        "            content: |",
        "              deb [signed-by=/etc/apt/keyrings/pgdg.asc] http://apt.postgresql.org/pub/repos/apt {{ ansible_distribution_release }}-pgdg main",
        "        - name: apt-get update after adding PGDG",
        "          ansible.builtin.apt:",
        "            update_cache: true",

        # ---------- PGDG repo (RHEL family) ----------
        "    - name: Configure PGDG yum repo (RHEL/Rocky)",
        "      when: ansible_os_family == 'RedHat'",
        "      ansible.builtin.dnf:",
        "        name: \"https://download.postgresql.org/pub/repos/yum/reporpms/EL-{{ ansible_distribution_major_version }}-x86_64/pgdg-redhat-repo-latest.noarch.rpm\"",
        "        state: present",
        "        disable_gpg_check: true",
        "      failed_when: false",
        "    - name: Disable built-in postgresql module (RHEL)",
        "      when: ansible_os_family == 'RedHat'",
        "      ansible.builtin.command: dnf -qy module disable postgresql",
        "      register: _pg_mod_disable",
        "      changed_when: '\"Nothing to do\" not in _pg_mod_disable.stdout'",
        "      failed_when: false",

        # ---------- Install PostgreSQL packages ----------
        "    - name: Install PostgreSQL {{ pg_version }} packages (Debian/Ubuntu)",
        "      when: ansible_os_family == 'Debian'",
        "      ansible.builtin.apt:",
        "        name:",
        "          - postgresql-{{ pg_version }}",
        "          - postgresql-client-{{ pg_version }}",
        "          - postgresql-contrib",
        "        state: present",
        "        update_cache: true",
        "    - name: Install PostgreSQL {{ pg_version }} packages (RHEL/Rocky)",
        "      when: ansible_os_family == 'RedHat'",
        "      ansible.builtin.dnf:",
        "        name:",
        "          - postgresql{{ pg_version }}",
        "          - postgresql{{ pg_version }}-server",
        "          - postgresql{{ pg_version }}-contrib",
        "        state: present",

        # ---------- Disable OS postgres service (Patroni owns it) ----------
        "    - name: Stop and disable OS-managed postgresql service (Patroni will own it)",
        "      ansible.builtin.systemd:",
        "        name: \"{{ item }}\"",
        "        state: stopped",
        "        enabled: false",
        "      loop:",
        "        - postgresql",
        "        - \"postgresql@{{ pg_version }}-main.service\"",
        "        - \"postgresql-{{ pg_version }}.service\"",
        "      failed_when: false",

        # ---------- postgres user + dirs ----------
        "    - name: Ensure postgres user exists",
        "      ansible.builtin.user:",
        "        name: postgres",
        "        system: true",
        "        shell: /bin/bash",
        "        home: /var/lib/postgresql",
        "        create_home: false",
        "    - name: Ensure Patroni data dir",
        "      ansible.builtin.file:",
        "        path: /var/lib/patroni",
        "        state: directory",
        "        owner: postgres",
        "        group: postgres",
        "        mode: '0700'",
        "    - name: Ensure Patroni config dir",
        "      ansible.builtin.file:",
        "        path: /etc/patroni",
        "        state: directory",
        "        owner: postgres",
        "        group: postgres",
        "        mode: '0750'",
        "    - name: Ensure Patroni log dir",
        "      ansible.builtin.file:",
        "        path: /var/log/patroni",
        "        state: directory",
        "        owner: postgres",
        "        group: postgres",
        "        mode: '0750'",
        "    - name: Wipe any stale data dir from an earlier failed run",
        "      ansible.builtin.file:",
        "        path: /var/lib/patroni/data",
        "        state: absent",
        "      when: not (pg_keep_data | default(false) | bool)",

        # ---------- Resolve PostgreSQL bin dir ----------
        "    - name: Resolve PostgreSQL binary dir",
        "      ansible.builtin.shell: |",
        "        set -e",
        "        if [ -d /usr/lib/postgresql/{{ pg_version }}/bin ]; then",
        "          echo /usr/lib/postgresql/{{ pg_version }}/bin",
        "        elif [ -d /usr/pgsql-{{ pg_version }}/bin ]; then",
        "          echo /usr/pgsql-{{ pg_version }}/bin",
        "        else",
        "          find /usr -maxdepth 5 -type d -name bin -path '*postgres*{{ pg_version }}*' 2>/dev/null | head -n1",
        "        fi",
        "      register: _pg_bindir",
        "      changed_when: false",
        "    - name: Fail if postgres bin dir not found",
        "      ansible.builtin.fail:",
        "        msg: \"Cannot locate PostgreSQL {{ pg_version }} bindir\"",
        "      when: (_pg_bindir.stdout | trim) == ''",
        "    - name: Set pg_bindir fact",
        "      ansible.builtin.set_fact:",
        "        pg_bindir: \"{{ _pg_bindir.stdout | trim }}\"",

        # ---------- Install etcd from tarball ----------
        "    - name: Detect etcd binary version if already installed",
        "      ansible.builtin.shell: |",
        "        /usr/local/bin/etcd --version 2>/dev/null | awk '/etcd Version/ {print $3}' | head -n1",
        "      register: _etcd_have",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Download etcd tarball when missing or version mismatch",
        "      when: (_etcd_have.stdout | trim) != etcd_version",
        "      ansible.builtin.get_url:",
        "        url: \"https://github.com/etcd-io/etcd/releases/download/v{{ etcd_version }}/etcd-v{{ etcd_version }}-linux-{{ 'arm64' if ansible_architecture in ['aarch64','arm64'] else 'amd64' }}.tar.gz\"",
        "        dest: \"/tmp/etcd-v{{ etcd_version }}.tar.gz\"",
        "        mode: '0644'",
        "        timeout: 60",
        "    - name: Unpack and install etcd binaries",
        "      when: (_etcd_have.stdout | trim) != etcd_version",
        "      ansible.builtin.shell: |",
        "        set -e",
        "        cd /tmp",
        "        rm -rf etcd-v{{ etcd_version }}-linux-*",
        "        tar -xzf etcd-v{{ etcd_version }}.tar.gz",
        "        d=$(ls -d etcd-v{{ etcd_version }}-linux-* | head -n1)",
        "        install -m 0755 \"$d/etcd\"    /usr/local/bin/etcd",
        "        install -m 0755 \"$d/etcdctl\" /usr/local/bin/etcdctl",
        "        rm -rf \"$d\" etcd-v{{ etcd_version }}.tar.gz",
        "      args:",
        "        executable: /bin/bash",
        "    - name: Ensure etcd data dir",
        "      ansible.builtin.file:",
        "        path: /var/lib/etcd",
        "        state: directory",
        "        owner: postgres",
        "        group: postgres",
        "        mode: '0700'",
        "    - name: Write etcd systemd unit",
        "      ansible.builtin.copy:",
        "        dest: /etc/systemd/system/etcd.service",
        "        mode: '0644'",
        "        content: |",
        "          [Unit]",
        "          Description=etcd (OpenSible Patroni DCS)",
        "          Documentation=https://etcd.io",
        "          After=network-online.target",
        "          Wants=network-online.target",
        "",
        "          [Service]",
        "          Type=notify",
        "          User=postgres",
        "          Group=postgres",
        "          Restart=on-failure",
        "          RestartSec=5",
        "          LimitNOFILE=65536",
        "          Environment=ETCD_NAME={{ pg_node_slug }}",
        "          Environment=ETCD_DATA_DIR=/var/lib/etcd",
        "          Environment=ETCD_LISTEN_CLIENT_URLS=http://0.0.0.0:{{ etcd_client_port }}",
        "          Environment=ETCD_ADVERTISE_CLIENT_URLS=http://{{ pg_announce_host }}:{{ etcd_client_port }}",
        "          Environment=ETCD_LISTEN_PEER_URLS=http://0.0.0.0:{{ etcd_peer_port }}",
        "          Environment=ETCD_INITIAL_ADVERTISE_PEER_URLS=http://{{ pg_announce_host }}:{{ etcd_peer_port }}",
        "          Environment=ETCD_INITIAL_CLUSTER_TOKEN={{ pg_scope }}-etcd",
        "          Environment=ETCD_INITIAL_CLUSTER_STATE=new",
        "          Environment=ETCD_INITIAL_CLUSTER={{ etcd_initial_cluster }}",
        "          Environment=ETCD_ENABLE_V2=false",
        "          ExecStart=/usr/local/bin/etcd",
        "",
        "          [Install]",
        "          WantedBy=multi-user.target",
        "      register: _etcd_unit",
        "    - name: Reload systemd for etcd unit",
        "      ansible.builtin.systemd:",
        "        daemon_reload: true",
        "      when: _etcd_unit.changed",
        "    - name: Enable and start etcd",
        "      ansible.builtin.systemd:",
        "        name: etcd",
        "        enabled: true",
        "        state: started",

        # ---------- Wait for etcd quorum ----------
        "    - name: Wait for etcd client port on this node",
        "      ansible.builtin.wait_for:",
        "        host: 127.0.0.1",
        "        port: \"{{ etcd_client_port }}\"",
        "        timeout: 60",
        "    - name: Wait for etcd cluster health (run once)",
        "      run_once: true",
        "      ansible.builtin.shell: |",
        "        set -e",
        "        for i in $(seq 1 30); do",
        "          if /usr/local/bin/etcdctl --endpoints={{ etcd_endpoints }} endpoint health >/dev/null 2>&1; then",
        "            /usr/local/bin/etcdctl --endpoints={{ etcd_endpoints }} endpoint health",
        "            exit 0",
        "          fi",
        "          sleep 3",
        "        done",
        "        /usr/local/bin/etcdctl --endpoints={{ etcd_endpoints }} endpoint health",
        "      args:",
        "        executable: /bin/bash",
        "      register: _etcd_health",
        "      changed_when: false",

        # ---------- Install Patroni via pip venv ----------
        "    - name: Create Patroni venv",
        "      ansible.builtin.command: python3 -m venv /opt/patroni",
        "      args:",
        "        creates: /opt/patroni/bin/python",
        "    - name: Upgrade pip inside venv",
        "      ansible.builtin.command: /opt/patroni/bin/pip install --upgrade pip setuptools wheel",
        "      changed_when: false",
        "    - name: Install Patroni + etcd3 + psycopg driver in venv",
        "      ansible.builtin.command: /opt/patroni/bin/pip install \"patroni[etcd3]=={{ patroni_version }}\" \"psycopg[binary]>=3.1\" python-etcd",
        "      register: _patroni_pip",
        "      changed_when: \"'Successfully installed' in _patroni_pip.stdout\"",
        "    - name: Symlink patroni + patronictl into /usr/local/bin",
        "      ansible.builtin.file:",
        "        src: \"/opt/patroni/bin/{{ item }}\"",
        "        dest: \"/usr/local/bin/{{ item }}\"",
        "        state: link",
        "        force: true",
        "      loop:",
        "        - patroni",
        "        - patronictl",

        # ---------- Patroni config ----------
        "    - name: Write patroni.yml",
        "      ansible.builtin.copy:",
        "        dest: /etc/patroni/patroni.yml",
        "        owner: postgres",
        "        group: postgres",
        "        mode: '0640'",
        "        content: |",
        "          scope: {{ pg_scope }}",
        "          namespace: /service/",
        "          name: {{ pg_node_slug }}",
        "          restapi:",
        "            listen: 0.0.0.0:{{ patroni_rest_port }}",
        "            connect_address: {{ pg_announce_host }}:{{ patroni_rest_port }}",
        "          etcd3:",
        "            hosts: {{ etcd_endpoints | regex_replace('http://','') }}",
        "          bootstrap:",
        "            dcs:",
        "              ttl: {{ pg_ttl }}",
        "              loop_wait: {{ pg_loop_wait }}",
        "              retry_timeout: {{ pg_retry_timeout }}",
        "              maximum_lag_on_failover: {{ pg_max_lag_on_failover }}",
        "              synchronous_mode: {{ pg_sync_mode | ternary('true','false') }}",
        "              postgresql:",
        "                use_pg_rewind: true",
        "                use_slots: true",
        "                parameters:",
        "                  max_connections: {{ pg_max_connections }}",
        "                  shared_buffers: {{ pg_shared_buffers }}",
        "                  wal_level: {{ pg_wal_level }}",
        "                  hot_standby: on",
        "                  max_wal_senders: 10",
        "                  max_replication_slots: 10",
        "                  wal_log_hints: on",
        "                  logging_collector: on",
        "                  log_directory: /var/log/patroni",
        "                  log_filename: postgresql-%a.log",
        "            initdb:",
        "              - encoding: UTF8",
        "              - data-checksums",
        "            pg_hba:",
        "              - host replication replicator 0.0.0.0/0 scram-sha-256",
        "              - host all all 0.0.0.0/0 scram-sha-256",
        "              - local all all trust",
        "              - host all all 127.0.0.1/32 scram-sha-256",
        "              - host all all ::1/128 scram-sha-256",
        _indent_hba(extra_hba),
        "            users:",
        "              admin:",
        "                password: {{ pg_superuser_password }}",
        "                options:",
        "                  - createrole",
        "                  - createdb",
        "          postgresql:",
        "            listen: {{ pg_listen }}:{{ pg_port }}",
        "            connect_address: {{ pg_announce_host }}:{{ pg_port }}",
        "            data_dir: /var/lib/patroni/data",
        "            bin_dir: {{ pg_bindir }}",
        "            pgpass: /tmp/pgpass0",
        "            authentication:",
        "              replication:",
        "                username: replicator",
        "                password: {{ pg_replication_password }}",
        "              superuser:",
        "                username: postgres",
        "                password: {{ pg_superuser_password }}",
        "              rewind:",
        "                username: rewind_user",
        "                password: {{ pg_rewind_password }}",
        "            parameters:",
        "              unix_socket_directories: /var/run/postgresql",
        "          tags:",
        "            nofailover: false",
        "            noloadbalance: false",
        "            clonefrom: false",
        "            nosync: false",
        "      register: _patroni_conf",

        # ---------- Patroni systemd unit ----------
        "    - name: Ensure postgres socket dir",
        "      ansible.builtin.file:",
        "        path: /var/run/postgresql",
        "        state: directory",
        "        owner: postgres",
        "        group: postgres",
        "        mode: '0755'",
        "    - name: Write Patroni systemd unit",
        "      ansible.builtin.copy:",
        "        dest: /etc/systemd/system/patroni.service",
        "        mode: '0644'",
        "        content: |",
        "          [Unit]",
        "          Description=Patroni (OpenSible PostgreSQL HA)",
        "          Documentation=https://patroni.readthedocs.io",
        "          After=network-online.target etcd.service",
        "          Wants=network-online.target etcd.service",
        "",
        "          [Service]",
        "          Type=simple",
        "          User=postgres",
        "          Group=postgres",
        "          ExecStart=/usr/local/bin/patroni /etc/patroni/patroni.yml",
        "          KillMode=process",
        "          Restart=on-failure",
        "          RestartSec=5",
        "          LimitNOFILE=65536",
        "          TimeoutStopSec=30",
        "",
        "          [Install]",
        "          WantedBy=multi-user.target",
        "      register: _patroni_unit",
        "    - name: Reload systemd for patroni unit",
        "      ansible.builtin.systemd:",
        "        daemon_reload: true",
        "      when: _patroni_unit.changed",
    ]

    # ---------- Firewall ----------
    if open_firewall:
        parts += [
            "    - name: Open PostgreSQL + Patroni + etcd ports (ufw, if active)",
            "      ansible.builtin.shell: |",
            "        set -e",
            "        command -v ufw >/dev/null 2>&1 || exit 0",
            "        ufw status | grep -q 'Status: active' || exit 0",
            f"        ufw allow {pg_port}/tcp || true",
            f"        ufw allow {rest_port}/tcp || true",
            f"        ufw allow {etcd_client_port}/tcp || true",
            f"        ufw allow {etcd_peer_port}/tcp || true",
            *([f"        ufw allow {health_http_port}/tcp || true"] if health_http_enabled else []),
            "      changed_when: false",
            "      failed_when: false",
            "    - name: Open PostgreSQL + Patroni + etcd ports (firewalld, if active)",
            "      ansible.builtin.shell: |",
            "        set -e",
            "        command -v firewall-cmd >/dev/null 2>&1 || exit 0",
            "        firewall-cmd --state >/dev/null 2>&1 || exit 0",
            f"        firewall-cmd --permanent --add-port={pg_port}/tcp || true",
            f"        firewall-cmd --permanent --add-port={rest_port}/tcp || true",
            f"        firewall-cmd --permanent --add-port={etcd_client_port}/tcp || true",
            f"        firewall-cmd --permanent --add-port={etcd_peer_port}/tcp || true",
            *([f"        firewall-cmd --permanent --add-port={health_http_port}/tcp || true"] if health_http_enabled else []),
            "        firewall-cmd --reload || true",
            "      changed_when: false",
            "      failed_when: false",
        ]

    # ---------- Start Patroni: leader first, replicas after ----------
    parts += [
        "    - name: Start Patroni on the initial leader first",
        "      when: pg_is_initial_leader | bool",
        "      block:",
        "        - name: Enable + start patroni (leader)",
        "          ansible.builtin.systemd:",
        "            name: patroni",
        "            enabled: true",
        "            state: started",
        "            daemon_reload: true",
        "        - name: Restart patroni if config changed (leader)",
        "          ansible.builtin.systemd:",
        "            name: patroni",
        "            state: restarted",
        "          when: _patroni_conf.changed and not (_patroni_unit.changed)",
        "        - name: Wait for Patroni REST /health on the leader",
        "          ansible.builtin.uri:",
        "            url: \"http://127.0.0.1:{{ patroni_rest_port }}/health\"",
        "            status_code: [200, 503]",
        "          register: _leader_health",
        "          retries: 60",
        "          delay: 5",
        "          until: _leader_health.status == 200",
        "      rescue:",
        "        - name: Dump patroni journal on leader start failure",
        "          ansible.builtin.command: journalctl -u patroni -n 200 --no-pager",
        "          register: _patroni_journal",
        "          changed_when: false",
        "          failed_when: false",
        "        - name: Show patroni diagnostics",
        "          ansible.builtin.debug:",
        "            msg: \"{{ _patroni_journal.stdout_lines | default([]) }}\"",
        "        - name: Re-raise",
        "          ansible.builtin.fail:",
        "            msg: \"Patroni failed to start on the initial leader — see journal above.\"",

        "    - name: Wait for leader Patroni REST to be reachable from replicas",
        "      when: not (pg_is_initial_leader | bool)",
        "      ansible.builtin.wait_for:",
        "        host: \"{{ pg_initial_leader_ip }}\"",
        "        port: \"{{ patroni_rest_port }}\"",
        "        timeout: 300",

        "    - name: Start Patroni on replicas",
        "      when: not (pg_is_initial_leader | bool)",
        "      block:",
        "        - name: Enable + start patroni (replica)",
        "          ansible.builtin.systemd:",
        "            name: patroni",
        "            enabled: true",
        "            state: started",
        "            daemon_reload: true",
        "        - name: Restart patroni if config changed (replica)",
        "          ansible.builtin.systemd:",
        "            name: patroni",
        "            state: restarted",
        "          when: _patroni_conf.changed and not (_patroni_unit.changed)",
        "        - name: Wait for local Patroni REST to answer",
        "          ansible.builtin.uri:",
        "            url: \"http://127.0.0.1:{{ patroni_rest_port }}/patroni\"",
        "            status_code: [200, 503]",
        "          register: _replica_health",
        "          retries: 60",
        "          delay: 5",
        "          until: _replica_health.status in [200, 503]",
        "      rescue:",
        "        - name: Dump patroni journal on replica start failure",
        "          ansible.builtin.command: journalctl -u patroni -n 200 --no-pager",
        "          register: _patroni_replica_journal",
        "          changed_when: false",
        "          failed_when: false",
        "        - name: Show patroni replica diagnostics",
        "          ansible.builtin.debug:",
        "            msg: \"{{ _patroni_replica_journal.stdout_lines | default([]) }}\"",
        "        - name: Re-raise",
        "          ansible.builtin.fail:",
        "            msg: \"Patroni failed to start on replica — see journal above.\"",
        "",
    ]

    # ---------- Health dashboard (per node) ----------
    if health_http_enabled:
        parts += _health_tasks(
            cluster_id, scope, pg_port, rest_port,
            etcd_client_port, health_http_port, etcd_endpoints_expr,
        )


    # ------------------------------------------------------------------ #
    # PLAY 2 — cluster verification (run_once from any node)
    # ------------------------------------------------------------------ #
    parts += [
        "- name: Verify Patroni cluster state",
        f"  hosts: {play_hosts}",
        f"  become: {become}",
        "  gather_facts: false",
        "  run_once: true",
        "  tasks:",
        "    - name: Query Patroni cluster view",
        "      ansible.builtin.uri:",
        f"        url: \"http://127.0.0.1:{rest_port}/cluster\"",
        "        return_content: true",
        "      register: _cluster",
        "      retries: 12",
        "      delay: 5",
        "      until: _cluster.status == 200",
        "    - name: Run patronictl list",
        "      ansible.builtin.command: patronictl -c /etc/patroni/patroni.yml list",
        "      register: _pctl",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Cluster summary",
        "      ansible.builtin.debug:",
        "        msg:",
        f"          - \"PostgreSQL HA cluster '{cluster_id}' is up (scope={scope}).\"",
        "          - \"Members (from Patroni REST /cluster):\"",
        "          - \"{{ _cluster.json.members | map(attribute='name') | list }}\"",
        "          - \"patronictl list:\"",
        "          - \"{{ _pctl.stdout_lines | default([]) }}\"",
        f"          - \"Connect: psql -h <leader-ip> -p {pg_port} -U postgres  (leader determined by Patroni REST /leader).\"",
        "",
    ]

    return "\n".join(parts)


def _indent_hba(extra_hba: str) -> str:
    """Render user-supplied extra pg_hba lines as YAML list items under a
    12-space indent (matches the ``pg_hba:`` block above)."""
    lines: List[str] = []
    for raw in (extra_hba or "").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        # Strip any leading YAML list marker the user might have pasted.
        s = s.lstrip("-").strip()
        if not s:
            continue
        # Emit as a quoted list item at the pg_hba block's indent.
        lines.append("              - " + yaml_str(s))
    return "\n".join(lines) if lines else "              # (no extra pg_hba rules)"
