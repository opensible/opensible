variable "env" {
  type        = string
  default     = "dev"
  description = "Short env tag (dev / sit / prod)."
}

variable "project_name" {
  type        = string
  description = "Naming prefix and label on managed Kubernetes objects."
}

# ---------- Cluster auth ----------
variable "auth_method" {
  type        = string
  default     = "kubeconfig"
  description = "'kubeconfig' (config_path) or 'token' (host+ca+token)."
  validation {
    condition     = contains(["kubeconfig", "token"], var.auth_method)
    error_message = "auth_method must be 'kubeconfig' or 'token'."
  }
}

variable "kubeconfig_path" {
  type        = string
  default     = "./kubeconfig.yaml"
  description = "Path to the kubeconfig file (materialised from the secret by OpenSible at run time)."
}

variable "cluster_endpoint" {
  type        = string
  default     = ""
  description = "API-server URL when auth_method='token'."
}

variable "cluster_ca_cert" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Base64-encoded CA cert when auth_method='token'."
}

variable "cluster_token" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Bearer token when auth_method='token'."
}

# ---------- Namespace ----------
variable "namespace_name" {
  type        = string
  description = "Kubernetes namespace to deploy into."
}

variable "create_namespace" {
  type        = bool
  default     = true
  description = "Create the namespace when true; else assume it exists."
}

# ---------- Workloads ----------
variable "workloads" {
  type = list(object({
    name     = string
    image    = string
    replicas = optional(number, 1)
    port     = optional(number, 80)
    env      = optional(map(string), {})
  }))
  default     = []
  description = "One Deployment + Service per entry."
}

# ---------- Ingress ----------
variable "enable_ingress" {
  type        = bool
  default     = false
  description = "Create a single Ingress in front of one workload's Service."
}

variable "ingress_class" {
  type        = string
  default     = "nginx"
  description = "IngressClass name (nginx, traefik, ...)."
}

variable "ingress_host" {
  type        = string
  default     = ""
  description = "FQDN routed by the Ingress."
}

variable "ingress_target_workload" {
  type        = string
  default     = ""
  description = "Name of the workload whose Service receives ingress traffic."
}

variable "ingress_target_port" {
  type        = number
  default     = 80
  description = "Service port to route to."
}

variable "ingress_tls" {
  type        = bool
  default     = false
  description = "Add cert-manager annotations for a letsencrypt-prod cert."
}

# ---------- Helm addons ----------
variable "install_ingress_nginx" {
  type    = bool
  default = false
}

variable "install_cert_manager" {
  type    = bool
  default = false
}

variable "install_metrics_server" {
  type    = bool
  default = false
}

variable "labels" {
  type    = map(string)
  default = {}
}
