"""Template: Elasticsearch HA cluster on Docker containers.

Deploys a production-style Elasticsearch cluster across N nodes (recommended
3 or 5) using the official Elasticsearch Docker image. Each node runs a
single `elasticsearch` container managed by a dedicated systemd unit
(`elasticsearch-docker.service`) so the container survives host reboots and
docker daemon restarts.

Highlights:

  * Docker Engine ensured (installed via convenience script if missing).
  * Per-node systemd unit runs `docker run` against the pinned image tag.
  * Cluster formation via `discovery.seed_hosts` and
    `cluster.initial_master_nodes` (first bootstrap only).
  * Kernel tuning: vm.max_map_count=262144 (Elasticsearch requirement).
  * Optional built-in HTTP health dashboard on every node exposing an HTML
    view + /health.json (green / yellow / red + per-shard summary).
  * Optional X-Pack security (basic auth for the elastic user) — off by
    default so the cluster works out-of-the-box; flip on for prod.

Marker: 2026-07-elasticsearch-cluster-v1
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
#   /            HTML dashboard (auto-refresh 5s) — cluster + local node
#   /health.json JSON payload (200 green/yellow, 503 red / unreachable)
#   /live        liveness (always 200 if process is up)
# ---------------------------------------------------------------------------
_HEALTH_SCRIPT = r'''#!/usr/bin/env python3
"""OpenSible Elasticsearch HA HTTP health dashboard."""
import base64, json, os, html, socket, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib import request as _rq, error as _er

CFG = {
    "cluster_name": os.environ.get("CLUSTER_NAME", ""),
    "es_host": os.environ.get("ES_HOST", "127.0.0.1"),
    "es_port": int(os.environ.get("ES_PORT", "9200")),
    "es_scheme": os.environ.get("ES_SCHEME", "http"),
    "es_user": os.environ.get("ES_USER", ""),
    "es_password": os.environ.get("ES_PASSWORD", ""),
    "http_port": int(os.environ.get("HTTP_PORT", "9280")),
}


def _get(path, timeout=4):
    url = f"{CFG['es_scheme']}://{CFG['es_host']}:{CFG['es_port']}{path}"
    try:
        req = _rq.Request(url, headers={"Accept": "application/json"})
        if CFG["es_user"]:
            tok = base64.b64encode(f"{CFG['es_user']}:{CFG['es_password']}".encode()).decode()
            req.add_header("Authorization", f"Basic {tok}")
        ctx = None
        if CFG["es_scheme"] == "https":
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


def _json(path):
    code, body = _get(path)
    data = {}
    try:
        data = json.loads(body) if body and body.startswith(("{", "[")) else {}
    except Exception:
        data = {}
    return code, data, body


def collect():
    hc, hj, hb = _json("/_cluster/health")
    sc, sj, sb = _json("/")
    nc, nj, nb = _json("/_cat/nodes?format=json&h=name,ip,node.role,master,heap.percent,cpu,load_1m,version")
    ic, ij, ib = _json("/_cat/indices?format=json&h=index,health,status,pri,rep,docs.count,store.size&s=index")
    healthy = hc == 200 and (hj.get("status") in ("green", "yellow"))
    return {
        "hostname": socket.gethostname(),
        "ts": int(time.time()),
        "cluster_name": CFG["cluster_name"],
        "health": {"code": hc, "data": hj, "raw": hb[:4000]},
        "server": {"code": sc, "data": sj, "raw": sb[:4000]},
        "nodes": {"code": nc, "data": nj if isinstance(nj, list) else [], "raw": nb[:4000]},
        "indices": {"code": ic, "data": ij if isinstance(ij, list) else [], "raw": ib[:4000]},
        "healthy": bool(healthy),
    }


def esc(v):
    return html.escape("" if v is None else str(v), quote=True)


def badge(status):
    s = (status or "").lower()
    color = {"green": "#238636", "yellow": "#9e6a03", "red": "#da3633"}.get(s, "#6e7681")
    return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:10px;font-weight:600">{esc(s or "unknown")}</span>'


def render_html(d):
    h = d["health"]["data"] if isinstance(d["health"]["data"], dict) else {}
    s = d["server"]["data"] if isinstance(d["server"]["data"], dict) else {}
    nodes_rows = ""
    for n in d["nodes"]["data"]:
        nodes_rows += (
            "<tr>"
            f"<td>{esc(n.get('name'))}</td>"
            f"<td>{esc(n.get('ip'))}</td>"
            f"<td>{esc(n.get('node.role'))}</td>"
            f"<td>{esc(n.get('master'))}</td>"
            f"<td>{esc(n.get('heap.percent'))}%</td>"
            f"<td>{esc(n.get('cpu'))}%</td>"
            f"<td>{esc(n.get('load_1m'))}</td>"
            f"<td>{esc(n.get('version'))}</td>"
            "</tr>"
        )
    if not nodes_rows:
        nodes_rows = '<tr><td colspan="8" style="opacity:.7">no nodes</td></tr>'
    idx_rows = ""
    for i in d["indices"]["data"][:200]:
        idx_rows += (
            "<tr>"
            f"<td>{esc(i.get('index'))}</td>"
            f"<td>{badge(i.get('health'))}</td>"
            f"<td>{esc(i.get('status'))}</td>"
            f"<td>{esc(i.get('pri'))}</td>"
            f"<td>{esc(i.get('rep'))}</td>"
            f"<td>{esc(i.get('docs.count'))}</td>"
            f"<td>{esc(i.get('store.size'))}</td>"
            "</tr>"
        )
    if not idx_rows:
        idx_rows = '<tr><td colspan="7" style="opacity:.7">no indices</td></tr>'
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="5">
<title>Elasticsearch — {esc(d['cluster_name'])} @ {esc(d['hostname'])}</title>
<style>
body{{background:#0d1117;color:#c9d1d9;font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:24px}}
h1{{margin:0 0 4px 0;font-size:18px}} h2{{margin:24px 0 8px 0;font-size:14px;color:#8b949e;text-transform:uppercase;letter-spacing:.05em}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin-bottom:16px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{padding:6px 10px;border-bottom:1px solid #21262d;text-align:left}} th{{color:#8b949e;font-weight:600}}
pre{{background:#0b0f14;padding:10px;border-radius:6px;overflow:auto;font-size:12px;color:#c9d1d9;max-height:220px}}
.k{{color:#8b949e}} .v{{color:#c9d1d9;font-weight:600}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px 24px}}
</style></head><body>
<div class="card">
  <h1>Elasticsearch cluster <span class="v">{esc(d['cluster_name'])}</span> — node <span class="v">{esc(d['hostname'])}</span></h1>
  <div style="margin-top:6px">Status: {badge(h.get('status'))} &middot; Overall: {badge('green' if d['healthy'] else 'red')}</div>
</div>
<div class="card"><h2>Cluster health</h2>
  <div class="grid">
    <div><span class="k">nodes</span><br><span class="v">{esc(h.get('number_of_nodes'))}</span></div>
    <div><span class="k">data nodes</span><br><span class="v">{esc(h.get('number_of_data_nodes'))}</span></div>
    <div><span class="k">active shards</span><br><span class="v">{esc(h.get('active_shards'))}</span></div>
    <div><span class="k">relocating</span><br><span class="v">{esc(h.get('relocating_shards'))}</span></div>
    <div><span class="k">initializing</span><br><span class="v">{esc(h.get('initializing_shards'))}</span></div>
    <div><span class="k">unassigned</span><br><span class="v">{esc(h.get('unassigned_shards'))}</span></div>
    <div><span class="k">pending tasks</span><br><span class="v">{esc(h.get('number_of_pending_tasks'))}</span></div>
    <div><span class="k">version</span><br><span class="v">{esc((s.get('version') or {}).get('number'))}</span></div>
  </div>
</div>
<div class="card"><h2>Nodes (_cat/nodes)</h2>
<table><thead><tr><th>name</th><th>ip</th><th>roles</th><th>master</th><th>heap%</th><th>cpu%</th><th>load1m</th><th>version</th></tr></thead>
<tbody>{nodes_rows}</tbody></table></div>
<div class="card"><h2>Indices (_cat/indices)</h2>
<table><thead><tr><th>index</th><th>health</th><th>status</th><th>pri</th><th>rep</th><th>docs</th><th>size</th></tr></thead>
<tbody>{idx_rows}</tbody></table></div>
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


def _health_tasks(cluster_id: str, es_port: int, http_port: int,
                  es_user: str, es_password: str) -> List[str]:
    lines: List[str] = [
        "    - name: Ensure python3 present for Elasticsearch health service",
        "      ansible.builtin.package:",
        "        name: python3",
        "        state: present",
        "      failed_when: false",
        "    - name: Install elasticsearch-health dashboard script",
        "      ansible.builtin.copy:",
        "        dest: /usr/local/bin/opensible-elasticsearch-health.py",
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
        "    - name: Install elasticsearch-health systemd unit",
        "      ansible.builtin.copy:",
        "        dest: /etc/systemd/system/opensible-elasticsearch-health.service",
        "        owner: root",
        "        group: root",
        "        mode: '0644'",
        "        content: |",
        "          [Unit]",
        f"          Description=OpenSible Elasticsearch HTTP health dashboard ({cluster_id})",
        "          After=network-online.target elasticsearch-docker.service",
        "          Wants=network-online.target",
        "          [Service]",
        "          Type=simple",
        f"          Environment=HTTP_PORT={http_port}",
        f"          Environment=CLUSTER_NAME={cluster_id}",
        "          Environment=ES_HOST=127.0.0.1",
        f"          Environment=ES_PORT={es_port}",
        "          Environment=ES_SCHEME=http",
        f"          Environment=ES_USER={es_user}",
        f"          Environment=ES_PASSWORD={es_password}",
        "          ExecStart=/usr/bin/env python3 /usr/local/bin/opensible-elasticsearch-health.py",
        "          Restart=on-failure",
        "          RestartSec=3",
        "          User=root",
        "          [Install]",
        "          WantedBy=multi-user.target",
        "      register: _es_health_unit",
        "    - name: Enable and start elasticsearch-health service",
        "      ansible.builtin.systemd:",
        "        name: opensible-elasticsearch-health.service",
        "        enabled: true",
        "        state: restarted",
        "        daemon_reload: true",
        f"    - name: Wait for Elasticsearch health HTTP port {http_port}",
        "      ansible.builtin.wait_for:",
        "        host: 127.0.0.1",
        f"        port: {http_port}",
        "        timeout: 30",
        "    - name: Report Elasticsearch health dashboard URL",
        "      ansible.builtin.debug:",
        f"        msg: \"Elasticsearch health dashboard: http://{{{{ ansible_host | default(inventory_hostname) }}}}:{http_port}/  (JSON: /health.json)\"",
    ]
    return lines


TEMPLATE = {
    "id": "elasticsearch-cluster",
    "name": "Elasticsearch HA (Docker cluster)",
    "category": "Databases",
    "icon": "database",
    "description": (
        "Production Elasticsearch cluster across N nodes running the "
        "official Elasticsearch Docker image. Each node runs one container "
        "managed by a dedicated systemd unit; cluster formation uses "
        "discovery.seed_hosts and cluster.initial_master_nodes. Add each "
        "node under 'Cluster nodes' — the first entry seeds the initial "
        "master set. Use 3+ master-eligible nodes for real HA."
    ),
    "tags": ["elasticsearch", "elastic", "docker", "cluster", "ha", "search"],
    "variables": [
        # ---------- Cluster identity ----------
        {"name": "cluster_id", "label": "Cluster name",
         "type": "string", "default": "opensible-es",
         "help": "Free-form label. Used as cluster.name and for filenames."},
        {"name": "es_version", "label": "Elasticsearch image tag",
         "type": "string", "default": "8.15.3",
         "help": "Docker Hub tag under docker.elastic.co/elasticsearch/elasticsearch."},

        # ---------- Hosts / HA ----------
        {"name": "ssh_user_default", "label": "Default SSH user",
         "type": "string", "default": "root"},
        {"name": "ssh_port_default", "label": "Default SSH port",
         "type": "number", "default": 22},
        {"name": "nodes", "label": "Cluster nodes (first = initial master seed)",
         "type": "nodes", "required": False,
         "help": "Add 3 nodes for real HA. The first entry's name is used to bootstrap the initial master set alongside the others.",
         "default": [
             {"name": "es-1", "ip": "", "ssh_user": "", "ssh_port": ""},
             {"name": "es-2", "ip": "", "ssh_user": "", "ssh_port": ""},
             {"name": "es-3", "ip": "", "ssh_user": "", "ssh_port": ""},
         ]},

        # ---------- Networking ----------
        {"name": "http_port", "label": "Elasticsearch HTTP port",
         "type": "number", "default": 9200},
        {"name": "transport_port", "label": "Elasticsearch transport port",
         "type": "number", "default": 9300},
        {"name": "announce_host", "label": "Advertised host (per node)",
         "type": "string",
         "default": "{{ ansible_host | default(inventory_hostname) }}",
         "help": "Jinja expression evaluated per host. Used for network.publish_host."},
        {"name": "health_http_enabled", "label": "Enable HTTP health dashboard",
         "type": "boolean", "default": True,
         "help": "Install a small HTTP service on each node exposing / (HTML dashboard), /health.json and /live with the cluster + node + index status."},
        {"name": "health_http_port", "label": "Health HTTP port",
         "type": "number", "default": 9280,
         "help": "Port for the health dashboard. Visit http://<node-ip>:<port>/ from your browser."},

        # ---------- Security ----------
        {"name": "security_enabled", "label": "Enable X-Pack basic security",
         "type": "boolean", "default": False,
         "help": "When enabled, sets xpack.security.enabled=true and a bootstrap password for the built-in 'elastic' user. Leave off for a quick internal/lab cluster."},
        {"name": "elastic_password", "label": "'elastic' user password",
         "type": "password", "required": False, "default": "",
         "help": "Required when security is enabled. Also fed into ELASTIC_PASSWORD."},

        # ---------- JVM / storage ----------
        {"name": "heap_size", "label": "JVM heap size",
         "type": "string", "default": "1g",
         "help": "Sets -Xms and -Xmx. Recommendation: 50%% of RAM, cap at 31g."},
        {"name": "data_dir", "label": "Host data directory",
         "type": "string", "default": "/var/lib/elasticsearch-data",
         "help": "Bind-mounted into the container at /usr/share/elasticsearch/data."},

        # ---------- Ops ----------
        {"name": "open_firewall", "label": "Open ports in UFW/firewalld",
         "type": "boolean", "default": True},
        {"name": "become", "label": "Run as sudo (become)",
         "type": "boolean", "default": True},
        VAULT_FILES_VARIABLE,
    ],
}


def suggested_filename(values: Dict[str, Any]) -> str:
    stem = slugify(values.get("cluster_id"), "elasticsearch")
    return f"{stem}-elasticsearch-cluster.yml"


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
        name = str(n.get("name") or f"es-{i+1}").strip() or f"es-{i+1}"
        user = str(n.get("ssh_user") or default_user or "root").strip() or "root"
        try:
            port = int(n.get("ssh_port") or default_port or 22)
        except Exception:
            port = 22
        out.append({
            "name": name,
            "node_slug": slugify(name, f"es-{i+1}") or f"es-{i+1}",
            "ip": ip,
            "ssh_user": user,
            "ssh_port": port,
            "index": i + 1,
        })
    return out


def render(values: Dict[str, Any], targets: Dict[str, Any]) -> str:
    become = "true" if values.get("become", True) else "false"

    cluster_id = str(values.get("cluster_id") or "opensible-es").strip() or "opensible-es"
    es_version = str(values.get("es_version") or "8.15.3").strip().lstrip("v") or "8.15.3"
    http_port = int(values.get("http_port") or 9200)
    transport_port = int(values.get("transport_port") or 9300)
    announce = values.get("announce_host") or "{{ ansible_host | default(inventory_hostname) }}"
    health_http_enabled = bool(values.get("health_http_enabled", True))
    health_http_port = int(values.get("health_http_port") or 9280)

    security_enabled = bool(values.get("security_enabled", False))
    elastic_password = str(values.get("elastic_password") or "")

    heap_size = str(values.get("heap_size") or "1g").strip() or "1g"
    data_dir = str(values.get("data_dir") or "/var/lib/elasticsearch-data").strip() or "/var/lib/elasticsearch-data"
    open_firewall = bool(values.get("open_firewall", True))

    nodes = _norm_nodes(
        values.get("nodes"),
        values.get("ssh_user_default") or "root",
        values.get("ssh_port_default") or 22,
    )

    cluster_group = slugify(cluster_id, "es").replace("-", "_") + "_nodes"
    cluster_name = slugify(cluster_id, "opensible-es")
    container_name = f"elasticsearch-{cluster_name}"

    if nodes:
        seed_hosts_expr = ",".join(f"{n['ip']}:{transport_port}" for n in nodes)
        initial_masters_expr = ",".join(n["node_slug"] for n in nodes)
    else:
        seed_hosts_expr = (
            "{% for h in ansible_play_hosts %}"
            f"{{{{ h }}}}:{transport_port}"
            "{% if not loop.last %},{% endif %}{% endfor %}"
        )
        initial_masters_expr = (
            "{% for h in ansible_play_hosts %}"
            "{{ hostvars[h].inventory_hostname_short | default(h) }}"
            "{% if not loop.last %},{% endif %}{% endfor %}"
        )

    parts: List[str] = ["---"]
    parts.append("# OpenSible elasticsearch-cluster template generation: 2026-07-elasticsearch-cluster-v1")
    parts.append(f"# Rendered from template: {TEMPLATE['name']}")
    parts.append(f"# Cluster: {cluster_id} | image tag: {es_version} | nodes: {len(nodes) if nodes else 'from targets'}")
    parts.append("")

    # ------------------------------------------------------------------ #
    # PLAY 0 — dynamic inventory
    # ------------------------------------------------------------------ #
    if nodes:
        parts += [
            "- name: Register Elasticsearch nodes into a dynamic inventory group",
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
            "        es_node_name: \"{{ item.name }}\"",
            "        es_node_slug: \"{{ item.node_slug }}\"",
            "        es_node_index: \"{{ item.index }}\"",
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
        initial_seed_ip = nodes[0]["ip"]
    else:
        play_hosts = render_hosts(targets)
        initial_seed_ip = "{{ ansible_play_hosts[0] }}"

    # ------------------------------------------------------------------ #
    # PLAY 1 — install docker, kernel tuning, run ES container per node
    # ------------------------------------------------------------------ #
    parts += [
        f"- name: Deploy Elasticsearch {es_version} (Docker) on every node",
        f"  hosts: {play_hosts}",
        f"  become: {become}",
        "  gather_facts: true",
        "  any_errors_fatal: true",
        *vars_files_lines(parse_vault_files(values.get("vault_files"))),
        "  vars:",
        f"    es_cluster_id: {yaml_str(cluster_id)}",
        f"    es_cluster_name: {yaml_str(cluster_name)}",
        f"    es_container_name: {yaml_str(container_name)}",
        f"    es_image: \"docker.elastic.co/elasticsearch/elasticsearch:{es_version}\"",
        f"    es_http_port: {http_port}",
        f"    es_transport_port: {transport_port}",
        f"    es_announce_host: \"{announce}\"",
        f"    es_data_dir: {yaml_str(data_dir)}",
        f"    es_heap_size: {yaml_str(heap_size)}",
        f"    es_seed_hosts: \"{seed_hosts_expr}\"",
        f"    es_initial_masters: \"{initial_masters_expr}\"",
        f"    es_initial_seed_ip: {yaml_str(initial_seed_ip)}",
        f"    es_security_enabled: {'true' if security_enabled else 'false'}",
        f"    es_elastic_password: {yaml_str(elastic_password)}",
        "  tasks:",

        # ---------- Preflight ----------
        "    - name: Resolve node slug for Elasticsearch node.name",
        "      ansible.builtin.set_fact:",
        "        es_node_slug: \"{{ es_node_slug | default(inventory_hostname_short) | default(inventory_hostname) }}\"",
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

        # ---------- Kernel tuning (required by Elasticsearch) ----------
        "    - name: Set vm.max_map_count for Elasticsearch (runtime)",
        "      ansible.posix.sysctl:",
        "        name: vm.max_map_count",
        "        value: '262144'",
        "        sysctl_set: true",
        "        state: present",
        "        reload: true",
        "      failed_when: false",
        "    - name: Fallback — write vm.max_map_count via sysctl.d if posix module missing",
        "      ansible.builtin.copy:",
        "        dest: /etc/sysctl.d/99-elasticsearch.conf",
        "        mode: '0644'",
        "        content: |",
        "          vm.max_map_count=262144",
        "      register: _es_sysctl",
        "    - name: Apply sysctl fallback",
        "      when: _es_sysctl.changed",
        "      ansible.builtin.command: sysctl --system",
        "      changed_when: false",
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
        "    - name: Ensure containerd service is enabled and running",
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
        "    - name: Stop existing elasticsearch-docker unit before Docker maintenance",
        "      ansible.builtin.systemd:",
        "        name: elasticsearch-docker",
        "        state: stopped",
        "      failed_when: false",
        "    - name: Reset failed elasticsearch-docker unit state before Docker maintenance",
        "      ansible.builtin.command: systemctl reset-failed elasticsearch-docker",
        "      changed_when: false",
        "      failed_when: false",

        # ---------- Data dir ----------
        "    - name: Ensure Elasticsearch data dir on host",
        "      ansible.builtin.file:",
        "        path: \"{{ es_data_dir }}\"",
        "        state: directory",
        # Elasticsearch container runs as UID/GID 1000 (elasticsearch)
        "        owner: '1000'",
        "        group: '0'",
        "        mode: '0770'",

        # ---------- Verify docker <-> containerd link (self-heal) ----------
        # A stale containerd socket (common after partial uninstalls) makes
        # `docker pull` fail immediately with:
        #   dial unix:///run/containerd/containerd.sock: timeout
        # Probe with a cheap `docker info` and restart the stack if broken.
        "    - name: Probe docker daemon (detect stale containerd socket)",
        "      ansible.builtin.command: docker info --format '{{ '{{' }}.ServerVersion{{ '}}' }}'",
        "      register: _docker_probe",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Restart containerd when docker daemon is unhealthy",
        "      when: _docker_probe.rc != 0",
        "      ansible.builtin.systemd:",
        "        name: containerd",
        "        state: restarted",
        "      failed_when: false",
        "    - name: Restart docker when docker daemon is unhealthy",
        "      when: _docker_probe.rc != 0",
        "      ansible.builtin.systemd:",
        "        name: docker",
        "        state: restarted",
        "    - name: Wait for docker daemon to become responsive",
        "      when: _docker_probe.rc != 0",
        "      ansible.builtin.command: docker info --format '{{ '{{' }}.ServerVersion{{ '}}' }}'",
        "      register: _docker_probe2",
        "      retries: 20",
        "      delay: 3",
        "      until: _docker_probe2.rc == 0",
        "      changed_when: false",
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
        "    - name: Pull Elasticsearch image",
        "      ansible.builtin.command: docker pull {{ es_image }}",
        "      register: _es_pull",
        "      retries: 3",
        "      delay: 5",
        "      until: _es_pull.rc == 0",
        "      changed_when: \"'Downloaded newer image' in _es_pull.stdout or 'Pull complete' in _es_pull.stdout\"",

        # ---------- Remove stale containers from a prior failed uninstall/run ----------
        # If an older Elasticsearch container still owns 9200/9300, the new
        # systemd unit starts and immediately exits, leaving wait_for to time
        # out with no useful context. Clean only Elasticsearch containers here.
        "    - name: Stop existing elasticsearch-docker unit before recreate",
        "      ansible.builtin.systemd:",
        "        name: elasticsearch-docker",
        "        state: stopped",
        "      failed_when: false",
        "    - name: Reset failed elasticsearch-docker unit state",
        "      ansible.builtin.command: systemctl reset-failed elasticsearch-docker",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Remove stale Elasticsearch Docker containers",
        "      ansible.builtin.shell: |",
        "        set +e",
        "        command -v docker >/dev/null 2>&1 || exit 0",
        "        docker rm -f {{ es_container_name }} 2>/dev/null || true",
        "        for c in $(docker ps -aq --filter 'name=^/elasticsearch-'); do",
        "          docker rm -f \"$c\" 2>/dev/null || true",
        "        done",
        "        for c in $(docker ps -aq 2>/dev/null); do",
        "          img=$(docker inspect --format '{{ '{{' }}.Config.Image{{ '}}' }}' \"$c\" 2>/dev/null)",
        "          case \"$img\" in",
        "            *docker.elastic.co/elasticsearch/elasticsearch*) docker rm -f \"$c\" 2>/dev/null || true ;;",
        "          esac",
        "        done",
        "        exit 0",
        "      args:",
        "        executable: /bin/bash",
        "      changed_when: false",
        "      failed_when: false",

        # ---------- Systemd unit that owns the container ----------
        "    - name: Write elasticsearch-docker systemd unit",
        "      ansible.builtin.copy:",
        "        dest: /etc/systemd/system/elasticsearch-docker.service",
        "        mode: '0644'",
        "        content: |",
        "          [Unit]",
        f"          Description=OpenSible Elasticsearch ({cluster_id}) via Docker",
        "          Requires=docker.service",
        "          After=docker.service network-online.target",
        "          Wants=network-online.target",
        "",
        "          [Service]",
        "          Type=simple",
        "          Restart=always",
        "          RestartSec=5",
        "          TimeoutStartSec=0",
        "          ExecStartPre=-/usr/bin/docker stop {{ es_container_name }}",
        "          ExecStartPre=-/usr/bin/docker rm {{ es_container_name }}",
        "          ExecStart=/usr/bin/docker run --rm \\",
        "            --name {{ es_container_name }} \\",
        "            --ulimit memlock=-1:-1 \\",
        "            --ulimit nofile=65536:65536 \\",
        "            -e cluster.name={{ es_cluster_name }} \\",
        "            -e node.name={{ es_node_slug }} \\",
        "            -e network.host=0.0.0.0 \\",
        "            -e network.publish_host={{ es_announce_host }} \\",
        f"            -e http.port={http_port} \\",
        f"            -e transport.port={transport_port} \\",
        "            -e discovery.seed_hosts={{ es_seed_hosts }} \\",
        "            -e cluster.initial_master_nodes={{ es_initial_masters }} \\",
        "            -e bootstrap.memory_lock=true \\",
        "            -e xpack.security.enabled={{ 'true' if es_security_enabled else 'false' }} \\",
        "            -e xpack.security.http.ssl.enabled=false \\",
        "            -e xpack.security.transport.ssl.enabled=false \\",
        "            {% if es_security_enabled %}-e ELASTIC_PASSWORD={{ es_elastic_password }} \\",
        "            {% endif %}-e ES_JAVA_OPTS=\"-Xms{{ es_heap_size }} -Xmx{{ es_heap_size }}\" \\",
        f"            -p {http_port}:{http_port} \\",
        f"            -p {transport_port}:{transport_port} \\",
        "            -v {{ es_data_dir }}:/usr/share/elasticsearch/data \\",
        "            {{ es_image }}",
        "          ExecStop=/usr/bin/docker stop {{ es_container_name }}",
        "",
        "          [Install]",
        "          WantedBy=multi-user.target",
        "      register: _es_unit",
        "    - name: Reload systemd for elasticsearch-docker unit",
        "      ansible.builtin.systemd:",
        "        daemon_reload: true",
        "      when: _es_unit.changed",
        "    - name: Enable + start elasticsearch-docker",
        "      ansible.builtin.systemd:",
        "        name: elasticsearch-docker",
        "        enabled: true",
        "        state: started",
        "    - name: Restart elasticsearch-docker if unit changed",
        "      when: _es_unit.changed",
        "      ansible.builtin.systemd:",
        "        name: elasticsearch-docker",
        "        state: restarted",
    ]

    # ---------- Firewall ----------
    if open_firewall:
        parts += [
            "    - name: Open Elasticsearch ports (ufw, if active)",
            "      ansible.builtin.shell: |",
            "        set -e",
            "        command -v ufw >/dev/null 2>&1 || exit 0",
            "        ufw status | grep -q 'Status: active' || exit 0",
            f"        ufw allow {http_port}/tcp || true",
            f"        ufw allow {transport_port}/tcp || true",
            *([f"        ufw allow {health_http_port}/tcp || true"] if health_http_enabled else []),
            "      changed_when: false",
            "      failed_when: false",
            "    - name: Open Elasticsearch ports (firewalld, if active)",
            "      ansible.builtin.shell: |",
            "        set -e",
            "        command -v firewall-cmd >/dev/null 2>&1 || exit 0",
            "        firewall-cmd --state >/dev/null 2>&1 || exit 0",
            f"        firewall-cmd --permanent --add-port={http_port}/tcp || true",
            f"        firewall-cmd --permanent --add-port={transport_port}/tcp || true",
            *([f"        firewall-cmd --permanent --add-port={health_http_port}/tcp || true"] if health_http_enabled else []),
            "        firewall-cmd --reload || true",
            "      changed_when: false",
            "      failed_when: false",
        ]

    # ---------- Wait for local Elasticsearch ----------
    parts += [
        f"    - name: Wait for Elasticsearch HTTP port {http_port} locally",
        "      ansible.builtin.wait_for:",
        "        host: 127.0.0.1",
        f"        port: {http_port}",
        "        timeout: 180",
        "      register: _es_wait",
        "      ignore_errors: true",
        "    - name: Collect Elasticsearch startup diagnostics when HTTP port did not open",
        "      when: _es_wait is failed",
        "      ansible.builtin.shell: |",
        "        set +e",
        "        echo '== systemctl status elasticsearch-docker =='",
        "        systemctl status elasticsearch-docker --no-pager -l 2>&1 || true",
        "        echo '== recent elasticsearch-docker journal =='",
        "        journalctl -u elasticsearch-docker -n 160 --no-pager 2>&1 || true",
        "        echo '== docker containers =='",
        "        docker ps -a 2>&1 || true",
        "        echo '== elasticsearch container logs =='",
        "        docker logs --tail 200 {{ es_container_name }} 2>&1 || true",
        "        echo '== docker bridge network =='",
        "        docker network inspect bridge 2>&1 || true",
        "        ip link show docker0 2>&1 || true",
        "        echo '== listening ports =='",
        "        (ss -ltnp 2>/dev/null || netstat -ltnp 2>/dev/null) | grep -E ':({{ es_http_port }}|{{ es_transport_port }})\\b' || true",
        "        echo '== data dir =='",
        "        ls -ld {{ es_data_dir }} 2>&1 || true",
        "        find {{ es_data_dir }} -maxdepth 2 -type f -name 'node.lock' -o -name '*.lock' 2>/dev/null | head -20 || true",
        "      args:",
        "        executable: /bin/bash",
        "      register: _es_startup_diag",
        "      changed_when: false",
        "      failed_when: false",
        "    - name: Show Elasticsearch startup diagnostics",
        "      when: _es_wait is failed",
        "      ansible.builtin.debug:",
        "        var: _es_startup_diag.stdout_lines",
        "    - name: Fail if Elasticsearch HTTP port did not open",
        "      when: _es_wait is failed",
        "      ansible.builtin.fail:",
        f"        msg: Elasticsearch did not open local HTTP port {http_port}; see diagnostics above for the Docker/journal error.",
    ]

    # ---------- Health dashboard ----------
    if health_http_enabled:
        parts += _health_tasks(
            cluster_id, http_port, health_http_port,
            "elastic" if security_enabled else "",
            elastic_password if security_enabled else "",
        )

    # ------------------------------------------------------------------ #
    # PLAY 2 — cluster verification (run once)
    # ------------------------------------------------------------------ #
    auth_arg = "-u elastic:{{ es_elastic_password }} " if security_enabled else ""
    parts += [
        "- name: Verify Elasticsearch cluster state",
        f"  hosts: {play_hosts}",
        f"  become: {become}",
        "  gather_facts: false",
        "  run_once: true",
        "  vars:",
        f"    es_elastic_password: {yaml_str(elastic_password)}",
        "  tasks:",
        "    - name: Query Elasticsearch _cluster/health",
        "      ansible.builtin.uri:",
        f"        url: \"http://127.0.0.1:{http_port}/_cluster/health\"",
        "        return_content: true",
        *(["        user: elastic", "        password: \"{{ es_elastic_password }}\"", "        force_basic_auth: true"] if security_enabled else []),
        "      register: _es_health",
        "      retries: 30",
        "      delay: 5",
        "      until: _es_health.status == 200 and (_es_health.json.status in ['green','yellow'])",
        "    - name: Cluster summary",
        "      ansible.builtin.debug:",
        "        msg:",
        f"          - \"Elasticsearch cluster '{cluster_id}' is up (name={cluster_name}).\"",
        "          - \"Status: {{ _es_health.json.status }} | nodes: {{ _es_health.json.number_of_nodes }} | data nodes: {{ _es_health.json.number_of_data_nodes }}\"",
        f"          - \"Test: curl {auth_arg}http://<node-ip>:{http_port}/_cluster/health?pretty\"",
        "",
    ]

    return "\n".join(parts)
