# AWS provider. Credentials are supplied via credentials.auto.tfvars (chmod 600)
# rendered by the OpenSible backend from the encrypted secret store.
provider "aws" {
  region     = var.region
  access_key = var.aws_access_key
  secret_key = var.aws_secret_key
}
