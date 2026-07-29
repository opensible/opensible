# Redis / Valkey + Sentinel

A production-ready primary/replica cluster with **automatic failover**
via Redis Sentinel. Installed from **distro packages** and managed by
**systemd** — no Docker, no Bitnami.

## Topology

- **Node 1** is the initial primary.
- All other nodes are replicas (`replicaof <primary>`).
- Every node also runs `redis-sentinel` (or `valkey-sentinel`).
- Sentinels coordinate failover if the primary goes down.

For real HA you need **at least 3 nodes** — Sentinel needs a majority
to agree on failover. Single-node runs are supported for dev.

## Flavors

- `redis` (default): distro `redis-server` + `redis-sentinel`.
- `valkey`: distro `valkey` package (Ubuntu 24.04+ / EPEL 9+).

## Client connection

Clients should connect via **Sentinel**, not directly to a fixed IP.
Point your Redis client at every node's Sentinel port (`26379`) and
give it the master name (`mymaster` by default). The client will
resolve the current primary and reconnect automatically after failover.

## Key variables

| Variable | Default | Notes |
|---|---|---|
| `flavor` | `redis` | `redis` or `valkey`. |
| `port` | `6379` | Redis client port. |
| `sentinel_port` | `26379` | Sentinel port. |
| `primary_name` | `mymaster` | Symbolic name used by clients. |
| `quorum` | `2` | Sentinels required to agree on failover. |
| `requirepass` | *(empty)* | Auth password; shared by Redis and Sentinel. |
| `maxmemory` | *(empty)* | e.g. `512mb`. |
| `appendonly` | `true` | Enables AOF persistence. |

## Verifying failover

```bash
# Ask Sentinel who the primary is
redis-cli -p 26379 SENTINEL get-master-addr-by-name mymaster

# Force a failover
redis-cli -p 26379 SENTINEL failover mymaster
```
