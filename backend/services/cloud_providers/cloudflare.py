"""Cloudflare provider adapter — DNS zones, records, R2 buckets, Workers, Access apps.

Cloudflare has no VMs. The 'inventory' for a Cloudflare stack is synthesized from
DNS records (A/AAAA/CNAME) so downstream Ansible playbooks can still target the
managed hostnames.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .base import ProviderAdapter


SCHEMA: Dict[str, Any] = {
    "provider": "cloudflare",
    "groups": [
        {
            "id": "project",
            "title": "Project",
            "icon": "fa-folder",
            "fields": [
                {"name": "env", "label": "Environment", "type": "string", "default": "dev", "required": True,
                 "help": "Short env tag — dev / sit / prod."},
                {"name": "project_name", "label": "Project name (naming prefix)", "type": "string", "required": True,
                 "help": "Used as prefix and label on managed resources."},
            ],
        },
        {
            "id": "credentials",
            "title": "Credentials",
            "icon": "fa-key",
            "secret": True,
            "fields": [
                {"name": "api_token", "label": "Cloudflare API Token", "type": "secret", "required": True,
                 "help": "Create in Cloudflare Dashboard → My Profile → API Tokens. Needs Zone:Edit, DNS:Edit, Workers/R2/Access scopes as required."},
                {"name": "account_id", "label": "Cloudflare Account ID", "type": "string", "required": True,
                 "help": "Right-hand sidebar of any zone overview page in the Cloudflare dashboard."},
            ],
        },
        {
            "id": "zone",
            "title": "DNS Zone",
            "icon": "fa-globe",
            "fields": [
                {"name": "zone_name", "label": "Zone (domain)", "type": "string", "required": True,
                 "help": "Root domain — e.g. example.com. Used for records, worker routes, and Access apps."},
                {"name": "create_zone", "label": "Create the zone in Cloudflare", "type": "bool", "default": False,
                 "help": "OFF (default): use an existing zone (data source). ON: creates it (you must change registrar nameservers)."},
                {"name": "zone_plan", "label": "Zone plan (when creating)", "type": "select", "default": "free",
                 "options": ["free", "pro", "business", "enterprise"],
                 "visible_when": {"create_zone": True}},
            ],
        },
        {
            "id": "dns",
            "title": "DNS Records",
            "icon": "fa-network-wired",
            "fields": [
                {"name": "dns_records", "label": "DNS records (JSON array)", "type": "json",
                 "default": [
                     {"name": "@", "type": "A", "content": "192.0.2.10", "ttl": 1, "proxied": True},
                     {"name": "www", "type": "CNAME", "content": "@", "ttl": 1, "proxied": True},
                 ],
                 "help": 'Each record: {"name","type","content","ttl","proxied"}. Use "@" for the zone apex. ttl 1 = automatic.'},
            ],
        },
        {
            "id": "r2",
            "title": "R2 Object Storage",
            "icon": "fa-database",
            "fields": [
                {"name": "r2_buckets", "label": "R2 buckets (JSON array)", "type": "json",
                 "default": [],
                 "help": 'Each bucket: {"name","location"}. location optional (e.g. "wnam", "enam", "weur").'},
            ],
        },
        {
            "id": "workers",
            "title": "Workers",
            "icon": "fa-cog",
            "fields": [
                {"name": "workers", "label": "Worker scripts (JSON array)", "type": "json",
                 "default": [],
                 "help": 'Each: {"name","content","module":false}. content is the JS source string.'},
                {"name": "worker_routes", "label": "Worker routes (JSON array)", "type": "json",
                 "default": [],
                 "help": 'Each: {"pattern":"example.com/api/*","script_name":"my-worker"}.'},
            ],
        },
        {
            "id": "access",
            "title": "Zero Trust / Access",
            "icon": "fa-shield-halved",
            "fields": [
                {"name": "access_apps", "label": "Access applications (JSON array)", "type": "json",
                 "default": [],
                 "help": 'Each app: {"name","domain","session_duration":"24h","allowed_emails":["user@corp"]}. Emails list creates an allow-policy.'},
            ],
        },
    ],
}


TFVARS_ORDER = [
    "env", "project_name",
    "account_id",
    "zone_name", "create_zone", "zone_plan",
    "dns_records",
    "r2_buckets",
    "workers", "worker_routes",
    "access_apps",
    "labels",
]

SECRET_KEYS = ("api_token",)


# Cloudflare doesn't use platform_overrides, but base adapter expects the set.
PLATFORM_OVERRIDE_KEYS: set = set()


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
    """Synthesize a VM-like inventory from managed DNS records.

    A/AAAA records → hosts with public_ip = record content.
    CNAME records  → hosts with public_ip = None, aliased to content.
    """
    instances: List[Dict[str, Any]] = []
    zones: Dict[str, str] = {}
    r2: List[Dict[str, Any]] = []
    workers: List[Dict[str, Any]] = []
    access_apps: List[Dict[str, Any]] = []

    for r in _iter_state_resources(state):
        t = r.get("type") or ""
        v = r.get("values") or {}
        if t == "cloudflare_zone":
            zid = str(v.get("id") or "")
            zname = v.get("zone") or v.get("name")
            if zid and zname:
                zones[zid] = zname
        elif t == "cloudflare_r2_bucket":
            r2.append({"id": v.get("id"), "name": v.get("name"), "location": v.get("location")})
        elif t == "cloudflare_worker_script":
            workers.append({"id": v.get("id"), "name": v.get("name")})
        elif t == "cloudflare_access_application":
            access_apps.append({"id": v.get("id"), "name": v.get("name"), "domain": v.get("domain")})

    for r in _iter_state_resources(state):
        if r.get("type") != "cloudflare_record":
            continue
        v = r.get("values") or {}
        rtype = (v.get("type") or "").upper()
        if rtype not in ("A", "AAAA", "CNAME"):
            continue
        hostname = v.get("hostname") or v.get("name")
        content = v.get("content") or v.get("value")
        instances.append({
            "address": r.get("address"),
            "hostname": hostname,
            "instance_id": str(v.get("id")) if v.get("id") is not None else None,
            "status": "active",
            "az": None,
            "image_id": None,
            "flavor_id": rtype,
            "private_ip": None,
            "mac": None,
            "port_id": None,
            "public_ip": content if rtype in ("A", "AAAA") else None,
            "subnet_id": None,
            "subnet_name": None,
            "subnet_cidr": None,
            "subnet_gateway": None,
            "vpc_id": str(v.get("zone_id") or "") or None,
            "vpc_name": zones.get(str(v.get("zone_id") or "")),
            "vpc_cidr": None,
            "security_groups": [],
            "system_disk_type": None,
            "system_disk_size": None,
            "role": rtype.lower(),
            "cname_target": content if rtype == "CNAME" else None,
            "proxied": bool(v.get("proxied")),
        })

    instances.sort(key=lambda x: (x.get("hostname") or ""))
    return {
        "vms": instances,
        "vpcs": [{"id": zid, "name": zname, "cidr": None} for zid, zname in zones.items()],
        "subnets": [],
        "eips": [],
        "load_balancers": [],
        "r2_buckets": r2,
        "workers": workers,
        "access_apps": access_apps,
        "count": len(instances),
    }


ADAPTER = ProviderAdapter(
    id="cloudflare",
    label="Cloudflare",
    description="Cloudflare — DNS zone, records, R2 buckets, Workers, Zero Trust Access.",
    logo="cloudflare",
    schema=SCHEMA,
    tfvars_order=TFVARS_ORDER,
    secret_keys=SECRET_KEYS,
    platform_override_keys=PLATFORM_OVERRIDE_KEYS,
    build_inventory=build_inventory,
    enabled=True,
)
