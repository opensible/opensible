locals {
  name_prefix = "${var.project_name}-${var.env}"
  base_labels = merge({
    managed_by = "opensible"
    env        = var.env
    project    = var.project_name
  }, var.labels)
}

# ---------- Zone: create OR reference existing ----------
resource "cloudflare_zone" "this" {
  count      = var.create_zone ? 1 : 0
  account_id = var.account_id
  zone       = var.zone_name
  plan       = var.zone_plan
}

data "cloudflare_zone" "existing" {
  count      = var.create_zone ? 0 : 1
  name       = var.zone_name
  account_id = var.account_id
}

locals {
  zone_id = var.create_zone ? cloudflare_zone.this[0].id : data.cloudflare_zone.existing[0].id
}

# ---------- DNS records ----------
resource "cloudflare_record" "records" {
  for_each = { for r in var.dns_records : "${r.type}-${r.name}" => r }
  zone_id  = local.zone_id
  name     = each.value.name
  type     = each.value.type
  content  = each.value.content
  ttl      = coalesce(each.value.ttl, 1)
  proxied  = coalesce(each.value.proxied, false)
}

# ---------- R2 buckets ----------
resource "cloudflare_r2_bucket" "buckets" {
  for_each   = { for b in var.r2_buckets : b.name => b }
  account_id = var.account_id
  name       = each.value.name
  location   = try(each.value.location, null)
}

# ---------- Workers ----------
resource "cloudflare_worker_script" "scripts" {
  for_each   = { for w in var.workers : w.name => w }
  account_id = var.account_id
  name       = each.value.name
  content    = each.value.content
  module     = coalesce(each.value.module, false)
}

resource "cloudflare_worker_route" "routes" {
  for_each    = { for r in var.worker_routes : r.pattern => r }
  zone_id     = local.zone_id
  pattern     = each.value.pattern
  script_name = each.value.script_name

  depends_on = [cloudflare_worker_script.scripts]
}

# ---------- Zero Trust / Access ----------
resource "cloudflare_access_application" "apps" {
  for_each         = { for a in var.access_apps : a.name => a }
  zone_id          = local.zone_id
  name             = each.value.name
  domain           = each.value.domain
  session_duration = coalesce(each.value.session_duration, "24h")
  type             = "self_hosted"
}

resource "cloudflare_access_policy" "app_allow" {
  for_each       = { for a in var.access_apps : a.name => a if length(coalesce(a.allowed_emails, [])) > 0 }
  application_id = cloudflare_access_application.apps[each.key].id
  zone_id        = local.zone_id
  name           = "${each.value.name}-allow"
  precedence     = 1
  decision       = "allow"

  include {
    email = each.value.allowed_emails
  }
}

# ---------- Outputs ----------
output "zone_id" {
  value = local.zone_id
}

output "zone_name" {
  value = var.zone_name
}

output "managed_records" {
  value = { for k, r in cloudflare_record.records : k => {
    hostname = r.hostname
    type     = r.type
    content  = r.content
    proxied  = r.proxied
  } }
}

output "r2_buckets" {
  value = { for k, b in cloudflare_r2_bucket.buckets : k => b.name }
}

output "workers" {
  value = [for w in cloudflare_worker_script.scripts : w.name]
}

output "access_apps" {
  value = { for k, a in cloudflare_access_application.apps : k => a.domain }
}
