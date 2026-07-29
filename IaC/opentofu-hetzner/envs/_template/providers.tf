# Hetzner Cloud provider. Token is provided via credentials.auto.tfvars (chmod 600)
# rendered by the OpenSible backend from the encrypted secret store.
provider "hcloud" {
  token = var.hcloud_token
}
