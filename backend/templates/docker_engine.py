"""Template: Install Docker Engine + compose plugin."""
from __future__ import annotations

from typing import Any, Dict

from ._common import render_hosts, VAULT_FILES_VARIABLE, parse_vault_files, vars_files_lines


TEMPLATE = {
    "id": "docker-engine",
    "name": "Docker Engine",
    "category": "Containers",
    "icon": "package",
    "description": "Install Docker CE, the compose plugin, enable the daemon, and add a user to the docker group.",
    "tags": ["docker", "bootstrap"],
    "variables": [
        {"name": "docker_user", "label": "User to add to docker group", "type": "string", "default": "ubuntu"},
        {"name": "enable_buildx", "label": "Install buildx plugin", "type": "boolean", "default": True},
        {"name": "become", "label": "Run as sudo (become)", "type": "boolean", "default": True},
        VAULT_FILES_VARIABLE,
    ],
}


def suggested_filename(values: Dict[str, Any]) -> str:
    return "tmpl-docker-engine.yml"


def render(values: Dict[str, Any], targets: Dict[str, Any]) -> str:
    hosts = render_hosts(targets)
    become = "true" if values.get("become", True) else "false"
    user = values.get("docker_user") or "ubuntu"
    buildx = "docker-buildx-plugin" if values.get("enable_buildx", True) else ""

    pkg_lines = [
        "          - docker-ce",
        "          - docker-ce-cli",
        "          - containerd.io",
        "          - docker-compose-plugin",
    ]
    if buildx:
        pkg_lines.append(f"          - {buildx}")

    parts = [
        "---",
        f"# Rendered from template: {TEMPLATE['name']}",
        "- name: Install Docker Engine",
        f"  hosts: {hosts}",
        f"  become: {become}",
        "  gather_facts: false",
        *vars_files_lines(parse_vault_files(values.get("vault_files"))),
        "  tasks:",
        "    - name: Bootstrap Python if missing",
        "      ansible.builtin.raw: |",
        "        if command -v python3 >/dev/null 2>&1; then",
        "          echo 'python3 present'",
        "          exit 0",
        "        fi",
        "        if command -v apt-get >/dev/null 2>&1; then",
        "          export DEBIAN_FRONTEND=noninteractive",
        "          apt-get update -y",
        "          apt-get install -y python3",
        "          echo 'python3 installed'",
        "          exit 0",
        "        fi",
        "        echo 'python3 missing and apt-get is unavailable' >&2",
        "        exit 1",
        "      register: bootstrap_python",
        "      changed_when: \"'python3 installed' in bootstrap_python.stdout\"",
        "    - name: Gather facts after Python bootstrap",
        "      ansible.builtin.setup:",
        "    - name: Detect upstream Debian/Ubuntu distro id for Docker repo",
        "      ansible.builtin.shell: |",
        "        set -e",
        "        . /etc/os-release",
        "        if [ \"$ID\" = \"ubuntu\" ] || [ \"$ID\" = \"debian\" ]; then",
        "          echo \"$ID\"",
        "        elif echo \"${ID_LIKE:-}\" | grep -qw ubuntu; then",
        "          echo ubuntu",
        "        elif echo \"${ID_LIKE:-}\" | grep -qw debian; then",
        "          echo debian",
        "        else",
        "          echo \"unsupported: $ID\" >&2; exit 1",
        "        fi",
        "      register: docker_distro_id",
        "      changed_when: false",
        "      when: ansible_os_family == 'Debian'",
        "    - name: Detect upstream Debian/Ubuntu codename for Docker repo",
        "      ansible.builtin.shell: |",
        "        set -e",
        "        . /etc/os-release",
        "        if [ -n \"${UBUNTU_CODENAME:-}\" ]; then",
        "          echo \"$UBUNTU_CODENAME\"",
        "        elif [ -n \"${DEBIAN_CODENAME:-}\" ]; then",
        "          echo \"$DEBIAN_CODENAME\"",
        "        elif [ \"$ID\" = \"ubuntu\" ] || [ \"$ID\" = \"debian\" ]; then",
        "          echo \"${VERSION_CODENAME:-}\"",
        "        else",
        "          echo \"${VERSION_CODENAME:-}\"",
        "        fi",
        "      register: docker_distro_codename",
        "      changed_when: false",
        "      when: ansible_os_family == 'Debian'",
        "    - name: Install prerequisites",
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
        "    - name: Ensure keyrings dir",
        "      ansible.builtin.file:",
        "        path: /etc/apt/keyrings",
        "        state: directory",
        "        mode: '0755'",
        "      when: ansible_os_family == 'Debian'",
        "    - name: Add Docker apt key",
        "      ansible.builtin.get_url:",
        "        url: https://download.docker.com/linux/{{ docker_distro_id.stdout }}/gpg",
        "        dest: /etc/apt/keyrings/docker.asc",
        "        mode: '0644'",
        "        force: true",
        "      when: ansible_os_family == 'Debian'",
        "    - name: Add Docker apt repo",
        "      ansible.builtin.apt_repository:",
        "        repo: >-",
        "          deb [arch={{ docker_deb_arch.stdout }} signed-by=/etc/apt/keyrings/docker.asc]",
        "          https://download.docker.com/linux/{{ docker_distro_id.stdout }}",
        "          {{ docker_distro_codename.stdout }} stable",
        "        filename: docker",
        "        state: present",
        "        update_cache: true",
        "      when: ansible_os_family == 'Debian'",
        "    - name: Install docker packages",
        "      ansible.builtin.apt:",
        "        name:",
        *pkg_lines,
        "        state: present",
        "        update_cache: true",
        "      when: ansible_os_family == 'Debian'",
        "    - name: Enable and start docker",
        "      ansible.builtin.service:",

        "        name: docker",
        "        state: started",
        "        enabled: true",
        f"    - name: Add {user} to docker group",
        "      ansible.builtin.user:",
        f"        name: {user}",
        "        groups: docker",
        "        append: true",
        "      when: ansible_os_family == 'Debian'",
        "",
    ]
    return "\n".join(parts)
