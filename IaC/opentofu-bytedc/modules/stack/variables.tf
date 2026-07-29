# Top-level "stack" composition module.
# Wraps vpc + ecs + platform + eip + elb + nat + dns so each env folder is
# just credentials + tfvars + a 10-line main.tf calling this module.

variable "env"          { type = string }
variable "project_name" { type = string }
variable "name_prefix"  {
  type        = string
  default     = ""
  description = "Resource naming prefix for VMs/VPCs/ELBs. Defaults to project_name when empty."
}

# ---------- VPC / subnets ----------
variable "vpc_cidr"           { type = string }
variable "vpc_name" {
  type        = string
  default     = ""
  description = "Optional VPC name override. Defaults to dbcs-<env>-vpc."
}
variable "public_subnet_cidr" { type = string }
variable "public_subnet_gw"   { type = string }
variable "app_subnet_cidr"    { type = string }
variable "app_subnet_gw"      { type = string }
variable "data_subnet_cidr"   { type = string }
variable "data_subnet_gw"     { type = string }


variable "admin_cidr" { type = string }
variable "web_cidr" {
  type    = string
  default = "0.0.0.0/0"
}
variable "enable_web_ingress" {
  type    = bool
  default = true
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




# ---------- Compute defaults ----------
variable "az"        { type = string }
variable "image_id"  { type = string }
variable "flavor_id" { type = string }
variable "ecs_admin_pass" {
  type      = string
  sensitive = true
}

# ---------- App pool ----------
variable "vm_count" {
  type    = number
  default = 0
}

# ---------- Platform pool (postgres/redis/nexus/openbao/observability/runner) ----------
variable "enable_platform" {
  type    = bool
  default = true
}
# Map of role -> count. Set a count of 0 (or omit the key) to skip that role
# entirely, e.g. platform_roles = { runner = 1, observability = 1 } only.
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

# Per-role subnet placement. Values must be "app" or "data". Roles not listed
# fall back to "data".
variable "platform_subnets" {
  type    = map(string)
  default = {}
}


# Optional per-role overrides — flavor / image / az / disk size. Anything not
# supplied falls back to the env-level defaults above.
variable "platform_overrides" {
  type = map(object({
    flavor_id        = optional(string)
    image_id         = optional(string)
    az               = optional(string)
    system_disk_size = optional(number)
    system_disk_type = optional(string)
  }))
  default = {}
}

variable "platform_eip_roles" {
  type        = list(string)
  default     = ["runner", "observability"]
  description = "Subset of platform_roles getting a public EIP."
}

# ---------- Optional bespoke / one-off VMs ----------
# Use this for ad-hoc machines outside the standard app + platform pools
# (e.g. a bastion, a load-test VM, a temporary migration host). Key = logical
# name (becomes part of the VM hostname). Set pool = "app" or "data" to pick
# which subnet/SG to land on.
variable "extra_vms" {
  type = map(object({
    pool             = optional(string, "data") # "app" | "data"
    vm_count         = optional(number, 1)
    flavor_id        = optional(string)
    image_id         = optional(string)
    az               = optional(string)
    system_disk_size = optional(number)
    system_disk_type = optional(string)
    with_eip         = optional(bool, false)
  }))
  default = {}
}

# ---------- Edge ----------
variable "enable_elb" {
  type    = bool
  default = true
}
variable "enable_nat" {
  type    = bool
  default = true
}
variable "enable_dns" {
  type    = bool
  default = true
}
variable "domain_base" {
  type    = string
  default = "example.com"
}

# ---------- EIP pools ----------
variable "eip_pool_type" {
  type        = string
  default     = null
  description = "Legacy shared override for both NAT + platform EIPs."
}
variable "platform_eip_pool_type" {
  type    = string
  default = null
}
variable "nat_eip_pool_type" {
  type    = string
  default = null
}
variable "nat_floating_ip_id" {
  type        = string
  default     = null
  description = "Reuse an unmanaged NAT EIP by ID. Leave null when Terraform owns it."
}
variable "existing_nat_gateway_id" {
  type        = string
  default     = ""
  description = "Reuse an existing NAT gateway by ID. No NAT gateway is created when set."
}
variable "create_nat_in_existing_vpc" {
  type        = bool
  default     = false
  description = "Explicitly create a new NAT gateway even when existing_vpc_id is set. Default false to avoid collisions with shared/deleted networks."
}
variable "manage_existing_nat_snat_rules" {
  type        = bool
  default     = false
  description = "When existing_nat_gateway_id is set, also manage SNAT rules for app/data subnets. Requires nat_floating_ip_id."
}
