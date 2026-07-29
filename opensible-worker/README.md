# OpenSible Worker (Go)

A drop-in replacement for the Python `worker/` package, rewritten in Go for
faster startup, lower memory usage, and easier static distribution.

Feature parity with `worker/`:

- Self-registers with the backend (`/api/worker/register`) using
  `WORKER_REGISTRATION_SECRET` when set.
- Persists its token in `WORKER_TOKEN_FILE` (default: `<DATA_DIR>/worker.token`).
- Polls `/api/worker/claim` for claimed executions.
- Streams `ansible-playbook` and `tofu` output back via `/api/worker/executions/<id>/log`.
- Reports final status via `/api/worker/executions/<id>/finish`.
- Handles CANCELING → SIGTERM → SIGKILL grace period.
- Sends heartbeats + system info + auto-reloads log level.
- Materializes transferred playbook/inventory payloads, vault credentials
  and `connectionSecret` SSH keys/passwords into temp files.
- Resolves inventory files under both `IaC/ansible/inventories` and
  `repo/inventories` layouts.
- Injects `community.docker` Python-dep preflight tasks into generated
  playbooks that use Docker modules.
- Mitogen strategy plugin path detection.
- HOST_CHECK / HOST_FACTS result extraction from ansible output.
- OpenTofu (`init|plan|apply|destroy|validate|fmt|refresh`) execution
  for the Cloud Provisioning stacks, including credential injection into
  `credentials.auto.tfvars` and post-apply `tofu state pull` snapshots.
- Optional vault HTTP server (`/encrypt`, `/decrypt`, `/health`) with
  `X-Vault-Secret` auth, bound to loopback by default.
- Recovery pass for stuck RUNNING/CANCELING executions.

## Build

```bash
cd worker-go
go build -o opensible-worker ./cmd/worker
```

## Run

```bash
./opensible-worker \
  --server-url http://backend:5000 \
  --poll-interval 5 \
  --max-concurrency 1
```

Environment variables (identical to the Python worker):

| Name | Default | Purpose |
|---|---|---|
| `WORKER_SERVER_URL` | `http://localhost:5000` | Backend URL |
| `WORKER_TOKEN_FILE` | `<DATA_DIR>/worker.token` | Token cache |
| `WORKER_TOKEN` | — | Pre-issued token |
| `WORKER_REGISTRATION_SECRET` | — | Bootstrap header for `/register` |
| `WORKER_NAME` | hostname | Display name |
| `DATA_DIR` | `./data` | Persistent path |
| `LOG_LEVEL` | `DEBUG` | DEBUG/INFO/WARNING/ERROR/CRITICAL |
| `MAX_LOG_SIZE_MB` | `10` | Rotate-file size |
| `ANSIBLE_HOST_KEY_CHECKING` | `False` | Ansible env |
| `VAULT_SERVER_HOST` | `127.0.0.1` | Vault HTTP host |
| `VAULT_SERVER_PORT` | `9999` | Vault HTTP port |
| `VAULT_SERVER_SECRET` | — | `X-Vault-Secret` header |
| `OPENTOFU_VERSION` | `1.8.4` | Auto-install version |

## Container

The Dockerfile builds a static Go binary and installs `ansible-core`,
`sshpass`, `openssh-client`, `git`, `unzip`, and `tofu` (same runtime
dependencies as the Python worker).
