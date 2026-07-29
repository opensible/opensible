# PostgreSQL HA (Patroni + etcd)

A production-style 3-node PostgreSQL HA cluster driven by
[Patroni](https://patroni.readthedocs.io/) with an embedded
[etcd v3](https://etcd.io/) DCS on every node.

## What this blueprint installs

Per node (systemd):

- **etcd v3** — client `:2379`, peer `:2380`. All nodes form a single
  etcd cluster used exclusively as Patroni's DCS.
- **PostgreSQL** from the official PGDG apt/yum repositories.
  Major version is selectable (14 / 15 / 16 / 17).
- **Patroni** installed into an isolated Python venv at `/opt/patroni`
  from PyPI (`patroni[etcd3]`). Patroni owns postgres — the OS
  `postgresql` service is stopped and disabled so Patroni is the sole
  supervisor.
- Patroni **REST API** on `:8008` (`/health`, `/leader`, `/cluster`,
  `/patroni`).

## Topology

- The **first** node in the `nodes` list becomes the initial leader.
- All other nodes bootstrap as streaming replicas by cloning the leader
  through `pg_basebackup`.
- Use **3+ nodes** for real HA; both Patroni and etcd need a majority
  to promote a new leader on failure.

## Client access

Clients should route writes to the current leader. Discover it with:

```bash
curl -s http://<any-node>:8008/leader     # 200 = this node is the leader
curl -s http://<any-node>:8008/cluster    # JSON view of every member
patronictl -c /etc/patroni/patroni.yml list
```

Front the cluster with HAProxy or pgbouncer for a single write VIP —
both can query Patroni's REST API for health.

## Uninstall

Use the **Uninstall — PostgreSQL HA (Patroni + etcd)** blueprint to
cleanly stop patroni + etcd, kill leftover postgres processes, purge
PGDG packages, and remove `/opt/patroni`, `/etc/patroni`,
`/var/lib/patroni`, `/var/lib/etcd` and `/var/lib/postgresql`.
