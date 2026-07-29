variable "hcloud_token" {
  type      = string
  sensitive = true
  description = "Hetzner Cloud API token (Read & Write)."
}

variable "env" {
  type        = string
  default     = "dev"
  description = "Short environment tag (dev / sit / prod)."
}

variable "project_name" {
  type        = string
  description = "Naming prefix for all resources: <project_name>-<env>-<role>."
}

variable "location" {
  type        = string
  default     = "nbg1"
  description = "Hetzner Cloud location (nbg1, fsn1, hel1, ash, hil, sin)."
}

variable "network_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "app_subnet_cidr" {
  type    = string
  default = "10.0.1.0/24"
}

variable "image" {
  type        = string
  default     = "ubuntu-24.04"
  description = "Hetzner image name (ubuntu-24.04, debian-12, etc.)."
}

variable "server_type" {
  type        = string
  default     = "cx22"
  description = "Default Hetzner server type (cx22, cpx11, cax11, ...)."
}

variable "app_vm_count" {
  type    = number
  default = 2
}

variable "ssh_public_key" {
  type        = string
  default     = ""
  description = "Public SSH key (ssh-ed25519 ... or ssh-rsa ...) uploaded to the project."
}

variable "auth_method" {
  type        = string
  default     = "ssh_key"
  description = "VM login method: 'ssh_key' (inject SSH public key) or 'password' (create Linux user via cloud-init)."
  validation {
    condition     = contains(["ssh_key", "password"], var.auth_method)
    error_message = "auth_method must be 'ssh_key' or 'password'."
  }
}

variable "admin_username" {
  type        = string
  default     = "opensible"
  description = "Linux admin user created by cloud-init when auth_method = 'password'."
}

variable "admin_password" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Password for admin_username. Injected via cloud-init user_data. Required when auth_method = 'password'."
}


variable "admin_cidrs" {
  type        = list(string)
  default     = ["0.0.0.0/0"]
  description = "Source CIDRs allowed to reach SSH (port 22)."
}

variable "web_cidrs" {
  type        = list(string)
  default     = ["0.0.0.0/0"]
  description = "Source CIDRs allowed to reach 80/443."
}

variable "enable_web_ingress" {
  type    = bool
  default = true
}

# Free-form ingress rules — lets users open any port(s) to any CIDR list
# without editing this module. Each entry becomes one hcloud_firewall rule.
# Example:
#   custom_ingress_rules = [
#     { description = "App API", protocol = "tcp", port = "8080",       source_ips = ["10.0.0.0/8"] },
#     { description = "PG",      protocol = "tcp", port = "5432",       source_ips = ["203.0.113.10/32"] },
#     { description = "K8s NP",  protocol = "udp", port = "30000-32767", source_ips = ["0.0.0.0/0"] },
#   ]
# `port` is a string so it can be a single port ("22") or a range ("30000-32767").
variable "custom_ingress_rules" {
  type = list(object({
    description = optional(string, "")
    protocol    = optional(string, "tcp")
    port        = optional(string, "")
    source_ips  = optional(list(string), ["0.0.0.0/0", "::/0"])
  }))
  default     = []
  description = "Additional inbound firewall rules. Each entry opens the given port/range over the protocol for source_ips."
}

variable "enable_load_balancer" {
  type    = bool
  default = true
}

variable "load_balancer_type" {
  type    = string
  default = "lb11"
}

variable "enable_platform" {
  type    = bool
  default = false
}

variable "platform_roles" {
  type = map(number)
  default = {
    postgres      = 1
    redis         = 1
    observability = 1
  }
  description = "Platform pool roles → VM count."
}

variable "platform_overrides" {
  type = map(object({
    server_type = optional(string)
    image       = optional(string)
    location    = optional(string)
  }))
  default     = {}
  description = "Per-role platform pool overrides."
}

variable "extra_vms" {
  type = map(object({
    server_type = optional(string)
    image       = optional(string)
    location    = optional(string)
    vm_count    = optional(number, 1)
  }))
  default     = {}
  description = "Ad-hoc extra VMs outside the standard pools."
}

variable "labels" {
  type    = map(string)
  default = {}
}
