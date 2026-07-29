# Thin env wrapper. All real composition lives in modules/stack.
# To add a new environment: copy envs/dev/ to envs/<new-env>/, update
# backend.hcl + terraform.tfvars, run `tofu init -backend-config=backend.hcl`.

module "stack" {
  source = "../../modules/stack"

  env          = var.env
  project_name = var.project_name
  name_prefix  = var.name_prefix


  vpc_cidr           = var.vpc_cidr
  vpc_name           = var.vpc_name
  public_subnet_cidr = var.public_subnet_cidr
  public_subnet_gw   = var.public_subnet_gw
  app_subnet_cidr    = var.app_subnet_cidr
  app_subnet_gw      = var.app_subnet_gw
  data_subnet_cidr   = var.data_subnet_cidr
  data_subnet_gw     = var.data_subnet_gw
  admin_cidr         = var.admin_cidr
  web_cidr           = var.web_cidr
  enable_web_ingress = var.enable_web_ingress

  az             = var.az
  image_id       = var.image_id
  flavor_id      = var.flavor_id
  ecs_admin_pass = var.ecs_admin_pass
  vm_count       = var.vm_count

  enable_platform    = var.enable_platform
  platform_roles     = var.platform_roles
  platform_subnets   = var.platform_subnets
  platform_overrides = var.platform_overrides
  platform_eip_roles = var.platform_eip_roles


  extra_vms = var.extra_vms

  enable_elb  = var.enable_elb
  enable_nat  = var.enable_nat
  enable_dns  = var.enable_dns
  domain_base = var.domain_base

  eip_pool_type          = var.eip_pool_type
  platform_eip_pool_type = var.platform_eip_pool_type
  nat_eip_pool_type      = var.nat_eip_pool_type
  nat_floating_ip_id     = var.nat_floating_ip_id
  existing_nat_gateway_id      = var.existing_nat_gateway_id
  create_nat_in_existing_vpc   = var.create_nat_in_existing_vpc
  manage_existing_nat_snat_rules = var.manage_existing_nat_snat_rules
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

output "vpc_id"                 { value = module.stack.vpc_id }
output "public_subnet_id"       { value = module.stack.public_subnet_id }
output "app_subnet_id"          { value = module.stack.app_subnet_id }
output "data_subnet_id"         { value = module.stack.data_subnet_id }
output "app_sg_id"              { value = module.stack.app_sg_id }
output "data_sg_id"             { value = module.stack.data_sg_id }
output "ecs_ids"                { value = module.stack.ecs_ids }
output "ecs_private_ips"        { value = module.stack.ecs_private_ips }
output "platform_private_ips"   { value = module.stack.platform_private_ips }
output "platform_eip_addresses" { value = module.stack.platform_eip_addresses }
output "extra_private_ips"      { value = module.stack.extra_private_ips }
output "extra_eip_addresses"    { value = module.stack.extra_eip_addresses }
output "elb_public_ip"          { value = module.stack.elb_public_ip }
