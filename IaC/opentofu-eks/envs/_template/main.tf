locals {
  name_prefix = "${var.project_name}-${var.env}"
  base_tags = merge({
    managed_by = "opensible"
    env        = var.env
    project    = var.project_name
  }, var.labels)

  azs              = length(var.availability_zones) > 0 ? var.availability_zones : slice(data.aws_availability_zones.available.names, 0, length(var.public_subnet_cidrs))
  cluster_name     = "${local.name_prefix}-eks"
  cluster_role_arn = var.create_iam_roles ? aws_iam_role.cluster[0].arn : var.existing_cluster_role_arn
  node_role_arn    = var.create_iam_roles ? aws_iam_role.node[0].arn : var.existing_node_role_arn
}

data "aws_availability_zones" "available" {
  state = "available"
}

# ---------- VPC / Subnets / IGW / Route Table ----------
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags = merge(local.base_tags, {
    Name = "${local.name_prefix}-vpc"
    # Tag required by the AWS load balancer controller / kube-proxy discovery.
    "kubernetes.io/cluster/${local.cluster_name}" = "shared"
  })
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = merge(local.base_tags, { Name = "${local.name_prefix}-igw" })
}

resource "aws_subnet" "public" {
  count                   = length(var.public_subnet_cidrs)
  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.public_subnet_cidrs[count.index]
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true
  tags = merge(local.base_tags, {
    Name                                          = "${local.name_prefix}-public-${count.index + 1}"
    "kubernetes.io/role/elb"                      = "1"
    "kubernetes.io/cluster/${local.cluster_name}" = "shared"
  })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = merge(local.base_tags, { Name = "${local.name_prefix}-rt-public" })
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# ---------- IAM: EKS cluster role ----------
data "aws_iam_policy_document" "eks_cluster_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "cluster" {
  count              = var.create_iam_roles ? 1 : 0
  name               = "${local.name_prefix}-eks-cluster"
  assume_role_policy = data.aws_iam_policy_document.eks_cluster_assume.json
  tags               = local.base_tags
}

resource "aws_iam_role_policy_attachment" "cluster_AmazonEKSClusterPolicy" {
  count      = var.create_iam_roles ? 1 : 0
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
  role       = aws_iam_role.cluster[0].name
}

resource "aws_iam_role_policy_attachment" "cluster_AmazonEKSVPCResourceController" {
  count      = var.create_iam_roles ? 1 : 0
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSVPCResourceController"
  role       = aws_iam_role.cluster[0].name
}

# ---------- IAM: node group role ----------
data "aws_iam_policy_document" "node_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "node" {
  count              = var.create_iam_roles ? 1 : 0
  name               = "${local.name_prefix}-eks-node"
  assume_role_policy = data.aws_iam_policy_document.node_assume.json
  tags               = local.base_tags
}

resource "aws_iam_role_policy_attachment" "node_AmazonEKSWorkerNodePolicy" {
  count      = var.create_iam_roles ? 1 : 0
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
  role       = aws_iam_role.node[0].name
}

resource "aws_iam_role_policy_attachment" "node_AmazonEKS_CNI_Policy" {
  count      = var.create_iam_roles ? 1 : 0
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
  role       = aws_iam_role.node[0].name
}

resource "aws_iam_role_policy_attachment" "node_AmazonEC2ContainerRegistryReadOnly" {
  count      = var.create_iam_roles ? 1 : 0
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
  role       = aws_iam_role.node[0].name
}

# ---------- EKS cluster ----------
resource "aws_eks_cluster" "main" {
  name     = local.cluster_name
  role_arn = local.cluster_role_arn
  version  = var.kubernetes_version

  vpc_config {
    subnet_ids              = aws_subnet.public[*].id
    endpoint_public_access  = var.endpoint_public_access
    endpoint_private_access = var.endpoint_private_access
    public_access_cidrs     = var.endpoint_public_access ? var.public_access_cidrs : null
  }

  tags = local.base_tags

  depends_on = [
    aws_iam_role_policy_attachment.cluster_AmazonEKSClusterPolicy,
    aws_iam_role_policy_attachment.cluster_AmazonEKSVPCResourceController,
  ]

  lifecycle {
    precondition {
      condition     = var.create_iam_roles || trimspace(var.existing_cluster_role_arn) != ""
      error_message = "existing_cluster_role_arn is required when create_iam_roles is false."
    }
  }
}

# ---------- Primary managed node group ----------
resource "aws_eks_node_group" "primary" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "${local.name_prefix}-primary"
  node_role_arn   = local.node_role_arn
  subnet_ids      = aws_subnet.public[*].id

  instance_types = [var.primary_instance_type]
  disk_size      = var.primary_disk_size_gb
  ami_type       = var.primary_ami_type
  capacity_type  = var.primary_capacity_type

  scaling_config {
    desired_size = var.primary_enable_autoscaling ? var.primary_min_size : var.primary_desired_size
    min_size     = var.primary_enable_autoscaling ? var.primary_min_size : var.primary_desired_size
    max_size     = var.primary_enable_autoscaling ? var.primary_max_size : var.primary_desired_size
  }

  update_config {
    max_unavailable = 1
  }

  labels = { pool = "primary" }
  tags   = local.base_tags

  depends_on = [
    aws_iam_role_policy_attachment.node_AmazonEKSWorkerNodePolicy,
    aws_iam_role_policy_attachment.node_AmazonEKS_CNI_Policy,
    aws_iam_role_policy_attachment.node_AmazonEC2ContainerRegistryReadOnly,
  ]

  lifecycle {
    precondition {
      condition     = var.create_iam_roles || trimspace(var.existing_node_role_arn) != ""
      error_message = "existing_node_role_arn is required when create_iam_roles is false."
    }
  }
}

# ---------- Extra managed node groups ----------
resource "aws_eks_node_group" "extras" {
  for_each = var.extra_node_groups

  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "${local.name_prefix}-${each.key}"
  node_role_arn   = local.node_role_arn
  subnet_ids      = aws_subnet.public[*].id

  instance_types = [coalesce(each.value.instance_type, var.primary_instance_type)]
  disk_size      = coalesce(each.value.disk_size_gb, var.primary_disk_size_gb)
  ami_type       = coalesce(each.value.ami_type, var.primary_ami_type)
  capacity_type  = coalesce(each.value.capacity_type, var.primary_capacity_type)

  scaling_config {
    desired_size = coalesce(each.value.enable_autoscaling, false) ? coalesce(each.value.min_size, 1) : coalesce(each.value.desired_size, 1)
    min_size     = coalesce(each.value.enable_autoscaling, false) ? coalesce(each.value.min_size, 1) : coalesce(each.value.desired_size, 1)
    max_size     = coalesce(each.value.enable_autoscaling, false) ? coalesce(each.value.max_size, 3) : coalesce(each.value.desired_size, 1)
  }

  update_config {
    max_unavailable = 1
  }

  labels = merge({ pool = each.key }, coalesce(each.value.labels, {}))
  tags   = local.base_tags

  depends_on = [
    aws_iam_role_policy_attachment.node_AmazonEKSWorkerNodePolicy,
    aws_iam_role_policy_attachment.node_AmazonEKS_CNI_Policy,
    aws_iam_role_policy_attachment.node_AmazonEC2ContainerRegistryReadOnly,
  ]

  lifecycle {
    precondition {
      condition     = var.create_iam_roles || trimspace(var.existing_node_role_arn) != ""
      error_message = "existing_node_role_arn is required when create_iam_roles is false."
    }
  }
}

# ---------- Outputs ----------
output "cluster_name" {
  value = aws_eks_cluster.main.name
}

output "cluster_endpoint" {
  value     = aws_eks_cluster.main.endpoint
  sensitive = true
}

output "cluster_ca_certificate" {
  value     = aws_eks_cluster.main.certificate_authority[0].data
  sensitive = true
}

output "cluster_version" {
  value = aws_eks_cluster.main.version
}

output "kubectl_config_command" {
  value = "aws eks update-kubeconfig --region ${var.region} --name ${aws_eks_cluster.main.name}"
}

output "vpc_id" {
  value = aws_vpc.main.id
}

output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}
