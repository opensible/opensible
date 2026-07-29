import awsLogo from "@/assets/aws-dark-logo.svg";
import awsLogoLight from "@/assets/aws-logo.svg";
import eksLogo from "@/assets/eks-logo.svg";
import azureLogo from "@/assets/azure-logo.png";
import gcpLogo from "@/assets/google-cloud.svg";
import gkeLogo from "@/assets/gke-logo.svg";
import kubernetesLogo from "@/assets/kubernetes-logo.svg";
import proxmoxLogo from "@/assets/proxmox-logo.png";
import vcenterLogo from "@/assets/vcenter-logo.png";
import cloudflareLogo from "@/assets/cloudflare-logo.png";
import hetznerLogo from "@/assets/hetzner-h.svg";
import { useTheme } from "@/lib/theme";



export type ProviderMeta = {
  id: string;
  label: string;
  description?: string;
  enabled?: boolean;
  logo?: string;
  category: "cloud" | "onprem";
};

export const PROVIDER_LOGOS: Record<string, string> = {
  aws: awsLogo,
  eks: eksLogo,
  gcp: gcpLogo,
  gke: gkeLogo,
  azure: azureLogo,
  proxmox: proxmoxLogo,
  vcenter: vcenterLogo,
  esxi: vcenterLogo,
  cloudflare: cloudflareLogo,
  hetzner: hetznerLogo,
  kubernetes: kubernetesLogo,
  k8s: kubernetesLogo,
};

// Light-theme overrides (used when the current UI theme is "light").
// Only providers whose default (dark) logo is unreadable on a light background need an entry here.
export const PROVIDER_LOGOS_LIGHT: Record<string, string> = {
  aws: awsLogoLight,
};

export function useProviderLogo(id?: string): string | undefined {
  const { theme } = useTheme();
  if (!id) return undefined;
  if (theme === "light" && PROVIDER_LOGOS_LIGHT[id]) return PROVIDER_LOGOS_LIGHT[id];
  return PROVIDER_LOGOS[id];
}

export const PROVIDER_LABELS: Record<string, string> = {
  aws: "Amazon Web Services",
  eks: "Amazon EKS",
  gcp: "Google Cloud",
  gke: "Google Kubernetes Engine",
  azure: "Azure",
  bytedc: "ByteDC",
  proxmox: "Proxmox",
  vcenter: "vCenter ESXi",
  esxi: "vCenter ESXi",
  cloudflare: "Cloudflare",
  hetzner: "Hetzner Cloud",
  kubernetes: "Kubernetes",
  k8s: "Kubernetes",
};

// Display order for the picker & any provider list. Providers not in this list
// keep their original relative order after the ones listed here.
export const PROVIDER_DISPLAY_ORDER: string[] = [
  "aws", "eks", "gcp", "gke", "azure", "hetzner", "cloudflare", "bytedc",
  "kubernetes", "vcenter", "proxmox",
];

export const STATIC_PROVIDERS: ProviderMeta[] = [
  { id: "aws", label: "Amazon Web Services", description: "Amazon Web Services — EC2, VPC, Security Group, Key Pair, Internet Gateway.", enabled: true, category: "cloud" },
  { id: "eks", label: "Amazon EKS", description: "Amazon Elastic Kubernetes Service — managed Kubernetes on AWS with VPC, IAM roles and managed node groups.", enabled: true, category: "cloud" },
  { id: "gcp", label: "Google Cloud", description: "Google Cloud Platform — VPC, Compute Engine, Firewall, Cloud NAT.", enabled: true, category: "cloud" },
  { id: "gke", label: "Google Kubernetes Engine", description: "GKE — managed Kubernetes on Google Cloud with VPC-native networking and node pools.", enabled: true, category: "cloud" },
  { id: "azure", label: "Azure", description: "Microsoft Azure — VMs, VNet, AKS, Storage.", enabled: false, category: "cloud" },
  { id: "hetzner", label: "Hetzner Cloud", description: "Hetzner Cloud — European cloud servers, volumes, and networks.", enabled: true, category: "cloud" },
  { id: "cloudflare", label: "Cloudflare", description: "Cloudflare — CDN, DNS, Workers, R2, Zero Trust.", enabled: true, category: "cloud" },
  { id: "bytedc", label: "ByteDC", description: "Huawei-compatible private cloud — VPC, ECS, ELB, NAT, DNS.", enabled: true, category: "cloud" },
  { id: "kubernetes", label: "Kubernetes", description: "Bring your own cluster — namespaces, deployments, services, ingress and Helm addons.", enabled: true, category: "onprem" },
  { id: "vcenter", label: "vCenter ESXi", description: "VMware vSphere / ESXi — on-premise virtualization.", enabled: false, category: "onprem" },
  { id: "proxmox", label: "Proxmox", description: "Proxmox VE — open-source virtualization platform.", enabled: false, category: "onprem" },
];

export function sortProviders<T extends { id: string }>(items: T[]): T[] {
  const idx = (id: string) => {
    const i = PROVIDER_DISPLAY_ORDER.indexOf(id);
    return i === -1 ? PROVIDER_DISPLAY_ORDER.length + 1 : i;
  };
  return [...items].sort((a, b) => idx(a.id) - idx(b.id));
}


export function providerCategory(id: string): "cloud" | "onprem" {
  if (["vcenter", "esxi", "proxmox", "kubernetes", "k8s"].includes(id)) return "onprem";
  return "cloud";
}

export function ProviderLogo({ id, className = "h-5 w-5" }: { id?: string; className?: string }) {
  const url = useProviderLogo(id);
  if (!id) return null;
  if (url) return <img src={url} alt={PROVIDER_LABELS[id] || id} className={`${className} object-contain`} />;
  if (id === "bytedc") {
    return (
      <div className={`${className} rounded bg-gradient-to-br from-cyan-500 to-sky-700 text-white flex items-center justify-center font-bold text-xs shrink-0`}>
        B
      </div>
    );
  }
  return null;
}

export function ProviderCell({ id }: { id?: string }) {
  if (!id) return <span className="text-[var(--color-muted-foreground)]">—</span>;
  return (
    <div className="flex items-center gap-2">
      <ProviderLogo id={id} className="h-5 w-5" />
      <span>{PROVIDER_LABELS[id] || id}</span>
    </div>
  );
}
