import type { BlueprintGroup } from "./types";

const LOGO = (slug: string, color?: string) =>
  `https://cdn.simpleicons.org/${slug}${color ? `/${color}` : ""}`;

export const kubernetesGroup: BlueprintGroup = {
  id: "kubernetes",
  name: "Kubernetes",
  description: "Reusable blueprints for Kubernetes clusters and workloads.",
  logo: LOGO("kubernetes", "326CE5"),
  blueprints: [
    {
      id: "k8s-kubeadm-ha",
      name: "Kubernetes HA (kubeadm)",
      description: "Multi control-plane kubeadm cluster with Calico CNI and metrics-server.",
      logo: LOGO("kubernetes", "326CE5"),
      tags: ["kubeadm", "HA", "calico"],
      author: "opensible",
      stars: 128,
      available: true,
      path: "IaC/blueprints/kubernetes/kubernetes-ha-kubeadm",
      templateId: "k8s-cluster",
      filenameStem: "kubernetes-ha-kubeadm",
      defaults: {
        cluster_name: "opensible",
        kubernetes_version: "1.30.4",
        ha_mode: false,
        control_plane_endpoint: "",
        pod_cidr: "10.244.0.0/16",
        service_cidr: "10.96.0.0/12",
        cni_plugin: "calico",
        container_runtime: "containerd",
        kube_proxy_mode: "iptables",
        install_metrics_server: true,
        storage_provisioner: "none",
        reset_existing_cluster: false,
        ssh_port_default: 22,
        control_planes: [{ name: "cp-1", ip: "10.0.0.10" }],
        workers: [{ name: "worker-1", ip: "10.0.0.20" }],
      },
    },

    {
      id: "k8s-uninstall",
      name: "Kubernetes Uninstall / Purge",
      description:
        "Completely remove a kubeadm or k3s cluster from selected nodes. Drains, resets, removes packages, deletes etcd/CNI data, flushes iptables, and cleans images.",
      logo: LOGO("kubernetes", "326CE5"),
      tags: ["kubeadm", "k3s", "uninstall", "cleanup"],
      author: "opensible",
      stars: 42,
      available: true,
      path: "IaC/blueprints/kubernetes/kubernetes-uninstall",
      templateId: "k8s-uninstall",
      filenameStem: "kubernetes-uninstall",
      defaults: {
        cluster_name: "opensible",
        cluster_type: "auto",
        confirm_purge: false,
        drain_nodes: true,
        remove_packages: true,
        remove_containerd: true,
        remove_etcd_data: true,
        remove_cni: true,
        remove_images: false,
        remove_kubeconfig: true,
      },
    },

    {
      id: "k3s-ha-etcd",
      name: "k3s HA (embedded etcd)",
      description: "Lightweight k3s cluster with embedded etcd and Longhorn storage.",
      logo: LOGO("k3s", "FFC61C"),
      tags: ["k3s", "edge", "longhorn"],
      author: "opensible",
      stars: 96,
      available: true,
      path: "IaC/blueprints/kubernetes/k3s-ha-etcd",
      templateId: "k3s-bootstrap",
      filenameStem: "k3s-ha-etcd",
      defaults: {
        cluster_name: "opensible-k3s",
        cluster_token: "change-me-please",
        k3s_version: "stable",
        ha_mode: false,
        disable_traefik: true,
        install_longhorn: false,
        servers: [{ name: "k3s-server-1", ip: "10.0.0.10" }],
        agents: [{ name: "k3s-agent-1", ip: "10.0.0.20" }],
      },
    },
    {
      id: "helm-argocd",
      name: "Argo CD via Helm",
      description: "Install Argo CD on any cluster using the official argo-helm chart.",
      logo: LOGO("argo", "EF7B4D"),
      tags: ["gitops", "argocd", "helm"],
      author: "opensible",
      stars: 71,
      available: true,
      path: "IaC/blueprints/kubernetes/argocd-helm",
      templateId: "k8s-manifests",
      filenameStem: "argocd-helm",
      defaults: {
        action: "helm",
        helm_release: "argocd",
        helm_chart: "argo/argo-cd",
        helm_repo_name: "argo",
        helm_repo_url: "https://argoproj.github.io/argo-helm",
        helm_version: "",
        namespace: "argocd",
        create_namespace: true,
        helm_wait: true,
        kubeconfig: "",

        helm_values:
          "global:\n  domain: argocd.example.com\nserver:\n  service:\n    type: ClusterIP\n  ingress:\n    enabled: false\nconfigs:\n  params:\n    server.insecure: \"true\"\ndex:\n  enabled: false\nnotifications:\n  enabled: false\n",
      },
    },
    {
      id: "ingress-nginx",
      name: "ingress-nginx + cert-manager",
      description: "Production ingress with TLS via Let's Encrypt DNS-01.",
      logo: LOGO("nginx", "009639"),
      tags: ["ingress", "tls"],
      stars: 54,
    },
    {
      id: "istio-base",
      name: "Istio Service Mesh",
      description:
        "Install Istio via the official istio-helm charts: base + istiod control plane in the istio-system namespace. Deploys sidecar-injection ready mesh; add gateways separately.",
      logo: LOGO("istio", "466BB0"),
      tags: ["service-mesh", "istio", "helm", "k8s"],
      author: "opensible",
      stars: 88,
      available: true,
      path: "IaC/blueprints/kubernetes/istio",
      templateId: "k8s-manifests",
      filenameStem: "istio-service-mesh",
      defaults: {
        action: "helm",
        helm_release: "istiod",
        helm_chart: "istio/istiod",
        helm_repo_name: "istio",
        helm_repo_url: "https://istio-release.storage.googleapis.com/charts",
        helm_version: "",
        namespace: "istio-system",
        create_namespace: true,
        helm_wait: true,
        kubeconfig: "",
        helm_values:
          "# Istio control plane values\n" +
          "global:\n" +
          "  proxy:\n" +
          "    resources:\n" +
          "      requests:\n" +
          "        cpu: 100m\n" +
          "        memory: 128Mi\n" +
          "pilot:\n" +
          "  autoscaleEnabled: true\n" +
          "  autoscaleMin: 1\n" +
          "  autoscaleMax: 3\n" +
          "  resources:\n" +
          "    requests:\n" +
          "      cpu: 200m\n" +
          "      memory: 256Mi\n" +
          "meshConfig:\n" +
          "  accessLogFile: /dev/stdout\n" +
          "  enableTracing: false\n",
      },
    },
    {
      id: "linkerd-control-plane",
      name: "Linkerd Service Mesh",
      description:
        "Ultralight service mesh (Rust data plane). Deploys the linkerd-control-plane Helm chart into the linkerd namespace. Requires linkerd-crds + trust anchor first — see the values.yaml comment for a one-liner.",
      logo: LOGO("linkerd", "2BEDA6"),
      tags: ["service-mesh", "linkerd", "helm", "k8s"],
      author: "opensible",
      stars: 62,
      available: true,
      path: "IaC/blueprints/kubernetes/linkerd",
      templateId: "k8s-manifests",
      filenameStem: "linkerd-service-mesh",
      defaults: {
        action: "helm",
        helm_release: "linkerd-control-plane",
        helm_chart: "linkerd/linkerd-control-plane",
        helm_repo_name: "linkerd",
        helm_repo_url: "https://helm.linkerd.io/stable",
        helm_version: "",
        namespace: "linkerd",
        create_namespace: true,
        helm_wait: true,
        kubeconfig: "",
        helm_values:
          "# Prerequisite (run once on a control-plane node):\n" +
          "#   helm install linkerd-crds linkerd/linkerd-crds -n linkerd --create-namespace\n" +
          "#   step certificate create root.linkerd.cluster.local ca.crt ca.key --profile root-ca --no-password --insecure\n" +
          "#   step certificate create identity.linkerd.cluster.local issuer.crt issuer.key --profile intermediate-ca --not-after 8760h --no-password --insecure --ca ca.crt --ca-key ca.key\n" +
          "# then pass --set-file identityTrustAnchorsPEM=ca.crt --set-file identity.issuer.tls.crtPEM=issuer.crt --set-file identity.issuer.tls.keyPEM=issuer.key\n" +
          "identity:\n" +
          "  issuer:\n" +
          "    scheme: kubernetes.io/tls\n" +
          "proxy:\n" +
          "  resources:\n" +
          "    cpu:\n" +
          "      request: 100m\n" +
          "    memory:\n" +
          "      request: 20Mi\n" +
          "controllerReplicas: 1\n" +
          "policyController:\n" +
          "  logLevel: info\n",
      },
    },
    {
      id: "kuma-control-plane",
      name: "Kuma Service Mesh (kuma.io)",
      description:
        "Envoy-based, universal service mesh from kuma.io. Deploys the kuma control plane via the official Helm chart into the kuma-system namespace in standalone mode.",
      logo: LOGO("kuma", "3070E8"),
      tags: ["service-mesh", "kuma", "envoy", "helm", "k8s"],
      author: "opensible",
      stars: 41,
      available: true,
      path: "IaC/blueprints/kubernetes/kuma",
      templateId: "k8s-manifests",
      filenameStem: "kuma-service-mesh",
      defaults: {
        action: "helm",
        helm_release: "kuma",
        helm_chart: "kuma/kuma",
        helm_repo_name: "kuma",
        helm_repo_url: "https://kumahq.github.io/charts",
        helm_version: "",
        namespace: "kuma-system",
        create_namespace: true,
        helm_wait: true,
        kubeconfig: "",
        helm_values:
          "controlPlane:\n" +
          "  mode: standalone\n" +
          "  replicas: 1\n" +
          "  resources:\n" +
          "    requests:\n" +
          "      cpu: 100m\n" +
          "      memory: 256Mi\n" +
          "  tls:\n" +
          "    general:\n" +
          "      secretName: \"\"\n" +
          "  envVars:\n" +
          "    KUMA_STORE_UNSAFE_DELETE: \"false\"\n" +
          "cni:\n" +
          "  enabled: false\n" +
          "ingress:\n" +
          "  enabled: false\n",
      },
    },
  ],
};
