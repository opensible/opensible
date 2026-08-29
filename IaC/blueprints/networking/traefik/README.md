# Traefik Reverse Proxy / Load Balancer

Installs Traefik v3 from the official GitHub release tarball and manages it with
systemd. Two console blueprints share this folder:

| Blueprint | Group | Docker provider |
| --- | --- | --- |
| Traefik Reverse Proxy / LB (`traefik-proxy`) | Security & Secrets | off |
| Traefik Reverse Proxy (Docker provider) (`traefik-proxy-docker`) | Docker & Containers | on |

## What it deploys

- `/usr/local/bin/traefik` (version pinned, arch auto-detected: amd64 / arm64 / armv7)
- `/etc/traefik/traefik.yml` — static config (entrypoints, api, metrics, providers, ACME)
- `/etc/traefik/dynamic/` — file provider directory, watched and hot-reloaded
- `/etc/traefik/dynamic/dashboard-auth.yml` — dashboard router + bcrypt basic auth
- `/etc/systemd/system/traefik.service` — hardened unit running as the `traefik` user
- Optional keepalived VRRP VIP (cluster mode, via the console template)

## Entrypoints

| Name | Default port | Purpose |
| --- | --- | --- |
| `web` | 80 | HTTP (optionally redirected to HTTPS) |
| `websecure` | 443 | HTTPS / TLS (ACME cert resolver `le` when enabled) |
| `traefik` | 8080 | Dashboard (`/dashboard/`), API (`/api`), Prometheus (`/metrics`) |

## Dashboard

The bcrypt hash is generated on the target host with `htpasswd -nbB` — no
credential hash leaves the controller. Leave the password blank in the console
and a stable one is derived from the deployment name and printed in the run
summary.

    http://<host>:8080/dashboard/

## Docker provider

When enabled, the playbook verifies `/var/run/docker.sock` exists, adds the
`traefik` user to the `docker` group and sets `SupplementaryGroups=docker` on
the unit. Route containers by labelling them:

```yaml
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.app.rule=Host(`app.example.com`)"
  - "traefik.http.routers.app.entrypoints=websecure"
  - "traefik.http.routers.app.tls=true"
```

Deploy the Docker Engine blueprint first if Docker is not installed.

## Static routes (file provider)

Drop YAML files into `/etc/traefik/dynamic/`; Traefik reloads them without a
restart:

```yaml
http:
  routers:
    api:
      rule: "Host(`api.example.com`)"
      entryPoints: [websecure]
      service: api
      tls:
        certResolver: le
  services:
    api:
      loadBalancer:
        servers:
          - url: "http://10.0.0.21:8080"
```

## Standalone run

```bash
ansible-playbook -i inventories/opensible.yml playbook.yml -e @vars.example.yml
```

## Verify

```bash
systemctl status traefik
curl -I http://<host>:80
curl -u admin:<password> http://<host>:8080/api/rawdata
journalctl -u traefik -n 50
```

Removal is handled by the **Uninstall — Traefik Reverse Proxy / LB** blueprint.
