# Cloudflare provider. Token is supplied via credentials.auto.tfvars (chmod 600)
# rendered by the OpenSible backend from the encrypted secret store.
provider "cloudflare" {
  api_token = var.api_token
}
