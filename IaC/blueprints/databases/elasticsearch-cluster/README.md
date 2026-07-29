# Elasticsearch HA (Docker cluster)

Deploys an Elasticsearch cluster across N nodes using the official
`docker.elastic.co/elasticsearch/elasticsearch` image. Every node runs one
container supervised by a dedicated systemd unit
(`elasticsearch-docker.service`) so the container survives host reboots and
Docker daemon restarts.

## What it does

- Ensures Docker Engine is present (installs via the official convenience
  script when missing) and enables the `docker` service.
- Sets `vm.max_map_count=262144` (a hard Elasticsearch requirement).
- Writes and starts `elasticsearch-docker.service` on every node with:
  - `cluster.name`, `node.name`, `network.publish_host` per host
  - `discovery.seed_hosts` = every node's `IP:transport_port`
  - `cluster.initial_master_nodes` = every node name (first bootstrap only)
  - Pinned image tag, JVM heap (`ES_JAVA_OPTS`) and bind-mounted data dir
- Optionally opens HTTP (9200), transport (9300) and the health port in
  `ufw` / `firewalld` when active.
- Installs a small **per-node HTTP health dashboard** (default `:9280`)
  serving:
  - `/`             HTML view (auto-refresh 5s) with cluster + nodes + indices
  - `/health.json`  machine-readable payload (200 green/yellow, 503 red)
  - `/live`         liveness probe

## Recommended sizing

Use at least **3 master-eligible nodes** for real HA. Single-node deployments
still work but lose all failover guarantees.

## Security

`security_enabled: false` by default so the cluster comes up out of the box
for internal/lab use. For anything reachable outside a trusted network, set
`security_enabled: true` and supply `elastic_password` (ideally via an
`ansible-vault` file listed in `vault_files`).

## Test

```bash
curl http://<any-node-ip>:9200/_cluster/health?pretty
# Health dashboard:
open http://<any-node-ip>:9280/
```
