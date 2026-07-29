"""Amazon EKS (Elastic Kubernetes Service) provider adapter.

Provisions a managed Kubernetes cluster on AWS: a public VPC with two or more
subnets across AZs (EKS requirement), IAM roles for the control plane and
nodes, an EKS cluster, a primary managed node group, and any number of
extra node groups via a JSON map.

Reuses the AWS credentials (access key + secret) and shares the display
logo with the plain AWS provider. Kept as a separate adapter so the schema
and OpenTofu template can evolve independently from EC2.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .base import ProviderAdapter


SCHEMA: Dict[str, Any] = {
    "provider": "eks",
    "groups": [
        {
            "id": "project",
            "title": "Project",
            "icon": "fa-folder",
            "fields": [
                {"name": "env", "label": "Environment", "type": "string", "default": "dev", "required": True,
                 "help": "Short env tag — dev / sit / prod."},
                {"name": "project_name", "label": "Project name (naming prefix)", "type": "string", "required": True,
                 "help": "Prefix for every resource: <project_name>-<env>-<role>."},
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
                 "help": "IAM user access key ID with EKS + EC2 + IAM permissions."},
                {"name": "aws_secret_key", "label": "AWS Secret Access Key", "type": "secret", "required": True,
                 "help": "IAM user secret access key. Stored encrypted per stack."},
            ],
        },
        {
            "id": "cluster",
            "title": "Cluster",
            "icon": "fa-layer-group",
            "fields": [
                {"name": "kubernetes_version", "label": "Kubernetes version", "type": "string", "default": "1.31",
                 "placeholder": "e.g. 1.31",
                 "help": "EKS control-plane version (e.g. 1.30, 1.31, 1.32). Must be a version currently supported by AWS EKS in your region."},
                {"name": "create_iam_roles", "label": "Create IAM roles", "type": "bool", "default": True,
                 "help": "Turn off if your AWS user cannot create IAM roles. You must then provide existing EKS role ARNs."},
                {"name": "existing_cluster_role_arn", "label": "Existing EKS cluster role ARN", "type": "string", "default": "",
                 "visible_when": {"create_iam_roles": False},
                 "help": "Role trusted by eks.amazonaws.com with AmazonEKSClusterPolicy and AmazonEKSVPCResourceController."},
                {"name": "existing_node_role_arn", "label": "Existing EKS node role ARN", "type": "string", "default": "",
                 "visible_when": {"create_iam_roles": False},
                 "help": "Role trusted by ec2.amazonaws.com with AmazonEKSWorkerNodePolicy, AmazonEKS_CNI_Policy, and AmazonEC2ContainerRegistryReadOnly."},
            ],
        },
        {
            "id": "network",
            "title": "Networking",
            "icon": "fa-network-wired",
            "fields": [
                {"name": "vpc_cidr", "label": "VPC CIDR", "type": "cidr", "default": "10.30.0.0/16", "required": True},
                {"name": "public_subnet_cidrs", "label": "Public subnet CIDRs (one per AZ, min 2)", "type": "json",
                 "default": ["10.30.1.0/24", "10.30.2.0/24"],
                 "help": "JSON array of at least 2 CIDRs. EKS requires subnets in different AZs."},
                {"name": "availability_zones", "label": "Availability zones (optional)", "type": "json", "default": [],
                 "help": 'JSON array like ["us-east-1a","us-east-1b"]. Leave empty to auto-pick the first N AZs.'},
                {"name": "endpoint_public_access", "label": "Public API endpoint", "type": "bool", "default": True,
                 "help": "Expose the EKS control plane publicly. Turn off for private clusters."},
                {"name": "endpoint_private_access", "label": "Private API endpoint", "type": "bool", "default": False,
                 "help": "Expose the EKS control plane inside the VPC."},
                {"name": "public_access_cidrs", "label": "Public API CIDRs", "type": "json",
                 "default": ["0.0.0.0/0"],
                 "visible_when": {"endpoint_public_access": True},
                 "help": "CIDRs allowed to reach the public EKS API. Restrict in production."},
            ],
        },
        {
            "id": "primary_pool",
            "title": "Primary node group",
            "icon": "fa-server",
            "fields": [
                {"name": "primary_instance_type", "label": "Instance type", "type": "string", "default": "t3.medium",
                 "help": "EC2 instance type (t3.medium, m5.large, c6i.xlarge, ...)."},
                {"name": "primary_disk_size_gb", "label": "Node disk size (GB)", "type": "number", "default": 50, "min": 20, "max": 2000},
                {"name": "primary_capacity_type", "label": "Capacity type", "type": "select", "default": "ON_DEMAND",
                 "options": ["ON_DEMAND", "SPOT"]},
                {"name": "primary_ami_type", "label": "AMI type", "type": "select", "default": "AL2023_x86_64_STANDARD",
                 "options": ["AL2023_x86_64_STANDARD", "AL2_x86_64", "BOTTLEROCKET_x86_64", "AL2023_ARM_64_STANDARD"]},
                {"name": "primary_enable_autoscaling", "label": "Enable autoscaling", "type": "bool", "default": True},
                {"name": "primary_desired_size", "label": "Desired size (fixed)", "type": "number", "default": 2, "min": 1, "max": 50,
                 "visible_when": {"primary_enable_autoscaling": False}},
                {"name": "primary_min_size", "label": "Min size", "type": "number", "default": 1, "min": 0, "max": 50,
                 "visible_when": {"primary_enable_autoscaling": True}},
                {"name": "primary_max_size", "label": "Max size", "type": "number", "default": 5, "min": 1, "max": 200,
                 "visible_when": {"primary_enable_autoscaling": True}},
            ],
        },
        {
            "id": "extras",
            "title": "Extra node groups (optional)",
            "icon": "fa-plus-square",
            "fields": [
                {"name": "extra_node_groups", "label": "Extra node groups (JSON map)", "type": "json",
                 "default": {},
                 "help": ('Map of group_name -> object. Keys: instance_type, disk_size_gb, capacity_type, ami_type, '
                          'desired_size, enable_autoscaling, min_size, max_size, labels. '
                          'Example: {"gpu": {"instance_type": "g4dn.xlarge", "desired_size": 1, "labels": {"gpu": "true"}}}')},
            ],
        },
    ],
}


TFVARS_ORDER = [
    "env", "project_name", "region",
    "kubernetes_version", "create_iam_roles", "existing_cluster_role_arn", "existing_node_role_arn",
    "vpc_cidr", "public_subnet_cidrs", "availability_zones",
    "endpoint_public_access", "endpoint_private_access", "public_access_cidrs",
    "primary_instance_type", "primary_disk_size_gb", "primary_capacity_type",
    "primary_ami_type", "primary_enable_autoscaling",
    "primary_desired_size", "primary_min_size", "primary_max_size",
    "extra_node_groups", "labels",
]

SECRET_KEYS = ("aws_access_key", "aws_secret_key")

# No `platform_overrides`-style map on this provider.
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
    """Synthesize a VM-like inventory from the EKS cluster + node groups.

    EKS clusters don't expose user-managed VMs directly (they live in
    autoscaling groups). We surface the cluster as one control-plane row
    and each managed node group as one row, mirroring the GKE adapter.
    """
    resources = list(_iter_state_resources(state))

    vpcs: Dict[str, Dict[str, Any]] = {}
    subnets: Dict[str, Dict[str, Any]] = {}
    for r in resources:
        t = r.get("type") or ""
        v = r.get("values") or {}
        rid = str(v.get("id")) if v.get("id") is not None else ""
        if t == "aws_vpc" and rid:
            tags = v.get("tags") or {}
            vpcs[rid] = {"id": rid, "name": tags.get("Name") if isinstance(tags, dict) else None, "cidr": v.get("cidr_block")}
        elif t == "aws_subnet" and rid:
            tags = v.get("tags") or {}
            subnets[rid] = {
                "id": rid,
                "name": tags.get("Name") if isinstance(tags, dict) else None,
                "cidr": v.get("cidr_block"),
                "gateway_ip": None,
                "vpc_id": str(v.get("vpc_id") or ""),
            }

    rows: List[Dict[str, Any]] = []
    cluster_name = None
    cluster_endpoint = None
    cluster_version = None
    for r in resources:
        if r.get("type") != "aws_eks_cluster":
            continue
        v = r.get("values") or {}
        cluster_name = v.get("name")
        cluster_endpoint = v.get("endpoint")
        cluster_version = v.get("version")
        rows.append({
            "address": r.get("address"),
            "hostname": cluster_name or "eks-cluster",
            "instance_id": cluster_name,
            "status": v.get("status") or "ACTIVE",
            "az": v.get("region"),
            "image_id": cluster_version,
            "flavor_id": "eks-control-plane",
            "private_ip": None,
            "public_ip": cluster_endpoint,
            "role": "control-plane",
            "security_groups": [],
        })

    for r in resources:
        if r.get("type") != "aws_eks_node_group":
            continue
        v = r.get("values") or {}
        scaling = (v.get("scaling_config") or [{}])[0] if isinstance(v.get("scaling_config"), list) else {}
        desired = scaling.get("desired_size") if isinstance(scaling, dict) else None
        min_s = scaling.get("min_size") if isinstance(scaling, dict) else None
        max_s = scaling.get("max_size") if isinstance(scaling, dict) else None
        instance_types = v.get("instance_types") or []
        it = instance_types[0] if isinstance(instance_types, list) and instance_types else None
        size_desc = f"{desired} nodes" if desired else (f"autoscale {min_s}-{max_s}" if min_s is not None else "autoscale")
        rows.append({
            "address": r.get("address"),
            "hostname": v.get("node_group_name") or "node-group",
            "instance_id": v.get("node_group_name"),
            "status": v.get("status") or size_desc,
            "az": None,
            "image_id": v.get("ami_type"),
            "flavor_id": it,
            "private_ip": None,
            "public_ip": None,
            "role": "node-group",
            "security_groups": [],
            "system_disk_size": v.get("disk_size"),
            "system_disk_type": v.get("capacity_type"),
        })

    rows.sort(key=lambda x: (x.get("role") != "control-plane", x.get("hostname") or ""))
    return {
        "vms": rows,
        "vpcs": list(vpcs.values()),
        "subnets": list(subnets.values()),
        "eips": [],
        "load_balancers": [],
        "count": len(rows),
        "eks_cluster": {
            "name": cluster_name,
            "endpoint": cluster_endpoint,
            "version": cluster_version,
        } if cluster_name else None,
    }


ADAPTER = ProviderAdapter(
    id="eks",
    label="Amazon EKS",
    description="Amazon Elastic Kubernetes Service — managed Kubernetes on AWS with a public VPC, IAM roles, and managed node groups.",
    logo="aws",
    schema=SCHEMA,
    tfvars_order=TFVARS_ORDER,
    secret_keys=SECRET_KEYS,
    platform_override_keys=PLATFORM_OVERRIDE_KEYS,
    build_inventory=build_inventory,
    enabled=True,
)
