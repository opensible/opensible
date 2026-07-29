# CI/CD Pipeline (Git → Build → Push → Deploy)

Container CI/CD pipeline that runs entirely from Ansible — no external CI
server required. Suitable for isolated / on-prem environments where the
OpenSible worker is your build agent.

## Stages

1. **Prepare builder** — installs Docker + buildx on the builder host.
2. **Checkout** — clones the specified Git ref (branch/tag/SHA). Supports
   token-based auth for private repos on GitHub, GitLab, Gitea, Forgejo.
3. **Build** — `docker buildx build` with configurable Dockerfile path,
   context, `--build-arg` values and multi-arch platforms.
4. **Push** — logs into the registry and pushes the image with the
   configured tag. Optionally also pushes a `:<git-sha>` tag for
   traceability.
5. **Deploy (optional)** — SSHes into a target host, pulls the image, and
   rolls the container using either `docker run` or `docker compose up -d`.

## Supported registries

- Harbor
- Sonatype Nexus (Docker hosted repo)
- Zot
- Docker Hub
- GitHub Container Registry (ghcr.io)
- GitLab Container Registry
- Any other OCI-compliant registry (generic mode)

Enable **Allow insecure registry** for HTTP-only or self-signed setups —
`/etc/docker/daemon.json` is patched and Docker is restarted on the builder.

## Secrets

Store the registry password / Git token in an OpenSible vault and reference
it from the fields as `{{ registry_password }}` / `{{ git_token }}`. Attach
the matching vault under **Vault files** so they are decrypted at run time.
