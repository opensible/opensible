"""Template: Install nginx and configure a reverse proxy vhost."""
from __future__ import annotations

from typing import Any, Dict

from ._common import (
    slugify, yaml_str, render_hosts, indent_block,
    VAULT_FILES_VARIABLE, parse_vault_files, vars_files_lines,
)


TEMPLATE = {
    "id": "nginx-reverse-proxy",
    "name": "Nginx Reverse Proxy",
    "category": "Web",
    "icon": "globe",
    "description": "Install Nginx and drop a vhost that reverse-proxies to an upstream service.",
    "tags": ["nginx", "web", "proxy"],
    "variables": [
        {"name": "server_name", "label": "Server name (domain)", "type": "string", "required": True,
         "placeholder": "app.example.com"},
        {"name": "upstream_url", "label": "Upstream URL", "type": "string", "required": True,
         "placeholder": "http://127.0.0.1:8080"},
        {"name": "listen_port", "label": "Listen port", "type": "number", "default": 80},
        {"name": "client_max_body_size", "label": "client_max_body_size", "type": "string", "default": "50m"},
        {"name": "extra_headers", "label": "Extra proxy_set_header lines", "type": "code", "language": "nginx",
         "rows": 4, "default": "proxy_set_header X-Forwarded-Proto $scheme;\nproxy_set_header X-Real-IP $remote_addr;"},
        {"name": "become", "label": "Run as sudo (become)", "type": "boolean", "default": True},
        VAULT_FILES_VARIABLE,
    ],
}


def suggested_filename(values: Dict[str, Any]) -> str:
    return f"tmpl-nginx-{slugify(values.get('server_name'), 'site')}.yml"


def render(values: Dict[str, Any], targets: Dict[str, Any]) -> str:
    server_name = values.get("server_name") or "example.com"
    upstream = values.get("upstream_url") or "http://127.0.0.1:8080"
    port = int(values.get("listen_port") or 80)
    cmbs = values.get("client_max_body_size") or "50m"
    headers = values.get("extra_headers") or ""
    hosts = render_hosts(targets)
    become = "true" if values.get("become", True) else "false"
    site_slug = slugify(server_name, "site")

    vhost = f"""server {{
    listen {port};
    server_name {server_name};
    client_max_body_size {cmbs};

    location / {{
        proxy_pass {upstream};
        proxy_set_header Host $host;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
{indent_block(headers.rstrip(), "        ")}
    }}
}}
"""

    parts = [
        "---",
        f"# Rendered from template: {TEMPLATE['name']}",
        f"- name: Configure Nginx reverse proxy for {server_name}",
        f"  hosts: {hosts}",
        f"  become: {become}",
        "  gather_facts: true",
        *vars_files_lines(parse_vault_files(values.get("vault_files"))),
        "  tasks:",
        "    - name: Install nginx",
        "      ansible.builtin.package:",
        "        name: nginx",
        "        state: present",
        "    - name: Deploy vhost",
        "      ansible.builtin.copy:",
        f"        dest: /etc/nginx/sites-available/{site_slug}.conf",
        "        mode: '0644'",
        "        content: |",
        indent_block(vhost.rstrip("\n"), "          "),
        "    - name: Enable vhost",
        "      ansible.builtin.file:",
        f"        src: /etc/nginx/sites-available/{site_slug}.conf",
        f"        dest: /etc/nginx/sites-enabled/{site_slug}.conf",
        "        state: link",
        "        force: true",
        "    - name: Test nginx config",
        "      ansible.builtin.command: nginx -t",
        "      changed_when: false",
        "    - name: Reload nginx",
        "      ansible.builtin.service:",
        "        name: nginx",
        "        state: reloaded",
        "        enabled: true",
        "",
    ]
    return "\n".join(parts)
