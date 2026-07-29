# Valkey + Sentinel

Valkey (open-source Redis fork) primary/replica cluster with Sentinel for automatic failover.

- Installed from distro packages (`valkey-server`, `valkey-sentinel`) — requires Ubuntu 24.04+ or EPEL.
- Managed by systemd. No Docker, no Bitnami.
- First node in the `nodes` list is the initial primary; the rest are configured with `replicaof`.
- Every node also runs `valkey-sentinel` monitoring the primary.

Use **3+ nodes** for real HA (Sentinel needs a majority to agree on failover).

Clients should connect via Sentinel (default port `26379`) using the `primary_name`
(default `mymaster`) rather than hard-coding the primary IP.

See `vars.example.yml` for a starting variable set. The playbook is rendered by
the `redis-sentinel` backend template with `flavor: valkey`.
