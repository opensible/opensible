locals {
  name_prefix = "${var.project_name}-${var.env}"
  base_labels = merge({
    managed_by = "opensible"
    env        = var.env
    project    = var.project_name
  }, var.labels)

  platform_servers = var.enable_platform ? merge([
    for role, count in var.platform_roles : {
      for i in range(count) :
      "${role}-${i + 1}" => {
        role        = role
        server_type = try(var.platform_overrides[role].server_type, null) != null ? var.platform_overrides[role].server_type : var.server_type
        image       = try(var.platform_overrides[role].image, null)       != null ? var.platform_overrides[role].image       : var.image
        location    = try(var.platform_overrides[role].location, null)    != null ? var.platform_overrides[role].location    : var.location
      }
    }
  ]...) : {}

  extra_servers = merge([
    for name, cfg in var.extra_vms : {
      for i in range(coalesce(cfg.vm_count, 1)) :
      "${name}-${i + 1}" => {
        role        = name
        server_type = coalesce(cfg.server_type, var.server_type)
        image       = coalesce(cfg.image, var.image)
        location    = coalesce(cfg.location, var.location)
      }
    }
  ]...)

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

  vm_user_data = local.use_password_auth ? local.user_data_password : null
  vm_ssh_keys  = local.use_ssh_key_auth ? hcloud_ssh_key.main[*].id : []
}

# ---------- SSH key ----------
resource "hcloud_ssh_key" "main" {
  count      = local.use_ssh_key_auth ? 1 : 0
  name       = "${local.name_prefix}-key"
  public_key = var.ssh_public_key
  labels     = local.base_labels
}


# ---------- Network ----------
resource "hcloud_network" "main" {
  name     = "${local.name_prefix}-net"
  ip_range = var.network_cidr
  labels   = local.base_labels
}

resource "hcloud_network_subnet" "app" {
  network_id   = hcloud_network.main.id
  type         = "cloud"
  network_zone = "eu-central"
  ip_range     = var.app_subnet_cidr
}

# ---------- Firewall ----------
resource "hcloud_firewall" "main" {
  name   = "${local.name_prefix}-fw"
  labels = local.base_labels

  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "22"
    source_ips = var.admin_cidrs
  }

  dynamic "rule" {
    for_each = var.enable_web_ingress ? [80, 443] : []
    content {
      direction  = "in"
      protocol   = "tcp"
      port       = tostring(rule.value)
      source_ips = var.web_cidrs
    }
  }

  rule {
    direction  = "in"
    protocol   = "icmp"
    source_ips = ["0.0.0.0/0", "::/0"]
  }

  # User-defined ingress rules from var.custom_ingress_rules.
  dynamic "rule" {
    for_each = var.custom_ingress_rules
    content {
      direction   = "in"
      description = try(rule.value.description, "")
      protocol    = try(rule.value.protocol, "tcp")
      port        = contains(["tcp", "udp"], try(rule.value.protocol, "tcp")) ? rule.value.port : null
      source_ips  = try(rule.value.source_ips, ["0.0.0.0/0", "::/0"])
    }
  }
}

# ---------- App pool ----------
resource "hcloud_server" "app" {
  count       = var.app_vm_count
  name        = "${local.name_prefix}-app-${count.index + 1}"
  server_type = var.server_type
  image       = var.image
  location    = var.location
  ssh_keys    = local.vm_ssh_keys
  user_data   = local.vm_user_data
  firewall_ids = [hcloud_firewall.main.id]
  labels = merge(local.base_labels, { role = "app" })

  network {
    network_id = hcloud_network.main.id
  }

  depends_on = [hcloud_network_subnet.app]
}

# ---------- Platform pool ----------
resource "hcloud_server" "platform" {
  for_each     = local.platform_servers
  name         = "${local.name_prefix}-${each.key}"
  server_type  = each.value.server_type
  image        = each.value.image
  location     = each.value.location
  ssh_keys     = local.vm_ssh_keys
  user_data    = local.vm_user_data
  firewall_ids = [hcloud_firewall.main.id]
  labels       = merge(local.base_labels, { role = each.value.role })

  network {
    network_id = hcloud_network.main.id
  }

  depends_on = [hcloud_network_subnet.app]
}

# ---------- Extra VMs ----------
resource "hcloud_server" "extras" {
  for_each     = local.extra_servers
  name         = "${local.name_prefix}-${each.key}"
  server_type  = each.value.server_type
  image        = each.value.image
  location     = each.value.location
  ssh_keys     = local.vm_ssh_keys
  user_data    = local.vm_user_data
  firewall_ids = [hcloud_firewall.main.id]
  labels       = merge(local.base_labels, { role = each.value.role })

  network {
    network_id = hcloud_network.main.id
  }

  depends_on = [hcloud_network_subnet.app]
}

# ---------- Load balancer (public) ----------
resource "hcloud_load_balancer" "main" {
  count              = var.enable_load_balancer ? 1 : 0
  name               = "${local.name_prefix}-lb"
  load_balancer_type = var.load_balancer_type
  location           = var.location
  labels             = local.base_labels
}

resource "hcloud_load_balancer_network" "main" {
  count            = var.enable_load_balancer ? 1 : 0
  load_balancer_id = hcloud_load_balancer.main[0].id
  network_id       = hcloud_network.main.id
  depends_on       = [hcloud_network_subnet.app]
}

resource "hcloud_load_balancer_target" "app" {
  count            = var.enable_load_balancer ? var.app_vm_count : 0
  load_balancer_id = hcloud_load_balancer.main[0].id
  type             = "server"
  server_id        = hcloud_server.app[count.index].id
  use_private_ip   = true
  depends_on       = [hcloud_load_balancer_network.main]
}

resource "hcloud_load_balancer_service" "http" {
  count            = var.enable_load_balancer && var.enable_web_ingress ? 1 : 0
  load_balancer_id = hcloud_load_balancer.main[0].id
  protocol         = "http"
  listen_port      = 80
  destination_port = 80
}

# ---------- Outputs ----------
output "app_ips" {
  value = { for s in hcloud_server.app : s.name => s.ipv4_address }
}

output "platform_ips" {
  value = { for k, s in hcloud_server.platform : s.name => s.ipv4_address }
}

output "extra_ips" {
  value = { for k, s in hcloud_server.extras : s.name => s.ipv4_address }
}

output "load_balancer_ip" {
  value = var.enable_load_balancer ? hcloud_load_balancer.main[0].ipv4 : null
}

output "network_id" {
  value = hcloud_network.main.id
}
