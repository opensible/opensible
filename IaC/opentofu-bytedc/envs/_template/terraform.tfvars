# Sample values. The wizard overwrites this file on every save.
env          = "dev"
project_name = ""            # ByteDC resource space name
region       = ""            # e.g. cn-north-1

vpc_cidr           = "10.0.0.0/16"
public_subnet_cidr = "10.0.0.0/24"
public_subnet_gw   = "10.0.0.1"
app_subnet_cidr    = "10.0.1.0/24"
app_subnet_gw      = "10.0.1.1"
data_subnet_cidr   = "10.0.250.0/24"
data_subnet_gw     = "10.0.250.1"

az        = ""               # e.g. az1.dc1
image_id  = ""               # ByteDC IMS image UUID
flavor_id = "s3.small.1"
vm_count  = 1

enable_elb = false
enable_nat = false
enable_dns = false

enable_platform = false
platform_roles = {
  postgres      = 1
  redis         = 1
  observability = 1
}

# CIDRs allowed to reach admin/web ingress. Wizard overwrites these.
admin_cidr = "0.0.0.0/0"
web_cidr   = "0.0.0.0/0"
enable_web_ingress = true
