"""ByteDC provider adapter — schema, tfvars order, secret keys, inventory."""
from __future__ import annotations

from typing import Any, Dict, List

from .base import ProviderAdapter


SCHEMA: Dict[str, Any] = {
    "provider": "bytedc",
    "groups": [
        {
            "id": "project",
            "title": "Project",
            "icon": "fa-folder",
            "fields": [
                {"name": "env", "label": "Environment", "type": "string", "default": "dev", "required": True,
                 "help": "Short env tag — dev / sit / prod."},
                {"name": "project_name", "label": "ByteDC Project Name", "type": "string", "required": True,
                 "help": "Resource space name from ByteDC console (Service List > Resource Space). Used for the HCS provider."},
                {"name": "name_prefix", "label": "Stack Project Name (Naming Prefix)", "type": "string", "default": "",
                 "help": "Prefix used for VM/VPC/ELB names: <prefix>-<env>-<role>. Falls back to ByteDC Project Name when empty."},
                {"name": "region", "label": "Region", "type": "string", "default": "", "required": True,
                 "help": "ByteDC region ID from the console, e.g. cn-north-1."},
                {"name": "az", "label": "Availability Zone", "type": "string", "default": "az1.dc1", "required": True},
            ],
        },
        {
            "id": "credentials",
            "title": "Credentials",
            "icon": "fa-key",
            "secret": True,
            "fields": [
                {"name": "access_key", "label": "Access Key", "type": "secret", "required": True},
                {"name": "secret_key", "label": "Secret Key", "type": "secret", "required": True},
            ],
        },
        {
            "id": "network",
            "title": "Networking (VPC)",
            "icon": "fa-network-wired",
            "fields": [
                {"name": "vpc_name", "label": "VPC name (optional)", "type": "string", "default": "",
                 "help": "Custom VPC display name. Leave blank to use the default <prefix>-<env>-vpc."},
                {"name": "vpc_cidr", "label": "VPC CIDR", "type": "cidr", "default": "10.0.0.0/16", "required": True},

                {"name": "public_subnet_cidr", "label": "Public subnet CIDR", "type": "cidr", "default": "10.0.0.0/24"},
                {"name": "public_subnet_gw", "label": "Public subnet gateway", "type": "string", "default": "10.0.0.1"},
                {"name": "app_subnet_cidr", "label": "App subnet CIDR", "type": "cidr", "default": "10.0.1.0/24"},
                {"name": "app_subnet_gw", "label": "App subnet gateway", "type": "string", "default": "10.0.1.1"},
                {"name": "data_subnet_cidr", "label": "Data subnet CIDR", "type": "cidr", "default": "10.0.250.0/24"},
                {"name": "data_subnet_gw", "label": "Data subnet gateway", "type": "string", "default": "10.0.250.1"},
                {"name": "admin_cidr", "label": "Admin CIDR (SSH/admin UIs)", "type": "cidr",
                 "default": "", "help": "Your office/public CIDR allowed to reach port 2222 + admin UIs. Example: 203.0.113.10/32."},
                {"name": "web_cidr", "label": "Web CIDR (80/443)", "type": "cidr", "default": "0.0.0.0/0"},
                {"name": "enable_web_ingress", "label": "Enable web ingress", "type": "bool", "default": True},

                {"name": "use_existing_network", "label": "Reuse an existing VPC / subnets", "type": "bool", "default": False,
                 "help": "Off by default — the stack creates its own VPC, subnets and SGs. Turn on only when you want this stack to plug into an existing network so multiple stacks can reach each other."},
                {"name": "existing_vpc_id", "label": "Existing VPC ID", "type": "string", "default": "",
                 "visible_when": {"use_existing_network": True},
                 "help": "Required when reuse is on. When set, VPC creation is skipped. You MUST also fill all three subnet IDs below (Public, App, Data) — otherwise new subnets are created inside the existing VPC and may collide with your CIDRs."},
                {"name": "existing_public_subnet_id", "label": "Existing Public Subnet ID", "type": "string", "default": "",
                 "visible_when": {"use_existing_network": True},
                 "help": "Required when reuse is on."},
                {"name": "existing_app_subnet_id", "label": "Existing App Subnet ID", "type": "string", "default": "",
                 "visible_when": {"use_existing_network": True},
                 "help": "Required when reuse is on."},
                {"name": "existing_data_subnet_id", "label": "Existing Data Subnet ID", "type": "string", "default": "",
                 "visible_when": {"use_existing_network": True},
                 "help": "Required when reuse is on."},
                {"name": "existing_public_ipv4_subnet_id", "label": "Existing Public IPv4 Subnet ID (ELB only)", "type": "string", "default": "",
                 "visible_when": {"use_existing_network": True},
                 "help": "Only required if Enable ELB is on. The neutron IPv4 subnet ID of the public subnet (different from the VPC subnet ID)."},
                {"name": "existing_app_ipv4_subnet_id", "label": "Existing App IPv4 Subnet ID (ELB only)", "type": "string", "default": "",
                 "visible_when": {"use_existing_network": True}},
                {"name": "existing_data_ipv4_subnet_id", "label": "Existing Data IPv4 Subnet ID (ELB only)", "type": "string", "default": "",
                 "visible_when": {"use_existing_network": True}},
                {"name": "existing_app_sg_id", "label": "Existing App SG ID (optional)", "type": "string", "default": "",
                 "visible_when": {"use_existing_network": True},
                 "help": "Reuse the previous stack's app-sg so you don't get duplicate-name conflicts. Leave blank to create a fresh SG."},
                {"name": "existing_data_sg_id", "label": "Existing Data SG ID (optional)", "type": "string", "default": "",
                 "visible_when": {"use_existing_network": True}},
            ],
        },
        {
            "id": "compute",
            "title": "Compute defaults",
            "icon": "fa-server",
            "fields": [
                {"name": "image_id", "label": "ByteDC IMS image ID", "type": "string", "required": True,
                 "help": "e.g. Ubuntu 22.04 image UUID. Used as the default for the Platform pool."},
                {"name": "flavor_id", "label": "Default flavor", "type": "string", "default": "s3.small.1",
                 "help": "Fallback flavor for Platform pool roles that don't set their own."},
            ],
        },
        {
            "id": "platform",
            "title": "Platform pool",
            "icon": "fa-layer-group",
            "fields": [
                {"name": "enable_platform", "label": "Provision platform pool", "type": "bool", "default": True},
                {"name": "platform_roles", "label": "Platform roles (rename / add / remove, set count + subnet + flavor per role)", "type": "role_counts",
                 "default": {"postgres": 1, "redis": 1, "nexus": 1, "openbao": 1, "observability": 1, "runner": 1},
                 "roles": ["postgres", "redis", "nexus", "openbao", "observability", "runner"],
                 "subnet_field": "platform_subnets",
                 "subnet_options": ["data", "app"],
                 "flavor_field": "platform_overrides",
                 "flavor_key": "flavor_id",
                 "help": "Edit role names inline, set VM count, pick subnet (data/app), and optionally override the flavor per role. Leave flavor blank to use the default."},
                {"name": "platform_subnets", "label": "Platform subnet assignment (auto)", "type": "hidden_map",
                 "default": {"postgres": "data", "redis": "data", "nexus": "data", "openbao": "data", "observability": "data", "runner": "data"}},
                {"name": "platform_overrides", "label": "Platform per-role overrides (auto)", "type": "hidden_map",
                 "default": {}},
                {"name": "platform_eip_roles", "label": "Roles getting a public EIP", "type": "multiselect",
                 "source": "platform_roles",
                 "options": ["postgres", "redis", "nexus", "openbao", "observability", "runner"],
                 "default": ["runner", "observability"]},
            ],
        },
        {
            "id": "edge",
            "title": "Edge (ELB / NAT / DNS)",
            "icon": "fa-globe",
            "fields": [
                {"name": "enable_elb", "label": "Provision ELB + EIP", "type": "bool", "default": True},
                {"name": "enable_nat", "label": "Enable NAT / outbound egress", "type": "bool", "default": True,
                 "help": "For a new VPC, this creates a NAT gateway. For an existing VPC, leave this off unless you also provide an Existing NAT Gateway ID or explicitly create a new NAT in that VPC."},
                {"name": "enable_dns", "label": "Provision private DNS zone", "type": "bool", "default": False},
                {"name": "domain_base", "label": "Domain base", "type": "string", "default": "example.com",
                 "help": "Base DNS domain for the private zone, e.g. internal.example.com."},
                {"name": "platform_eip_pool_type", "label": "Platform EIP pool", "type": "string", "default": ""},
                {"name": "nat_eip_pool_type", "label": "NAT EIP pool", "type": "string", "default": ""},
                {"name": "use_existing_nat", "label": "Reuse / manage an existing NAT gateway", "type": "bool", "default": False,
                 "help": "Off by default. Turn on only when you want to plug into an existing NAT gateway (or intentionally create another NAT inside a reused VPC)."},
                {"name": "existing_nat_gateway_id", "label": "Existing NAT Gateway ID (optional)", "type": "string", "default": "",
                 "visible_when": {"use_existing_nat": True},
                 "help": "Use this when the selected existing VPC already has a NAT gateway. The stack will not create a duplicate NAT gateway."},
                {"name": "create_nat_in_existing_vpc", "label": "Create new NAT inside existing VPC", "type": "bool", "default": False,
                 "visible_when": {"use_existing_nat": True},
                 "help": "Advanced: only turn on when you intentionally want this stack to create another NAT gateway in a reused VPC."},
                {"name": "manage_existing_nat_snat_rules", "label": "Manage SNAT rules on existing NAT", "type": "bool", "default": False,
                 "visible_when": {"use_existing_nat": True},
                 "help": "Usually off. Turn on only if you provide NAT EIP ID and want this stack to add SNAT rules to an existing NAT gateway."},
                {"name": "nat_floating_ip_id", "label": "NAT EIP ID (optional)", "type": "string", "default": "",
                 "visible_when": {"use_existing_nat": True},
                 "help": "Existing EIP ID for NAT. Required only when managing SNAT rules on an existing NAT gateway."},
            ],
        },
        {
            "id": "security",
            "title": "Security & Access",
            "icon": "fa-shield-halved",
            "fields": [
                {"name": "ecs_admin_pass", "label": "ECS Admin Password", "type": "secret", "required": True,
                 "help": "Default root/admin password seeded into every provisioned VM. Stored encrypted."},
                {"name": "ssh_port", "label": "Custom SSH Port", "type": "number", "default": 2222,
                 "min": 1, "max": 65535,
                 "help": "Port opened on the app/data security groups from the Admin CIDR. Golden image ships on 2222."},
                {"name": "extra_users", "label": "Extra Linux Users",
                 "type": "json", "default": [],
                 "help": "List of additional users seeded on every VM. Example:\n"
                         "[{\"name\": \"alice\", \"password\": \"Str0ng!Pass\", \"sudo\": true, \"ssh_key\": \"ssh-ed25519 AAAA...\"}]"},
                {"name": "ingress_rules", "label": "Extra Security Group Ingress Rules",
                 "type": "json", "default": [],
                 "help": "List of extra inbound rules applied to the app SG. Example:\n"
                         "[{\"protocol\": \"tcp\", \"port\": 8080, \"cidr\": \"10.0.0.0/8\", \"sg\": \"app\", \"description\": \"internal api\"}]\n"
                         "sg: \"app\" | \"data\" (default app)."},
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
    "env", "region", "project_name", "name_prefix",
    "vpc_name", "vpc_cidr",
    "public_subnet_cidr", "public_subnet_gw",
    "app_subnet_cidr", "app_subnet_gw",
    "data_subnet_cidr", "data_subnet_gw",
    "admin_cidr", "web_cidr", "enable_web_ingress",
    "existing_vpc_id",
    "existing_public_subnet_id", "existing_app_subnet_id", "existing_data_subnet_id",
    "existing_public_ipv4_subnet_id", "existing_app_ipv4_subnet_id", "existing_data_ipv4_subnet_id",
    "existing_app_sg_id", "existing_data_sg_id",
    "az", "image_id", "flavor_id", "vm_count",
    "enable_elb", "enable_nat", "enable_dns", "domain_base",
    "enable_platform", "platform_roles", "platform_subnets",
    "platform_overrides", "platform_eip_roles",
    "platform_eip_pool_type", "nat_eip_pool_type",
    "existing_nat_gateway_id", "create_nat_in_existing_vpc", "manage_existing_nat_snat_rules", "nat_floating_ip_id",
    "ssh_port", "extra_users", "ingress_rules",
    "extra_vms",
]

SECRET_KEYS = ("access_key", "secret_key", "ecs_admin_pass")

PLATFORM_OVERRIDE_KEYS = {"flavor_id", "image_id", "az", "az_zone", "eip_pool_type"}


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
    subnets: Dict[str, Dict[str, Any]] = {}
    vpcs: Dict[str, Dict[str, Any]] = {}
    eips_by_instance: Dict[str, str] = {}
    sgs: Dict[str, str] = {}
    raw_eips: Dict[str, str] = {}

    resources = list(_iter_state_resources(state))

    for r in resources:
        t = r.get("type") or ""
        v = r.get("values") or {}
        rid = v.get("id")
        if t == "hcs_vpc_subnet" and rid:
            subnets[rid] = {
                "name": v.get("name"), "cidr": v.get("cidr"),
                "gateway_ip": v.get("gateway_ip"), "vpc_id": v.get("vpc_id"),
            }
        elif t == "hcs_vpc" and rid:
            vpcs[rid] = {"name": v.get("name"), "cidr": v.get("cidr")}
        elif t == "hcs_networking_secgroup" and rid:
            sgs[rid] = v.get("name") or rid
        elif t == "hcs_vpc_eip" and rid:
            addr = v.get("address") or ""
            if not addr:
                pub = v.get("publicip") or []
                if pub and isinstance(pub, list):
                    addr = pub[0].get("ip_address", "") if isinstance(pub[0], dict) else ""
            raw_eips[rid] = addr
        elif t == "hcs_ecs_compute_eip_associate":
            pip = v.get("public_ip"); iid = v.get("instance_id")
            if pip and iid:
                eips_by_instance[iid] = pip

    instances: List[Dict[str, Any]] = []
    for r in resources:
        if r.get("type") != "hcs_ecs_compute_instance":
            continue
        v = r.get("values") or {}
        nics = v.get("network") or []
        first = nics[0] if nics else {}
        subnet_id = first.get("uuid")
        sn = subnets.get(subnet_id or "", {})
        vpc = vpcs.get(sn.get("vpc_id") or "", {})
        sg_ids = v.get("security_group_ids") or []
        instances.append({
            "address": r.get("address"),
            "hostname": v.get("name"),
            "instance_id": v.get("id"),
            "status": v.get("status"),
            "az": v.get("availability_zone"),
            "image_id": v.get("image_id"),
            "flavor_id": v.get("flavor_id"),
            "private_ip": first.get("fixed_ip_v4") or v.get("access_ip_v4"),
            "mac": first.get("mac"),
            "port_id": first.get("port"),
            "public_ip": eips_by_instance.get(v.get("id") or "") or None,
            "subnet_id": subnet_id,
            "subnet_name": sn.get("name"),
            "subnet_cidr": sn.get("cidr"),
            "subnet_gateway": sn.get("gateway_ip"),
            "vpc_id": sn.get("vpc_id"),
            "vpc_name": vpc.get("name"),
            "vpc_cidr": vpc.get("cidr"),
            "security_groups": [sgs.get(s, s) for s in sg_ids],
            "system_disk_type": v.get("system_disk_type"),
            "system_disk_size": v.get("system_disk_size"),
        })

    instances.sort(key=lambda x: (x.get("hostname") or ""))
    return {
        "vms": instances,
        "vpcs": [{"id": k, **val} for k, val in vpcs.items()],
        "subnets": [{"id": k, **val} for k, val in subnets.items()],
        "eips": [{"id": k, "address": a} for k, a in raw_eips.items()],
        "count": len(instances),
    }


ADAPTER = ProviderAdapter(
    id="bytedc",
    label="ByteDC",
    description="Huawei-compatible private cloud — VPC, ECS, ELB, NAT, DNS.",
    logo="bytedc",
    schema=SCHEMA,
    tfvars_order=TFVARS_ORDER,
    secret_keys=SECRET_KEYS,
    platform_override_keys=PLATFORM_OVERRIDE_KEYS,
    build_inventory=build_inventory,
    enabled=True,
)
