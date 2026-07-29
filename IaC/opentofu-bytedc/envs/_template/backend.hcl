bucket    = "REPLACE_ME_OBS_BUCKET"
key       = "opensible/terraform.tfstate"
region    = "REPLACE_ME_REGION"
endpoints = { s3 = "https://obsv3.REPLACE_ME_REGION.bytedc.com" }
encrypt   = false
use_path_style              = true
skip_credentials_validation = true
skip_metadata_api_check     = true
skip_region_validation      = true
skip_requesting_account_id  = true
