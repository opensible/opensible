locals {
  # Sanitized resource name prefix. Uses the explicit name_prefix variable when
  # set, otherwise falls back to the ByteDC project_name. This keeps the cloud
  # tenant name (project_name) separate from the VM/VPC naming convention.
  name_prefix = trimspace(var.name_prefix) != "" ? lower(replace(trimspace(var.name_prefix), "/[^a-zA-Z0-9_-]+/", "-")) : (
    trimspace(var.project_name) != "" ? lower(replace(trimspace(var.project_name), "/[^a-zA-Z0-9_-]+/", "-")) : "dbcs"
  )

  # Drop roles with count 0 so platform_roles = { runner = 1, postgres = 0 }
  # is equivalent to omitting postgres entirely.
  active_platform_roles = var.enable_platform ? {
    for k, v in var.platform_roles : k => v if v > 0
  } : {}

  platform_eip_set = toset([
    for r in var.platform_eip_roles : r
    if contains(keys(local.active_platform_roles), r)
  ])

  existing_nat_gateway_id = trimspace(var.existing_nat_gateway_id != null ? var.existing_nat_gateway_id : "")
  nat_enabled = var.enable_nat && (
    trimspace(var.existing_vpc_id != null ? var.existing_vpc_id : "") == "" ||
    var.create_nat_in_existing_vpc ||
    local.existing_nat_gateway_id != ""
  )
  manage_nat_snat_rules = local.existing_nat_gateway_id == "" ? true : var.manage_existing_nat_snat_rules
}

module "vpc" {
  source             = "../vpc"
  env                = var.env
  name_prefix        = local.name_prefix
  vpc_name           = var.vpc_name
  vpc_cidr           = var.vpc_cidr
  public_subnet_cidr = var.public_subnet_cidr
  public_subnet_gw   = var.public_subnet_gw
  app_subnet_cidr    = var.app_subnet_cidr
  app_subnet_gw      = var.app_subnet_gw
  data_subnet_cidr   = var.data_subnet_cidr
  data_subnet_gw     = var.data_subnet_gw
  admin_cidr         = var.admin_cidr
  web_cidr           = var.web_cidr
  enable_web_ingress = var.enable_web_ingress

  existing_vpc_id                = var.existing_vpc_id
  existing_public_subnet_id      = var.existing_public_subnet_id
  existing_app_subnet_id         = var.existing_app_subnet_id
  existing_data_subnet_id        = var.existing_data_subnet_id
  existing_public_ipv4_subnet_id = var.existing_public_ipv4_subnet_id
  existing_app_ipv4_subnet_id    = var.existing_app_ipv4_subnet_id
  existing_data_ipv4_subnet_id   = var.existing_data_ipv4_subnet_id
  existing_app_sg_id             = var.existing_app_sg_id
  existing_data_sg_id            = var.existing_data_sg_id
}



# App pool — tenant containers on the app subnet.
module "ecs" {
  source     = "../ecs"
  env        = var.env
  name_prefix = local.name_prefix
  name       = "app"
  vm_count   = var.vm_count
  image_id   = var.image_id
  flavor_id  = var.flavor_id
  az         = var.az
  subnet_id  = module.vpc.app_subnet_id
  sg_id      = module.vpc.app_sg_id
  admin_pass = var.ecs_admin_pass
}

# Platform pool — one ECS module per role, per-role override-aware.
module "platform" {
  for_each   = local.active_platform_roles
  source     = "../ecs"
  env        = var.env
  name_prefix = local.name_prefix
  name       = each.key
  vm_count   = each.value
  image_id   = try(var.platform_overrides[each.key].image_id, null) != null ? var.platform_overrides[each.key].image_id : var.image_id
  flavor_id  = try(var.platform_overrides[each.key].flavor_id, null) != null ? var.platform_overrides[each.key].flavor_id : var.flavor_id
  az         = try(var.platform_overrides[each.key].az, null) != null ? var.platform_overrides[each.key].az : var.az
  subnet_id  = lookup(var.platform_subnets, each.key, "data") == "app" ? module.vpc.app_subnet_id : module.vpc.data_subnet_id
  sg_id      = lookup(var.platform_subnets, each.key, "data") == "app" ? module.vpc.app_sg_id    : module.vpc.data_sg_id

  admin_pass = var.ecs_admin_pass
  system_disk_size = try(var.platform_overrides[each.key].system_disk_size, null) != null ? var.platform_overrides[each.key].system_disk_size : 40
  system_disk_type = try(var.platform_overrides[each.key].system_disk_type, null) != null ? var.platform_overrides[each.key].system_disk_type : "SSD"
}

# Optional one-off VMs (bastion / load-test / migration host / etc).
module "extra" {
  for_each   = var.extra_vms
  source     = "../ecs"
  env        = var.env
  name_prefix = local.name_prefix
  name       = each.key
  vm_count   = each.value.vm_count
  image_id   = coalesce(each.value.image_id, var.image_id)
  flavor_id  = coalesce(each.value.flavor_id, var.flavor_id)
  az         = coalesce(each.value.az, var.az)
  subnet_id  = each.value.pool == "app" ? module.vpc.app_subnet_id : module.vpc.data_subnet_id
  sg_id      = each.value.pool == "app" ? module.vpc.app_sg_id : module.vpc.data_sg_id
  admin_pass = var.ecs_admin_pass
  system_disk_size = coalesce(each.value.system_disk_size, 40)
  system_disk_type = coalesce(each.value.system_disk_type, "SSD")
}

# Per-role EIPs for platform pool (default: runner + observability).
module "platform_eip" {
  for_each      = local.platform_eip_set
  source        = "../eip"
  env           = var.env
  name_prefix   = local.name_prefix
  name          = each.key
  eip_pool_type = coalesce(var.platform_eip_pool_type, var.eip_pool_type, "EIP_Pool_Global")
  instance_id   = module.platform[each.key].first_id
  associate     = true
}

# Optional EIPs for extra_vms entries marked with_eip = true.
module "extra_eip" {
  for_each      = { for k, v in var.extra_vms : k => v if try(v.with_eip, false) }
  source        = "../eip"
  env           = var.env
  name_prefix   = local.name_prefix
  name          = each.key
  eip_pool_type = coalesce(var.platform_eip_pool_type, var.eip_pool_type, "EIP_Pool_Global")
  instance_id   = module.extra[each.key].first_id
  associate     = true
}

module "elb" {
  count            = var.enable_elb ? 1 : 0
  source           = "../elb"
  env              = var.env
  name_prefix      = local.name_prefix
  name             = "app"
  vpc_id           = module.vpc.vpc_id
  subnet_id        = module.vpc.public_subnet_id
  ipv4_subnet_id   = module.vpc.public_ipv4_subnet_id
  member_subnet_id = module.vpc.app_ipv4_subnet_id
  member_ips       = module.ecs.private_ips
}

module "nat" {
  count           = local.nat_enabled ? 1 : 0
  source          = "../nat"
  env             = var.env
  name_prefix     = local.name_prefix
  vpc_id          = module.vpc.vpc_id
  subnet_id       = module.vpc.public_subnet_id
  snat_subnet_ids = { app = module.vpc.app_subnet_id, data = module.vpc.data_subnet_id }
  eip_pool_type   = coalesce(var.nat_eip_pool_type, var.eip_pool_type, "EIP_Pool_Cambodia")
  floating_ip_id  = var.nat_floating_ip_id
  existing_nat_gateway_id = local.existing_nat_gateway_id
  manage_snat_rules       = local.manage_nat_snat_rules
}

module "dns" {
  count       = var.enable_dns ? 1 : 0
  source      = "../dns"
  env         = var.env
  vpc_id      = module.vpc.vpc_id
  domain_base = var.domain_base
  private_records = {
    for role, m in module.platform :
    role => m.private_ips[0]
    if length(m.private_ips) > 0
  }
  public_records = var.enable_elb ? { "portal" = module.elb[0].elb_eip } : {}
}
