# VPC + three subnets + two security groups for the DBCS platform.
#
# Layout (matches runbooks/08-platform-vms.md, extended with public tier):
#
#   VPC dbcs-${env}-vpc          10.x.0.0/16
#   ├── subnet "public" 10.x.0.0/24    → edge tier (ELB / NAT / bastion EIPs live here)
#   ├── subnet "app"    10.x.1.0/24    → app-sg   (tenant containers)
#   └── subnet "data"   10.x.250.0/24  → data-sg  (postgres/redis/nexus/obs/runner)
#
# Notes on "public": ByteDC/HCS subnets are L3 the same regardless of routing,
# but we keep a dedicated public-tier subnet so ELB listeners and NAT gateway
# resources have a stable CIDR distinct from workload subnets — easier for
# audit, NACLs and future migration to real per-subnet route tables.
#
# Security groups:
#   app-sg   — tenant containers, web-exposed via ELB
#   data-sg  — platform services, only reachable from app-sg + admin_cidr

variable "env"          { type = string }
variable "vpc_cidr"     { type = string }

variable "name_prefix" {
  type        = string
  default     = "dbcs"
  description = "Resource name prefix for VPC, subnets and security groups."
}

variable "vpc_name" {
  type        = string
  default     = ""
  description = "Optional explicit VPC name. Falls back to <name_prefix>-<env>-vpc when empty."
}


variable "public_subnet_cidr" { type = string }
variable "public_subnet_gw"   { type = string }

variable "app_subnet_cidr" { type = string }
variable "app_subnet_gw"   { type = string }

variable "data_subnet_cidr" { type = string }
variable "data_subnet_gw"   { type = string }

variable "dns_list" {
  type    = list(string)
  default = ["10.7.132.42", "10.7.132.43"]
}

variable "admin_cidr" {
  type        = string
  default     = "203.0.113.10/32"
  description = "Office/jump CIDR allowed to SSH on port 2222 + reach Grafana/Nexus admin UIs. Defaults to MCNC office egress."
}

variable "web_cidr" {
  type        = string
  default     = "0.0.0.0/0"
  description = "Source CIDR allowed to reach 80/443 on the public ELB / app VMs."
}

variable "enable_web_ingress" {
  type    = bool
  default = true
}

# ---- Reuse existing VPC / subnets / SGs -----------------------------------
# When these are set, we skip creating the corresponding resource and use the
# provided ID instead. This lets multiple stacks share one network so their
# VMs can reach each other on private IPs.
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





locals {
  create_vpc    = var.existing_vpc_id == ""
  create_public = var.existing_public_subnet_id == ""
  create_app    = var.existing_app_subnet_id == ""
  create_data   = var.existing_data_subnet_id == ""
  create_app_sg  = var.existing_app_sg_id == ""
  create_data_sg = var.existing_data_sg_id == ""
  # Use try() so `tofu apply -refresh-only` against an empty state doesn't fail
  # on `hcs_vpc.this[0]` when count=1 but the resource hasn't been created yet.
  vpc_id_effective = local.create_vpc ? try(hcs_vpc.this[0].id, null) : var.existing_vpc_id
}


# ---- VPC + subnets ---------------------------------------------------------

resource "hcs_vpc" "this" {
  count = local.create_vpc ? 1 : 0
  name  = var.vpc_name != "" ? var.vpc_name : "${var.name_prefix}-${var.env}-vpc"
  cidr  = var.vpc_cidr
}

resource "hcs_vpc_subnet" "public" {
  count      = local.create_public ? 1 : 0
  name       = "${var.name_prefix}-${var.env}-public-subnet"
  cidr       = var.public_subnet_cidr
  gateway_ip = var.public_subnet_gw
  vpc_id     = local.vpc_id_effective
  dns_list   = var.dns_list
}

resource "hcs_vpc_subnet" "app" {
  count      = local.create_app ? 1 : 0
  name       = "${var.name_prefix}-${var.env}-app-subnet"
  cidr       = var.app_subnet_cidr
  gateway_ip = var.app_subnet_gw
  vpc_id     = local.vpc_id_effective
  dns_list   = var.dns_list
}

resource "hcs_vpc_subnet" "data" {
  count      = local.create_data ? 1 : 0
  name       = "${var.name_prefix}-${var.env}-data-subnet"
  cidr       = var.data_subnet_cidr
  gateway_ip = var.data_subnet_gw
  vpc_id     = local.vpc_id_effective
  dns_list   = var.dns_list
}


# ---- Security groups -------------------------------------------------------

resource "hcs_networking_secgroup" "app" {
  count                = local.create_app_sg ? 1 : 0
  name                 = "${var.name_prefix}-${var.env}-app-sg"
  delete_default_rules = false
}

resource "hcs_networking_secgroup" "data" {
  count                = local.create_data_sg ? 1 : 0
  name                 = "${var.name_prefix}-${var.env}-data-sg"
  delete_default_rules = false
}

locals {
  app_sg_id_effective  = local.create_app_sg  ? try(hcs_networking_secgroup.app[0].id,  null) : var.existing_app_sg_id
  data_sg_id_effective = local.create_data_sg ? try(hcs_networking_secgroup.data[0].id, null) : var.existing_data_sg_id
}



# ---- app-sg ingress --------------------------------------------------------

resource "hcs_networking_secgroup_rule" "app_intra_tcp" {
  count             = local.create_app_sg ? 1 : 0
  security_group_id = local.app_sg_id_effective
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 1
  port_range_max    = 65535
  remote_group_id   = local.app_sg_id_effective
}

resource "hcs_networking_secgroup_rule" "app_intra_udp" {
  count             = local.create_app_sg ? 1 : 0
  security_group_id = local.app_sg_id_effective
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "udp"
  port_range_min    = 1
  port_range_max    = 65535
  remote_group_id   = local.app_sg_id_effective
}

resource "hcs_networking_secgroup_rule" "app_intra_icmp" {
  count             = local.create_app_sg ? 1 : 0
  security_group_id = local.app_sg_id_effective
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "icmp"
  remote_group_id   = local.app_sg_id_effective
}

# SSH 2222 from office only.
resource "hcs_networking_secgroup_rule" "app_ssh" {
  count             = local.create_app_sg ? 1 : 0
  security_group_id = local.app_sg_id_effective
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 2222
  port_range_max    = 2222
  remote_ip_prefix  = var.admin_cidr
}

# Public 80/443 — from anywhere (or web_cidr); used by ELB health-checks and
# direct hits when bypassing the LB.
resource "hcs_networking_secgroup_rule" "app_http" {
  count             = local.create_app_sg && (var.enable_web_ingress) ? 1 : 0
  security_group_id = local.app_sg_id_effective
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 80
  port_range_max    = 80
  remote_ip_prefix  = var.web_cidr
}

resource "hcs_networking_secgroup_rule" "app_https" {
  count             = local.create_app_sg && (var.enable_web_ingress) ? 1 : 0
  security_group_id = local.app_sg_id_effective
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 443
  port_range_max    = 443
  remote_ip_prefix  = var.web_cidr
}

# ELB / NAT health checks come from the public subnet CIDR.
resource "hcs_networking_secgroup_rule" "app_from_public_subnet" {
  count             = local.create_app_sg ? 1 : 0
  security_group_id = local.app_sg_id_effective
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 1
  port_range_max    = 65535
  remote_ip_prefix  = var.public_subnet_cidr
}

# ---- data-sg ingress -------------------------------------------------------

resource "hcs_networking_secgroup_rule" "data_intra_tcp" {
  count             = local.create_data_sg ? 1 : 0
  security_group_id = local.data_sg_id_effective
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 1
  port_range_max    = 65535
  remote_group_id   = local.data_sg_id_effective
}

resource "hcs_networking_secgroup_rule" "data_intra_udp" {
  count             = local.create_data_sg ? 1 : 0
  security_group_id = local.data_sg_id_effective
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "udp"
  port_range_min    = 1
  port_range_max    = 65535
  remote_group_id   = local.data_sg_id_effective
}

resource "hcs_networking_secgroup_rule" "data_intra_icmp" {
  count             = local.create_data_sg ? 1 : 0
  security_group_id = local.data_sg_id_effective
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "icmp"
  remote_group_id   = local.data_sg_id_effective
}

# Service ports from app-sg → data-sg.
locals {
  app_to_data_ports = {
    postgres   = { from = 5432, to = 5432 }
    redis      = { from = 6379, to = 6379 }
    nexus      = { from = 8081, to = 8082 }
    vault      = { from = 8200, to = 8200 }
    node_exp   = { from = 9100, to = 9100 }
    vm_select  = { from = 8428, to = 8428 }
    loki       = { from = 3100, to = 3100 }
  }
}

resource "hcs_networking_secgroup_rule" "data_from_app" {
  for_each          = local.create_data_sg ? local.app_to_data_ports : {}
  security_group_id = local.data_sg_id_effective
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = each.value.from
  port_range_max    = each.value.to
  remote_group_id   = local.app_sg_id_effective
}

# SSH 2222 to data VMs — office only.
resource "hcs_networking_secgroup_rule" "data_ssh" {
  count             = local.create_data_sg ? 1 : 0
  security_group_id = local.data_sg_id_effective
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 2222
  port_range_max    = 2222
  remote_ip_prefix  = var.admin_cidr
}

# Grafana on observability EIP from office.
resource "hcs_networking_secgroup_rule" "data_grafana" {
  count             = local.create_data_sg ? 1 : 0
  security_group_id = local.data_sg_id_effective
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 3000
  port_range_max    = 3000
  remote_ip_prefix  = var.admin_cidr
}

# Nexus UI/Docker registry (8081-8082) from office for image pushes.
resource "hcs_networking_secgroup_rule" "data_nexus_admin" {
  count             = local.create_data_sg ? 1 : 0
  security_group_id = local.data_sg_id_effective
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 8081
  port_range_max    = 8082
  remote_ip_prefix  = var.admin_cidr
}

# Public subnet (ELB / NAT) → data VMs full TCP for LB health-checks.
resource "hcs_networking_secgroup_rule" "data_from_public_subnet" {
  count             = local.create_data_sg ? 1 : 0
  security_group_id = local.data_sg_id_effective
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 1
  port_range_max    = 65535
  remote_ip_prefix  = var.public_subnet_cidr
}

# ---- egress (both SGs) -----------------------------------------------------

resource "hcs_networking_secgroup_rule" "app_egress_tcp" {
  count             = local.create_app_sg ? 1 : 0
  security_group_id = local.app_sg_id_effective
  direction         = "egress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 1
  port_range_max    = 65535
  remote_ip_prefix  = "0.0.0.0/0"
}

resource "hcs_networking_secgroup_rule" "app_egress_udp" {
  count             = local.create_app_sg ? 1 : 0
  security_group_id = local.app_sg_id_effective
  direction         = "egress"
  ethertype         = "IPv4"
  protocol          = "udp"
  port_range_min    = 1
  port_range_max    = 65535
  remote_ip_prefix  = "0.0.0.0/0"
}

resource "hcs_networking_secgroup_rule" "data_egress_tcp" {
  count             = local.create_data_sg ? 1 : 0
  security_group_id = local.data_sg_id_effective
  direction         = "egress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 1
  port_range_max    = 65535
  remote_ip_prefix  = "0.0.0.0/0"
}

resource "hcs_networking_secgroup_rule" "data_egress_udp" {
  count             = local.create_data_sg ? 1 : 0
  security_group_id = local.data_sg_id_effective
  direction         = "egress"
  ethertype         = "IPv4"
  protocol          = "udp"
  port_range_min    = 1
  port_range_max    = 65535
  remote_ip_prefix  = "0.0.0.0/0"
}

# ---- outputs ---------------------------------------------------------------

output "vpc_id"           { value = local.vpc_id_effective }
output "public_subnet_id" { value = local.create_public ? try(hcs_vpc_subnet.public[0].id, null) : var.existing_public_subnet_id }
output "app_subnet_id"    { value = local.create_app    ? try(hcs_vpc_subnet.app[0].id,    null) : var.existing_app_subnet_id }
output "data_subnet_id"   { value = local.create_data   ? try(hcs_vpc_subnet.data[0].id,   null) : var.existing_data_subnet_id }

# IPv4 neutron subnet IDs — required by ELB v3 (ipv4_subnet_id) and some other services.
output "public_ipv4_subnet_id" { value = local.create_public ? try(hcs_vpc_subnet.public[0].ipv4_subnet_id, null) : var.existing_public_ipv4_subnet_id }
output "app_ipv4_subnet_id"    { value = local.create_app    ? try(hcs_vpc_subnet.app[0].ipv4_subnet_id,    null) : var.existing_app_ipv4_subnet_id }
output "data_ipv4_subnet_id"   { value = local.create_data   ? try(hcs_vpc_subnet.data[0].ipv4_subnet_id,   null) : var.existing_data_ipv4_subnet_id }

output "app_sg_id"        { value = local.app_sg_id_effective }
output "data_sg_id"       { value = local.data_sg_id_effective }

# Back-compat aliases.
output "subnet_id" { value = local.create_app ? try(hcs_vpc_subnet.app[0].id, null) : var.existing_app_subnet_id }
output "sg_id"     { value = local.app_sg_id_effective }

