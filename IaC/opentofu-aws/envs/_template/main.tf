locals {
  name_prefix = "${var.project_name}-${var.env}"
  base_tags = merge({
    managed_by = "opensible"
    env        = var.env
    project    = var.project_name
  }, var.labels)

  use_password_auth = var.auth_method == "password" && var.admin_password != ""
  use_ssh_key_auth  = var.auth_method == "ssh_key" && var.ssh_public_key != ""

  # A second subnet is required for both ALB (multi-AZ) and the RDS DB subnet group.
  need_subnet_b = var.enable_alb || var.enable_rds


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
  vm_key_name  = local.use_ssh_key_auth ? aws_key_pair.main[0].key_name : null

  # Resolved AMI: explicit value wins, otherwise fall back to the latest
  # Canonical Ubuntu 24.04 LTS x86_64 lookup below.
  resolved_ami = var.ami_id != "" ? var.ami_id : data.aws_ami.ubuntu.id

  platform_servers = var.enable_platform ? merge([
    for role, count in var.platform_roles : {
      for i in range(count) :
      "${role}-${i + 1}" => {
        role          = role
        instance_type = try(var.platform_overrides[role].instance_type, null) != null ? var.platform_overrides[role].instance_type : var.instance_type
        ami_id        = try(var.platform_overrides[role].ami_id, null)        != null ? var.platform_overrides[role].ami_id        : local.resolved_ami
      }
    }
  ]...) : {}

  extra_servers = merge([
    for name, cfg in var.extra_vms : {
      for i in range(coalesce(cfg.vm_count, 1)) :
      "${name}-${i + 1}" => {
        role          = name
        instance_type = coalesce(cfg.instance_type, var.instance_type)
        ami_id        = coalesce(cfg.ami_id, local.resolved_ami)
      }
    }
  ]...)
}

# ---------- AMI lookup (Ubuntu 24.04 Canonical) ----------
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}

# ---------- Key pair ----------
resource "aws_key_pair" "main" {
  count      = local.use_ssh_key_auth ? 1 : 0
  key_name   = "${local.name_prefix}-key"
  public_key = var.ssh_public_key
  tags       = local.base_tags
}

# ---------- VPC / Subnet / IGW / Route table ----------
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(local.base_tags, { Name = "${local.name_prefix}-vpc" })
}

resource "aws_subnet" "app" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.subnet_cidr
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true
  tags                    = merge(local.base_tags, { Name = "${local.name_prefix}-subnet-a" })
}

resource "aws_subnet" "app_b" {
  count                   = local.need_subnet_b ? 1 : 0
  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.subnet_cidr_b
  availability_zone       = data.aws_availability_zones.available.names[1]
  map_public_ip_on_launch = true
  tags                    = merge(local.base_tags, { Name = "${local.name_prefix}-subnet-b" })
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = merge(local.base_tags, { Name = "${local.name_prefix}-igw" })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = merge(local.base_tags, { Name = "${local.name_prefix}-rt-public" })
}

resource "aws_route_table_association" "app" {
  subnet_id      = aws_subnet.app.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "app_b" {
  count          = local.need_subnet_b ? 1 : 0
  subnet_id      = aws_subnet.app_b[0].id
  route_table_id = aws_route_table.public.id
}

# ---------- Security group ----------
resource "aws_security_group" "main" {
  name        = "${local.name_prefix}-sg"
  description = "OpenSible-managed security group for ${local.name_prefix}."
  vpc_id      = aws_vpc.main.id
  tags        = merge(local.base_tags, { Name = "${local.name_prefix}-sg" })

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.admin_cidrs
  }

  dynamic "ingress" {
    for_each = var.enable_web_ingress ? [80, 443] : []
    content {
      description = "Web ${ingress.value}"
      from_port   = ingress.value
      to_port     = ingress.value
      protocol    = "tcp"
      cidr_blocks = var.web_cidrs
    }
  }

  ingress {
    description = "ICMP echo"
    from_port   = -1
    to_port     = -1
    protocol    = "icmp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # User-defined ingress rules from var.custom_ingress_rules — multiple
  # ports/protocols/CIDRs without touching this module.
  dynamic "ingress" {
    for_each = var.custom_ingress_rules
    content {
      description = try(ingress.value.description, "")
      from_port   = ingress.value.from_port
      to_port     = ingress.value.to_port
      protocol    = try(ingress.value.protocol, "tcp")
      cidr_blocks = try(ingress.value.cidr_blocks, ["0.0.0.0/0"])
    }
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ---------- App pool ----------
resource "aws_instance" "app" {
  count                       = var.app_vm_count
  ami                         = local.resolved_ami
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.app.id
  vpc_security_group_ids      = [aws_security_group.main.id]
  key_name                    = local.vm_key_name
  user_data                   = local.vm_user_data
  associate_public_ip_address = true
  tags = merge(local.base_tags, {
    Name = "${local.name_prefix}-app-${count.index + 1}"
    role = "app"
  })
}

# ---------- Platform pool ----------
resource "aws_instance" "platform" {
  for_each                    = local.platform_servers
  ami                         = each.value.ami_id
  instance_type               = each.value.instance_type
  subnet_id                   = aws_subnet.app.id
  vpc_security_group_ids      = [aws_security_group.main.id]
  key_name                    = local.vm_key_name
  user_data                   = local.vm_user_data
  associate_public_ip_address = true
  tags = merge(local.base_tags, {
    Name = "${local.name_prefix}-${each.key}"
    role = each.value.role
  })
}

# ---------- Extra VMs ----------
resource "aws_instance" "extras" {
  for_each                    = local.extra_servers
  ami                         = each.value.ami_id
  instance_type               = each.value.instance_type
  subnet_id                   = aws_subnet.app.id
  vpc_security_group_ids      = [aws_security_group.main.id]
  key_name                    = local.vm_key_name
  user_data                   = local.vm_user_data
  associate_public_ip_address = true
  tags = merge(local.base_tags, {
    Name = "${local.name_prefix}-${each.key}"
    role = each.value.role
  })
}

# ---------- Outputs ----------
output "app_public_ips" {
  value = { for i, s in aws_instance.app : s.tags["Name"] => s.public_ip }
}

output "platform_public_ips" {
  value = { for k, s in aws_instance.platform : s.tags["Name"] => s.public_ip }
}

output "extra_public_ips" {
  value = { for k, s in aws_instance.extras : s.tags["Name"] => s.public_ip }
}

output "vpc_id" {
  value = aws_vpc.main.id
}

output "security_group_id" {
  value = aws_security_group.main.id
}

output "resolved_ami" {
  value = local.resolved_ami
}

# =====================================================================
# Application Load Balancer (optional)
# =====================================================================
resource "aws_security_group" "alb" {
  count       = var.enable_alb ? 1 : 0
  name        = "${local.name_prefix}-alb-sg"
  description = "OpenSible-managed ALB security group."
  vpc_id      = aws_vpc.main.id
  tags        = merge(local.base_tags, { Name = "${local.name_prefix}-alb-sg" })

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = var.web_cidrs
  }

  dynamic "ingress" {
    for_each = var.alb_certificate_arn != "" ? [443] : []
    content {
      description = "HTTPS"
      from_port   = ingress.value
      to_port     = ingress.value
      protocol    = "tcp"
      cidr_blocks = var.web_cidrs
    }
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_lb" "main" {
  count              = var.enable_alb ? 1 : 0
  name               = "${local.name_prefix}-alb"
  internal           = var.alb_internal
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb[0].id]
  subnets            = [aws_subnet.app.id, aws_subnet.app_b[0].id]
  tags               = merge(local.base_tags, { Name = "${local.name_prefix}-alb" })
}

resource "aws_lb_target_group" "app" {
  count       = var.enable_alb ? 1 : 0
  name        = substr("${local.name_prefix}-tg", 0, 32)
  port        = var.alb_target_port
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "instance"

  health_check {
    path                = var.alb_health_check_path
    matcher             = "200-399"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = merge(local.base_tags, { Name = "${local.name_prefix}-tg" })
}

resource "aws_lb_target_group_attachment" "app" {
  for_each         = var.enable_alb ? { for i, s in aws_instance.app : tostring(i) => s.id } : {}
  target_group_arn = aws_lb_target_group.app[0].arn
  target_id        = each.value
  port             = var.alb_target_port
}

resource "aws_lb_listener" "http" {
  count             = var.enable_alb ? 1 : 0
  load_balancer_arn = aws_lb.main[0].arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app[0].arn
  }
}

resource "aws_lb_listener" "https" {
  count             = var.enable_alb && var.alb_certificate_arn != "" ? 1 : 0
  load_balancer_arn = aws_lb.main[0].arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.alb_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app[0].arn
  }
}

# =====================================================================
# Amazon RDS (optional)
# =====================================================================
resource "aws_security_group" "rds" {
  count       = var.enable_rds ? 1 : 0
  name        = "${local.name_prefix}-rds-sg"
  description = "OpenSible-managed RDS security group."
  vpc_id      = aws_vpc.main.id
  tags        = merge(local.base_tags, { Name = "${local.name_prefix}-rds-sg" })

  ingress {
    description     = "DB from app SG"
    from_port       = var.rds_engine == "mysql" ? 3306 : 5432
    to_port         = var.rds_engine == "mysql" ? 3306 : 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.main.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_db_subnet_group" "main" {
  count      = var.enable_rds ? 1 : 0
  name       = "${local.name_prefix}-db-subnets"
  subnet_ids = [aws_subnet.app.id, aws_subnet.app_b[0].id]
  tags       = merge(local.base_tags, { Name = "${local.name_prefix}-db-subnets" })
}

resource "aws_db_instance" "main" {
  count                       = var.enable_rds ? 1 : 0
  identifier                  = "${local.name_prefix}-db"
  engine                      = var.rds_engine
  engine_version              = var.rds_engine_version != "" ? var.rds_engine_version : null
  instance_class              = var.rds_instance_class
  allocated_storage           = var.rds_allocated_storage
  db_name                     = replace(var.rds_db_name, "/[^A-Za-z0-9]/", "")
  username                    = var.rds_username
  password                    = var.rds_password
  db_subnet_group_name        = aws_db_subnet_group.main[0].name
  vpc_security_group_ids      = [aws_security_group.rds[0].id]
  publicly_accessible         = var.rds_publicly_accessible
  skip_final_snapshot         = var.rds_skip_final_snapshot
  storage_encrypted           = true
  deletion_protection         = false
  tags                        = merge(local.base_tags, { Name = "${local.name_prefix}-db" })
}

# =====================================================================
# Amazon S3 (optional)
# =====================================================================
resource "aws_s3_bucket" "main" {
  count  = var.enable_s3 ? 1 : 0
  bucket = var.s3_bucket_name
  tags   = merge(local.base_tags, { Name = var.s3_bucket_name })
}

resource "aws_s3_bucket_versioning" "main" {
  count  = var.enable_s3 ? 1 : 0
  bucket = aws_s3_bucket.main[0].id
  versioning_configuration {
    status = var.s3_versioning ? "Enabled" : "Suspended"
  }
}

resource "aws_s3_bucket_public_access_block" "main" {
  count                   = var.enable_s3 ? 1 : 0
  bucket                  = aws_s3_bucket.main[0].id
  block_public_acls       = var.s3_block_public_access
  block_public_policy     = var.s3_block_public_access
  ignore_public_acls      = var.s3_block_public_access
  restrict_public_buckets = var.s3_block_public_access
}

# ---------- Additional outputs ----------
output "alb_dns_name" {
  value = var.enable_alb ? aws_lb.main[0].dns_name : null
}

output "rds_endpoint" {
  value = var.enable_rds ? aws_db_instance.main[0].address : null
}

output "rds_port" {
  value = var.enable_rds ? aws_db_instance.main[0].port : null
}

output "s3_bucket" {
  value = var.enable_s3 ? aws_s3_bucket.main[0].bucket : null
}

