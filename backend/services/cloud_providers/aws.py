"""AWS provider adapter — EC2 + VPC + Security Group (first iteration).

Scope: enough to stand up a public VPC with a subnet, security group, key pair
and EC2 instance pools. Additional services (RDS, ELB, S3, IAM roles) can be
added by extending the schema + IaC template without touching this file's
contract with `cloud_provisioning.py`.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .base import ProviderAdapter


SCHEMA: Dict[str, Any] = {
    "provider": "aws",
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
                {"name": "region", "label": "AWS Region", "type": "string", "default": "us-east-1", "required": True,
                 "help": "AWS region code (us-east-1, eu-west-1, ap-southeast-1, ...)."},
            ],
        },
        {
            "id": "credentials",
            "title": "Credentials",
            "icon": "fa-key",
            "secret": True,
            "fields": [
                {"name": "aws_access_key", "label": "AWS Access Key ID", "type": "secret", "required": True,
                 "help": "IAM user access key ID."},
                {"name": "aws_secret_key", "label": "AWS Secret Access Key", "type": "secret", "required": True,
                 "help": "IAM user secret access key. Store in the encrypted secret store."},
                {"name": "auth_method", "label": "VM login method", "type": "select", "default": "ssh_key",
                 "options": ["ssh_key", "password"],
                 "help": "'ssh_key' creates an aws_key_pair from your public key. 'password' uses cloud-init to create a Linux user with password login."},
                {"name": "ssh_public_key", "label": "SSH public key", "type": "string", "default": "",
                 "visible_when": {"auth_method": "ssh_key"},
                 "help": "ssh-ed25519 or ssh-rsa public key. Registered as an aws_key_pair and attached to every instance."},
                {"name": "admin_username", "label": "Admin username", "type": "string", "default": "opensible",
                 "visible_when": {"auth_method": "password"},
                 "help": "Linux user created on every instance via cloud-init, with sudo NOPASSWD."},
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
                {"name": "vpc_cidr", "label": "VPC CIDR", "type": "cidr", "default": "10.0.0.0/16", "required": True,
                 "help": "IPv4 CIDR block for the VPC."},
                {"name": "subnet_cidr", "label": "Subnet CIDR", "type": "cidr", "default": "10.0.1.0/24", "required": True,
                 "help": "Public subnet CIDR (must sit inside the VPC CIDR)."},
            ],
        },
        {
            "id": "firewall",
            "title": "Security Group",
            "icon": "fa-shield-halved",
            "fields": [
                {"name": "admin_cidrs", "label": "Admin CIDRs (SSH port 22)", "type": "json", "default": ["0.0.0.0/0"],
                 "help": "JSON array of source CIDRs allowed on port 22, e.g. [\"203.0.113.10/32\"]. Restrict in production."},
                {"name": "enable_web_ingress", "label": "Allow web ingress (ports 80/443)", "type": "bool", "default": True,
                 "help": "When enabled, opens 80 and 443 on the security group."},
                {"name": "web_cidrs", "label": "Web CIDRs (ports 80/443)", "type": "json", "default": ["0.0.0.0/0"],
                 "visible_when": {"enable_web_ingress": True},
                 "help": "Source CIDRs allowed on 80/443. Ignored if web ingress is disabled."},
                {"name": "custom_ingress_rules", "label": "Custom ingress rules (any port / CIDR)", "type": "json",
                 "default": [],
                 "help": (
                     "JSON array of extra ingress rules on the main security group. "
                     "Each entry is an object with description, protocol (tcp/udp/icmp/-1), "
                     "from_port, to_port, cidr_blocks. Example: "
                     "[{\"description\":\"App API\",\"protocol\":\"tcp\",\"from_port\":8080,"
                     "\"to_port\":8080,\"cidr_blocks\":[\"10.0.0.0/8\"]}]"
                 )},
            ],
        },
        {
            "id": "compute",
            "title": "Compute defaults",
            "icon": "fa-server",
            "fields": [
                {"name": "ami_id", "label": "AMI ID (optional)", "type": "string", "default": "",
                 "help": "Leave empty to auto-select the latest Canonical Ubuntu 24.04 LTS x86_64 AMI in the region."},
                {"name": "instance_type", "label": "Default instance type", "type": "string", "default": "t3.micro",
                 "help": "EC2 instance type (t3.micro, t3.small, m5.large, c6i.xlarge, ...)."},
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
                {"name": "platform_roles", "label": "Platform roles (rename / add / remove, set count + instance type per role)", "type": "role_counts",
                 "default": {"postgres": 1, "redis": 1, "observability": 1},
                 "roles": ["postgres", "redis", "observability", "nexus", "openbao", "runner"],
                 "flavor_field": "platform_overrides",
                 "flavor_key": "instance_type",
                 "help": "Edit role names inline, set VM count, and optionally override the instance type per role."},
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
        {
            "id": "alb",
            "title": "Load Balancer (ALB)",
            "icon": "fa-globe",
            "fields": [
                {"name": "enable_alb", "label": "Provision Application Load Balancer", "type": "bool", "default": False,
                 "help": "Creates an ALB across two subnets in different AZs, a target group and an HTTP listener. App-pool instances are auto-registered."},
                {"name": "alb_internal", "label": "Internal ALB (no public IPs)", "type": "bool", "default": False,
                 "visible_when": {"enable_alb": True}},
                {"name": "alb_target_port", "label": "Backend target port", "type": "number", "default": 80, "min": 1, "max": 65535,
                 "visible_when": {"enable_alb": True},
                 "help": "Port that App instances listen on. The ALB forwards HTTP traffic to this port."},
                {"name": "alb_health_check_path", "label": "Health-check path", "type": "string", "default": "/",
                 "visible_when": {"enable_alb": True},
                 "help": "HTTP path used by the target group to verify instance health."},
                {"name": "alb_certificate_arn", "label": "ACM certificate ARN (optional)", "type": "string", "default": "",
                 "visible_when": {"enable_alb": True},
                 "help": "When set, the ALB also exposes an HTTPS listener on 443 using this ACM certificate."},
                {"name": "subnet_cidr_b", "label": "Second subnet CIDR (different AZ)", "type": "cidr", "default": "10.0.2.0/24",
                 "visible_when": {"enable_alb": True},
                 "help": "A second subnet is required by the ALB (and by RDS). Created automatically in a different AZ."},
            ],
        },
        {
            "id": "rds",
            "title": "Database (RDS)",
            "icon": "fa-database",
            "fields": [
                {"name": "enable_rds", "label": "Provision Amazon RDS", "type": "bool", "default": False,
                 "help": "Creates a managed RDS instance in a private DB subnet group. Reachable from the App security group on the engine port."},
                {"name": "rds_engine", "label": "Engine", "type": "select", "default": "postgres",
                 "options": ["postgres", "mysql"], "visible_when": {"enable_rds": True}},
                {"name": "rds_engine_version", "label": "Engine version (optional)", "type": "string", "default": "",
                 "visible_when": {"enable_rds": True},
                 "help": "Empty = AWS default. Examples: '16.4' (postgres), '8.0' (mysql)."},
                {"name": "rds_instance_class", "label": "Instance class", "type": "string", "default": "db.t3.micro",
                 "visible_when": {"enable_rds": True},
                 "help": "db.t3.micro, db.t3.small, db.m5.large, ..."},
                {"name": "rds_allocated_storage", "label": "Allocated storage (GB)", "type": "number", "default": 20, "min": 5, "max": 65536,
                 "visible_when": {"enable_rds": True}},
                {"name": "rds_db_name", "label": "Initial database name", "type": "string", "default": "appdb",
                 "visible_when": {"enable_rds": True}},
                {"name": "rds_username", "label": "Master username", "type": "string", "default": "opensible",
                 "visible_when": {"enable_rds": True}},
                {"name": "rds_password", "label": "Master password", "type": "secret",
                 "visible_when": {"enable_rds": True},
                 "help": "Stored encrypted. Required when RDS is enabled."},
                {"name": "rds_publicly_accessible", "label": "Publicly accessible", "type": "bool", "default": False,
                 "visible_when": {"enable_rds": True},
                 "help": "Leave off unless you need to reach the DB from outside the VPC."},
                {"name": "rds_skip_final_snapshot", "label": "Skip final snapshot on destroy", "type": "bool", "default": True,
                 "visible_when": {"enable_rds": True},
                 "help": "Turn off for production so RDS keeps a snapshot when the stack is destroyed."},
            ],
        },
        {
            "id": "s3",
            "title": "Object storage (S3)",
            "icon": "fa-database",
            "fields": [
                {"name": "enable_s3", "label": "Provision S3 bucket", "type": "bool", "default": False,
                 "help": "Creates a bucket for backups, logs, assets and state files."},
                {"name": "s3_bucket_name", "label": "Bucket name (globally unique)", "type": "string", "default": "",
                 "visible_when": {"enable_s3": True},
                 "help": "S3 bucket names must be globally unique across all AWS accounts."},
                {"name": "s3_versioning", "label": "Enable object versioning", "type": "bool", "default": True,
                 "visible_when": {"enable_s3": True}},
                {"name": "s3_block_public_access", "label": "Block all public access", "type": "bool", "default": True,
                 "visible_when": {"enable_s3": True},
                 "help": "Recommended. Turn off only if you intentionally serve public content from the bucket."},
            ],
        },
    ],
}


TFVARS_ORDER = [
    "env", "project_name", "region",
    "vpc_cidr", "subnet_cidr", "subnet_cidr_b",
    "ami_id", "instance_type", "app_vm_count",
    "auth_method", "ssh_public_key", "admin_username", "admin_password",
    "admin_cidrs", "web_cidrs", "enable_web_ingress", "custom_ingress_rules",
    "enable_platform", "platform_roles", "platform_overrides",
    "extra_vms",
    "enable_alb", "alb_internal", "alb_target_port", "alb_health_check_path", "alb_certificate_arn",
    "enable_rds", "rds_engine", "rds_engine_version", "rds_instance_class",
    "rds_allocated_storage", "rds_db_name", "rds_username", "rds_password",
    "rds_publicly_accessible", "rds_skip_final_snapshot",
    "enable_s3", "s3_bucket_name", "s3_versioning", "s3_block_public_access",
    "labels",
]

SECRET_KEYS = ("aws_access_key", "aws_secret_key", "admin_password", "rds_password")


PLATFORM_OVERRIDE_KEYS = {"instance_type", "ami_id"}


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
    vpcs: Dict[str, Dict[str, Any]] = {}
    subnets: Dict[str, Dict[str, Any]] = {}
    security_groups: Dict[str, str] = {}

    resources = list(_iter_state_resources(state))

    for r in resources:
        t = r.get("type") or ""
        v = r.get("values") or {}
        rid = str(v.get("id")) if v.get("id") is not None else ""
        if t == "aws_vpc" and rid:
            tags = v.get("tags") or {}
            vpcs[rid] = {"name": tags.get("Name") if isinstance(tags, dict) else None, "cidr": v.get("cidr_block")}
        elif t == "aws_subnet" and rid:
            tags = v.get("tags") or {}
            subnets[rid] = {
                "id": rid,
                "name": tags.get("Name") if isinstance(tags, dict) else None,
                "cidr": v.get("cidr_block"),
                "gateway_ip": None,
                "vpc_id": str(v.get("vpc_id") or ""),
            }
        elif t == "aws_security_group" and rid:
            security_groups[rid] = v.get("name") or rid

    instances: List[Dict[str, Any]] = []
    for r in resources:
        if r.get("type") != "aws_instance":
            continue
        v = r.get("values") or {}
        tags = v.get("tags") or {}
        sg_ids = [str(x) for x in (v.get("vpc_security_group_ids") or [])]
        subnet_id = str(v.get("subnet_id") or "")
        subnet = subnets.get(subnet_id, {})
        vpc_id = subnet.get("vpc_id") or ""
        vpc = vpcs.get(vpc_id, {})
        instances.append({
            "address": r.get("address"),
            "hostname": (tags.get("Name") if isinstance(tags, dict) else None) or str(v.get("id") or ""),
            "instance_id": str(v.get("id")) if v.get("id") is not None else None,
            "status": v.get("instance_state"),
            "az": v.get("availability_zone"),
            "image_id": v.get("ami"),
            "flavor_id": v.get("instance_type"),
            "private_ip": v.get("private_ip") or None,
            "mac": None,
            "port_id": None,
            "public_ip": v.get("public_ip") or None,
            "subnet_id": subnet_id or None,
            "subnet_name": subnet.get("name"),
            "subnet_cidr": subnet.get("cidr"),
            "subnet_gateway": None,
            "vpc_id": vpc_id or None,
            "vpc_name": vpc.get("name"),
            "vpc_cidr": vpc.get("cidr"),
            "security_groups": [security_groups.get(x, x) for x in sg_ids],
            "system_disk_type": None,
            "system_disk_size": None,
            "role": tags.get("role") if isinstance(tags, dict) else None,
        })

    instances.sort(key=lambda x: (x.get("hostname") or ""))
    return {
        "vms": instances,
        "vpcs": [{"id": k, **val} for k, val in vpcs.items()],
        "subnets": list(subnets.values()),
        "eips": [{"id": vm["instance_id"], "address": vm["public_ip"]}
                 for vm in instances if vm.get("public_ip")],
        "load_balancers": [],
        "count": len(instances),
    }


ADAPTER = ProviderAdapter(
    id="aws",
    label="AWS",
    description="Amazon Web Services — VPC, EC2, Security Group, Key Pair, Internet Gateway.",
    logo="aws",
    schema=SCHEMA,
    tfvars_order=TFVARS_ORDER,
    secret_keys=SECRET_KEYS,
    platform_override_keys=PLATFORM_OVERRIDE_KEYS,
    build_inventory=build_inventory,
    enabled=True,
)
