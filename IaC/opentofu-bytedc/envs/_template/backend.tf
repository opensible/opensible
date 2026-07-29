terraform {
  # Local backend by default — state persists inside this stack directory,
  # which lives under the mounted data volume. To switch to S3/OBS, replace
  # this block with `backend "s3" {}` and provide backend.hcl, then run
  # `tofu init -reconfigure -backend-config=backend.hcl`.
  backend "local" {
    path = "terraform.tfstate"
  }
}
