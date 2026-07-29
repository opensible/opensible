"""Google Cloud (GCP) provider adapter — Compute Engine + VPC + Firewall.

First iteration mirrors the AWS provider shape so the wizard and provisioning
pipeline stay uniform. Scope: VPC, subnet, firewall rules, and Compute Engine
instance pools (App / Platform / Extras). Managed services (GKE, Cloud SQL,
GCS) can be added by extending the schema and IaC template without touching
the adapter contract with `cloud_provisioning.py`.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .base import ProviderAdapter


SCHEMA: Dict[str, Any] = {
    "provider": "gcp",
    "groups": [
        {
            "id": "project",
            "title": "Project",
            "icon": "fa-folder",
            "fields": [
                {"name": "env", "label": "Environment", "type": "string", "default": "dev", "required": True,
                 "help": "Short env tag — dev / sit / prod."},
                {"name": "project_name", "label": "Project name (naming prefix)", "type": "string", "required": True,
                 "help": "Prefix used for every resource: <project_name>-<env>-<role>."},
                {"name": "gcp_project_id", "label": "GCP Project ID", "type": "string", "required": True,
                 "help": "The Google Cloud project id (e.g. 'my-org-prod-1234')."},
                {"name": "region", "label": "GCP Region", "type": "string", "default": "us-central1", "required": True,
                 "help": "GCP region (us-central1, europe-west1, asia-southeast1, ...)."},
                {"name": "zone", "label": "GCP Zone", "type": "string", "default": "us-central1-a", "required": True,
                 "help": "Zone inside the region (e.g. us-central1-a). VMs are created in this zone."},
            ],
        },
        {
            "id": "credentials",
            "title": "Credentials",
            "icon": "fa-key",
            "secret": True,
            "fields": [
                {"name": "gcp_credentials_json", "label": "Service Account JSON key", "type": "secret", "required": True,
                 "help": "Paste the full JSON key file for a service account with Compute Admin + Network Admin. Stored encrypted."},
                {"name": "auth_method", "label": "VM login method", "type": "select", "default": "ssh_key",
                 "options": ["ssh_key", "password"],
                 "help": "'ssh_key' injects your public key via instance metadata. 'password' uses cloud-init to create a Linux user with password login."},
                {"name": "ssh_public_key", "label": "SSH public key", "type": "string", "default": "",
                 "visible_when": {"auth_method": "ssh_key"},
                 "help": "ssh-ed25519 or ssh-rsa public key. Attached to every instance via ssh-keys metadata."},
                {"name": "admin_username", "label": "Admin username", "type": "string", "default": "opensible",
                 "help": "Linux user for SSH (created via cloud-init when auth_method='password', or used as the metadata key user)."},
                {"name": "admin_password", "label": "Admin password", "type": "secret",
                 "visible_when": {"auth_method": "password"},
                 "help": "Password for the admin user. Injected via cloud-init user_data."},
            ],
        },
        {
            "id": "network",
            "title": "Networking",
            "icon": "fa-network-wired",
            "fields": [
                {"name": "subnet_cidr", "label": "Subnet CIDR", "type": "cidr", "default": "10.10.0.0/24", "required": True,
                 "help": "Primary CIDR range for the auto-created subnet in the selected region."},
            ],
        },
        {
            "id": "firewall",
            "title": "Firewall",
            "icon": "fa-shield-halved",
            "fields": [
                {"name": "admin_cidrs", "label": "Admin CIDRs (SSH port 22)", "type": "json", "default": ["0.0.0.0/0"],
                 "help": "JSON array of source CIDRs allowed on port 22. Restrict in production."},
                {"name": "enable_web_ingress", "label": "Allow web ingress (ports 80/443)", "type": "bool", "default": True,
                 "help": "Opens 80/443 on the firewall when enabled."},
                {"name": "web_cidrs", "label": "Web CIDRs (ports 80/443)", "type": "json", "default": ["0.0.0.0/0"],
                 "visible_when": {"enable_web_ingress": True},
                 "help": "Source CIDRs allowed on 80/443."},
                {"name": "custom_ingress_rules", "label": "Custom ingress rules (any port / CIDR)", "type": "json",
                 "default": [],
                 "help": (
                     "JSON array of extra firewall rules. Each entry is an object with description, "
                     "protocol (tcp/udp/icmp), ports (list of strings — single \"8080\" or range \"30000-32767\", "
                     "omit for icmp), and source_ranges. Example: "
                     "[{\"description\":\"App API\",\"protocol\":\"tcp\",\"ports\":[\"8080\"],"
                     "\"source_ranges\":[\"10.0.0.0/8\"]}]"
                 )},
            ],
        },

        {
            "id": "compute",
            "title": "Compute defaults",
            "icon": "fa-server",
            "fields": [
                {"name": "image", "label": "Boot image", "type": "string",
                 "default": "ubuntu-os-cloud/ubuntu-2404-lts-amd64",
                 "help": "GCE image in '<project>/<family-or-image>' form. Default = Ubuntu 24.04 LTS."},
                {"name": "machine_type", "label": "Default machine type", "type": "string", "default": "e2-small",
                 "help": "GCE machine type (e2-small, e2-medium, n2-standard-2, c3-standard-4, ...)."},
                {"name": "disk_size_gb", "label": "Boot disk size (GB)", "type": "number", "default": 20, "min": 10, "max": 2000,
                 "help": "Boot persistent disk size in gibibytes."},
                {"name": "app_vm_count", "label": "App VM count", "type": "number", "default": 0, "min": 0, "max": 50,
                 "help": "Number of generic app VMs. Leave 0 if you only want the Platform pool."},
            ],
        },
        {
            "id": "platform",
            "title": "Platform pool",
            "icon": "fa-layer-group",
            "fields": [
                {"name": "enable_platform", "label": "Provision platform pool", "type": "bool", "default": False},
                {"name": "platform_roles", "label": "Platform roles (rename / add / remove, set count + machine type per role)", "type": "role_counts",
                 "default": {"postgres": 1, "redis": 1, "observability": 1},
                 "roles": ["postgres", "redis", "observability", "nexus", "openbao", "runner"],
                 "flavor_field": "platform_overrides",
                 "flavor_key": "machine_type",
                 "help": "Edit role names inline, set VM count, and optionally override the machine type per role."},
                {"name": "platform_overrides", "label": "Platform per-role overrides (auto)", "type": "hidden_map",
                 "default": {}},
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
    "env", "project_name", "gcp_project_id", "region", "zone",
    "subnet_cidr",
    "image", "machine_type", "disk_size_gb", "app_vm_count",
    "auth_method", "ssh_public_key", "admin_username", "admin_password",
    "admin_cidrs", "web_cidrs", "enable_web_ingress", "custom_ingress_rules",
    "enable_platform", "platform_roles", "platform_overrides",
    "extra_vms", "labels",
]

SECRET_KEYS = ("gcp_credentials_json", "admin_password")

PLATFORM_OVERRIDE_KEYS = {"machine_type", "image", "disk_size_gb"}


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
    subnets: Dict[str, Dict[str, Any]] = {}
    firewalls: Dict[str, str] = {}

    resources = list(_iter_state_resources(state))

    for r in resources:
        t = r.get("type") or ""
        v = r.get("values") or {}
        rid = str(v.get("id")) if v.get("id") is not None else ""
        if t == "google_compute_network" and rid:
            networks[rid] = {"name": v.get("name"), "cidr": None}
        elif t == "google_compute_subnetwork" and rid:
            subnets[rid] = {
                "id": rid,
                "name": v.get("name"),
                "cidr": v.get("ip_cidr_range"),
                "gateway_ip": v.get("gateway_address"),
                "network_id": str(v.get("network") or ""),
            }
        elif t == "google_compute_firewall" and rid:
            firewalls[rid] = v.get("name") or rid

    instances: List[Dict[str, Any]] = []
    for r in resources:
        if r.get("type") != "google_compute_instance":
            continue
        v = r.get("values") or {}
        labels = v.get("labels") or {}
        nics = v.get("network_interface") or []
        first_nic = nics[0] if nics else {}
        acc = first_nic.get("access_config") or []
        pub_ip = (acc[0].get("nat_ip") if acc else None) if isinstance(acc, list) else None
        subnet_url = str(first_nic.get("subnetwork") or "")
        # match by trailing name
        subnet_match = next((s for s in subnets.values() if s.get("name") and subnet_url.endswith(f"/{s['name']}")), {})
        boot_disks = v.get("boot_disk") or []
        boot0 = (boot_disks[0] if boot_disks else {}) or {}
        init_params = (boot0.get("initialize_params") or [{}])[0] if isinstance(boot0.get("initialize_params"), list) else {}
        instances.append({
            "address": r.get("address"),
            "hostname": v.get("name") or str(v.get("id") or ""),
            "instance_id": str(v.get("instance_id") or v.get("id") or "") or None,
            "status": v.get("current_status"),
            "az": v.get("zone"),
            "image_id": init_params.get("image") if isinstance(init_params, dict) else None,
            "flavor_id": v.get("machine_type"),
            "private_ip": first_nic.get("network_ip") or None,
            "mac": None,
            "port_id": None,
            "public_ip": pub_ip,
            "subnet_id": subnet_match.get("id"),
            "subnet_name": subnet_match.get("name"),
            "subnet_cidr": subnet_match.get("cidr"),
            "subnet_gateway": subnet_match.get("gateway_ip"),
            "vpc_id": subnet_match.get("network_id"),
            "vpc_name": None,
            "vpc_cidr": None,
            "security_groups": [f["name"] if isinstance(f, dict) else str(f) for f in firewalls.values()],
            "system_disk_type": init_params.get("type") if isinstance(init_params, dict) else None,
            "system_disk_size": init_params.get("size") if isinstance(init_params, dict) else None,
            "role": labels.get("role") if isinstance(labels, dict) else None,
        })

    instances.sort(key=lambda x: (x.get("hostname") or ""))
    return {
        "vms": instances,
        "vpcs": [{"id": k, **val} for k, val in networks.items()],
        "subnets": list(subnets.values()),
        "eips": [{"id": vm["instance_id"], "address": vm["public_ip"]}
                 for vm in instances if vm.get("public_ip")],
        "load_balancers": [],
        "count": len(instances),
    }


ADAPTER = ProviderAdapter(
    id="gcp",
    label="Google Cloud",
    description="Google Cloud Platform — VPC, Compute Engine, Firewall, Cloud NAT.",
    logo="gcp",
    schema=SCHEMA,
    tfvars_order=TFVARS_ORDER,
    secret_keys=SECRET_KEYS,
    platform_override_keys=PLATFORM_OVERRIDE_KEYS,
    build_inventory=build_inventory,
    enabled=True,
)
