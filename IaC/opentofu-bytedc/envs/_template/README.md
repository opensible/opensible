# ByteDC OpenTofu template (HCS: VPC + ECS + ELB + NAT + DNS)

Managed by the OpenSible **Cloud Provisioning** wizard. Copied into each stack
directory under `envs/<stack>/`.

## Layout
- `variables.tf` — input variables (rendered from the wizard as `terraform.tfvars`).
- `providers.tf` — `huaweicloud/hcs` provider bootstrap.
- `versions.tf` — Terraform / provider version pins.
- `main.tf` — thin wrapper that calls `modules/stack` (VPC, subnets, ECS
  instances, EIPs, ELB, NAT, DNS).
- `backend.tf` — local backend (edit + `backend.hcl` to switch to remote OBS state).
- `credentials.auto.tfvars.example` — sample for the encrypted API keys.

## Usage
```bash
tofu init
tofu plan
tofu apply
```
The OpenSible UI runs these commands via the worker pool. Secrets are
materialised as `credentials.auto.tfvars` (chmod 600) only during a run.
