# Kubernetes HA (kubeadm)

Bootstraps a production-ready Kubernetes cluster with `kubeadm`. Supports
single or multi control-plane, Calico/Flannel/Cilium CNI, and optional
`metrics-server`, Longhorn, NFS, or local-path storage.

## Usage

```bash
ansible-playbook -i inventory.yml \
  -e @vars.example.yml \
  IaC/blueprints/kubernetes/kubernetes-ha-kubeadm/playbook.yml
```

## Highlights

- **HA-aware joins**: control-plane joins failover across healthy endpoints
  and self-heal via `kubeadm reset` on transient errors.
- **Clean reinstall**: `reset_existing_cluster: true` purges kubeadm/k3s state,
  frees port 6443, and flushes IPVS/iptables/CNI leftovers.
- **Storage**: pick `local-path`, `longhorn`, `nfs`, or `none`. The chosen
  class is marked default when `storage_default_class: true`.
- **Kubeconfig**: fetched to the runner as `~/.kube/<cluster_name>.yaml`.

## Notes

Tested on Ubuntu 22.04 / Debian 12 with containerd. Restricted environments
(LXC, OrbStack) are auto-detected and skip kernel-module tasks.
