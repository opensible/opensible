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

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "subnet_cidr" {
  type    = string
  default = "10.0.1.0/24"
}

variable "ami_id" {
  type        = string
  default     = ""
  description = "Explicit AMI ID. Leave empty to auto-select the latest Canonical Ubuntu 24.04 LTS x86_64 AMI in the region."
}

variable "instance_type" {
  type        = string
  default     = "t3.micro"
  description = "Default EC2 instance type (t3.micro, t3.small, m5.large, ...)."
}

variable "app_vm_count" {
  type    = number
  default = 1
}

variable "ssh_public_key" {
  type        = string
  default     = ""
  description = "Public SSH key. Uploaded as an aws_key_pair and attached to instances when auth_method = 'ssh_key'."
}

variable "auth_method" {
  type        = string
  default     = "ssh_key"
  description = "VM login method: 'ssh_key' (aws_key_pair) or 'password' (cloud-init user)."
  validation {
    condition     = contains(["ssh_key", "password"], var.auth_method)
    error_message = "auth_method must be 'ssh_key' or 'password'."
  }
}

variable "admin_username" {
  type        = string
  default     = "opensible"
  description = "Linux admin user created via cloud-init when auth_method = 'password'."
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
  description = "Source CIDRs allowed on SSH (port 22)."
}

variable "web_cidrs" {
  type        = list(string)
  default     = ["0.0.0.0/0"]
  description = "Source CIDRs allowed on 80/443."
}

variable "enable_web_ingress" {
  type    = bool
  default = true
}

# Free-form ingress rules — lets users open any port(s) to any CIDR list
# without having to modify the module. Each entry becomes one ingress block
# on the main security group. Example:
#   custom_ingress_rules = [
#     { description = "App API",    protocol = "tcp",  from_port = 8080, to_port = 8080, cidr_blocks = ["10.0.0.0/8"] },
#     { description = "PG replica", protocol = "tcp",  from_port = 5432, to_port = 5432, cidr_blocks = ["203.0.113.10/32"] },
#     { description = "UDP range",  protocol = "udp",  from_port = 30000, to_port = 32767, cidr_blocks = ["0.0.0.0/0"] },
#   ]
variable "custom_ingress_rules" {
  type = list(object({
    description = optional(string, "")
    protocol    = optional(string, "tcp")
    from_port   = number
    to_port     = number
    cidr_blocks = optional(list(string), ["0.0.0.0/0"])
  }))
  default     = []
  description = "Additional ingress rules on the main security group. Each entry opens from_port..to_port over protocol for the given CIDRs."
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
    instance_type = optional(string)
    ami_id        = optional(string)
  }))
  default     = {}
  description = "Per-role platform pool overrides."
}

variable "extra_vms" {
  type = map(object({
    instance_type = optional(string)
    ami_id        = optional(string)
    vm_count      = optional(number, 1)
  }))
  default     = {}
  description = "Ad-hoc extra VMs outside the standard pools."
}

variable "labels" {
  type    = map(string)
  default = {}
}

# ---------- Application Load Balancer ----------
variable "enable_alb" {
  type        = bool
  default     = false
  description = "Provision an Application Load Balancer in front of the App pool."
}

variable "alb_internal" {
  type        = bool
  default     = false
  description = "If true, ALB is internal (no public IPs)."
}

variable "alb_target_port" {
  type        = number
  default     = 80
  description = "Backend port on the App instances that the ALB target group forwards to."
}

variable "alb_health_check_path" {
  type        = string
  default     = "/"
  description = "HTTP path used for ALB target health checks."
}

variable "alb_certificate_arn" {
  type        = string
  default     = ""
  description = "ACM certificate ARN. When set, ALB also listens on 443 (HTTPS)."
}

variable "subnet_cidr_b" {
  type        = string
  default     = "10.0.2.0/24"
  description = "Second subnet CIDR (different AZ). Created automatically when ALB or RDS is enabled."
}

# ---------- Amazon RDS ----------
variable "enable_rds" {
  type        = bool
  default     = false
  description = "Provision a managed Amazon RDS database instance."
}

variable "rds_engine" {
  type        = string
  default     = "postgres"
  description = "RDS engine: 'postgres' or 'mysql'."
  validation {
    condition     = contains(["postgres", "mysql"], var.rds_engine)
    error_message = "rds_engine must be 'postgres' or 'mysql'."
  }
}

variable "rds_engine_version" {
  type        = string
  default     = ""
  description = "Optional engine version (e.g. '16.4' for postgres, '8.0' for mysql). Empty = AWS default."
}

variable "rds_instance_class" {
  type        = string
  default     = "db.t3.micro"
  description = "RDS instance class (db.t3.micro, db.t3.small, db.m5.large, ...)."
}

variable "rds_allocated_storage" {
  type        = number
  default     = 20
  description = "Allocated storage in GB."
}

variable "rds_db_name" {
  type        = string
  default     = "appdb"
  description = "Initial database name created inside the RDS instance."
}

variable "rds_username" {
  type        = string
  default     = "opensible"
  description = "Master username for the RDS instance."
}

variable "rds_password" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Master password for the RDS instance. Required when enable_rds = true."
}

variable "rds_publicly_accessible" {
  type        = bool
  default     = false
  description = "Whether the RDS instance is reachable from outside the VPC."
}

variable "rds_skip_final_snapshot" {
  type        = bool
  default     = true
  description = "Skip the final snapshot on destroy. Set false for production."
}

# ---------- Amazon S3 ----------
variable "enable_s3" {
  type        = bool
  default     = false
  description = "Provision an S3 bucket for backups, logs and assets."
}

variable "s3_bucket_name" {
  type        = string
  default     = ""
  description = "Globally-unique S3 bucket name. Required when enable_s3 = true."
}

variable "s3_versioning" {
  type        = bool
  default     = true
  description = "Enable object versioning on the bucket."
}

variable "s3_block_public_access" {
  type        = bool
  default     = true
  description = "Block all public access on the bucket (recommended)."
}

