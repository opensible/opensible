# Sample values. The wizard overwrites this file on every save.
env                        = "dev"
project_name               = "opensible-demo"
gcp_project_id             = "REPLACE_ME"
node_service_account_email = ""
manage_node_service_account_act_as_binding = true
region                     = "us-central1"
zone                       = ""
cluster_type               = "regional"

kubernetes_version = ""
release_channel    = "REGULAR"

subnet_cidr             = "10.20.0.0/22"
pods_cidr               = "10.24.0.0/14"
services_cidr           = "10.28.0.0/20"
master_authorized_cidrs = ["0.0.0.0/0"]
enable_private_nodes    = false

primary_machine_type       = "e2-standard-2"
primary_disk_size_gb       = 50
primary_disk_type          = "pd-balanced"
primary_node_count         = 2
primary_enable_autoscaling = true
primary_min_nodes          = 1
primary_max_nodes          = 5
primary_preemptible        = false

extra_node_pools = {}
labels           = {}
