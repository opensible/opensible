terraform {
  required_version = ">= 1.3.0"
  required_providers {
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = "~> 1.53"
    }
  }
}
