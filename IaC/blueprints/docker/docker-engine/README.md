# Docker Engine

Installs Docker CE, the compose plugin, and (optionally) buildx on Debian/Ubuntu
targets. Enables and starts the daemon, and adds a user to the `docker` group.

## Usage

```bash
ansible-playbook -i inventory.yml \
  -e @vars.example.yml \
  IaC/blueprints/docker/docker-engine/playbook.yml
```

## What it does

- Bootstraps `python3` on hosts that lack it.
- Detects upstream Debian/Ubuntu distro id + codename for the Docker apt repo.
- Adds the official Docker apt key and repository.
- Installs `docker-ce`, `docker-ce-cli`, `containerd.io`, `docker-compose-plugin`
  (and `docker-buildx-plugin` when `enable_buildx: true`).
- Enables the daemon and appends `docker_user` to the `docker` group.
