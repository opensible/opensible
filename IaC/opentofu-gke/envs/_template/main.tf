locals {
  name_prefix = "${var.project_name}-${var.env}"
  base_labels = merge({
    managed_by = "opensible"
    env        = var.env
    project    = var.project_name
  }, var.labels)

  is_regional = var.cluster_type == "regional"
  location    = local.is_regional ? var.region : coalesce(var.zone, "${var.region}-a")

  # Secondary range names attached to the subnet.
  pods_range_name     = "${local.name_prefix}-pods"
  services_range_name = "${local.name_prefix}-services"

  credentials_service_account_email = try(jsondecode(var.gcp_credentials_json).client_email, "")
  requested_node_service_account    = trimspace(var.node_service_account_email)
  node_service_account_email        = local.requested_node_service_account != "" ? local.requested_node_service_account : local.credentials_service_account_email
  manage_node_act_as_binding        = var.manage_node_service_account_act_as_binding && local.credentials_service_account_email != "" && local.node_service_account_email != ""
}

# ---------- VPC / Subnet with secondary ranges for VPC-native GKE ----------
resource "google_compute_network" "main" {
  name                    = "${local.name_prefix}-vpc"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
}

resource "google_compute_subnetwork" "gke" {
  name          = "${local.name_prefix}-subnet"
  region        = var.region
  network       = google_compute_network.main.id
  ip_cidr_range = var.subnet_cidr

  secondary_ip_range {
    range_name    = local.pods_range_name
    ip_cidr_range = var.pods_cidr
  }
  secondary_ip_range {
    range_name    = local.services_range_name
    ip_cidr_range = var.services_cidr
  }

  private_ip_google_access = true
}

# ---------- Cloud NAT (only meaningful for private nodes) ----------
resource "google_compute_router" "nat" {
  count   = var.enable_private_nodes ? 1 : 0
  name    = "${local.name_prefix}-router"
  region  = var.region
  network = google_compute_network.main.id
}

resource "google_compute_router_nat" "nat" {
  count                              = var.enable_private_nodes ? 1 : 0
  name                               = "${local.name_prefix}-nat"
  region                             = var.region
  router                             = google_compute_router.nat[0].name
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

# The identity represented by gcp_credentials_json must be allowed to attach
# the selected service account to the temporary bootstrap node pool and the
# managed node pools. Managing the binding here avoids the common GKE failure:
# "The user does not have access to service account ...".
resource "google_service_account_iam_member" "node_act_as" {
  count              = local.manage_node_act_as_binding ? 1 : 0
  service_account_id = "projects/${var.gcp_project_id}/serviceAccounts/${local.node_service_account_email}"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${local.credentials_service_account_email}"
}

# ---------- GKE cluster (no default node pool — we manage pools separately) ----------
resource "google_container_cluster" "primary" {
  name     = "${local.name_prefix}-gke"
  location = local.location

  network    = google_compute_network.main.id
  subnetwork = google_compute_subnetwork.gke.id

  remove_default_node_pool = true
  initial_node_count       = 1
  deletion_protection      = false

  release_channel {
    channel = var.release_channel
  }

  min_master_version = var.kubernetes_version != "" ? var.kubernetes_version : null

  ip_allocation_policy {
    cluster_secondary_range_name  = local.pods_range_name
    services_secondary_range_name = local.services_range_name
  }

  dynamic "private_cluster_config" {
    for_each = var.enable_private_nodes ? [1] : []
    content {
      enable_private_nodes    = true
      enable_private_endpoint = false
      master_ipv4_cidr_block  = "172.16.0.0/28"
    }
  }

  master_authorized_networks_config {
    dynamic "cidr_blocks" {
      for_each = var.master_authorized_cidrs
      content {
        cidr_block   = cidr_blocks.value
        display_name = "opensible-${replace(cidr_blocks.value, "/", "-")}"
      }
    }
  }

  workload_identity_config {
    workload_pool = "${var.gcp_project_id}.svc.id.goog"
  }

  # The temporary default node pool created during cluster bootstrap must not
  # fall back to the Compute Engine default service account unless explicitly
  # requested. Using the credential JSON's client_email by default avoids the
  # common GKE error: "user does not have access to service account
  # <project-number>-compute@developer.gserviceaccount.com".
  node_config {
    service_account = local.node_service_account_email != "" ? local.node_service_account_email : null
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  resource_labels = local.base_labels

  depends_on = [google_service_account_iam_member.node_act_as]
}

# ---------- Primary node pool ----------
resource "google_container_node_pool" "primary" {
  name       = "${local.name_prefix}-primary"
  location   = local.location
  cluster    = google_container_cluster.primary.name
  node_count = var.primary_enable_autoscaling ? null : var.primary_node_count

  version = var.kubernetes_version != "" ? var.kubernetes_version : null

  dynamic "autoscaling" {
    for_each = var.primary_enable_autoscaling ? [1] : []
    content {
      min_node_count = var.primary_min_nodes
      max_node_count = var.primary_max_nodes
    }
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    machine_type    = var.primary_machine_type
    disk_size_gb    = var.primary_disk_size_gb
    disk_type       = var.primary_disk_type
    preemptible     = var.primary_preemptible
    service_account = local.node_service_account_email != "" ? local.node_service_account_email : null

    labels       = merge(local.base_labels, { pool = "primary" })
    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"]

    workload_metadata_config {
      mode = "GKE_METADATA"
    }
  }

  depends_on = [google_service_account_iam_member.node_act_as]
}

# ---------- Extra node pools ----------
resource "google_container_node_pool" "extras" {
  for_each = var.extra_node_pools

  name       = "${local.name_prefix}-${each.key}"
  location   = local.location
  cluster    = google_container_cluster.primary.name
  node_count = coalesce(each.value.enable_autoscaling, false) ? null : coalesce(each.value.node_count, 1)

  version = var.kubernetes_version != "" ? var.kubernetes_version : null

  dynamic "autoscaling" {
    for_each = coalesce(each.value.enable_autoscaling, false) ? [1] : []
    content {
      min_node_count = coalesce(each.value.min_nodes, 1)
      max_node_count = coalesce(each.value.max_nodes, 3)
    }
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    machine_type    = coalesce(each.value.machine_type, var.primary_machine_type)
    disk_size_gb    = coalesce(each.value.disk_size_gb, var.primary_disk_size_gb)
    disk_type       = coalesce(each.value.disk_type, var.primary_disk_type)
    preemptible     = coalesce(each.value.preemptible, false)
    service_account = local.node_service_account_email != "" ? local.node_service_account_email : null

    labels       = merge(local.base_labels, { pool = each.key }, coalesce(each.value.labels, {}))
    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"]

    workload_metadata_config {
      mode = "GKE_METADATA"
    }
  }

  depends_on = [google_service_account_iam_member.node_act_as]
}

# ---------- Outputs ----------
output "cluster_name" {
  value = google_container_cluster.primary.name
}

output "cluster_endpoint" {
  value     = google_container_cluster.primary.endpoint
  sensitive = true
}

output "cluster_ca_certificate" {
  value     = google_container_cluster.primary.master_auth[0].cluster_ca_certificate
  sensitive = true
}

output "cluster_location" {
  value = google_container_cluster.primary.location
}

output "kubectl_config_command" {
  value = "gcloud container clusters get-credentials ${google_container_cluster.primary.name} --${local.is_regional ? "region" : "zone"} ${local.location} --project ${var.gcp_project_id}"
}

output "vpc_id" {
  value = google_compute_network.main.id
}

output "subnet_id" {
  value = google_compute_subnetwork.gke.id
}
