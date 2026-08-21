#!/usr/bin/env bash
#
# OpenSible — release: build, push, SBOM, sign.
# Toggles:
#   SIGN=1        sign images with cosign          (default 1)
#   SBOM=1        generate + attach SPDX SBOMs     (default 1)
#   KEYLESS=0     1 = Sigstore keyless (browser),  0 = cosign key pair (default 0)
#   COSIGN_KEY    path to cosign private key       (default cosign.key)
#   ALSO_LATEST=1 also push/sign the :latest tag   (default 1)
#   BINFMT=1      register qemu binfmt handlers    (default 1)
#
# Prerequisites:
#   docker (with buildx), cosign >= 2.0, syft
#   docker login docker.io
#
set -euo pipefail

DEFAULT_VERSION="${1:-}"
NAMESPACE="${NAMESPACE:-ossopensible}"
REGISTRY="${REGISTRY:-docker.io}"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
SIGN="${SIGN:-1}"
SBOM="${SBOM:-1}"
KEYLESS="${KEYLESS:-0}"
COSIGN_KEY="${COSIGN_KEY:-cosign.key}"
ALSO_LATEST="${ALSO_LATEST:-1}"
BINFMT="${BINFMT:-1}"

SERVER_VERSION="${SERVER_VERSION:-$DEFAULT_VERSION}"
CONSOLE_VERSION="${CONSOLE_VERSION:-$DEFAULT_VERSION}"
WORKER_VERSION="${WORKER_VERSION:-$DEFAULT_VERSION}"

set --  # clear positional args so they don't leak into the IMAGES filter below
_found=0
for v in SERVER_VERSION CONSOLE_VERSION WORKER_VERSION; do
  [[ -n "${!v}" ]] && _found=1 && break
done
[[ "$_found" == "1" ]] || {
  echo "usage: $0 <version>                              # all three, same version" >&2
  echo "       SERVER_VERSION=X $0                      # one component only" >&2
  echo "       SERVER_VERSION=X CONSOLE_VERSION=Y WORKER_VERSION=Z $0" >&2
  exit 1
}

IMAGES=()
[[ -n "$SERVER_VERSION"  ]] && IMAGES+=( "server:backend/Dockerfile:$SERVER_VERSION" )
[[ -n "$CONSOLE_VERSION" ]] && IMAGES+=( "console:Dockerfile:$CONSOLE_VERSION" )
[[ -n "$WORKER_VERSION"  ]] && IMAGES+=( "worker:worker-go/Dockerfile:$WORKER_VERSION" )

need() { command -v "$1" >/dev/null 2>&1 || { echo "missing required tool: $1" >&2; exit 1; }; }
have() { command -v "$1" >/dev/null 2>&1; }
need docker

if [[ "$SIGN" == "1" ]] && ! have cosign; then
  echo "warning: cosign not found — skipping image signing (install cosign to enable)" >&2
  SIGN=0
fi
if [[ "$SBOM" == "1" ]] && ! have syft; then
  echo "warning: syft not found — skipping SBOM generation (install syft to enable)" >&2
  SBOM=0
fi

echo "==> Release to $REGISTRY/$NAMESPACE (platforms: $PLATFORMS)"
echo "    server=$SERVER_VERSION console=$CONSOLE_VERSION worker=$WORKER_VERSION"

docker buildx create --use --name opensible-builder >/dev/null 2>&1 \
  || docker buildx use opensible-builder

[[ "$BINFMT" == "1" ]] && docker run --privileged --rm tonistiigi/binfmt --install all >/dev/null

for entry in "${IMAGES[@]}"; do
  name="${entry%%:*}"
  rest="${entry#*:}"
  dockerfile="${rest%%:*}"
  version="${rest#*:}"
  repo="$REGISTRY/$NAMESPACE/opensible-$name"

  echo
  echo "==> [$name] build + push ($version)"
  tags=(-t "$repo:$version")
  [[ "$ALSO_LATEST" == "1" ]] && tags+=(-t "$repo:latest")

  docker buildx build \
    --platform "$PLATFORMS" \
    -f "$dockerfile" \
    "${tags[@]}" \
    --provenance=true \
    --push .

  ref=""
  if [[ "$SIGN" == "1" || "$SBOM" == "1" ]]; then
    digest="$(docker buildx imagetools inspect "$repo:$version" --format '{{.Manifest.Digest}}' 2>/dev/null || true)"
    if [[ -z "$digest" ]]; then
      digest="$(docker buildx imagetools inspect "$repo:$version" --raw 2>/dev/null \
                | sha256sum | awk '{print "sha256:"$1}')"
    fi
    if [[ -z "$digest" || "$digest" == "sha256:" ]]; then
      echo "warning: could not resolve digest for $repo:$version — skipping sign/SBOM" >&2
      SIGN_THIS=0; SBOM_THIS=0
    else
      ref="$repo@$digest"
      SIGN_THIS="$SIGN"; SBOM_THIS="$SBOM"
      echo "    digest: $digest"
    fi
  else
    SIGN_THIS=0; SBOM_THIS=0
  fi

  if [[ "$SBOM_THIS" == "1" ]]; then
    echo "==> [$name] SPDX SBOM"
    mkdir -p dist/sbom
    out="dist/sbom/opensible-$name-$version.spdx.json"
    syft "$repo:$version" -o spdx-json > "$out"
    echo "    wrote $out"
  fi

  if [[ "$SIGN_THIS" == "1" ]]; then
    echo "==> [$name] cosign sign"
    if [[ "$KEYLESS" == "1" ]]; then
      COSIGN_EXPERIMENTAL=1 cosign sign --yes "$ref"
      [[ "$SBOM_THIS" == "1" ]] && COSIGN_EXPERIMENTAL=1 cosign attest --yes \
        --type spdxjson --predicate "dist/sbom/opensible-$name-$version.spdx.json" "$ref"
    else
      [[ -f "$COSIGN_KEY" ]] || { echo "cosign key not found: $COSIGN_KEY (run: cosign generate-key-pair)" >&2; exit 1; }
      cosign sign --yes --key "$COSIGN_KEY" "$ref"
      [[ "$SBOM_THIS" == "1" ]] && cosign attest --yes --key "$COSIGN_KEY" \
        --type spdxjson --predicate "dist/sbom/opensible-$name-$version.spdx.json" "$ref"
    fi
  fi
done

echo
echo "==> Done. Published:"
for entry in "${IMAGES[@]}"; do
  echo "    $REGISTRY/$NAMESPACE/opensible-${entry%%:*}:${entry##*:}"
done
echo "    SBOMs in ./dist/sbom (attach these to the GitHub release)"
