"""Kubernetes provider adapter — Mode A "Bring your own cluster".

The user provides an existing cluster (kubeconfig OR endpoint+CA+token) and
OpenSible provisions Kubernetes objects into it: a namespace, one or more
workloads (Deployment + Service), an optional Ingress, and optional Helm
addons (ingress-nginx, cert-manager, metrics-server).

Since there are no VMs, `build_inventory` synthesizes a VM-like inventory from
the state's `kubernetes_service` entries (LoadBalancer IPs preferred, ClusterIP
fallback) so Ansible playbooks can still target the managed hostnames.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .base import ProviderAdapter


SCHEMA: Dict[str, Any] = {
    "provider": "kubernetes",
    "groups": [
        {
            "id": "project",
            "title": "Project",
            "icon": "fa-folder",
            "fields": [
                {"name": "env", "label": "Environment", "type": "string", "default": "dev", "required": True,
                 "help": "Short env tag — dev / sit / prod."},
                {"name": "project_name", "label": "Project name (naming prefix)", "type": "string", "required": True,
                 "help": "Used as label prefix on every Kubernetes object OpenSible creates."},
            ],
        },
        {
            "id": "cluster",
            "title": "Cluster Auth",
            "icon": "fa-key",
            "secret": True,
            "fields": [
                {"name": "auth_method", "label": "Cluster auth method", "type": "select", "default": "kubeconfig",
                 "options": ["kubeconfig", "token"],
                 "help": "'kubeconfig' pastes the full YAML (recommended, works with EKS/GKE/AKS/k3s). "
                         "'token' uses cluster endpoint + CA cert + bearer token instead."},
                {"name": "kubeconfig", "label": "Kubeconfig YAML", "type": "secret", "multiline": True,
                 "visible_when": {"auth_method": "kubeconfig"},
                 "help": "Paste the full kubeconfig file (~/.kube/config). Stored encrypted, "
                         "written to <stack>/kubeconfig.yaml (chmod 600) only at execution time."},
                {"name": "cluster_endpoint", "label": "API server URL", "type": "string", "default": "",
                 "visible_when": {"auth_method": "token"},
                 "help": "https://<cluster-endpoint>:6443"},
                {"name": "cluster_ca_cert", "label": "Cluster CA certificate (base64)", "type": "secret", "multiline": True,
                 "visible_when": {"auth_method": "token"},
                 "help": "Base64-encoded CA cert (the value from `kubectl config view --raw` under `certificate-authority-data`)."},
                {"name": "cluster_token", "label": "Service-account bearer token", "type": "secret",
                 "visible_when": {"auth_method": "token"},
                 "help": "Bearer token for a ServiceAccount with sufficient RBAC (cluster-admin for the sample workloads)."},
            ],
        },
        {
            "id": "namespace",
            "title": "Namespace",
            "icon": "fa-layer-group",
            "fields": [
                {"name": "namespace_name", "label": "Namespace name", "type": "string", "required": True,
                 "default": "opensible", "help": "Kubernetes namespace to deploy into."},
                {"name": "create_namespace", "label": "Create namespace if missing", "type": "bool", "default": True,
                 "help": "OFF: assume it exists (data lookup). ON: OpenSible creates it."},
            ],
        },
        {
            "id": "workloads",
            "title": "Workloads",
            "icon": "fa-boxes-stacked",
            "fields": [
                {"name": "workloads", "label": "Workloads (JSON array)", "type": "json",
                 "default": [
                     {"name": "hello", "image": "nginxdemos/hello:latest", "replicas": 2,
                      "port": 80, "env": {}},
                 ],
                 "help": ('Each workload: {"name","image","replicas","port","env":{K:V}}. '
                          'Creates one Deployment + ClusterIP Service per entry.')},
            ],
        },
        {
            "id": "ingress",
            "title": "Ingress",
            "icon": "fa-network-wired",
            "fields": [
                {"name": "enable_ingress", "label": "Enable Ingress", "type": "bool", "default": False,
                 "help": "Creates a single Ingress in front of one of the workloads' services."},
                {"name": "ingress_class", "label": "Ingress class", "type": "string", "default": "nginx",
                 "visible_when": {"enable_ingress": True},
                 "help": "IngressClass name (e.g. 'nginx', 'traefik'). Requires a controller installed on the cluster."},
                {"name": "ingress_host", "label": "Ingress host", "type": "string", "default": "",
                 "visible_when": {"enable_ingress": True},
                 "help": "FQDN, e.g. app.example.com. Must resolve to your ingress controller's external IP."},
                {"name": "ingress_target_workload", "label": "Target workload name", "type": "string", "default": "hello",
                 "visible_when": {"enable_ingress": True},
                 "help": "Name of the workload above whose Service should receive the ingress traffic."},
                {"name": "ingress_target_port", "label": "Target service port", "type": "number", "default": 80,
                 "visible_when": {"enable_ingress": True},
                 "help": "Port exposed by the workload's Service."},
                {"name": "ingress_tls", "label": "Enable TLS (uses cert-manager 'letsencrypt-prod' issuer)", "type": "bool", "default": False,
                 "visible_when": {"enable_ingress": True},
                 "help": "Adds cert-manager annotations so certs are issued automatically. Install cert-manager below if not already present."},
            ],
        },
        {
            "id": "addons",
            "title": "Helm Addons (optional)",
            "icon": "fa-cog",
            "fields": [
                {"name": "install_ingress_nginx", "label": "Install ingress-nginx", "type": "bool", "default": False,
                 "help": "Deploys the ingress-nginx controller (helm chart, namespace 'ingress-nginx')."},
                {"name": "install_cert_manager", "label": "Install cert-manager", "type": "bool", "default": False,
                 "help": "Deploys cert-manager into the 'cert-manager' namespace (CRDs included)."},
                {"name": "install_metrics_server", "label": "Install metrics-server", "type": "bool", "default": False,
                 "help": "Deploys metrics-server into 'kube-system' (needed for HPA / `kubectl top`)."},
            ],
        },
    ],
}


TFVARS_ORDER = [
    "env", "project_name",
    "auth_method", "cluster_endpoint",
    "namespace_name", "create_namespace",
    "workloads",
    "enable_ingress", "ingress_class", "ingress_host",
    "ingress_target_workload", "ingress_target_port", "ingress_tls",
    "install_ingress_nginx", "install_cert_manager", "install_metrics_server",
    "labels",
]

SECRET_KEYS = ("kubeconfig", "cluster_ca_cert", "cluster_token")

# Kubernetes has no VM pools, but the base adapter expects the set.
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


def _svc_public_ip(v: Dict[str, Any]) -> str | None:
    """Best-effort extract LoadBalancer external IP/hostname from a service."""
    try:
        status = (v.get("status") or [])
        if not status:
            return None
        lb = ((status[0] or {}).get("load_balancer") or [])
        if not lb:
            return None
        ingress = ((lb[0] or {}).get("ingress") or [])
        if not ingress:
            return None
        first = ingress[0] or {}
        return first.get("ip") or first.get("hostname")
    except Exception:
        return None


def build_inventory(state: Dict[str, Any]) -> Dict[str, Any]:
    """Synthesize a VM-like inventory from kubernetes_service state entries."""
    namespaces: Dict[str, str] = {}
    instances: List[Dict[str, Any]] = []
    helm_releases: List[Dict[str, Any]] = []
    ingresses: List[Dict[str, Any]] = []

    for r in _iter_state_resources(state):
        t = r.get("type") or ""
        v = r.get("values") or {}
        if t == "kubernetes_namespace" or t == "kubernetes_namespace_v1":
            meta = (v.get("metadata") or [{}])[0] if isinstance(v.get("metadata"), list) else (v.get("metadata") or {})
            nid = meta.get("name") or v.get("id")
            if nid:
                namespaces[str(nid)] = meta.get("name") or str(nid)
        elif t == "helm_release":
            helm_releases.append({
                "name": v.get("name"),
                "chart": v.get("chart"),
                "namespace": v.get("namespace"),
                "status": v.get("status"),
                "version": v.get("version"),
            })
        elif t in ("kubernetes_ingress_v1", "kubernetes_ingress"):
            meta = (v.get("metadata") or [{}])[0] if isinstance(v.get("metadata"), list) else (v.get("metadata") or {})
            spec = (v.get("spec") or [{}])[0] if isinstance(v.get("spec"), list) else (v.get("spec") or {})
            rules = spec.get("rule") or spec.get("rules") or []
            hosts = []
            for rr in rules:
                h = rr.get("host") if isinstance(rr, dict) else None
                if h: hosts.append(h)
            ingresses.append({
                "name": meta.get("name"),
                "namespace": meta.get("namespace"),
                "hosts": hosts,
                "class": meta.get("annotations", {}).get("kubernetes.io/ingress.class") if isinstance(meta.get("annotations"), dict) else None,
            })

    for r in _iter_state_resources(state):
        t = r.get("type") or ""
        if t not in ("kubernetes_service", "kubernetes_service_v1"):
            continue
        v = r.get("values") or {}
        meta = (v.get("metadata") or [{}])[0] if isinstance(v.get("metadata"), list) else (v.get("metadata") or {})
        spec = (v.get("spec") or [{}])[0] if isinstance(v.get("spec"), list) else (v.get("spec") or {})
        ports = spec.get("port") or spec.get("ports") or []
        first_port = (ports[0] if ports else {}) or {}
        svc_type = spec.get("type") or "ClusterIP"
        cluster_ip = spec.get("cluster_ip") or spec.get("cluster_i_p")
        public_ip = _svc_public_ip(v) if svc_type == "LoadBalancer" else None
        ns = meta.get("namespace") or "default"
        name = meta.get("name") or ""
        hostname = f"{name}.{ns}.svc.cluster.local" if name else str(v.get("id") or "")
        instances.append({
            "address": r.get("address"),
            "hostname": hostname,
            "instance_id": str(v.get("id") or "") or None,
            "status": "active",
            "az": None,
            "image_id": None,
            "flavor_id": svc_type,
            "private_ip": cluster_ip,
            "mac": None,
            "port_id": (first_port.get("port") if isinstance(first_port, dict) else None),
            "public_ip": public_ip,
            "subnet_id": None,
            "subnet_name": ns,
            "subnet_cidr": None,
            "subnet_gateway": None,
            "vpc_id": ns,
            "vpc_name": ns,
            "vpc_cidr": None,
            "security_groups": [],
            "system_disk_type": None,
            "system_disk_size": None,
            "role": (meta.get("labels", {}) or {}).get("app.kubernetes.io/name") if isinstance(meta.get("labels"), dict) else None,
        })

    instances.sort(key=lambda x: (x.get("hostname") or ""))
    return {
        "vms": instances,
        "vpcs": [{"id": nid, "name": name, "cidr": None} for nid, name in namespaces.items()],
        "subnets": [],
        "eips": [{"id": vm.get("instance_id"), "address": vm["public_ip"]}
                 for vm in instances if vm.get("public_ip")],
        "load_balancers": [{"hostname": vm["hostname"], "address": vm["public_ip"]}
                           for vm in instances if vm.get("public_ip")],
        "helm_releases": helm_releases,
        "ingresses": ingresses,
        "count": len(instances),
    }


ADAPTER = ProviderAdapter(
    id="kubernetes",
    label="Kubernetes",
    description="Kubernetes — deploy namespaces, workloads, services, ingresses and Helm addons into an existing cluster (bring your own kubeconfig).",
    logo="kubernetes",
    schema=SCHEMA,
    tfvars_order=TFVARS_ORDER,
    secret_keys=SECRET_KEYS,
    platform_override_keys=PLATFORM_OVERRIDE_KEYS,
    build_inventory=build_inventory,
    enabled=True,
    category="onprem",
)
