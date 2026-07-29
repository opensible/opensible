# Private DNS zone for internal service discovery + optional public A records
# pointing at the ELB EIP. Matches group_vars/all.yml domain_base = example.com.

variable "env"         { type = string }
variable "vpc_id"      { type = string }
variable "domain_base" {
  type    = string
  default = "example.com"
}

# A-records: name (without env suffix) => private IP.
# Example: { "postgres" = "10.0.250.10", "nexus" = "10.0.250.10" }
variable "private_records" {
  type    = map(string)
  default = {}
}

# Public A records on the ELB EIP. name => ip.
# Example: { "portal" = "203.0.113.10" }
variable "public_records" {
  type    = map(string)
  default = {}
}

# HCS public DNS zone API is not available in all regions (returns
# DNS.0204 "Invalid zone type"). Default OFF — public DNS is typically
# registered with the corporate DNS provider, not HCS clouddns.
variable "enable_public_zone" {
  type    = bool
  default = false
}

locals {
  private_zone = "${var.env}.${var.domain_base}."          # e.g. dev.example.com.
  public_zone  = var.domain_base                            # e.g. example.com
}

resource "hcs_dns_zone" "private" {
  name        = local.private_zone
  description = "DBCS ${var.env} private zone"
  zone_type   = "private"
  router {
    router_id = var.vpc_id
  }
}

resource "hcs_dns_recordset" "private" {
  for_each    = var.private_records
  zone_id     = hcs_dns_zone.private.id
  name        = "${each.key}.${local.private_zone}"
  type        = "A"
  ttl         = 300
  records     = [each.value]
}

# Public records — only created if enable_public_zone is true AND records exist.
resource "hcs_dns_zone" "public" {
  count       = var.enable_public_zone && length(var.public_records) > 0 ? 1 : 0
  name        = "${local.public_zone}."
  description = "DBCS public zone"
  zone_type   = "public"
}

resource "hcs_dns_recordset" "public" {
  for_each = var.enable_public_zone ? var.public_records : {}
  zone_id  = hcs_dns_zone.public[0].id
  name     = "${each.key}.${local.public_zone}."
  type     = "A"
  ttl      = 300
  records  = [each.value]
}

output "private_zone_id" { value = hcs_dns_zone.private.id }
output "private_zone"    { value = local.private_zone }
