# ByteDC Cambodia — Huawei Cloud Stack (HCS) provider.
# Mirrors the working reference in resource-from-byteDC-team/terra: AK/SK only,
# no token exchange, no OpenStack workaround provider.

provider "hcs" {
  cloud        = "bytedc.com"
  region       = var.region
  project_name = var.project_name
  access_key   = var.access_key
  secret_key   = var.secret_key
  auth_url     = "https://iam-apigateway-proxy.${var.region}.bytedc.com/v3"
  insecure     = true
}
