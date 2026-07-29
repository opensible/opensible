variable "gcp_credentials_json" {
  type        = string
  sensitive   = true
  description = "GCP service account JSON key (full file contents)."
}

variable "gcp_project_id" {
  type        = string
  description = "The Google Cloud project id."
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

variable "region" {
  type        = string
  default     = "us-central1"
  description = "GCP region."
}

variable "zone" {
  type        = string
  default     = "us-central1-a"
  description = "GCP zone. VMs are created here."
}

variable "subnet_cidr" {
  type    = string
  default = "10.10.0.0/24"
}

variable "image" {
  type        = string
  default     = "ubuntu-os-cloud/ubuntu-2404-lts-amd64"
  description = "GCE image in '<project>/<family-or-image>' form."
}

variable "machine_type" {
  type        = string
  default     = "e2-small"
  description = "Default GCE machine type."
}

variable "disk_size_gb" {
  type    = number
  default = 20
}

variable "app_vm_count" {
  type    = number
  default = 0
}

variable "ssh_public_key" {
  type        = string
  default     = ""
  description = "Public SSH key. Attached to instances via ssh-keys metadata when auth_method='ssh_key'."
}

variable "auth_method" {
  type        = string
  default     = "ssh_key"
  description = "VM login method: 'ssh_key' (metadata) or 'password' (cloud-init user)."
  validation {
    condition     = contains(["ssh_key", "password"], var.auth_method)
    error_message = "auth_method must be 'ssh_key' or 'password'."
  }
}

variable "admin_username" {
  type        = string
  default     = "opensible"
  description = "Linux admin user."
}

variable "admin_password" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Password for admin_username when auth_method='password'."
}

variable "admin_cidrs" {
  type    = list(string)
  default = ["0.0.0.0/0"]
}

variable "web_cidrs" {
  type    = list(string)
  default = ["0.0.0.0/0"]
}

variable "enable_web_ingress" {
  type    = bool
  default = true
}

# Free-form firewall ingress rules. Each entry becomes one
# google_compute_firewall resource on the main VPC, targeted at the same
# network tag as the built-in SSH / web rules. Example:
#   custom_ingress_rules = [
#     { description = "App API",      protocol = "tcp",  ports = ["8080"],         source_ranges = ["10.0.0.0/8"] },
#     { description = "K8s NodePort", protocol = "udp",  ports = ["30000-32767"], source_ranges = ["0.0.0.0/0"] },
#     { description = "Ping",         protocol = "icmp", ports = [],              source_ranges = ["0.0.0.0/0"] },
#   ]
variable "custom_ingress_rules" {
  type = list(object({
    description   = optional(string, "")
    protocol      = optional(string, "tcp")
    ports         = optional(list(string), [])
    source_ranges = optional(list(string), ["0.0.0.0/0"])
  }))
  default     = []
  description = "Additional ingress firewall rules on the main VPC."
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
}

variable "platform_overrides" {
  type = map(object({
    machine_type = optional(string)
    image        = optional(string)
    disk_size_gb = optional(number)
  }))
  default = {}
}

variable "extra_vms" {
  type = map(object({
    machine_type = optional(string)
    image        = optional(string)
    disk_size_gb = optional(number)
    vm_count     = optional(number)
  }))
  default = {}
}

variable "labels" {
  type    = map(string)
  default = {}
}
