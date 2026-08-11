# Kubernetes HA (kubeadm)

Bootstraps a single or multi-control-plane Kubernetes cluster with `kubeadm`
and containerd. The OpenSible `k8s-cluster` renderer supports Calico, Flannel,
or manual CNI installation, plus optional metrics-server and storage add-ons.

## OpenSible renderer

Configure and render this blueprint from the OpenSible console. The renderer
validates configuration before it generates or saves a playbook:

- `kubernetes_version` must be a full patch version such as `1.30.4` (an
  optional leading `v` is accepted).
- Pod and service networks must be strict, non-overlapping IPv4 CIDRs.
- Flannel currently requires `pod_cidr: 10.244.0.0/16`, matching its upstream
  manifest. Calico accepts another valid IPv4 pod CIDR; `none` leaves CNI
  installation to the operator.
- `kube_proxy_mode: none` is only accepted with `cni_plugin: none`.
- Supported storage values are `none`, `longhorn`, `local-path`,
  `openebs-hostpath`, and `nfs-subdir`. NFS requires a server and absolute
  export path.
- At least one control-plane node is required. Node addresses and generated
  Kubernetes node names must be unique, and SSH ports must be valid.
- An optional control-plane endpoint must use `host:port` syntax.

The current contract is IPv4 single-stack. Cilium, dual-stack/IPv6, and other
container runtimes are not implemented by this renderer.

## Static example playbook

`playbook.yml` is a fixed example generated from the renderer defaults. Its
cluster hosts, version, networks, CNI, and add-ons are already embedded; it is
not a variable-driven entrypoint and does not consume `vars.example.yml`.

To use custom values, render a new playbook in OpenSible. The static example
can be run as-is after adapting its embedded hosts:

```bash
ansible-playbook -i inventory.yml IaC/blueprints/kubernetes/kubernetes-ha-kubeadm/playbook.yml
```

## Highlights

- **HA-aware joins**: control-plane joins failover across healthy endpoints
  and self-heal via `kubeadm reset` on transient errors.
- **Clean reinstall**: `reset_existing_cluster: true` purges kubeadm/k3s state,
  frees port 6443, and flushes IPVS/iptables/CNI leftovers.
- **Storage**: pick `local-path`, `longhorn`, `openebs-hostpath`,
  `nfs-subdir`, or `none`. The chosen class is marked default when
  `storage_default_class: true`.
- **Kubeconfig**: fetched to the runner as `~/.kube/<cluster_name>.yaml`.

## Notes

Tested on Ubuntu 22.04 / Debian 12 with containerd. Restricted environments
(LXC, OrbStack) are auto-detected and skip kernel-module tasks.
