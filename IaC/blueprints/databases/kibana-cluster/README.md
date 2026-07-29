# Kibana HA (Docker cluster)

Deploys N stateless Kibana nodes using the official
`docker.elastic.co/kibana/kibana` image. Every node runs one container
supervised by a dedicated systemd unit (`kibana-docker.service`) so the
container survives host reboots and Docker daemon restarts. Because
Kibana keeps all its state in Elasticsearch, HA is achieved by running
multiple identical nodes behind a load balancer.

## What it does

- Ensures Docker Engine is present (installs via the official convenience
  script when missing) and enables the `docker` service.
- Writes and starts `kibana-docker.service` on every node with:
  - `SERVER_NAME` / `SERVER_UUID` derived per host (stable identity in
    `.kibana*` system indices)
  - `ELASTICSEARCH_HOSTS` = the full list of Elasticsearch node URLs
  - `ELASTICSEARCH_USERNAME` / `ELASTICSEARCH_PASSWORD` when security is on
  - `ELASTICSEARCH_SSL_VERIFICATIONMODE` for HTTPS backends
  - Optional shared `XPACK_ENCRYPTEDSAVEDOBJECTS_ENCRYPTIONKEY` (required
    for HA saved-object decryption to work across nodes)
- Optionally opens the Kibana HTTP port and the health port in
  `ufw` / `firewalld` when active.
- Installs a small **per-node HTTP health dashboard** (default `:5680`)
  serving:
  - `/`             HTML view (auto-refresh 5s) with Kibana core + plugin
                    status and upstream Elasticsearch summary
  - `/health.json`  machine-readable payload (200 available/degraded,
                    503 unavailable / unreachable)
  - `/live`         liveness probe

## Recommended sizing

Use at least **2 Kibana nodes** behind a load balancer for real HA. A
single node still works but a Kibana restart makes the UI unavailable
until it comes back.

## Load balancer

Point HAProxy / Nginx / Traefik / your cloud LB at every Kibana node on
port `5601` (or your chosen `http_port`). Use `/api/status` as the LB
health check, or the local `/health.json` on port `5680` — both return
`200` when the node is healthy and `503` otherwise.

## Security

`security_enabled: false` by default so the blueprint comes up out of the
box against an unauthenticated lab Elasticsearch. For production, turn on
security in Elasticsearch first, then set `security_enabled: true`,
`elasticsearch_username: kibana_system`, and provide
`elasticsearch_password` via an `ansible-vault` file listed in
`vault_files`.

Set `encryption_key` to the SAME 32+ character value on every node — this
is what lets any Kibana instance decrypt saved objects, reports and
credentials created on a different node.

## Test

```bash
# Per-node status
curl http://<any-node-ip>:5601/api/status

# Health dashboard
open http://<any-node-ip>:5680/
```
