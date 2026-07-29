"""Google Kubernetes Engine (GKE) provider adapter.

Provisions a managed Kubernetes cluster on Google Cloud: VPC-native network,
regional or zonal cluster (no default node pool), primary node pool with
optional autoscaling, and any number of extra node pools via a map.

Reuses GCP-style credentials (service account JSON) and shares the display
logo with the plain GCP provider. Kept as a separate adapter so the schema
and OpenTofu template can evolve independently from Compute Engine.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .base import ProviderAdapter


SCHEMA: Dict[str, Any] = {
    "provider": "gke",
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
                {"name": "gcp_project_id", "label": "GCP Project ID", "type": "string", "required": True,
                 "help": "The Google Cloud project id (e.g. 'my-org-prod-1234')."},
                {"name": "node_service_account_email", "label": "GKE node service account email", "type": "string", "default": "",
                 "help": "Optional. Empty = use the client_email from the JSON key for GKE nodes. Grant IAM roles to that service account email, not only to your human Google user."},
                {"name": "manage_node_service_account_act_as_binding", "label": "Auto-grant node service account access", "type": "bool", "default": True,
                 "help": "OpenSible will add Service Account User on the selected node service account. The JSON key service account needs Service Account Admin, because OpenTofu runs as the JSON key identity, not your Cloud Shell user."},
                {"name": "region", "label": "GCP Region", "type": "string", "default": "us-central1", "required": True,
                 "help": "Region for the VPC and (for regional clusters) the control plane."},
                {"name": "zone", "label": "GCP Zone (zonal clusters only)", "type": "string", "default": "",
                 "visible_when": {"cluster_type": "zonal"},
                 "help": "Zone for a zonal cluster (e.g. us-central1-a). Ignored for regional clusters."},
                {"name": "cluster_type", "label": "Cluster type", "type": "select", "default": "regional",
                 "options": ["regional", "zonal"],
                 "help": "Regional clusters span 3 zones (HA control plane). Zonal clusters are cheaper but single-zone."},
            ],
        },
        {
            "id": "credentials",
            "title": "Credentials",
            "icon": "fa-key",
            "secret": True,
            "fields": [
                {"name": "gcp_credentials_json", "label": "Service Account JSON key", "type": "secret", "required": True,
                 "help": "Paste the full JSON key. Its client_email is the identity OpenSible uses, so grant roles/container.admin, roles/compute.networkAdmin, and if auto-grant is enabled roles/iam.serviceAccountAdmin to this service account email."},
            ],
        },
        {
            "id": "cluster",
            "title": "Cluster",
            "icon": "fa-layer-group",
            "fields": [
                {"name": "kubernetes_version", "label": "Kubernetes version", "type": "string", "default": "",
                 "help": "Empty = GKE picks the default for the selected release channel (recommended)."},
                {"name": "release_channel", "label": "Release channel", "type": "select", "default": "REGULAR",
                 "options": ["RAPID", "REGULAR", "STABLE", "UNSPECIFIED"],
                 "help": "STABLE lags behind but is safest. REGULAR is the GKE default."},
            ],
        },
        {
            "id": "network",
            "title": "Networking",
            "icon": "fa-network-wired",
            "fields": [
                {"name": "subnet_cidr", "label": "Subnet CIDR (nodes)", "type": "cidr", "default": "10.20.0.0/22", "required": True,
                 "help": "Primary CIDR for node IPs."},
                {"name": "pods_cidr", "label": "Pods secondary CIDR", "type": "cidr", "default": "10.24.0.0/14", "required": True,
                 "help": "Secondary range for Pod IPs (VPC-native / alias IP). Must not overlap the node subnet."},
                {"name": "services_cidr", "label": "Services secondary CIDR", "type": "cidr", "default": "10.28.0.0/20", "required": True,
                 "help": "Secondary range for ClusterIP Services."},
                {"name": "master_authorized_cidrs", "label": "Master authorized networks", "type": "json",
                 "default": ["0.0.0.0/0"],
                 "help": "JSON array of CIDRs allowed to reach the GKE control plane. Restrict in production."},
                {"name": "enable_private_nodes", "label": "Private nodes (Cloud NAT for egress)", "type": "bool", "default": False,
                 "help": "When ON, nodes have no public IP and a Cloud NAT is created for outbound traffic."},
            ],
        },
        {
            "id": "primary_pool",
            "title": "Primary node pool",
            "icon": "fa-server",
            "fields": [
                {"name": "primary_machine_type", "label": "Machine type", "type": "string", "default": "e2-standard-2",
                 "help": "e2-standard-2, n2-standard-4, c3-standard-4, ..."},
                {"name": "primary_disk_size_gb", "label": "Boot disk size (GB)", "type": "number", "default": 50, "min": 10, "max": 2000},
                {"name": "primary_disk_type", "label": "Disk type", "type": "select", "default": "pd-balanced",
                 "options": ["pd-standard", "pd-balanced", "pd-ssd"]},
                {"name": "primary_enable_autoscaling", "label": "Enable autoscaling", "type": "bool", "default": True},
                {"name": "primary_node_count", "label": "Node count per location (fixed size)", "type": "number", "default": 2, "min": 1, "max": 50,
                 "visible_when": {"primary_enable_autoscaling": False},
                 "help": "Regional clusters multiply this by 3 (one per zone)."},
                {"name": "primary_min_nodes", "label": "Min nodes (autoscale)", "type": "number", "default": 1, "min": 0, "max": 50,
                 "visible_when": {"primary_enable_autoscaling": True}},
                {"name": "primary_max_nodes", "label": "Max nodes (autoscale)", "type": "number", "default": 5, "min": 1, "max": 200,
                 "visible_when": {"primary_enable_autoscaling": True}},
                {"name": "primary_preemptible", "label": "Preemptible / Spot nodes", "type": "bool", "default": False,
                 "help": "Cheaper but Google may reclaim the VM at any time. Not for stateful workloads."},
            ],
        },
        {
            "id": "extras",
            "title": "Extra node pools (optional)",
            "icon": "fa-plus-square",
            "fields": [
                {"name": "extra_node_pools", "label": "Extra node pools (JSON map)", "type": "json",
                 "default": {},
                 "help": ('Map of pool_name -> object. Keys: machine_type, disk_size_gb, disk_type, node_count, '
                          'enable_autoscaling, min_nodes, max_nodes, preemptible, labels. '
                          'Example: {"gpu": {"machine_type": "n1-standard-4", "node_count": 1, "labels": {"gpu": "true"}}}')},
            ],
        },
    ],
}


TFVARS_ORDER = [
    "env", "project_name", "gcp_project_id", "node_service_account_email", "manage_node_service_account_act_as_binding", "region", "zone", "cluster_type",
    "kubernetes_version", "release_channel",
    "subnet_cidr", "pods_cidr", "services_cidr",
    "master_authorized_cidrs", "enable_private_nodes",
    "primary_machine_type", "primary_disk_size_gb", "primary_disk_type",
    "primary_node_count", "primary_enable_autoscaling",
    "primary_min_nodes", "primary_max_nodes", "primary_preemptible",
    "extra_node_pools", "labels",
]

SECRET_KEYS = ("gcp_credentials_json",)

# No `platform_overrides`-style map on this provider — keep set empty so the
# sanitizer strips any legacy field left over from a previous edit under
# another provider schema.
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
    """Synthesize a VM-like inventory from the GKE cluster + node pools.

    We surface the cluster as a single "vm" record (so it shows up on the
    stack detail page) plus one row per node pool describing size / machine
    type. This mirrors the Kubernetes provider approach — GKE clusters have
    no user-managed VMs to enumerate directly.
    """
    resources = list(_iter_state_resources(state))

    networks: Dict[str, Dict[str, Any]] = {}
    subnets: Dict[str, Dict[str, Any]] = {}
    for r in resources:
        t = r.get("type") or ""
        v = r.get("values") or {}
        rid = str(v.get("id")) if v.get("id") is not None else ""
        if t == "google_compute_network" and rid:
            networks[rid] = {"id": rid, "name": v.get("name"), "cidr": None}
        elif t == "google_compute_subnetwork" and rid:
            subnets[rid] = {
                "id": rid, "name": v.get("name"),
                "cidr": v.get("ip_cidr_range"),
                "gateway_ip": v.get("gateway_address"),
                "network_id": str(v.get("network") or ""),
            }

    rows: List[Dict[str, Any]] = []
    cluster_endpoint = None
    cluster_location = None
    cluster_name = None
    for r in resources:
        if r.get("type") != "google_container_cluster":
            continue
        v = r.get("values") or {}
        cluster_name = v.get("name")
        cluster_endpoint = v.get("endpoint")
        cluster_location = v.get("location")
        rows.append({
            "address": r.get("address"),
            "hostname": cluster_name or "gke-cluster",
            "instance_id": cluster_name,
            "status": "RUNNING",
            "az": cluster_location,
            "image_id": v.get("min_master_version") or v.get("master_version"),
            "flavor_id": "gke-control-plane",
            "private_ip": None,
            "public_ip": cluster_endpoint,
            "role": "control-plane",
            "security_groups": [],
        })

    for r in resources:
        if r.get("type") != "google_container_node_pool":
            continue
        v = r.get("values") or {}
        node_cfg = (v.get("node_config") or [{}])[0] if isinstance(v.get("node_config"), list) else {}
        autoscaling = (v.get("autoscaling") or [{}])[0] if isinstance(v.get("autoscaling"), list) else {}
        node_count = v.get("node_count")
        min_n = autoscaling.get("min_node_count") if isinstance(autoscaling, dict) else None
        max_n = autoscaling.get("max_node_count") if isinstance(autoscaling, dict) else None
        size_desc = f"{node_count} nodes" if node_count else (f"autoscale {min_n}-{max_n}" if min_n is not None else "autoscale")
        rows.append({
            "address": r.get("address"),
            "hostname": v.get("name") or "node-pool",
            "instance_id": v.get("name"),
            "status": size_desc,
            "az": v.get("location") or cluster_location,
            "image_id": None,
            "flavor_id": node_cfg.get("machine_type") if isinstance(node_cfg, dict) else None,
            "private_ip": None,
            "public_ip": None,
            "role": "node-pool",
            "security_groups": [],
            "system_disk_size": node_cfg.get("disk_size_gb") if isinstance(node_cfg, dict) else None,
            "system_disk_type": node_cfg.get("disk_type") if isinstance(node_cfg, dict) else None,
        })

    rows.sort(key=lambda x: (x.get("role") != "control-plane", x.get("hostname") or ""))
    return {
        "vms": rows,
        "vpcs": list(networks.values()),
        "subnets": list(subnets.values()),
        "eips": [],
        "load_balancers": [],
        "count": len(rows),
        "gke_cluster": {
            "name": cluster_name,
            "endpoint": cluster_endpoint,
            "location": cluster_location,
        } if cluster_name else None,
    }


ADAPTER = ProviderAdapter(
    id="gke",
    label="Google Kubernetes Engine",
    description="Google Kubernetes Engine (GKE) — managed Kubernetes on Google Cloud with VPC-native networking and node pools.",
    logo="gcp",
    schema=SCHEMA,
    tfvars_order=TFVARS_ORDER,
    secret_keys=SECRET_KEYS,
    platform_override_keys=PLATFORM_OVERRIDE_KEYS,
    build_inventory=build_inventory,
    enabled=True,
)
