# Sample values. The wizard overwrites this file on every save.
env          = "dev"
project_name = "opensible-demo"
region       = "us-east-1"

vpc_cidr    = "10.0.0.0/16"
subnet_cidr = "10.0.1.0/24"

ami_id        = ""
instance_type = "t3.micro"
app_vm_count  = 1

admin_cidrs        = ["0.0.0.0/0"]
web_cidrs          = ["0.0.0.0/0"]
enable_web_ingress = true

# Add any extra ports/CIDRs here. Empty list = no extra rules.
# Example:
#   custom_ingress_rules = [
#     { description = "App API", protocol = "tcp", from_port = 8080, to_port = 8080, cidr_blocks = ["10.0.0.0/8"] },
#   ]
custom_ingress_rules = []

enable_platform = false
platform_roles = {
  postgres      = 1
  redis         = 1
  observability = 1
}

auth_method    = "ssh_key"
ssh_public_key = ""

# ---- Application Load Balancer ----
enable_alb            = false
alb_internal          = false
alb_target_port       = 80
alb_health_check_path = "/"
alb_certificate_arn   = ""
subnet_cidr_b         = "10.0.2.0/24"

# ---- Amazon RDS ----
enable_rds              = false
rds_engine              = "postgres"
rds_engine_version      = ""
rds_instance_class      = "db.t3.micro"
rds_allocated_storage   = 20
rds_db_name             = "appdb"
rds_username            = "opensible"
rds_publicly_accessible = false
rds_skip_final_snapshot = true

# ---- Amazon S3 ----
enable_s3              = false
s3_bucket_name         = ""
s3_versioning          = true
s3_block_public_access = true
