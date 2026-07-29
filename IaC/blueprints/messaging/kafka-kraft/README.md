# Apache Kafka (KRaft Cluster)

Production-grade Apache Kafka cluster in **KRaft mode** (no ZooKeeper),
installed from the **official Apache tarball** and managed by
**systemd**. No Docker, no Bitnami — just OpenJDK + Kafka.

## Why systemd instead of Bitnami / Docker?

- **Vendor-neutral.** Pure Apache release, no Bitnami layer and no
  surprises after the Broadcom acquisition of Bitnami.
- **Predictable upgrades.** Pin the exact Apache version; no image
  retagging.
- **Lower footprint.** Direct JVM heap control, no container/JVM
  tuning quirks.
- **Fits the OpenSible stack.** Matches how k3s / kubeadm / docker are
  deployed via Ansible + systemd units.

## Topology

Each target host runs as a **combined controller + broker**. Cluster
scale is driven entirely by inventory — one host = single-node dev, 3+
hosts = HA. Node IDs, controller quorum voters and advertised
listeners are computed from `ansible_play_hosts` at render time.

## Key variables

| Variable | Default | Notes |
|---|---|---|
| `kafka_version` | `3.8.1` | Any published Apache release. |
| `kafka_scala_version` | `2.13` | Scala build of the tarball. |
| `cluster_id` | `opensible-kafka` | Free-form; converted to a stable UUID. |
| `client_port` / `controller_port` | `9092` / `9093` | PLAINTEXT client + KRaft controller quorum. |
| `replication_factor` / `min_insync_replicas` | `3` / `2` | Auto-capped to broker count. |
| `install_dir` | `/opt/kafka` | Symlink to versioned dir. |
| `data_dir` | `/var/lib/kafka/data` | Persistent log dirs. |

## What the playbook does

1. Installs OpenJDK 17 (Debian/Ubuntu or RHEL family).
2. Creates a `kafka` system user + directories.
3. Downloads the Apache tarball and symlinks it to `/opt/kafka`.
4. Renders `config/kraft/server.properties` from the inventory.
5. Formats KRaft storage once (`kafka-storage.sh format --ignore-formatted`).
6. Installs a `kafka.service` systemd unit and starts it.
7. Waits for the broker to accept connections and runs a smoke test.

## Post-install smoke test

```
/opt/kafka/bin/kafka-topics.sh --bootstrap-server <host>:9092 --list
```
