# Argo CD via Helm

Installs [Argo CD](https://argo-cd.readthedocs.io/) onto an existing Kubernetes
cluster (k3s, kubeadm, EKS, GKE, AKS — anything with a valid kubeconfig on the
Ansible target host) using the official `argo/argo-cd` Helm chart.

## Prerequisites

- A running Kubernetes cluster reachable from the target host.
- `kubectl` and `helm` installed on the target host (use the
  `Kubernetes Manifests / Helm / Tooling` template with action
  `install-tools` if you need them).
- A readable `KUBECONFIG` on the target host — defaults to
  `/etc/rancher/k3s/k3s.yaml`, change to `/root/.kube/config` for kubeadm.

## Usage

```bash
ansible-playbook -i inventory.yml \
  -e @vars.example.yml \
  IaC/blueprints/kubernetes/argocd-helm/playbook.yml
```

## First login

```bash
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d && echo
kubectl -n argocd port-forward svc/argocd-server 8080:443
```

Then browse to <https://localhost:8080> and log in as `admin`.

## Highlights

- Adds the `argo` Helm repo and runs `helm upgrade --install` (idempotent).
- Creates the `argocd` namespace automatically.
- Waits for the rollout to finish so subsequent `kubectl` calls succeed.
- Fully overridable inline `values:` for ingress, SSO, HA replica counts, etc.
