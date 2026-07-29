variable "api_token" {
  type        = string
  sensitive   = true
  description = "Cloudflare API token. Scopes required depend on enabled features (Zone/DNS/Workers/R2/Access)."
}

variable "account_id" {
  type        = string
  description = "Cloudflare Account ID (from the zone overview sidebar)."
}

variable "env" {
  type        = string
  default     = "dev"
  description = "Short env tag (dev / sit / prod)."
}

variable "project_name" {
  type        = string
  description = "Naming prefix / label on managed resources."
}

variable "zone_name" {
  type        = string
  description = "Root domain (example.com). Used for records, worker routes and Access apps."
}

variable "create_zone" {
  type        = bool
  default     = false
  description = "When true, create the zone in Cloudflare. When false, look up the existing zone."
}

variable "zone_plan" {
  type        = string
  default     = "free"
  description = "Zone plan when create_zone = true."
}

variable "dns_records" {
  type = list(object({
    name    = string
    type    = string
    content = string
    ttl     = optional(number, 1)
    proxied = optional(bool, false)
  }))
  default     = []
  description = "DNS records to manage on the zone."
}

variable "r2_buckets" {
  type = list(object({
    name     = string
    location = optional(string)
  }))
  default     = []
  description = "R2 buckets to provision on the account."
}

variable "workers" {
  type = list(object({
    name    = string
    content = string
    module  = optional(bool, false)
  }))
  default     = []
  description = "Worker scripts to publish on the account."
}

variable "worker_routes" {
  type = list(object({
    pattern     = string
    script_name = string
  }))
  default     = []
  description = "Route patterns on the zone that dispatch to a Worker script."
}

variable "access_apps" {
  type = list(object({
    name             = string
    domain           = string
    session_duration = optional(string, "24h")
    allowed_emails   = optional(list(string), [])
  }))
  default     = []
  description = "Cloudflare Access (Zero Trust) self-hosted applications."
}

variable "labels" {
  type    = map(string)
  default = {}
}
