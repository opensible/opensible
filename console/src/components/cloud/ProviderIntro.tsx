import { Card } from "@/components/ui/card";
import { ProviderLogo, PROVIDER_LABELS } from "@/lib/providers";
import { BookOpen, Github } from "lucide-react";
import { Button } from "@/components/ui/button";

const GITHUB_ISSUES_URL = "https://github.com/opensible/opensible/issues/new";

type ProviderId = "aws" | "eks" | "gcp" | "gke" | "hetzner" | "bytedc" | "cloudflare" | "kubernetes";

type Info = {
  tagline: string;
  about: string;
  credentials: string;
  docsUrl?: string;
  docsLabel?: string;
};

const INFO: Record<ProviderId, Info> = {
  aws: {
    tagline: "Amazon Web Services — global public cloud (EC2, VPC, IAM, S3, and 200+ services).",
    about:
      "This stack provisions a minimal AWS footprint: a VPC with public subnet, Internet Gateway, Security Group, EC2 key pair, and EC2 instances organized as App / Platform / Extras pools. Extendable to RDS, ELB, S3, and IAM in future iterations.",
    credentials: "Uses AWS Access Key ID + Secret Access Key. Store as stack secrets; never committed.",
    docsUrl: "https://registry.terraform.io/providers/hashicorp/aws/latest/docs",
    docsLabel: "AWS provider docs",
  },
  eks: {
    tagline: "Amazon EKS — managed Kubernetes on AWS with VPC integration, IAM roles and managed node groups.",
    about:
      "This stack provisions a production-shaped EKS footprint: a public VPC with two or more subnets across availability zones (EKS requirement), an Internet Gateway and route table, IAM roles for the control plane and worker nodes with the required AWS-managed policies, an EKS cluster, and a primary managed node group. Extra node groups can be added as a JSON map (e.g. GPU or SPOT pools).",
    credentials: "Uses an AWS Access Key ID + Secret with EKS, EC2, VPC and IAM permissions. Stored encrypted per stack.",
    docsUrl: "https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/eks_cluster",
    docsLabel: "EKS provider docs",
  },
  gcp: {
    tagline: "Google Cloud Platform — global public cloud (Compute Engine, VPC, GKE, Cloud SQL, GCS, and 100+ services).",
    about:
      "This stack provisions a minimal GCP footprint: a custom-mode VPC with a regional subnet, firewall rules (SSH, optional web, ICMP), and Compute Engine VMs organized as App / Platform / Extras pools. Extendable to GKE, Cloud SQL, and GCS in future iterations.",
    credentials: "Uses a GCP Service Account JSON key with Compute Admin + Network Admin. Stored as a stack secret; never committed.",
    docsUrl: "https://registry.terraform.io/providers/hashicorp/google/latest/docs",
    docsLabel: "Google provider docs",
  },
  gke: {
    tagline: "Google Kubernetes Engine — managed Kubernetes on Google Cloud with VPC-native networking, node pools and Workload Identity.",
    about:
      "This stack provisions a production-shaped GKE footprint: a custom-mode VPC with a regional subnet (secondary ranges for pods and services), a regional or zonal cluster with the default node pool removed, a primary node pool with optional autoscaling, and any number of extra node pools defined as a JSON map. Cloud NAT is added automatically when private nodes are enabled.",
    credentials: "Uses a GCP Service Account JSON key with Kubernetes Engine Admin + Compute Network Admin. OpenSible can auto-grant Service Account User (actAs) from the JSON key service account to the selected node service account; this requires permission to update service account IAM policy. Leaving the node account empty uses the JSON key's client_email.",
    docsUrl: "https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/container_cluster",
    docsLabel: "GKE provider docs",
  },
  hetzner: {
    tagline: "Hetzner Cloud — European cloud provider known for simple pricing and fast SSD VMs.",
    about:
      "Provisions Hetzner Cloud servers grouped in App / Platform / Extras pools, an optional private network, firewall, and optional load balancer — all in a single OpenTofu workspace.",
    credentials: "Uses a Hetzner Cloud API token (Read & Write) scoped to the target project.",
    docsUrl: "https://registry.terraform.io/providers/hetznercloud/hcloud/latest/docs",
    docsLabel: "Hetzner provider docs",
  },
  bytedc: {
    tagline: "ByteDC — Cambodia-based Huawei Cloud Stack (HCS) compatible region.",
    about:
      "Provisions ECS instances, VPC, subnet, and security groups on ByteDC's HCS-compatible endpoints. Designed for the platform stack (bank, financial service, shared services, tenant workloads).",
    credentials: "Uses ByteDC Access Key + Secret Key + ECS admin password. Stored encrypted per stack.",
  },
  cloudflare: {
    tagline: "Cloudflare — global edge network for DNS, CDN, R2 object storage, Workers, and Zero Trust.",
    about:
      "This is not a VM provider — it manages edge resources on your Cloudflare account: DNS records for a zone, R2 buckets, Worker scripts + routes, and Access applications.",
    credentials: "Uses a Cloudflare API Token with Zone:Read + DNS:Edit (add R2/Workers/Access scopes if used).",
    docsUrl: "https://registry.terraform.io/providers/cloudflare/cloudflare/4.52.0/docs",
    docsLabel: "Cloudflare provider docs",
  },
  kubernetes: {
    tagline: "Kubernetes — deploy into any existing cluster (EKS, GKE, AKS, k3s, on-prem) via kubeconfig or bearer-token auth.",
    about:
      "Mode A: bring your own cluster. This stack creates a namespace, one Deployment + Service per workload, an optional Ingress in front of one workload, and optional Helm addons (ingress-nginx, cert-manager, metrics-server). Since there are no VMs, OpenSible synthesizes a virtual inventory from Kubernetes Services so downstream Ansible playbooks can still target the managed hostnames.",
    credentials: "Kubeconfig YAML OR cluster endpoint + base64 CA cert + bearer token. Stored encrypted per stack; never committed.",
    docsUrl: "https://registry.terraform.io/providers/hashicorp/kubernetes/latest/docs",
    docsLabel: "Kubernetes provider docs",
  },
};

export function ProviderIntro({ providerId }: { providerId: ProviderId }) {
  const info = INFO[providerId];
  if (!info) return null;
  const label = PROVIDER_LABELS[providerId] || providerId;

  return (
    <Card className="p-5">
      <div className="flex items-start gap-4">
        <div className="shrink-0 h-12 w-12 rounded-md bg-[var(--color-muted)]/40 flex items-center justify-center">
          <ProviderLogo id={providerId} className="h-9 w-9" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h2 className="text-base font-semibold">About {label}</h2>
            {info.docsUrl && (
              <a
                href={info.docsUrl}
                target="_blank"
                rel="noreferrer"
                className="text-xs inline-flex items-center gap-1 text-[var(--color-primary)] hover:underline"
              >
                <BookOpen className="h-3 w-3" />
                {info.docsLabel || "Provider docs"}
              </a>
            )}
          </div>
          <p className="text-sm text-[var(--color-muted-foreground)] mt-1">{info.tagline}</p>
          <p className="text-sm mt-3">{info.about}</p>

          <div className="mt-4 flex items-center justify-between gap-3 text-sm rounded-md border border-[var(--color-border)] bg-[var(--color-muted)]/30 p-3">
            <p className="text-[var(--color-muted-foreground)]">
              Missing something or want to improve this provider? OpenSible is open source — your feedback shapes the roadmap.
            </p>
            <Button asChild variant="outline" size="sm" className="shrink-0">
              <a href={GITHUB_ISSUES_URL} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5">
                <Github className="h-3.5 w-3.5" />
                Suggest an issue
              </a>
            </Button>
          </div>
        </div>
      </div>
    </Card>
  );
}
