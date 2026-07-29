# Sample values. The wizard overwrites this file on every save.
env             = "dev"
project_name    = "opensible-demo"
gcp_project_id  = "REPLACE_ME"
region          = "us-central1"
zone            = "us-central1-a"

subnet_cidr = "10.10.0.0/24"

image         = "ubuntu-os-cloud/ubuntu-2404-lts-amd64"
machine_type  = "e2-small"
disk_size_gb  = 20
app_vm_count  = 0

admin_cidrs        = ["0.0.0.0/0"]
web_cidrs          = ["0.0.0.0/0"]
enable_web_ingress = true

# Add any number of custom rules here. Leave [] to disable.
# custom_ingress_rules = [
#   { description = "App API",      protocol = "tcp", ports = ["8080"],         source_ranges = ["10.0.0.0/8"] },
#   { description = "K8s NodePort", protocol = "udp", ports = ["30000-32767"], source_ranges = ["0.0.0.0/0"] },
# ]
custom_ingress_rules = []

enable_platform = false
platform_roles = {
  postgres      = 1
  redis         = 1
  observability = 1
}

auth_method    = "ssh_key"
ssh_public_key = ""
