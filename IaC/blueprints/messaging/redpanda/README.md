# Redpanda Cluster (Self-Managed) + Console

Production-grade **Redpanda Self-Managed** cluster installed from the
**official Redpanda apt/dnf repository** and managed by **systemd**.
Kafka-API compatible drop-in replacement for Apache Kafka — no
ZooKeeper, no JVM, no Docker, no Bitnami.

The **Redpanda Console** (browser-based Web UI) is deployed on the
first broker so you can inspect topics, consumer groups and messages
right away.

## Topology

- Each host runs `redpanda` as a systemd service.
- Seed servers, advertised Kafka/RPC listeners and node IDs are derived
  from the inventory.
- 1 host = single-node dev, 3+ hosts = HA.
- First broker also runs `redpanda-console` on `console_port`.

## Key variables

| Variable | Default | Notes |
|---|---|---|
| `redpanda_channel` | `redpanda` | `redpanda` (stable) or `redpanda-unstable`. |
| `kafka_port` | `9092` | Client Kafka API. |
| `rpc_port` | `33145` | Internal RPC (seed servers use this). |
| `admin_port` | `9644` | Admin API (Console + rpk). |
| `schema_registry_port` | `8081` | Schema Registry HTTP. |
| `pandaproxy_port` | `8082` | REST proxy. |
| `console_enabled` | `true` | Deploy the Web UI on the first broker. |
| `console_port` | `8080` | Console HTTP port. |
| `developer_mode` | `false` | Enable for small VMs; disables kernel tuning. |
| `seastar_smp` / `seastar_memory` | `0` / `0` | Optional pinning — leave `0` for auto. |

## After the run

- Kafka bootstrap: `<first-broker>:9092`
- Web UI: `http://<first-broker>:8080`
- CLI: `rpk cluster info` on any broker.

## Uninstall

Use the **Uninstall / Cleanup Stack** blueprint and pick
**Redpanda**. It stops `redpanda` and `redpanda-console`, purges the
apt/dnf repo, removes packages, config, data and users.
