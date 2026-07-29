# Logstash HA (Docker cluster)

Deploys N Logstash nodes using the official
`docker.elastic.co/logstash/logstash` image. Every node runs one container
supervised by a dedicated systemd unit (`logstash-docker.service`) so the
container survives host reboots and Docker daemon restarts. Nodes share
an identical pipeline configuration and all write to the same
Elasticsearch backend — HA is achieved by pointing Beats/clients at the
full list of Logstash nodes (Filebeat, Metricbeat, etc. do their own
round-robin + failover).

## What it does

- Ensures Docker Engine is present (installs via the official convenience
  script when missing) and enables the `docker` service.
- Writes `/etc/opensible/logstash/logstash.yml` (node settings + monitoring
  API) and `/etc/opensible/logstash/pipeline/main.conf` (default
  beats-in → elasticsearch-out pipeline).
- Runs `logstash-docker.service` on every node with the pinned image tag,
  mounting the config + pipeline directories read-only into the container.
- Optionally opens the Beats port and the health port in `ufw` / `firewalld`.
- Installs a **per-node HTTP health dashboard** (default `:9680`) serving:
  - `/`             HTML view (auto-refresh 5s) with Logstash node stats,
                    per-pipeline events/in/out/errors, and upstream
                    Elasticsearch reachability
  - `/health.json`  machine-readable payload (200 healthy, 503 otherwise)
  - `/live`         liveness probe

## Default pipeline

```
input  { beats { port => 5044 } }
output { elasticsearch { hosts => [<elasticsearch_hosts>] index => "logs-%{+YYYY.MM.dd}" } }
```

Replace `/etc/opensible/logstash/pipeline/main.conf` on each node to run
your own filters — the systemd unit reloads via `docker restart` when the
file changes on the next deploy.

## Recommended sizing

Use at least **2 Logstash nodes**. Beats clients accept an array of
Logstash outputs and will load-balance + failover between them on their
own, so no extra load balancer is required for Beats traffic.

## Security

`security_enabled: false` by default so the blueprint comes up out of the
box against an unauthenticated lab Elasticsearch. For production, turn on
security in Elasticsearch, then set `security_enabled: true`, provide
`elasticsearch_username` and `elasticsearch_password` via an
`ansible-vault` file listed in `vault_files`.

## Test

```bash
# Logstash monitoring API (bound to 127.0.0.1)
ssh <node> curl -s http://127.0.0.1:9600/_node/stats | head

# Health dashboard
open http://<any-node-ip>:9680/

# Send a test event with netcat over the beats port after enabling the
# tcp input, or point a Filebeat at logstash: hosts: ["<node>:5044"]
```
