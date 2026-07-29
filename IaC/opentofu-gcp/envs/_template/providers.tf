# Google Cloud provider. Credentials are supplied via credentials.auto.tfvars
# (chmod 600) rendered by the OpenSible backend from the encrypted secret store.
provider "google" {
  project     = var.gcp_project_id
  region      = var.region
  zone        = var.zone
  credentials = var.gcp_credentials_json
}
