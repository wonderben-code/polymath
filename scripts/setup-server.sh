#!/usr/bin/env bash
# One-time Polymath setup on a fresh Ubuntu 24.04 LTS server; it installs
# Docker if needed. Point the domain (and www) at the server first. Run as root:
#
#   curl -fsSL https://raw.githubusercontent.com/wonderben-code/polymath/main/scripts/setup-server.sh | bash -s -- your.domain
#
# It makes every secret on the server itself, never prints one, and leaves
# every spending and publishing switch off. Running it again updates the code
# and restarts, keeping the existing settings.
set -euo pipefail

DOMAIN="${1:-}"
if [[ ! "$DOMAIN" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$ ]] || [[ "$DOMAIN" != *.* ]]; then
  echo "Usage: setup-server.sh your.domain   (for example askpolymath.com)" >&2
  exit 1
fi
[[ $EUID -eq 0 ]] || { echo "Please run as root." >&2; exit 1; }
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  echo "→ Installing Docker (official installer)"
  curl -fsSL https://get.docker.com | sh
fi
docker compose version >/dev/null 2>&1 || { echo "Docker Compose v2 could not be installed." >&2; exit 1; }

DIR=/opt/polymath
echo "→ Room to build: a 2 GB swap file"
if ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "→ Firewall: only SSH, HTTP and HTTPS"
if command -v ufw >/dev/null; then
  ufw allow 22/tcp >/dev/null && ufw allow 80/tcp >/dev/null && ufw allow 443/tcp >/dev/null
  ufw --force enable >/dev/null
fi

echo "→ Code"
if [[ -d "$DIR/.git" ]]; then
  git -C "$DIR" pull --ff-only
else
  git clone https://github.com/wonderben-code/polymath.git "$DIR"
fi
cd "$DIR"

ENV=deploy/production.env
if [[ ! -f "$ENV" ]]; then
  echo "→ Private settings, with fresh secrets made here"
  umask 077
  cp deploy/production.env.example "$ENV"
  set_value() { sed -i "s|^$1=.*|$1=$2|" "$ENV"; }
  set_value POLYMATH_DOMAIN "$DOMAIN"
  set_value PUBLIC_SITE_ORIGIN "https://$DOMAIN"
  set_value POSTGRES_PASSWORD "$(openssl rand -hex 24)"
  set_value OPERATOR_BOOTSTRAP_TOKEN "$(openssl rand -hex 32)"
  set_value ACCOUNTS_KEY "$(openssl rand -base64 32)"
  set_value ZENODO_CONNECTION_KEY "$(openssl rand -base64 32)"
  chmod 600 "$ENV"
else
  echo "→ Keeping the existing private settings"
fi

echo "→ Building and starting (about 10 minutes the first time)"
PROFILES=()
# Connect Zenodo needs its worker once it is switched on (scripts/connect-sign-in.sh).
grep -q '^VISITOR_ZENODO_ENABLED=true' "$ENV" && PROFILES+=(--profile zenodo)
# Once paid research is on, the research worker must be rebuilt with the same code.
grep -q '^PAID_META_RESEARCH_ENABLED=true' "$ENV" && PROFILES+=(--profile research)
docker compose -f deploy/compose.production.yaml --env-file "$ENV" "${PROFILES[@]}" up -d --build --wait

echo
if curl -fsS --max-time 20 "https://$DOMAIN/api/v1/readiness" >/dev/null 2>&1; then
  echo "✓ Polymath is live at https://$DOMAIN"
else
  echo "Started. The secure certificate can take a minute; then open https://$DOMAIN"
fi
echo "Your operator code (keep it private) is shown by:  grep OPERATOR_BOOTSTRAP_TOKEN $DIR/$ENV"
echo "Every spending and publishing switch is off until you turn it on."
echo "To switch on Sign in and Connect Zenodo for visitors:  bash $DIR/scripts/connect-sign-in.sh"
