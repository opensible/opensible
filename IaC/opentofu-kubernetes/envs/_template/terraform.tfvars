# Sample values. The wizard overwrites this file on every save.
env          = "dev"
project_name = "opensible-demo"

auth_method      = "kubeconfig"
namespace_name   = "opensible"
create_namespace = true

workloads = [
  { name = "hello", image = "nginxdemos/hello:latest", replicas = 2, port = 80, env = {} },
]

enable_ingress          = false
ingress_class           = "nginx"
ingress_host            = ""
ingress_target_workload = "hello"
ingress_target_port     = 80
ingress_tls             = false

install_ingress_nginx  = false
install_cert_manager   = false
install_metrics_server = false
