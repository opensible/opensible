"""Template: Full-featured k3s cluster bootstrap with per-node inputs.

Renders:
  - a playbook (`playbooks/<cluster>-k3s.yml`) that:
      * prepares each node (kernel modules, sysctl, swap off, firewall ports,
        LXC/container-safe defaults for OrbStack / Proxmox LXC)
      * installs k3s on the first server node (with `--cluster-init` when HA)
      * joins additional server nodes to the embedded-etcd control plane
      * joins agent (worker) nodes
      * optionally installs Longhorn / opens Traefik / fetches kubeconfig
  - a sidecar inventory (`inventories/<cluster>.yml`) built from the per-node
    inputs so the play is directly runnable and version-controlled.

Mirrors the design of the Kubernetes (kubeadm) template so both flows behave
consistently: idempotent, retry-safe, LXC-aware.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ._common import (  # noqa: F401
    slugify, yaml_str, render_hosts,
    VAULT_FILES_VARIABLE, parse_vault_files, vars_files_lines,
)


K3S_TEMPLATE_GENERATION = "2026-07-k3s-hardened-v10"


TEMPLATE = {
    "id": "k3s-bootstrap",
    "name": "k3s Cluster Bootstrap",
    "category": "Kubernetes",
    "icon": "boxes",
    "description": (
        "Full k3s cluster wizard. Define server & agent nodes with IPs, "
        "choose HA (embedded etcd) mode, disable bundled add-ons, install "
        "Longhorn, and fetch kubeconfig. LXC/OrbStack-aware and safe to "
        "re-run — generates a playbook + inventory in your repo for GitOps."
    ),
    "tags": ["k3s", "kubernetes", "cluster", "ha", "gitops"],
    "variables": [
        {"name": "cluster_name", "label": "Cluster name", "type": "string", "required": True,
         "placeholder": "prod-k3s", "help": "Used as filename slug and inventory group prefix."},
        {"name": "cluster_token", "label": "Cluster token", "type": "string", "required": True,
         "placeholder": "long-random-string",
         "help": "Shared secret used by all server and agent nodes to join. Keep this in a vault."},
        {"name": "k3s_version", "label": "k3s channel or version",
         "type": "string", "default": "stable",
         "placeholder": "stable | latest | v1.30.5+k3s1",
         "help": "Empty / 'stable' installs the latest stable. Pin an explicit version like v1.30.5+k3s1 for prod."},
        {"name": "ha_mode", "label": "HA mode (embedded etcd across servers)",
         "type": "boolean", "default": False,
         "help": "When enabled the first server uses --cluster-init and additional servers join it. Requires an odd count (1/3/5)."},
        {"name": "control_plane_endpoint", "label": "Control-plane endpoint (VIP or LB, host or host:6443)",
         "type": "string", "default": "",
         "placeholder": "k3s-api.example.com",
         "help": "Optional. Points agents (and extra servers) at a VIP/LB instead of the first server IP. Also added to server TLS SANs."},
        {"name": "disable_traefik", "label": "Disable bundled Traefik ingress",
         "type": "boolean", "default": True,
         "help": "Recommended — install your own ingress (Traefik, Nginx, or Istio) via Helm/GitOps."},
        {"name": "disable_servicelb", "label": "Disable bundled ServiceLB (klipper-lb)",
         "type": "boolean", "default": False,
         "help": "Turn off when you use an external LB (MetalLB, ByteDC ELB, etc.)."},
        {"name": "disable_local_storage", "label": "Disable bundled local-path StorageClass",
         "type": "boolean", "default": False,
         "help": "Turn off when you install Longhorn or another storage provisioner."},
        {"name": "disable_network_policy", "label": "Disable bundled NetworkPolicy controller",
         "type": "boolean", "default": False,
         "help": "Turn off when you install Calico/Cilium to handle NetworkPolicy instead."},
        {"name": "flannel_backend", "label": "Flannel backend",
         "type": "select", "default": "vxlan",
         "options": [
             {"value": "vxlan", "label": "vxlan (default, works everywhere incl. LXC)"},
             {"value": "host-gw", "label": "host-gw (fastest, requires L2 adjacency)"},
             {"value": "wireguard-native", "label": "wireguard-native (encrypted overlay)"},
             {"value": "none", "label": "none (bring your own CNI: Calico/Cilium)"},
         ]},
        {"name": "cluster_cidr", "label": "Pod (cluster) CIDR", "type": "string", "default": "10.42.0.0/16"},
        {"name": "service_cidr", "label": "Service CIDR", "type": "string", "default": "10.43.0.0/16"},
        {"name": "cluster_dns", "label": "Cluster DNS IP (inside service CIDR)",
         "type": "string", "default": "10.43.0.10"},
        {"name": "install_longhorn", "label": "Install Longhorn (distributed block storage)",
         "type": "boolean", "default": False},
        {"name": "longhorn_version", "label": "Longhorn version (e.g. v1.7.2)",
         "type": "string", "default": "v1.7.2"},
        {"name": "longhorn_replica_count", "label": "Longhorn default replica count",
         "type": "number", "default": 3},
        {"name": "reset_existing_cluster", "label": "Remove existing Kubernetes/k3s before install",
         "type": "boolean", "default": True,
         "help": "Recommended for clean bootstrap. Stops/removes kubeadm, k3s, RKE2, MicroK8s state, frees port 6443, and purges CNI/kubelet leftovers on selected nodes."},
        {"name": "open_firewall", "label": "Open required firewall ports (ufw/firewalld)",
         "type": "boolean", "default": True,
         "help": "6443/tcp (API), 10250/tcp (kubelet), 8472/udp (flannel-vxlan), 51820/udp (wireguard), 2379-2380/tcp (etcd HA)."},
        {"name": "fetch_kubeconfig", "label": "Fetch kubeconfig to controller",
         "type": "boolean", "default": True,
         "help": "Downloads /etc/rancher/k3s/k3s.yaml from the first server to ~/.kube/<cluster>.yaml with the API address rewritten."},
        {"name": "ssh_user_default", "label": "Default SSH user for nodes", "type": "string", "default": "root"},
        {"name": "ssh_port_default", "label": "Default SSH port", "type": "number", "default": 22},
        {"name": "servers", "label": "Server nodes (control-plane + etcd)",
         "type": "nodes", "required": True,
         "help": "First entry becomes the bootstrap server. Use 1, 3, or 5 for HA.",
         "default": [{"name": "server-1", "ip": "", "ssh_user": "", "ssh_port": ""}]},
        {"name": "agents", "label": "Agent nodes (workers)",
         "type": "nodes",
         "default": []},
        {"name": "become", "label": "Run as sudo (become)", "type": "boolean", "default": True},
        VAULT_FILES_VARIABLE,
    ],
}


def suggested_filename(values: Dict[str, Any]) -> str:
    return f"{slugify(values.get('cluster_name'), 'cluster')}-k3s.yml"


def _norm_nodes(raw: Any, default_user: str, default_port: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for i, n in enumerate(raw):
        if not isinstance(n, dict):
            continue
        ip = str(n.get("ip") or "").strip()
        if not ip:
            continue
        name = str(n.get("name") or f"node-{i+1}").strip()
        user = str(n.get("ssh_user") or default_user or "root").strip() or "root"
        try:
            port = int(n.get("ssh_port") or default_port or 22)
        except Exception:
            port = 22
        k3s_name = slugify(name, f"node-{i+1}")[:63].strip("-") or f"node-{i+1}"
        out.append({"name": name, "k3s_name": k3s_name, "ip": ip, "ssh_user": user, "ssh_port": port})
    return out


def _inventory_yaml(cluster: str, servers: List[Dict[str, Any]], agents: List[Dict[str, Any]]) -> str:
    srv_group = f"{cluster}_servers"
    agt_group = f"{cluster}_agents"
    all_group = f"{cluster}_cluster"

    def hosts_block(nodes: List[Dict[str, Any]]) -> List[str]:
        lines: List[str] = []
        for n in nodes:
            lines.append(f"        {n['name']}:")
            lines.append(f"          ansible_host: {n['ip']}")
            lines.append(f"          ansible_user: {n['ssh_user']}")
            lines.append(f"          ansible_port: {n['ssh_port']}")
        if not lines:
            lines.append("        {}")
        return lines

    parts: List[str] = ["---", "all:", "  children:"]
    parts.append(f"    {srv_group}:")
    parts.append("      hosts:")
    parts.extend(hosts_block(servers))
    parts.append(f"    {agt_group}:")
    parts.append("      hosts:")
    parts.extend(hosts_block(agents))
    parts.append(f"    {all_group}:")
    parts.append("      children:")
    parts.append(f"        {srv_group}: {{}}")
    parts.append(f"        {agt_group}: {{}}")
    return "\n".join(parts) + "\n"


def render(values: Dict[str, Any], targets: Dict[str, Any]) -> str:
    cluster = slugify(values.get("cluster_name") or "cluster", "cluster")
    token = str(values.get("cluster_token") or "CHANGE_ME_TOKEN").strip() or "CHANGE_ME_TOKEN"
    version_raw = str(values.get("k3s_version") or "stable").strip()
    ha_mode = bool(values.get("ha_mode"))
    cp_endpoint = str(values.get("control_plane_endpoint") or "").strip()
    become = "true" if values.get("become", True) else "false"

    disable_traefik = bool(values.get("disable_traefik", True))
    disable_servicelb = bool(values.get("disable_servicelb", False))
    disable_local_storage = bool(values.get("disable_local_storage", False))
    disable_network_policy = bool(values.get("disable_network_policy", False))
    flannel_backend = str(values.get("flannel_backend") or "vxlan").lower().strip() or "vxlan"

    cluster_cidr = str(values.get("cluster_cidr") or "10.42.0.0/16").strip()
    service_cidr = str(values.get("service_cidr") or "10.43.0.0/16").strip()
    cluster_dns = str(values.get("cluster_dns") or "10.43.0.10").strip()

    install_longhorn = bool(values.get("install_longhorn"))
    longhorn_version = str(values.get("longhorn_version") or "v1.7.2").strip() or "v1.7.2"
    try:
        longhorn_replicas = int(values.get("longhorn_replica_count") or 3)
    except Exception:
        longhorn_replicas = 3

    reset_existing = bool(values.get("reset_existing_cluster", True))
    open_firewall = bool(values.get("open_firewall", True))
    fetch_kubeconfig = bool(values.get("fetch_kubeconfig", True))

    servers = _norm_nodes(values.get("servers"),
                          values.get("ssh_user_default") or "root",
                          values.get("ssh_port_default") or 22)
    agents = _norm_nodes(values.get("agents"),
                         values.get("ssh_user_default") or "root",
                         values.get("ssh_port_default") or 22)

    if not servers:
        # Render an obvious error playbook rather than silently producing a
        # broken YAML that only fails at runtime.
        return (
            "---\n"
            "# ERROR: k3s template requires at least one server node with an IP.\n"
            "- hosts: localhost\n  gather_facts: false\n  tasks:\n"
            "    - ansible.builtin.fail:\n"
            "        msg: 'Add at least one server node (with IP) before saving this instance.'\n"
        )

    first = servers[0]
    extra_servers = servers[1:]

    # Version handling: 'stable'/'latest' → channel env; explicit vX.Y.Z+k3sN → version env.
    version_env_line = ""
    channel_env_line = ""
    if version_raw and version_raw.lower() not in ("stable", "latest", ""):
        version_env_line = f'INSTALL_K3S_VERSION="{version_raw}"'
    elif version_raw.lower() in ("stable", "latest"):
        channel_env_line = f'INSTALL_K3S_CHANNEL="{version_raw.lower()}"'
    else:
        channel_env_line = 'INSTALL_K3S_CHANNEL="stable"'
    version_prefix = " ".join(x for x in (version_env_line, channel_env_line) if x)

    # Assemble common server flags
    disables: List[str] = []
    if disable_traefik:
        disables.append("traefik")
    if disable_servicelb:
        disables.append("servicelb")
    if disable_local_storage:
        disables.append("local-storage")
    if disable_network_policy:
        disables.append("network-policy")

    server_flags: List[str] = []
    for d in disables:
        server_flags.append(f"--disable={d}")
    if flannel_backend and flannel_backend != "vxlan":
        if flannel_backend == "none":
            server_flags.append("--flannel-backend=none")
            server_flags.append("--disable-network-policy")
        else:
            server_flags.append(f"--flannel-backend={flannel_backend}")
    server_flags.append(f"--cluster-cidr={cluster_cidr}")
    server_flags.append(f"--service-cidr={service_cidr}")
    server_flags.append(f"--cluster-dns={cluster_dns}")
    server_flags.append("--write-kubeconfig-mode=0640")
    if cp_endpoint:
        # Add both hostname and any IP form to TLS SANs
        san = cp_endpoint.split(":")[0]
        server_flags.append(f"--tls-san={san}")

    # First server URL agents & extra servers point at
    first_join_host = cp_endpoint.split(":")[0] if cp_endpoint else first["ip"]
    first_server_url = f"https://{first_join_host}:6443"

    vf_lines = vars_files_lines(parse_vault_files(values.get("vault_files")))
    srv_group = f"{cluster}_servers"
    agt_group = f"{cluster}_agents"

    parts: List[str] = []
    parts.append("---")
    parts.append(f"# Rendered from template: {TEMPLATE['name']}")
    parts.append(f"# OpenSible k3s template generation: {K3S_TEMPLATE_GENERATION}")
    parts.append(f"# Cluster: {cluster} (HA={ha_mode}, servers={len(servers)}, agents={len(agents)})")
    parts.append(f"# k3s version/channel: {version_raw} | Flannel: {flannel_backend} | Pod CIDR: {cluster_cidr}")
    parts.append(f"# Inventory sidecar: inventories/{cluster}.yml")
    parts.append("")

    # ---------- Host patterns (inline, survive --limit) ----------
    def _hosts_pattern(nodes: List[Dict[str, Any]]) -> str:
        return ",".join(n["ip"] for n in nodes) + ","

    def _conn_vars_lines(nodes: List[Dict[str, Any]], indent: str = "    ") -> List[str]:
        if not nodes:
            return []
        lines = [f"{indent}_node_users:"]
        for n in nodes:
            lines.append(f"{indent}  {n['ip']}: {yaml_str(n['ssh_user'])}")
        lines.append(f"{indent}_node_ports:")
        for n in nodes:
            lines.append(f"{indent}  {n['ip']}: {n['ssh_port']}")
        lines.append(f"{indent}_node_names:")
        for n in nodes:
            lines.append(f"{indent}  {n['ip']}: {yaml_str(n['k3s_name'])}")
        return lines

    all_nodes = servers + agents
    all_pattern = _hosts_pattern(all_nodes)
    servers_pattern = _hosts_pattern(servers)
    first_pattern = _hosts_pattern([first])
    extra_servers_pattern = _hosts_pattern(extra_servers) if extra_servers else ""
    agents_pattern = _hosts_pattern(agents) if agents else ""

    # ============================================================
    # PLAY 1 — Prepare every node (kernel, sysctl, swap, firewall)
    # ============================================================
    parts.append("- name: Prepare nodes for k3s")
    parts.append(f"  hosts: {all_pattern}")
    parts.append(f"  become: {become}")
    parts.append("  gather_facts: true")
    parts.append("  any_errors_fatal: true")
    parts.extend(vf_lines)
    parts.append("  vars:")
    parts.extend(_conn_vars_lines(all_nodes))
    parts.append("  pre_tasks:")
    parts.append("    - name: Apply per-host SSH connection settings")
    parts.append("      ansible.builtin.set_fact:")
    parts.append("        ansible_user: \"{{ _node_users[inventory_hostname] | default(ansible_user) }}\"")
    parts.append("        ansible_port: \"{{ _node_ports[inventory_hostname] | default(ansible_port | default(22)) }}\"")
    parts.append("  tasks:")

    parts.append("    - name: Detect container/LXC environment (OrbStack, Proxmox LXC, etc.)")
    parts.append("      ansible.builtin.shell: |")
    parts.append("        set -e")
    parts.append("        virt=$(systemd-detect-virt 2>/dev/null || echo unknown)")
    parts.append("        echo \"virt=$virt\"")
    parts.append("      register: k3s_virt_probe")
    parts.append("      changed_when: false")
    parts.append("      failed_when: false")
    parts.append("")
    parts.append("    - name: Set fact for LXC/container-safe defaults")
    parts.append("      ansible.builtin.set_fact:")
    parts.append("        k3s_in_container: \"{{ 'lxc' in (k3s_virt_probe.stdout | default('')) or 'container' in (k3s_virt_probe.stdout | default('')) or 'docker' in (k3s_virt_probe.stdout | default('')) }}\"")
    parts.append("")

    if reset_existing:
        parts.append("    - name: Remove existing Kubernetes/k3s state before install")
        parts.append("      ansible.builtin.shell: |")
        parts.append("        set +e")
        parts.append("        echo '[pre-clean] removing existing kubeadm/k3s/rke2/microk8s state if present'")
        parts.append("        for unit in k3s k3s-agent rke2-server rke2-agent kubelet snap.microk8s.daemon-kubelite microk8s.daemon-kubelite; do")
        parts.append("          systemctl stop \"$unit\" 2>/dev/null || true")
        parts.append("          systemctl disable \"$unit\" 2>/dev/null || true")
        parts.append("          systemctl reset-failed \"$unit\" 2>/dev/null || true")
        parts.append("        done")
        parts.append("        if [ -x /usr/local/bin/k3s-uninstall.sh ]; then /usr/local/bin/k3s-uninstall.sh || true; fi")
        parts.append("        if [ -x /usr/local/bin/k3s-agent-uninstall.sh ]; then /usr/local/bin/k3s-agent-uninstall.sh || true; fi")
        parts.append("        if [ -x /usr/local/bin/rke2-uninstall.sh ]; then /usr/local/bin/rke2-uninstall.sh || true; fi")
        parts.append("        if [ -x /usr/local/bin/rke2-agent-uninstall.sh ]; then /usr/local/bin/rke2-agent-uninstall.sh || true; fi")
        parts.append("        if command -v kubeadm >/dev/null 2>&1; then")
        parts.append("          timeout 60s kubeadm reset -f --cri-socket=unix:///run/containerd/containerd.sock 2>/dev/null || timeout 60s kubeadm reset -f || true")
        parts.append("        fi")
        parts.append("        if command -v microk8s >/dev/null 2>&1; then")
        parts.append("          timeout 60s microk8s reset --destroy-storage 2>/dev/null || true")
        parts.append("          snap remove microk8s --purge 2>/dev/null || true")
        parts.append("        fi")
        parts.append("        # Stop any leftover Kubernetes API process that still owns 6443.")
        parts.append("        if command -v ss >/dev/null 2>&1; then")
        parts.append("          ss -H -ltnp 'sport = :6443' 2>/dev/null | while read -r line; do")
        parts.append("            echo \"[pre-clean] 6443 listener: $line\"")
        parts.append("            for pid in $(printf '%s\\n' \"$line\" | sed -n 's/.*pid=\\([0-9][0-9]*\\).*/\\1/p'); do")
        parts.append("              comm=$(ps -o comm= -p \"$pid\" 2>/dev/null | tr -d ' ')")
        parts.append("              case \"$comm\" in")
        parts.append("                kube-apiserver|k3s|rke2|kubelite|microk8s*) kill \"$pid\" 2>/dev/null || true; sleep 2; kill -9 \"$pid\" 2>/dev/null || true ;;")
        parts.append("                *) echo \"[pre-clean] not killing non-Kubernetes process pid=$pid comm=$comm\" ;;")
        parts.append("              esac")
        parts.append("            done")
        parts.append("          done")
        parts.append("        fi")
        parts.append("        # Unmount kubelet/CNI mounts before deleting directories.")
        parts.append("        awk '$2 ~ /^\\/var\\/lib\\/kubelet/ || $2 ~ /^\\/run\\/k3s/ || $2 ~ /^\\/run\\/flannel/ {print $2}' /proc/mounts 2>/dev/null | sort -r | xargs -r -n1 umount -fl 2>/dev/null || true")
        parts.append("        rm -rf /etc/kubernetes /var/lib/etcd /etc/cni/net.d /var/lib/cni /var/lib/kubelet \\")
        parts.append("               /etc/rancher/k3s /var/lib/rancher/k3s /run/k3s /run/flannel \\")
        parts.append("               /etc/rancher/rke2 /var/lib/rancher/rke2 /var/lib/rancher/agent \\")
        parts.append("               /var/snap/microk8s /snap/microk8s 2>/dev/null || true")
        parts.append("        for link in cni0 flannel.1 flannel-v6.1 kube-ipvs0 flannel-wg flannel-wg-v6 vxlan.calico; do ip link delete \"$link\" 2>/dev/null || true; done")
        parts.append("        ip link show 2>/dev/null | awk -F': ' '/^[0-9]+: cali/ {print $2}' | cut -d@ -f1 | xargs -r -n1 ip link delete 2>/dev/null || true")
        parts.append("        if command -v iptables-save >/dev/null 2>&1 && command -v iptables-restore >/dev/null 2>&1; then")
        parts.append("          iptables-save 2>/dev/null | grep -vE 'KUBE-|CNI-|FLANNEL|cali-|K3S' | iptables-restore 2>/dev/null || true")
        parts.append("        fi")
        parts.append("        if command -v ip6tables-save >/dev/null 2>&1 && command -v ip6tables-restore >/dev/null 2>&1; then")
        parts.append("          ip6tables-save 2>/dev/null | grep -vE 'KUBE-|CNI-|FLANNEL|cali-|K3S' | ip6tables-restore 2>/dev/null || true")
        parts.append("        fi")
        parts.append("        systemctl daemon-reload 2>/dev/null || true")
        parts.append("        for i in $(seq 1 20); do")
        parts.append("          if ! command -v ss >/dev/null 2>&1 || ! ss -H -ltn 'sport = :6443' 2>/dev/null | grep -q LISTEN; then")
        parts.append("            echo '[pre-clean] port 6443 is free'")
        parts.append("            exit 0")
        parts.append("          fi")
        parts.append("          sleep 1")
        parts.append("        done")
        parts.append("        echo '[pre-clean] warning: port 6443 is still in use after cleanup'")
        parts.append("        ss -H -ltnp 'sport = :6443' 2>/dev/null || true")
        parts.append("      args: { executable: /bin/bash }")
        parts.append("      failed_when: false")
        parts.append("      changed_when: true")
        parts.append("")

    parts.append("    - name: Disable swap now with bounded timeout (skipped in containers)")
    parts.append("      ansible.builtin.shell: |")
    parts.append("        set +e")
    parts.append("        active_swaps=$(awk 'NR > 1 { count++ } END { print count + 0 }' /proc/swaps 2>/dev/null || echo 0)")
    parts.append("        if [ \"$active_swaps\" -eq 0 ]; then")
    parts.append("          echo 'No active swap devices found.'")
    parts.append("          exit 0")
    parts.append("        fi")
    parts.append("        echo \"Found $active_swaps active swap device(s); disabling with timeout.\"")
    parts.append("        if command -v timeout >/dev/null 2>&1; then")
    parts.append("          timeout 15s swapoff -a")
    parts.append("          rc=$?")
    parts.append("          if [ \"$rc\" -eq 124 ]; then")
    parts.append("            echo 'swapoff timed out after 15s; continuing so automation does not hang.'")
    parts.append("          fi")
    parts.append("          exit 0")
    parts.append("        fi")
    parts.append("        python3 - <<'PY' || true")
    parts.append("        import subprocess")
    parts.append("        try:")
    parts.append("            subprocess.run(['swapoff', '-a'], timeout=15, check=False)")
    parts.append("        except Exception as exc:")
    parts.append("            print(f'swapoff skipped/timeout: {exc}')")
    parts.append("        PY")
    parts.append("      args: { executable: /bin/bash }")
    parts.append("      failed_when: false")
    parts.append("      changed_when: false")
    parts.append("      when: not k3s_in_container | bool")
    parts.append("")
    parts.append("    - name: Check if /etc/fstab exists")
    parts.append("      ansible.builtin.stat:")
    parts.append("        path: /etc/fstab")
    parts.append("      register: k3s_fstab_stat")
    parts.append("")
    parts.append("    - name: Disable swap in /etc/fstab (persistent)")
    parts.append("      ansible.builtin.replace:")
    parts.append("        path: /etc/fstab")
    parts.append("        regexp: '^([^#].*\\sswap\\s.*)$'")
    parts.append("        replace: '# \\1'")
    parts.append("      when: k3s_fstab_stat.stat.exists | bool and not k3s_in_container | bool")
    parts.append("      failed_when: false")
    parts.append("")
    parts.append("    - name: Load required kernel modules")
    parts.append("      ansible.builtin.shell: |")
    parts.append("        modprobe br_netfilter 2>/dev/null || true")
    parts.append("        modprobe overlay 2>/dev/null || true")
    parts.append("        modprobe nf_conntrack 2>/dev/null || true")
    parts.append("      changed_when: false")
    parts.append("      failed_when: false")
    parts.append("      when: not k3s_in_container | bool")
    parts.append("")
    parts.append("    - name: Persist kernel modules")
    parts.append("      ansible.builtin.copy:")
    parts.append("        dest: /etc/modules-load.d/k3s.conf")
    parts.append("        mode: '0644'")
    parts.append("        content: |")
    parts.append("          br_netfilter")
    parts.append("          overlay")
    parts.append("          nf_conntrack")
    parts.append("      when: not k3s_in_container | bool")
    parts.append("      failed_when: false")
    parts.append("")
    parts.append("    - name: Set sysctl for Kubernetes networking")
    parts.append("      ansible.posix.sysctl:")
    parts.append("        name: \"{{ item.name }}\"")
    parts.append("        value: \"{{ item.value }}\"")
    parts.append("        sysctl_file: /etc/sysctl.d/99-k3s.conf")
    parts.append("        state: present")
    parts.append("        reload: true")
    parts.append("      loop:")
    parts.append("        - { name: net.bridge.bridge-nf-call-iptables, value: '1' }")
    parts.append("        - { name: net.bridge.bridge-nf-call-ip6tables, value: '1' }")
    parts.append("        - { name: net.ipv4.ip_forward, value: '1' }")
    parts.append("      when: not k3s_in_container | bool")
    parts.append("      failed_when: false")
    parts.append("")

    if open_firewall:
        firewall_ports = ["6443/tcp", "10250/tcp", "8472/udp", "51820/udp"]
        if ha_mode:
            firewall_ports.extend(["2379/tcp", "2380/tcp"])
        parts.append("    - name: Detect firewall in use")
        parts.append("      ansible.builtin.shell: |")
        parts.append("        if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi active; then echo ufw")
        parts.append("        elif command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state 2>/dev/null | grep -qi running; then echo firewalld")
        parts.append("        else echo none")
        parts.append("        fi")
        parts.append("      register: k3s_fw")
        parts.append("      changed_when: false")
        parts.append("      failed_when: false")
        parts.append("")
        parts.append("    - name: Open k3s firewall ports (ufw)")
        parts.append("      ansible.builtin.shell: |")
        for p in firewall_ports:
            parts.append(f"        ufw allow {p} || true")
        parts.append("      when: k3s_fw.stdout | default('') == 'ufw'")
        parts.append("      changed_when: false")
        parts.append("      failed_when: false")
        parts.append("")
        parts.append("    - name: Open k3s firewall ports (firewalld)")
        parts.append("      ansible.builtin.shell: |")
        for p in firewall_ports:
            parts.append(f"        firewall-cmd --permanent --add-port={p} || true")
        parts.append("        firewall-cmd --reload || true")
        parts.append("      when: k3s_fw.stdout | default('') == 'firewalld'")
        parts.append("      changed_when: false")
        parts.append("      failed_when: false")
        parts.append("")

    if install_longhorn:
        parts.append("    - name: Install Longhorn prerequisites (open-iscsi, nfs-common)")
        parts.append("      ansible.builtin.package:")
        parts.append("        name: \"{{ item }}\"")
        parts.append("        state: present")
        parts.append("      loop: [open-iscsi, nfs-common]")
        parts.append("      failed_when: false")
        parts.append("")
        parts.append("    - name: Enable iscsid")
        parts.append("      ansible.builtin.service:")
        parts.append("        name: iscsid")
        parts.append("        state: started")
        parts.append("        enabled: true")
        parts.append("      failed_when: false")
        parts.append("")

    # ============================================================
    # PLAY 2 — Install first k3s server (bootstrap)
    # ============================================================
    parts.append(f"- name: Install first k3s server ({first['name']})")
    parts.append(f"  hosts: {first_pattern}")
    parts.append(f"  become: {become}")
    parts.append("  gather_facts: false")
    parts.append("  any_errors_fatal: true")
    parts.extend(vf_lines)
    parts.append("  vars:")
    parts.append(f"    k3s_token: {yaml_str(token)}")
    parts.extend(_conn_vars_lines([first]))
    parts.append("  pre_tasks:")
    parts.append("    - name: Apply per-host SSH connection settings")
    parts.append("      ansible.builtin.set_fact:")
    parts.append("        ansible_user: \"{{ _node_users[inventory_hostname] | default(ansible_user) }}\"")
    parts.append("        ansible_port: \"{{ _node_ports[inventory_hostname] | default(ansible_port | default(22)) }}\"")
    parts.append("  tasks:")

    first_exec_parts = list(server_flags)
    if ha_mode:
        first_exec_parts.insert(0, "--cluster-init")
    first_exec_parts.append(f"--node-name={first['k3s_name']}")
    first_exec_parts.append(f"--node-ip={first['ip']}")
    first_exec = " ".join(first_exec_parts)

    parts.append("    - name: Install first k3s server (bootstrap, with retry + diagnostics)")
    parts.append("      ansible.builtin.shell: |")
    parts.append("        set -o pipefail")
    parts.append("        cleanup_existing_kubernetes() {")
    parts.append("          set +e")
    parts.append("          for unit in k3s k3s-agent rke2-server rke2-agent kubelet snap.microk8s.daemon-kubelite microk8s.daemon-kubelite; do systemctl stop \"$unit\" 2>/dev/null || true; systemctl disable \"$unit\" 2>/dev/null || true; systemctl reset-failed \"$unit\" 2>/dev/null || true; done")
    parts.append("          if [ -x /usr/local/bin/k3s-uninstall.sh ]; then /usr/local/bin/k3s-uninstall.sh || true; fi")
    parts.append("          if [ -x /usr/local/bin/k3s-agent-uninstall.sh ]; then /usr/local/bin/k3s-agent-uninstall.sh || true; fi")
    parts.append("          if [ -x /usr/local/bin/rke2-uninstall.sh ]; then /usr/local/bin/rke2-uninstall.sh || true; fi")
    parts.append("          if [ -x /usr/local/bin/rke2-agent-uninstall.sh ]; then /usr/local/bin/rke2-agent-uninstall.sh || true; fi")
    parts.append("          if command -v kubeadm >/dev/null 2>&1; then timeout 60s kubeadm reset -f --cri-socket=unix:///run/containerd/containerd.sock 2>/dev/null || timeout 60s kubeadm reset -f || true; fi")
    parts.append("          if command -v microk8s >/dev/null 2>&1; then timeout 60s microk8s reset --destroy-storage 2>/dev/null || true; snap remove microk8s --purge 2>/dev/null || true; fi")
    parts.append("          if command -v ss >/dev/null 2>&1; then")
    parts.append("            ss -H -ltnp 'sport = :6443' 2>/dev/null | while read -r line; do")
    parts.append("              echo \"[k3s-bootstrap] 6443 listener before retry cleanup: $line\"")
    parts.append("              for pid in $(printf '%s\\n' \"$line\" | sed -n 's/.*pid=\\([0-9][0-9]*\\).*/\\1/p'); do")
    parts.append("                comm=$(ps -o comm= -p \"$pid\" 2>/dev/null | tr -d ' ')")
    parts.append("                case \"$comm\" in kube-apiserver|k3s|rke2|kubelite|microk8s*) kill \"$pid\" 2>/dev/null || true; sleep 2; kill -9 \"$pid\" 2>/dev/null || true ;; *) echo \"[k3s-bootstrap] non-Kubernetes 6443 owner kept: pid=$pid comm=$comm\" ;; esac")
    parts.append("              done")
    parts.append("            done")
    parts.append("          fi")
    parts.append("          awk '$2 ~ /^\\/var\\/lib\\/kubelet/ || $2 ~ /^\\/run\\/k3s/ || $2 ~ /^\\/run\\/flannel/ {print $2}' /proc/mounts 2>/dev/null | sort -r | xargs -r -n1 umount -fl 2>/dev/null || true")
    parts.append("          rm -rf /etc/kubernetes /var/lib/etcd /etc/cni/net.d /var/lib/cni /var/lib/kubelet /etc/rancher/k3s /var/lib/rancher/k3s /run/k3s /run/flannel /etc/rancher/rke2 /var/lib/rancher/rke2 /var/lib/rancher/agent 2>/dev/null || true")
    parts.append("          # Nuke stale kubeconfigs from prior kubeadm/k3s installs so k3s kubectl")
    parts.append("          # does not pick up old CA certs and hit x509 unknown-authority errors.")
    parts.append("          rm -f /root/.kube/config /root/.kube/config.bak /etc/kubernetes/admin.conf 2>/dev/null || true")
    parts.append("          for h in $(ls /home 2>/dev/null); do rm -f \"/home/$h/.kube/config\" 2>/dev/null || true; done")
    parts.append("          for link in cni0 flannel.1 flannel-v6.1 kube-ipvs0 flannel-wg flannel-wg-v6 vxlan.calico; do ip link delete \"$link\" 2>/dev/null || true; done")
    parts.append("          ip link show 2>/dev/null | awk -F': ' '/^[0-9]+: cali/ {print $2}' | cut -d@ -f1 | xargs -r -n1 ip link delete 2>/dev/null || true")
    parts.append("          systemctl daemon-reload 2>/dev/null || true")
    parts.append("          set -e")
    parts.append("        }")
    if reset_existing:
        parts.append("        cleanup_existing_kubernetes")
    else:
        parts.append("        if systemctl is-active --quiet k3s && [ -x /usr/local/bin/k3s ]; then echo '[k3s-bootstrap] k3s already active; skipping install'; exit 0; fi")
        parts.append("        cleanup_existing_kubernetes")
    parts.append("        # Detect containerized host (LXC/OrbStack/Docker) and pick a safe snapshotter.")
    parts.append("        EXTRA=\"\"")
    parts.append("        virt=\"$(systemd-detect-virt 2>/dev/null || echo unknown)\"")
    parts.append("        case \"$virt\" in")
    parts.append("          lxc|lxc-libvirt|docker|podman|container-other|openvz|systemd-nspawn)")
    parts.append("            EXTRA=\"--snapshotter=native\" ;;")
    parts.append("        esac")
    parts.append("        # Also force native if overlayfs-on-overlayfs is detected")
    parts.append("        if [ -z \"$EXTRA\" ] && grep -qE 'overlay .* overlay' /proc/mounts 2>/dev/null; then")
    parts.append("          if mount | awk '$3==\"/var/lib\" || $3==\"/\" {print $5}' | grep -q overlay; then")
    parts.append("            EXTRA=\"--snapshotter=native\"")
    parts.append("          fi")
    parts.append("        fi")
    parts.append("        attempt=0; max=3")
    parts.append("        while [ $attempt -lt $max ]; do")
    parts.append("          attempt=$((attempt+1))")
    parts.append("          echo \"[k3s-bootstrap] attempt $attempt/$max (extra=$EXTRA)\"")
    parts.append(f"          if curl -sfL https://get.k3s.io | {version_prefix} K3S_TOKEN='{{{{ k3s_token }}}}' sh -s - server {first_exec} $EXTRA; then")
    parts.append("            systemctl is-active --quiet k3s && exit 0")
    parts.append("          fi")
    parts.append("          echo '[k3s-bootstrap] k3s failed to start — dumping diagnostics:'")
    parts.append("          systemctl status k3s --no-pager -l 2>&1 | tail -n 40 || true")
    parts.append("          journalctl -xeu k3s --no-pager 2>&1 | tail -n 80 || true")
    parts.append("          echo '[k3s-bootstrap] cleaning up before retry'")
    parts.append("          cleanup_existing_kubernetes")
    parts.append("          # On second attempt, force native snapshotter regardless of detection")
    parts.append("          EXTRA=\"--snapshotter=native\"")
    parts.append("          sleep 10")
    parts.append("        done")
    parts.append("        echo '[k3s-bootstrap] exhausted retries'")
    parts.append("        exit 1")
    parts.append("      args:")
    parts.append("        executable: /bin/bash")
    parts.append("")
    parts.append("    - name: Wait for k3s API to be reachable on first server")
    parts.append("      ansible.builtin.wait_for:")
    parts.append("        host: \"{{ inventory_hostname }}\"")
    parts.append("        port: 6443")
    parts.append("        timeout: 300")
    parts.append("")
    parts.append("    - name: Wait for node-token file")
    parts.append("      ansible.builtin.wait_for:")
    parts.append("        path: /var/lib/rancher/k3s/server/node-token")
    parts.append("        timeout: 180")
    parts.append("")
    parts.append("    - name: Read node-token")
    parts.append("      ansible.builtin.slurp:")
    parts.append("        src: /var/lib/rancher/k3s/server/node-token")
    parts.append("      register: k3s_node_token_b64")
    parts.append("")
    parts.append("    - name: Publish join facts")
    parts.append("      ansible.builtin.set_fact:")
    parts.append("        k3s_node_token: \"{{ k3s_node_token_b64.content | b64decode | trim }}\"")
    parts.append(f"        k3s_server_url: {yaml_str(first_server_url)}")
    parts.append("")
    parts.append("    - name: Wait for first server node Ready")
    parts.append("      ansible.builtin.shell: |")
    parts.append("        set +e")
    parts.append("        # Force k3s kubectl to use the k3s-generated kubeconfig. A stale")
    parts.append("        # /root/.kube/config or KUBECONFIG env from a previous kubeadm/k3s")
    parts.append("        # install would otherwise cause x509 certificate signed by unknown authority.")
    parts.append("        unset KUBECONFIG")
    parts.append("        export KUBECONFIG=/etc/rancher/k3s/k3s.yaml")
    parts.append("        KUBECTL='/usr/local/bin/k3s kubectl --kubeconfig=/etc/rancher/k3s/k3s.yaml'")
    parts.append("        expected=\"{{ _node_names[inventory_hostname] }}\"")
    parts.append("        node_ip=\"{{ inventory_hostname }}\"")
    parts.append("        nodes=\"$($KUBECTL get nodes -o wide --no-headers 2>/tmp/k3s-nodes.err)\"")
    parts.append("        rc=$?")
    parts.append("        if [ $rc -ne 0 ]; then")
    parts.append("          echo \"[first-ready] kubectl failed rc=$rc\"")
    parts.append("          cat /tmp/k3s-nodes.err 2>/dev/null || true")
    parts.append("          systemctl status k3s --no-pager -l 2>&1 | tail -n 25 || true")
    parts.append("          journalctl -u k3s --no-pager 2>&1 | tail -n 40 || true")
    parts.append("          exit 1")
    parts.append("        fi")
    parts.append("        printf '%s\\n' \"$nodes\"")
    parts.append("        status=\"$(printf '%s\\n' \"$nodes\" | awk -v n=\"$expected\" '$1==n {print $2; exit}')\"")
    parts.append("        if [ \"$status\" != \"Ready\" ]; then")
    parts.append("          status=\"$(printf '%s\\n' \"$nodes\" | awk -v ip=\"$node_ip\" '$6==ip {print $2; exit}')\"")
    parts.append("        fi")
    parts.append("        if [ \"$status\" = \"Ready\" ]; then")
    parts.append("          echo READY")
    parts.append("          exit 0")
    parts.append("        fi")
    parts.append("        echo \"[first-ready] expected node '$expected' at IP '$node_ip' is not Ready yet (status=${status:-missing})\"")
    parts.append("        $KUBECTL get pods -A -o wide 2>/dev/null | tail -n 30 || true")
    parts.append("        journalctl -u k3s --no-pager 2>&1 | tail -n 30 || true")
    parts.append("        exit 1")
    parts.append("      environment:")
    parts.append("        KUBECONFIG: /etc/rancher/k3s/k3s.yaml")
    parts.append("      register: k3s_first_ready")
    parts.append("      changed_when: false")
    parts.append("      retries: 45")
    parts.append("      delay: 10")
    parts.append("      until: k3s_first_ready.rc == 0")
    parts.append("")

    # ============================================================
    # PLAY 3 — Join additional server nodes (HA)
    # ============================================================
    if extra_servers and ha_mode:
        parts.append("- name: Join additional k3s servers (HA embedded etcd)")
        parts.append(f"  hosts: {extra_servers_pattern}")
        parts.append(f"  become: {become}")
        parts.append("  gather_facts: false")
        parts.append("  serial: 1")
        parts.append("  any_errors_fatal: true")
        parts.extend(vf_lines)
        parts.append("  vars:")
        parts.append(f"    k3s_token: {yaml_str(token)}")
        parts.append(f"    k3s_server_url: {yaml_str(first_server_url)}")
        parts.extend(_conn_vars_lines(extra_servers))
        parts.append("  pre_tasks:")
        parts.append("    - name: Apply per-host SSH connection settings")
        parts.append("      ansible.builtin.set_fact:")
        parts.append("        ansible_user: \"{{ _node_users[inventory_hostname] | default(ansible_user) }}\"")
        parts.append("        ansible_port: \"{{ _node_ports[inventory_hostname] | default(ansible_port | default(22)) }}\"")
        parts.append("  tasks:")
        server_join_flags = list(server_flags)
        server_join_flags.append("--node-name={{ _node_names[inventory_hostname] }}")
        server_join_flags.append("--node-ip={{ inventory_hostname }}")
        server_join_exec = " ".join(server_join_flags)
        parts.append("    - name: Join server to control plane (with retry)")
        parts.append("      ansible.builtin.shell: |")
        parts.append("        set -o pipefail")
        parts.append("        EXTRA=\"\"")
        parts.append("        virt=\"$(systemd-detect-virt 2>/dev/null || echo unknown)\"")
        parts.append("        case \"$virt\" in lxc|lxc-libvirt|docker|podman|container-other|openvz|systemd-nspawn) EXTRA=\"--snapshotter=native\" ;; esac")
        parts.append("        attempt=0; max=5")
        parts.append("        while [ $attempt -lt $max ]; do")
        parts.append("          attempt=$((attempt+1))")
        parts.append("          echo \"[join-server] attempt $attempt/$max (extra=$EXTRA)\"")
        parts.append(f"          if curl -sfL https://get.k3s.io | {version_prefix} K3S_TOKEN='{{{{ k3s_token }}}}' K3S_URL='{{{{ k3s_server_url }}}}' sh -s - server {server_join_exec} $EXTRA; then")
        parts.append("            systemctl is-active --quiet k3s && exit 0")
        parts.append("          fi")
        parts.append("          echo '[join-server] failed, dumping diagnostics + cleaning up'")
        parts.append("          journalctl -xeu k3s --no-pager 2>&1 | tail -n 60 || true")
        parts.append("          if [ -x /usr/local/bin/k3s-uninstall.sh ]; then /usr/local/bin/k3s-uninstall.sh || true; fi")
        parts.append("          rm -rf /etc/rancher/k3s /var/lib/rancher/k3s /run/k3s /var/lib/kubelet || true")
        parts.append("          EXTRA=\"--snapshotter=native\"")
        parts.append("          sleep 15")
        parts.append("        done")
        parts.append("        echo '[join-server] exhausted retries'")
        parts.append("        exit 1")
        parts.append("      args:")
        parts.append("        executable: /bin/bash")
        parts.append("        creates: /usr/local/bin/k3s")
        parts.append("")

    # ============================================================
    # PLAY 4 — Join k3s agents
    # ============================================================
    if agents:
        parts.append("- name: Join k3s agents")
        parts.append(f"  hosts: {agents_pattern}")
        parts.append(f"  become: {become}")
        parts.append("  gather_facts: false")
        parts.append("  any_errors_fatal: false")
        parts.extend(vf_lines)
        parts.append("  vars:")
        parts.append(f"    k3s_token: {yaml_str(token)}")
        parts.append(f"    k3s_server_url: {yaml_str(first_server_url)}")
        parts.extend(_conn_vars_lines(agents))
        parts.append("  pre_tasks:")
        parts.append("    - name: Apply per-host SSH connection settings")
        parts.append("      ansible.builtin.set_fact:")
        parts.append("        ansible_user: \"{{ _node_users[inventory_hostname] | default(ansible_user) }}\"")
        parts.append("        ansible_port: \"{{ _node_ports[inventory_hostname] | default(ansible_port | default(22)) }}\"")
        parts.append("  tasks:")
        agent_flags = [
            "--node-name={{ _node_names[inventory_hostname] }}",
            "--node-ip={{ inventory_hostname }}",
        ]
        agent_exec = " ".join(agent_flags)
        parts.append("    - name: Install k3s agent (with retry)")
        parts.append("      ansible.builtin.shell: |")
        parts.append("        set -o pipefail")
        parts.append("        EXTRA=\"\"")
        parts.append("        virt=\"$(systemd-detect-virt 2>/dev/null || echo unknown)\"")
        parts.append("        case \"$virt\" in lxc|lxc-libvirt|docker|podman|container-other|openvz|systemd-nspawn) EXTRA=\"--snapshotter=native\" ;; esac")
        parts.append("        attempt=0; max=5")
        parts.append("        while [ $attempt -lt $max ]; do")
        parts.append("          attempt=$((attempt+1))")
        parts.append("          echo \"[join-agent] attempt $attempt/$max (extra=$EXTRA)\"")
        parts.append(f"          if curl -sfL https://get.k3s.io | {version_prefix} K3S_TOKEN='{{{{ k3s_token }}}}' K3S_URL='{{{{ k3s_server_url }}}}' sh -s - agent {agent_exec} $EXTRA; then")
        parts.append("            systemctl is-active --quiet k3s-agent && exit 0")
        parts.append("          fi")
        parts.append("          echo '[join-agent] failed, dumping diagnostics + cleaning up'")
        parts.append("          journalctl -xeu k3s-agent --no-pager 2>&1 | tail -n 60 || true")
        parts.append("          if [ -x /usr/local/bin/k3s-agent-uninstall.sh ]; then /usr/local/bin/k3s-agent-uninstall.sh || true; fi")
        parts.append("          rm -rf /etc/rancher/k3s /var/lib/rancher/k3s /run/k3s /var/lib/kubelet || true")
        parts.append("          EXTRA=\"--snapshotter=native\"")
        parts.append("          sleep 15")
        parts.append("        done")
        parts.append("        echo '[join-agent] exhausted retries'")
        parts.append("        exit 1")
        parts.append("      args:")
        parts.append("        executable: /bin/bash")
        parts.append("        creates: /usr/local/bin/k3s")
        parts.append("")

    # ============================================================
    # PLAY 5 — Post-install (verify, optional Longhorn, fetch kubeconfig)
    # ============================================================
    parts.append("- name: Post-install verification and add-ons")
    parts.append(f"  hosts: {first_pattern}")
    parts.append(f"  become: {become}")
    parts.append("  gather_facts: false")
    parts.extend(vf_lines)
    parts.append("  vars:")
    parts.extend(_conn_vars_lines([first]))
    parts.append("  pre_tasks:")
    parts.append("    - name: Apply per-host SSH connection settings")
    parts.append("      ansible.builtin.set_fact:")
    parts.append("        ansible_user: \"{{ _node_users[inventory_hostname] | default(ansible_user) }}\"")
    parts.append("        ansible_port: \"{{ _node_ports[inventory_hostname] | default(ansible_port | default(22)) }}\"")
    parts.append("  tasks:")
    expected_nodes = len(servers) + len(agents)
    parts.append("    - name: Wait for all nodes to register (Ready)")
    parts.append("      ansible.builtin.shell: |")
    parts.append("        /usr/local/bin/k3s kubectl --kubeconfig=/etc/rancher/k3s/k3s.yaml get nodes --no-headers | awk '{print $2}' | grep -c '^Ready$' || true")
    parts.append("      environment:")
    parts.append("        KUBECONFIG: /etc/rancher/k3s/k3s.yaml")
    parts.append("      register: k3s_ready_count")
    parts.append("      changed_when: false")
    parts.append("      retries: 60")
    parts.append("      delay: 10")
    parts.append(f"      until: (k3s_ready_count.stdout | default('0') | int) >= {expected_nodes}")
    parts.append("      failed_when: false")
    parts.append("")
    parts.append("    - name: Show cluster nodes")
    parts.append("      ansible.builtin.command: /usr/local/bin/k3s kubectl --kubeconfig=/etc/rancher/k3s/k3s.yaml get nodes -o wide")
    parts.append("      environment:")
    parts.append("        KUBECONFIG: /etc/rancher/k3s/k3s.yaml")
    parts.append("      register: k3s_nodes_out")
    parts.append("      changed_when: false")
    parts.append("      failed_when: false")
    parts.append("")
    parts.append("    - name: Display node listing")
    parts.append("      ansible.builtin.debug:")
    parts.append("        msg: \"{{ k3s_nodes_out.stdout_lines | default([]) }}\"")
    parts.append("")





    if install_longhorn:
        parts.append("    - name: Install Longhorn via Helm-manifest (k3s HelmChart CRD)")
        parts.append("      ansible.builtin.copy:")
        parts.append("        dest: /var/lib/rancher/k3s/server/manifests/longhorn.yaml")
        parts.append("        mode: '0644'")
        parts.append("        content: |")
        parts.append("          apiVersion: v1")
        parts.append("          kind: Namespace")
        parts.append("          metadata:")
        parts.append("            name: longhorn-system")
        parts.append("          ---")
        parts.append("          apiVersion: helm.cattle.io/v1")
        parts.append("          kind: HelmChart")
        parts.append("          metadata:")
        parts.append("            name: longhorn")
        parts.append("            namespace: kube-system")
        parts.append("          spec:")
        parts.append("            targetNamespace: longhorn-system")
        parts.append("            repo: https://charts.longhorn.io")
        parts.append("            chart: longhorn")
        parts.append(f"            version: {longhorn_version}")
        parts.append("            valuesContent: |-")
        parts.append(f"              persistence:")
        parts.append(f"                defaultClassReplicaCount: {longhorn_replicas}")
        parts.append("                defaultClass: true")
        parts.append("")

    if fetch_kubeconfig:
        parts.append("    - name: Read kubeconfig from first server")
        parts.append("      ansible.builtin.slurp:")
        parts.append("        src: /etc/rancher/k3s/k3s.yaml")
        parts.append("      register: k3s_kubeconfig_b64")
        parts.append("")
        parts.append("    - name: Ensure local ~/.kube exists")
        parts.append("      ansible.builtin.file:")
        parts.append("        path: \"{{ lookup('env', 'HOME') }}/.kube\"")
        parts.append("        state: directory")
        parts.append("        mode: '0700'")
        parts.append("      delegate_to: localhost")
        parts.append("      become: false")
        parts.append("      run_once: true")
        parts.append("")
        api_host = cp_endpoint.split(":")[0] if cp_endpoint else first["ip"]
        parts.append(f"    - name: Write kubeconfig to controller (~/.kube/{cluster}.yaml)")
        parts.append("      ansible.builtin.copy:")
        parts.append(f"        dest: \"{{{{ lookup('env', 'HOME') }}}}/.kube/{cluster}.yaml\"")
        parts.append("        mode: '0600'")
        # Single-quoted YAML scalar so embedded double-quotes in the Jinja
        # expression don't collide with the outer quoting.
        parts.append(f"        content: '{{{{ (k3s_kubeconfig_b64.content | b64decode) | replace(\"127.0.0.1\", \"{api_host}\") | replace(\"default\", \"{cluster}\") }}}}'")
        parts.append("      delegate_to: localhost")
        parts.append("      become: false")
        parts.append("      run_once: true")
        parts.append("")

    # ============================================================
    # PLAY 6 — Install root kubeconfig on every server node so
    # `kubectl` on any control-plane host uses the current k3s CA
    # (fixes "x509: certificate signed by unknown authority" when
    # a stale ~/.kube/config from a prior kubeadm/k3s install exists).
    # ============================================================
    parts.append(f"- name: Install root kubeconfig on all server nodes")
    parts.append(f"  hosts: {servers_pattern}")
    parts.append(f"  become: {become}")
    parts.append("  gather_facts: false")
    parts.extend(vf_lines)
    parts.append("  vars:")
    parts.extend(_conn_vars_lines(servers))
    parts.append("  pre_tasks:")
    parts.append("    - name: Apply per-host SSH connection settings")
    parts.append("      ansible.builtin.set_fact:")
    parts.append("        ansible_user: \"{{ _node_users[inventory_hostname] | default(ansible_user) }}\"")
    parts.append("        ansible_port: \"{{ _node_ports[inventory_hostname] | default(ansible_port | default(22)) }}\"")
    parts.append("  tasks:")
    parts.append("    - name: Wait for /etc/rancher/k3s/k3s.yaml")
    parts.append("      ansible.builtin.wait_for:")
    parts.append("        path: /etc/rancher/k3s/k3s.yaml")
    parts.append("        timeout: 120")
    parts.append("    - name: Refresh /root/.kube/config from k3s")
    parts.append("      ansible.builtin.shell: |")
    parts.append("        set -e")
    parts.append("        install -d -m 0700 /root/.kube")
    parts.append("        cp -f /etc/rancher/k3s/k3s.yaml /root/.kube/config")
    parts.append("        chmod 0600 /root/.kube/config")
    parts.append("        chown root:root /root/.kube/config")
    parts.append("        if ! grep -q 'KUBECONFIG=/root/.kube/config' /root/.bashrc 2>/dev/null; then")
    parts.append("          echo 'export KUBECONFIG=/root/.kube/config' >> /root/.bashrc")
    parts.append("        fi")
    parts.append("      changed_when: false")
    parts.append("")

    return "\n".join(parts)

