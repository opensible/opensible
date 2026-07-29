# Uninstall & Cleanup Blueprints

First-class Resource Catalog entries that cleanly remove previously-installed
stacks from your hosts. All map to the `uninstall-stack` backend template.

| Blueprint | Stack value | Removes |
|-----------|-------------|---------|
| `uninstall-kubeadm` | `kubeadm` | kubeadm cluster + kube packages + CNI + iptables |
| `uninstall-k3s`     | `k3s`     | k3s server & agent + `/var/lib/rancher` + kubeconfigs |
| `uninstall-docker`  | `docker`  | docker-ce + containerd + `/var/lib/docker` |
| `uninstall-kafka`   | `kafka`   | `kafka.service` + `/opt/kafka` + `/var/lib/kafka` |
| `uninstall-redis`   | `redis`   | redis-server + redis-sentinel + `/etc/redis` + data |
| `uninstall-valkey`  | `valkey`  | valkey-server + valkey-sentinel + `/etc/valkey` + data |
| `uninstall-argocd`  | `argocd`  | Helm release + `argoproj.io` CRDs (opt. namespace) |

All routines are idempotent and safe to re-run.
