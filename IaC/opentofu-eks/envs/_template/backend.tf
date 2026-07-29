terraform {
  # Local backend by default — state persists inside this stack directory
  # under the mounted data volume. Switch to remote state (S3) by replacing
  # this block and providing backend.hcl.
  backend "local" {
    path = "terraform.tfstate"
  }
}
