"""Deployment Templates Catalog.

Each template is a parameterized workload that renders to an Ansible playbook
YAML. Templates declare a JSON-schema-like set of variables that the UI turns
into a form; on submit the backend renders the play(s) using the provided
values.

Public API:
    list_templates() -> list of template summaries
    get_template(template_id) -> template detail (with variables schema)
    render_template(template_id, values, targets) -> {yaml, filename, summary}
"""
from __future__ import annotations

from typing import Any, Dict, List

from . import docker_compose as _docker_compose
from . import nginx_reverse_proxy as _nginx_rp
from . import k3s_bootstrap as _k3s
from . import k8s_cluster as _k8s_cluster
from . import k8s_manifests as _k8s_manifests
from . import system_update as _sysupd
from . import docker_engine as _dockereng
from . import manage_users as _manage_users
from . import openbao as _openbao
from . import vault_cluster as _vault_cluster
from . import opensible_worker as _opensible_worker
from . import kafka_cluster as _kafka_cluster
from . import redpanda_cluster as _redpanda_cluster
from . import redis_sentinel as _redis_sentinel
from . import postgres_patroni as _postgres_patroni
from . import rabbitmq_cluster as _rabbitmq_cluster
from . import elasticsearch_cluster as _elasticsearch_cluster
from . import kibana_cluster as _kibana_cluster
from . import logstash_cluster as _logstash_cluster
from . import uninstall_stack as _uninstall_stack
from . import cicd_pipeline as _cicd_pipeline
from . import haproxy_lb as _haproxy_lb
from . import traefik_proxy as _traefik_proxy
from . import node_exporter as _node_exporter
from . import metrics_server as _metrics_server
from . import grafana_oss as _grafana_oss
from . import generic as _generic



_REGISTRY: Dict[str, Any] = {
    m.TEMPLATE["id"]: m for m in (
        _generic,
        _docker_compose,
        _nginx_rp,
        _k3s,
        _k8s_cluster,
        _k8s_manifests,
        _sysupd,
        _dockereng,
        _manage_users,
        _openbao,
        _vault_cluster,
        _opensible_worker,
        _kafka_cluster,
        _redpanda_cluster,
        _redis_sentinel,
        _postgres_patroni,
        _rabbitmq_cluster,
        _elasticsearch_cluster,
        _kibana_cluster,
        _logstash_cluster,
        _uninstall_stack,
        _cicd_pipeline,
        _haproxy_lb,
        _traefik_proxy,
        _node_exporter,
        _metrics_server,
        _grafana_oss,
    )
}




def list_templates() -> List[Dict[str, Any]]:
    """Return catalog cards (no variable schemas)."""
    out = []
    for mod in _REGISTRY.values():
        t = mod.TEMPLATE
        out.append({
            "id": t["id"],
            "name": t["name"],
            "category": t.get("category", "Other"),
            "description": t.get("description", ""),
            "icon": t.get("icon", "package"),
            "tags": t.get("tags", []),
        })
    out.sort(key=lambda x: (x["category"], x["name"]))
    return out


def get_template(template_id: str) -> Dict[str, Any] | None:
    mod = _REGISTRY.get(template_id)
    if not mod:
        return None
    t = dict(mod.TEMPLATE)
    return t


def render_template(template_id: str, values: Dict[str, Any], targets: Dict[str, Any] | None = None) -> Dict[str, Any]:
    mod = _REGISTRY.get(template_id)
    if not mod:
        raise ValueError(f"Unknown template: {template_id}")
    targets = targets or {}
    yaml_text = mod.render(values or {}, targets)
    filename = mod.suggested_filename(values or {})
    sidecars: Dict[str, str] = {}
    fn = getattr(mod, "sidecar_files", None)
    if callable(fn):
        try:
            sidecars = fn(values or {}, targets) or {}
        except Exception:
            sidecars = {}
    return {
        "yaml": yaml_text,
        "filename": filename,
        "template_id": template_id,
        "template_name": mod.TEMPLATE["name"],
        "sidecars": sidecars,
    }
