# syntax=docker/dockerfile:1.6

# ---------- build stage ----------
FROM golang:1.22-bookworm AS build
WORKDIR /src
COPY worker/go.mod worker/go.sum* ./
RUN go mod download || true
COPY worker/ .
# Static build for a slim runtime image.
ENV CGO_ENABLED=0
RUN go build -trimpath -ldflags="-s -w" -o /out/worker ./cmd/worker
    
    # ---------- runtime stage ----------
FROM python:3.14-slim
WORKDIR /app
    
    # Runtime tools (mirrors the Python worker Dockerfile).
    RUN apt-get update && apt-get install -y --no-install-recommends \
        openssh-client sshpass bash net-tools iputils-ping python3-passlib \
        curl ca-certificates unzip git \
        && rm -rf /var/lib/apt/lists/*
    
    ARG OPENTOFU_VERSION=1.8.4
    RUN set -eux; \
        arch="$(uname -m)"; \
        case "$arch" in \
          x86_64)  tofu_arch=amd64 ;; \
          aarch64) tofu_arch=arm64 ;; \
          *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
        esac; \
        curl -fsSL -o /tmp/tofu.zip \
          "https://github.com/opentofu/opentofu/releases/download/v${OPENTOFU_VERSION}/tofu_${OPENTOFU_VERSION}_linux_${tofu_arch}.zip"; \
        unzip -o /tmp/tofu.zip -d /usr/local/bin tofu; \
        chmod +x /usr/local/bin/tofu; \
        rm -f /tmp/tofu.zip; \
        tofu version
    
    RUN apt-get update && apt-get install -y --no-install-recommends \
        ansible-core \
        && rm -rf /var/lib/apt/lists/*
    
    RUN ansible-galaxy collection install \
            ansible.posix:==1.5.4 \
            community.general:==9.5.0 \
            community.docker:==3.13.0 \
            ansible.utils:==5.1.2 \
        -p /usr/share/ansible/collections
    ENV ANSIBLE_COLLECTIONS_PATH=/usr/share/ansible/collections
    
    # Ship the IaC tree so tofu runs can refresh modules per-project.
    COPY IaC /app/IaC
    
    # The Go worker binary.
    COPY --from=build /out/worker /usr/local/bin/worker
    
    RUN mkdir -p /app/data
    ENV WORKER_SERVER_URL=http://backend:5000
    ENV DATA_DIR=/app/data
    
    CMD ["opensible-worker", "--poll-interval", "5", "--max-concurrency", "1"]
    
    