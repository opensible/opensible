"""Template: Logstash HA cluster on Docker containers.

Deploys N Logstash nodes (recommended 2+) all writing to the same
Elasticsearch backend. Each node runs a single `logstash` container
managed by a dedicated systemd unit (`logstash-docker.service`) so the
container survives host reboots and docker daemon restarts.

Highlights:

  * Docker Engine ensured (installed via convenience script if missing).
  * Per-node systemd unit runs `docker run` against the pinned image tag.
  * Shared logstash.yml + pipeline (main.conf) mounted read-only.
  * Default pipeline: Beats input -> Elasticsearch output.
  * Optional built-in HTTP health dashboard on every node exposing an
    HTML view + /health.json (Logstash node stats, per-pipeline
    events/in/out/errors, upstream Elasticsearch reachability).

Marker: 2026-07-logstash-cluster-v1
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
# Per-node HTTP health dashboard
# Endpoints:
#   /            HTML dashboard (auto-refresh 5s) — Logstash node + pipelines
#   /health.json JSON payload (200 healthy, 503 unhealthy / unreachable)
#   /live        liveness (always 200 if process is up)
# ---------------------------------------------------------------------------
_HEALTH_SCRIPT = r'''#!/usr/bin/env python3
"""OpenSible Logstash HA HTTP health dashboard."""
import base64, json, os, html, socket, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib import request as _rq, error as _er

CFG = {
    "cluster_name": os.environ.get("CLUSTER_NAME", ""),
    "ls_host": os.environ.get("LS_HOST", "127.0.0.1"),
    "ls_port": int(os.environ.get("LS_PORT", "9600")),
    "es_hosts": os.environ.get("ES_HOSTS", ""),
    "es_user": os.environ.get("ES_USER", ""),
    "es_password": os.environ.get("ES_PASSWORD", ""),
    "http_port": int(os.environ.get("HTTP_PORT", "9680")),
}


def _get(url, user="", password="", timeout=4):
    try:
        req = _rq.Request(url, headers={"Accept": "application/json"})
        if user:
            tok = base64.b64encode(f"{user}:{password}".encode()).decode()
            req.add_header("Authorization", f"Basic {tok}")
        ctx = None
        if url.startswith("https://"):
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


def _json(url, user="", password=""):
    code, body = _get(url, user, password)
    data = {}
    try:
        data = json.loads(body) if body and body.startswith(("{", "[")) else {}
    except Exception:
        data = {}
    return code, data, body


def collect():
    base = f"http://{CFG['ls_host']}:{CFG['ls_port']}"
    nc, nj, nb = _json(base + "/_node")
    sc, sj, sb = _json(base + "/_node/stats")
    ls_up = nc == 200
    status = "green" if ls_up else "red"
    pipelines = {}
    if isinstance(sj, dict):
        pipelines = (sj.get("pipelines") or {})
    es_first = ""
    es_status = "unknown"
    es_code = -1
    if CFG["es_hosts"]:
        es_first = CFG["es_hosts"].split(",")[0].strip()
        if es_first:
            ec, _ej, _eb = _json(
                es_first.rstrip("/") + "/_cluster/health",
                CFG["es_user"], CFG["es_password"],
            )
            es_code = ec
            es_status = "green" if ec == 200 else "red"
    healthy = ls_up and (not CFG["es_hosts"] or es_status == "green")
    return {
        "hostname": socket.gethostname(),
        "ts": int(time.time()),
        "cluster_name": CFG["cluster_name"],
        "logstash": {"code": nc, "node": nj if isinstance(nj, dict) else {},
                     "stats": sj if isinstance(sj, dict) else {},
                     "status": status},
        "pipelines": pipelines,
        "elasticsearch": {"first": es_first, "code": es_code,
                          "status": es_status},
        "healthy": bool(healthy),
    }


def esc(v):
    return html.escape("" if v is None else str(v), quote=True)


def badge(s):
    s = (s or "").lower()
    color = {"green": "#238636", "yellow": "#9e6a03",
             "red": "#da3633"}.get(s, "#6e7681")
    return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:10px;font-weight:600">{esc(s or "unknown")}</span>'


def render_html(d):
    node = d["logstash"]["node"] or {}
    version = node.get("version") or ""
    name = node.get("name") or ""
    http_addr = node.get("http_address") or ""
    rows = ""
    if isinstance(d["pipelines"], dict):
        for pid, p in d["pipelines"].items():
            ev = (p or {}).get("events") or {}
            rows += (
                "<tr>"
                f"<td>{esc(pid)}</td>"
                f"<td>{esc(ev.get('in'))}</td>"
                f"<td>{esc(ev.get('filtered'))}</td>"
                f"<td>{esc(ev.get('out'))}</td>"
                f"<td>{esc(ev.get('duration_in_millis'))}</td>"
                f"<td>{esc(ev.get('queue_push_duration_in_millis'))}</td>"
                "</tr>"
            )
    if not rows:
        rows = '<tr><td colspan="6" style="opacity:.7">no pipeline stats yet</td></tr>'
    es = d["elasticsearch"]
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="5">
<title>Logstash — {esc(d['cluster_name'])} @ {esc(d['hostname'])}</title>
<style>
body{{background:#0d1117;color:#c9d1d9;font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:24px}}
h1{{margin:0 0 4px 0;font-size:18px}} h2{{margin:24px 0 8px 0;font-size:14px;color:#8b949e;text-transform:uppercase;letter-spacing:.05em}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin-bottom:16px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{padding:6px 10px;border-bottom:1px solid #21262d;text-align:left}} th{{color:#8b949e;font-weight:600}}
.k{{color:#8b949e}} .v{{color:#c9d1d9;font-weight:600}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:8px 24px}}
</style></head><body>
<div class="card">
  <h1>Logstash cluster <span class="v">{esc(d['cluster_name'])}</span> — node <span class="v">{esc(d['hostname'])}</span></h1>
  <div style="margin-top:6px">Logstash: {badge(d['logstash']['status'])} &middot; Elasticsearch: {badge(es['status'])} &middot; Overall: {badge('green' if d['healthy'] else 'red')}</div>
</div>
<div class="card"><h2>Node</h2>
  <div class="grid">
    <div><span class="k">name</span><br><span class="v">{esc(name)}</span></div>
    <div><span class="k">version</span><br><span class="v">{esc(version)}</span></div>
    <div><span class="k">http_address</span><br><span class="v">{esc(http_addr)}</span></div>
    <div><span class="k">ephemeral_id</span><br><span class="v">{esc(node.get('ephemeral_id'))}</span></div>
  </div>
</div>
<div class="card"><h2>Pipelines</h2>
<table><thead><tr><th>id</th><th>in</th><th>filtered</th><th>out</th><th>duration_ms</th><th>queue_push_ms</th></tr></thead>
<tbody>{rows}</tbody></table></div>
<div class="card"><h2>Upstream Elasticsearch</h2>
  <div class="grid">
    <div><span class="k">first host</span><br><span class="v">{esc(es['first'])}</span></div>
    <div><span class="k">/_cluster/health code</span><br><span class="v">{esc(es['code'])}</span></div>
    <div><span class="k">status</span><br>{badge(es['status'])}</div>
  </div>
</div>
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


def _health_tasks(cluster_id: str, ls_api_port: int, http_port: int,
                  es_hosts_csv: str, es_user: str, es_password: str) -> List[str]:
    lines: List[str] = [
        "    - name: Ensure python3 present for Logstash health service",
        "      ansible.builtin.package:",
        "        name: python3",
        "        state: present",
        "      failed_when: false",
        "    - name: Install logstash-health dashboard script",
        "      ansible.builtin.copy:",
        "        dest: /usr/local/bin/opensible-logstash-health.py",
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
        "    - name: Install logstash-health systemd unit",
        "      ansible.builtin.copy:",
        "        dest: /etc/systemd/system/opensible-logstash-health.service",
        "        owner: root",
        "        group: root",
        "        mode: '0644'",
        "        content: |",
        "          [Unit]",
        f"          Description=OpenSible Logstash HTTP health dashboard ({cluster_id})",
        "          After=network-online.target logstash-docker.service",
        "          Wants=network-online.target",
        "          [Service]",
        "          Type=simple",
        f"          Environment=HTTP_PORT={http_port}",
        f"          Environment=CLUSTER_NAME={cluster_id}",
        "          Environment=LS_HOST=127.0.0.1",
        f"          Environment=LS_PORT={ls_api_port}",
        f"          Environment=ES_HOSTS={es_hosts_csv}",
        f"          Environment=ES_USER={es_user}",
        f"          Environment=ES_PASSWORD={es_password}",
        "          ExecStart=/usr/bin/env python3 /usr/local/bin/opensible-logstash-health.py",
        "          Restart=on-failure",
        "          RestartSec=3",
        "          User=root",
        "          [Install]",
        "          WantedBy=multi-user.target",
        "      register: _ls_health_unit",
        "    - name: Enable and start logstash-health service",
        "      ansible.builtin.systemd:",
        "        name: opensible-logstash-health.service",
        "        enabled: true",
        "        state: restarted",
        "        daemon_reload: true",
        f"    - name: Wait for Logstash health HTTP port {http_port}",
        "      ansible.builtin.wait_for:",
        "        host: 127.0.0.1",
        f"        port: {http_port}",
        "        timeout: 30",
        "    - name: Report Logstash health dashboard URL",
        "      ansible.builtin.debug:",
        f"        msg: \"Logstash health dashboard: http://{{{{ ansible_host | default(inventory_hostname) }}}}:{http_port}/  (JSON: /health.json)\"",
    ]
    return lines


TEMPLATE = {
    "id": "logstash-cluster",
    "name": "Logstash HA (Docker cluster)",
    "category": "Databases",
    "icon": "activity",
    "description": (
        "Multi-node Logstash deployment (recommended 2+) running the "
        "official Logstash Docker image. Every node runs one container "
        "managed by a dedicated systemd unit, sharing the same pipeline "
        "and writing to the same Elasticsearch backend. Default pipeline: "
        "Beats input -> Elasticsearch output. Ships with a per-node HTTP "
        "health dashboard on port 9680 exposing Logstash node + pipeline "
        "stats and upstream Elasticsearch reachability as HTML and JSON."
    ),
    "tags": ["logstash", "elastic", "docker", "cluster", "ha", "pipeline", "ingest"],
    "variables": [
        # ---------- Cluster identity ----------
        {"name": "cluster_id", "label": "Cluster name",
         "type": "string", "default": "opensible-logstash",
         "help": "Free-form label. Used in the systemd unit description, container name, and filenames."},
        {"name": "logstash_version", "label": "Logstash image tag",
         "type": "string", "default": "8.15.3",
         "help": "Docker Hub tag under docker.elastic.co/logstash/logstash. Must match your Elasticsearch major version."},

        # ---------- Hosts / HA ----------
        {"name": "ssh_user_default", "label": "Default SSH user",
         "type": "string", "default": "root"},
        {"name": "ssh_port_default", "label": "Default SSH port",
         "type": "number", "default": 22},
        {"name": "nodes", "label": "Logstash nodes",
         "type": "nodes", "required": False,
         "help": "Add 2+ nodes. Beats clients accept a list of Logstash hosts and load-balance / failover on their own.",
         "default": [
             {"name": "logstash-1", "ip": "", "ssh_user": "", "ssh_port": ""},
             {"name": "logstash-2", "ip": "", "ssh_user": "", "ssh_port": ""},
         ]},

        # ---------- Networking ----------
        {"name": "beats_port", "label": "Beats input port",
         "type": "number", "default": 5044,
         "help": "Port the default pipeline exposes for Filebeat/Metricbeat/etc."},
        {"name": "http_api_port", "label": "Logstash monitoring API port",
         "type": "number", "default": 9600,
         "help": "Bound to 127.0.0.1 inside the container; used by the health dashboard."},
        {"name": "health_http_enabled", "label": "Enable HTTP health dashboard",
         "type": "boolean", "default": True,
         "help": "Install a small HTTP service on each node exposing / (HTML dashboard), /health.json and /live."},
        {"name": "health_http_port", "label": "Health HTTP port",
         "type": "number", "default": 9680,
         "help": "Port for the health dashboard. Visit http://<node-ip>:<port>/ from your browser."},

        # ---------- Upstream Elasticsearch ----------
        {"name": "elasticsearch_hosts", "label": "Elasticsearch hosts (comma or newline separated)",
         "type": "text", "required": True,
         "default": "http://10.0.0.11:9200,http://10.0.0.12:9200,http://10.0.0.13:9200",
         "help": "Full URLs of every Elasticsearch node. Logstash load-balances across them and follows failovers."},
        {"name": "security_enabled", "label": "Upstream Elasticsearch has security enabled",
         "type": "boolean", "default": False,
         "help": "Turn on when your Elasticsearch cluster runs with xpack.security.enabled=true."},
        {"name": "elasticsearch_username", "label": "Elasticsearch username",
         "type": "string", "required": False, "default": "elastic",
         "help": "User Logstash uses to write to Elasticsearch. Consider creating a dedicated 'logstash_writer' role."},
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
         "help": "Applied to the elasticsearch output when using https:// hosts."},

        # ---------- Pipeline / JVM ----------
        {"name": "pipeline_workers", "label": "Pipeline workers",
         "type": "number", "default": 2,
         "help": "Number of parallel worker threads per pipeline. Defaults to CPU count if left blank in Logstash."},
        {"name": "pipeline_batch_size", "label": "Pipeline batch size",
         "type": "number", "default": 125,
         "help": "Events per batch per worker. Increase for higher throughput and larger memory use."},
        {"name": "heap_size", "label": "JVM heap size",
         "type": "string", "default": "1g",
         "help": "Passed as both -Xms and -Xmx via LS_JAVA_OPTS."},
        {"name": "index_pattern", "label": "Elasticsearch index pattern",
         "type": "string", "default": "logs-%{+YYYY.MM.dd}",
         "help": "Index the default elasticsearch output writes to."},

        # ---------- Ops ----------
        {"name": "open_firewall", "label": "Open ports in UFW/firewalld",
         "type": "boolean", "default": True},
        {"name": "become", "label": "Run as sudo (become)",
         "type": "boolean", "default": True},
        VAULT_FILES_VARIABLE,
    ],
}


def suggested_filename(values: Dict[str, Any]) -> str:
    stem = slugify(values.get("cluster_id"), "logstash")
    return f"{stem}-logstash-cluster.yml"


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
        name = str(n.get("name") or f"logstash-{i+1}").strip() or f"logstash-{i+1}"
        user = str(n.get("ssh_user") or default_user or "root").strip() or "root"
        try:
            port = int(n.get("ssh_port") or default_port or 22)
        except Exception:
            port = 22
        out.append({
            "name": name,
            "node_slug": slugify(name, f"logstash-{i+1}") or f"logstash-{i+1}",
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
        items = []
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


def render(values: Dict[str, Any], targets: Dict[str, Any]) -> str:
    become = "true" if values.get("become", True) else "false"

    cluster_id = str(values.get("cluster_id") or "opensible-logstash").strip() or "opensible-logstash"
    ls_version = str(values.get("logstash_version") or "8.15.3").strip().lstrip("v") or "8.15.3"
    beats_port = int(values.get("beats_port") or 5044)
    http_api_port = int(values.get("http_api_port") or 9600)
    health_http_enabled = bool(values.get("health_http_enabled", True))
    health_http_port = int(values.get("health_http_port") or 9680)

    security_enabled = bool(values.get("security_enabled", False))
    es_user = str(values.get("elasticsearch_username") or "elastic").strip() or "elastic"
    es_password = str(values.get("elasticsearch_password") or "")
    ssl_mode = str(values.get("elasticsearch_ssl_verificationMode") or "full").strip() or "full"

    try:
        pipeline_workers = max(int(values.get("pipeline_workers") or 2), 1)
    except Exception:
        pipeline_workers = 2
    try:
        pipeline_batch_size = max(int(values.get("pipeline_batch_size") or 125), 1)
    except Exception:
        pipeline_batch_size = 125
    heap_size = str(values.get("heap_size") or "1g").strip() or "1g"
    index_pattern = str(values.get("index_pattern") or "logs-%{+YYYY.MM.dd}").strip() or "logs-%{+YYYY.MM.dd}"

    es_hosts = _parse_es_hosts(values.get("elasticsearch_hosts"))
    # Ruby array literal for logstash pipeline config
    es_hosts_ruby = "[" + ",".join(f"\"{h}\"" for h in es_hosts) + "]"
    es_hosts_csv = ",".join(es_hosts)

    open_firewall = bool(values.get("open_firewall", True))

    nodes = _norm_nodes(
        values.get("nodes"),
        values.get("ssh_user_default") or "root",
        values.get("ssh_port_default") or 22,
    )

    cluster_group = slugify(cluster_id, "logstash").replace("-", "_") + "_nodes"
    cluster_name = slugify(cluster_id, "opensible-logstash")
    container_name = f"logstash-{cluster_name}"

    parts: List[str] = ["---"]
    parts.append("# OpenSible logstash-cluster template generation: 2026-07-logstash-cluster-v1")
    parts.append(f"# Rendered from template: {TEMPLATE['name']}")
    parts.append(f"# Cluster: {cluster_id} | image tag: {ls_version} | nodes: {len(nodes) if nodes else 'from targets'}")
    parts.append(f"# Elasticsearch backend: {', '.join(es_hosts) if es_hosts else '(none configured)'}")
    parts.append("")

    # ------------------------------------------------------------------ #
    # PLAY 0 — dynamic inventory
    # ------------------------------------------------------------------ #
    if nodes:
        parts += [
            "- name: Register Logstash nodes into a dynamic inventory group",
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
            "        ls_node_name: \"{{ item.name }}\"",
            "        ls_node_slug: \"{{ item.node_slug }}\"",
            "        ls_node_index: \"{{ item.index }}\"",
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
    # PLAY 1 — install docker, run Logstash container per node
    # ------------------------------------------------------------------ #
    parts += [
        f"- name: Deploy Logstash {ls_version} (Docker) on every node",
        f"  hosts: {play_hosts}",
        f"  become: {become}",
        "  gather_facts: true",
        "  any_errors_fatal: true",
        *vars_files_lines(parse_vault_files(values.get("vault_files"))),
        "  vars:",
        f"    ls_cluster_id: {yaml_str(cluster_id)}",
        f"    ls_cluster_name: {yaml_str(cluster_name)}",
        f"    ls_container_name: {yaml_str(container_name)}",
        f"    ls_image: \"docker.elastic.co/logstash/logstash:{ls_version}\"",
        f"    ls_beats_port: {beats_port}",
        f"    ls_http_api_port: {http_api_port}",
        f"    ls_security_enabled: {'true' if security_enabled else 'false'}",
        f"    ls_es_username: {yaml_str(es_user)}",
        f"    ls_es_password: {yaml_str(es_password)}",
        f"    ls_es_ssl_mode: {yaml_str(ssl_mode)}",
        f"    ls_pipeline_workers: {pipeline_workers}",
        f"    ls_pipeline_batch_size: {pipeline_batch_size}",
        f"    ls_heap_size: {yaml_str(heap_size)}",
        f"    ls_index_pattern: {yaml_str(index_pattern)}",
        "  tasks:",

        # ---------- Preflight ----------
        "    - name: Resolve node slug for Logstash node.name",
        "      ansible.builtin.set_fact:",
        "        ls_node_slug: \"{{ ls_node_slug | default(inventory_hostname_short) | default(inventory_hostname) }}\"",
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
        "    - name: Stop existing logstash-docker unit before Docker maintenance",
        "      ansible.builtin.systemd:",
        "        name: logstash-docker",
        "        state: stopped",
        "      failed_when: false",
        "    - name: Reset failed logstash-docker unit state before Docker maintenance",
        "      ansible.builtin.command: systemctl reset-failed logstash-docker",
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
        "    - name: Pull Logstash image",
        "      ansible.builtin.command: docker pull {{ ls_image }}",
        "      register: _ls_pull",
        "      retries: 3",
        "      delay: 5",
        "      until: _ls_pull.rc == 0",
        "      changed_when: \"'Downloaded newer image' in _ls_pull.stdout or 'Pull complete' in _ls_pull.stdout\"",

        # ---------- Cleanup stale containers ----------
        "    - name: Stop existing logstash-docker unit before recreate",
        "      ansible.builtin.systemd:",
        "        name: logstash-docker",
        "        state: stopped",
        "      failed_when: false",
        "    - name: Reset failed logstash-docker unit state",
        "      ansible.builtin.command: systemctl reset-failed logstash-docker",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Remove stale Logstash Docker containers",
        "      ansible.builtin.shell: |",
        "        set +e",
        "        command -v docker >/dev/null 2>&1 || exit 0",
        "        docker rm -f {{ ls_container_name }} 2>/dev/null || true",
        "        for c in $(docker ps -aq --filter 'name=^/logstash-'); do",
        "          docker rm -f \"$c\" 2>/dev/null || true",
        "        done",
        "        exit 0",
        "      args:",
        "        executable: /bin/bash",
        "      changed_when: false",
        "      failed_when: false",

        # ---------- Logstash config + pipeline ----------
        "    - name: Create Logstash config directories",
        "      ansible.builtin.file:",
        "        path: \"{{ item }}\"",
        "        state: directory",
        "        owner: root",
        "        group: root",
        "        mode: '0755'",
        "      loop:",
        "        - /etc/opensible/logstash",
        "        - /etc/opensible/logstash/pipeline",
        "    - name: Write logstash.yml",
        "      ansible.builtin.copy:",
        "        dest: /etc/opensible/logstash/logstash.yml",
        "        owner: root",
        "        group: root",
        "        mode: '0644'",
        "        content: |",
        "          node.name: {{ ls_node_slug | to_json }}",
        "          path.data: /usr/share/logstash/data",
        "          path.logs: /usr/share/logstash/logs",
        "          pipeline.workers: {{ ls_pipeline_workers }}",
        "          pipeline.batch.size: {{ ls_pipeline_batch_size }}",
        "          api.http.host: \"0.0.0.0\"",
        "          api.http.port: {{ ls_http_api_port }}",
        "          xpack.monitoring.enabled: false",
        "      register: _ls_config",
        "    - name: Write default pipeline (beats -> elasticsearch)",
        "      ansible.builtin.copy:",
        "        dest: /etc/opensible/logstash/pipeline/main.conf",
        "        owner: root",
        "        group: root",
        "        mode: '0644'",
        "        content: |",
        "          input {",
        "            beats {",
        "              port => {{ ls_beats_port }}",
        "            }",
        "          }",
        "          output {",
        "            elasticsearch {",
        f"              hosts => {es_hosts_ruby}",
        "              index => \"{{ ls_index_pattern }}\"",
        "              {% if ls_security_enabled %}",
        "              user => \"{{ ls_es_username }}\"",
        "              password => \"{{ ls_es_password }}\"",
        "              ssl_verification_mode => \"{{ ls_es_ssl_mode }}\"",
        "              {% endif %}",
        "            }",
        "          }",
        "      register: _ls_pipeline",

        # ---------- Systemd unit ----------
        "    - name: Write logstash-docker systemd unit",
        "      ansible.builtin.copy:",
        "        dest: /etc/systemd/system/logstash-docker.service",
        "        mode: '0644'",
        "        content: |",
        "          [Unit]",
        f"          Description=OpenSible Logstash ({cluster_id}) via Docker",
        "          Requires=docker.service",
        "          After=docker.service network-online.target",
        "          Wants=network-online.target",
        "",
        "          [Service]",
        "          Type=simple",
        "          Restart=always",
        "          RestartSec=5",
        "          TimeoutStartSec=0",
        "          ExecStartPre=-/usr/bin/docker stop {{ ls_container_name }}",
        "          ExecStartPre=-/usr/bin/docker rm {{ ls_container_name }}",
        "          ExecStart=/usr/bin/docker run --rm \\",
        "            --name {{ ls_container_name }} \\",
        f"            -e LS_JAVA_OPTS=\"-Xms{heap_size} -Xmx{heap_size}\" \\",
        "            -v /etc/opensible/logstash/logstash.yml:/usr/share/logstash/config/logstash.yml:ro \\",
        "            -v /etc/opensible/logstash/pipeline:/usr/share/logstash/pipeline:ro \\",
        f"            -p {beats_port}:{beats_port} \\",
        f"            -p 127.0.0.1:{http_api_port}:{http_api_port} \\",
        "            {{ ls_image }}",
        "          ExecStop=/usr/bin/docker stop {{ ls_container_name }}",
        "",
        "          [Install]",
        "          WantedBy=multi-user.target",
        "      register: _ls_unit",
        "    - name: Reload systemd for logstash-docker unit",
        "      ansible.builtin.systemd:",
        "        daemon_reload: true",
        "      when: _ls_unit.changed",
        "    - name: Enable + start logstash-docker",
        "      ansible.builtin.systemd:",
        "        name: logstash-docker",
        "        enabled: true",
        "        state: started",
        "    - name: Restart logstash-docker if unit / config / pipeline changed",
        "      when: _ls_unit.changed or _ls_config.changed or _ls_pipeline.changed",
        "      ansible.builtin.systemd:",
        "        name: logstash-docker",
        "        state: restarted",
    ]

    # ---------- Firewall ----------
    if open_firewall:
        parts += [
            "    - name: Open Logstash ports (ufw, if active)",
            "      ansible.builtin.shell: |",
            "        set -e",
            "        command -v ufw >/dev/null 2>&1 || exit 0",
            "        ufw status | grep -q 'Status: active' || exit 0",
            f"        ufw allow {beats_port}/tcp || true",
            *([f"        ufw allow {health_http_port}/tcp || true"] if health_http_enabled else []),
            "      changed_when: false",
            "      failed_when: false",
            "    - name: Open Logstash ports (firewalld, if active)",
            "      ansible.builtin.shell: |",
            "        set -e",
            "        command -v firewall-cmd >/dev/null 2>&1 || exit 0",
            "        firewall-cmd --state >/dev/null 2>&1 || exit 0",
            f"        firewall-cmd --permanent --add-port={beats_port}/tcp || true",
            *([f"        firewall-cmd --permanent --add-port={health_http_port}/tcp || true"] if health_http_enabled else []),
            "        firewall-cmd --reload || true",
            "      changed_when: false",
            "      failed_when: false",
        ]

    # ---------- Wait for Logstash monitoring API ----------
    parts += [
        f"    - name: Wait for Logstash monitoring API port {http_api_port} locally",
        "      ansible.builtin.wait_for:",
        "        host: 127.0.0.1",
        f"        port: {http_api_port}",
        "        timeout: 300",
        "      register: _ls_wait",
        "      ignore_errors: true",
        "    - name: Collect Logstash startup diagnostics when API did not open",
        "      when: _ls_wait is failed",
        "      ansible.builtin.shell: |",
        "        set +e",
        "        echo '== systemctl status logstash-docker =='",
        "        systemctl status logstash-docker --no-pager -l 2>&1 || true",
        "        echo '== recent logstash-docker journal =='",
        "        journalctl -u logstash-docker -n 160 --no-pager 2>&1 || true",
        "        echo '== docker containers =='",
        "        docker ps -a 2>&1 || true",
        "        echo '== logstash container logs =='",
        "        docker logs --tail 200 {{ ls_container_name }} 2>&1 || true",
        "        echo '== docker bridge network =='",
        "        docker network inspect bridge 2>&1 || true",
        "        ip link show docker0 2>&1 || true",
        "      args:",
        "        executable: /bin/bash",
        "      register: _ls_startup_diag",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Show Logstash startup diagnostics",
        "      when: _ls_wait is failed",
        "      ansible.builtin.debug:",
        "        var: _ls_startup_diag.stdout_lines",
        "    - name: Fail if Logstash monitoring API did not open",
        "      when: _ls_wait is failed",
        "      ansible.builtin.fail:",
        f"        msg: Logstash did not open local API port {http_api_port}; see diagnostics above.",
    ]

    # ---------- Health dashboard ----------
    if health_http_enabled:
        parts += _health_tasks(
            cluster_id, http_api_port, health_http_port,
            es_hosts_csv,
            es_user if security_enabled else "",
            es_password if security_enabled else "",
        )

    # ------------------------------------------------------------------ #
    # PLAY 2 — verification (run once)
    # ------------------------------------------------------------------ #
    parts += [
        "- name: Verify Logstash availability",
        f"  hosts: {play_hosts}",
        f"  become: {become}",
        "  gather_facts: false",
        "  run_once: true",
        "  tasks:",
        "    - name: Query Logstash /_node",
        "      ansible.builtin.uri:",
        f"        url: \"http://127.0.0.1:{http_api_port}/_node\"",
        "        return_content: true",
        "      register: _ls_status",
        "      retries: 60",
        "      delay: 5",
        "      until: _ls_status.status == 200",
        "    - name: Logstash summary",
        "      ansible.builtin.debug:",
        "        msg:",
        f"          - \"Logstash cluster '{cluster_id}' is up (name={cluster_name}).\"",
        "          - \"Version: {{ (_ls_status.json | default({})).version | default('?') }} | node: {{ (_ls_status.json | default({})).name | default('?') }}\"",
        f"          - \"Beats input: <node-ip>:{beats_port}  (point Filebeat.hosts at every node for HA)\"",
        "",
    ]

    return "\n".join(parts)
