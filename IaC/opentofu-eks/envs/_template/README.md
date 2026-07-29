# EKS OpenTofu template (Amazon Elastic Kubernetes Service)

Managed by the OpenSible **Cloud Provisioning** wizard. Copied into each stack
directory under `envs/<stack>/`.

## Layout
- `variables.tf` — input variables (rendered from the wizard as `terraform.tfvars`).
- `providers.tf` — `hashicorp/aws` provider bootstrap.
- `versions.tf` — Terraform / provider version pins.
- `main.tf` — VPC (2+ public subnets across AZs), IGW, IAM roles for cluster
  and nodes, EKS control plane, and managed node groups.
- `backend.tf` — local backend (edit + `backend.hcl` for S3 remote state).
- `credentials.auto.tfvars.example` — sample for the encrypted API keys.

## Scope
EKS control plane + managed node groups on a public VPC. Extendable to
private subnets + NAT, IRSA, Fargate profiles, and AWS load balancer
controller in future iterations.

## Usage
```bash
tofu init
tofu plan
tofu apply
aws eks update-kubeconfig --region <region> --name <cluster_name>
```
