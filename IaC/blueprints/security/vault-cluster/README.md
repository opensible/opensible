# HashiCorp Vault HA (Docker cluster)

Deploys a production **HashiCorp Vault** HA cluster across N nodes using the
official `hashicorp/vault` Docker image with **integrated Raft storage**.

This blueprint is the Docker-container equivalent of the OpenBao (systemd)
blueprint: same cluster topology, same auto-init + persistent auto-unseal
flow, but every node runs Vault inside a Docker container supervised by a
dedicated systemd unit (`vault-docker.service`).

## What it does

- Ensures Docker Engine is installed and healthy on every node (self-heals a
  stale Docker daemon and repairs a missing `docker0` bridge).
- Pulls the pinned `hashicorp/vault:<tag>` image on every node.
- Renders `/etc/vault/vault.hcl` with an integrated Raft `storage "raft"`
  block, cross-node `retry_join`, listener, `api_addr` and `cluster_addr`.
- Writes `vault-docker.service` and starts one container per node with:
  - `-v /etc/vault/vault.hcl:/etc/vault.d/vault.hcl:ro`
  - `-v <data_dir>:/vault/file` (persistent Raft storage)
  - `-p 8200:8200 -p 8201:8201`
  - `SKIP_SETCAP=true` when `disable_mlock=true`, otherwise
    `--cap-add=IPC_LOCK`.
  - `vault server -config=/etc/vault.d/vault.hcl`, intentionally bypassing
    the image entrypoint's automatic `/vault/config` injection so the listener
    block is loaded only once.
- Stops any legacy host-level `vault` / `openbao` services and frees Vault
  ports before starting the Docker unit, so the Docker container owns the API
  and Raft listeners.
- Waits for the API port, dumps rich diagnostics if it does not open.
- (Optional) On first run: `vault operator init`, shares the unseal keys
  across the play, unseals every node, and installs
  `vault-autounseal.service` + `vault-autounseal.timer` so the cluster
  re-unseals automatically after every reboot/restart.
- Writes a full bootstrap bundle on every node to
  `/opt/vault/cluster-info/cluster-info.txt` (root token, unseal keys,
  leader address). **Store securely and delete after distribution.**

## Recommended topology

Use **3 or 5 nodes** for real HA. Fewer than 3 will not survive the loss of
a single node without an unavailable quorum.

## Ports

| Port  | Role                   |
| ----- | ---------------------- |
| 8200  | Vault HTTP API + UI    |
| 8201  | Raft cluster traffic   |

## Uninstall

Use the "Uninstall — HashiCorp Vault (Docker cluster)" blueprint to stop
`vault-docker.service` + the auto-unseal units, remove the container and
image, and purge `/etc/vault`, `/opt/vault`, and the Raft data directory.
