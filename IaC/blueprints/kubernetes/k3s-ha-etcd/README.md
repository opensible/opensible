# k3s HA (embedded etcd)

Bootstraps a lightweight k3s cluster with embedded etcd. Ideal for edge, small
teams, or homelab environments. Optional Longhorn distributed storage.

## Usage

```bash
ansible-playbook -i inventory.yml \
  -e @vars.example.yml \
  IaC/blueprints/kubernetes/k3s-ha-etcd/playbook.yml
```

## Highlights

- **HA-ready**: first server bootstraps with `--cluster-init`; extra servers
  join via `--server https://<endpoint>:6443`.
- **Clean reinstall**: purges existing k3s/kubeadm state and frees port 6443
  before install to avoid conflicts with prior clusters.
- **Restricted env detection**: LXC/OrbStack skip kernel-module tweaks.
- **Longhorn** installs post-join when `install_longhorn: true`.

## Notes

Tested on Ubuntu 22.04 / Debian 12. Traefik is disabled by default so you can
bring your own ingress controller.
