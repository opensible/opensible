output "vpc_id"           { value = module.vpc.vpc_id }
output "public_subnet_id" { value = module.vpc.public_subnet_id }
output "app_subnet_id"    { value = module.vpc.app_subnet_id }
output "data_subnet_id"   { value = module.vpc.data_subnet_id }
output "app_sg_id"        { value = module.vpc.app_sg_id }
output "data_sg_id"       { value = module.vpc.data_sg_id }

output "ecs_ids"         { value = module.ecs.instance_ids }
output "ecs_private_ips" { value = module.ecs.private_ips }

output "platform_private_ips" {
  value = { for k, m in module.platform : k => m.private_ips }
}
output "platform_eip_addresses" {
  value = { for k, e in module.platform_eip : k => e.eip_address }
}

output "extra_private_ips" {
  value = { for k, m in module.extra : k => m.private_ips }
}
output "extra_eip_addresses" {
  value = { for k, e in module.extra_eip : k => e.eip_address }
}

output "elb_public_ip" { value = var.enable_elb ? module.elb[0].elb_eip : null }
