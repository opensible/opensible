# Cluster auth is chosen by `auth_method`:
#   - "kubeconfig": the backend materialises the pasted YAML to
#     <stack>/kubeconfig.yaml (chmod 600) at run time. `config_path`
#     picks it up.
#   - "token": use explicit endpoint / CA / bearer token variables.
locals {
  use_kubeconfig = var.auth_method == "kubeconfig"
  ca_pem         = (!local.use_kubeconfig && var.cluster_ca_cert != "") ? base64decode(var.cluster_ca_cert) : null
}

provider "kubernetes" {
  config_path            = local.use_kubeconfig ? var.kubeconfig_path : null
  host                   = local.use_kubeconfig ? null : var.cluster_endpoint
  cluster_ca_certificate = local.use_kubeconfig ? null : local.ca_pem
  token                  = local.use_kubeconfig ? null : var.cluster_token
}

provider "helm" {
  kubernetes {
    config_path            = local.use_kubeconfig ? var.kubeconfig_path : null
    host                   = local.use_kubeconfig ? null : var.cluster_endpoint
    cluster_ca_certificate = local.use_kubeconfig ? null : local.ca_pem
    token                  = local.use_kubeconfig ? null : var.cluster_token
  }
}
