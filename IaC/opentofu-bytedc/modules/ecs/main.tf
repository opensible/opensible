# Minimal ECS module — mirrors the ByteDC team reference.
# Plain admin_pass bootstrap, single NIC, single system disk.

variable "env"        { type = string }
variable "name"       { type = string }
variable "name_prefix" {
  type        = string
  default     = "dbcs"
  description = "Resource name prefix. Stack passes the sanitized project_name so VMs are named <project>-<env>-<role>."
}
variable "vm_count" {
  type    = number
  default = 1
}
variable "image_id"  { type = string }
variable "flavor_id" { type = string }
variable "az"        { type = string }
variable "subnet_id" { type = string }
variable "sg_id"     { type = string }
variable "admin_pass" {
  type      = string
  sensitive = true
}
variable "system_disk_type" {
  type    = string
  default = "SSD"
}
variable "system_disk_size" {
  type    = number
  default = 40
}

resource "hcs_ecs_compute_instance" "this" {
  count              = var.vm_count
  name               = var.vm_count > 1 ? format("%s-%s-%s-%02d", var.name_prefix, var.env, var.name, count.index + 1) : "${var.name_prefix}-${var.env}-${var.name}"
  image_id           = var.image_id
  flavor_id          = var.flavor_id
  availability_zone  = var.az
  security_group_ids = [var.sg_id]
  admin_pass         = var.admin_pass

  network {
    uuid              = var.subnet_id
    source_dest_check = false
  }

  system_disk_type = var.system_disk_type
  system_disk_size = var.system_disk_size

  delete_disks_on_termination = true
  delete_eip_on_termination   = true
}

output "instance_ids" { value = hcs_ecs_compute_instance.this[*].id }
output "private_ips"  { value = hcs_ecs_compute_instance.this[*].access_ip_v4 }
output "first_id"     { value = length(hcs_ecs_compute_instance.this) > 0 ? hcs_ecs_compute_instance.this[0].id : null }
output "first_port_id" {
  value = length(hcs_ecs_compute_instance.this) > 0 ? hcs_ecs_compute_instance.this[0].network[0].port : null
}
