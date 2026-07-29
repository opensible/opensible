terraform {
  required_version = ">= 1.3.0"
  required_providers {
    hcs = {
      source  = "huaweicloud/hcs"
      version = "~> 2.4.0"
    }
  }
}
