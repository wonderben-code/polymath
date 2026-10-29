#!/usr/bin/env bash
# Write a private, checksummed PostgreSQL dump of the production stack.
# A dump is not a safe restore by itself: restored copies must pass
# `python -m polymath.restore_quarantine` before any process uses them.
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
mkdir -p backups
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="backups/polymath-${stamp}.dump"
docker compose -f deploy/compose.production.yaml --env-file deploy/production.env \
  exec -T db pg_dump --username=polymath --dbname=polymath --format=custom --no-owner \
  > "${target}.partial"
mv "${target}.partial" "${target}"
sha256sum "${target}" > "${target}.sha256"
echo "Backup written: ${target} ($(du -h "${target}" | cut -f1)). Copy it off the host."
