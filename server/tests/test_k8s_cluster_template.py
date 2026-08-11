"""Contract tests for the kubeadm template renderer validation boundary."""
from __future__ import annotations

from copy import deepcopy

import pytest
import yaml
from flask import Flask

from api.templates_routes import bp as templates_bp
from templates import render_template


BASE_VALUES = {
    "cluster_name": "validation-cluster",
    "kubernetes_version": "1.30.4",
    "pod_cidr": "10.244.0.0/16",
    "service_cidr": "10.96.0.0/12",
    "cni_plugin": "calico",
    "container_runtime": "containerd",
    "kube_proxy_mode": "iptables",
    "storage_provisioner": "none",
    "control_planes": [{"name": "cp-1", "ip": "10.0.0.10", "ssh_port": 22}],
    "workers": [{"name": "worker-1", "ip": "worker-1.internal", "ssh_port": 22}],
}


def render_values(**overrides):
    values = deepcopy(BASE_VALUES)
    values.update(overrides)
    return render_template("k8s-cluster", values, {})


@pytest.fixture
def templates_client():
    app = Flask(__name__)
    app.register_blueprint(templates_bp)
    return app.test_client()


def test_render_accepts_valid_custom_calico_configuration():
    result = render_values(
        kubernetes_version="v1.29.7",
        pod_cidr="10.200.0.0/16",
        service_cidr="10.100.0.0/16",
        kube_proxy_mode="ipvs",
        control_plane_endpoint="api.k8s.example:6443",
        ha_mode=True,
        control_planes=[
            {"name": "cp-1", "ip": "10.0.0.10", "ssh_port": 22},
            {"name": "cp-2", "ip": "10.0.0.11", "ssh_port": 22},
        ],
        storage_provisioner="nfs-subdir",
        nfs_server="nfs.internal.example",
        nfs_path="/srv/nfs/k8s",
    )

    assert isinstance(yaml.safe_load(result["yaml"]), list)
    assert "--kubernetes-version=v1.29.7" in result["yaml"]
    assert "--pod-network-cidr=10.200.0.0/16" in result["yaml"]
    assert "--service-cidr=10.100.0.0/16" in result["yaml"]
    assert "api.k8s.example:6443" in result["yaml"]
    assert result["sidecars"]["inventories/validation-cluster.yml"]


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"kubernetes_version": "1.30"}, "kubernetes_version"),
        ({"kubernetes_version": "1.30.4; echo unsafe"}, "kubernetes_version"),
        ({"pod_cidr": "not-a-cidr"}, "pod_cidr"),
        ({"pod_cidr": "10.244.1.1/16"}, "pod_cidr"),
        ({"pod_cidr": "fd00::/64"}, "pod_cidr"),
        ({"pod_cidr": "10.96.0.0/16", "service_cidr": "10.96.0.0/12"}, "pod_cidr"),
        ({"cni_plugin": "flannel", "pod_cidr": "10.200.0.0/16"}, "pod_cidr"),
        ({"cni_plugin": "cilium"}, "cni_plugin"),
        ({"kube_proxy_mode": "unknown"}, "kube_proxy_mode"),
        ({"kube_proxy_mode": "none"}, "kube_proxy_mode"),
        ({"container_runtime": "docker"}, "container_runtime"),
        ({"storage_provisioner": "nfs"}, "storage_provisioner"),
        ({"storage_provisioner": "nfs-subdir", "nfs_server": ""}, "nfs_server"),
        ({"storage_provisioner": "nfs-subdir", "nfs_server": "nfs.example", "nfs_path": ""}, "nfs_path"),
        ({"storage_provisioner": "nfs-subdir", "nfs_server": "nfs.example", "nfs_path": "relative/path"}, "nfs_path"),
        ({"control_plane_endpoint": "api.k8s.example"}, "control_plane_endpoint"),
        ({"control_plane_endpoint": "api.k8s.example:70000"}, "control_plane_endpoint"),
    ],
)
def test_render_rejects_invalid_scalar_configuration_with_field_errors(overrides, field):
    with pytest.raises(ValueError) as exc_info:
        render_values(**overrides)

    assert field in exc_info.value.field_errors


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"control_planes": []}, "control_planes"),
        ({"control_planes": "10.0.0.10"}, "control_planes"),
        ({"control_planes": [{"name": "cp-1", "ip": ""}]}, "control_planes[0].ip"),
        ({"control_planes": [{"name": "cp-1", "ip": "10.0.0.10", "ssh_port": 0}]}, "control_planes[0].ssh_port"),
        ({"control_planes": [{"name": "cp-1", "ip": "10.0.0.10"}], "workers": [{"name": "worker-1", "ip": "10.0.0.10"}]}, "workers[0].ip"),
        ({"control_planes": [{"name": "cp-1", "ip": "cp.internal"}], "workers": [{"name": "worker-1", "ip": "CP.INTERNAL"}]}, "workers[0].ip"),
        ({"control_planes": [{"name": "CP One", "ip": "10.0.0.10"}], "workers": [{"name": "cp-one", "ip": "10.0.0.20"}]}, "workers[0].name"),
    ],
)
def test_render_rejects_invalid_node_topology_with_field_errors(overrides, field):
    with pytest.raises(ValueError) as exc_info:
        render_values(**overrides)

    assert field in exc_info.value.field_errors


def test_render_endpoint_returns_structured_field_errors(templates_client):
    response = templates_client.post(
        "/api/templates/k8s-cluster/render",
        json={"values": {**BASE_VALUES, "kubernetes_version": "1.30"}, "targets": {}},
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "validation failed",
        "field_errors": {
            "kubernetes_version": ["must be a full version such as 1.30.4"],
        },
    }
