# Sample values. The wizard overwrites this file on every save.
env          = "dev"
project_name = "opensible-demo"
location     = "nbg1"

network_cidr    = "10.0.0.0/16"
app_subnet_cidr = "10.0.1.0/24"

image        = "ubuntu-24.04"
server_type  = "cx22"
app_vm_count = 2

enable_load_balancer = true
enable_web_ingress   = true
admin_cidrs          = ["0.0.0.0/0"]
web_cidrs            = ["0.0.0.0/0"]

# Extra firewall rules. `port` is a string — single ("22") or range ("30000-32767").
# Example:
#   custom_ingress_rules = [
#     { description = "App API", protocol = "tcp", port = "8080", source_ips = ["10.0.0.0/8"] },
#   ]
custom_ingress_rules = []

enable_platform = false
platform_roles = {
  postgres      = 1
  redis         = 1
  observability = 1
}

ssh_public_key = ""
