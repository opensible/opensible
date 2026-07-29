variable "env" {
  type    = string
  default = "dev"
}

variable "region" {
  type    = string
  default = "REGION_ID"
}

variable "project_name" {
  type        = string
  description = "ByteDC project (resource space) name — found under Service List > Resource Space."
}

variable "name_prefix" {
  type        = string
  default     = ""
  description = "Resource naming prefix for VMs/VPCs/ELBs. Defaults to project_name when empty."
}

variable "access_key" {
  type      = string
  sensitive = true
}

variable "secret_key" {
  type      = string
  sensitive = true
}

variable "az" {
  type    = string
  default = "az1.dc1"
}

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "vpc_name" {
  type        = string
  default     = ""
  description = "Optional explicit VPC name. Defaults to <name_prefix>-<env>-vpc when empty."
}


variable "public_subnet_cidr" {
  type    = string
  default = "10.0.0.0/24"
}

variable "public_subnet_gw" {
  type    = string
  default = "10.0.0.1"
}

variable "app_subnet_cidr" {
  type    = string
  default = "10.0.1.10/24"
}

variable "app_subnet_gw" {
  type    = string
  default = "10.0.1.10"
}

variable "data_subnet_cidr" {
  type    = string
  default = "10.0.250.10/24"
}

variable "data_subnet_gw" {
  type    = string
  default = "10.0.250.10"
}

variable "image_id" {
  type        = string
  description = "ByteDC IMS image ID (e.g. Ubuntu 22.04)."
}

variable "flavor_id" {
  type    = string
  default = "s3.small.1"
}

variable "ecs_admin_pass" {
  type      = string
  sensitive = true
}

variable "vm_count" {
  type        = number
  default     = 0
  description = "Legacy app pool VM count. Defaults to 0 — provision compute through the platform pool instead."
}

variable "enable_platform" {
  type        = bool
  default     = true
  description = "Provision the platform pool (postgres/redis/nexus/openbao/observability/runner)."
}

variable "platform_roles" {
  type = map(number)
  default = {
    postgres      = 1
    redis         = 1
    nexus         = 1
    openbao       = 1
    observability = 1
    runner        = 1
  }
}

variable "platform_subnets" {
  type        = map(string)
  default     = {}
  description = "Per-role subnet placement: \"app\" or \"data\". Roles not listed default to data subnet."
}


variable "enable_elb" {
  type        = bool
  default     = true
  description = "Provision public ELB + EIP fronting the app pool."
}

variable "enable_nat" {
  type        = bool
  default     = true
  description = "Provision NAT gateway in public subnet for private VM egress."
}

variable "enable_dns" {
  type        = bool
  default     = true
  description = "Provision private DNS zone (env.example.com) for platform records."
}

variable "domain_base" {
  type    = string
  default = "example.com"
}

variable "admin_cidr" {
  type        = string
  default     = "203.0.113.10/32"
  description = "Office CIDR allowed to SSH on port 2222 + reach admin UIs."
}

variable "web_cidr" {
  type        = string
  default     = "0.0.0.0/0"
  description = "Source CIDR allowed to reach 80/443 on the ELB / app VMs."
}

variable "enable_web_ingress" {
  type    = bool
  default = true
}

variable "platform_eip_roles" {
  type        = list(string)
  description = "Subset of platform_roles getting a public EIP. runner gets one to serve as the ansible jump host."
  default     = ["runner", "observability"]
}

variable "eip_pool_type" {
  type        = string
  default     = null
  description = "Legacy shared EIP pool override for both NAT + platform EIPs. Prefer nat_eip_pool_type/platform_eip_pool_type."
}

variable "platform_eip_pool_type" {
  type        = string
  default     = null
  description = "Pool for platform EIPs (runner/observability). Defaults to EIP_Pool_Global unless legacy eip_pool_type is set."
}

variable "nat_eip_pool_type" {
  type        = string
  default     = null
  description = "Pool for the NAT EIP. Defaults to EIP_Pool_Cambodia unless legacy eip_pool_type is set."
}

variable "nat_floating_ip_id" {
  type        = string
  default     = null
  description = "Reuse an existing NAT EIP by ID. Use this only for an unmanaged/external EIP; if Terraform already manages the NAT EIP, leave null."
}

variable "existing_nat_gateway_id" {
  type        = string
  default     = ""
  description = "Reuse an existing NAT gateway by ID instead of creating a new one. Useful when reusing an existing VPC."
}

variable "create_nat_in_existing_vpc" {
  type        = bool
  default     = false
  description = "Explicitly create a new NAT gateway inside an existing VPC. Leave false when the shared VPC already has egress."
}

variable "manage_existing_nat_snat_rules" {
  type        = bool
  default     = false
  description = "When reusing an existing NAT gateway, also create SNAT rules for app/data subnets. Requires nat_floating_ip_id."
}

# Optional per-role flavor/image/az/disk overrides for the platform pool.
# Example:
#   platform_overrides = {
#     postgres = { flavor_id = "s3.large.4", system_disk_size = 100 }
#   }
variable "platform_overrides" {
  type = map(object({
    flavor_id        = optional(string)
    image_id         = optional(string)
    az               = optional(string)
    system_disk_size = optional(number)
    system_disk_type = optional(string)
  }))
  default     = {}
  description = "Per-role overrides for the platform pool. Anything unset falls back to env defaults."
}

# Optional one-off VMs outside the standard app + platform pools.
# Example:
#   extra_vms = {
#     bastion = { pool = "app", with_eip = true, flavor_id = "s3.small.1" }
#     loadtest = { pool = "data", vm_count = 2 }
#   }
variable "extra_vms" {
  type = map(object({
    pool             = optional(string, "data")
    vm_count         = optional(number, 1)
    flavor_id        = optional(string)
    image_id         = optional(string)
    az               = optional(string)
    system_disk_size = optional(number)
    system_disk_type = optional(string)
    with_eip         = optional(bool, false)
  }))
  default     = {}
  description = "Ad-hoc VMs (bastion, load-test, migration host, etc.). Empty by default."
}

# ---------------------------------------------------------------------------
# Security & access — surfaced by the Security step in the wizard.
# ---------------------------------------------------------------------------

variable "ssh_port" {
  type        = number
  default     = 2222
  description = "SSH port opened on app/data SGs from admin_cidr. Golden image ships on 2222."
}

variable "extra_users" {
  type = list(object({
    name     = string
    password = optional(string)
    ssh_key  = optional(string)
    sudo     = optional(bool, false)
    shell    = optional(string, "/bin/bash")
    groups   = optional(list(string), [])
  }))
  default     = []
  description = "Additional Linux users to seed on every provisioned VM."
}

variable "ingress_rules" {
  type = list(object({
    protocol    = string
    port        = number
    cidr        = string
    sg          = optional(string, "app") # "app" | "data"
    description = optional(string, "")
  }))
  default     = []
  description = "Extra inbound rules applied to app/data security groups."
}


# ---------- Reuse existing VPC / subnets / SGs ----------
variable "existing_vpc_id" {
  type = string
  default = "" 
}


variable "existing_public_subnet_id" {
  type = string
  default = "" 
}


variable "existing_app_subnet_id" {
  type = string
  default = "" 
}


variable "existing_data_subnet_id" {
  type = string
  default = "" 
}


variable "existing_public_ipv4_subnet_id" {
  type = string
  default = "" 
}


variable "existing_app_ipv4_subnet_id" {
  type = string
  default = "" 
}


variable "existing_data_ipv4_subnet_id" {
  type = string
  default = "" 
}


variable "existing_app_sg_id" {
  type = string
  default = "" 
}


variable "existing_data_sg_id" {
  type = string
  default = "" 
}


