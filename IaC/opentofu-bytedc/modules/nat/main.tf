# Minimal NAT gateway + SNAT — owns its own EIP for outbound egress
# from private VMs (app + data subnets), or can reference an existing NAT.

variable "env"            { type = string }
variable "vpc_id"         { type = string }
variable "subnet_id"      { type = string }

variable "name_prefix" {
  type    = string
  default = "dbcs"
}

# Optional: reuse an existing EIP (by id). If null, the module creates one.
variable "floating_ip_id" {
  type    = string
  default = null
}

variable "existing_nat_gateway_id" {
  type        = string
  default     = ""
  description = "Optional existing NAT gateway ID. When set, this module will not create a NAT gateway."
}

variable "manage_snat_rules" {
  type        = bool
  default     = true
  description = "Whether this module should manage SNAT rules. Usually false when reusing an existing NAT that already has rules."
}

variable "snat_subnet_ids" {
  type        = map(string)
  description = "Map of static name => subnet ID for SNAT rules. Static keys avoid for_each unknown-at-plan errors."
  default     = {}
}


variable "spec" {
  type    = string
  default = "1"
}

variable "eip_bandwidth_size" {
  type    = number
  default = 5
}

variable "eip_pool_type" {
  type        = string
  default     = "EIP_Pool_Global"
  description = "ByteDC EIP pool name for the NAT gateway. Use the same pool syntax as the working platform EIPs."
}

locals {
  create_nat_gateway       = trimspace(var.existing_nat_gateway_id != null ? var.existing_nat_gateway_id : "") == ""
  input_floating_ip_id     = trimspace(var.floating_ip_id != null ? var.floating_ip_id : "") != "" ? trimspace(var.floating_ip_id != null ? var.floating_ip_id : "") : null
  should_create_eip        = local.create_nat_gateway && local.input_floating_ip_id == null
  effective_floating_ip_id = local.input_floating_ip_id != null ? local.input_floating_ip_id : try(hcs_vpc_eip.this[0].id, null)
  effective_nat_gateway_id = local.create_nat_gateway ? hcs_nat_gateway.this[0].id : var.existing_nat_gateway_id
  effective_snat_subnets   = length(var.snat_subnet_ids) > 0 ? var.snat_subnet_ids : { default = var.subnet_id }
}

resource "hcs_nat_gateway" "this" {
  count = local.create_nat_gateway ? 1 : 0

  name      = "${var.name_prefix}-${var.env}-nat"
  vpc_id    = var.vpc_id
  subnet_id = var.subnet_id
  spec      = var.spec
}

# Own EIP for SNAT egress (only when caller didn't pass one in).
resource "hcs_vpc_eip" "this" {
  count = local.should_create_eip ? 1 : 0

  publicip {
    type = var.eip_pool_type
  }
  bandwidth {
    name       = "${var.name_prefix}-${var.env}-nat-bw"
    size       = var.eip_bandwidth_size
    share_type = "PER"
  }

  lifecycle {
    # If the EIP is deleted out-of-band in the ByteDC console, tofu will
    # detect the drift on next plan and recreate it automatically.
    create_before_destroy = true
  }
}

# One SNAT rule per subnet. Keys are static (e.g. "app","data") so for_each
# is plannable even though subnet IDs are known only after apply.
resource "hcs_nat_snat_rule" "this" {
  for_each       = var.manage_snat_rules ? local.effective_snat_subnets : {}
  nat_gateway_id = local.effective_nat_gateway_id
  floating_ip_id = local.effective_floating_ip_id
  subnet_id      = each.value
}


output "nat_gateway_id" { value = local.effective_nat_gateway_id }
output "nat_eip_id"     { value = local.effective_floating_ip_id }
output "nat_eip_address" {
  value = local.should_create_eip ? try(hcs_vpc_eip.this[0].address, null) : null
}
