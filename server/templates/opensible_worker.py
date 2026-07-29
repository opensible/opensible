"""Template: Deploy OpenSible Worker container with host networking."""
from __future__ import annotations

from typing import Any, Dict

from ._common import (
    render_hosts,
    yaml_str,
    VAULT_FILES_VARIABLE,
    parse_vault_files,
    vars_files_lines,
)


TEMPLATE = {
    "id": "opensible-worker",
    "name": "OpenSible Worker",
    "category": "OpenSible",
    "icon": "boxes",
    "description": (
        "Deploy the OpenSible Worker (Ansible/OpenTofu execution agent) as a "
        "Docker container with host networking. Persists /app/data so the "
        "registration token survives restarts, and mounts SSH keys so the "
        "worker can reach inventory hosts."
    ),
    "tags": ["opensible", "worker", "docker", "ansible"],
    "variables": [
        {"name": "image", "label": "Worker image",
         "type": "string", "default": "registry.openqrm.dev/opensible/worker:latest"},
        {"name": "registry_url", "label": "Registry URL (for docker login)",
         "type": "string", "default": "registry.openqrm.dev",
         "help": "Registry hostname used for `docker login`. Leave blank to skip login (public image)."},
        {"name": "registry_username", "label": "Registry username",
         "type": "string", "default": "",
         "help": "Plain value or Jinja from a vault file, e.g. \"{{ registry_username }}\". Leave blank to skip login."},
        {"name": "registry_password", "label": "Registry password / token",
         "type": "password", "default": "",
         "help": "Plain value or Jinja from a vault file, e.g. \"{{ registry_password }}\". Leave blank to skip login."},
        {"name": "container_name", "label": "Container name",
         "type": "string", "default": "opensible-worker"},
        {"name": "backend_url", "label": "Backend API URL (WORKER_SERVER_URL)",
         "type": "string", "default": "http://127.0.0.1:5000",
         "help": "Reachable from the worker host. With --network host, 127.0.0.1 points to the backend on the same host."},
        {"name": "worker_name", "label": "Worker display name",
         "type": "string", "default": "{{ inventory_hostname }}"},
        {"name": "worker_tags", "label": "Worker tags (comma-separated)",
         "type": "string", "default": ""},
        {"name": "worker_token", "label": "Pre-issued worker token (optional)",
         "type": "string", "default": "",
         "help": "Leave empty to let the worker self-register on first start."},
        {"name": "data_volume", "label": "Data volume (host path or named volume)",
         "type": "string", "default": "opensible-worker-data"},
        {"name": "ssh_keys_host_path", "label": "SSH keys host path (mounted read-only)",
         "type": "string", "default": "/root/.ssh",
         "help": "Directory on the worker host that will be mounted at /root/.ssh inside the container. Must contain a private key trusted by every inventory host the worker needs to reach (e.g. authorized_keys on 10.0.250.10)."},
        {"name": "ssh_private_key", "label": "SSH private key content (optional)",
         "type": "textarea", "default": "",
         "help": "Paste a private key (or use \"{{ worker_ssh_private_key }}\" from a vault file) to provision it into <ssh_keys_host_path>/id_ed25519 (0600). Leave blank if the key is already present on the worker host."},
        {"name": "ssh_key_filename", "label": "SSH private key filename",
         "type": "string", "default": "id_ed25519",
         "help": "File name written under the SSH keys host path when a key is provided."},
        {"name": "verify_ssh_key", "label": "Fail if no SSH private key is found",
         "type": "boolean", "default": True,
         "help": "Preflight check: fails early if the mounted SSH keys directory contains no id_* private key. Prevents silent 'Permission denied' on inventory hosts."},
        {"name": "tz", "label": "Timezone",
         "type": "string", "default": "UTC"},
        {"name": "ansible_host_key_checking", "label": "ANSIBLE_HOST_KEY_CHECKING",
         "type": "select", "default": "False",
         "options": [
             {"value": "False", "label": "False (accept new hosts)"},
             {"value": "True", "label": "True (pre-seeded known_hosts)"},
         ]},
        {"name": "restart_policy", "label": "Restart policy",
         "type": "select", "default": "unless-stopped",
         "options": [
             {"value": "unless-stopped", "label": "unless-stopped"},
             {"value": "always", "label": "always"},
             {"value": "on-failure", "label": "on-failure"},
             {"value": "no", "label": "no"},
         ]},
        {"name": "pull", "label": "Always pull latest image before start",
         "type": "boolean", "default": True},
        {"name": "install_docker", "label": "Install Docker Engine if missing",
         "type": "boolean", "default": True},
        {"name": "become", "label": "Run as sudo (become)",
         "type": "boolean", "default": True},
        VAULT_FILES_VARIABLE,
    ],
}


def suggested_filename(values: Dict[str, Any]) -> str:
    return "tmpl-opensible-worker.yml"


def render(values: Dict[str, Any], targets: Dict[str, Any]) -> str:
    hosts = render_hosts(targets)
    become = "true" if values.get("become", True) else "false"
    image = values.get("image") or "registry.openqrm.dev/opensible/worker:latest"
    name = values.get("container_name") or "opensible-worker"
    backend = values.get("backend_url") or "http://127.0.0.1:5000"
    worker_name = values.get("worker_name") or "{{ inventory_hostname }}"
    worker_tags = values.get("worker_tags") or ""
    worker_token = values.get("worker_token") or ""
    data_volume = values.get("data_volume") or "opensible-worker-data"
    ssh_keys = values.get("ssh_keys_host_path") or "/root/.ssh"
    ssh_private_key = values.get("ssh_private_key") or ""
    ssh_key_filename = values.get("ssh_key_filename") or "id_ed25519"
    verify_ssh_key = bool(values.get("verify_ssh_key", True))
    tz = values.get("tz") or "UTC"
    ahkc = values.get("ansible_host_key_checking", "False")
    restart_policy = values.get("restart_policy") or "unless-stopped"
    pull_always = bool(values.get("pull", True))
    install_docker = bool(values.get("install_docker", True))

    env_block = [
        f"          WORKER_SERVER_URL: {yaml_str(backend)}",
        f"          WORKER_TOKEN_FILE: /app/data/worker.token",
        f"          WORKER_NAME: {yaml_str(worker_name)}",
        f"          WORKER_TAGS: {yaml_str(worker_tags)}",
        f"          DATA_DIR: /app/data",
        f"          ANSIBLE_HOST_KEY_CHECKING: {yaml_str(ahkc)}",
        f"          TZ: {yaml_str(tz)}",
    ]
    if worker_token:
        env_block.append(f"          WORKER_TOKEN: {yaml_str(worker_token)}")

    parts = [
        "---",
        f"# Rendered from template: {TEMPLATE['name']}",
        f"- name: Deploy OpenSible Worker",
        f"  hosts: {hosts}",
        f"  become: {become}",
        "  gather_facts: true",
        *vars_files_lines(parse_vault_files(values.get("vault_files"))),
        "  tasks:",
    ]

    if install_docker:
        parts += [
            "    - name: Install prerequisites (Debian)",
            "      ansible.builtin.apt:",
            "        name: [ca-certificates, curl, gnupg, lsb-release]",
            "        state: present",
            "        update_cache: true",
            "      when: ansible_os_family == 'Debian'",

            "    - name: Detect Debian package architecture for Docker repo",
            "      ansible.builtin.command: dpkg --print-architecture",
            "      register: docker_deb_arch",
            "      changed_when: false",
            "      when: ansible_os_family == 'Debian'",

            "    - name: Add Docker APT signing key (Debian)",
            "      ansible.builtin.get_url:",
            "        url: https://download.docker.com/linux/{{ ansible_distribution | lower }}/gpg",
            "        dest: /etc/apt/keyrings/docker.asc",
            "        mode: '0644'",
            "        force: false",
            "      when: ansible_os_family == 'Debian'",

            "    - name: Add Docker APT repository (Debian)",
            "      ansible.builtin.apt_repository:",
            "        repo: \"deb [arch={{ docker_deb_arch.stdout }} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/{{ ansible_distribution | lower }} {{ ansible_distribution_release }} stable\"",
            "        filename: docker",
            "        state: present",
            "        update_cache: true",
            "      when: ansible_os_family == 'Debian'",

            "    - name: Install Docker Engine (Debian)",
            "      ansible.builtin.apt:",
            "        name: [docker-ce, docker-ce-cli, containerd.io, docker-buildx-plugin, docker-compose-plugin]",
            "        state: present",
            "      when: ansible_os_family == 'Debian'",

            "    - name: Ensure docker service is running",
            "      ansible.builtin.service:",
            "        name: docker",
            "        enabled: true",
            "        state: started",
        ]

    parts += [
        "    - name: Ensure host SSH keys directory exists",
        "      ansible.builtin.file:",
        f"        path: {yaml_str(ssh_keys)}",
        "        state: directory",
        "        mode: '0700'",
    ]

    if ssh_private_key:
        parts += [
            "    - name: Provision SSH private key for worker",
            "      ansible.builtin.copy:",
            f"        dest: {yaml_str(ssh_keys.rstrip('/') + '/' + ssh_key_filename)}",
            f"        content: {yaml_str(ssh_private_key)}",
            "        mode: '0600'",
            "      no_log: true",
        ]

    if verify_ssh_key:
        parts += [
            "    - name: Preflight — find SSH private keys on worker host",
            "      ansible.builtin.find:",
            f"        paths: {yaml_str(ssh_keys)}",
            "        patterns: 'id_*'",
            "        excludes: '*.pub'",
            "        file_type: file",
            "      register: worker_ssh_keys",
            "    - name: Preflight — fail if no SSH private key is present",
            "      ansible.builtin.fail:",
            "        msg: >-",
            f"          No SSH private key found under {ssh_keys} on the worker host.",
            "          The worker container will fail to reach inventory hosts with",
            "          'Permission denied (publickey,password)'. Provide ssh_private_key",
            "          in the template, or place a trusted key on the host before running.",
            "      when: worker_ssh_keys.matched | int == 0",
        ]


    if str(data_volume).startswith("/"):
        parts += [
            "    - name: Ensure worker data volume/dir exists (host path)",
            "      ansible.builtin.file:",
            f"        path: {yaml_str(data_volume)}",
            "        state: directory",
            "        mode: '0755'",
        ]



    # community.docker modules require the `docker` and `requests` Python
    # libraries on the target host. Install them once before any docker_*
    # task runs, otherwise docker_login/docker_image/docker_container fail
    # with "Failed to import the required Python library (requests)".
    parts += [
        "    - name: Install Python deps for community.docker (Debian)",
        "      ansible.builtin.apt:",
        "        name: [python3-docker, python3-requests]",
        "        state: present",
        "        update_cache: true",
        "      when: ansible_os_family == 'Debian'",
        "    - name: Install Python deps for community.docker (RedHat)",
        "      ansible.builtin.package:",
        "        name: [python3-docker, python3-requests]",
        "        state: present",
        "      when: ansible_os_family == 'RedHat'",
    ]

    registry_url = values.get("registry_url") or ""
    registry_username = values.get("registry_username") or ""
    registry_password = values.get("registry_password") or ""

    if registry_username and registry_password:
        parts += [
            "    - name: Log in to container registry",
            "      community.docker.docker_login:",
            f"        registry_url: {yaml_str(registry_url)}",
            f"        username: {yaml_str(registry_username)}",
            f"        password: {yaml_str(registry_password)}",
            "        reauthorize: true",
            "      no_log: true",
        ]


    parts += [

        "    - name: Pull worker image",
        "      community.docker.docker_image:",
        f"        name: {yaml_str(image)}",
        "        source: pull",
        f"        force_source: {'true' if pull_always else 'false'}",

        "    - name: Run OpenSible Worker container",
        "      community.docker.docker_container:",
        f"        name: {yaml_str(name)}",
        f"        image: {yaml_str(image)}",
        f"        restart_policy: {yaml_str(restart_policy)}",
        "        network_mode: host",
        "        detach: true",
        f"        pull: {'true' if pull_always else 'false'}",
        "        env:",
        *env_block,
        "        volumes:",
        f"          - {yaml_str(str(data_volume) + ':/app/data')}",
        f"          - {yaml_str(str(ssh_keys) + ':/root/.ssh:ro')}",

        "    - name: Show worker container status",
        "      ansible.builtin.shell: |",
        "        {% raw %}docker ps --filter name=" + str(name) + " --format '{{.Names}}\t{{.Status}}'{% endraw %}",
        "      register: worker_ps",
        "      changed_when: false",
        "    - name: Worker status",
        "      ansible.builtin.debug:",
        "        var: worker_ps.stdout_lines",
        "",
    ]
    return "\n".join(parts)
