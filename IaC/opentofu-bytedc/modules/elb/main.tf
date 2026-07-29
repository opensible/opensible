# Public ELB in front of the app pool.
# Auto-fronts tenant containers on 80/443 → app VMs on the same ports.

variable "env" {
  type = string
}

variable "name" {
  type    = string
  default = "app"
}

variable "name_prefix" {
  type    = string
  default = "dbcs"
}

variable "vpc_id" {
  type = string
}

variable "subnet_id" {
  type        = string
  description = "VPC subnet ID of the public subnet (informational / future use)."
  default     = null
}

variable "ipv4_subnet_id" {
  type        = string
  description = "IPv4 neutron subnet ID of the public subnet (required by ELB v3)."
}

variable "member_subnet_id" {
  type        = string
  description = "IPv4 neutron subnet ID of the backend members (app subnet)."
}

variable "member_ips" {
  type        = list(string)
  description = "Private IPs of app VMs to register as ELB pool members."
}

variable "listeners" {
  type = map(object({
    protocol = string
    port     = number
  }))
  default = {
    http  = { protocol = "TCP", port = 80 }
    https = { protocol = "TCP", port = 443 }
  }
}

variable "l4_elb_flavor_name" {
  type        = string
  description = "Dedicated ELB L4 flavor name."
  default     = "L4_flavor.elb.s1.small"
}

variable "l7_elb_flavor_name" {
  type        = string
  description = "Optional dedicated ELB L7 flavor name. Leave null for TCP-only listeners."
  default     = null
}

data "hcs_elb_flavors" "l4" {
  name = var.l4_elb_flavor_name
}

data "hcs_elb_flavors" "l7" {
  count = var.l7_elb_flavor_name == null ? 0 : 1
  name  = var.l7_elb_flavor_name
}

locals {
  l4_elb_flavor_id = try(data.hcs_elb_flavors.l4.ids[0], null)
  l7_elb_flavor_id = var.l7_elb_flavor_name == null ? null : try(data.hcs_elb_flavors.l7[0].ids[0], null)

  members = merge([
    for lkey, l in var.listeners : {
      for idx, ip in var.member_ips :
      "${lkey}-${idx}" => {
        listener_key = lkey
        address      = ip
        port         = l.port
      }
    }
  ]...)
}

resource "hcs_elb_loadbalancer" "this" {
  name              = "${var.name_prefix}-${var.env}-${var.name}-elb"
  description       = "Public ELB for ${var.name_prefix} ${var.env} ${var.name}"
  cross_vpc_backend = true
  vpc_id            = var.vpc_id
  ipv4_subnet_id    = var.ipv4_subnet_id
  l4_flavor_id      = local.l4_elb_flavor_id
  l7_flavor_id      = local.l7_elb_flavor_id

  lifecycle {
    ignore_changes = [
      iptype,
      bandwidth_charge_mode,
      sharetype,
      bandwidth_size,
    ]
  }
}

resource "hcs_elb_listener" "this" {
  for_each        = var.listeners
  name            = "${var.name_prefix}-${var.env}-${var.name}-${each.key}"
  description     = "${upper(each.key)} listener for ${var.name_prefix} ${var.env} ${var.name}"
  protocol        = each.value.protocol
  protocol_port   = each.value.port
  loadbalancer_id = hcs_elb_loadbalancer.this.id
  idle_timeout    = 60
}

resource "hcs_elb_pool" "this" {
  for_each    = var.listeners
  name        = "${var.name_prefix}-${var.env}-${var.name}-${each.key}-pool"
  description = "Backend pool for ${each.key}"
  protocol    = each.value.protocol
  lb_method   = "ROUND_ROBIN"
  listener_id = hcs_elb_listener.this[each.key].id
}

resource "hcs_elb_monitor" "this" {
  for_each    = var.listeners
  pool_id     = hcs_elb_pool.this[each.key].id
  protocol    = each.value.protocol
  interval    = 10
  timeout     = 5
  max_retries = 3
  port        = each.value.port
}

resource "hcs_elb_member" "this" {
  for_each      = local.members
  name          = "${var.name_prefix}-${var.env}-${var.name}-${each.key}"
  address       = each.value.address
  protocol_port = each.value.port
  pool_id       = hcs_elb_pool.this[each.value.listener_key].id
  subnet_id     = var.member_subnet_id
}

output "elb_id" {
  value = hcs_elb_loadbalancer.this.id
}

output "elb_vip" {
  value = hcs_elb_loadbalancer.this.ipv4_address
}

output "elb_eip" {
  value = hcs_elb_loadbalancer.this.ipv4_eip
}

output "elb_eip_id" {
  value = hcs_elb_loadbalancer.this.ipv4_eip_id
}
