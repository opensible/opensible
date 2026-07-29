variable "gcp_credentials_json" {
  type        = string
  sensitive   = true
  description = "GCP service account JSON key (full file contents)."
}

variable "gcp_project_id" {
  type        = string
  description = "The Google Cloud project id."
}

variable "node_service_account_email" {
  type        = string
  default     = ""
  description = "Optional service account email for GKE nodes. Empty = use the client_email from gcp_credentials_json. The provisioning service account must have roles/iam.serviceAccountUser on this service account."
}

variable "manage_node_service_account_act_as_binding" {
  type        = bool
  default     = true
  description = "When true, OpenTofu grants the provisioning service account roles/iam.serviceAccountUser on the selected GKE node service account before creating the cluster. Disable only if your organization manages this IAM binding separately."
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
  description = "GCP region. Regional clusters spread nodes across zones; zonal clusters pin them to `zone`."
}

variable "zone" {
  type        = string
  default     = ""
  description = "Zone for zonal clusters (leave empty for a regional cluster in `region`)."
}

variable "cluster_type" {
  type        = string
  default     = "regional"
  description = "'regional' (recommended) or 'zonal'."
  validation {
    condition     = contains(["regional", "zonal"], var.cluster_type)
    error_message = "cluster_type must be 'regional' or 'zonal'."
  }
}

variable "kubernetes_version" {
  type        = string
  default     = ""
  description = "GKE master/node version. Empty = GKE picks the default stable channel version."
}

variable "release_channel" {
  type        = string
  default     = "REGULAR"
  description = "GKE release channel: RAPID, REGULAR, STABLE or UNSPECIFIED."
}

# ---------- Networking ----------
variable "subnet_cidr" {
  type    = string
  default = "10.20.0.0/22"
}

variable "pods_cidr" {
  type        = string
  default     = "10.24.0.0/14"
  description = "Secondary range for Pod IPs (VPC-native / alias IP)."
}

variable "services_cidr" {
  type        = string
  default     = "10.28.0.0/20"
  description = "Secondary range for Service IPs."
}

variable "master_authorized_cidrs" {
  type        = list(string)
  default     = ["0.0.0.0/0"]
  description = "CIDRs allowed to reach the GKE control plane."
}

variable "enable_private_nodes" {
  type    = bool
  default = false
}

# ---------- Primary node pool ----------
variable "primary_machine_type" {
  type    = string
  default = "e2-standard-2"
}

variable "primary_disk_size_gb" {
  type    = number
  default = 50
}

variable "primary_disk_type" {
  type    = string
  default = "pd-balanced"
}

variable "primary_node_count" {
  type        = number
  default     = 2
  description = "Node count per zone (regional cluster multiplies this by 3)."
}

variable "primary_enable_autoscaling" {
  type    = bool
  default = true
}

variable "primary_min_nodes" {
  type    = number
  default = 1
}

variable "primary_max_nodes" {
  type    = number
  default = 5
}

variable "primary_preemptible" {
  type    = bool
  default = false
}

# ---------- Extra node pools ----------
variable "extra_node_pools" {
  type = map(object({
    machine_type       = optional(string)
    disk_size_gb       = optional(number)
    disk_type          = optional(string)
    node_count         = optional(number)
    enable_autoscaling = optional(bool)
    min_nodes          = optional(number)
    max_nodes          = optional(number)
    preemptible        = optional(bool)
    labels             = optional(map(string))
  }))
  default = {}
}

variable "labels" {
  type    = map(string)
  default = {}
}
