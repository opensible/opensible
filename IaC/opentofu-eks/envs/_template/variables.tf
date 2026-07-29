variable "aws_access_key" {
  type        = string
  sensitive   = true
  description = "AWS access key ID."
}

variable "aws_secret_key" {
  type        = string
  sensitive   = true
  description = "AWS secret access key."
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
  default     = "us-east-1"
  description = "AWS region."
}

variable "kubernetes_version" {
  type        = string
  default     = "1.30"
  description = "EKS Kubernetes control plane version (e.g. 1.28, 1.29, 1.30)."
}

# ---------- Networking ----------
variable "vpc_cidr" {
  type    = string
  default = "10.30.0.0/16"
}

variable "public_subnet_cidrs" {
  type        = list(string)
  default     = ["10.30.1.0/24", "10.30.2.0/24"]
  description = "Public subnet CIDRs — one per AZ. Must contain at least 2 for EKS."
}

variable "availability_zones" {
  type        = list(string)
  default     = []
  description = "AZ names (e.g. [\"us-east-1a\",\"us-east-1b\"]). Leave empty to auto-pick the first N AZs in the region."
}

variable "endpoint_public_access" {
  type        = bool
  default     = true
  description = "Expose the EKS control plane API endpoint publicly."
}

variable "endpoint_private_access" {
  type        = bool
  default     = false
  description = "Expose the EKS control plane API endpoint inside the VPC."
}

variable "public_access_cidrs" {
  type        = list(string)
  default     = ["0.0.0.0/0"]
  description = "CIDRs allowed to reach the public EKS API endpoint."
}

# ---------- IAM roles ----------
variable "create_iam_roles" {
  type        = bool
  default     = true
  description = "When true, OpenSible creates the EKS cluster and node IAM roles. When false, provide existing role ARNs."
}

variable "existing_cluster_role_arn" {
  type        = string
  default     = ""
  description = "Existing IAM role ARN for the EKS control plane. Required when create_iam_roles is false."
}

variable "existing_node_role_arn" {
  type        = string
  default     = ""
  description = "Existing IAM role ARN for EKS managed node groups. Required when create_iam_roles is false."
}

# ---------- Primary node group ----------
variable "primary_instance_type" {
  type    = string
  default = "t3.medium"
}

variable "primary_disk_size_gb" {
  type    = number
  default = 50
}

variable "primary_capacity_type" {
  type        = string
  default     = "ON_DEMAND"
  description = "ON_DEMAND or SPOT."
  validation {
    condition     = contains(["ON_DEMAND", "SPOT"], var.primary_capacity_type)
    error_message = "primary_capacity_type must be 'ON_DEMAND' or 'SPOT'."
  }
}

variable "primary_ami_type" {
  type    = string
  default = "AL2023_x86_64_STANDARD"
}

variable "primary_enable_autoscaling" {
  type    = bool
  default = true
}

variable "primary_desired_size" {
  type    = number
  default = 2
}

variable "primary_min_size" {
  type    = number
  default = 1
}

variable "primary_max_size" {
  type    = number
  default = 5
}

# ---------- Extra node groups ----------
variable "extra_node_groups" {
  type = map(object({
    instance_type      = optional(string)
    disk_size_gb       = optional(number)
    capacity_type      = optional(string)
    ami_type           = optional(string)
    desired_size       = optional(number)
    enable_autoscaling = optional(bool)
    min_size           = optional(number)
    max_size           = optional(number)
    labels             = optional(map(string))
  }))
  default = {}
}

variable "labels" {
  type    = map(string)
  default = {}
}
