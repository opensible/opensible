import type { BlueprintGroup } from "./types";

const LOGO = (slug: string, color?: string) =>
  `https://cdn.simpleicons.org/${slug}${color ? `/${color}` : ""}`;

export const storageGroup: BlueprintGroup = {
  id: "storage",
  name: "Storage & Object Store",
  description: "S3-compatible object storage and distributed file systems.",
  logo: LOGO("rust", "000000"),
  blueprints: [
    {
      id: "rustfs",
      name: "RustFS (S3-compatible)",
      description:
        "High-performance, distributed S3-compatible object storage written in Rust. Apache-2.0 licensed MinIO alternative — deploy single-node or an erasure-coded cluster across multiple hosts with the RustFS server binary managed via systemd.",
      logo: LOGO("rust", "000000"),
      tags: ["rustfs", "s3", "object-storage", "systemd"],
      author: "opensible",
      stars: 6,
      available: false,
      source: "https://github.com/rustfs/rustfs",
      path: "IaC/blueprints/storage/rustfs",
      templateId: "rustfs-cluster",
      filenameStem: "rustfs-cluster",
      defaults: {
        cluster_id: "opensible-rustfs",
        rustfs_version: "latest",
        api_port: 9000,
        console_port: 9001,
        data_dirs: ["/srv/rustfs/data1"],
        access_key: "rustfsadmin",
        secret_key: "",
        ssh_user_default: "root",
        ssh_port_default: 22,
        nodes: [
          { name: "rustfs-1", ip: "", ssh_user: "", ssh_port: "" },
        ],
        open_firewall: true,
        become: true,
      },
    },
  ],
};
