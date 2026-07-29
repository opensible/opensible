"""Shared HTTP health dashboard for Vault-compatible APIs (HashiCorp Vault, OpenBao).

Both Vault and OpenBao expose the same HTTP endpoints:
  * /v1/sys/health
  * /v1/sys/seal-status
  * /v1/sys/leader

This module renders a small self-contained Python HTTP dashboard and returns
Ansible task lines that install it as a systemd unit on every node. Each
consumer (vault_cluster.py / openbao.py) chooses its own script path,
service name, HTTP port and display label.

Endpoints exposed:
  /            HTML dashboard (auto-refresh 5s)
  /health.json JSON payload (200 healthy = initialized+unsealed, 503 otherwise)
  /live        liveness (always 200)

Marker: 2026-07-vault-health-v1
"""
from __future__ import annotations

from typing import List


_HEALTH_SCRIPT = r'''#!/usr/bin/env python3
"""OpenSible Vault/OpenBao HTTP health dashboard."""
import json, os, html, socket, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib import request as _rq, error as _er

CFG = {
    "cluster_name": os.environ.get("CLUSTER_NAME", ""),
    "product":      os.environ.get("PRODUCT", "Vault"),
    "api_host":     os.environ.get("API_HOST", "127.0.0.1"),
    "api_port":     int(os.environ.get("API_PORT", "8200")),
    "scheme":       os.environ.get("SCHEME", "http"),
    "http_port":    int(os.environ.get("HTTP_PORT", "8280")),
}


def _get(url, timeout=4):
    try:
        req = _rq.Request(url, headers={"Accept": "application/json"})
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


def _json(url):
    code, body = _get(url)
    data = {}
    try:
        data = json.loads(body) if body and body.startswith(("{", "[")) else {}
    except Exception:
        data = {}
    return code, data


def collect():
    base = f"{CFG['scheme']}://{CFG['api_host']}:{CFG['api_port']}"
    hc, hj = _json(base + "/v1/sys/health")
    sc, sj = _json(base + "/v1/sys/seal-status")
    lc, lj = _json(base + "/v1/sys/leader")
    initialized = bool((sj or {}).get("initialized") or (hj or {}).get("initialized"))
    sealed = bool((sj or {}).get("sealed", True))
    standby = bool((hj or {}).get("standby", False))
    api_up = hc in (200, 429, 472, 473, 501, 503)
    if not api_up:
        status = "red"
    elif not initialized:
        status = "yellow"
    elif sealed:
        status = "red"
    elif standby:
        status = "yellow"
    else:
        status = "green"
    healthy = (status == "green")
    return {
        "hostname": socket.gethostname(),
        "ts": int(time.time()),
        "cluster_name": CFG["cluster_name"],
        "product": CFG["product"],
        "api": {"base": base, "code": hc, "status": status,
                "health": hj if isinstance(hj, dict) else {},
                "seal": sj if isinstance(sj, dict) else {},
                "leader": lj if isinstance(lj, dict) else {}},
        "initialized": initialized,
        "sealed": sealed,
        "standby": standby,
        "healthy": healthy,
    }


def esc(v):
    return html.escape("" if v is None else str(v), quote=True)


def badge(s):
    s = (s or "").lower()
    color = {"green": "#238636", "yellow": "#9e6a03",
             "red": "#da3633"}.get(s, "#6e7681")
    return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:10px;font-weight:600">{esc(s or "unknown")}</span>'


def render_html(d):
    seal = d["api"]["seal"] or {}
    health = d["api"]["health"] or {}
    leader = d["api"]["leader"] or {}
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="5">
<title>{esc(d['product'])} — {esc(d['cluster_name'])} @ {esc(d['hostname'])}</title>
<style>
body{{background:#0d1117;color:#c9d1d9;font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:24px}}
h1{{margin:0 0 4px 0;font-size:18px}} h2{{margin:24px 0 8px 0;font-size:14px;color:#8b949e;text-transform:uppercase;letter-spacing:.05em}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin-bottom:16px}}
.k{{color:#8b949e}} .v{{color:#c9d1d9;font-weight:600}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px 24px}}
pre{{background:#0b0f14;border:1px solid #21262d;border-radius:6px;padding:10px;overflow:auto;font-size:12px;color:#c9d1d9}}
</style></head><body>
<div class="card">
  <h1>{esc(d['product'])} cluster <span class="v">{esc(d['cluster_name'])}</span> — node <span class="v">{esc(d['hostname'])}</span></h1>
  <div style="margin-top:6px">
    Overall: {badge(d['api']['status'])} &middot;
    Initialized: {badge('green' if d['initialized'] else 'red')} &middot;
    Sealed: {badge('red' if d['sealed'] else 'green')} &middot;
    Standby: {badge('yellow' if d['standby'] else 'green')}
  </div>
</div>
<div class="card"><h2>API</h2>
  <div class="grid">
    <div><span class="k">endpoint</span><br><span class="v">{esc(d['api']['base'])}</span></div>
    <div><span class="k">/v1/sys/health code</span><br><span class="v">{esc(d['api']['code'])}</span></div>
    <div><span class="k">version</span><br><span class="v">{esc(seal.get('version') or health.get('version'))}</span></div>
    <div><span class="k">cluster_name</span><br><span class="v">{esc(seal.get('cluster_name') or health.get('cluster_name'))}</span></div>
    <div><span class="k">cluster_id</span><br><span class="v">{esc(seal.get('cluster_id') or health.get('cluster_id'))}</span></div>
    <div><span class="k">storage_type</span><br><span class="v">{esc(seal.get('storage_type') or health.get('storage_type'))}</span></div>
  </div>
</div>
<div class="card"><h2>Seal</h2>
  <div class="grid">
    <div><span class="k">type</span><br><span class="v">{esc(seal.get('type'))}</span></div>
    <div><span class="k">t (threshold)</span><br><span class="v">{esc(seal.get('t'))}</span></div>
    <div><span class="k">n (shares)</span><br><span class="v">{esc(seal.get('n'))}</span></div>
    <div><span class="k">progress</span><br><span class="v">{esc(seal.get('progress'))}</span></div>
    <div><span class="k">nonce</span><br><span class="v">{esc(seal.get('nonce'))}</span></div>
    <div><span class="k">migration</span><br><span class="v">{esc(seal.get('migration'))}</span></div>
  </div>
</div>
<div class="card"><h2>Leader / HA</h2>
  <div class="grid">
    <div><span class="k">ha_enabled</span><br><span class="v">{esc(leader.get('ha_enabled'))}</span></div>
    <div><span class="k">is_self</span><br><span class="v">{esc(leader.get('is_self'))}</span></div>
    <div><span class="k">leader_address</span><br><span class="v">{esc(leader.get('leader_address'))}</span></div>
    <div><span class="k">leader_cluster_address</span><br><span class="v">{esc(leader.get('leader_cluster_address'))}</span></div>
    <div><span class="k">performance_standby</span><br><span class="v">{esc(leader.get('performance_standby'))}</span></div>
    <div><span class="k">raft_committed_index</span><br><span class="v">{esc(leader.get('raft_committed_index'))}</span></div>
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


def health_tasks(
    *,
    cluster_id: str,
    product: str,
    script_path: str,
    service_name: str,
    unit_after: str,
    http_port: int,
    api_port: int,
    scheme: str,
) -> List[str]:
    """Return Ansible task lines that install and start the health dashboard."""
    lines: List[str] = [
        f"    - name: Ensure python3 present for {product} health service",
        "      ansible.builtin.package:",
        "        name: python3",
        "        state: present",
        "      failed_when: false",
        f"    - name: Install {product} health dashboard script",
        "      ansible.builtin.copy:",
        f"        dest: {script_path}",
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
        f"    - name: Install {service_name} systemd unit",
        "      ansible.builtin.copy:",
        f"        dest: /etc/systemd/system/{service_name}",
        "        owner: root",
        "        group: root",
        "        mode: '0644'",
        "        content: |",
        "          [Unit]",
        f"          Description=OpenSible {product} HTTP health dashboard ({cluster_id})",
        f"          After=network-online.target {unit_after}",
        "          Wants=network-online.target",
        "          [Service]",
        "          Type=simple",
        f"          Environment=HTTP_PORT={http_port}",
        f"          Environment=CLUSTER_NAME={cluster_id}",
        f"          Environment=PRODUCT={product}",
        "          Environment=API_HOST=127.0.0.1",
        f"          Environment=API_PORT={api_port}",
        f"          Environment=SCHEME={scheme}",
        f"          ExecStart=/usr/bin/env python3 {script_path}",
        "          Restart=on-failure",
        "          RestartSec=3",
        "          User=root",
        "          [Install]",
        "          WantedBy=multi-user.target",
        "      register: _vh_unit",
        f"    - name: Enable and start {service_name}",
        "      ansible.builtin.systemd:",
        f"        name: {service_name}",
        "        enabled: true",
        "        state: restarted",
        "        daemon_reload: true",
        f"    - name: Wait for {product} health HTTP port {http_port}",
        "      ansible.builtin.wait_for:",
        "        host: 127.0.0.1",
        f"        port: {http_port}",
        "        timeout: 30",
        f"    - name: Report {product} health dashboard URL",
        "      ansible.builtin.debug:",
        f"        msg: \"{product} health dashboard: http://{{{{ ansible_host | default(inventory_hostname) }}}}:{http_port}/  (JSON: /health.json)\"",
    ]
    return lines
