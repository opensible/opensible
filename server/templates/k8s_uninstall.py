"""Template: Complete Kubernetes cluster uninstall / purge.

Renders a playbook that tears down a kubeadm or k3s cluster and cleans up
all leftover state so the node(s) can be reinstalled cleanly.

Safety:
  - Requires an explicit confirmation boolean.
  - Runs `kubeadm reset -f` or the k3s uninstall script.
  - Removes containerd, kubelet, kubeadm, kubectl packages when requested.
  - Deletes etcd data, CNI config, kubeconfig, and container images.
  - Flushes iptables / ipvs rules and removes CNI tunnel interfaces.
"""
from __future__ import annotations

from typing import Any, Dict

from ._common import (
    slugify, yaml_str, render_hosts,
    VAULT_FILES_VARIABLE, parse_vault_files, vars_files_lines,
)


TEMPLATE = {
    "id": "k8s-uninstall",
    "name": "Kubernetes Uninstall / Purge",
    "category": "Kubernetes",
    "icon": "trash-2",
    "description": (
        "Completely remove a kubeadm or k3s Kubernetes cluster from the selected nodes. "
        "Drains nodes, resets kubeadm / runs k3s-uninstall, removes packages, deletes "
        "etcd/CNI data, flushes iptables/ipvs, and cleans container images."
    ),
    "tags": ["kubernetes", "kubeadm", "k3s", "uninstall", "cleanup", "purge"],
    "variables": [
        {"name": "cluster_name", "label": "Cluster name (for reporting)", "type": "string",
         "default": "opensible", "placeholder": "prod-k8s"},
        {"name": "cluster_type", "label": "Cluster type",
         "type": "select", "default": "auto",
         "options": [
             {"value": "auto", "label": "Auto-detect (kubeadm or k3s)"},
             {"value": "kubeadm", "label": "kubeadm (upstream Kubernetes)"},
             {"value": "k3s", "label": "k3s (Rancher / SUSE)"},
         ],
         "help": "Auto-detect looks for /usr/bin/kubeadm or /usr/local/bin/k3s on each node."},
        {"name": "confirm_purge", "label": "Confirm destructive purge",
         "type": "boolean", "default": False,
         "help": "Must be enabled or the playbook fails immediately. This protects against accidental runs."},
        {"name": "drain_nodes", "label": "Drain nodes before reset (kubeadm only)",
         "type": "boolean", "default": True,
         "help": "Attempts kubectl drain if a kubeconfig is readable; ignored for k3s."},
        {"name": "remove_packages", "label": "Remove kubelet / kubeadm / kubectl packages",
         "type": "boolean", "default": True},
        {"name": "remove_containerd", "label": "Remove containerd package and data",
         "type": "boolean", "default": True},
        {"name": "remove_etcd_data", "label": "Remove etcd data directories",
         "type": "boolean", "default": True},
        {"name": "remove_cni", "label": "Remove CNI config and tunnel interfaces",
         "type": "boolean", "default": True},
        {"name": "remove_images", "label": "Remove all container images",
         "type": "boolean", "default": False,
         "help": "Runs crictl rmi -a or ctr image rm -a. Slow on nodes with many images."},
        {"name": "remove_kubeconfig", "label": "Remove local kubeconfig files",
         "type": "boolean", "default": True,
         "help": "Deletes ~/.kube/config and /etc/kubernetes/admin.conf."},
        {"name": "ssh_user_default", "label": "Default SSH user for nodes", "type": "string", "default": "root"},
        {"name": "ssh_port_default", "label": "Default SSH port", "type": "number", "default": 22},
        {"name": "nodes",
         "label": "Target nodes (leave empty to run on the current inventory group)",
         "type": "nodes",
         "default": []},
        {"name": "become", "label": "Run as sudo (become)", "type": "boolean", "default": True},
        VAULT_FILES_VARIABLE,
    ],
}


def suggested_filename(values: Dict[str, Any]) -> str:
    return f"{slugify(values.get('cluster_name'), 'cluster')}-uninstall.yml"


def _render_tasks(values: Dict[str, Any]) -> str:
    confirm = bool(values.get("confirm_purge"))
    cluster_type = str(values.get("cluster_type") or "auto").lower()
    drain = bool(values.get("drain_nodes"))
    remove_packages = bool(values.get("remove_packages"))
    remove_containerd = bool(values.get("remove_containerd"))
    remove_etcd = bool(values.get("remove_etcd_data"))
    remove_cni = bool(values.get("remove_cni"))
    remove_images = bool(values.get("remove_images"))
    remove_kubeconfig = bool(values.get("remove_kubeconfig"))

    lines: list[str] = []

    # --- safety gate ---
    lines.append("  - name: Safety check — purge must be confirmed")
    lines.append("    ansible.builtin.fail:")
    lines.append('      msg: "Set confirm_purge=true to run this destructive playbook."')
    lines.append("    when: not (confirm_purge | default(false) | bool)")
    lines.append("")

    # --- auto-detect cluster type ---
    if cluster_type == "auto":
        lines.append("  - name: Detect kubeadm")
        lines.append("    ansible.builtin.stat:")
        lines.append("      path: /usr/bin/kubeadm")
        lines.append("    register: kubeadm_bin")
        lines.append("")
        lines.append("  - name: Detect k3s")
        lines.append("    ansible.builtin.stat:")
        lines.append("      path: /usr/local/bin/k3s")
        lines.append("    register: k3s_bin")
        lines.append("")
        lines.append("  - name: Set detected cluster type fact")
    else:
        lines.append("  - name: Set requested cluster type fact")

    lines.append("    ansible.builtin.set_fact:")
    if cluster_type == "auto":
        lines.append('      detected_cluster_type: "{% if kubeadm_bin.stat.exists %}kubeadm{% elif k3s_bin.stat.exists %}k3s{% else %}unknown{% endif %}"')
        lines.append('      cluster_type: "{% if kubeadm_bin.stat.exists %}kubeadm{% elif k3s_bin.stat.exists %}k3s{% else %}unknown{% endif %}"')
    else:
        lines.append(f'      cluster_type: "{cluster_type}"')
    lines.append("")
    lines.append("  - name: Fail if cluster type cannot be determined")
    lines.append("    ansible.builtin.fail:")
    lines.append('      msg: "Could not auto-detect cluster type. Set cluster_type to kubeadm or k3s, or ensure binaries are present."')
    if cluster_type == "auto":
        lines.append("    when: cluster_type == 'unknown'")
    lines.append("")

    # --- kubeadm path ---
    if cluster_type in ("auto", "kubeadm"):
        lines.append("  - name: Find kubeconfig for kubeadm")
        lines.append("    ansible.builtin.shell: |")
        lines.append("      set -o pipefail")
        lines.append("      if test -r /etc/kubernetes/admin.conf; then echo /etc/kubernetes/admin.conf")
        lines.append("      elif test -r ~/.kube/config; then echo ~/.kube/config")
        lines.append("      elif test -r /etc/rancher/k3s/k3s.yaml; then echo /etc/rancher/k3s/k3s.yaml")
        lines.append("      else echo ''")
        lines.append("      fi")
        lines.append("    args:")
        lines.append("      executable: /bin/bash")
        lines.append("    register: kubeconfig_path")
        lines.append("    changed_when: false")
        lines.append("    when: cluster_type == 'kubeadm'")
        lines.append("")

        if drain:
            lines.append("  - name: Drain node before reset")
            lines.append("    ansible.builtin.shell: |")
            lines.append("      set -o pipefail")
            lines.append('      KUBECONFIG="{{ kubeconfig_path.stdout | trim }}" kubectl drain "{{ ansible_hostname | default(inventory_hostname) }}" --ignore-daemonsets --delete-emptydir-data --force --timeout=120s || true')
            lines.append("    args:")
            lines.append("      executable: /bin/bash")
            lines.append("    when:")
            lines.append("      - cluster_type == 'kubeadm'")
            lines.append("      - drain_nodes | default(true) | bool")
            lines.append("      - kubeconfig_path.stdout | trim | length > 0")
            lines.append("    ignore_errors: true")
            lines.append("")

        lines.append("  - name: Reset kubeadm")
        lines.append("    ansible.builtin.command: kubeadm reset -f")
        lines.append("    register: kubeadm_reset")
        lines.append("    when: cluster_type == 'kubeadm'")
        lines.append("    ignore_errors: true")
        lines.append("")

    # --- k3s path ---
    if cluster_type in ("auto", "k3s"):
        lines.append("  - name: Run k3s server uninstall script")
    else:
        lines.append("  - name: Run k3s server uninstall script (skipped for kubeadm)")
    lines.append("    ansible.builtin.command: /usr/local/bin/k3s-uninstall.sh")
    lines.append("    args:")
    lines.append("      removes: /usr/local/bin/k3s-uninstall.sh")
    lines.append("    register: k3s_uninstall")
    lines.append("    when: cluster_type == 'k3s'")
    lines.append("    ignore_errors: true")
    lines.append("")
    lines.append("  - name: Run k3s agent uninstall script")
    lines.append("    ansible.builtin.command: /usr/local/bin/k3s-agent-uninstall.sh")
    lines.append("    args:")
    lines.append("      removes: /usr/local/bin/k3s-agent-uninstall.sh")
    lines.append("    when: cluster_type == 'k3s'")
    lines.append("    ignore_errors: true")
    lines.append("")

    # --- stop and disable services (only those that exist) ---
    lines.append("  - name: Stop and disable Kubernetes services (only present units)")
    lines.append("    ansible.builtin.shell: |")
    lines.append("      set -o pipefail")
    lines.append("      for svc in kubelet containerd k3s k3s-agent kubectl-proxy; do")
    lines.append("        if systemctl list-unit-files \"${svc}.service\" 2>/dev/null | grep -q \"^${svc}.service\"; then")
    lines.append("          systemctl stop \"${svc}\" 2>/dev/null || true")
    lines.append("          systemctl disable \"${svc}\" 2>/dev/null || true")
    lines.append("        fi")
    lines.append("      done")
    lines.append("      systemctl reset-failed 2>/dev/null || true")
    lines.append("    args:")
    lines.append("      executable: /bin/bash")
    lines.append("    changed_when: true")
    lines.append("    ignore_errors: true")
    lines.append("")

    # --- remove packages ---
    if remove_packages:
        lines.append("  - name: Release apt holds on Kubernetes packages")
        lines.append("    ansible.builtin.shell: |")
        lines.append("      set -o pipefail")
        lines.append("      command -v apt-mark >/dev/null 2>&1 || exit 0")
        lines.append("      apt-mark unhold kubelet kubeadm kubectl kubernetes-cni cri-tools 2>/dev/null || true")
        lines.append("    args:")
        lines.append("      executable: /bin/bash")
        lines.append("    changed_when: false")
        lines.append("    ignore_errors: true")
        lines.append("")
        lines.append("  - name: Remove Kubernetes packages")
        lines.append("    ansible.builtin.shell: |")
        lines.append("      set -o pipefail")
        lines.append("      export DEBIAN_FRONTEND=noninteractive")
        lines.append("      if command -v apt-get >/dev/null 2>&1; then")
        lines.append("        apt-get purge -y --allow-change-held-packages \\")
        lines.append("          kubelet kubeadm kubectl kubernetes-cni cri-tools || true")
        lines.append("        apt-get autoremove -y --purge || true")
        lines.append("      elif command -v dnf >/dev/null 2>&1; then")
        lines.append("        dnf remove -y kubelet kubeadm kubectl kubernetes-cni cri-tools || true")
        lines.append("      elif command -v yum >/dev/null 2>&1; then")
        lines.append("        yum remove -y kubelet kubeadm kubectl kubernetes-cni cri-tools || true")
        lines.append("      fi")
        lines.append("      dpkg --purge --force-all kubelet kubeadm kubectl kubernetes-cni cri-tools 2>/dev/null || true")
        lines.append("    args:")
        lines.append("      executable: /bin/bash")
        lines.append("    changed_when: true")
        lines.append("    ignore_errors: true")
        lines.append("")
        lines.append("  - name: Remove leftover Kubernetes binaries and apt sources")
        lines.append("    ansible.builtin.shell: |")
        lines.append("      set -o pipefail")
        lines.append("      rm -f /usr/bin/kubeadm /usr/bin/kubectl /usr/bin/kubelet \\")
        lines.append("            /usr/local/bin/kubeadm /usr/local/bin/kubectl /usr/local/bin/kubelet \\")
        lines.append("            /usr/bin/crictl /usr/local/bin/crictl /usr/local/bin/helm /usr/bin/helm || true")
        lines.append("      rm -rf /etc/default/kubelet /etc/systemd/system/kubelet.service.d \\")
        lines.append("             /etc/systemd/system/kubelet.service /usr/lib/systemd/system/kubelet.service \\")
        lines.append("             /etc/crictl.yaml /etc/sysctl.d/99-kubernetes-cri.conf \\")
        lines.append("             /etc/modules-load.d/k8s.conf || true")
        lines.append("      rm -f /etc/apt/sources.list.d/kubernetes.list /etc/apt/sources.list.d/pkgs_k8s_io.list \\")
        lines.append("            /etc/apt/keyrings/kubernetes-apt-keyring.gpg \\")
        lines.append("            /usr/share/keyrings/kubernetes-apt-keyring.gpg || true")
        lines.append("      hash -r 2>/dev/null || true")
        lines.append("      systemctl daemon-reload 2>/dev/null || true")
        lines.append("    args:")
        lines.append("      executable: /bin/bash")
        lines.append("    changed_when: true")
        lines.append("    ignore_errors: true")
        lines.append("")

    if remove_containerd:
        lines.append("  - name: Remove containerd package")
        lines.append("    ansible.builtin.shell: |")
        lines.append("      set -o pipefail")
        lines.append("      export DEBIAN_FRONTEND=noninteractive")
        lines.append("      if command -v apt-get >/dev/null 2>&1; then")
        lines.append("        apt-mark unhold containerd containerd.io runc 2>/dev/null || true")
        lines.append("        apt-get purge -y --allow-change-held-packages containerd containerd.io runc || true")
        lines.append("        apt-get autoremove -y --purge || true")
        lines.append("      elif command -v dnf >/dev/null 2>&1; then")
        lines.append("        dnf remove -y containerd containerd.io runc || true")
        lines.append("      elif command -v yum >/dev/null 2>&1; then")
        lines.append("        yum remove -y containerd containerd.io runc || true")
        lines.append("      fi")
        lines.append("      rm -f /usr/local/bin/containerd /usr/local/bin/ctr /usr/local/bin/runc || true")
        lines.append("    args:")
        lines.append("      executable: /bin/bash")
        lines.append("    changed_when: true")
        lines.append("    ignore_errors: true")
        lines.append("")


    # --- remove container images ---
    if remove_images:
        lines.append("  - name: Remove all container images")
        lines.append("    ansible.builtin.shell: |")
        lines.append("      set -o pipefail")
        lines.append("      if command -v crictl >/dev/null 2>&1; then crictl rmi -a || true; fi")
        lines.append("      if command -v ctr >/dev/null 2>&1; then ctr image rm -a || true; fi")
        lines.append("      if command -v docker >/dev/null 2>&1; then docker rmi -f $(docker images -q) || true; fi")
        lines.append("    args:")
        lines.append("      executable: /bin/bash")
        lines.append("    ignore_errors: true")
        lines.append("")

    # --- remove directories ---
    dirs_to_remove = [
        "/etc/kubernetes",
        "/var/lib/kubelet",
        "/var/lib/dockershim",
        "/var/run/kubernetes",
        "/var/lib/cni",
        "/etc/cni",
        "/opt/cni",
    ]
    if remove_etcd:
        dirs_to_remove.extend([
            "/var/lib/etcd",
            "/var/lib/etcd-backup",
        ])
    if remove_containerd:
        dirs_to_remove.extend([
            "/var/lib/containerd",
            "/run/containerd",
            "/etc/containerd",
        ])
    if remove_cni:
        dirs_to_remove.extend([
            "/etc/cni/net.d",
        ])

    lines.append("  - name: Remove Kubernetes data directories")
    lines.append("    ansible.builtin.file:")
    lines.append("      path: '{{ item }}'")
    lines.append("      state: absent")
    lines.append("    loop:")
    for d in dirs_to_remove:
        lines.append(f"      - {d}")
    lines.append("")

    if remove_kubeconfig:
        lines.append("  - name: Remove kubeconfig files")
        lines.append("    ansible.builtin.file:")
        lines.append("      path: '{{ item }}'")
        lines.append("      state: absent")
        lines.append("    loop:")
        lines.append("      - /etc/kubernetes/admin.conf")
        lines.append("      - /root/.kube/config")
        lines.append("      - /root/.kube")
        lines.append("      - /etc/rancher/k3s/k3s.yaml")
        lines.append("    ignore_errors: true")
        lines.append("")

    # --- CNI / network cleanup ---
    if remove_cni:
        lines.append("  - name: Remove CNI tunnel interfaces")
        lines.append("    ansible.builtin.shell: |")
        lines.append("      set -o pipefail")
        for iface in ["cni0", "flannel.1", "calico", "tunl0", "vxlan.calico"]:
            lines.append(f"      ip link delete {iface} 2>/dev/null || true")
        lines.append("      ip link show 2>/dev/null | awk '/^cali[a-f0-9]+/ {print $2}' | sed 's/://' | xargs -r -n1 ip link delete || true")
        lines.append("    args:")
        lines.append("      executable: /bin/bash")
        lines.append("    ignore_errors: true")
        lines.append("")

        lines.append("  - name: Flush iptables / nftables Kubernetes chains")
        lines.append("    ansible.builtin.shell: |")
        lines.append("      set -o pipefail")
        lines.append("      iptables -F 2>/dev/null || true")
        lines.append("      iptables -t nat -F 2>/dev/null || true")
        lines.append("      iptables -t mangle -F 2>/dev/null || true")
        lines.append("      iptables -X 2>/dev/null || true")
        lines.append("      iptables -t nat -X 2>/dev/null || true")
        lines.append("      iptables -t mangle -X 2>/dev/null || true")
        lines.append("      ip6tables -F 2>/dev/null || true")
        lines.append("      ip6tables -t nat -F 2>/dev/null || true")
        lines.append("      ip6tables -t mangle -F 2>/dev/null || true")
        lines.append("      ip6tables -X 2>/dev/null || true")
        lines.append("      ipvsadm --clear 2>/dev/null || true")
        lines.append("    args:")
        lines.append("      executable: /bin/bash")
        lines.append("    ignore_errors: true")
        lines.append("")

    # --- verification ---
    lines.append("  - name: Verify Kubernetes binaries are gone")
    lines.append("    ansible.builtin.shell: |")
    lines.append("      set -o pipefail")
    lines.append("      left=''")
    lines.append("      for b in kubeadm kubectl kubelet k3s; do")
    lines.append("        p=$(command -v \"$b\" 2>/dev/null || true)")
    lines.append("        if [ -n \"$p\" ]; then left=\"$left $p\"; fi")
    lines.append("      done")
    lines.append("      echo \"${left# }\"")
    lines.append("    args:")
    lines.append("      executable: /bin/bash")
    lines.append("    register: k8s_leftovers")
    lines.append("    changed_when: false")
    lines.append("    ignore_errors: true")
    lines.append("")
    if remove_packages:
        lines.append("  - name: Fail if Kubernetes binaries still present")
        lines.append("    ansible.builtin.fail:")
        lines.append('      msg: "Purge incomplete — these binaries are still installed: {{ k8s_leftovers.stdout | trim }}"')
        lines.append("    when: (k8s_leftovers.stdout | default('') | trim) | length > 0")
        lines.append("")

    # --- final summary ---
    lines.append("  - name: Show cleanup summary")
    lines.append("    ansible.builtin.debug:")
    lines.append('      msg: "Kubernetes purge completed on {{ inventory_hostname }} (cluster_type={{ cluster_type }}, leftovers=\'{{ k8s_leftovers.stdout | default(\'\') | trim }}\')"')
    lines.append("")


    return "\n".join(lines)


def render(values: Dict[str, Any], targets: Dict[str, Any] | None = None) -> str:
    vault_files = parse_vault_files(values.get("vault_files"))
    hosts = render_hosts(targets or {})

    become = "true" if values.get("become", True) else "false"

    lines: list[str] = []
    lines.append("---")
    lines.append(f"# Rendered from template: Kubernetes Uninstall / Purge  (cluster_type={yaml_str(values.get('cluster_type') or 'auto')})")
    lines.append("")
    lines.append(f"- name: Kubernetes — purge cluster '{yaml_str(values.get('cluster_name') or 'opensible')}'")
    lines.append(f"  hosts: {hosts}")
    lines.append(f"  become: {become}")
    lines.append("  gather_facts: true")
    lines.append("  vars:")
    lines.append(f"    confirm_purge: {str(values.get('confirm_purge', False)).lower()}")
    lines.append(f"    drain_nodes: {str(values.get('drain_nodes', True)).lower()}")
    lines.append(f"    cluster_type: {yaml_str(values.get('cluster_type') or 'auto')}")
    lines.extend(vars_files_lines(vault_files, indent="  "))
    lines.append("  tasks:")
    lines.append(_render_tasks(values))

    return "\n".join(lines) + "\n"
