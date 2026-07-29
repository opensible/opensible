# AWS OpenTofu template (EC2 + VPC + Security Group)

Managed by the OpenSible **Cloud Provisioning** wizard. Copied into each stack
directory under `envs/<stack>/`.

## Layout
- `variables.tf` — input variables (rendered from the wizard as `terraform.tfvars`).
- `providers.tf` — `hashicorp/aws` provider bootstrap.
- `versions.tf` — Terraform / provider version pins.
- `main.tf` — VPC, subnet, internet gateway, route table, security group,
  key pair, EC2 instances (app + platform + extras).
- `backend.tf` — local backend (edit + `backend.hcl` for remote state).
- `credentials.auto.tfvars.example` — sample for the encrypted API keys.

## Usage
```bash
tofu init
tofu plan
tofu apply
```
The OpenSible UI runs these commands via the worker pool. Secrets are
materialised as `credentials.auto.tfvars` (chmod 600) only during a run.

## Scope (first iteration)
EC2 + VPC + Security Group + Internet Gateway. RDS, ELB, S3, IAM roles will
land in subsequent iterations without breaking this template.
