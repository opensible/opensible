#!/usr/bin/env bash
# Build the OpenSible images for local development.
#
# Usage:
#   ./scripts/build-images-dev.sh                 # all three
#   ./scripts/build-images-dev.sh worker console  # a subset
#   TAG=wip PREFIX=myorg ./scripts/build-images-dev.sh
set -euo pipefail

TAG="${TAG:-dev}"
PREFIX="${PREFIX:-opensible}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

build() { # <name> <dockerfile> <context>
  echo "==> $PREFIX-$1:$TAG"
  docker build -f "$ROOT/$2" -t "$PREFIX-$1:$TAG" "$ROOT/$3"
}

components=("$@")
[ ${#components[@]} -eq 0 ] && components=(server worker console)

for c in "${components[@]}"; do
  case "$c" in
    # All three COPY from IaC/, so the build context is the repo root for each.
    server)  build server  server/Dockerfile  . ;;
    worker)  build worker  worker/Dockerfile  . ;;
    console) build console console/Dockerfile . ;;
    *) echo "unknown component: $c (expected server, worker or console)" >&2; exit 1 ;;
  esac
done
