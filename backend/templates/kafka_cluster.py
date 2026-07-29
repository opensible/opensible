"""Template: Apache Kafka (KRaft) Cluster on systemd.

Deploys a production-grade Apache Kafka cluster in KRaft mode (no
ZooKeeper) directly on the target hosts using the official Apache Kafka
tarball and systemd. Each host runs as a combined controller+broker;
node IDs, controller quorum voters, and advertised listeners are
computed automatically from the broker list.

Two ways to select hosts:

1. **Broker nodes** (recommended for HA). Provide a list of
   ``{name, ip, ssh_user, ssh_port}`` in the UI. The playbook builds
   its inventory dynamically via ``add_host`` — no static inventory
   file required. Use 1 host for dev, 3+ for HA.
2. **Legacy targets.** If no brokers are provided, the play falls back
   to the generic ``hosts:``/``groups:`` targets picker.

Marker: 2026-07-kafka-systemd-v2
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
# Per-broker HTTP health dashboard (installed to /usr/local/bin/opensible-kafka-health.py)
# Endpoints:
#   /            HTML dashboard (auto-refresh 5s)
#   /health.json JSON payload (200 healthy / 503 degraded)
#   /live        liveness (always 200 if process is up)
# ---------------------------------------------------------------------------
_HEALTH_SCRIPT = r'''#!/usr/bin/env python3
"""OpenSible Kafka (KRaft) HTTP health dashboard."""
import json, os, subprocess, html, socket, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

CFG = {
    "install_dir": os.environ.get("KAFKA_INSTALL_DIR", "/opt/kafka"),
    "client_port": int(os.environ.get("KAFKA_CLIENT_PORT", "9092")),
    "controller_port": int(os.environ.get("KAFKA_CONTROLLER_PORT", "9093")),
    "cluster_id": os.environ.get("CLUSTER_ID", ""),
    "cluster_name": os.environ.get("CLUSTER_NAME", ""),
    "bootstrap": os.environ.get("BOOTSTRAP", "127.0.0.1:9092"),
    "http_port": int(os.environ.get("HTTP_PORT", "8080")),
}


def run(args, timeout=8):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return -1, "", str(e)


def _bin(name):
    return os.path.join(CFG["install_dir"], "bin", name)


def broker_api():
    rc, out, err = run([_bin("kafka-broker-api-versions.sh"),
                        "--bootstrap-server", CFG["bootstrap"]])
    return {"ok": rc == 0, "text": (out or err)[:4000]}


def quorum_status():
    # Try controller endpoint first, then bootstrap fallback.
    for args in (
        [_bin("kafka-metadata-quorum.sh"), "--bootstrap-controller",
         f"127.0.0.1:{CFG['controller_port']}", "describe", "--status"],
        [_bin("kafka-metadata-quorum.sh"), "--bootstrap-server",
         CFG["bootstrap"], "describe", "--status"],
    ):
        rc, out, err = run(args)
        if rc == 0 and out:
            return {"ok": True, "text": out}
    return {"ok": False, "text": (out or err)[:4000]}


def quorum_replication():
    rc, out, err = run([_bin("kafka-metadata-quorum.sh"),
                        "--bootstrap-server", CFG["bootstrap"],
                        "describe", "--replication"])
    return {"ok": rc == 0, "text": (out or err)[:6000]}


def cluster_brokers():
    rc, out, err = run([_bin("kafka-cluster.sh"), "cluster-id",
                        "--bootstrap-server", CFG["bootstrap"]])
    cid = ""
    if rc == 0 and "=" in out:
        cid = out.split("=", 1)[1].strip()
    rc2, out2, err2 = run([_bin("kafka-broker-api-versions.sh"),
                           "--bootstrap-server", CFG["bootstrap"]])
    brokers = []
    if rc2 == 0:
        for line in out2.splitlines():
            line = line.strip()
            # lines start like "10.0.0.1:9092 (id: 1 rack: null)"
            if " (id:" in line and ":" in line.split(" ", 1)[0]:
                brokers.append(line.split(" (", 1)[0])
    return {"cluster_id": cid, "brokers": sorted(set(brokers))}


def topics():
    rc, out, err = run([_bin("kafka-topics.sh"),
                        "--bootstrap-server", CFG["bootstrap"], "--list"])
    if rc != 0:
        return {"ok": False, "list": [], "error": (err or out)[:2000]}
    lst = [t for t in out.splitlines() if t.strip()]
    return {"ok": True, "list": lst}


def under_replicated():
    rc, out, err = run([_bin("kafka-topics.sh"),
                        "--bootstrap-server", CFG["bootstrap"],
                        "--describe", "--under-replicated-partitions"])
    return {"ok": rc == 0, "text": (out or err)[:4000]}


def gather():
    data = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()),
        "hostname": socket.gethostname(),
        "cluster_name": CFG["cluster_name"],
        "expected_cluster_id": CFG["cluster_id"],
        "bootstrap": CFG["bootstrap"],
    }
    data["broker_api"] = broker_api()
    data["quorum_status"] = quorum_status()
    data["quorum_replication"] = quorum_replication()
    data["cluster"] = cluster_brokers()
    data["topics"] = topics()
    data["under_replicated"] = under_replicated()

    healthy = (
        data["broker_api"]["ok"]
        and data["quorum_status"]["ok"]
        and data["topics"]["ok"]
    )
    data["healthy"] = healthy
    return data


HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>Kafka Health - {host}</title>
<meta http-equiv="refresh" content="5">
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0b0f14;color:#e6edf3;margin:0;padding:24px}}
 h1{{margin:0 0 4px 0}} h2{{margin:24px 0 8px;color:#7ee787;font-size:16px}}
 .ok{{color:#3fb950}} .bad{{color:#f85149}}
 .card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px 18px;margin:10px 0}}
 pre{{background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:10px;overflow:auto;white-space:pre-wrap;font-size:12px;max-height:340px}}
 .kv{{display:grid;grid-template-columns:180px 1fr;gap:4px 16px;font-size:14px}}
 .badge{{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;font-weight:600}}
 .b-ok{{background:#0f3320;color:#3fb950}} .b-bad{{background:#3a0f10;color:#f85149}}
 code{{color:#79c0ff}}
</style></head><body>
<h1>Kafka Health <span class="badge {bcls}">{bstat}</span></h1>
<div style="color:#8b949e">{host} - refreshed {ts}</div>

<div class="card">
 <h2>Cluster</h2>
 <div class="kv">
  <div>Cluster name</div><div><code>{cname}</code></div>
  <div>Cluster ID (live)</div><div><code>{live_cid}</code></div>
  <div>Bootstrap</div><div><code>{boot}</code></div>
  <div>Brokers</div><div>{brokers_html}</div>
 </div>
</div>

<div class="card"><h2>KRaft Quorum Status <span class="badge {qcls}">{qstat}</span></h2><pre>{qtxt}</pre></div>
<div class="card"><h2>KRaft Quorum Replication</h2><pre>{qrepl}</pre></div>
<div class="card"><h2>Broker API Versions <span class="badge {acls}">{astat}</span></h2><pre>{atxt}</pre></div>
<div class="card"><h2>Topics ({tcount})</h2><pre>{tlist}</pre></div>
<div class="card"><h2>Under-replicated partitions</h2><pre>{urp}</pre></div>

<div style="color:#8b949e;font-size:12px;margin-top:20px">
 JSON: <a style="color:#79c0ff" href="/health.json">/health.json</a> - Liveness: <a style="color:#79c0ff" href="/live">/live</a>
</div>
</body></html>"""


def render_html(d):
    def esc(x):
        return html.escape(str(x)) if x is not None else ""

    def badge(ok):
        return ("b-ok", "OK") if ok else ("b-bad", "DEGRADED")

    bcls, bstat = badge(d["healthy"])
    qcls, qstat = badge(d["quorum_status"]["ok"])
    acls, astat = badge(d["broker_api"]["ok"])
    brokers = d["cluster"].get("brokers") or []
    brokers_html = ", ".join(f"<code>{esc(b)}</code>" for b in brokers) or "-"
    tlist = "\n".join(d["topics"].get("list") or []) or "(none)"
    return HTML.format(
        host=esc(d["hostname"]),
        ts=esc(d["generated_at"]),
        cname=esc(d["cluster_name"]),
        live_cid=esc(d["cluster"].get("cluster_id") or "-"),
        boot=esc(d["bootstrap"]),
        brokers_html=brokers_html,
        bcls=bcls, bstat=bstat,
        qcls=qcls, qstat=qstat,
        qtxt=esc(d["quorum_status"]["text"] or "-"),
        qrepl=esc(d["quorum_replication"]["text"] or "-"),
        acls=acls, astat=astat,
        atxt=esc(d["broker_api"]["text"] or "-"),
        tcount=len(d["topics"].get("list") or []),
        tlist=esc(tlist),
        urp=esc(d["under_replicated"]["text"] or "(none / not reported)"),
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


TEMPLATE = {
    "id": "kafka-cluster",
    "name": "Apache Kafka (KRaft Cluster)",
    "category": "Data & Streaming",
    "icon": "boxes",
    "description": (
        "Install a production-grade Apache Kafka cluster in KRaft mode "
        "(no ZooKeeper) from the official Apache tarball, running as a "
        "systemd service. Add each broker under 'Broker nodes' — one "
        "host for dev, 3+ for HA. Node IDs, quorum voters and "
        "advertised listeners are computed from the list. No Docker, "
        "no Bitnami."
    ),
    "tags": ["kafka", "kraft", "streaming", "cluster", "systemd", "ha"],
    "variables": [
        # ---------- Cluster identity ----------
        {"name": "cluster_id", "label": "KRaft cluster name",
         "type": "string", "default": "opensible-kafka",
         "help": "Free-form cluster name. Converted to a stable UUID and shared across every broker."},
        {"name": "kafka_version", "label": "Kafka version",
         "type": "string", "default": "3.8.1",
         "help": "Any published Apache Kafka release, e.g. 3.7.2, 3.8.1, 3.9.0."},
        {"name": "kafka_scala_version", "label": "Scala build",
         "type": "string", "default": "2.13",
         "help": "Scala version the Kafka tarball is built against (2.13 recommended)."},
        {"name": "kafka_mirror", "label": "Download mirror",
         "type": "string",
         "default": "https://downloads.apache.org/kafka",
         "help": "Apache mirror root. Full URL: <mirror>/<version>/kafka_<scala>-<version>.tgz"},

        # ---------- Hosts / HA ----------
        {"name": "ssh_user_default", "label": "Default SSH user for nodes",
         "type": "string", "default": "root"},
        {"name": "ssh_port_default", "label": "Default SSH port",
         "type": "number", "default": 22},
        {"name": "brokers", "label": "Broker nodes (controller + broker)",
         "type": "nodes", "required": False,
         "help": "One entry per host. First entry becomes node.id=1. Use 1 host for dev, 3 or 5 for HA. Leave blank to use the generic host picker below.",
         "default": [{"name": "kafka-1", "ip": "", "ssh_user": "", "ssh_port": ""}]},

        # ---------- Listeners ----------
        {"name": "client_port", "label": "Client (PLAINTEXT) port",
         "type": "number", "default": 9092},
        {"name": "controller_port", "label": "Controller quorum port",
         "type": "number", "default": 9093},
        {"name": "advertised_host", "label": "Advertised host (per node)",
         "type": "string",
         "default": "{{ ansible_host | default(inventory_hostname) }}",
         "help": "Jinja expression evaluated per host. Defaults to the SSH/broker address."},

        # ---------- Cluster tuning ----------
        {"name": "replication_factor", "label": "Default replication factor",
         "type": "number", "default": 3,
         "help": "Auto-capped to the number of brokers if the cluster is smaller."},
        {"name": "min_insync_replicas", "label": "min.insync.replicas",
         "type": "number", "default": 2,
         "help": "Auto-capped to replication_factor."},
        {"name": "num_partitions", "label": "Default partitions per topic",
         "type": "number", "default": 6},
        {"name": "auto_create_topics", "label": "Allow auto topic creation",
         "type": "boolean", "default": False},
        {"name": "log_retention_hours", "label": "Log retention (hours)",
         "type": "number", "default": 168},
        {"name": "heap_opts", "label": "KAFKA_HEAP_OPTS",
         "type": "string", "default": "-Xms1G -Xmx1G"},

        # ---------- Layout ----------
        {"name": "kafka_user", "label": "System user",
         "type": "string", "default": "kafka"},
        {"name": "install_dir", "label": "Install directory",
         "type": "string", "default": "/opt/kafka"},
        {"name": "data_dir", "label": "Log/data directory",
         "type": "string", "default": "/var/lib/kafka/data",
         "help": "Persisted on the host so logs and cluster metadata survive restarts."},
        {"name": "log_dir", "label": "Application log directory",
         "type": "string", "default": "/var/log/kafka"},
        {"name": "java_package_debian", "label": "Java package (Debian/Ubuntu)",
         "type": "string", "default": "openjdk-17-jre-headless"},
        {"name": "java_package_rhel", "label": "Java package (RHEL family)",
         "type": "string", "default": "java-17-openjdk-headless"},
        {"name": "open_firewall", "label": "Open ports in UFW/firewalld",
         "type": "boolean", "default": True},
        {"name": "become", "label": "Run as sudo (become)",
         "type": "boolean", "default": True},

        # ---------- Health HTTP endpoint ----------
        {"name": "health_http_enabled", "label": "Enable HTTP health dashboard",
         "type": "boolean", "default": True,
         "help": "Install a small HTTP service on each broker exposing / (HTML dashboard) and /health.json with full KRaft/quorum/topic/broker status."},
        {"name": "health_http_port", "label": "Health HTTP port",
         "type": "number", "default": 8080,
         "help": "Port for the health dashboard. Visit http://<broker-ip>:<port>/ from your browser."},
        VAULT_FILES_VARIABLE,
    ],
}


def suggested_filename(values: Dict[str, Any]) -> str:
    stem = slugify(values.get("cluster_id"), "kafka")
    return f"{stem}-kafka-kraft.yml"


def _norm_nodes(raw: Any, default_user: str, default_port: Any) -> List[Dict[str, Any]]:
    """Normalise the UI 'nodes' variable into a list of broker dicts."""
    out: List[Dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for i, n in enumerate(raw):
        if not isinstance(n, dict):
            continue
        ip = str(n.get("ip") or "").strip()
        if not ip:
            continue
        name = str(n.get("name") or f"kafka-{i+1}").strip() or f"kafka-{i+1}"
        user = str(n.get("ssh_user") or default_user or "root").strip() or "root"
        try:
            port = int(n.get("ssh_port") or default_port or 22)
        except Exception:
            port = 22
        broker_name = slugify(name, f"kafka-{i+1}") or f"kafka-{i+1}"
        out.append({
            "name": name,
            "broker_name": broker_name,
            "ip": ip,
            "ssh_user": user,
            "ssh_port": port,
            "node_id": i + 1,
        })
    return out


def _health_tasks(cluster_id: str, install_dir: str, client_port: int,
                  controller_port: int, http_port: int) -> List[str]:
    """Ansible tasks installing the per-broker HTTP health dashboard."""
    lines: List[str] = [
        "    - name: Ensure python3 is present for Kafka health service",
        "      ansible.builtin.package:",
        "        name: python3",
        "        state: present",
        "      failed_when: false",
        "    - name: Install kafka-health dashboard script",
        "      ansible.builtin.copy:",
        "        dest: /usr/local/bin/opensible-kafka-health.py",
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
        "    - name: Install kafka-health systemd unit",
        "      ansible.builtin.copy:",
        "        dest: /etc/systemd/system/opensible-kafka-health.service",
        "        owner: root",
        "        group: root",
        "        mode: '0644'",
        "        content: |",
        "          [Unit]",
        f"          Description=OpenSible Kafka KRaft HTTP health dashboard ({cluster_id})",
        "          After=network-online.target kafka.service",
        "          Wants=network-online.target kafka.service",
        "          [Service]",
        "          Type=simple",
        f"          Environment=HTTP_PORT={http_port}",
        f"          Environment=KAFKA_INSTALL_DIR={install_dir}",
        f"          Environment=KAFKA_CLIENT_PORT={client_port}",
        f"          Environment=KAFKA_CONTROLLER_PORT={controller_port}",
        f"          Environment=CLUSTER_NAME={cluster_id}",
        "          Environment=CLUSTER_ID={{ kafka_kraft_cluster_id }}",
        f"          Environment=BOOTSTRAP=127.0.0.1:{client_port}",
        "          ExecStart=/usr/bin/env python3 /usr/local/bin/opensible-kafka-health.py",
        "          Restart=on-failure",
        "          RestartSec=3",
        "          User=root",
        "          [Install]",
        "          WantedBy=multi-user.target",
        "    - name: Enable and start kafka-health service",
        "      ansible.builtin.systemd:",
        "        name: opensible-kafka-health.service",
        "        enabled: true",
        "        state: restarted",
        "        daemon_reload: true",
        f"    - name: Wait for Kafka health HTTP port {http_port}",
        "      ansible.builtin.wait_for:",
        "        host: 127.0.0.1",
        f"        port: {http_port}",
        "        timeout: 30",
        "    - name: Report Kafka health dashboard URL",
        "      ansible.builtin.debug:",
        f"        msg: \"Kafka health dashboard: http://{{{{ ansible_host | default(inventory_hostname) }}}}:{http_port}/  (JSON: /health.json)\"",
    ]
    return lines


def render(values: Dict[str, Any], targets: Dict[str, Any]) -> str:
    become = "true" if values.get("become", True) else "false"

    kafka_version = str(values.get("kafka_version") or "3.8.1").strip()
    scala_version = str(values.get("kafka_scala_version") or "2.13").strip()
    mirror_raw = str(values.get("kafka_mirror") or "https://downloads.apache.org/kafka").strip().rstrip("/")
    # Defensive: if a user pasted a full tarball URL as the mirror, strip it
    # back down to the mirror root (…/kafka). This prevents doubled paths like
    # https://downloads.apache.org/kafka/<v>/kafka_2.13-<v>.tgz/<v>/kafka_2.13-<v>.tgz
    _low = mirror_raw.lower()
    if _low.endswith(".tgz") or _low.endswith(".tar.gz"):
        mirror_raw = mirror_raw.rsplit("/", 1)[0]
    # Strip a trailing /<version> segment if present
    _tail = mirror_raw.rsplit("/", 1)[-1]
    if _tail and _tail[0].isdigit() and "." in _tail:
        mirror_raw = mirror_raw.rsplit("/", 1)[0]
    mirror = mirror_raw or "https://downloads.apache.org/kafka"
    cluster_id = values.get("cluster_id") or "opensible-kafka"
    client_port = int(values.get("client_port") or 9092)
    controller_port = int(values.get("controller_port") or 9093)
    advertised_host = values.get("advertised_host") or "{{ ansible_host | default(inventory_hostname) }}"
    replication_factor = int(values.get("replication_factor") or 3)
    min_isr = int(values.get("min_insync_replicas") or 2)
    num_partitions = int(values.get("num_partitions") or 6)
    auto_create = "true" if values.get("auto_create_topics") else "false"
    retention_hours = int(values.get("log_retention_hours") or 168)
    heap_opts = values.get("heap_opts") or "-Xms1G -Xmx1G"
    kafka_user = values.get("kafka_user") or "kafka"
    install_dir = values.get("install_dir") or "/opt/kafka"
    data_dir = values.get("data_dir") or "/var/lib/kafka/data"
    log_dir = values.get("log_dir") or "/var/log/kafka"
    java_deb = values.get("java_package_debian") or "openjdk-17-jre-headless"
    java_rhel = values.get("java_package_rhel") or "java-17-openjdk-headless"
    open_firewall = bool(values.get("open_firewall", True))
    health_http_enabled = bool(values.get("health_http_enabled", True))
    health_http_port = int(values.get("health_http_port") or 8080)

    brokers = _norm_nodes(
        values.get("brokers"),
        values.get("ssh_user_default") or "root",
        values.get("ssh_port_default") or 22,
    )

    tarball = f"kafka_{scala_version}-{kafka_version}.tgz"
    download_url = f"{mirror}/{kafka_version}/{tarball}"
    archive_url = f"https://archive.apache.org/dist/kafka/{kafka_version}/{tarball}"
    versioned_dir = f"{install_dir}-{kafka_version}"
    cluster_group = slugify(cluster_id, "kafka") + "_brokers"

    parts: List[str] = ["---"]
    parts.append(f"# Rendered from template: {TEMPLATE['name']} (systemd, no Docker)")
    parts.append(f"# Cluster: {cluster_id} | brokers: {len(brokers) if brokers else 'from targets'} | version: {kafka_version}")
    parts.append("")

    # ------------------------------------------------------------------ #
    # PLAY 0 — Build dynamic inventory from the broker list (if provided)
    # ------------------------------------------------------------------ #
    if brokers:
        parts += [
            "- name: Register Kafka brokers into a dynamic inventory group",
            "  hosts: localhost",
            "  gather_facts: false",
            "  connection: local",
            "  tasks:",
            "    - name: add_host each broker",
            "      ansible.builtin.add_host:",
            "        name: \"{{ item.ip }}\"",
            f"        groups: {cluster_group}",
            "        ansible_host: \"{{ item.ip }}\"",
            "        ansible_user: \"{{ item.ssh_user }}\"",
            "        ansible_port: \"{{ item.ssh_port }}\"",
            "        broker_name: \"{{ item.broker_name }}\"",
            "        kafka_node_id: \"{{ item.node_id }}\"",
            "      loop:",
        ]
        for b in brokers:
            parts.append(
                "        - { "
                f"name: {yaml_str(b['name'])}, "
                f"broker_name: {yaml_str(b['broker_name'])}, "
                f"ip: {yaml_str(b['ip'])}, "
                f"ssh_user: {yaml_str(b['ssh_user'])}, "
                f"ssh_port: {b['ssh_port']}, "
                f"node_id: {b['node_id']}"
                " }"
            )
        parts.append("")
        play_hosts = cluster_group
    else:
        # Fallback: use generic targets picker
        play_hosts = render_hosts(targets)

    # ------------------------------------------------------------------ #
    # PLAY 1 — Deploy Kafka on every broker
    # ------------------------------------------------------------------ #
    parts += [
        "- name: Deploy Apache Kafka (KRaft) cluster on systemd",
        f"  hosts: {play_hosts}",
        f"  become: {become}",
        "  gather_facts: true",
        "  any_errors_fatal: true",
        *vars_files_lines(parse_vault_files(values.get("vault_files"))),
        "  vars:",
        f"    kafka_version: {yaml_str(kafka_version)}",
        f"    kafka_scala_version: {yaml_str(scala_version)}",
        f"    kafka_tarball: {yaml_str(tarball)}",
        f"    kafka_download_url: {yaml_str(download_url)}",
        f"    kafka_archive_url: {yaml_str(archive_url)}",
        f"    kafka_user: {yaml_str(kafka_user)}",
        f"    kafka_install_dir: {yaml_str(install_dir)}",
        f"    kafka_versioned_dir: {yaml_str(versioned_dir)}",
        f"    kafka_data_dir: {yaml_str(data_dir)}",
        f"    kafka_log_dir: {yaml_str(log_dir)}",
        f"    kafka_cluster_name: {yaml_str(cluster_id)}",
        f"    kafka_client_port: {client_port}",
        f"    kafka_controller_port: {controller_port}",
        f"    kafka_advertised_host: \"{advertised_host}\"",
        f"    kafka_replication_factor: {replication_factor}",
        f"    kafka_min_isr: {min_isr}",
        f"    kafka_num_partitions: {num_partitions}",
        f"    kafka_auto_create_topics: {auto_create}",
        f"    kafka_log_retention_hours: {retention_hours}",
        f"    kafka_heap_opts: {yaml_str(heap_opts)}",
        "    kafka_broker_count: \"{{ ansible_play_hosts | length }}\"",
        "    kafka_effective_rf: \"{{ [kafka_replication_factor | int, kafka_broker_count | int] | min }}\"",
        "    kafka_effective_min_isr: \"{{ [kafka_min_isr | int, kafka_effective_rf | int] | min }}\"",
        "  tasks:",
        # When brokers are provided the node_id comes from add_host; otherwise
        # fall back to the play-hosts index so single-target runs still work.
        "    - name: Resolve kafka_node_id (fallback to play index)",
        "      ansible.builtin.set_fact:",
        "        kafka_node_id: \"{{ kafka_node_id | default(ansible_play_hosts.index(inventory_hostname) + 1) }}\"",
        "    - name: Compute stable KRaft cluster UUID from name",
        "      ansible.builtin.set_fact:",
        "        kafka_kraft_cluster_id: >-",
        "          {{ (kafka_cluster_name | to_uuid | replace('-', ''))[:22] }}",

        # ---------- Low-memory guard (Ubuntu/Debian small VMs get OOM-killed) ----------
        "    - name: Ensure /swapfile exists on low-memory Debian/Ubuntu hosts",
        "      ansible.builtin.shell: |",
        "        set -e",
        "        if [ ! -f /swapfile ]; then",
        "          fallocate -l 1G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=1024",
        "          chmod 600 /swapfile",
        "          mkswap /swapfile",
        "          swapon /swapfile",
        "          grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab",
        "        fi",
        "      args:",
        "        executable: /bin/bash",
        "      when:",
        "        - ansible_os_family == 'Debian'",
        "        - (ansible_memtotal_mb | default(4096)) < 1400",
        "      failed_when: false",

        # ---------- Prerequisites ----------
        "    - name: Install Java and helpers (Debian/Ubuntu)",
        "      ansible.builtin.shell: |",
        "        set -e",
        "        export DEBIAN_FRONTEND=noninteractive",
        "        apt-get update -y",
        f"        apt-get install -y --no-install-recommends {java_deb} curl tar",
        "      args:",
        "        executable: /bin/bash",
        "      when: ansible_os_family == 'Debian'",
        "    - name: Install Java and helpers (RHEL family)",
        "      ansible.builtin.yum:",
        f"        name: [{java_rhel}, curl, tar]",
        "        state: present",
        "      when: ansible_os_family == 'RedHat'",


        # ---------- System user + dirs ----------
        "    - name: Ensure kafka group",
        "      ansible.builtin.group:",
        "        name: \"{{ kafka_user }}\"",
        "        system: true",
        "    - name: Ensure kafka system user",
        "      ansible.builtin.user:",
        "        name: \"{{ kafka_user }}\"",
        "        group: \"{{ kafka_user }}\"",
        "        system: true",
        "        shell: /usr/sbin/nologin",
        "        home: \"{{ kafka_install_dir }}\"",
        "        create_home: false",
        "    - name: Create Kafka directories",
        "      ansible.builtin.file:",
        "        path: \"{{ item }}\"",
        "        state: directory",
        "        owner: \"{{ kafka_user }}\"",
        "        group: \"{{ kafka_user }}\"",
        "        mode: '0755'",
        "      loop:",
        "        - \"{{ kafka_data_dir }}\"",
        "        - \"{{ kafka_log_dir }}\"",
        "        - /opt",

        # ---------- Existing KRaft metadata (safe re-runs / reordered broker lists) ----------
        "    - name: Remember configured Kafka node identity",
        "      ansible.builtin.set_fact:",
        "        kafka_configured_node_id: \"{{ kafka_node_id | string }}\"",
        "        kafka_effective_node_id: \"{{ kafka_node_id | string }}\"",
        "    - name: Check existing KRaft metadata",
        "      ansible.builtin.stat:",
        "        path: \"{{ kafka_data_dir }}/meta.properties\"",
        "      register: kafka_meta",
        # Kafka stores both cluster.id and node.id in meta.properties. On a
        # re-run after broker list/order changes, the generated node.id may no
        # longer match the disk metadata and Kafka exits fatally. Preserve the
        # existing IDs so re-runs are idempotent; uninstall/reinstall remains
        # the explicit path to intentionally change node identity.
        "    - name: Read existing KRaft cluster.id and node.id from meta.properties",
        "      ansible.builtin.shell: |",
        "        set -e",
        "        awk -F= '/^(cluster.id|node.id)=/{print $1 \"=\" $2}' {{ kafka_data_dir }}/meta.properties | tr -d '\\r'",
        "      args:",
        "        executable: /bin/bash",
        "      register: kafka_existing_meta",
        "      changed_when: false",
        "      failed_when: false",
        "      when: kafka_meta.stat.exists",
        "    - name: Parse existing KRaft metadata if present",
        "      ansible.builtin.set_fact:",
        "        kafka_existing_cluster_id: \"{{ (kafka_existing_meta.stdout_lines | default([]) | select('match', '^cluster[.]id=') | list | first | default('cluster.id=')) | regex_replace('^cluster[.]id=', '') }}\"",
        "        kafka_existing_node_id: \"{{ (kafka_existing_meta.stdout_lines | default([]) | select('match', '^node[.]id=') | list | first | default('node.id=')) | regex_replace('^node[.]id=', '') }}\"",
        "      when:",
        "        - kafka_meta.stat.exists",
        "        - kafka_existing_meta is defined",
        "        - (kafka_existing_meta.stdout | default('')) | length > 0",
        "    - name: Adopt existing KRaft metadata for safe re-runs",
        "      ansible.builtin.set_fact:",
        "        kafka_kraft_cluster_id: \"{{ kafka_existing_cluster_id | default(kafka_kraft_cluster_id, true) }}\"",
        "        kafka_effective_node_id: \"{{ kafka_existing_node_id | default(kafka_configured_node_id, true) }}\"",
        "      when: kafka_meta.stat.exists",
        "    - name: Show Kafka node identity decision",
        "      ansible.builtin.debug:",
        "        msg: \"Kafka node identity on {{ inventory_hostname }}: configured={{ kafka_configured_node_id }}, existing={{ kafka_existing_node_id | default('none', true) }}, effective={{ kafka_effective_node_id }}\"",
        "    - name: Validate effective Kafka node IDs are unique",
        "      run_once: true",
        "      ansible.builtin.assert:",
        "        that:",
        "          - (ansible_play_hosts | map('extract', hostvars, 'kafka_effective_node_id') | map('string') | list | unique | length) == (ansible_play_hosts | length)",
        "        fail_msg: >-",
        "          Duplicate Kafka node IDs were detected after reading existing meta.properties files. This usually means one broker data directory was copied to another host. Run the Kafka uninstall blueprint on the affected hosts, or change kafka_data_dir to an empty directory, then deploy again.",
        "    - name: Compute KRaft controller quorum voters",
        "      ansible.builtin.set_fact:",
        "        kafka_controller_quorum_voters: >-",
        "          {%- set voters = [] -%}",
        "          {%- for h in ansible_play_hosts -%}",
        "          {%- set hv = hostvars[h] -%}",
        "          {%- set addr = hv.ansible_host | default(h) -%}",
        "          {%- set nid = hv.kafka_effective_node_id | default(hv.kafka_node_id | default(loop.index)) -%}",
        "          {%- set _ = voters.append((nid | string) + '@' + addr + ':' + (kafka_controller_port | string)) -%}",
        "          {%- endfor -%}",
        "          {{ voters | join(',') }}",

        # ---------- Download + extract ----------
        "    - name: Check if Kafka is already installed at this version",
        "      ansible.builtin.stat:",
        "        path: \"{{ kafka_versioned_dir }}/bin/kafka-server-start.sh\"",
        "      register: kafka_installed",
        "    - name: Download Kafka tarball (mirror, fallback to archive)",
        "      when: not kafka_installed.stat.exists",
        "      block:",
        "        - name: Download Kafka tarball from mirror",
        "          ansible.builtin.get_url:",
        "            url: \"{{ kafka_download_url }}\"",
        "            dest: \"/tmp/{{ kafka_tarball }}\"",
        "            mode: '0644'",
        "            timeout: 120",
        "      rescue:",
        "        - name: Mirror missing this version; falling back to archive.apache.org",
        "          ansible.builtin.debug:",
        "            msg: \"{{ kafka_download_url }} unavailable, trying {{ kafka_archive_url }}\"",
        "        - name: Download Kafka tarball from archive.apache.org",
        "          ansible.builtin.get_url:",
        "            url: \"{{ kafka_archive_url }}\"",
        "            dest: \"/tmp/{{ kafka_tarball }}\"",
        "            mode: '0644'",
        "            timeout: 180",
        "    - name: Extract Kafka tarball",
        "      ansible.builtin.unarchive:",
        "        src: \"/tmp/{{ kafka_tarball }}\"",
        "        dest: /opt",
        "        remote_src: true",
        "        owner: \"{{ kafka_user }}\"",
        "        group: \"{{ kafka_user }}\"",
        "        creates: \"/opt/kafka_{{ kafka_scala_version }}-{{ kafka_version }}/bin/kafka-server-start.sh\"",
        "      when: not kafka_installed.stat.exists",
        "    - name: Symlink versioned dir to canonical path",
        "      ansible.builtin.file:",
        "        src: \"/opt/kafka_{{ kafka_scala_version }}-{{ kafka_version }}\"",
        "        dest: \"{{ kafka_versioned_dir }}\"",
        "        state: link",
        "        force: true",
        "    - name: Symlink current install to {{ kafka_install_dir }}",
        "      ansible.builtin.file:",
        "        src: \"{{ kafka_versioned_dir }}\"",
        "        dest: \"{{ kafka_install_dir }}\"",
        "        state: link",
        "        force: true",

        "    - name: Ensure Kafka KRaft config directory exists",
        "      ansible.builtin.file:",
        "        path: \"{{ kafka_install_dir }}/config/kraft\"",
        "        state: directory",
        "        owner: \"{{ kafka_user }}\"",
        "        group: \"{{ kafka_user }}\"",
        "        mode: '0755'",

        # ---------- Config ----------
        "    - name: Write server.properties (KRaft combined mode)",
        "      ansible.builtin.copy:",
        "        dest: \"{{ kafka_install_dir }}/config/kraft/server.properties\"",
        "        owner: \"{{ kafka_user }}\"",
        "        group: \"{{ kafka_user }}\"",
        "        mode: '0644'",
        "        content: |",
        "          # Managed by OpenSible - do not edit by hand",
        "          process.roles=broker,controller",
        "          node.id={{ kafka_effective_node_id }}",
        "          controller.quorum.voters={{ kafka_controller_quorum_voters }}",
        f"          listeners=PLAINTEXT://:{client_port},CONTROLLER://:{controller_port}",
        "          inter.broker.listener.name=PLAINTEXT",
        "          controller.listener.names=CONTROLLER",
        "          listener.security.protocol.map=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT",
        f"          advertised.listeners=PLAINTEXT://{{{{ kafka_advertised_host }}}}:{client_port}",
        "          log.dirs={{ kafka_data_dir }}",
        "          num.partitions={{ kafka_num_partitions }}",
        "          auto.create.topics.enable={{ kafka_auto_create_topics }}",
        "          default.replication.factor={{ kafka_effective_rf }}",
        "          offsets.topic.replication.factor={{ kafka_effective_rf }}",
        "          transaction.state.log.replication.factor={{ kafka_effective_rf }}",
        "          transaction.state.log.min.isr={{ kafka_effective_min_isr }}",
        "          min.insync.replicas={{ kafka_effective_min_isr }}",
        "          log.retention.hours={{ kafka_log_retention_hours }}",
        "          num.network.threads=3",
        "          num.io.threads=8",
        "          socket.send.buffer.bytes=102400",
        "          socket.receive.buffer.bytes=102400",
        "          socket.request.max.bytes=104857600",
        "          group.initial.rebalance.delay.ms=0",
        "      notify: Restart kafka",

        # ---------- Format storage (idempotent, cluster/node-id safe) ----------
        "    - name: Format KRaft storage (first time only)",
        "      ansible.builtin.command: >-",
        "        {{ kafka_install_dir }}/bin/kafka-storage.sh format",
        "        --ignore-formatted",
        "        --cluster-id {{ kafka_kraft_cluster_id }}",
        "        --config {{ kafka_install_dir }}/config/kraft/server.properties",
        "      become_user: \"{{ kafka_user }}\"",
        "      when: not kafka_meta.stat.exists",
        "      changed_when: true",


        # ---------- systemd unit ----------
        "    - name: Install kafka systemd unit",
        "      ansible.builtin.copy:",
        "        dest: /etc/systemd/system/kafka.service",
        "        owner: root",
        "        group: root",
        "        mode: '0644'",
        "        content: |",
        "          [Unit]",
        "          Description=Apache Kafka (KRaft)",
        "          Documentation=https://kafka.apache.org/documentation/",
        "          Requires=network-online.target",
        "          After=network-online.target",
        "          [Service]",
        "          Type=simple",
        "          User={{ kafka_user }}",
        "          Group={{ kafka_user }}",
        f"          Environment=KAFKA_HEAP_OPTS={heap_opts}",
        "          Environment=LOG_DIR={{ kafka_log_dir }}",
        "          ExecStart={{ kafka_install_dir }}/bin/kafka-server-start.sh {{ kafka_install_dir }}/config/kraft/server.properties",
        "          ExecStop={{ kafka_install_dir }}/bin/kafka-server-stop.sh",
        "          Restart=on-failure",
        "          RestartSec=5",
        "          LimitNOFILE=100000",
        "          TimeoutStopSec=180",
        "          [Install]",
        "          WantedBy=multi-user.target",
        "      notify: Restart kafka",
        "    - name: Reload systemd",
        "      ansible.builtin.systemd:",
        "        daemon_reload: true",
        # Flush pending handlers (Restart kafka) BEFORE we wait_for the port,
        # so config changes actually take effect before the smoke test.
        "    - name: Flush handlers (apply config restarts before health checks)",
        "      ansible.builtin.meta: flush_handlers",
    ]

    if open_firewall:
        parts += [
            "    - name: Open Kafka ports (ufw, if active)",
            "      ansible.builtin.shell: |",
            "        set -e",
            "        command -v ufw >/dev/null 2>&1 || exit 0",
            "        ufw status | grep -q 'Status: active' || exit 0",
            f"        ufw allow {client_port}/tcp || true",
            f"        ufw allow {controller_port}/tcp || true",
            *([f"        ufw allow {health_http_port}/tcp || true"] if health_http_enabled else []),
            "      changed_when: false",
            "      failed_when: false",
            "    - name: Open Kafka ports (firewalld, if active)",
            "      ansible.builtin.shell: |",
            "        set -e",
            "        command -v firewall-cmd >/dev/null 2>&1 || exit 0",
            "        firewall-cmd --state >/dev/null 2>&1 || exit 0",
            f"        firewall-cmd --permanent --add-port={client_port}/tcp || true",
            f"        firewall-cmd --permanent --add-port={controller_port}/tcp || true",
            *([f"        firewall-cmd --permanent --add-port={health_http_port}/tcp || true"] if health_http_enabled else []),
            "        firewall-cmd --reload || true",
            "      changed_when: false",
            "      failed_when: false",
        ]

    parts += [
        "    - name: Enable and start kafka",
        "      ansible.builtin.systemd:",
        "        name: kafka",
        "        enabled: true",
        "        state: started",
        "    - name: Wait for Kafka to accept connections (with diagnostics on failure)",
        "      block:",
        "        - name: wait_for kafka client port",
        "          ansible.builtin.wait_for:",
        "            host: 127.0.0.1",
        f"            port: {client_port}",
        "            timeout: 180",
        "      rescue:",
        "        - name: Dump kafka systemd status",
        "          ansible.builtin.shell: |",
        "            set +e",
        "            echo '===== systemctl status kafka ====='",
        "            systemctl status kafka --no-pager -l | tail -n 60",
        "            echo '===== journalctl -u kafka (last 200 lines) ====='",
        "            journalctl -u kafka --no-pager -n 200 || true",
        "            echo '===== server.log tail ====='",
        "            tail -n 120 {{ kafka_log_dir }}/server.log 2>/dev/null || true",
        "          args:",
        "            executable: /bin/bash",
        "          register: kafka_diag",
        "          changed_when: false",
        "          failed_when: false",
        "        - name: Show kafka diagnostics",
        "          ansible.builtin.debug:",
        "            var: kafka_diag.stdout_lines",
        "        - name: Fail with clear message",
        "          ansible.builtin.fail:",
        "            msg: \"Kafka did not open port {{ kafka_client_port }} within 180s. See diagnostics above.\"",

        "    - name: Smoke test - list API versions",
        "      ansible.builtin.command: >-",
        "        {{ kafka_install_dir }}/bin/kafka-broker-api-versions.sh",
        f"        --bootstrap-server 127.0.0.1:{client_port}",
        "      changed_when: false",
        "      register: kafka_smoke",
        "      retries: 6",
        "      delay: 5",
        "      until: kafka_smoke.rc == 0",
        # ---------- KRaft quorum health (HA verification) ----------
        "    - name: Verify KRaft controller quorum status (via controller)",
        "      run_once: true",
        "      ansible.builtin.shell: |",
        "        set -e",
        f"        if {{{{ kafka_install_dir }}}}/bin/kafka-metadata-quorum.sh --bootstrap-controller 127.0.0.1:{controller_port} describe --status 2>/tmp/kraft_q.err; then",
        "          exit 0",
        "        fi",
        f"        {{{{ kafka_install_dir }}}}/bin/kafka-metadata-quorum.sh --bootstrap-server 127.0.0.1:{client_port} describe --status 2>>/tmp/kraft_q.err",
        "      args:",
        "        executable: /bin/bash",
        "      changed_when: false",
        "      register: kraft_quorum",
        "      retries: 6",
        "      delay: 5",
        "      until: kraft_quorum.rc == 0",
        "      failed_when: false",
        # ---------- Cluster-info bundle (OpenBao-style) ----------
        "    - name: Ensure {{ kafka_install_dir }}/cluster-info exists",
        "      ansible.builtin.file:",
        "        path: \"{{ kafka_install_dir }}/cluster-info\"",
        "        state: directory",
        "        owner: \"{{ kafka_user }}\"",
        "        group: \"{{ kafka_user }}\"",
        "        mode: '0755'",
        "    - name: Write Kafka cluster-info bundle on every broker",
        "      ansible.builtin.copy:",
        "        dest: \"{{ kafka_install_dir }}/cluster-info/cluster-info.txt\"",
        "        owner: \"{{ kafka_user }}\"",
        "        group: \"{{ kafka_user }}\"",
        "        mode: '0644'",
        "        content: |",
        "          # Managed by OpenSible - Kafka KRaft cluster info",
        "          cluster_name       : {{ kafka_cluster_name }}",
        "          cluster_uuid       : {{ kafka_kraft_cluster_id }}",
        "          version            : {{ kafka_version }} (Scala {{ kafka_scala_version }})",
        "          node_id            : {{ kafka_effective_node_id }}",
        "          node_role          : broker,controller (KRaft combined)",
        "          brokers            : {{ ansible_play_hosts | map('extract', hostvars) | map(attribute='ansible_host', default=inventory_hostname) | list | join(', ') }}",
        "          bootstrap_servers  : {% for h in ansible_play_hosts %}{{ hostvars[h].ansible_host | default(h) }}:{{ kafka_client_port }}{% if not loop.last %},{% endif %}{% endfor %}",
        "          quorum_voters      : {{ kafka_controller_quorum_voters }}",
        "          replication_factor : {{ kafka_effective_rf }}",
        "          min_insync_replicas: {{ kafka_effective_min_isr }}",
        "          data_dir           : {{ kafka_data_dir }}",
        "          install_dir        : {{ kafka_install_dir }}",
        "          service            : systemctl status kafka",
        "          logs               : journalctl -u kafka -f",
        "          quorum_check       : {{ kafka_install_dir }}/bin/kafka-metadata-quorum.sh --bootstrap-server 127.0.0.1:{{ kafka_client_port }} describe --status",
        "          topic_list         : {{ kafka_install_dir }}/bin/kafka-topics.sh --bootstrap-server 127.0.0.1:{{ kafka_client_port }} --list",
        "    - name: Cluster summary",
        "      run_once: true",
        "      ansible.builtin.debug:",
        "        msg:",
        "          - \"Kafka {{ kafka_version }} (KRaft) is up\"",
        "          - \"Cluster UUID: {{ kafka_kraft_cluster_id }}\"",
        "          - \"Brokers: {{ kafka_broker_count }} | RF: {{ kafka_effective_rf }} | min.ISR: {{ kafka_effective_min_isr }}\"",
        "          - \"Quorum voters: {{ kafka_controller_quorum_voters }}\"",
        f"          - \"Bootstrap servers (PLAINTEXT :{client_port}):\"",
        "          - \"{{ ansible_play_hosts | map('extract', hostvars) | map(attribute='ansible_host', default='') | reject('equalto','') | list }}\"",
        "          - \"Cluster-info bundle: {{ kafka_install_dir }}/cluster-info/cluster-info.txt (every node)\"",
        "          - \"Quorum status:\"",
        "          - \"{{ kraft_quorum.stdout_lines | default([]) }}\"",
        *(_health_tasks(cluster_id, install_dir, client_port, controller_port, health_http_port) if health_http_enabled else []),
        "  handlers:",
        "    - name: Restart kafka",
        "      ansible.builtin.systemd:",
        "        name: kafka",
        "        state: restarted",
        "        daemon_reload: true",
        "",
    ]
    return "\n".join(parts)
