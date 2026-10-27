#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export BUILDX_CONFIG="${BUILDX_CONFIG:-/tmp/polymath-buildx}"
build_args=()
# This cloud's package proxy uses an injected trust root. Mount it for build
# requests only; never disable TLS verification or copy host credentials.
if [[ -n "${CODEX_PROXY_CERT:-}" ]]; then
  build_args+=(--secret "id=proxy_ca,src=${CODEX_PROXY_CERT}")
fi
docker build "${build_args[@]}" -t polymath:local .
