# Hetzner Cloud OpenTofu template

Managed by the OpenSible **Cloud Provisioning** wizard. This template is
copied into each stack directory under `envs/<stack>/`.

## Layout
- `variables.tf` — input variables (rendered from the wizard as `terraform.tfvars`).
- `providers.tf` — `hetznercloud/hcloud` provider bootstrap.
- `versions.tf` — Terraform / provider version pins.
- `main.tf` — network, firewall, SSH key, server pools, optional load balancer.
- `backend.tf` — local backend (edit + `backend.hcl` for remote state).
- `credentials.auto.tfvars.example` — sample for the encrypted API token.

## Usage (from within the stack directory)
```bash
tofu init
tofu plan
tofu apply
```

The OpenSible UI runs these commands via the worker pool. Secrets are
materialised as `credentials.auto.tfvars` (chmod 600) only during a run.
