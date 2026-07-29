"""Template: Apply Kubernetes manifests / Helm charts / install K8s tooling.

A flexible catch-all for anything Kubernetes-related that runs from an Ansible
control host that already has (or should have) `kubectl`/`helm` available.

Supported actions:
  * apply       - kubectl apply -f (paste raw YAML, supports multi-doc)
  * delete      - kubectl delete -f (same paste box)
  * kustomize   - kubectl apply -k <dir> (path on target)
  * helm        - helm upgrade --install <release> <chart>
  * install-tools - install kubectl / helm / k9s / kustomize via apt/snap/binary
"""
from __future__ import annotations

from typing import Any, Dict

from ._common import (
    slugify, yaml_str, render_hosts, indent_block,
    VAULT_FILES_VARIABLE, parse_vault_files, vars_files_lines,
)


TEMPLATE = {
    "id": "k8s-manifests",
    "name": "Kubernetes Manifests / Helm / Tooling",
    "category": "Kubernetes",
    "icon": "ship",
    "description": (
        "Apply raw Kubernetes YAML (kubectl apply -f), install a Helm chart, run kustomize, "
        "or install cluster tooling (kubectl, helm, k9s, kustomize)."
    ),
    "tags": ["kubernetes", "kubectl", "helm", "k8s", "manifests", "argocd"],
    "variables": [
        {
            "name": "action",
            "label": "Action",
            "type": "select",
            "default": "apply",
            "options": [
                {"value": "apply", "label": "kubectl apply -f  (paste YAML)"},
                {"value": "delete", "label": "kubectl delete -f  (paste YAML)"},
                {"value": "kustomize", "label": "kubectl apply -k  (kustomize path)"},
                {"value": "helm", "label": "helm upgrade --install"},
                {"value": "install-tools", "label": "Install tools (kubectl / helm / k9s / kustomize)"},
            ],
        },
        # ---- kubectl apply / delete ----
        {
            "name": "manifest_yaml",
            "label": "Manifest YAML (multi-doc supported, e.g. argocd-appproject.yaml)",
            "type": "code",
            "language": "yaml",
            "rows": 18,
            "default": (
                "apiVersion: v1\n"
                "kind: Namespace\n"
                "metadata:\n"
                "  name: demo\n"
                "---\n"
                "apiVersion: apps/v1\n"
                "kind: Deployment\n"
                "metadata:\n"
                "  name: hello\n"
                "  namespace: demo\n"
                "spec:\n"
                "  replicas: 1\n"
                "  selector:\n"
                "    matchLabels: {app: hello}\n"
                "  template:\n"
                "    metadata: {labels: {app: hello}}\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: hello\n"
                "          image: nginxdemos/hello:plain-text\n"
            ),
            "visible_when": {"action": ["apply", "delete"]},
        },
        {
            "name": "namespace",
            "label": "Namespace (optional, -n)",
            "type": "string",
            "default": "",
            "visible_when": {"action": ["apply", "delete", "kustomize", "helm"]},
        },
        {
            "name": "create_namespace",
            "label": "Create namespace if missing",
            "type": "boolean",
            "default": False,
            "visible_when": {"action": ["apply", "kustomize", "helm"]},
        },
        {
            "name": "server_side",
            "label": "Server-side apply (--server-side)",
            "type": "boolean",
            "default": False,
            "visible_when": {"action": ["apply"]},
        },
        {
            "name": "force_conflicts",
            "label": "Force conflicts (--force-conflicts, server-side only)",
            "type": "boolean",
            "default": False,
            "visible_when": {"action": ["apply"]},
        },
        {
            "name": "wait_condition",
            "label": "Wait after apply (e.g. 'condition=Available deployment --all -n demo --timeout=180s')",
            "type": "string",
            "default": "",
            "visible_when": {"action": ["apply", "kustomize"]},
        },
        # ---- kustomize ----
        {
            "name": "kustomize_path",
            "label": "Kustomize directory path on target host",
            "type": "string",
            "default": "/root/manifests/overlays/dev",
            "visible_when": {"action": ["kustomize"]},
        },
        # ---- helm ----
        {
            "name": "helm_release",
            "label": "Helm release name",
            "type": "string",
            "default": "my-release",
            "visible_when": {"action": ["helm"]},
        },
        {
            "name": "helm_chart",
            "label": "Chart (repo/name  OR  oci://…  OR  local path)",
            "type": "string",
            "default": "bitnami/nginx",
            "visible_when": {"action": ["helm"]},
        },
        {
            "name": "helm_repo_name",
            "label": "Repo alias (added via helm repo add if URL is set)",
            "type": "string",
            "default": "bitnami",
            "visible_when": {"action": ["helm"]},
        },
        {
            "name": "helm_repo_url",
            "label": "Repo URL (leave empty for OCI / local chart)",
            "type": "string",
            "default": "https://charts.bitnami.com/bitnami",
            "visible_when": {"action": ["helm"]},
        },
        {
            "name": "helm_version",
            "label": "Chart version (empty = latest)",
            "type": "string",
            "default": "",
            "visible_when": {"action": ["helm"]},
        },
        {
            "name": "helm_values",
            "label": "values.yaml (inline)",
            "type": "code",
            "language": "yaml",
            "rows": 10,
            "default": "# key: value\n",
            "visible_when": {"action": ["helm"]},
        },
        {
            "name": "helm_wait",
            "label": "helm --wait (block until ready)",
            "type": "boolean",
            "default": True,
            "visible_when": {"action": ["helm"]},
        },
        # ---- install-tools ----
        {
            "name": "install_kubectl",
            "label": "Install kubectl",
            "type": "boolean",
            "default": True,
            "visible_when": {"action": ["install-tools"]},
        },
        {
            "name": "install_helm",
            "label": "Install helm",
            "type": "boolean",
            "default": True,
            "visible_when": {"action": ["install-tools"]},
        },
        {
            "name": "install_kustomize",
            "label": "Install kustomize",
            "type": "boolean",
            "default": False,
            "visible_when": {"action": ["install-tools"]},
        },
        {
            "name": "install_k9s",
            "label": "Install k9s",
            "type": "boolean",
            "default": False,
            "visible_when": {"action": ["install-tools"]},
        },
        {
            "name": "install_method",
            "label": "Install method",
            "type": "select",
            "default": "binary",
            "options": [
                {"value": "binary", "label": "Official binary (curl-based, distro-agnostic)"},
                {"value": "apt", "label": "APT (Debian/Ubuntu, official repos)"},
                {"value": "snap", "label": "Snap (Ubuntu)"},
            ],
            "visible_when": {"action": ["install-tools"]},
        },
        # ---- general ----
        {
            "name": "kubeconfig",
            "label": "KUBECONFIG path on target host",
            "type": "string",
            "default": "/etc/rancher/k3s/k3s.yaml",
            "help": "Used for all kubectl/helm commands. Common values: /etc/rancher/k3s/k3s.yaml (k3s), /root/.kube/config, ~/.kube/config",
        },
        {
            "name": "kube_context",
            "label": "kube context (optional, --context)",
            "type": "string",
            "default": "",
        },
        {
            "name": "become",
            "label": "Run as sudo (become)",
            "type": "boolean",
            "default": True,
        },
        VAULT_FILES_VARIABLE,
    ],
}


def suggested_filename(values: Dict[str, Any]) -> str:
    action = values.get("action") or "apply"
    if action == "helm":
        return f"tmpl-k8s-helm-{slugify(values.get('helm_release'), 'release')}.yml"
    if action == "install-tools":
        return "tmpl-k8s-install-tools.yml"
    if action == "kustomize":
        return f"tmpl-k8s-kustomize-{slugify(values.get('namespace') or 'default')}.yml"
    if action == "delete":
        return "tmpl-k8s-delete.yml"
    return "tmpl-k8s-apply.yml"


def _kube_env(values: Dict[str, Any]) -> str:
    """Env block for kubectl/helm — indented under 'environment:'."""
    kc = values.get("kubeconfig") or ""
    lines = []
    if kc:
        lines.append(f"        KUBECONFIG: {yaml_str(kc)}")
    return "\n".join(lines) if lines else "        {}"


def _ctx_flag(values: Dict[str, Any]) -> str:
    ctx = values.get("kube_context") or ""
    return f" --context={ctx}" if ctx else ""


def _ns_flag(values: Dict[str, Any]) -> str:
    ns = values.get("namespace") or ""
    return f" -n {ns}" if ns else ""


def render(values: Dict[str, Any], targets: Dict[str, Any]) -> str:
    action = values.get("action") or "apply"
    hosts = render_hosts(targets)
    become = "true" if values.get("become", True) else "false"
    env_block = _kube_env(values)
    ctx = _ctx_flag(values)
    ns = _ns_flag(values)

    header = [
        "---",
        f"# Rendered from template: {TEMPLATE['name']}  (action={action})",
        f"- name: Kubernetes — {action}",
        f"  hosts: {hosts}",
        f"  become: {become}",
        "  gather_facts: false",
        *vars_files_lines(parse_vault_files(values.get("vault_files"))),
        "  environment:",
        env_block,
        "  tasks:",
    ]

    tasks: list[str] = []

    if action in ("apply", "delete"):
        manifest = values.get("manifest_yaml") or ""
        tasks += [
            "    - name: Ensure staging dir",
            "      ansible.builtin.file:",
            "        path: /tmp/opensible-k8s",
            "        state: directory",
            "        mode: '0755'",
            "    - name: Write manifest file",
            "      ansible.builtin.copy:",
            "        dest: /tmp/opensible-k8s/manifest.yaml",
            "        mode: '0644'",
            "        content: |",
            indent_block(manifest, "          "),
        ]
        if action == "apply" and values.get("create_namespace") and values.get("namespace"):
            tasks += [
                "    - name: Ensure namespace exists",
                "      ansible.builtin.shell: |",
                f"        kubectl{ctx} get ns {values.get('namespace')} >/dev/null 2>&1 || kubectl{ctx} create ns {values.get('namespace')}",
            ]
        verb = "apply" if action == "apply" else "delete"
        extra = ""
        if action == "apply":
            if values.get("server_side"):
                extra += " --server-side"
            if values.get("force_conflicts"):
                extra += " --force-conflicts"
        tasks += [
            f"    - name: kubectl {verb} -f",
            "      ansible.builtin.shell: |",
            f"        kubectl{ctx}{ns} {verb} -f /tmp/opensible-k8s/manifest.yaml{extra}",
            "      register: kubectl_out",
            "    - name: Show kubectl output",
            "      ansible.builtin.debug:",
            "        var: kubectl_out.stdout_lines",
        ]
        wait = (values.get("wait_condition") or "").strip()
        if action == "apply" and wait:
            tasks += [
                "    - name: Wait for readiness",
                "      ansible.builtin.shell: |",
                f"        kubectl{ctx} wait --for={wait}",
            ]

    elif action == "kustomize":
        path = values.get("kustomize_path") or "."
        if values.get("create_namespace") and values.get("namespace"):
            tasks += [
                "    - name: Ensure namespace exists",
                "      ansible.builtin.shell: |",
                f"        kubectl{ctx} get ns {values.get('namespace')} >/dev/null 2>&1 || kubectl{ctx} create ns {values.get('namespace')}",
            ]
        tasks += [
            "    - name: kubectl apply -k",
            "      ansible.builtin.shell: |",
            f"        kubectl{ctx}{ns} apply -k {path}",
            "      register: kubectl_out",
            "    - name: Show output",
            "      ansible.builtin.debug:",
            "        var: kubectl_out.stdout_lines",
        ]
        wait = (values.get("wait_condition") or "").strip()
        if wait:
            tasks += [
                "    - name: Wait for readiness",
                "      ansible.builtin.shell: |",
                f"        kubectl{ctx} wait --for={wait}",
            ]

    elif action == "helm":
        release = values.get("helm_release") or "my-release"
        chart = values.get("helm_chart") or "bitnami/nginx"
        repo_name = values.get("helm_repo_name") or ""
        repo_url = values.get("helm_repo_url") or ""
        version = values.get("helm_version") or ""
        vals = values.get("helm_values") or ""
        wait_flag = " --wait" if values.get("helm_wait", True) else ""
        ver_flag = f" --version {version}" if version else ""
        create_ns = " --create-namespace" if values.get("create_namespace") else ""

        if repo_url and repo_name:
            tasks += [
                "    - name: Add helm repo",
                "      ansible.builtin.shell: |",
                f"        helm repo add {repo_name} {repo_url} || true",
                "        helm repo update",
            ]
        tasks += [
            "    - name: Write values.yaml",
            "      ansible.builtin.copy:",
            f"        dest: /tmp/opensible-k8s/values-{slugify(release, 'release')}.yaml",
            "        mode: '0644'",
            "        content: |",
            indent_block(vals, "          "),
            "    - name: helm upgrade --install",
            "      ansible.builtin.shell: |",
            f"        helm{ctx} upgrade --install {release} {chart}{ver_flag}{ns}{create_ns}{wait_flag} -f /tmp/opensible-k8s/values-{slugify(release, 'release')}.yaml",
            "      register: helm_out",
            "    - name: Show helm output",
            "      ansible.builtin.debug:",
            "        var: helm_out.stdout_lines",
        ]

    elif action == "install-tools":
        method = values.get("install_method") or "binary"
        want_kubectl = bool(values.get("install_kubectl", True))
        want_helm = bool(values.get("install_helm", True))
        want_kustomize = bool(values.get("install_kustomize", False))
        want_k9s = bool(values.get("install_k9s", False))

        if method == "apt":
            tasks += [
                "    - name: Install prerequisites",
                "      ansible.builtin.apt:",
                "        name: [apt-transport-https, ca-certificates, curl, gnupg]",
                "        state: present",
                "        update_cache: true",
            ]
            if want_kubectl:
                tasks += [
                    "    - name: Add kubernetes apt key",
                    "      ansible.builtin.shell: |",
                    "        install -m 0755 -d /etc/apt/keyrings",
                    "        curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.30/deb/Release.key | gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg",
                    "        echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.30/deb/ /' > /etc/apt/sources.list.d/kubernetes.list",
                    "    - name: Install kubectl (apt)",
                    "      ansible.builtin.apt:",
                    "        name: kubectl",
                    "        state: present",
                    "        update_cache: true",
                ]
            if want_helm:
                tasks += [
                    "    - name: Add helm apt key/repo",
                    "      ansible.builtin.shell: |",
                    "        curl -fsSL https://baltocdn.com/helm/signing.asc | gpg --dearmor -o /usr/share/keyrings/helm.gpg",
                    "        echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/helm.gpg] https://baltocdn.com/helm/stable/debian/ all main' > /etc/apt/sources.list.d/helm-stable-debian.list",
                    "    - name: Install helm (apt)",
                    "      ansible.builtin.apt:",
                    "        name: helm",
                    "        state: present",
                    "        update_cache: true",
                ]
            if want_kustomize:
                tasks += [
                    "    - name: Install kustomize (binary — no apt package)",
                    "      ansible.builtin.shell: |",
                    "        curl -s 'https://raw.githubusercontent.com/kubernetes-sigs/kustomize/master/hack/install_kustomize.sh' | bash",
                    "        mv kustomize /usr/local/bin/",
                    "      args: {creates: /usr/local/bin/kustomize}",
                ]
            if want_k9s:
                tasks += [
                    "    - name: Install k9s (binary — no apt package)",
                    "      ansible.builtin.shell: |",
                    "        curl -sSL https://github.com/derailed/k9s/releases/latest/download/k9s_Linux_amd64.tar.gz | tar -xz -C /usr/local/bin k9s",
                    "      args: {creates: /usr/local/bin/k9s}",
                ]
        elif method == "snap":
            for pkg, want, classic in [
                ("kubectl", want_kubectl, True),
                ("helm", want_helm, True),
                ("kustomize", want_kustomize, False),
                ("k9s", want_k9s, False),
            ]:
                if want:
                    flag = " --classic" if classic else ""
                    tasks += [
                        f"    - name: snap install {pkg}",
                        "      ansible.builtin.shell: |",
                        f"        snap install {pkg}{flag}",
                    ]
        else:  # binary
            if want_kubectl:
                tasks += [
                    "    - name: Install kubectl (binary)",
                    "      ansible.builtin.shell: |",
                    "        VER=$(curl -Ls https://dl.k8s.io/release/stable.txt)",
                    "        curl -sSL -o /usr/local/bin/kubectl \"https://dl.k8s.io/release/${VER}/bin/linux/amd64/kubectl\"",
                    "        chmod +x /usr/local/bin/kubectl",
                    "      args: {creates: /usr/local/bin/kubectl}",
                ]
            if want_helm:
                tasks += [
                    "    - name: Install helm (binary)",
                    "      ansible.builtin.shell: |",
                    "        curl -sSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash",
                    "      args: {creates: /usr/local/bin/helm}",
                ]
            if want_kustomize:
                tasks += [
                    "    - name: Install kustomize (binary)",
                    "      ansible.builtin.shell: |",
                    "        curl -s 'https://raw.githubusercontent.com/kubernetes-sigs/kustomize/master/hack/install_kustomize.sh' | bash",
                    "        mv kustomize /usr/local/bin/",
                    "      args: {creates: /usr/local/bin/kustomize}",
                ]
            if want_k9s:
                tasks += [
                    "    - name: Install k9s (binary)",
                    "      ansible.builtin.shell: |",
                    "        curl -sSL https://github.com/derailed/k9s/releases/latest/download/k9s_Linux_amd64.tar.gz | tar -xz -C /usr/local/bin k9s",
                    "      args: {creates: /usr/local/bin/k9s}",
                ]

        tasks += [
            "    - name: Show installed versions",
            "      ansible.builtin.shell: |",
            "        set +e",
            "        which kubectl && kubectl version --client=true --output=yaml 2>/dev/null | head -5",
            "        which helm && helm version --short",
            "        which kustomize && kustomize version",
            "        which k9s && k9s version --short",
            "      register: versions",
            "      changed_when: false",
            "    - name: Versions",
            "      ansible.builtin.debug:",
            "        var: versions.stdout_lines",
        ]

    return "\n".join(header + tasks) + "\n"
