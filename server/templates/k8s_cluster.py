"""Template: Full-featured Kubernetes cluster bootstrap (kubeadm) with per-node inputs.

Renders:
  - a playbook (`playbooks/<cluster>-cluster.yml`) that:
      * prepares each node (containerd, kernel modules, sysctl, swap off,
        kubeadm/kubelet/kubectl apt repo + install)
      * initializes the first control-plane with `kubeadm init`
      * joins additional control-planes (HA mode)
      * joins worker nodes
      * installs a CNI (Calico or Flannel) and optional add-ons
      * fetches the admin kubeconfig locally
  - a sidecar inventory (`inventories/<cluster>.yml`) built from the per-node
    inputs so the play is directly runnable and version-controlled with the
    rest of the repo (GitOps).
"""
from __future__ import annotations

from typing import Any, Dict, List

from ._common import (  # noqa: F401
    slugify, yaml_str, render_hosts,
    VAULT_FILES_VARIABLE, parse_vault_files, vars_files_lines,
)


K8S_TEMPLATE_GENERATION = "2026-07-metrics-tls-v10"


TEMPLATE = {
    "id": "k8s-cluster",
    "name": "Kubernetes Cluster (kubeadm)",
    "category": "Kubernetes",
    "icon": "boxes",
    "description": (
        "Full cluster wizard using upstream Kubernetes (kubeadm + containerd). "
        "Define control-plane & worker nodes with IPs, choose HA mode, pod & service "
        "CIDRs, CNI (Calico/Flannel), add-ons. Generates a playbook + inventory in "
        "your repo so everything is GitOps-managed."
    ),
    "tags": ["kubernetes", "kubeadm", "k8s", "cluster", "ha", "gitops"],
    "variables": [
        {"name": "cluster_name", "label": "Cluster name", "type": "string", "required": True,
         "placeholder": "prod-k8s", "help": "Used as filename slug and inventory group prefix."},
        {"name": "ha_mode", "label": "HA mode (stacked etcd on multiple control-plane nodes)",
         "type": "boolean", "default": False,
         "help": "When enabled additional control-plane nodes join the first one with --control-plane."},
        {"name": "control_plane_endpoint", "label": "Control-plane endpoint (VIP or LB, host:port)",
         "type": "string", "default": "",
         "placeholder": "k8s-api.example.com:6443",
         "help": "Recommended for HA mode. If empty, the first control-plane IP is used so the install can still complete."},
        {"name": "kubernetes_version", "label": "Kubernetes version (e.g. 1.30.4)",
         "type": "string", "default": "1.30.4",
         "placeholder": "1.30.4"},
        {"name": "pod_cidr", "label": "Pod (cluster) CIDR", "type": "string", "default": "10.244.0.0/16"},
        {"name": "service_cidr", "label": "Service CIDR", "type": "string", "default": "10.96.0.0/12"},
        {"name": "cni_plugin", "label": "CNI plugin",
         "type": "select", "default": "calico",
         "options": [
             {"value": "calico", "label": "Calico"},
             {"value": "flannel", "label": "Flannel"},
             {"value": "none", "label": "None (install manually)"},
         ]},
        {"name": "container_runtime", "label": "Container runtime",
         "type": "select", "default": "containerd",
         "options": [
             {"value": "containerd", "label": "containerd (recommended)"},
         ]},
        {"name": "kube_proxy_mode", "label": "kube-proxy mode",
         "type": "select", "default": "iptables",
         "options": [
             {"value": "iptables", "label": "iptables (default, legacy backend forced on Debian/Ubuntu)"},
             {"value": "ipvs", "label": "ipvs (skip iptables, uses IPVS load balancer — needs ipvsadm + ip_vs modules)"},
              {"value": "none", "label": "Disable kube-proxy (only for CNI=None / Cilium-style manual proxy replacement)"},
         ],
          "help": "Choose 'ipvs' to avoid kube-proxy iptables mode. 'none' is only valid when you manually install a CNI with kube-proxy replacement."},
        {"name": "reset_existing_cluster", "label": "Reset existing kubeadm state before install",
         "type": "boolean", "default": False,
         "help": "Dangerous cleanup for failed/dirty bootstrap attempts. Runs kubeadm reset and removes old CNI/control-plane state on selected nodes before rebuilding."},
        {"name": "install_metrics_server", "label": "Install metrics-server add-on",
         "type": "boolean", "default": True},
        {"name": "storage_provisioner", "label": "Persistent storage / default StorageClass",
         "type": "select", "default": "none",
         "options": [
             {"value": "none", "label": "None (install later)"},
             {"value": "longhorn", "label": "Longhorn (distributed block storage)"},
             {"value": "local-path", "label": "Rancher local-path-provisioner (single-node friendly)"},
             {"value": "openebs-hostpath", "label": "OpenEBS hostpath"},
             {"value": "nfs-subdir", "label": "NFS subdir external provisioner"},
         ],
         "help": "Installs a StorageClass so PVCs work out-of-the-box. Longhorn auto-installs open-iscsi + nfs-common on all nodes."},
        {"name": "storage_default_class", "label": "Mark installed StorageClass as cluster default",
         "type": "boolean", "default": True},
        {"name": "longhorn_version", "label": "Longhorn version (e.g. v1.7.2)",
         "type": "string", "default": "v1.7.2",
         "help": "Only used when storage_provisioner = longhorn."},
        {"name": "longhorn_replica_count", "label": "Longhorn default replica count",
         "type": "number", "default": 3,
         "help": "Set to 1 for single/2-node labs; 3 for production."},
        {"name": "nfs_server", "label": "NFS server (host or IP)", "type": "string", "default": "",
         "help": "Required when storage_provisioner = nfs-subdir."},
        {"name": "nfs_path", "label": "NFS exported path", "type": "string", "default": "/srv/nfs/k8s",
         "help": "Required when storage_provisioner = nfs-subdir."},
        {"name": "allow_scheduling_on_control_plane",
         "label": "Allow workloads on control-plane nodes (untaint)",
         "type": "boolean", "default": False,
         "help": "Useful for single-node or lab clusters."},
        {"name": "fetch_kubeconfig", "label": "Fetch kubeconfig to controller",
         "type": "boolean", "default": True,
         "help": "Downloads /etc/kubernetes/admin.conf from the first control-plane to ~/.kube/<cluster>.yaml."},
        {"name": "ssh_user_default", "label": "Default SSH user for nodes", "type": "string", "default": "root"},
        {"name": "ssh_port_default", "label": "Default SSH port", "type": "number", "default": 22},
        {"name": "control_planes", "label": "Control-plane nodes",
         "type": "nodes", "required": True,
         "help": "First entry becomes the bootstrap control-plane (kubeadm init).",
         "default": [{"name": "cp-1", "ip": "", "ssh_user": "", "ssh_port": ""}]},
        {"name": "workers", "label": "Worker nodes",
         "type": "nodes",
         "default": []},
        {"name": "become", "label": "Run as sudo (become)", "type": "boolean", "default": True},
        VAULT_FILES_VARIABLE,
    ],
}


def suggested_filename(values: Dict[str, Any]) -> str:
    return f"{slugify(values.get('cluster_name'), 'cluster')}-cluster.yml"


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
        # Kubernetes node names must be stable DNS-1123 labels. Do not rely on
        # the OS hostname from cloned VMs; duplicated hostnames cause kubelet TLS
        # bootstrap and stacked-etcd peer identity failures during HA joins.
        k8s_name = slugify(name, f"node-{i+1}")[:63].strip("-") or f"node-{i+1}"
        out.append({"name": name, "k8s_name": k8s_name, "ip": ip, "ssh_user": user, "ssh_port": port})
    return out


def _inventory_yaml(cluster: str, cps: List[Dict[str, Any]], workers: List[Dict[str, Any]]) -> str:
    cp_group = f"{cluster}_control_plane"
    wk_group = f"{cluster}_workers"
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
    parts.append(f"    {cp_group}:")
    parts.append("      hosts:")
    parts.extend(hosts_block(cps))
    parts.append(f"    {wk_group}:")
    parts.append("      hosts:")
    parts.extend(hosts_block(workers))
    parts.append(f"    {all_group}:")
    parts.append("      children:")
    parts.append(f"        {cp_group}: {{}}")
    parts.append(f"        {wk_group}: {{}}")
    return "\n".join(parts) + "\n"


def render(values: Dict[str, Any], targets: Dict[str, Any]) -> str:
    cluster = slugify(values.get("cluster_name") or "cluster", "cluster")
    cp_group = f"{cluster}_control_plane"
    wk_group = f"{cluster}_workers"
    all_group = f"{cluster}_cluster"

    version = str(values.get("kubernetes_version") or "1.30.4").strip().lstrip("v")
    # apt repo uses only major.minor
    minor = ".".join(version.split(".")[:2])
    become = "true" if values.get("become", True) else "false"
    ha_mode = bool(values.get("ha_mode"))
    cp_endpoint = str(values.get("control_plane_endpoint") or "").strip()
    pod_cidr = values.get("pod_cidr") or "10.244.0.0/16"
    service_cidr = values.get("service_cidr") or "10.96.0.0/12"
    cni = (values.get("cni_plugin") or "calico").lower()
    reset_existing = bool(values.get("reset_existing_cluster", False))
    install_metrics = bool(values.get("install_metrics_server", True))
    storage = str(values.get("storage_provisioner") or "none").lower().strip()
    storage_default = bool(values.get("storage_default_class", True))
    longhorn_version = str(values.get("longhorn_version") or "v1.7.2").strip()
    try:
        longhorn_replicas = int(values.get("longhorn_replica_count") or 3)
    except Exception:
        longhorn_replicas = 3
    nfs_server = str(values.get("nfs_server") or "").strip()
    nfs_path = str(values.get("nfs_path") or "/srv/nfs/k8s").strip()
    allow_cp_sched = bool(values.get("allow_scheduling_on_control_plane", False))
    fetch_kubeconfig = bool(values.get("fetch_kubeconfig", True))
    kube_proxy_mode = str(values.get("kube_proxy_mode") or "iptables").lower().strip()
    if kube_proxy_mode not in ("iptables", "ipvs", "none"):
        kube_proxy_mode = "iptables"
    requested_kube_proxy_mode = kube_proxy_mode
    # Calico/Flannel do not replace Kubernetes Service routing by default. If
    # kube-proxy is skipped, calico-node's install-cni init container commonly
    # cannot reach the apiserver Service (10.96.0.1), leaving every node
    # NotReady with "CNI plugin not initialized". Treat "none" as IPVS for
    # built-in Calico/Flannel so users can avoid iptables mode without breaking
    # the cluster. True kube-proxy-less mode remains available with CNI=None for
    # users who install Cilium/Calico eBPF manually.
    if kube_proxy_mode == "none" and cni in ("calico", "flannel"):
        kube_proxy_mode = "ipvs"

    cps = _norm_nodes(values.get("control_planes"),
                      values.get("ssh_user_default") or "root",
                      values.get("ssh_port_default") or 22)
    workers = _norm_nodes(values.get("workers"),
                          values.get("ssh_user_default") or "root",
                          values.get("ssh_port_default") or 22)
    first_cp_name = cps[0]["name"] if cps else ""
    vf_lines = vars_files_lines(parse_vault_files(values.get("vault_files")))

    parts: List[str] = []
    parts.append("---")
    parts.append(f"# Rendered from template: {TEMPLATE['name']}")
    parts.append(f"# OpenSible k8s template generation: {K8S_TEMPLATE_GENERATION}")
    parts.append(f"# Cluster: {cluster} (HA={ha_mode}, control-plane={len(cps)}, workers={len(workers)})")
    parts.append(f"# Kubernetes: v{version} | CNI: {cni} | Pod CIDR: {pod_cidr}")
    if requested_kube_proxy_mode != kube_proxy_mode:
        parts.append(f"# kube-proxy: requested {requested_kube_proxy_mode}, using {kube_proxy_mode} because {cni} does not replace Service routing by default")
    else:
        parts.append(f"# kube-proxy: {kube_proxy_mode}")
    parts.append(f"# Inventory sidecar: inventories/{cluster}.yml")
    parts.append("")

    # ---- Build inline host patterns (no groups needed; survives --limit) ----
    def _hosts_pattern(nodes: List[Dict[str, Any]]) -> str:
        # Inline comma-separated list becomes an implicit host pattern.
        # Trailing comma ensures Ansible treats it as a list of hosts, not a group.
        return ",".join(n["ip"] for n in nodes) + ","

    def _conn_vars_lines(nodes: List[Dict[str, Any]], indent: str = "    ") -> List[str]:
        """Emit play-level vars mapping each inventory_hostname (IP) -> user/port."""
        if not nodes:
            return []
        lines = [f"{indent}_node_users:"]
        for n in nodes:
            lines.append(f"{indent}  {n['ip']}: {yaml_str(n['ssh_user'])}")
        lines.append(f"{indent}_node_ports:")
        for n in nodes:
            lines.append(f"{indent}  {n['ip']}: {n['ssh_port']}")
        lines.append(f"{indent}_node_k8s_names:")
        for n in nodes:
            lines.append(f"{indent}  {n['ip']}: {yaml_str(n['k8s_name'])}")
        lines.append(f"{indent}ansible_user: \"{{{{ _node_users[inventory_hostname] | default('root') }}}}\"")
        lines.append(f"{indent}ansible_port: \"{{{{ _node_ports[inventory_hostname] | default(22) }}}}\"")
        lines.append(f"{indent}ansible_python_interpreter: /usr/bin/python3")
        lines.append(f"{indent}k8s_node_name: \"{{{{ _node_k8s_names[inventory_hostname] | default(inventory_hostname | replace('.', '-')) }}}}\"")
        return lines

    all_nodes = cps + workers
    peer_hosts_lines: List[str] = []
    for n in all_nodes:
        aliases = [n["k8s_name"]]
        if n["name"] != n["k8s_name"]:
            aliases.append(n["name"])
        peer_hosts_lines.append(f"          {n['ip']} {' '.join(dict.fromkeys(aliases))}")
    all_hosts_pat = _hosts_pattern(all_nodes) if all_nodes else "localhost"
    first_cp_pat = (cps[0]["ip"] + ",") if cps else "localhost"
    workers_pat = _hosts_pattern(workers) if workers else ""
    extra_cps_pat = _hosts_pattern(cps[1:]) if len(cps) > 1 else ""
    first_cp_ip = cps[0]["ip"] if cps else ""
    effective_cp_endpoint = cp_endpoint
    if ha_mode and len(cps) > 1 and not effective_cp_endpoint and first_cp_ip:
        effective_cp_endpoint = f"{first_cp_ip}:6443"
    apiserver_sans = [n["ip"] for n in cps] + [n["k8s_name"] for n in cps]
    if effective_cp_endpoint:
        apiserver_sans.append(effective_cp_endpoint.split(":", 1)[0])
    apiserver_sans = [s for s in dict.fromkeys(apiserver_sans) if s]
    cp_api_endpoints = []
    if cp_endpoint:
        cp_api_endpoints.append(cp_endpoint if ":" in cp_endpoint else f"{cp_endpoint}:6443")
    cp_api_endpoints.extend(f"{n['ip']}:6443" for n in cps)
    cp_api_endpoints = list(dict.fromkeys(e for e in cp_api_endpoints if e and not e.startswith(":")))

    def _cp_api_endpoint_vars(indent: str = "    ") -> List[str]:
        lines = [f"{indent}cp_api_endpoints:"]
        for endpoint in cp_api_endpoints:
            lines.append(f"{indent}  - {yaml_str(endpoint)}")
        return lines

    # ---- Common node preparation (all nodes) ----
    parts.append(f"- name: \"k8s :: prepare nodes ({cluster})\"")
    parts.append(f"  hosts: \"{all_hosts_pat}\"")
    parts.append(f"  become: {become}")
    parts.append("  gather_facts: false")
    parts.extend(vf_lines)
    parts.append("  vars:")
    parts.extend(_conn_vars_lines(all_nodes))
    parts.append("  tasks:")

    parts.append("    - name: Bootstrap Python (raw)")
    parts.append("      ansible.builtin.raw: |")
    parts.append("        set -e")
    parts.append("        if ! command -v python3 >/dev/null 2>&1; then")
    parts.append("          if command -v apt-get >/dev/null 2>&1; then")
    parts.append("            apt-get update && apt-get install -y python3")
    parts.append("          elif command -v dnf >/dev/null 2>&1; then")
    parts.append("            dnf install -y python3")
    parts.append("          elif command -v yum >/dev/null 2>&1; then")
    parts.append("            yum install -y python3")
    parts.append("          fi")
    parts.append("        fi")
    parts.append("      changed_when: false")
    parts.append("    - name: Gather facts")
    parts.append("      ansible.builtin.setup:")
    # Ensure all cluster peers resolve each other by short hostname — required
    # for stacked-etcd HA (etcd peer URLs use hostnames) and stable apiserver
    # advertisement. Without this etcd/apiserver crash-loop and Calico stalls
    # at Init:1/3 waiting for a reachable API.
    parts.append("    - name: Ensure /etc/hosts has cluster peer entries")
    parts.append("      ansible.builtin.blockinfile:")
    parts.append("        path: /etc/hosts")
    parts.append("        marker: \"# {mark} ANSIBLE MANAGED: k8s cluster peers\"")
    parts.append("        block: |")
    parts.extend(peer_hosts_lines)
    parts.append("        create: true")
    parts.append("        mode: '0644'")
    parts.append("    - name: Set unique Kubernetes node hostname")
    parts.append("      ansible.builtin.hostname:")
    parts.append("        name: '{{ k8s_node_name }}'")
    parts.append("        use: systemd")
    if reset_existing:
        parts.append("    - name: Clean reinstall - purge kubeadm, CNI, and runtime state")
        parts.append("      ansible.builtin.shell: |")
        parts.append("        set +e")
        parts.append("        export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
        parts.append("        systemctl stop kubelet 2>/dev/null || true")
        parts.append("        if command -v crictl >/dev/null 2>&1; then")
        parts.append("          crictl --runtime-endpoint unix:///run/containerd/containerd.sock stopp $(crictl --runtime-endpoint unix:///run/containerd/containerd.sock pods -q 2>/dev/null) 2>/dev/null || true")
        parts.append("          crictl --runtime-endpoint unix:///run/containerd/containerd.sock rm -fa 2>/dev/null || true")
        parts.append("          crictl --runtime-endpoint unix:///run/containerd/containerd.sock rmp -fa 2>/dev/null || true")
        parts.append("        fi")
        parts.append("        if command -v kubeadm >/dev/null 2>&1; then")
        parts.append("          kubeadm reset -f --cri-socket=unix:///run/containerd/containerd.sock || kubeadm reset -f || true")
        parts.append("        fi")
        parts.append("        systemctl stop kubelet 2>/dev/null || true")
        parts.append("        awk '$2 ~ /^\\/var\\/lib\\/kubelet/ {print $2}' /proc/mounts | sort -r | while read -r mountpoint; do")
        parts.append("          umount -lf \"$mountpoint\" 2>/dev/null || true")
        parts.append("        done")
        parts.append("        rm -rf /etc/kubernetes /var/lib/etcd /var/lib/kubelet /etc/cni/net.d /var/lib/cni")
        parts.append("        rm -rf /var/run/calico /var/lib/calico /run/flannel /root/.kube/config /root/.kube/bootstrap-local.conf")
        parts.append("        rm -rf /var/log/pods /var/log/containers")
        parts.append("        if command -v ip >/dev/null 2>&1; then")
        parts.append("          for link in cni0 flannel.1 vxlan.calico tunl0 kube-ipvs0; do ip link delete \"$link\" 2>/dev/null || true; done")
        parts.append("          ip -o link show 2>/dev/null | awk -F': ' '{print $2}' | cut -d@ -f1 | grep -E '^(cali|flannel|vxlan\\.calico)' | while read -r link; do")
        parts.append("            ip link delete \"$link\" 2>/dev/null || true")
        parts.append("          done")
        parts.append("        fi")
        parts.append("        for bin in iptables ip6tables; do")
        parts.append("          command -v \"$bin\" >/dev/null 2>&1 || continue")
        parts.append("          for table in filter nat mangle raw; do")
        parts.append("            $bin -t \"$table\" -S 2>/dev/null | awk '/^-A/ && $0 ~ / -j (KUBE|CNI|CALI|FLANNEL|WEAVE)/ {sub(/^-A /, \"-D \"); print}' | while read -r rule; do")
        parts.append("              $bin -t \"$table\" $rule 2>/dev/null || true")
        parts.append("            done")
        parts.append("            $bin -t \"$table\" -S 2>/dev/null | awk '/^-N (KUBE|CNI|CALI|FLANNEL|WEAVE)/ {print $2}' | while read -r chain; do")
        parts.append("              $bin -t \"$table\" -F \"$chain\" 2>/dev/null || true")
        parts.append("              $bin -t \"$table\" -X \"$chain\" 2>/dev/null || true")
        parts.append("            done")
        parts.append("          done")
        parts.append("        done")
        parts.append("        if command -v ipvsadm >/dev/null 2>&1; then ipvsadm --clear 2>/dev/null || true; fi")
        parts.append("        systemctl daemon-reload 2>/dev/null || true")
        parts.append("        systemctl reset-failed kubelet containerd 2>/dev/null || true")
        parts.append("        systemctl start containerd 2>/dev/null || true")
        parts.append("      args: {executable: /bin/bash}")
        parts.append("      changed_when: true")
        parts.append("    - name: Clean reinstall - verify kubeadm state is absent")
        parts.append("      ansible.builtin.shell: |")
        parts.append("        set -e")
        parts.append("        for path in /etc/kubernetes/admin.conf /etc/kubernetes/kubelet.conf /etc/kubernetes/manifests/kube-apiserver.yaml /var/lib/etcd/member; do")
        parts.append("          if [ -e \"$path\" ]; then")
        parts.append("            echo \"stale Kubernetes state remains: $path\"")
        parts.append("            exit 1")
        parts.append("          fi")
        parts.append("        done")
        parts.append("      args: {executable: /bin/bash}")
        parts.append("      changed_when: false")
        parts.append("    - name: Clean reinstall - verify control-plane ports are free")
        parts.append("      ansible.builtin.shell: |")
        parts.append("        set -e")
        parts.append("        if command -v ss >/dev/null 2>&1; then")
        parts.append("          busy=$(ss -ltnH | awk '{print $4}' | grep -E ':(6443|2379|2380)$' || true)")
        parts.append("          if [ -n \"$busy\" ]; then")
        parts.append("            echo \"Kubernetes control-plane ports still listening after cleanup: $busy\"")
        parts.append("            exit 1")
        parts.append("          fi")
        parts.append("        fi")
        parts.append("      args: {executable: /bin/bash}")
        parts.append("      changed_when: false")
        parts.append("      when: inventory_hostname in " + repr([n["ip"] for n in cps]))
    parts.append("    - name: Disable swap (runtime)")
    parts.append("      ansible.builtin.shell: |")
    parts.append("        set -e")
    parts.append("        awk 'NR > 1 {found=1} END {exit found ? 0 : 1}' /proc/swaps || exit 0")
    parts.append("        if command -v systemctl >/dev/null 2>&1; then")
    parts.append("          systemctl --no-block stop '*.swap' 2>/dev/null || true")
    parts.append("        fi")
    parts.append("        timeout -k 5s 40s swapoff -a")
    parts.append("      args: {executable: /bin/bash}")
    parts.append("      changed_when: false")
    parts.append("      failed_when: false")
    parts.append("    - name: Disable swap (fstab)")
    parts.append("      ansible.builtin.replace:")
    parts.append("        path: /etc/fstab")
    parts.append("        regexp: '^([^#].*\\s+swap\\s+.*)$'")
    parts.append("        replace: '# \\1'")
    parts.append("    - name: Verify swap is disabled for kubeadm")
    parts.append("      ansible.builtin.shell: awk 'NR > 1 {print $1}' /proc/swaps")
    parts.append("      register: active_swaps")
    parts.append("      changed_when: false")
    parts.append("      failed_when: active_swaps.stdout | trim != ''")
    parts.append("    - name: Install Kubernetes node prerequisite packages (Debian/Ubuntu)")
    parts.append("      ansible.builtin.apt:")
    parts.append("        name: [apt-transport-https, ca-certificates, curl, gnupg, containerd, conntrack, ipset, iptables, ebtables, ethtool, socat, kmod, coreutils, util-linux]")
    parts.append("        state: present")
    parts.append("        update_cache: true")
    parts.append("      when: ansible_os_family == 'Debian'")
    parts.append("    - name: Install Kubernetes node prerequisite packages (RHEL family)")
    parts.append("      ansible.builtin.package:")
    parts.append("        name: [ca-certificates, curl, gnupg2, containerd, conntrack-tools, ipset, iptables, ebtables, ethtool, socat, kmod, coreutils, util-linux]")
    parts.append("        state: present")
    parts.append("      when: ansible_os_family == 'RedHat'")
    parts.append("    - name: Load required kernel modules")
    parts.append("      ansible.builtin.copy:")
    parts.append("        dest: /etc/modules-load.d/k8s.conf")
    parts.append("        mode: '0644'")
    parts.append("        content: |")
    parts.append("          overlay")
    parts.append("          br_netfilter")
    parts.append("          nf_conntrack")
    parts.append("    - name: Load required kernel modules now")
    parts.append("      ansible.builtin.command: modprobe {{ item }}")
    parts.append("      loop: [overlay, br_netfilter, nf_conntrack]")
    parts.append("      environment:")
    parts.append("        PATH: \"/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\"")
    parts.append("      changed_when: false")
    if kube_proxy_mode == "iptables":
        parts.append("    - name: Switch iptables to legacy backend (Debian/Ubuntu, required by kube-proxy iptables mode)")
        parts.append("      ansible.builtin.shell: |")
        parts.append("        set -e")
        parts.append("        if [ -x /usr/sbin/iptables-legacy ]; then update-alternatives --set iptables /usr/sbin/iptables-legacy; fi")
        parts.append("        if [ -x /usr/sbin/ip6tables-legacy ]; then update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy; fi")
        parts.append("        if [ -x /usr/sbin/arptables-legacy ]; then update-alternatives --set arptables /usr/sbin/arptables-legacy || true; fi")
        parts.append("        if [ -x /usr/sbin/ebtables-legacy ]; then update-alternatives --set ebtables /usr/sbin/ebtables-legacy || true; fi")
        parts.append("      args: {executable: /bin/bash}")
        parts.append("      when: ansible_os_family == 'Debian'")
        parts.append("      changed_when: false")
        parts.append("    - name: Verify iptables backend")
        parts.append("      ansible.builtin.command: iptables --version")
        parts.append("      register: iptables_version")
        parts.append("      changed_when: false")
    elif kube_proxy_mode == "ipvs":
        parts.append("    - name: Install IPVS userspace tools (Debian/Ubuntu)")
        parts.append("      ansible.builtin.apt: {name: [ipvsadm, ipset], state: present, update_cache: true}")
        parts.append("      when: ansible_os_family == 'Debian'")
        parts.append("    - name: Install IPVS userspace tools (RHEL family)")
        parts.append("      ansible.builtin.package: {name: [ipvsadm, ipset], state: present}")
        parts.append("      when: ansible_os_family == 'RedHat'")
        parts.append("    - name: Persist IPVS kernel modules")
        parts.append("      ansible.builtin.copy:")
        parts.append("        dest: /etc/modules-load.d/k8s-ipvs.conf")
        parts.append("        mode: '0644'")
        parts.append("        content: |")
        parts.append("          ip_vs")
        parts.append("          ip_vs_rr")
        parts.append("          ip_vs_wrr")
        parts.append("          ip_vs_sh")
        parts.append("    - name: Load IPVS kernel modules")
        parts.append("      ansible.builtin.command: modprobe {{ item }}")
        parts.append("      loop: [ip_vs, ip_vs_rr, ip_vs_wrr, ip_vs_sh]")
        parts.append("      environment:")
        parts.append("        PATH: \"/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\"")
        parts.append("      changed_when: false")
    # kube_proxy_mode == "none": nothing to prepare; kube-proxy addon is skipped.
    parts.append("    - name: Sysctl for k8s networking")
    parts.append("      ansible.builtin.copy:")
    parts.append("        dest: /etc/sysctl.d/k8s.conf")
    parts.append("        mode: '0644'")
    parts.append("        content: |")
    parts.append("          net.bridge.bridge-nf-call-iptables  = 1")
    parts.append("          net.bridge.bridge-nf-call-ip6tables = 1")
    parts.append("          net.ipv4.ip_forward                 = 1")
    parts.append("          net.ipv4.conf.all.rp_filter         = 0")
    parts.append("          net.ipv4.conf.default.rp_filter     = 0")
    parts.append("    - name: Apply sysctl")
    parts.append("      ansible.builtin.command: sysctl --system")
    parts.append("      changed_when: false")
    parts.append("    - name: Allow Kubernetes control-plane ports through host firewall")
    parts.append("      ansible.builtin.shell: |")
    parts.append("        set +e")
    parts.append("        if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi '^Status: active'; then")
    parts.append("          ufw allow 6443/tcp >/dev/null 2>&1 || true")
    parts.append("          ufw allow 2379:2380/tcp >/dev/null 2>&1 || true")
    parts.append("        fi")
    parts.append("        if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then")
    parts.append("          firewall-cmd --permanent --add-port=6443/tcp >/dev/null 2>&1 || true")
    parts.append("          firewall-cmd --permanent --add-port=2379-2380/tcp >/dev/null 2>&1 || true")
    parts.append("          firewall-cmd --reload >/dev/null 2>&1 || true")
    parts.append("        fi")
    parts.append("      args: {executable: /bin/bash}")
    parts.append("      changed_when: false")
    parts.append("      when: inventory_hostname in " + repr([n["ip"] for n in cps]))
    parts.append("    - name: Ensure containerd config dir")
    parts.append("      ansible.builtin.file: {path: /etc/containerd, state: directory, mode: '0755'}")
    parts.append("    - name: Remove Debian stub containerd config (disabled CRI plugin)")
    parts.append("      ansible.builtin.shell: |")
    parts.append("        if [ -f /etc/containerd/config.toml ] && grep -q 'disabled_plugins.*cri' /etc/containerd/config.toml; then")
    parts.append("          rm -f /etc/containerd/config.toml")
    parts.append("        fi")
    parts.append("      changed_when: false")
    parts.append("    - name: Generate default containerd config (always regenerate)")
    parts.append("      ansible.builtin.shell: containerd config default > /etc/containerd/config.toml")
    parts.append("      changed_when: true")
    parts.append("      notify: restart containerd")
    parts.append("    - name: Ensure containerd CRI plugin is enabled")
    parts.append("      ansible.builtin.replace:")
    parts.append("        path: /etc/containerd/config.toml")
    parts.append("        regexp: '^\\s*disabled_plugins\\s*=.*$'")
    parts.append("        replace: 'disabled_plugins = []'")
    parts.append("      notify: restart containerd")
    parts.append("    - name: Force SystemdCgroup = true for runc (robust — handles missing key)")
    parts.append("      ansible.builtin.shell: |")
    parts.append("        set -e")
    parts.append("        cfg=/etc/containerd/config.toml")
    parts.append("        python3 - <<'PY'")
    parts.append("        import re, io")
    parts.append("        p = '/etc/containerd/config.toml'")
    parts.append("        s = open(p).read()")
    parts.append("        # 1. Replace existing SystemdCgroup line if present")
    parts.append("        new, n = re.subn(r'SystemdCgroup\\s*=\\s*(true|false)', 'SystemdCgroup = true', s)")
    parts.append("        if n == 0:")
    parts.append("            # 2. Insert into runc.options block if the block exists")
    parts.append("            block = r'(\\[plugins\\.\"io\\.containerd\\.grpc\\.v1\\.cri\"\\.containerd\\.runtimes\\.runc\\.options\\][^\\[]*)'")
    parts.append("            if re.search(block, new):")
    parts.append("                new = re.sub(block, lambda m: m.group(1).rstrip() + '\\n  SystemdCgroup = true\\n', new, count=1)")
    parts.append("            else:")
    parts.append("                # 3. Append a full runc.options block at the end")
    parts.append("                new += '\\n[plugins.\"io.containerd.grpc.v1.cri\".containerd.runtimes.runc.options]\\n  SystemdCgroup = true\\n'")
    parts.append("        # Pin sandbox image too")
    parts.append("        new, _ = re.subn(r'sandbox_image\\s*=\\s*\"[^\"]*\"', 'sandbox_image = \"registry.k8s.io/pause:3.9\"', new)")
    parts.append("        if 'sandbox_image' not in new:")
    parts.append("            new = re.sub(r'(\\[plugins\\.\"io\\.containerd\\.grpc\\.v1\\.cri\"\\][^\\[]*)', lambda m: m.group(1).rstrip() + '\\n  sandbox_image = \"registry.k8s.io/pause:3.9\"\\n', new, count=1)")
    parts.append("        open(p, 'w').write(new)")
    parts.append("        PY")
    parts.append("      changed_when: true")
    parts.append("      notify: restart containerd")
    parts.append("    - name: Apply containerd configuration NOW (before kubelet starts)")
    parts.append("      ansible.builtin.meta: flush_handlers")
    parts.append("    - name: Verify SystemdCgroup is enabled in containerd")
    parts.append("      ansible.builtin.shell: grep -E 'SystemdCgroup\\s*=\\s*true' /etc/containerd/config.toml")
    parts.append("      changed_when: false")
    parts.append("    - name: Verify containerd is actually using systemd cgroup driver at runtime")
    parts.append("      ansible.builtin.shell: |")
    parts.append("        set -e")
    parts.append("        systemctl restart containerd")
    parts.append("        sleep 3")
    parts.append("        # containerd exposes runtime config via `containerd config dump`")
    parts.append("        containerd config dump 2>/dev/null | grep -E 'SystemdCgroup\\s*=\\s*true' >/dev/null")
    parts.append("      changed_when: false")
    parts.append("    - name: Apply containerd configuration before kubelet starts")
    parts.append("      ansible.builtin.meta: flush_handlers")
    parts.append("    - name: Ensure containerd running")
    parts.append("      ansible.builtin.systemd: {name: containerd, state: started, enabled: true}")
    parts.append("    - name: Configure crictl for containerd")
    parts.append("      ansible.builtin.copy:")
    parts.append("        dest: /etc/crictl.yaml")
    parts.append("        mode: '0644'")
    parts.append("        content: |")
    parts.append("          runtime-endpoint: unix:///run/containerd/containerd.sock")
    parts.append("          image-endpoint: unix:///run/containerd/containerd.sock")
    parts.append("          timeout: 10")
    parts.append("          debug: false")
    parts.append("    - name: Kubernetes apt keyring dir")
    parts.append("      ansible.builtin.file: {path: /etc/apt/keyrings, state: directory, mode: '0755'}")
    parts.append("      when: ansible_os_family == 'Debian'")
    parts.append("    - name: Add Kubernetes apt key")
    parts.append("      ansible.builtin.shell: |")
    parts.append(f"        curl -fsSL https://pkgs.k8s.io/core:/stable:/v{minor}/deb/Release.key \\")
    parts.append("          | gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg")
    parts.append("      args: {creates: /etc/apt/keyrings/kubernetes-apt-keyring.gpg}")
    parts.append("      when: ansible_os_family == 'Debian'")
    parts.append("    - name: Add Kubernetes apt repo")
    parts.append("      ansible.builtin.copy:")
    parts.append("        dest: /etc/apt/sources.list.d/kubernetes.list")
    parts.append("        mode: '0644'")
    parts.append(f"        content: 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v{minor}/deb/ /'")
    parts.append("      when: ansible_os_family == 'Debian'")
    parts.append("    - name: Add Kubernetes yum/dnf repo (RHEL family)")
    parts.append("      ansible.builtin.copy:")
    parts.append("        dest: /etc/yum.repos.d/kubernetes.repo")
    parts.append("        mode: '0644'")
    parts.append("        content: |")
    parts.append("          [kubernetes]")
    parts.append(f"          name=Kubernetes v{minor}")
    parts.append(f"          baseurl=https://pkgs.k8s.io/core:/stable:/v{minor}/rpm/")
    parts.append("          enabled=1")
    parts.append("          gpgcheck=1")
    parts.append("          gpgkey=https://pkgs.k8s.io/core:/stable:/v" + minor + "/rpm/repodata/repomd.xml.key")
    parts.append("      when: ansible_os_family == 'RedHat'")
    parts.append("    - name: Install kubeadm/kubelet/kubectl")
    parts.append("      ansible.builtin.apt:")
    parts.append(f"        name: [\"kubeadm={version}-*\", \"kubelet={version}-*\", \"kubectl={version}-*\"]")
    parts.append("        state: present")
    parts.append("        update_cache: true")
    parts.append("      when: ansible_os_family == 'Debian'")
    parts.append("    - name: Install kubeadm/kubelet/kubectl (RHEL family)")
    parts.append("      ansible.builtin.package:")
    parts.append("        name: [kubeadm, kubelet, kubectl]")
    parts.append("        state: present")
    parts.append("      when: ansible_os_family == 'RedHat'")
    parts.append("    - name: Hold kube* packages")
    parts.append("      ansible.builtin.dpkg_selections:")
    parts.append("        name: '{{ item }}'")
    parts.append("        selection: hold")
    parts.append("      loop: [kubeadm, kubelet, kubectl]")
    parts.append("      when: ansible_os_family == 'Debian'")
    parts.append("    - name: Pin kubelet node IP")
    parts.append("      ansible.builtin.copy:")
    parts.append("        dest: /etc/default/kubelet")
    parts.append("        mode: '0644'")
    parts.append("        content: |")
    parts.append("          KUBELET_EXTRA_ARGS=--node-ip={{ inventory_hostname }} --hostname-override={{ k8s_node_name }}")
    parts.append("      notify: restart kubelet")
    parts.append("    - name: Enable kubelet")
    parts.append("      ansible.builtin.systemd: {name: kubelet, enabled: true, state: started}")
    if storage == "longhorn":
        parts.append("    - name: Longhorn prerequisites (open-iscsi, nfs-common) [Debian/Ubuntu]")
        parts.append("      ansible.builtin.apt:")
        parts.append("        name: [open-iscsi, nfs-common, util-linux]")
        parts.append("        state: present")
        parts.append("        update_cache: true")
        parts.append("      when: ansible_os_family == 'Debian'")
        parts.append("    - name: Longhorn prerequisites (iscsi-initiator-utils, nfs-utils) [RHEL]")
        parts.append("      ansible.builtin.package:")
        parts.append("        name: [iscsi-initiator-utils, nfs-utils]")
        parts.append("        state: present")
        parts.append("      when: ansible_os_family == 'RedHat'")
        parts.append("    - name: Ensure iscsid enabled")
        parts.append("      ansible.builtin.systemd: {name: iscsid, enabled: true, state: started}")
        parts.append("      failed_when: false")
    if storage == "nfs-subdir":
        parts.append("    - name: NFS client packages [Debian/Ubuntu]")
        parts.append("      ansible.builtin.apt: {name: nfs-common, state: present, update_cache: true}")
        parts.append("      when: ansible_os_family == 'Debian'")
        parts.append("    - name: NFS client packages [RHEL]")
        parts.append("      ansible.builtin.package: {name: nfs-utils, state: present}")
        parts.append("      when: ansible_os_family == 'RedHat'")
    parts.append("  handlers:")
    parts.append("    - name: restart containerd")
    parts.append("      ansible.builtin.systemd: {name: containerd, state: restarted}")
    parts.append("    - name: restart kubelet")
    parts.append("      ansible.builtin.systemd: {name: kubelet, state: restarted}")
    parts.append("")

    # ---- First control-plane bootstrap ----
    parts.append(f"- name: \"k8s :: bootstrap first control-plane ({cluster})\"")
    parts.append(f"  hosts: \"{first_cp_pat}\"")
    parts.append(f"  become: {become}")
    parts.append("  gather_facts: true")
    parts.extend(vf_lines)
    parts.append("  vars:")
    parts.extend(_conn_vars_lines(cps[:1]))
    parts.extend(_cp_api_endpoint_vars())
    parts.append("  tasks:")

    parts.append("    - name: Check if cluster is already initialized")
    parts.append("      ansible.builtin.stat: {path: /etc/kubernetes/admin.conf}")
    parts.append("      register: admin_conf")
    parts.append("    - name: Ensure ~/.kube exists for root")
    parts.append("      ansible.builtin.file: {path: /root/.kube, state: directory, mode: '0700'}")
    parts.append("    - name: Create local bootstrap kubeconfig for existing cluster")
    parts.append("      ansible.builtin.copy:")
    parts.append("        src: /etc/kubernetes/admin.conf")
    parts.append("        dest: /root/.kube/bootstrap-local.conf")
    parts.append("        remote_src: true")
    parts.append("        mode: '0600'")
    parts.append("      when: admin_conf.stat.exists")
    parts.append("    - name: Pin bootstrap kubeconfig to this control-plane IP")
    parts.append("      ansible.builtin.replace:")
    parts.append("        path: /root/.kube/bootstrap-local.conf")
    parts.append("        regexp: '^\\s*server:\\s+https://.*:6443\\s*$'")
    parts.append("        replace: '    server: https://{{ inventory_hostname }}:6443'")
    parts.append("      when: admin_conf.stat.exists")
    parts.append("    - name: Check existing control-plane API health")
    parts.append("      ansible.builtin.command: kubectl --kubeconfig=/root/.kube/bootstrap-local.conf get --raw=/readyz")
    parts.append("      register: existing_api_ready")
    parts.append("      failed_when: false")
    parts.append("      changed_when: false")
    parts.append("      when: admin_conf.stat.exists")
    parts.append("    - name: Stop on stale kubeadm state")
    parts.append("      ansible.builtin.fail:")
    parts.append("        msg: 'Kubeadm state exists but the API is not reachable on this control-plane IP. Enable Reset existing kubeadm state before install, then rerun this template instance.'")
    parts.append("      when: admin_conf.stat.exists and existing_api_ready.rc != 0")
    init_cmd_parts = [
        "kubeadm init",
        f"--kubernetes-version=v{version}",
        f"--pod-network-cidr={pod_cidr}",
        f"--service-cidr={service_cidr}",
        "--apiserver-advertise-address={{ inventory_hostname }}",
        "--node-name={{ k8s_node_name }}",
        "--cri-socket=unix:///run/containerd/containerd.sock",
    ]
    if apiserver_sans:
        init_cmd_parts.append(f"--apiserver-cert-extra-sans={','.join(apiserver_sans)}")
    if ha_mode and effective_cp_endpoint:
        init_cmd_parts.append(f"--control-plane-endpoint={effective_cp_endpoint}")
        init_cmd_parts.append("--upload-certs")
    if kube_proxy_mode == "none":
        init_cmd_parts.append("--skip-phases=addon/kube-proxy")
    parts.append("    - name: kubeadm init")
    parts.append("      ansible.builtin.command: >-")
    parts.append("        " + " ".join(init_cmd_parts))
    parts.append("      when: not admin_conf.stat.exists")
    parts.append("      register: kubeadm_init")
    parts.append("    - name: Copy admin.conf for root kubectl")
    parts.append("      ansible.builtin.copy:")
    parts.append("        src: /etc/kubernetes/admin.conf")
    parts.append("        dest: /root/.kube/config")
    parts.append("        remote_src: true")
    parts.append("        mode: '0600'")
    parts.append("    - name: Create local bootstrap kubeconfig")
    parts.append("      ansible.builtin.copy:")
    parts.append("        src: /etc/kubernetes/admin.conf")
    parts.append("        dest: /root/.kube/bootstrap-local.conf")
    parts.append("        remote_src: true")
    parts.append("        mode: '0600'")
    parts.append("    - name: Pin bootstrap kubeconfig to this control-plane IP")
    parts.append("      ansible.builtin.replace:")
    parts.append("        path: /root/.kube/bootstrap-local.conf")
    parts.append("        regexp: '^\\s*server:\\s+https://.*:6443\\s*$'")
    parts.append("        replace: '    server: https://{{ inventory_hostname }}:6443'")
    parts.append("    - name: Wait for Kubernetes API to become ready")
    parts.append("      ansible.builtin.command: kubectl --kubeconfig=/root/.kube/bootstrap-local.conf get --raw=/readyz")
    parts.append("      register: api_ready")
    parts.append("      retries: 60")
    parts.append("      delay: 5")
    parts.append("      until: api_ready.rc == 0")
    parts.append("      changed_when: false")
    # Patch kube-proxy configmap to be LXC/OrbStack/containerized-host safe:
    # - conntrack.maxPerCore/min = 0 prevents kube-proxy from writing to
    #   /proc/sys/net/netfilter/nf_conntrack_* which is read-only in LXC
    #   and causes CrashLoopBackOff on OrbStack, Proxmox LXC, nested runners.
    # Also switch to IPVS if requested.
    parts.append("    - name: Patch kube-proxy for containerized/LXC-safe conntrack (and IPVS mode if selected)")
    parts.append("      ansible.builtin.shell: |")
    parts.append("        set -e")
    parts.append("        export KUBECONFIG=/root/.kube/bootstrap-local.conf")
    parts.append("        tmp=$(mktemp)")
    parts.append("        kubectl -n kube-system get cm kube-proxy -o yaml > \"$tmp\"")
    parts.append("        # Disable conntrack sysctl writes (read-only in LXC/OrbStack)")
    parts.append("        sed -i -E 's/(^\\s*maxPerCore:).*/\\1 0/; s/(^\\s*min:) [0-9]+/\\1 0/' \"$tmp\"")
    if kube_proxy_mode == "ipvs":
        parts.append("        sed -i -E 's/(^\\s*mode:).*/\\1 \"ipvs\"/' \"$tmp\"")
    parts.append("        kubectl apply -f \"$tmp\"")
    parts.append("        rm -f \"$tmp\"")
    parts.append("        kubectl -n kube-system rollout restart daemonset kube-proxy")
    parts.append("      args: {executable: /bin/bash}")
    parts.append("      changed_when: true")
    parts.append("    - name: Generate worker join command")
    parts.append("      ansible.builtin.command: kubeadm token create --kubeconfig=/root/.kube/bootstrap-local.conf --print-join-command")
    parts.append("      register: worker_join_cmd")
    parts.append("      changed_when: false")
    if ha_mode:
        parts.append("    - name: Upload certs and get certificate key")
        parts.append("      ansible.builtin.command: kubeadm init phase upload-certs --upload-certs --kubeconfig=/root/.kube/bootstrap-local.conf")
        parts.append("      register: cert_key_out")
        parts.append("      changed_when: false")
        parts.append("    - name: Extract certificate key")
        parts.append("      ansible.builtin.set_fact:")
        parts.append("        kubeadm_cert_key: \"{{ cert_key_out.stdout_lines[-1] | trim }}\"")
    parts.append("    - name: Slurp admin.conf for cluster ops")
    parts.append("      ansible.builtin.slurp: {src: /etc/kubernetes/admin.conf}")
    parts.append("      register: first_cp_admin_conf_b64")
    parts.append("    - name: Set join facts")
    parts.append("      ansible.builtin.set_fact:")
    parts.append("        kubeadm_worker_join: \"{{ worker_join_cmd.stdout | trim }}\"")
    parts.append("        first_cp_admin_conf_b64_content: \"{{ first_cp_admin_conf_b64.content }}\"")
    parts.append("    - name: Verify Kubernetes API is ready for node joins")
    parts.append("      ansible.builtin.command: kubectl --kubeconfig=/root/.kube/bootstrap-local.conf get --raw=/readyz")
    parts.append("      register: api_ready_for_joins")
    parts.append("      retries: 60")
    parts.append("      delay: 5")
    parts.append("      until: api_ready_for_joins.rc == 0")
    parts.append("      changed_when: false")
    parts.append("")

    # ---- Additional control-planes (HA) ----
    if ha_mode and len(cps) > 1:
        parts.append(f"- name: \"k8s :: join additional control-planes ({cluster})\"")
        parts.append(f"  hosts: \"{extra_cps_pat}\"")
        parts.append(f"  become: {become}")
        parts.append("  serial: 1")
        parts.append("  gather_facts: false")
        parts.extend(vf_lines)
        parts.append("  vars:")
        # Include first CP in the connection maps so delegate_to picks up its user/port.
        parts.extend(_conn_vars_lines(cps[:1] + cps[1:]))
        parts.append(f"    first_cp_ip: {yaml_str(first_cp_ip)}")
        parts.extend(_cp_api_endpoint_vars())
        parts.append("  tasks:")
        parts.append("    - name: Check if already joined")
        parts.append("      ansible.builtin.stat: {path: /etc/kubernetes/kubelet.conf}")
        parts.append("      register: kubelet_conf")
        parts.append("    - name: Validate join credentials from first control-plane")
        parts.append("      ansible.builtin.assert:")
        parts.append("        that:")
        parts.append("          - hostvars[first_cp_ip].kubeadm_worker_join is defined")
        parts.append("          - hostvars[first_cp_ip].kubeadm_worker_join | length > 0")
        parts.append("          - hostvars[first_cp_ip].kubeadm_cert_key is defined")
        parts.append("          - hostvars[first_cp_ip].kubeadm_cert_key | length > 0")
        parts.append("        fail_msg: 'Missing kubeadm join credentials on the first control-plane. Re-run the template with the first control-plane included, or enable reset for a clean rebuild.'")
        parts.append("      when: not kubelet_conf.stat.exists")
        parts.append("    - name: Write first control-plane admin.conf locally for etcd cleanup")
        parts.append("      ansible.builtin.copy:")
        parts.append("        dest: /tmp/opensible-first-cp-admin.conf")
        parts.append("        content: \"{{ hostvars[first_cp_ip].first_cp_admin_conf_b64_content | b64decode }}\"")
        parts.append("        mode: '0600'")
        parts.append("      when: not kubelet_conf.stat.exists and hostvars[first_cp_ip].first_cp_admin_conf_b64_content is defined")
        parts.append("    - name: Write control-plane join script")
        parts.append("      ansible.builtin.copy:")
        parts.append("        dest: /usr/local/sbin/opensible-cp-join.sh")
        parts.append("        mode: '0755'")
        parts.append("        content: |")
        parts.append("          #!/usr/bin/env bash")
        parts.append("          set +e")
        parts.append("          ENDPOINTS=\"${OPENSIBLE_ENDPOINTS:-}\"")
        parts.append("          JOIN_CMD_BASE=\"${OPENSIBLE_JOIN_CMD:-}\"")
        parts.append("          CERT_KEY=\"${OPENSIBLE_CERT_KEY:-}\"")
        parts.append("          NODE_NAME=\"${OPENSIBLE_NODE_NAME:-}\"")
        parts.append("          FIRST_CP=\"${OPENSIBLE_FIRST_CP:-}\"")
        parts.append("          THIS_IP=\"${OPENSIBLE_THIS_IP:-}\"")
        parts.append("          REMOTE_KUBECONFIG=/tmp/opensible-first-cp-admin.conf")
        parts.append("          LAST_ERR=\"\"")
        parts.append("          pick_endpoint() {")
        parts.append("            for ep in $ENDPOINTS; do")
        parts.append("              h=\"${ep%:*}\"; p=\"${ep##*:}\"")
        parts.append("              timeout 3 bash -c \"</dev/tcp/${h}/${p}\" >/dev/null 2>&1 || continue")
        parts.append("              c=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 \"https://${ep}/healthz\" 2>/dev/null || echo 000)")
        parts.append("              case \"$c\" in 200|401|403) echo \"$ep\"; return 0 ;; esac")
        parts.append("            done")
        parts.append("            return 1")
        parts.append("          }")
        parts.append("          remove_stale_etcd_member() {")
        parts.append("            [ -f \"$REMOTE_KUBECONFIG\" ] || return 0")
        parts.append("            local ep etcd_pod members")
        parts.append("            ep=$(pick_endpoint) || return 0")
        parts.append("            sed -i -E \"s|server: https://[^\\\"[:space:]]+|server: https://${ep}|g\" \"$REMOTE_KUBECONFIG\" 2>/dev/null || true")
        parts.append("            etcd_pod=$(kubectl --kubeconfig=$REMOTE_KUBECONFIG -n kube-system get pods -l component=etcd -o name 2>/dev/null | head -n1)")
        parts.append("            [ -z \"$etcd_pod\" ] && return 0")
        parts.append("            members=$(kubectl --kubeconfig=$REMOTE_KUBECONFIG -n kube-system exec \"$etcd_pod\" -- etcdctl --endpoints=https://127.0.0.1:2379 --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key member list 2>/dev/null)")
        parts.append("            [ -z \"$members\" ] && return 0")
        parts.append("            echo \"$members\" | awk -F', *' -v ip=\"$THIS_IP\" -v nn=\"$NODE_NAME\" '($0 ~ ip) || ($3 == nn) {print $1}' | while read -r mid; do")
        parts.append("              [ -z \"$mid\" ] && continue")
        parts.append("              echo \"[cleanup] removing stale etcd member $mid for $THIS_IP\"")
        parts.append("              kubectl --kubeconfig=$REMOTE_KUBECONFIG -n kube-system exec \"$etcd_pod\" -- etcdctl --endpoints=https://127.0.0.1:2379 --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key member remove \"$mid\" 2>&1 || true")
        parts.append("            done")
        parts.append("          }")
        parts.append("          remove_stale_etcd_member")
        parts.append("          for attempt in $(seq 1 20); do")
        parts.append("            endpoint=$(pick_endpoint) || { echo \"[attempt $attempt] no healthy endpoint yet\"; sleep 20; continue; }")
        parts.append("            REWRITTEN=$(echo \"$JOIN_CMD_BASE\" | sed -e \"s|localhost:6443|${endpoint}|g\" -e \"s|127.0.0.1:6443|${endpoint}|g\" -e \"s|${FIRST_CP}:6443|${endpoint}|g\")")
        parts.append("            echo \"[attempt $attempt] joining via $endpoint\"")
        parts.append("            OUT=$($REWRITTEN --control-plane --certificate-key \"$CERT_KEY\" --cri-socket=unix:///run/containerd/containerd.sock --node-name \"$NODE_NAME\" 2>&1)")
        parts.append("            rc=$?; echo \"$OUT\"")
        parts.append("            [ $rc -eq 0 ] && exit 0")
        parts.append("            LAST_ERR=\"$OUT\"")
        parts.append("            echo \"$OUT\" | grep -qi 'already joined' && exit 0")
        parts.append("            kubeadm reset -f --cri-socket=unix:///run/containerd/containerd.sock >/dev/null 2>&1 || kubeadm reset -f >/dev/null 2>&1 || true")
        parts.append("            rm -rf /etc/kubernetes /var/lib/etcd /var/lib/kubelet/pki /etc/cni/net.d 2>/dev/null || true")
        parts.append("            remove_stale_etcd_member")
        parts.append("            sleep 30")
        parts.append("          done")
        parts.append("          echo \"$LAST_ERR\" >&2")
        parts.append("          exit 1")
        parts.append("      when: not kubelet_conf.stat.exists")
        parts.append("    - name: Join as control-plane (with endpoint failover + retries)")
        parts.append("      ansible.builtin.command: /usr/local/sbin/opensible-cp-join.sh")
        parts.append("      environment:")
        parts.append("        OPENSIBLE_ENDPOINTS: \"{{ cp_api_endpoints | join(' ') }}\"")
        parts.append("        OPENSIBLE_JOIN_CMD: \"{{ hostvars[first_cp_ip].kubeadm_worker_join | trim | regex_replace('\\\\s+', ' ') }}\"")
        parts.append("        OPENSIBLE_CERT_KEY: \"{{ hostvars[first_cp_ip].kubeadm_cert_key }}\"")
        parts.append("        OPENSIBLE_NODE_NAME: \"{{ k8s_node_name }}\"")
        parts.append("        OPENSIBLE_FIRST_CP: \"{{ first_cp_ip }}\"")
        parts.append("        OPENSIBLE_THIS_IP: \"{{ inventory_hostname }}\"")
        parts.append("      when: not kubelet_conf.stat.exists")

        parts.append("    - name: Remove temporary kubeconfig")
        parts.append("      ansible.builtin.file:")
        parts.append("        path: /tmp/opensible-first-cp-admin.conf")
        parts.append("        state: absent")
        parts.append("    - name: Wait for joined control-plane kubelet config")
        parts.append("      ansible.builtin.stat: {path: /etc/kubernetes/kubelet.conf}")
        parts.append("      register: joined_cp_kubelet_conf")
        parts.append("      retries: 30")
        parts.append("      delay: 10")
        parts.append("      until: joined_cp_kubelet_conf.stat.exists")
        parts.append("    - name: Ensure ~/.kube exists on joined control-plane")
        parts.append("      ansible.builtin.file: {path: /root/.kube, state: directory, mode: '0700'}")
        parts.append("    - name: Create local kubeconfig on joined control-plane")
        parts.append("      ansible.builtin.copy:")
        parts.append("        src: /etc/kubernetes/admin.conf")
        parts.append("        dest: /root/.kube/bootstrap-local.conf")
        parts.append("        remote_src: true")
        parts.append("        mode: '0600'")
        parts.append("    - name: Pin joined control-plane kubeconfig to local API")
        parts.append("      ansible.builtin.replace:")
        parts.append("        path: /root/.kube/bootstrap-local.conf")
        parts.append("        regexp: '^\\s*server:\\s+https://.*:6443\\s*$'")
        parts.append("        replace: '    server: https://{{ inventory_hostname }}:6443'")
        parts.append("    - name: Wait for joined control-plane API to become ready")
        parts.append("      ansible.builtin.command: kubectl --kubeconfig=/root/.kube/bootstrap-local.conf get --raw=/readyz")
        parts.append("      register: joined_cp_api_ready")
        parts.append("      retries: 60")
        parts.append("      delay: 5")
        parts.append("      until: joined_cp_api_ready.rc == 0")
        parts.append("      changed_when: false")

        parts.append("")

    # ---- Worker join ----
    if workers:
        parts.append(f"- name: \"k8s :: join workers ({cluster})\"")
        parts.append(f"  hosts: \"{workers_pat}\"")
        parts.append(f"  become: {become}")
        parts.append("  gather_facts: false")
        parts.extend(vf_lines)
        parts.append("  vars:")
        # Include first CP in the connection maps so delegate_to picks up its user/port.
        parts.extend(_conn_vars_lines(cps[:1] + workers))
        parts.append(f"    first_cp_ip: {yaml_str(first_cp_ip)}")
        parts.extend(_cp_api_endpoint_vars())
        parts.append("  tasks:")
        parts.append("    - name: Check if already joined")
        parts.append("      ansible.builtin.stat: {path: /etc/kubernetes/kubelet.conf}")
        parts.append("      register: kubelet_conf")
        parts.append("    - name: Validate worker join credentials from first control-plane")
        parts.append("      ansible.builtin.assert:")
        parts.append("        that:")
        parts.append("          - hostvars[first_cp_ip].kubeadm_worker_join is defined")
        parts.append("          - hostvars[first_cp_ip].kubeadm_worker_join | length > 0")
        parts.append("        fail_msg: 'Missing kubeadm worker join command on the first control-plane. Re-run the template with the first control-plane included.'")
        parts.append("      when: not kubelet_conf.stat.exists")
        parts.append("    - name: Join cluster as worker (with endpoint failover + retries)")
        parts.append("      ansible.builtin.shell: |")
        parts.append("        set +e")
        parts.append("        ENDPOINTS=\"{{ cp_api_endpoints | join(' ') }}\"")
        parts.append("        JOIN_CMD_BASE=\"{{ hostvars[first_cp_ip].kubeadm_worker_join | trim | regex_replace('\\\\s+', ' ') }}\"")
        parts.append("        NODE_NAME=\"{{ k8s_node_name }}\"")
        parts.append("        FIRST_CP=\"{{ first_cp_ip }}\"")
        parts.append("        LAST_ERR=\"\"")
        parts.append("        for attempt in $(seq 1 30); do")
        parts.append("          for endpoint in $ENDPOINTS; do")
        parts.append("            host=\"${endpoint%:*}\"; port=\"${endpoint##*:}\"")
        parts.append("            timeout 3 bash -c \"</dev/tcp/${host}/${port}\" >/dev/null 2>&1 || continue")
        parts.append("            code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 \"https://${endpoint}/healthz\" 2>/dev/null || echo 000)")
        parts.append("            case \"$code\" in 200|401|403) : ;; *) continue ;; esac")
        parts.append("            REWRITTEN=$(echo \"$JOIN_CMD_BASE\" | sed -e \"s|localhost:6443|${endpoint}|g\" -e \"s|127.0.0.1:6443|${endpoint}|g\" -e \"s|${FIRST_CP}:6443|${endpoint}|g\")")
        parts.append("            echo \"[attempt $attempt] joining via $endpoint\"")
        parts.append("            OUT=$($REWRITTEN --cri-socket=unix:///run/containerd/containerd.sock --node-name \"$NODE_NAME\" 2>&1)")
        parts.append("            rc=$?; echo \"$OUT\"")
        parts.append("            if [ $rc -eq 0 ]; then exit 0; fi")
        parts.append("            LAST_ERR=\"$OUT\"")
        parts.append("            if echo \"$OUT\" | grep -qi 'already joined'; then exit 0; fi")
        parts.append("            kubeadm reset -f --cri-socket=unix:///run/containerd/containerd.sock >/dev/null 2>&1 || kubeadm reset -f >/dev/null 2>&1 || true")
        parts.append("            rm -rf /etc/kubernetes /var/lib/kubelet/pki /etc/cni/net.d 2>/dev/null || true")
        parts.append("          done")
        parts.append("          sleep 15")
        parts.append("        done")
        parts.append("        echo \"$LAST_ERR\" >&2")
        parts.append("        exit 1")
        parts.append("      args: {executable: /bin/bash}")
        parts.append("      when: not kubelet_conf.stat.exists")
        parts.append("    - name: Wait for joined worker kubelet config")
        parts.append("      ansible.builtin.stat: {path: /etc/kubernetes/kubelet.conf}")
        parts.append("      register: joined_worker_kubelet_conf")
        parts.append("      retries: 30")
        parts.append("      delay: 10")
        parts.append("      until: joined_worker_kubelet_conf.stat.exists")

        parts.append("")


    # ---- Post-install (metrics-server + storage + fetch kubeconfig) ----
    has_storage = storage in ("longhorn", "local-path", "openebs-hostpath", "nfs-subdir")
    if True:
        parts.append(f"- name: \"k8s :: post-install ({cluster})\"")
        parts.append(f"  hosts: \"{first_cp_pat}\"")
        parts.append(f"  become: {become}")
        parts.append("  gather_facts: false")
        parts.extend(vf_lines)
        parts.append("  vars:")
        parts.extend(_conn_vars_lines(cps[:1]))
        parts.extend(_cp_api_endpoint_vars())
        parts.append("  tasks:")

        parts.append("    - name: Ensure bootstrap kubeconfig is available for post-install checks")
        parts.append("      ansible.builtin.copy:")
        parts.append("        src: /etc/kubernetes/admin.conf")
        parts.append("        dest: /root/.kube/bootstrap-local.conf")
        parts.append("        remote_src: true")
        parts.append("        mode: '0600'")
        parts.append("    - name: Pin post-install kubeconfig to this control-plane IP")
        parts.append("      ansible.builtin.replace:")
        parts.append("        path: /root/.kube/bootstrap-local.conf")
        parts.append("        regexp: '^\\s*server:\\s+https://.*:6443\\s*$'")
        parts.append("        replace: '    server: https://{{ inventory_hostname }}:6443'")
        parts.append("    - name: Verify Kubernetes API before post-install add-ons")
        parts.append("      ansible.builtin.command: kubectl --kubeconfig=/root/.kube/bootstrap-local.conf get --raw=/readyz")
        parts.append("      register: api_ready_before_addons")
        parts.append("      retries: 60")
        parts.append("      delay: 5")
        parts.append("      until: api_ready_before_addons.rc == 0")
        parts.append("      changed_when: false")

        if cni == "calico":
            parts.append("    - name: Write Calico LXC-safe patcher script")
            parts.append("      ansible.builtin.copy:")
            parts.append("        dest: /usr/local/sbin/opensible-calico-patch.py")
            parts.append("        mode: '0755'")
            parts.append("        content: |")
            parts.append("          #!/usr/bin/env python3")
            parts.append("          import re, sys")
            parts.append("          p = sys.argv[1]")
            parts.append("          s = open(p).read()")
            parts.append("          # Force IPIP off; VXLAN is the LXC/OrbStack-safe dataplane.")
            parts.append("          s = re.sub(r'(name: CALICO_IPV4POOL_IPIP\\s*\\n\\s*value: \")[^\"]+(\")', r'\\1Never\\2', s)")
            parts.append("          extra = (")
            parts.append("              '            - name: CALICO_IPV4POOL_VXLAN\\n              value: \"CrossSubnet\"\\n'")
            parts.append("              '            - name: FELIX_VXLANENABLED\\n              value: \"true\"\\n'")
            parts.append("              '            - name: FELIX_IPINIPENABLED\\n              value: \"false\"\\n'")
            parts.append("              '            - name: FELIX_IGNORELOOSERPF\\n              value: \"true\"\\n'")
            parts.append("              '            - name: FELIX_BPFENABLED\\n              value: \"false\"\\n'")
            parts.append("              '            - name: FELIX_XDPENABLED\\n              value: \"false\"\\n'")
            parts.append("              '            - name: FELIX_HEALTHENABLED\\n              value: \"true\"\\n'")
            parts.append("          )")
            parts.append("          s = s.replace(")
            parts.append("              '- name: CLUSTER_TYPE\\n              value: \"k8s,bgp\"',")
            parts.append("              '- name: CLUSTER_TYPE\\n              value: \"k8s,bgp\"\\n' + extra,")
            parts.append("              1,")
            parts.append("          )")
            parts.append("          # Drop the mount-bpffs init container: BPF is disabled and this container")
            parts.append("          # frequently fails inside LXC because /sys/fs/bpf is not writable.")
            parts.append("          s = re.sub(")
            parts.append("              r'\\n        - name: \"mount-bpffs\".*?(?=\\n        - name: |\\n      containers:)',")
            parts.append("              '\\n', s, flags=re.S,")
            parts.append("          )")
            parts.append("          open(p, 'w').write(s)")
            parts.append("    - name: Install Calico CNI with matching pod CIDR (LXC/OrbStack-safe)")
            parts.append("      ansible.builtin.shell: |")
            parts.append("        set -e")
            parts.append("        curl -fsSL https://raw.githubusercontent.com/projectcalico/calico/v3.28.0/manifests/calico.yaml -o /tmp/calico.yaml")
            parts.append("        sed -i -E '/name: CALICO_IPV4POOL_CIDR/{n;s#value: \"[^\"]+\"#value: \"" + str(pod_cidr) + "\"#;}' /tmp/calico.yaml")
            parts.append("        virt=$(systemd-detect-virt 2>/dev/null || echo none)")
            parts.append("        is_lxc=0")
            parts.append("        case \"$virt\" in lxc|lxc-libvirt|container-other|podman|docker|openvz) is_lxc=1 ;; esac")
            parts.append("        if [ \"$is_lxc\" = 0 ] && uname -r | grep -qiE 'orbstack|microsoft|wsl'; then is_lxc=1; fi")
            parts.append("        if [ \"$is_lxc\" = 1 ]; then")
            parts.append("          echo '[calico] containerized host detected -> VXLAN, no IPIP/BPF/XDP'")
            parts.append("          python3 /usr/local/sbin/opensible-calico-patch.py /tmp/calico.yaml")
            parts.append("        fi")
            parts.append("        kubectl apply -f /tmp/calico.yaml")
            parts.append("      args: {executable: /bin/bash}")
            parts.append("      environment: {KUBECONFIG: /root/.kube/bootstrap-local.conf}")
            parts.append("      register: cni_apply")
            parts.append("      changed_when: \"'created' in cni_apply.stdout or 'configured' in cni_apply.stdout\"")
        elif cni == "flannel":
            parts.append("    - name: Install Flannel CNI")
            parts.append("      ansible.builtin.command: kubectl apply -f https://github.com/flannel-io/flannel/releases/latest/download/kube-flannel.yml")
            parts.append("      environment: {KUBECONFIG: /root/.kube/bootstrap-local.conf}")
            parts.append("      register: cni_apply")
            parts.append("      changed_when: \"'created' in cni_apply.stdout or 'configured' in cni_apply.stdout\"")
        if allow_cp_sched:
            parts.append("    - name: Untaint control-plane nodes (allow scheduling)")
            parts.append("      ansible.builtin.command: kubectl taint nodes --all node-role.kubernetes.io/control-plane- --overwrite=true")
            parts.append("      environment: {KUBECONFIG: /root/.kube/bootstrap-local.conf}")
            parts.append("      register: untaint")
            parts.append("      failed_when: untaint.rc != 0 and 'not found' not in untaint.stderr")
            parts.append("      changed_when: \"'untainted' in untaint.stdout\"")
        if cni in ("calico", "flannel"):
            parts.append("    - name: Verify Kubernetes API remains healthy after CNI")
            parts.append("      ansible.builtin.command: kubectl --kubeconfig=/root/.kube/bootstrap-local.conf get --raw=/readyz")
            parts.append("      register: api_ready_after_cni")
            parts.append("      retries: 60")
            parts.append("      delay: 5")
            parts.append("      until: api_ready_after_cni.rc == 0")
            parts.append("      changed_when: false")

        if install_metrics:
            parts.append("    - name: Apply metrics-server manifest")
            parts.append("      ansible.builtin.command: kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml")
            parts.append("      environment: {KUBECONFIG: /root/.kube/bootstrap-local.conf}")
            parts.append("      register: metrics_apply")
            parts.append("      changed_when: \"'created' in metrics_apply.stdout or 'configured' in metrics_apply.stdout\"")
            parts.append("    - name: Patch metrics-server to tolerate kubelet self-signed certs (no IP SANs)")
            parts.append("      ansible.builtin.shell: |")
            parts.append("        set -e")
            parts.append("        DESIRED_ADDS='--kubelet-insecure-tls --kubelet-preferred-address-types=InternalIP,Hostname,ExternalIP'")
            parts.append("        # Retry on optimistic-concurrency conflicts (deployment is rolling out)")
            parts.append("        for i in $(seq 1 20); do")
            parts.append("          NEW_ARGS=$(kubectl -n kube-system get deploy metrics-server -o json \\")
            parts.append("            | python3 -c \"import sys,json,os; d=json.load(sys.stdin); a=d['spec']['template']['spec']['containers'][0].get('args',[]) or []; adds=os.environ['DESIRED_ADDS'].split(); a=[x for x in a if x.split('=')[0] not in {y.split('=')[0] for y in adds}]+adds; print(json.dumps(a))\")")
            parts.append("          # If already contains --kubelet-insecure-tls, nothing to do")
            parts.append("          CUR=$(kubectl -n kube-system get deploy metrics-server -o jsonpath='{.spec.template.spec.containers[0].args}')")
            parts.append("          if echo \"$CUR\" | grep -q -- '--kubelet-insecure-tls' && echo \"$CUR\" | grep -q -- 'InternalIP,Hostname'; then")
            parts.append("            echo 'metrics-server args already patched'; break")
            parts.append("          fi")
            parts.append("          if kubectl -n kube-system patch deployment metrics-server --type=json \\")
            parts.append("               -p=\"[{\\\"op\\\":\\\"replace\\\",\\\"path\\\":\\\"/spec/template/spec/containers/0/args\\\",\\\"value\\\":${NEW_ARGS}}]\" 2>/tmp/mspatch.err; then")
            parts.append("            break")
            parts.append("          fi")
            parts.append("          if grep -q 'Conflict\\|has been modified' /tmp/mspatch.err; then")
            parts.append("            echo \"conflict, retry $i/20\"; sleep 3; continue")
            parts.append("          fi")
            parts.append("          cat /tmp/mspatch.err >&2; exit 1")
            parts.append("        done")
            parts.append("        kubectl -n kube-system rollout status deploy/metrics-server --timeout=180s || true")
            parts.append("      environment: {KUBECONFIG: /root/.kube/bootstrap-local.conf}")
            parts.append("      changed_when: false")
            parts.append("      failed_when: false")


        # ---- Persistent storage / default StorageClass ----
        sc_name = ""
        if storage == "longhorn":
            sc_name = "longhorn"
            parts.append("    - name: Ensure Helm is installed (for Longhorn)")
            parts.append("      ansible.builtin.shell: |")
            parts.append("        set -e")
            parts.append("        if ! command -v helm >/dev/null 2>&1; then")
            parts.append("          curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash")
            parts.append("        fi")
            parts.append("      args: {creates: /usr/local/bin/helm}")
            parts.append("    - name: Add Longhorn helm repo")
            parts.append("      ansible.builtin.command: helm repo add longhorn https://charts.longhorn.io")
            parts.append("      register: helm_add")
            parts.append("      changed_when: \"'has been added' in helm_add.stdout\"")
            parts.append("      failed_when: helm_add.rc != 0 and 'already exists' not in helm_add.stderr")
            parts.append("    - name: Update helm repos")
            parts.append("      ansible.builtin.command: helm repo update")
            parts.append("      changed_when: false")
            parts.append("    - name: Create longhorn-system namespace")
            parts.append("      ansible.builtin.command: kubectl create namespace longhorn-system")
            parts.append("      environment: {KUBECONFIG: /root/.kube/bootstrap-local.conf}")
            parts.append("      register: ns_create")
            parts.append("      changed_when: \"'created' in ns_create.stdout\"")
            parts.append("      failed_when: ns_create.rc != 0 and 'AlreadyExists' not in ns_create.stderr")
            parts.append("    - name: Install / upgrade Longhorn")
            parts.append("      ansible.builtin.command: >-")
            parts.append(f"        helm upgrade --install longhorn longhorn/longhorn --namespace longhorn-system")
            if longhorn_version:
                parts.append(f"        --version {longhorn_version}")
            parts.append(f"        --set persistence.defaultClassReplicaCount={longhorn_replicas}")
            parts.append(f"        --set defaultSettings.defaultReplicaCount={longhorn_replicas}")
            parts.append("      environment: {KUBECONFIG: /root/.kube/bootstrap-local.conf}")
        elif storage == "local-path":
            sc_name = "local-path"
            parts.append("    - name: Install Rancher local-path-provisioner")
            parts.append("      ansible.builtin.command: kubectl apply -f https://raw.githubusercontent.com/rancher/local-path-provisioner/master/deploy/local-path-storage.yaml")
            parts.append("      environment: {KUBECONFIG: /root/.kube/bootstrap-local.conf}")
            parts.append("      register: lp_apply")
            parts.append("      changed_when: \"'created' in lp_apply.stdout or 'configured' in lp_apply.stdout\"")
        elif storage == "openebs-hostpath":
            sc_name = "openebs-hostpath"
            parts.append("    - name: Install OpenEBS (hostpath)")
            parts.append("      ansible.builtin.command: kubectl apply -f https://openebs.github.io/charts/openebs-operator.yaml")
            parts.append("      environment: {KUBECONFIG: /root/.kube/bootstrap-local.conf}")
            parts.append("      register: oeb_apply")
            parts.append("      changed_when: \"'created' in oeb_apply.stdout or 'configured' in oeb_apply.stdout\"")
        elif storage == "nfs-subdir":
            sc_name = "nfs-client"
            parts.append("    - name: Ensure Helm is installed (for nfs-subdir provisioner)")
            parts.append("      ansible.builtin.shell: |")
            parts.append("        set -e")
            parts.append("        if ! command -v helm >/dev/null 2>&1; then")
            parts.append("          curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash")
            parts.append("        fi")
            parts.append("      args: {creates: /usr/local/bin/helm}")
            parts.append("    - name: Add nfs-subdir helm repo")
            parts.append("      ansible.builtin.command: helm repo add nfs-subdir-external-provisioner https://kubernetes-sigs.github.io/nfs-subdir-external-provisioner/")
            parts.append("      register: helm_add_nfs")
            parts.append("      changed_when: \"'has been added' in helm_add_nfs.stdout\"")
            parts.append("      failed_when: helm_add_nfs.rc != 0 and 'already exists' not in helm_add_nfs.stderr")
            parts.append("    - name: Update helm repos")
            parts.append("      ansible.builtin.command: helm repo update")
            parts.append("      changed_when: false")
            parts.append("    - name: Install / upgrade nfs-subdir-external-provisioner")
            parts.append("      ansible.builtin.command: >-")
            parts.append("        helm upgrade --install nfs-subdir nfs-subdir-external-provisioner/nfs-subdir-external-provisioner")
            parts.append("        --namespace kube-system")
            parts.append(f"        --set nfs.server={yaml_str(nfs_server)}")
            parts.append(f"        --set nfs.path={yaml_str(nfs_path)}")
            parts.append("        --set storageClass.name=nfs-client")
            parts.append("      environment: {KUBECONFIG: /root/.kube/bootstrap-local.conf}")

        if sc_name and storage_default:
            parts.append(f"    - name: Wait for StorageClass '{sc_name}' to be registered")
            parts.append("      ansible.builtin.command: >-")
            parts.append(f"        kubectl get storageclass {sc_name}")
            parts.append("      environment: {KUBECONFIG: /root/.kube/bootstrap-local.conf}")
            parts.append("      register: sc_check")
            parts.append("      retries: 30")
            parts.append("      delay: 10")
            parts.append("      until: sc_check.rc == 0")
            parts.append("      changed_when: false")
            parts.append("    - name: Clear existing default StorageClass annotation")
            parts.append("      ansible.builtin.shell: |")
            parts.append("        set -e")
            parts.append("        for sc in $(kubectl get sc -o jsonpath='{.items[?(@.metadata.annotations.storageclass\\.kubernetes\\.io/is-default-class==\"true\")].metadata.name}'); do")
            parts.append(f"          if [ \"$sc\" != \"{sc_name}\" ]; then")
            parts.append("            kubectl annotate sc \"$sc\" storageclass.kubernetes.io/is-default-class-")
            parts.append("          fi")
            parts.append("        done")
            parts.append("      environment: {KUBECONFIG: /root/.kube/bootstrap-local.conf}")
            parts.append("      changed_when: false")
            parts.append(f"    - name: Mark '{sc_name}' as default StorageClass")
            parts.append("      ansible.builtin.command: >-")
            parts.append(f"        kubectl patch storageclass {sc_name}")
            parts.append("        -p '{\"metadata\":{\"annotations\":{\"storageclass.kubernetes.io/is-default-class\":\"true\"}}}'")
            parts.append("      environment: {KUBECONFIG: /root/.kube/bootstrap-local.conf}")
            parts.append("      register: sc_default")
            parts.append("      changed_when: \"'patched' in sc_default.stdout\"")

        parts.append("    - name: Verify Kubernetes API remains healthy after add-ons")
        parts.append("      ansible.builtin.command: kubectl --kubeconfig=/root/.kube/bootstrap-local.conf get --raw=/readyz")
        parts.append("      register: api_ready_after_addons")
        parts.append("      retries: 60")
        parts.append("      delay: 5")
        parts.append("      until: api_ready_after_addons.rc == 0")
        parts.append("      changed_when: false")
        parts.append("    - name: Wait for all Kubernetes nodes to become Ready")
        parts.append("      ansible.builtin.command: kubectl --kubeconfig=/root/.kube/bootstrap-local.conf wait --for=condition=Ready nodes --all --timeout=300s")
        parts.append("      register: nodes_ready")
        parts.append("      failed_when: false")
        parts.append("      changed_when: false")
        parts.append("    - name: Collect Kubernetes diagnostics when cluster is not stable")
        parts.append("      ansible.builtin.shell: |")
        parts.append("        set +e")
        parts.append("        echo '=== API /readyz ==='")
        parts.append("        kubectl --kubeconfig=/root/.kube/bootstrap-local.conf get --raw=/readyz 2>&1")
        parts.append("        echo")
        parts.append("        echo '=== Nodes ==='")
        parts.append("        kubectl --kubeconfig=/root/.kube/bootstrap-local.conf get nodes -o wide 2>&1")
        parts.append("        echo")
        parts.append("        echo '=== kube-system pods ==='")
        parts.append("        kubectl --kubeconfig=/root/.kube/bootstrap-local.conf -n kube-system get pods -o wide 2>&1")
        parts.append("        echo")
        parts.append("        echo '=== kubelet status ==='")
        parts.append("        systemctl --no-pager -l status kubelet 2>&1 | sed -n '1,120p'")
        parts.append("        echo")
        parts.append("        echo '=== control-plane containers ==='")
        parts.append("        crictl ps -a 2>&1 | sed -n '1,120p'")
        parts.append("        echo")
        parts.append("        echo '=== kube-proxy pods ==='")
        parts.append("        kubectl --kubeconfig=/root/.kube/bootstrap-local.conf -n kube-system get pods -l k8s-app=kube-proxy -o wide 2>&1")
        parts.append("        echo")
        parts.append("        echo '=== calico install-cni logs ==='")
        parts.append("        for pod in $(kubectl --kubeconfig=/root/.kube/bootstrap-local.conf -n kube-system get pods -l k8s-app=calico-node -o name 2>/dev/null); do")
        parts.append("          echo \"--- $pod / install-cni ---\"")
        parts.append("          kubectl --kubeconfig=/root/.kube/bootstrap-local.conf -n kube-system logs \"$pod\" -c install-cni --tail=80 2>&1 || true")
        parts.append("        done")
        parts.append("        echo")
        parts.append("        echo '=== kube-scheduler logs ==='")
        parts.append("        for pod in $(kubectl --kubeconfig=/root/.kube/bootstrap-local.conf -n kube-system get pods -l component=kube-scheduler -o name 2>/dev/null); do")
        parts.append("          echo \"--- $pod / kube-scheduler ---\"")
        parts.append("          kubectl --kubeconfig=/root/.kube/bootstrap-local.conf -n kube-system logs \"$pod\" -c kube-scheduler --tail=80 2>&1 || true")
        parts.append("        done")
        parts.append("      args: {executable: /bin/bash}")
        parts.append("      register: cluster_diag")
        parts.append("      changed_when: false")
        parts.append("      when: nodes_ready.rc != 0")
        parts.append("    - name: Stop if Kubernetes cluster did not stabilize")
        parts.append("      ansible.builtin.fail:")
        parts.append("        msg: '{{ cluster_diag.stdout | default(nodes_ready.stderr, true) }}'")
        parts.append("      when: nodes_ready.rc != 0")

        if fetch_kubeconfig:
            parts.append(f"    - name: Fetch kubeconfig to controller (~/.kube/{cluster}.yaml)")
            parts.append("      ansible.builtin.fetch:")
            parts.append("        src: /etc/kubernetes/admin.conf")
            parts.append(f"        dest: \"~/.kube/{cluster}.yaml\"")
            parts.append("        flat: true")
            parts.append("    - name: Rewrite server URL in fetched kubeconfig")
            parts.append("      delegate_to: localhost")
            parts.append("      become: false")
            parts.append("      ansible.builtin.replace:")
            parts.append(f"        path: \"~/.kube/{cluster}.yaml\"")
            parts.append("        regexp: 'https://[^\\s]+:6443'")
            parts.append(f"        replace: \"https://{{{{ inventory_hostname }}}}:6443\"")
        parts.append("")

    if first_cp_name:
        parts.append(f"# First control-plane node: {first_cp_name}")
    return "\n".join(parts) + "\n"


def sidecar_files(values: Dict[str, Any], targets: Dict[str, Any]) -> Dict[str, str]:
    """Extra files to write into the project repo (GitOps)."""
    cluster = slugify(values.get("cluster_name") or "cluster", "cluster")
    cps = _norm_nodes(values.get("control_planes"),
                      values.get("ssh_user_default") or "root",
                      values.get("ssh_port_default") or 22)
    workers = _norm_nodes(values.get("workers"),
                          values.get("ssh_user_default") or "root",
                          values.get("ssh_port_default") or 22)
    inv = _inventory_yaml(cluster, cps, workers)
    return {
        f"inventories/{cluster}.yml": inv,
    }
