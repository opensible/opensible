# syntax=docker/dockerfile:1.6

# ---------- build stage: Go worker ----------
# Go 1.22 images stopped at alpine3.21 (1.22 is out of support), so the builder runs a
# supported toolchain on the same Alpine as the runtime. go.mod still declares `go 1.22`,
# which is what gates language semantics.
FROM golang:1.25-alpine3.24 AS build
WORKDIR /src
COPY worker/go.mod worker/go.sum* ./
RUN go mod download
COPY worker/ .
# Static build for a slim runtime image.
ENV CGO_ENABLED=0
RUN go build -trimpath -ldflags="-s -w" -o /out/worker ./cmd/worker

# ---------- build stage: OpenTofu ----------
# Fetched in its own stage so curl's zip and unzip never land in the runtime image.
# OpenTofu ships static CGO_ENABLED=0 binaries, so the glibc/musl split does not apply.
FROM alpine:3.24 AS tofu
ARG OPENTOFU_VERSION=1.8.4
RUN set -eux; \
    apk add --no-cache curl unzip; \
    case "$(uname -m)" in \
      x86_64)  tofu_arch=amd64 ;; \
      aarch64) tofu_arch=arm64 ;; \
      *) echo "unsupported arch: $(uname -m)" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/tofu.zip \
      "https://github.com/opentofu/opentofu/releases/download/v${OPENTOFU_VERSION}/tofu_${OPENTOFU_VERSION}_linux_${tofu_arch}.zip"; \
    unzip -o /tmp/tofu.zip -d /usr/local/bin tofu; \
    chmod +x /usr/local/bin/tofu; \
    rm -f /tmp/tofu.zip

# ---------- runtime stage ----------
FROM python:3.14-alpine3.24

# ansible-core and passlib come from pip rather than the distro package manager, so they
# run on the image's own interpreter instead of pulling in a second, distro-owned Python.
ARG ANSIBLE_CORE_VERSION=2.21.3

WORKDIR /app

# Runtime tools used by playbooks and the SSH transport.
RUN apk add --no-cache \
      openssh-client sshpass bash git curl ca-certificates net-tools iputils

# passlib backs Ansible's password_hash filter, which user-supplied playbooks may use.
RUN pip install --no-cache-dir "ansible-core==${ANSIBLE_CORE_VERSION}" passlib

ENV ANSIBLE_COLLECTIONS_PATH=/usr/share/ansible/collections
RUN ansible-galaxy collection install \
        ansible.posix:==1.5.4 \
        community.general:==9.5.0 \
        community.docker:==3.13.0 \
        ansible.utils:==5.1.2 \
        -p /usr/share/ansible/collections \
    && rm -rf /root/.ansible/tmp

COPY --from=tofu /usr/local/bin/tofu /usr/local/bin/tofu

# The Go worker binary, installed under the name CMD invokes.
COPY --from=build /out/worker /usr/local/bin/opensible-worker

# Ship the IaC tree so tofu runs can refresh modules per-project.
COPY IaC /app/IaC

RUN mkdir -p /app/data

ENV WORKER_SERVER_URL=http://backend:5000
ENV DATA_DIR=/app/data

CMD ["opensible-worker", "--poll-interval", "5", "--max-concurrency", "1"]
