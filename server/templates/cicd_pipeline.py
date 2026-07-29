"""Template: Git → Build → Push (Docker image) → optional Deploy.

Runs on a builder host that has (or will get) Docker + git installed. Clones
a source repository, builds a container image, pushes it to a registry
(Harbor / Sonatype Nexus / Zot / Docker Hub / GHCR / GitLab / etc.), and
optionally SSHes to a target host and rolls the container using
``docker run`` or ``docker compose up -d`` with the new image tag.

Kept dependency-free of the internal Ansible-worker HTTP infra — a normal
Ansible playbook you can trigger from Build & Deployment Jobs.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ._common import (
    render_hosts,
    yaml_str,
    VAULT_FILES_VARIABLE,
    parse_vault_files,
    vars_files_lines,
)


TEMPLATE = {
    "id": "cicd-pipeline",
    "name": "CI/CD Pipeline (Git → Build → Push → Deploy)",
    "category": "CI/CD",
    "icon": "git-branch",
    "description": (
        "Clone a Git repo (GitHub / GitLab / Gitea / Forgejo), build a "
        "container image with docker buildx, push it to any OCI registry "
        "(Harbor, Sonatype Nexus, Zot, Docker Hub, GHCR, GitLab Registry), "
        "and optionally deploy to a target host via docker or docker compose."
    ),
    "tags": ["cicd", "docker", "git", "registry", "deploy"],
    "variables": [
        # Git source
        {"name": "git_repo_url", "label": "Git repository URL",
         "type": "string", "required": True,
         "placeholder": "https://github.com/acme/app.git"},
        {"name": "git_ref", "label": "Git ref (branch, tag or SHA)",
         "type": "string", "default": "main"},
        {"name": "git_username", "label": "Git username (optional for private repos)",
         "type": "string", "default": ""},
        {"name": "git_token", "label": "Git token / password (use vault: '{{ git_token }}')",
         "type": "string", "default": ""},

        # Build
        {"name": "dockerfile_path", "label": "Dockerfile path (relative to repo root)",
         "type": "string", "default": "Dockerfile"},
        {"name": "build_context", "label": "Build context",
         "type": "string", "default": "."},
        {"name": "build_args", "label": "Build args (KEY=VALUE, one per line)",
         "type": "textarea", "rows": 3, "default": ""},
        {"name": "platforms", "label": "Target platforms (comma-separated)",
         "type": "string", "default": "linux/amd64"},

        # Registry
        {"name": "registry_url", "label": "Registry hostname (e.g. harbor.example.com)",
         "type": "string", "required": True,
         "placeholder": "harbor.example.com"},
        {"name": "registry_type", "label": "Registry type",
         "type": "select", "default": "harbor",
         "options": [
             {"value": "harbor", "label": "Harbor"},
             {"value": "nexus", "label": "Sonatype Nexus"},
             {"value": "zot", "label": "Zot"},
             {"value": "dockerhub", "label": "Docker Hub"},
             {"value": "ghcr", "label": "GitHub Container Registry"},
             {"value": "gitlab", "label": "GitLab Registry"},
             {"value": "generic", "label": "Other (generic OCI)"},
         ]},
        {"name": "registry_username", "label": "Registry username",
         "type": "string", "default": ""},
        {"name": "registry_password", "label": "Registry password/token (use vault)",
         "type": "string", "default": ""},
        {"name": "registry_insecure", "label": "Allow insecure (HTTP / self-signed) registry",
         "type": "boolean", "default": False},
        {"name": "image_repository", "label": "Image repository (project/name)",
         "type": "string", "required": True,
         "placeholder": "myproject/app"},
        {"name": "image_tag", "label": "Image tag",
         "type": "string", "default": "latest"},
        {"name": "also_tag_git_sha", "label": "Also push a :<git-sha> tag",
         "type": "boolean", "default": True},

        # Deploy
        {"name": "deploy_enabled", "label": "Deploy to target host after push",
         "type": "boolean", "default": False},
        {"name": "deploy_target_host", "label": "Deploy target (inventory host or IP)",
         "type": "string", "default": ""},
        {"name": "deploy_mode", "label": "Deploy mode",
         "type": "select", "default": "docker-run",
         "options": [
             {"value": "docker-run", "label": "docker run (single container)"},
             {"value": "docker-compose", "label": "docker compose up -d"},
         ]},
        {"name": "container_name", "label": "Container name (docker run mode)",
         "type": "string", "default": "app"},
        {"name": "container_ports", "label": "Port mappings (HOST:CONTAINER, one per line)",
         "type": "textarea", "rows": 3, "default": ""},
        {"name": "container_env", "label": "Container env (KEY=VALUE, one per line)",
         "type": "textarea", "rows": 3, "default": ""},
        {"name": "compose_file_path", "label": "docker-compose.yml path on target (compose mode)",
         "type": "string", "default": "/opt/app/docker-compose.yml"},

        {"name": "workdir", "label": "Builder work directory",
         "type": "string", "default": "/var/lib/opensible/cicd"},
        {"name": "become", "label": "Run as sudo (become)", "type": "boolean", "default": True},
        VAULT_FILES_VARIABLE,
    ],
}


def suggested_filename(values: Dict[str, Any]) -> str:
    return "tmpl-cicd-pipeline.yml"


def _kv_lines(raw: str) -> List[str]:
    out: List[str] = []
    for ln in (raw or "").splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        out.append(s)
    return out


def render(values: Dict[str, Any], targets: Dict[str, Any]) -> str:
    hosts = render_hosts(targets)
    become = "true" if values.get("become", True) else "false"

    v = lambda k, d="": values.get(k, d)

    git_repo = str(v("git_repo_url", "")).strip()
    git_ref = str(v("git_ref", "main")).strip() or "main"
    git_user = str(v("git_username", "")).strip()
    git_token = str(v("git_token", "")).strip()

    dockerfile = str(v("dockerfile_path", "Dockerfile")).strip() or "Dockerfile"
    context = str(v("build_context", ".")).strip() or "."
    platforms = str(v("platforms", "linux/amd64")).strip() or "linux/amd64"
    build_args = _kv_lines(str(v("build_args", "")))

    registry = str(v("registry_url", "")).strip()
    registry_type = str(v("registry_type", "harbor")).strip() or "harbor"
    reg_user = str(v("registry_username", "")).strip()
    reg_pass = str(v("registry_password", "")).strip()
    reg_insecure = bool(v("registry_insecure", False))

    image_repo = str(v("image_repository", "")).strip()
    image_tag = str(v("image_tag", "latest")).strip() or "latest"
    tag_sha = bool(v("also_tag_git_sha", True))

    deploy_enabled = bool(v("deploy_enabled", False))
    deploy_host = str(v("deploy_target_host", "")).strip()
    deploy_mode = str(v("deploy_mode", "docker-run")).strip() or "docker-run"
    container_name = str(v("container_name", "app")).strip() or "app"
    ports = [p.strip() for p in str(v("container_ports", "")).splitlines() if p.strip() and not p.strip().startswith("#")]
    envs = _kv_lines(str(v("container_env", "")))
    compose_file = str(v("compose_file_path", "/opt/app/docker-compose.yml")).strip()

    workdir = str(v("workdir", "/var/lib/opensible/cicd")).strip() or "/var/lib/opensible/cicd"

    image_full = f"{registry}/{image_repo}:{image_tag}"

    # Build docker buildx args
    ba_flags = " ".join([f"--build-arg {yaml_str(a)}" for a in build_args])
    tag_flags = f"-t {yaml_str(image_full)}"
    if tag_sha:
        tag_flags += f" -t {yaml_str(registry + '/' + image_repo + ':{{ git_sha.stdout }}')}"

    parts: List[str] = [
        "---",
        f"# Rendered from template: {TEMPLATE['name']}",
        "- name: CI/CD — Build & push image",
        f"  hosts: {hosts}",
        f"  become: {become}",
        "  gather_facts: true",
        *vars_files_lines(parse_vault_files(values.get("vault_files"))),
        "  vars:",
        f"    cicd_workdir: {yaml_str(workdir)}",
        f"    cicd_repo_url: {yaml_str(git_repo)}",
        f"    cicd_git_ref: {yaml_str(git_ref)}",
        f"    cicd_registry: {yaml_str(registry)}",
        f"    cicd_image: {yaml_str(image_full)}",
        "  tasks:",
        "    - name: Ensure git + curl present",
        "      ansible.builtin.package:",
        "        name: [git, curl, ca-certificates]",
        "        state: present",
        "    - name: Ensure Docker is installed",
        "      ansible.builtin.shell: |",
        "        set -e",
        "        if ! command -v docker >/dev/null 2>&1; then",
        "          curl -fsSL https://get.docker.com | sh",
        "        fi",
        "        systemctl enable --now docker || true",
        "        docker buildx version >/dev/null 2>&1 || docker buildx create --use --name opensible >/dev/null 2>&1 || true",
        "      args:",
        "        executable: /bin/bash",
        "      changed_when: false",
        "    - name: Ensure workdir exists",
        "      ansible.builtin.file:",
        "        path: \"{{ cicd_workdir }}\"",
        "        state: directory",
        "        mode: '0755'",
    ]

    # Git checkout (supports token via URL rewriting)
    if git_user or git_token:
        creds = ""
        if git_user and git_token:
            creds = f"{git_user}:{git_token}@"
        elif git_token:
            creds = f"oauth2:{git_token}@"
        parts += [
            "    - name: Build authenticated clone URL",
            "      ansible.builtin.set_fact:",
            f"        cicd_clone_url: \"{{{{ cicd_repo_url | regex_replace('^https?://', 'https://' + {yaml_str(creds)}) }}}}\"",
            "      no_log: true",
        ]
    else:
        parts += [
            "    - name: Use plain clone URL",
            "      ansible.builtin.set_fact:",
            "        cicd_clone_url: \"{{ cicd_repo_url }}\"",
        ]

    src_dir = "{{ cicd_workdir }}/src"
    parts += [
        "    - name: Clone / update source",
        "      ansible.builtin.git:",
        "        repo: \"{{ cicd_clone_url }}\"",
        f"        dest: \"{src_dir}\"",
        "        version: \"{{ cicd_git_ref }}\"",
        "        force: true",
        "      no_log: true",
        "    - name: Resolve short git SHA",
        "      ansible.builtin.command: git rev-parse --short HEAD",
        "      args:",
        f"        chdir: \"{src_dir}\"",
        "      register: git_sha",
        "      changed_when: false",
    ]

    # Registry login
    if reg_user and reg_pass:
        parts += [
            "    - name: Login to registry",
            "      ansible.builtin.shell: |",
            f"        echo {yaml_str(reg_pass)} | docker login {yaml_str(registry)} -u {yaml_str(reg_user)} --password-stdin",
            "      args:",
            "        executable: /bin/bash",
            "      no_log: true",
            "      changed_when: true",
        ]

    if reg_insecure:
        parts += [
            "    - name: Configure insecure registry",
            "      ansible.builtin.copy:",
            "        dest: /etc/docker/daemon.json",
            "        mode: '0644'",
            "        content: |",
            "          {",
            f"            \"insecure-registries\": [{yaml_str(registry)}]",
            "          }",
            "      register: daemon_json",
            "    - name: Restart docker if daemon.json changed",
            "      ansible.builtin.service:",
            "        name: docker",
            "        state: restarted",
            "      when: daemon_json.changed",
        ]

    # Build & push
    parts += [
        "    - name: Build & push image with docker buildx",
        "      ansible.builtin.shell: |",
        "        set -euo pipefail",
        f"        cd {src_dir}",
        f"        docker buildx build \\",
        f"          --platform {platforms} \\",
        f"          -f {yaml_str(dockerfile)} \\",
        f"          {ba_flags} \\",
        f"          {tag_flags} \\",
        f"          --push \\",
        f"          {yaml_str(context)}",
        "      args:",
        "        executable: /bin/bash",
        "      register: build_result",
        "      changed_when: true",
        "    - name: Show pushed image",
        "      ansible.builtin.debug:",
        "        msg: \"Pushed {{ cicd_image }} (git {{ git_sha.stdout }})\"",
    ]

    # ------------------------------------------------------------------ #
    # Deploy play                                                        #
    # ------------------------------------------------------------------ #
    if deploy_enabled and deploy_host:
        parts += [
            "",
            "- name: CI/CD — Deploy image to target host",
            f"  hosts: {yaml_str(deploy_host)}",
            f"  become: {become}",
            "  gather_facts: false",
            *vars_files_lines(parse_vault_files(values.get("vault_files"))),
            "  vars:",
            f"    cicd_image: {yaml_str(image_full)}",
            "  tasks:",
            "    - name: Ensure Docker on deploy target",
            "      ansible.builtin.shell: |",
            "        if ! command -v docker >/dev/null 2>&1; then",
            "          curl -fsSL https://get.docker.com | sh",
            "        fi",
            "        systemctl enable --now docker || true",
            "      args:",
            "        executable: /bin/bash",
            "      changed_when: false",
        ]
        if reg_user and reg_pass:
            parts += [
                "    - name: Login to registry on target",
                "      ansible.builtin.shell: |",
                f"        echo {yaml_str(reg_pass)} | docker login {yaml_str(registry)} -u {yaml_str(reg_user)} --password-stdin",
                "      args:",
                "        executable: /bin/bash",
                "      no_log: true",
                "      changed_when: true",
            ]
        parts += [
            "    - name: Pull latest image on target",
            "      ansible.builtin.command: docker pull {{ cicd_image }}",
            "      changed_when: true",
        ]

        if deploy_mode == "docker-compose":
            parts += [
                "    - name: docker compose pull & up -d",
                "      ansible.builtin.shell: |",
                "        set -e",
                f"        cd $(dirname {yaml_str(compose_file)})",
                f"        docker compose -f {yaml_str(compose_file)} pull",
                f"        docker compose -f {yaml_str(compose_file)} up -d",
                "      args:",
                "        executable: /bin/bash",
                "      changed_when: true",
            ]
        else:
            port_flags = " ".join([f"-p {yaml_str(p)}" for p in ports])
            env_flags = " ".join([f"-e {yaml_str(e)}" for e in envs])
            parts += [
                f"    - name: Stop & remove existing container ({container_name})",
                "      ansible.builtin.shell: |",
                f"        docker rm -f {yaml_str(container_name)} 2>/dev/null || true",
                "      args:",
                "        executable: /bin/bash",
                "      changed_when: true",
                "    - name: Run new container",
                "      ansible.builtin.shell: |",
                f"        docker run -d --name {yaml_str(container_name)} --restart unless-stopped \\",
                f"          {port_flags} {env_flags} \\",
                "          {{ cicd_image }}",
                "      args:",
                "        executable: /bin/bash",
                "      changed_when: true",
            ]

    parts.append("")
    return "\n".join(parts)
