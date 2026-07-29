locals {
  name_prefix = "${var.project_name}-${var.env}"
  base_labels = merge({
    managed_by = "opensible"
    env        = var.env
    project    = var.project_name
  }, var.labels)

  use_password_auth = var.auth_method == "password" && var.admin_password != ""
  use_ssh_key_auth  = var.auth_method == "ssh_key" && var.ssh_public_key != ""

  user_data_password = <<-EOT
    #cloud-config
    users:
      - name: ${var.admin_username}
        groups: sudo
        shell: /bin/bash
        sudo: ['ALL=(ALL) NOPASSWD:ALL']
        lock_passwd: false
        plain_text_passwd: ${jsonencode(var.admin_password)}
    ssh_pwauth: true
    chpasswd:
      expire: false
      list: |
        ${var.admin_username}:${var.admin_password}
    runcmd:
      - [ sh, -c, "sed -ri 's/^#?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config" ]
      - [ sh, -c, "sed -ri 's/^#?KbdInteractiveAuthentication.*/KbdInteractiveAuthentication yes/' /etc/ssh/sshd_config || true" ]
      - [ sh, -c, "printf 'PasswordAuthentication yes\\nKbdInteractiveAuthentication yes\\nChallengeResponseAuthentication yes\\n' > /etc/ssh/sshd_config.d/60-cloudimg-settings.conf" ]
      - [ sh, -c, "printf 'PasswordAuthentication yes\\nKbdInteractiveAuthentication yes\\n' > /etc/ssh/sshd_config.d/99-opensible.conf" ]
      - [ sh, -c, "systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null || true" ]
  EOT

  vm_metadata = merge(
    local.use_ssh_key_auth ? { "ssh-keys" = "${var.admin_username}:${var.ssh_public_key}" } : {},
    local.use_password_auth ? {
      "user-data"     = local.user_data_password
      "enable-oslogin" = "FALSE"
    } : {},
  )


  platform_servers = var.enable_platform ? merge([
    for role, count in var.platform_roles : {
      for i in range(count) :
      "${role}-${i + 1}" => {
        role         = role
        machine_type = try(var.platform_overrides[role].machine_type, null) != null ? var.platform_overrides[role].machine_type : var.machine_type
        image        = try(var.platform_overrides[role].image, null)        != null ? var.platform_overrides[role].image        : var.image
        disk_size_gb = try(var.platform_overrides[role].disk_size_gb, null) != null ? var.platform_overrides[role].disk_size_gb : var.disk_size_gb
      }
    }
  ]...) : {}

  extra_servers = merge([
    for name, cfg in var.extra_vms : {
      for i in range(coalesce(cfg.vm_count, 1)) :
      "${name}-${i + 1}" => {
        role         = name
        machine_type = coalesce(cfg.machine_type, var.machine_type)
        image        = coalesce(cfg.image, var.image)
        disk_size_gb = coalesce(cfg.disk_size_gb, var.disk_size_gb)
      }
    }
  ]...)
}

# ---------- VPC / Subnet ----------
resource "google_compute_network" "main" {
  name                    = "${local.name_prefix}-vpc"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
}

resource "google_compute_subnetwork" "app" {
  name          = "${local.name_prefix}-subnet"
  region        = var.region
  network       = google_compute_network.main.id
  ip_cidr_range = var.subnet_cidr
}

# ---------- Firewall rules ----------
resource "google_compute_firewall" "ssh" {
  name          = "${local.name_prefix}-ssh"
  network       = google_compute_network.main.name
  direction     = "INGRESS"
  source_ranges = var.admin_cidrs
  target_tags   = ["${local.name_prefix}"]
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

resource "google_compute_firewall" "web" {
  count         = var.enable_web_ingress ? 1 : 0
  name          = "${local.name_prefix}-web"
  network       = google_compute_network.main.name
  direction     = "INGRESS"
  source_ranges = var.web_cidrs
  target_tags   = ["${local.name_prefix}"]
  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }
}

resource "google_compute_firewall" "icmp" {
  name          = "${local.name_prefix}-icmp"
  network       = google_compute_network.main.name
  direction     = "INGRESS"
  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["${local.name_prefix}"]
  allow {
    protocol = "icmp"
  }
}

# User-defined ingress rules from var.custom_ingress_rules — one firewall
# resource per entry so users can open any port / protocol / CIDR combo
# without editing this module.
resource "google_compute_firewall" "custom" {
  for_each = {
    for idx, rule in var.custom_ingress_rules :
    format("%03d-%s", idx, replace(lower(coalesce(rule.description, "rule")), "/[^a-z0-9-]/", "-")) => rule
  }

  name          = substr("${local.name_prefix}-c-${each.key}", 0, 62)
  network       = google_compute_network.main.name
  direction     = "INGRESS"
  source_ranges = each.value.source_ranges
  target_tags   = ["${local.name_prefix}"]
  description   = each.value.description

  allow {
    protocol = each.value.protocol
    ports    = lower(each.value.protocol) == "icmp" ? null : each.value.ports
  }
}


# ---------- App pool ----------
resource "google_compute_instance" "app" {
  count        = var.app_vm_count
  name         = "${local.name_prefix}-app-${count.index + 1}"
  machine_type = var.machine_type
  zone         = var.zone
  tags         = ["${local.name_prefix}"]
  labels       = merge(local.base_labels, { role = "app" })

  boot_disk {
    initialize_params {
      image = var.image
      size  = var.disk_size_gb
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.app.id
    access_config {}
  }

  metadata = local.vm_metadata
}

# ---------- Platform pool ----------
resource "google_compute_instance" "platform" {
  for_each     = local.platform_servers
  name         = "${local.name_prefix}-${each.key}"
  machine_type = each.value.machine_type
  zone         = var.zone
  tags         = ["${local.name_prefix}"]
  labels       = merge(local.base_labels, { role = each.value.role })

  boot_disk {
    initialize_params {
      image = each.value.image
      size  = each.value.disk_size_gb
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.app.id
    access_config {}
  }

  metadata = local.vm_metadata
}

# ---------- Extra VMs ----------
resource "google_compute_instance" "extras" {
  for_each     = local.extra_servers
  name         = "${local.name_prefix}-${each.key}"
  machine_type = each.value.machine_type
  zone         = var.zone
  tags         = ["${local.name_prefix}"]
  labels       = merge(local.base_labels, { role = each.value.role })

  boot_disk {
    initialize_params {
      image = each.value.image
      size  = each.value.disk_size_gb
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.app.id
    access_config {}
  }

  metadata = local.vm_metadata
}

# ---------- Outputs ----------
output "app_public_ips" {
  value = { for s in google_compute_instance.app :
    s.name => try(s.network_interface[0].access_config[0].nat_ip, null)
  }
}

output "platform_public_ips" {
  value = { for k, s in google_compute_instance.platform :
    s.name => try(s.network_interface[0].access_config[0].nat_ip, null)
  }
}

output "extra_public_ips" {
  value = { for k, s in google_compute_instance.extras :
    s.name => try(s.network_interface[0].access_config[0].nat_ip, null)
  }
}

output "vpc_id" {
  value = google_compute_network.main.id
}

output "subnet_id" {
  value = google_compute_subnetwork.app.id
}
