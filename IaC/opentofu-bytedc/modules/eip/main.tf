# Minimal EIP module — mirrors the ByteDC team reference.

variable "env"  { type = string }
variable "name" { type = string }
variable "name_prefix" {
  type    = string
  default = "dbcs"
}
variable "eip_pool_type" {
  type        = string
  default     = "EIP_Pool_Global"
  description = "ByteDC EIP pool name. Defaults to EIP_Pool_Global; some tenants need EIP_Pool_Cambodia."
}
variable "bandwidth_size" {
  type    = number
  default = 20
}
variable "bandwidth_share_type" {
  type    = string
  default = "PER"
}
variable "instance_id" {
  type        = string
  default     = ""
  description = "Optional ECS instance ID to bind the EIP to. Pair with associate=true."
}
variable "associate" {
  type        = bool
  default     = false
  description = "Whether to bind the EIP to instance_id. Must be statically known at plan time."
}

resource "hcs_vpc_eip" "this" {
  publicip {
    type = var.eip_pool_type
  }
  bandwidth {
    name        = "${var.name_prefix}-${var.env}-${var.name}-eip"
    size        = var.bandwidth_size
    share_type  = var.bandwidth_share_type
  }
}

resource "hcs_ecs_compute_eip_associate" "this" {
  count       = var.associate ? 1 : 0
  public_ip   = hcs_vpc_eip.this.address
  instance_id = var.instance_id
}

output "eip_id"      { value = hcs_vpc_eip.this.id }
output "eip_address" { value = hcs_vpc_eip.this.address }
