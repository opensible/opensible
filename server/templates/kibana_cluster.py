"""Template: Kibana HA cluster on Docker containers.

Deploys N stateless Kibana nodes (recommended 2 or 3 behind a load
balancer / reverse proxy) all pointing at an existing Elasticsearch
cluster. Each node runs a single `kibana` container managed by a
dedicated systemd unit (`kibana-docker.service`) so the container
survives host reboots and docker daemon restarts.

Highlights:

  * Docker Engine ensured (installed via convenience script if missing).
  * Per-node systemd unit runs `docker run` against the pinned image tag.
  * Every node is configured with the same `ELASTICSEARCH_HOSTS` list,
    so they share the same Elasticsearch backend and can be freely
    load-balanced.
  * A unique `server.uuid` is derived per host so each Kibana node has a
    stable identity in `.kibana*` system indices.
  * Optional built-in HTTP health dashboard on every node exposing an
    HTML view + /health.json (green / yellow / red + Kibana status
    summary + upstream Elasticsearch status).

Marker: 2026-07-kibana-cluster-v2
"""
from __future__ import annotations

from typing import Any, Dict, List
from urllib.parse import urlparse

from ._common import (
    render_hosts,
    yaml_str,
    slugify,
    VAULT_FILES_VARIABLE,
    parse_vault_files,
    vars_files_lines,
)


# ---------------------------------------------------------------------------
# Per-node HTTP health dashboard
# Endpoints:
#   /            HTML dashboard (auto-refresh 5s) — Kibana + upstream ES
#   /health.json JSON payload (200 green/yellow, 503 red / unreachable)
#   /live        liveness (always 200 if process is up)
# ---------------------------------------------------------------------------
_HEALTH_SCRIPT = r'''#!/usr/bin/env python3
"""OpenSible Kibana HA HTTP health dashboard."""
import base64, json, os, html, socket, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib import request as _rq, error as _er

CFG = {
    "cluster_name": os.environ.get("CLUSTER_NAME", ""),
    "kb_host": os.environ.get("KB_HOST", "127.0.0.1"),
    "kb_port": int(os.environ.get("KB_PORT", "5601")),
    "kb_scheme": os.environ.get("KB_SCHEME", "http"),
    "kb_user": os.environ.get("KB_USER", ""),
    "kb_password": os.environ.get("KB_PASSWORD", ""),
    "http_port": int(os.environ.get("HTTP_PORT", "5680")),
}


def _get(base, path, user, password, timeout=4):
    url = f"{base}{path}"
    try:
        req = _rq.Request(url, headers={"Accept": "application/json",
                                         "kbn-xsrf": "true"})
        if user:
            tok = base64.b64encode(f"{user}:{password}".encode()).decode()
            req.add_header("Authorization", f"Basic {tok}")
        ctx = None
        if base.startswith("https://"):
            import ssl as _ssl
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
        with _rq.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except _er.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return e.code, body
    except Exception as e:
        return -1, str(e)


def _json(base, path, user="", password=""):
    code, body = _get(base, path, user, password)
    data = {}
    try:
        data = json.loads(body) if body and body.startswith(("{", "[")) else {}
    except Exception:
        data = {}
    return code, data, body


def _level(state):
    s = (state or "").lower()
    # Kibana status.overall.level: available | degraded | unavailable | critical
    if s in ("available", "green"):
        return "green"
    if s in ("degraded", "yellow"):
        return "yellow"
    if s in ("unavailable", "critical", "red"):
        return "red"
    return "unknown"


def collect():
    base = f"{CFG['kb_scheme']}://{CFG['kb_host']}:{CFG['kb_port']}"
    sc, sj, sb = _json(base, "/api/status", CFG["kb_user"], CFG["kb_password"])
    status = {}
    if isinstance(sj, dict):
        status = sj
    overall = ((status.get("status") or {}).get("overall") or {})
    kb_level = _level(overall.get("level") or overall.get("state"))
    healthy = sc == 200 and kb_level in ("green", "yellow")
    return {
        "hostname": socket.gethostname(),
        "ts": int(time.time()),
        "cluster_name": CFG["cluster_name"],
        "kibana": {"code": sc, "data": status, "raw": sb[:4000],
                   "level": kb_level},
        "healthy": bool(healthy),
    }


def esc(v):
    return html.escape("" if v is None else str(v), quote=True)


def badge(status):
    s = (status or "").lower()
    color = {"green": "#238636", "yellow": "#9e6a03", "red": "#da3633"}.get(s, "#6e7681")
    return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:10px;font-weight:600">{esc(s or "unknown")}</span>'


def render_html(d):
    kb = d["kibana"]["data"] if isinstance(d["kibana"]["data"], dict) else {}
    overall = ((kb.get("status") or {}).get("overall") or {})
    version = kb.get("version") or {}
    name = kb.get("name") or ""
    plugins_rows = ""
    statuses = ((kb.get("status") or {}).get("statuses") or [])
    if isinstance(statuses, list):
        for p in statuses[:200]:
            plugins_rows += (
                "<tr>"
                f"<td>{esc(p.get('id'))}</td>"
                f"<td>{badge(_level(p.get('state')))}</td>"
                f"<td>{esc(p.get('message'))}</td>"
                "</tr>"
            )
    core = ((kb.get("status") or {}).get("core") or {})
    core_rows = ""
    if isinstance(core, dict):
        for k, v in core.items():
            level = _level((v or {}).get("level"))
            summary = (v or {}).get("summary") or ""
            core_rows += (
                "<tr>"
                f"<td>{esc(k)}</td>"
                f"<td>{badge(level)}</td>"
                f"<td>{esc(summary)}</td>"
                "</tr>"
            )
    if not plugins_rows:
        plugins_rows = '<tr><td colspan="3" style="opacity:.7">no plugin status reported</td></tr>'
    if not core_rows:
        core_rows = '<tr><td colspan="3" style="opacity:.7">no core status reported</td></tr>'
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="5">
<title>Kibana — {esc(d['cluster_name'])} @ {esc(d['hostname'])}</title>
<style>
body{{background:#0d1117;color:#c9d1d9;font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:24px}}
h1{{margin:0 0 4px 0;font-size:18px}} h2{{margin:24px 0 8px 0;font-size:14px;color:#8b949e;text-transform:uppercase;letter-spacing:.05em}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin-bottom:16px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{padding:6px 10px;border-bottom:1px solid #21262d;text-align:left}} th{{color:#8b949e;font-weight:600}}
.k{{color:#8b949e}} .v{{color:#c9d1d9;font-weight:600}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px 24px}}
</style></head><body>
<div class="card">
  <h1>Kibana cluster <span class="v">{esc(d['cluster_name'])}</span> — node <span class="v">{esc(d['hostname'])}</span></h1>
  <div style="margin-top:6px">Kibana: {badge(d['kibana']['level'])} &middot; Overall: {badge('green' if d['healthy'] else 'red')}</div>
</div>
<div class="card"><h2>Kibana status</h2>
  <div class="grid">
    <div><span class="k">name</span><br><span class="v">{esc(name)}</span></div>
    <div><span class="k">state</span><br><span class="v">{esc(overall.get('level') or overall.get('state'))}</span></div>
    <div><span class="k">summary</span><br><span class="v">{esc(overall.get('summary'))}</span></div>
    <div><span class="k">version</span><br><span class="v">{esc(version.get('number'))}</span></div>
    <div><span class="k">build</span><br><span class="v">{esc(version.get('build_number'))}</span></div>
    <div><span class="k">snapshot</span><br><span class="v">{esc(version.get('build_snapshot'))}</span></div>
  </div>
</div>
<div class="card"><h2>Core subsystems</h2>
<table><thead><tr><th>subsystem</th><th>level</th><th>summary</th></tr></thead>
<tbody>{core_rows}</tbody></table></div>
<div class="card"><h2>Plugin statuses</h2>
<table><thead><tr><th>plugin</th><th>level</th><th>message</th></tr></thead>
<tbody>{plugins_rows}</tbody></table></div>
<div class="card" style="opacity:.65;font-size:12px">
  Auto-refresh 5s &middot; JSON: <a style="color:#79c0ff" href="/health.json">/health.json</a> &middot;
  Liveness: <a style="color:#79c0ff" href="/live">/live</a>
</div>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a, **kw):
        return

    def do_GET(self):
        if self.path.startswith("/live"):
            self.send_response(200); self.send_header("Content-Type", "text/plain"); self.end_headers()
            self.wfile.write(b"ok"); return
        d = collect()
        if self.path.startswith("/health.json"):
            body = json.dumps(d, default=str).encode()
            code = 200 if d["healthy"] else 503
            self.send_response(code); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(body); return
        body = render_html(d).encode()
        code = 200 if d["healthy"] else 503
        self.send_response(code); self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store"); self.end_headers()
        self.wfile.write(body)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def main():
    srv = ThreadedHTTPServer(("0.0.0.0", CFG["http_port"]), Handler)
    srv.serve_forever()


if __name__ == "__main__":
    main()
'''


def _health_tasks(cluster_id: str, kb_port: int, http_port: int,
                  kb_user: str, kb_password: str) -> List[str]:
    lines: List[str] = [
        "    - name: Ensure python3 present for Kibana health service",
        "      ansible.builtin.package:",
        "        name: python3",
        "        state: present",
        "      failed_when: false",
        "    - name: Install kibana-health dashboard script",
        "      ansible.builtin.copy:",
        "        dest: /usr/local/bin/opensible-kibana-health.py",
        "        owner: root",
        "        group: root",
        "        mode: '0755'",
        "        content: |",
    ]
    for idx, l in enumerate(_HEALTH_SCRIPT.splitlines()):
        prefix = "          "
        if idx == 0:
            lines.append(prefix + "{% raw %}" + l)
        else:
            lines.append(prefix + l if l else prefix)
    lines.append("          {% endraw %}")
    lines += [
        "    - name: Install kibana-health systemd unit",
        "      ansible.builtin.copy:",
        "        dest: /etc/systemd/system/opensible-kibana-health.service",
        "        owner: root",
        "        group: root",
        "        mode: '0644'",
        "        content: |",
        "          [Unit]",
        f"          Description=OpenSible Kibana HTTP health dashboard ({cluster_id})",
        "          After=network-online.target kibana-docker.service",
        "          Wants=network-online.target",
        "          [Service]",
        "          Type=simple",
        f"          Environment=HTTP_PORT={http_port}",
        f"          Environment=CLUSTER_NAME={cluster_id}",
        "          Environment=KB_HOST=127.0.0.1",
        f"          Environment=KB_PORT={kb_port}",
        "          Environment=KB_SCHEME=http",
        f"          Environment=KB_USER={kb_user}",
        f"          Environment=KB_PASSWORD={kb_password}",
        "          ExecStart=/usr/bin/env python3 /usr/local/bin/opensible-kibana-health.py",
        "          Restart=on-failure",
        "          RestartSec=3",
        "          User=root",
        "          [Install]",
        "          WantedBy=multi-user.target",
        "      register: _kb_health_unit",
        "    - name: Enable and start kibana-health service",
        "      ansible.builtin.systemd:",
        "        name: opensible-kibana-health.service",
        "        enabled: true",
        "        state: restarted",
        "        daemon_reload: true",
        f"    - name: Wait for Kibana health HTTP port {http_port}",
        "      ansible.builtin.wait_for:",
        "        host: 127.0.0.1",
        f"        port: {http_port}",
        "        timeout: 30",
        "    - name: Report Kibana health dashboard URL",
        "      ansible.builtin.debug:",
        f"        msg: \"Kibana health dashboard: http://{{{{ ansible_host | default(inventory_hostname) }}}}:{http_port}/  (JSON: /health.json)\"",
    ]
    return lines


TEMPLATE = {
    "id": "kibana-cluster",
    "name": "Kibana HA (Docker cluster)",
    "category": "Databases",
    "icon": "activity",
    "description": (
        "Multi-node Kibana deployment (recommended 2 or 3 behind a load "
        "balancer) running the official Kibana Docker image. Every node "
        "runs one container managed by a dedicated systemd unit and points "
        "at the same Elasticsearch backend via ELASTICSEARCH_HOSTS. Ships "
        "with a per-node HTTP health dashboard on port 5680 exposing "
        "Kibana + upstream Elasticsearch status as HTML and JSON."
    ),
    "tags": ["kibana", "elastic", "docker", "cluster", "ha", "observability"],
    "variables": [
        # ---------- Cluster identity ----------
        {"name": "cluster_id", "label": "Cluster name",
         "type": "string", "default": "opensible-kibana",
         "help": "Free-form label. Used in the systemd unit description, container name, and filenames."},
        {"name": "kibana_version", "label": "Kibana image tag",
         "type": "string", "default": "8.15.3",
         "help": "Docker Hub tag under docker.elastic.co/kibana/kibana. Must match your Elasticsearch major version."},

        # ---------- Hosts / HA ----------
        {"name": "ssh_user_default", "label": "Default SSH user",
         "type": "string", "default": "root"},
        {"name": "ssh_port_default", "label": "Default SSH port",
         "type": "number", "default": 22},
        {"name": "nodes", "label": "Kibana nodes",
         "type": "nodes", "required": False,
         "help": "Add 2+ nodes and put them behind a load balancer for real HA.",
         "default": [
             {"name": "kibana-1", "ip": "", "ssh_user": "", "ssh_port": ""},
             {"name": "kibana-2", "ip": "", "ssh_user": "", "ssh_port": ""},
         ]},

        # ---------- Networking ----------
        {"name": "http_port", "label": "Kibana HTTP port",
         "type": "number", "default": 5601},
        {"name": "server_publicBaseUrl", "label": "Public base URL",
         "type": "string", "required": False, "default": "",
         "help": "Optional. Set to the external URL users hit (e.g. https://kibana.example.com) so generated links/emails are correct."},
        {"name": "health_http_enabled", "label": "Enable HTTP health dashboard",
         "type": "boolean", "default": True,
         "help": "Install a small HTTP service on each node exposing / (HTML dashboard), /health.json and /live with Kibana and upstream Elasticsearch status."},
        {"name": "health_http_port", "label": "Health HTTP port",
         "type": "number", "default": 5680,
         "help": "Port for the health dashboard. Visit http://<node-ip>:<port>/ from your browser."},

        # ---------- Upstream Elasticsearch ----------
        {"name": "elasticsearch_hosts", "label": "Elasticsearch hosts (comma or newline separated)",
         "type": "text", "required": True,
         "default": "http://10.0.0.11:9200,http://10.0.0.12:9200,http://10.0.0.13:9200",
         "help": "Full URLs of every Elasticsearch node. Kibana load-balances across them and follows failovers."},
        {"name": "security_enabled", "label": "Upstream Elasticsearch has security enabled",
         "type": "boolean", "default": False,
         "help": "Turn on when your Elasticsearch cluster runs with xpack.security.enabled=true."},
        {"name": "elasticsearch_username", "label": "Elasticsearch username",
         "type": "string", "required": False, "default": "kibana_system",
         "help": "Recommended: kibana_system (built-in user). elastic works too but is over-privileged."},
        {"name": "elasticsearch_password", "label": "Elasticsearch password",
         "type": "password", "required": False, "default": "",
         "help": "Required when security is enabled. Prefer providing this via an ansible-vault file listed under vault_files."},
        {"name": "elasticsearch_ssl_verificationMode", "label": "TLS verification mode",
         "type": "select", "default": "full",
         "options": [
             {"value": "full", "label": "full — verify hostname + CA"},
             {"value": "certificate", "label": "certificate — verify CA only"},
             {"value": "none", "label": "none — skip verification (lab only)"},
         ],
         "help": "Applied to Kibana's connection to Elasticsearch when using https:// hosts."},

        # ---------- Kibana → Elasticsearch timeouts ----------
        {"name": "elasticsearch_requestTimeout", "label": "Elasticsearch requestTimeout (ms)",
         "type": "number", "required": False, "default": 300000,
         "help": "Max time Kibana waits for a response from Elasticsearch. OpenSible keeps a 300000ms floor for small HA clusters where Spaces or saved-object calls can exceed Kibana's 30000ms default."},
        {"name": "elasticsearch_pingTimeout", "label": "Elasticsearch pingTimeout (ms)",
         "type": "number", "required": False, "default": 60000,
         "help": "Timeout for the periodic ping Kibana sends to Elasticsearch. OpenSible keeps a 60000ms floor for slow networks or small clusters."},
        {"name": "elasticsearch_shardTimeout", "label": "Elasticsearch shardTimeout (ms)",
         "type": "number", "required": False, "default": 120000,
         "help": "Per-shard search timeout Kibana forwards to Elasticsearch. OpenSible keeps a 120000ms floor for slow system-index searches."},
        {"name": "elasticsearch_sniffOnStart", "label": "Sniff Elasticsearch nodes on start",
         "type": "boolean", "default": False,
         "help": "Leave off for HA setups behind a fixed hosts list; enabling it can cause Kibana to try to reach node names that are not resolvable from the Kibana host."},

        # ---------- Small-cluster tuning ----------
        {"name": "kibana_lightweight_mode", "label": "Lightweight mode for small clusters",
         "type": "boolean", "default": True,
         "help": "Recommended for small self-hosted clusters. Disables heavier optional UI plugins such as Fleet, Security Solution, AI Assistant and Reporting so /spaces/enter does not compete with background startup work."},
        {"name": "task_manager_maxWorkers", "label": "Task Manager max workers",
         "type": "number", "required": False, "default": 5,
         "help": "Limits Kibana background task concurrency. Lower values reduce Elasticsearch pressure during startup and migrations."},
        {"name": "task_manager_pollInterval", "label": "Task Manager poll interval (ms)",
         "type": "number", "required": False, "default": 10000,
         "help": "How often Kibana polls for background tasks, in milliseconds. A slower interval reduces load on small clusters."},

        # ---------- Kibana secrets ----------
        {"name": "encryption_key", "label": "Encryption key (xpack.encryptedSavedObjects)",
         "type": "password", "required": False, "default": "",
         "help": "Optional. 32+ character key used to encrypt saved objects. Auto-generated per host if left blank, but for HA you should set the SAME key across all nodes so saved-object decryption works everywhere."},

        # ---------- Ops ----------
        {"name": "open_firewall", "label": "Open ports in UFW/firewalld",
         "type": "boolean", "default": True},
        {"name": "become", "label": "Run as sudo (become)",
         "type": "boolean", "default": True},
        VAULT_FILES_VARIABLE,
    ],
}


def suggested_filename(values: Dict[str, Any]) -> str:
    stem = slugify(values.get("cluster_id"), "kibana")
    return f"{stem}-kibana-cluster.yml"


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
        name = str(n.get("name") or f"kibana-{i+1}").strip() or f"kibana-{i+1}"
        user = str(n.get("ssh_user") or default_user or "root").strip() or "root"
        try:
            port = int(n.get("ssh_port") or default_port or 22)
        except Exception:
            port = 22
        out.append({
            "name": name,
            "node_slug": slugify(name, f"kibana-{i+1}") or f"kibana-{i+1}",
            "ip": ip,
            "ssh_user": user,
            "ssh_port": port,
            "index": i + 1,
        })
    return out


def _parse_es_hosts(raw: Any) -> List[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        items = [str(x).strip() for x in raw]
    else:
        items: List[str] = []
        for chunk in str(raw).replace("\n", ",").split(","):
            chunk = chunk.strip()
            if chunk:
                items.append(chunk)
    out: List[str] = []
    for it in items:
        if not it:
            continue
        if "://" not in it:
            it = "http://" + it
        out.append(it)
    return out


def _normalize_public_base_url(raw: Any) -> str:
    """Return a Kibana-safe server.publicBaseUrl value or blank.

    Kibana 8.x exits immediately if server.publicBaseUrl is present but is not
    an absolute http/https URI. Users often enter a bare IP/host such as
    10.0.0.10:5601, so normalize that to http://... and ignore placeholders.
    """
    value = str(raw or "").strip().strip('"').strip("'")
    if not value:
        return ""
    if value.lower() in {"-", "none", "null", "n/a", "na"}:
        return ""
    if "://" not in value:
        value = "http://" + value.lstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return value.rstrip("/")


def _parse_kibana_ms(raw: Any, default: int) -> int:
    """Return a Kibana-safe millisecond integer.

    Kibana 8.15 validates xpack.task_manager.poll_interval as a number. Older
    blueprint defaults used strings such as "10s", so accept those values here
    and convert them before writing kibana.yml.
    """
    if raw is None or raw == "":
        return default
    if isinstance(raw, (int, float)):
        return max(int(raw), 1)
    value = str(raw).strip().lower()
    try:
        return max(int(float(value)), 1)
    except Exception:
        pass
    units = (("ms", 1), ("s", 1000), ("m", 60000))
    for suffix, multiplier in units:
        if value.endswith(suffix):
            try:
                number = float(value[: -len(suffix)].strip())
                return max(int(number * multiplier), 1)
            except Exception:
                return default
    return default


def render(values: Dict[str, Any], targets: Dict[str, Any]) -> str:
    become = "true" if values.get("become", True) else "false"

    cluster_id = str(values.get("cluster_id") or "opensible-kibana").strip() or "opensible-kibana"
    kb_version = str(values.get("kibana_version") or "8.15.3").strip().lstrip("v") or "8.15.3"
    http_port = int(values.get("http_port") or 5601)
    public_base_url = _normalize_public_base_url(values.get("server_publicBaseUrl"))
    health_http_enabled = bool(values.get("health_http_enabled", True))
    health_http_port = int(values.get("health_http_port") or 5680)

    security_enabled = bool(values.get("security_enabled", False))
    es_user = str(values.get("elasticsearch_username") or "kibana_system").strip() or "kibana_system"
    es_password = str(values.get("elasticsearch_password") or "")
    ssl_mode = str(values.get("elasticsearch_ssl_verificationMode") or "full").strip() or "full"
    try:
        es_request_timeout = max(int(values.get("elasticsearch_requestTimeout") or 300000), 300000)
    except Exception:
        es_request_timeout = 300000
    try:
        es_ping_timeout = max(int(values.get("elasticsearch_pingTimeout") or 60000), 60000)
    except Exception:
        es_ping_timeout = 60000
    try:
        es_shard_timeout = max(int(values.get("elasticsearch_shardTimeout") or 120000), 120000)
    except Exception:
        es_shard_timeout = 120000
    es_sniff_on_start = "true" if values.get("elasticsearch_sniffOnStart", False) else "false"
    kibana_lightweight_mode = bool(values.get("kibana_lightweight_mode", True))
    try:
        task_manager_max_workers = max(int(values.get("task_manager_maxWorkers") or 5), 1)
    except Exception:
        task_manager_max_workers = 5
    task_manager_poll_interval = _parse_kibana_ms(values.get("task_manager_pollInterval"), 10000)
    encryption_key = str(values.get("encryption_key") or "")

    es_hosts = _parse_es_hosts(values.get("elasticsearch_hosts"))
    es_hosts_json = "[" + ",".join(f"\"{h}\"" for h in es_hosts) + "]"

    open_firewall = bool(values.get("open_firewall", True))

    nodes = _norm_nodes(
        values.get("nodes"),
        values.get("ssh_user_default") or "root",
        values.get("ssh_port_default") or 22,
    )

    cluster_group = slugify(cluster_id, "kibana").replace("-", "_") + "_nodes"
    cluster_name = slugify(cluster_id, "opensible-kibana")
    container_name = f"kibana-{cluster_name}"

    parts: List[str] = ["---"]
    parts.append("# OpenSible kibana-cluster template generation: 2026-07-kibana-cluster-v2")
    parts.append(f"# Rendered from template: {TEMPLATE['name']}")
    parts.append(f"# Cluster: {cluster_id} | image tag: {kb_version} | nodes: {len(nodes) if nodes else 'from targets'}")
    parts.append(f"# Elasticsearch backend: {', '.join(es_hosts) if es_hosts else '(none configured)'}")
    if public_base_url:
        parts.append(f"# Kibana public base URL: {public_base_url}")
    parts.append("")

    # ------------------------------------------------------------------ #
    # PLAY 0 — dynamic inventory
    # ------------------------------------------------------------------ #
    if nodes:
        parts += [
            "- name: Register Kibana nodes into a dynamic inventory group",
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
            "        kb_node_name: \"{{ item.name }}\"",
            "        kb_node_slug: \"{{ item.node_slug }}\"",
            "        kb_node_index: \"{{ item.index }}\"",
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

    # ------------------------------------------------------------------ #
    # PLAY 1 — install docker, run Kibana container per node
    # ------------------------------------------------------------------ #
    parts += [
        f"- name: Deploy Kibana {kb_version} (Docker) on every node",
        f"  hosts: {play_hosts}",
        f"  become: {become}",
        "  gather_facts: true",
        "  any_errors_fatal: true",
        *vars_files_lines(parse_vault_files(values.get("vault_files"))),
        "  vars:",
        f"    kb_cluster_id: {yaml_str(cluster_id)}",
        f"    kb_cluster_name: {yaml_str(cluster_name)}",
        f"    kb_container_name: {yaml_str(container_name)}",
        f"    kb_image: \"docker.elastic.co/kibana/kibana:{kb_version}\"",
        f"    kb_http_port: {http_port}",
        f"    kb_public_base_url: {yaml_str(public_base_url)}",
        f"    kb_es_hosts_json: '{es_hosts_json}'",
        f"    kb_es_hosts_list: {es_hosts_json}",

        f"    kb_security_enabled: {'true' if security_enabled else 'false'}",
        f"    kb_es_username: {yaml_str(es_user)}",
        f"    kb_es_password: {yaml_str(es_password)}",
        f"    kb_es_ssl_mode: {yaml_str(ssl_mode)}",
        f"    kb_es_request_timeout: {es_request_timeout}",
        f"    kb_es_ping_timeout: {es_ping_timeout}",
        f"    kb_es_shard_timeout: {es_shard_timeout}",
        f"    kb_es_sniff_on_start: {es_sniff_on_start}",
        f"    kb_lightweight_mode: {'true' if kibana_lightweight_mode else 'false'}",
        f"    kb_task_manager_max_workers: {task_manager_max_workers}",
        f"    kb_task_manager_poll_interval: {task_manager_poll_interval}",
        f"    kb_encryption_key: {yaml_str(encryption_key)}",
        "  tasks:",

        # ---------- Preflight ----------
        "    - name: Resolve node slug for Kibana server.name",
        "      ansible.builtin.set_fact:",
        "        kb_node_slug: \"{{ kb_node_slug | default(inventory_hostname_short) | default(inventory_hostname) }}\"",
        "    - name: Derive stable per-host server.uuid",
        "      ansible.builtin.set_fact:",
        "        kb_server_uuid: \"{{ (kb_cluster_name ~ '-' ~ kb_node_slug) | to_uuid }}\"",
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
        "        state: present",
        "      failed_when: false",

        # ---------- Docker Engine ----------
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
        "    - name: Stop existing kibana-docker unit before Docker maintenance",
        "      ansible.builtin.systemd:",
        "        name: kibana-docker",
        "        state: stopped",
        "      failed_when: false",
        "    - name: Reset failed kibana-docker unit state before Docker maintenance",
        "      ansible.builtin.command: systemctl reset-failed kibana-docker",
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

        # ---------- Pull image ----------
        "    - name: Pull Kibana image",
        "      ansible.builtin.command: docker pull {{ kb_image }}",
        "      register: _kb_pull",
        "      retries: 3",
        "      delay: 5",
        "      until: _kb_pull.rc == 0",
        "      changed_when: \"'Downloaded newer image' in _kb_pull.stdout or 'Pull complete' in _kb_pull.stdout\"",

        # ---------- Cleanup stale containers ----------
        "    - name: Stop existing kibana-docker unit before recreate",
        "      ansible.builtin.systemd:",
        "        name: kibana-docker",
        "        state: stopped",
        "      failed_when: false",
        "    - name: Reset failed kibana-docker unit state",
        "      ansible.builtin.command: systemctl reset-failed kibana-docker",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Remove stale Kibana Docker containers",
        "      ansible.builtin.shell: |",
        "        set +e",
        "        command -v docker >/dev/null 2>&1 || exit 0",
        "        docker rm -f {{ kb_container_name }} 2>/dev/null || true",
        "        for c in $(docker ps -aq --filter 'name=^/kibana-'); do",
        "          docker rm -f \"$c\" 2>/dev/null || true",
        "        done",
        "        for c in $(docker ps -aq 2>/dev/null); do",
        "          img=$(docker inspect --format '{{ '{{' }}.Config.Image{{ '}}' }}' \"$c\" 2>/dev/null)",
        "          case \"$img\" in",
        "            *docker.elastic.co/kibana/kibana*) docker rm -f \"$c\" 2>/dev/null || true ;;",
        "          esac",
        "        done",
        "        exit 0",
        "      args:",
        "        executable: /bin/bash",
        "      changed_when: false",
        "      failed_when: false",

        # ---------- Kibana config file ----------
        "    - name: Create Kibana config directory",
        "      ansible.builtin.file:",
        "        path: /etc/opensible/kibana",
        "        state: directory",
        "        owner: root",
        "        group: root",
        "        mode: '0750'",
        "    - name: Write Kibana configuration file",
        "      ansible.builtin.copy:",
        "        dest: /etc/opensible/kibana/kibana.yml",
        "        owner: root",
        "        group: root",
        "        mode: '0640'",
        "        content: |",
        "          server.name: {{ kb_node_slug | to_json }}",
        "          server.host: \"0.0.0.0\"",
        "          server.port: {{ kb_http_port }}",
        "          server.uuid: {{ kb_server_uuid | to_json }}",
        "          {% if kb_public_base_url %}",
        "          server.publicBaseUrl: {{ kb_public_base_url | to_json }}",
        "          {% endif %}",
        "          elasticsearch.hosts: {{ kb_es_hosts_json }}",
        "          {% if kb_security_enabled %}",
        "          elasticsearch.username: {{ kb_es_username | to_json }}",
        "          elasticsearch.password: {{ kb_es_password | to_json }}",
        "          {% endif %}",
        "          elasticsearch.ssl.verificationMode: {{ kb_es_ssl_mode | to_json }}",
        "          elasticsearch.requestTimeout: {{ kb_es_request_timeout }}",
        "          elasticsearch.pingTimeout: {{ kb_es_ping_timeout }}",
        "          elasticsearch.shardTimeout: {{ kb_es_shard_timeout }}",
        "          elasticsearch.sniffOnStart: {{ kb_es_sniff_on_start }}",
        "          xpack.task_manager.max_workers: {{ kb_task_manager_max_workers }}",
        "          xpack.task_manager.poll_interval: {{ kb_task_manager_poll_interval }}",
        "          {% if kb_encryption_key %}",
        "          xpack.encryptedSavedObjects.encryptionKey: {{ kb_encryption_key | to_json }}",
        "          xpack.reporting.encryptionKey: {{ kb_encryption_key | to_json }}",
        "          {% endif %}",
        "          {% if kb_lightweight_mode %}",
        "          xpack.fleet.enabled: false",
        "          xpack.securitySolution.enabled: false",
        "          xpack.observabilityAIAssistant.enabled: false",
        "          xpack.reporting.enabled: false",
        "          {% endif %}",
        "          telemetry.optIn: false",
        "      register: _kb_config",
        "    - name: Validate Kibana configuration before starting container",
        "      ansible.builtin.shell: |",
        "        set -euo pipefail",
        "        python3 - <<'PY'",
        "        import re, sys",
        "        path = '/etc/opensible/kibana/kibana.yml'",
        "        text = open(path, encoding='utf-8').read()",
        "        match = re.search(r'^\\s*xpack\\.task_manager\\.poll_interval:\\s*(\\S+)\\s*$', text, re.M)",
        "        if not match:",
        "            raise SystemExit('missing xpack.task_manager.poll_interval in kibana.yml')",
        "        value = match.group(1).strip()",
        "        if not re.fullmatch(r'[0-9]+', value):",
        "            raise SystemExit(f'xpack.task_manager.poll_interval must be an unquoted integer in milliseconds, got {value!r}')",
        "        PY",
        "      args:",
        "        executable: /bin/bash",
        "      changed_when: false",

        # ---------- Systemd unit that owns the container ----------
        "    - name: Write kibana-docker systemd unit",
        "      ansible.builtin.copy:",
        "        dest: /etc/systemd/system/kibana-docker.service",
        "        mode: '0644'",
        "        content: |",
        "          [Unit]",
        f"          Description=OpenSible Kibana ({cluster_id}) via Docker",
        "          Requires=docker.service",
        "          After=docker.service network-online.target",
        "          Wants=network-online.target",
        "",
        "          [Service]",
        "          Type=simple",
        "          Restart=always",
        "          RestartSec=5",
        "          TimeoutStartSec=0",
        "          ExecStartPre=-/usr/bin/docker stop {{ kb_container_name }}",
        "          ExecStartPre=-/usr/bin/docker rm {{ kb_container_name }}",
        "          ExecStart=/usr/bin/docker run --rm \\",
        "            --name {{ kb_container_name }} \\",
        "            -v /etc/opensible/kibana/kibana.yml:/usr/share/kibana/config/kibana.yml:ro \\",
        f"            -p {http_port}:{http_port} \\",
        "            {{ kb_image }}",
        "          ExecStop=/usr/bin/docker stop {{ kb_container_name }}",
        "",
        "          [Install]",
        "          WantedBy=multi-user.target",
        "      register: _kb_unit",
        "    - name: Reload systemd for kibana-docker unit",
        "      ansible.builtin.systemd:",
        "        daemon_reload: true",
        "      when: _kb_unit.changed",
        "    - name: Enable + start kibana-docker",
        "      ansible.builtin.systemd:",
        "        name: kibana-docker",
        "        enabled: true",
        "        state: started",
        "    - name: Restart kibana-docker if unit or config changed",
        "      when: _kb_unit.changed or _kb_config.changed",
        "      ansible.builtin.systemd:",
        "        name: kibana-docker",
        "        state: restarted",
    ]

    # ---------- Firewall ----------
    if open_firewall:
        parts += [
            "    - name: Open Kibana ports (ufw, if active)",
            "      ansible.builtin.shell: |",
            "        set -e",
            "        command -v ufw >/dev/null 2>&1 || exit 0",
            "        ufw status | grep -q 'Status: active' || exit 0",
            f"        ufw allow {http_port}/tcp || true",
            *([f"        ufw allow {health_http_port}/tcp || true"] if health_http_enabled else []),
            "      changed_when: false",
            "      failed_when: false",
            "    - name: Open Kibana ports (firewalld, if active)",
            "      ansible.builtin.shell: |",
            "        set -e",
            "        command -v firewall-cmd >/dev/null 2>&1 || exit 0",
            "        firewall-cmd --state >/dev/null 2>&1 || exit 0",
            f"        firewall-cmd --permanent --add-port={http_port}/tcp || true",
            *([f"        firewall-cmd --permanent --add-port={health_http_port}/tcp || true"] if health_http_enabled else []),
            "        firewall-cmd --reload || true",
            "      changed_when: false",
            "      failed_when: false",
        ]

    # ---------- Wait for local Kibana ----------
    parts += [
        f"    - name: Wait for Kibana HTTP port {http_port} locally",
        "      ansible.builtin.wait_for:",
        "        host: 127.0.0.1",
        f"        port: {http_port}",
        "        timeout: 300",
        "      register: _kb_port_wait",
        "      failed_when: false",
        f"    - name: Wait for Kibana /api/status to respond on port {http_port}",
        "      ansible.builtin.uri:",
        f"        url: \"http://127.0.0.1:{http_port}/api/status\"",
        "        status_code: [200, 503]",
        "        return_content: false",
        "        headers:",
        "          kbn-xsrf: \"true\"",
        "      register: _kb_local_status",
        "      retries: 60",
        "      delay: 5",
        "      until: _kb_local_status.status | default(0) in [200, 503]",
        "      failed_when: false",
        "    - name: Collect Kibana startup diagnostics when /api/status did not respond",
        "      when: (_kb_local_status.status | default(0)) not in [200, 503]",
        "      ansible.builtin.shell: |",
        "        set +e",
        "        echo '== systemctl status kibana-docker =='",
        "        systemctl status kibana-docker --no-pager -l 2>&1 || true",
        "        echo '== recent kibana-docker journal =='",
        "        journalctl -u kibana-docker -n 200 --no-pager 2>&1 || true",
        "        echo '== docker containers =='",
        "        docker ps -a 2>&1 || true",
        "        echo '== kibana container logs =='",
        "        docker logs --tail 300 {{ kb_container_name }} 2>&1 || true",
        "        echo '== listening ports =='",
        f"        ss -ltnp | grep -E ':{http_port}\\b' 2>&1 || true",
        "        echo '== reachability to Elasticsearch =='",
        "        for h in {{ kb_es_hosts_list | join(' ') }}; do",
        "          echo \"-- $h --\"",
        "          curl -sS -m 5 -o /dev/null -w 'http_code=%{http_code}\\n' \"$h\" 2>&1 || true",
        "        done",
        "      args:",
        "        executable: /bin/bash",
        "      register: _kb_startup_diag",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Show Kibana startup diagnostics",
        "      when: (_kb_local_status.status | default(0)) not in [200, 503]",
        "      ansible.builtin.debug:",
        "        var: _kb_startup_diag.stdout_lines",
        "    - name: Fail if Kibana did not answer /api/status locally",
        "      when: (_kb_local_status.status | default(0)) not in [200, 503]",
        "      ansible.builtin.fail:",
        f"        msg: \"Kibana on {{{{ inventory_hostname }}}} did not respond on http://127.0.0.1:{http_port}/api/status — see diagnostics above.\"",
    ]

    # ---------- Health dashboard ----------
    if health_http_enabled:
        parts += _health_tasks(
            cluster_id, http_port, health_http_port,
            es_user if security_enabled else "",
            es_password if security_enabled else "",
        )

    # ------------------------------------------------------------------ #
    # PLAY 2 — cluster verification (per-node)
    # ------------------------------------------------------------------ #
    parts += [
        "- name: Verify Kibana availability",
        f"  hosts: {play_hosts}",
        f"  become: {become}",
        "  gather_facts: false",
        "  vars:",
        f"    kb_container_name: {yaml_str(container_name)}",
        f"    kb_es_password: {yaml_str(es_password)}",
        "  tasks:",
        "    - name: Query Kibana /api/status",
        "      ansible.builtin.uri:",
        f"        url: \"http://127.0.0.1:{http_port}/api/status\"",
        "        return_content: true",
        "        headers:",
        "          kbn-xsrf: \"true\"",
        "      register: _kb_status",
        "      retries: 60",
        "      delay: 5",
        "      until: _kb_status.status == 200 and (((_kb_status.json.status | default({})).overall | default({})).level | default('') in ['available','degraded'])",
        "      failed_when: false",
        "    - name: Collect Kibana verify diagnostics on failure",
        "      when: (_kb_status.status | default(0)) != 200",
        "      ansible.builtin.shell: |",
        "        set +e",
        "        echo '== systemctl status kibana-docker =='",
        "        systemctl status kibana-docker --no-pager -l 2>&1 || true",
        "        echo '== recent kibana-docker journal =='",
        "        journalctl -u kibana-docker -n 200 --no-pager 2>&1 || true",
        "        echo '== kibana container logs =='",
        "        docker logs --tail 300 {{ kb_container_name }} 2>&1 || true",
        "      args:",
        "        executable: /bin/bash",
        "      register: _kb_verify_diag",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Show Kibana verify diagnostics",
        "      when: (_kb_status.status | default(0)) != 200",
        "      ansible.builtin.debug:",
        "        var: _kb_verify_diag.stdout_lines",
        "    - name: Fail if Kibana /api/status did not return 200",
        "      when: (_kb_status.status | default(0)) != 200",
        "      ansible.builtin.fail:",
        "        msg: \"Kibana on {{ inventory_hostname }} did not return a healthy /api/status — see diagnostics above.\"",
        "    - name: Kibana node summary",
        "      ansible.builtin.debug:",
        "        msg:",
        f"          - \"Kibana node {{{{ inventory_hostname }}}} of '{cluster_id}' is up (name={cluster_name}).\"",
        "          - \"Overall: {{ ((_kb_status.json.status | default({})).overall | default({})).level | default('unknown') }} | version: {{ (_kb_status.json.version | default({})).number | default('?') }}\"",
        f"          - \"Open: http://<node-ip>:{http_port}/  (put a load balancer in front for real HA)\"",
        "",
    ]

    return "\n".join(parts)

