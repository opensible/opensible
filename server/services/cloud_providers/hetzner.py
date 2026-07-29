"""Hetzner Cloud provider adapter — schema, tfvars order, secret keys, inventory."""
from __future__ import annotations

from typing import Any, Dict, List

from .base import ProviderAdapter


SCHEMA: Dict[str, Any] = {
    "provider": "hetzner",
    "groups": [
        {
            "id": "project",
            "title": "Project",
            "icon": "fa-folder",
            "fields": [
                {"name": "env", "label": "Environment", "type": "string", "default": "dev", "required": True,
                 "help": "Short env tag — dev / sit / prod."},
                {"name": "project_name", "label": "Project name (naming prefix)", "type": "string", "required": True,
                 "help": "Used as the prefix for every resource: <project_name>-<env>-<role>."},
                {"name": "location", "label": "Location", "type": "string", "default": "nbg1", "required": True,
                 "help": "Hetzner location code (nbg1, fsn1, hel1, ash, hil, sin)."},
            ],
        },
        {
            "id": "credentials",
            "title": "Credentials",
            "icon": "fa-key",
            "secret": True,
            "fields": [
                {"name": "hcloud_token", "label": "Hetzner Cloud API token", "type": "secret", "required": True,
                 "help": "Create in Hetzner Cloud Console → Project → Security → API Tokens (Read & Write)."},
                {"name": "auth_method", "label": "VM login method", "type": "select", "default": "ssh_key",
                 "options": ["ssh_key", "password"],
                 "help": "Choose 'ssh_key' to inject an SSH public key, or 'password' to create a Linux user with a password (cloud-init)."},
                {"name": "ssh_public_key", "label": "SSH public key", "type": "string", "default": "",
                 "visible_when": {"auth_method": "ssh_key"},
                 "help": "ssh-ed25519 or ssh-rsa public key. Uploaded to the Hetzner project and injected into every server."},
                {"name": "admin_username", "label": "Admin username", "type": "string", "default": "opensible",
                 "visible_when": {"auth_method": "password"},
                 "help": "Linux user created on every VM via cloud-init, with sudo NOPASSWD."},
                {"name": "admin_password", "label": "Admin password", "type": "secret",
                 "visible_when": {"auth_method": "password"},
                 "help": "Password for the admin user. Stored encrypted; injected via cloud-init user_data. Password SSH login is enabled on the VM."},
            ],
        },

        {
            "id": "network",
            "title": "Networking",
            "icon": "fa-network-wired",
            "fields": [
                {"name": "network_cidr", "label": "Network CIDR", "type": "cidr", "default": "10.0.0.0/16", "required": True,
                 "help": "Private network range for the Hetzner project."},
                {"name": "app_subnet_cidr", "label": "App subnet CIDR", "type": "cidr", "default": "10.0.1.0/24", "required": True,
                 "help": "Subnet inside the network CIDR where app + platform VMs attach."},
            ],
        },
        {
            "id": "firewall",
            "title": "Firewall",
            "icon": "fa-shield-halved",
            "fields": [
                {"name": "admin_cidrs", "label": "Admin CIDRs (SSH port 22)", "type": "json", "default": ["0.0.0.0/0"],
                 "help": "JSON array of source CIDRs allowed on port 22, e.g. [\"203.0.113.10/32\"]. Use your office/VPN IP in production."},
                {"name": "enable_web_ingress", "label": "Allow web ingress (ports 80/443)", "type": "bool", "default": True,
                 "help": "When enabled, opens 80 and 443 on the firewall to the Web CIDRs below."},
                {"name": "web_cidrs", "label": "Web CIDRs (ports 80/443)", "type": "json", "default": ["0.0.0.0/0"],
                 "visible_when": {"enable_web_ingress": True},
                 "help": "Source CIDRs allowed on 80/443. Ignored if web ingress is disabled."},
                {"name": "custom_ingress_rules", "label": "Custom firewall rules (any port / CIDR)", "type": "json",
                 "default": [],
                 "help": (
                     "JSON array of extra inbound rules. Each entry is an object with "
                     "description, protocol (tcp/udp/icmp), port (\"22\" or range \"30000-32767\", "
                     "omit for icmp), and source_ips. Example: "
                     "[{\"description\":\"App API\",\"protocol\":\"tcp\",\"port\":\"8080\","
                     "\"source_ips\":[\"10.0.0.0/8\"]}]"
                 )},
            ],
        },
        {
            "id": "compute",
            "title": "Compute defaults",
            "icon": "fa-server",
            "fields": [
                {"name": "image", "label": "Default image", "type": "string", "default": "ubuntu-24.04",
                 "help": "Hetzner image name (ubuntu-24.04, debian-12, ...)."},
                {"name": "server_type", "label": "Default server type", "type": "string", "default": "cx22",
                 "help": "Hetzner server type (cx22, cpx11, cax11, cpx31, ...)."},
                {"name": "app_vm_count", "label": "App VM count", "type": "number", "default": 0, "min": 0, "max": 50,
                 "help": "Number of generic app VMs to create. Leave 0 if you only want the Platform pool."},
            ],
        },
        {
            "id": "platform",
            "title": "Platform pool",
            "icon": "fa-layer-group",
            "fields": [
                {"name": "enable_platform", "label": "Provision platform pool", "type": "bool", "default": False},
                {"name": "platform_roles", "label": "Platform roles (rename / add / remove, set count + server type per role)", "type": "role_counts",
                 "default": {"postgres": 1, "redis": 1, "observability": 1},
                 "roles": ["postgres", "redis", "observability", "nexus", "openbao", "runner"],
                 "flavor_field": "platform_overrides",
                 "flavor_key": "server_type",
                 "help": "Edit role names inline, set VM count, and optionally override the server type per role."},
                {"name": "platform_overrides", "label": "Platform per-role overrides (auto)", "type": "hidden_map",
                 "default": {}},
            ],
        },
        {
            "id": "edge",
            "title": "Load balancer",
            "icon": "fa-globe",
            "fields": [
                {"name": "enable_load_balancer", "label": "Provision public load balancer", "type": "bool", "default": True},
                {"name": "load_balancer_type", "label": "Load balancer type", "type": "string", "default": "lb11",
                 "visible_when": {"enable_load_balancer": True},
                 "help": "lb11 / lb21 / lb31 — Hetzner LB size."},
            ],
        },
        {
            "id": "extras",
            "title": "Extra VMs (optional)",
            "icon": "fa-plus-square",
            "fields": [
                {"name": "extra_vms", "label": "Ad-hoc VMs (bastion / load-test / etc.)", "type": "extra_vms",
                 "default": {}},
            ],
        },
    ],
}


TFVARS_ORDER = [
    "env", "project_name", "location",
    "network_cidr", "app_subnet_cidr",
    "image", "server_type", "app_vm_count",
    "auth_method", "ssh_public_key", "admin_username", "admin_password",
    "admin_cidrs", "web_cidrs", "enable_web_ingress", "custom_ingress_rules",
    "enable_load_balancer", "load_balancer_type",
    "enable_platform", "platform_roles", "platform_overrides",
    "extra_vms", "labels",
]

SECRET_KEYS = ("hcloud_token", "admin_password")


PLATFORM_OVERRIDE_KEYS = {"server_type", "image", "location"}


def _iter_state_resources(state: Dict[str, Any]):
    for r in state.get("resources", []) or []:
        for inst in r.get("instances", []) or []:
            yield {
                "type": r.get("type"),
                "name": r.get("name"),
                "values": inst.get("attributes") or {},
                "address": (f"{r.get('module','')}." if r.get("module") else "") + f"{r.get('type')}.{r.get('name')}",
            }


def build_inventory(state: Dict[str, Any]) -> Dict[str, Any]:
    networks: Dict[str, Dict[str, Any]] = {}
    subnets_by_net: Dict[str, List[Dict[str, Any]]] = {}
    firewalls: Dict[str, str] = {}
    load_balancers: List[Dict[str, Any]] = []

    resources = list(_iter_state_resources(state))

    for r in resources:
        t = r.get("type") or ""
        v = r.get("values") or {}
        rid = str(v.get("id")) if v.get("id") is not None else ""
        if t == "hcloud_network" and rid:
            networks[rid] = {"name": v.get("name"), "cidr": v.get("ip_range")}
        elif t == "hcloud_network_subnet":
            nid = str(v.get("network_id") or "")
            if nid:
                subnets_by_net.setdefault(nid, []).append({
                    "id": rid or f"{nid}-{v.get('ip_range')}",
                    "name": v.get("network_zone") or "subnet",
                    "cidr": v.get("ip_range"),
                    "gateway_ip": None,
                    "vpc_id": nid,
                })
        elif t == "hcloud_firewall" and rid:
            firewalls[rid] = v.get("name") or rid
        elif t == "hcloud_load_balancer" and rid:
            load_balancers.append({
                "id": rid,
                "name": v.get("name"),
                "type": v.get("load_balancer_type"),
                "location": v.get("location"),
                "ipv4": v.get("ipv4"),
                "ipv6": v.get("ipv6"),
            })

    instances: List[Dict[str, Any]] = []
    for r in resources:
        if r.get("type") != "hcloud_server":
            continue
        v = r.get("values") or {}
        nets = v.get("network") or []
        first = nets[0] if nets else {}
        nid = str(first.get("network_id") or "")
        net = networks.get(nid, {})
        fw_ids = [str(x) for x in (v.get("firewall_ids") or [])]
        labels = v.get("labels") or {}
        instances.append({
            "address": r.get("address"),
            "hostname": v.get("name"),
            "instance_id": str(v.get("id")) if v.get("id") is not None else None,
            "status": v.get("status"),
            "az": v.get("location"),
            "image_id": v.get("image"),
            "flavor_id": v.get("server_type"),
            "private_ip": first.get("ip") or None,
            "mac": None,
            "port_id": None,
            "public_ip": v.get("ipv4_address") or None,
            "subnet_id": None,
            "subnet_name": None,
            "subnet_cidr": None,
            "subnet_gateway": None,
            "vpc_id": nid or None,
            "vpc_name": net.get("name"),
            "vpc_cidr": net.get("cidr"),
            "security_groups": [firewalls.get(x, x) for x in fw_ids],
            "system_disk_type": None,
            "system_disk_size": None,
            "role": labels.get("role") if isinstance(labels, dict) else None,
        })

    instances.sort(key=lambda x: (x.get("hostname") or ""))
    all_subnets: List[Dict[str, Any]] = []
    for sl in subnets_by_net.values():
        all_subnets.extend(sl)
    return {
        "vms": instances,
        "vpcs": [{"id": k, **val} for k, val in networks.items()],
        "subnets": all_subnets,
        "eips": [{"id": lb["id"], "address": lb.get("ipv4")} for lb in load_balancers if lb.get("ipv4")],
        "load_balancers": load_balancers,
        "count": len(instances),
    }


ADAPTER = ProviderAdapter(
    id="hetzner",
    label="Hetzner Cloud",
    description="Hetzner Cloud — servers, private network, firewall, load balancer.",
    logo="hetzner",
    schema=SCHEMA,
    tfvars_order=TFVARS_ORDER,
    secret_keys=SECRET_KEYS,
    platform_override_keys=PLATFORM_OVERRIDE_KEYS,
    build_inventory=build_inventory,
    enabled=True,
)
