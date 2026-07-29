locals {
  name_prefix = "${var.project_name}-${var.env}"
  base_labels = merge({
    "app.kubernetes.io/managed-by" = "opensible"
    "opensible.io/env"             = var.env
    "opensible.io/project"         = var.project_name
  }, var.labels)
}

# ---------- Namespace ----------
resource "kubernetes_namespace_v1" "this" {
  count = var.create_namespace ? 1 : 0
  metadata {
    name   = var.namespace_name
    labels = local.base_labels
  }
}

locals {
  namespace = var.create_namespace ? kubernetes_namespace_v1.this[0].metadata[0].name : var.namespace_name
}

# ---------- Workloads (Deployment + Service per entry) ----------
resource "kubernetes_deployment_v1" "workload" {
  for_each = { for w in var.workloads : w.name => w }

  metadata {
    name      = each.value.name
    namespace = local.namespace
    labels    = merge(local.base_labels, { "app.kubernetes.io/name" = each.value.name })
  }

  spec {
    replicas = coalesce(each.value.replicas, 1)

    selector {
      match_labels = { "app.kubernetes.io/name" = each.value.name }
    }

    template {
      metadata {
        labels = merge(local.base_labels, { "app.kubernetes.io/name" = each.value.name })
      }
      spec {
        container {
          name  = each.value.name
          image = each.value.image

          port {
            container_port = coalesce(each.value.port, 80)
          }

          dynamic "env" {
            for_each = coalesce(each.value.env, {})
            content {
              name  = env.key
              value = env.value
            }
          }
        }
      }
    }
  }
}

resource "kubernetes_service_v1" "workload" {
  for_each = { for w in var.workloads : w.name => w }

  metadata {
    name      = each.value.name
    namespace = local.namespace
    labels    = merge(local.base_labels, { "app.kubernetes.io/name" = each.value.name })
  }

  spec {
    type     = "ClusterIP"
    selector = { "app.kubernetes.io/name" = each.value.name }

    port {
      port        = coalesce(each.value.port, 80)
      target_port = coalesce(each.value.port, 80)
      protocol    = "TCP"
    }
  }

  depends_on = [kubernetes_deployment_v1.workload]
}

# ---------- Ingress (single, targets one workload's service) ----------
resource "kubernetes_ingress_v1" "this" {
  count = var.enable_ingress && var.ingress_host != "" && var.ingress_target_workload != "" ? 1 : 0

  metadata {
    name      = "${local.name_prefix}-ingress"
    namespace = local.namespace
    labels    = local.base_labels
    annotations = merge(
      {},
      var.ingress_tls ? { "cert-manager.io/cluster-issuer" = "letsencrypt-prod" } : {},
    )
  }

  spec {
    ingress_class_name = var.ingress_class

    rule {
      host = var.ingress_host
      http {
        path {
          path      = "/"
          path_type = "Prefix"
          backend {
            service {
              name = var.ingress_target_workload
              port {
                number = var.ingress_target_port
              }
            }
          }
        }
      }
    }

    dynamic "tls" {
      for_each = var.ingress_tls ? [1] : []
      content {
        hosts       = [var.ingress_host]
        secret_name = "${replace(var.ingress_host, ".", "-")}-tls"
      }
    }
  }

  depends_on = [kubernetes_service_v1.workload]
}

# ---------- Helm addons ----------
resource "helm_release" "ingress_nginx" {
  count            = var.install_ingress_nginx ? 1 : 0
  name             = "ingress-nginx"
  namespace        = "ingress-nginx"
  create_namespace = true
  repository       = "https://kubernetes.github.io/ingress-nginx"
  chart            = "ingress-nginx"
}

resource "helm_release" "cert_manager" {
  count            = var.install_cert_manager ? 1 : 0
  name             = "cert-manager"
  namespace        = "cert-manager"
  create_namespace = true
  repository       = "https://charts.jetstack.io"
  chart            = "cert-manager"
  set {
    name  = "installCRDs"
    value = "true"
  }
}

resource "helm_release" "metrics_server" {
  count      = var.install_metrics_server ? 1 : 0
  name       = "metrics-server"
  namespace  = "kube-system"
  repository = "https://kubernetes-sigs.github.io/metrics-server/"
  chart      = "metrics-server"
}

# ---------- Outputs ----------
output "namespace" {
  value = local.namespace
}

output "workloads" {
  value = { for k, d in kubernetes_deployment_v1.workload : k => {
    replicas = d.spec[0].replicas
    image    = d.spec[0].template[0].spec[0].container[0].image
  } }
}

output "services" {
  value = { for k, s in kubernetes_service_v1.workload : k => {
    cluster_ip = s.spec[0].cluster_ip
    port       = s.spec[0].port[0].port
  } }
}

output "ingress_host" {
  value = length(kubernetes_ingress_v1.this) > 0 ? kubernetes_ingress_v1.this[0].spec[0].rule[0].host : null
}
