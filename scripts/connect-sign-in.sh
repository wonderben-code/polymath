#!/usr/bin/env bash
# Turn on "Sign in" (ORCID, GitHub, Google) and "Connect Zenodo" for every
# visitor, after registering Polymath's free OAuth apps (docs/SIGN_IN_SETUP.md).
# Run on the server as root:
#
#   bash /opt/polymath/scripts/connect-sign-in.sh
#
# It asks for each app's client ID and secret (secrets are hidden as you type),
# saves them only in deploy/production.env (readable by root alone), switches the
# features on and restarts. It never prints a secret. Press Enter to skip any
# provider; anything already saved stays as it is.
set -euo pipefail

DIR="${POLYMATH_DIR:-/opt/polymath}"
ENV="$DIR/deploy/production.env"
[[ -f "$ENV" ]] || { echo "No settings at $ENV. Run setup-server.sh first." >&2; exit 1; }
[[ -w "$ENV" ]] || { echo "Please run as root." >&2; exit 1; }
umask 077
TTY=/dev/tty
{ : < /dev/tty; } 2>/dev/null || TTY=/dev/stdin

current() { grep -E "^$1=" "$ENV" | tail -n 1 | cut -d= -f2- || true; }
put() {
  KEY="$1" VAL="$2" awk 'BEGIN { k = ENVIRON["KEY"]; v = ENVIRON["VAL"]; done = 0 }
    index($0, k "=") == 1 { print k "=" v; done = 1; next } { print }
    END { if (!done) print k "=" v }' "$ENV" > "$ENV.next"
  cat "$ENV.next" > "$ENV" && rm -f "$ENV.next"
}
has() { [[ -n "$(current "$1_CLIENT_ID")" && -n "$(current "$1_CLIENT_SECRET")" ]]; }
valid_key() {
  local raw
  raw="$(printf '%s' "$1" | base64 -d 2>/dev/null | wc -c)" || return 1
  [[ "$raw" -eq 32 ]]
}

DOMAIN="$(current POLYMATH_DOMAIN)"
[[ -n "$DOMAIN" ]] || { echo "POLYMATH_DOMAIN is missing from $ENV." >&2; exit 1; }
SITE="https://$DOMAIN"

ask_app() { # PREFIX "Name"
  local prefix="$1" name="$2" state id secret
  has "$prefix" && state="saved" || state="not set up"
  echo
  read -r -p "$name ($state). Add or replace it now? [y/N] " answer < "$TTY"
  [[ "$answer" =~ ^[Yy] ]] || return 0
  while true; do
    read -r -p "  $name client ID: " id < "$TTY"
    [[ "$id" =~ ^[A-Za-z0-9_.-]{1,200}$ ]] && break
    echo "  That does not look like a client ID (letters, numbers, dots, dashes). Try again."
  done
  while true; do
    read -r -s -p "  $name client secret (hidden): " secret < "$TTY"; echo
    [[ "$secret" =~ ^[A-Za-z0-9._~+/=:-]{1,500}$ ]] && break
    echo "  That does not look like a client secret. Paste it again."
  done
  put "${prefix}_CLIENT_ID" "$id"
  put "${prefix}_CLIENT_SECRET" "$secret"
  unset secret
  echo "  ✓ $name saved"
}

echo "Sign in and Connect Zenodo for $SITE"
echo
echo "When each site asks for a callback or redirect address, paste:"
echo "  Sign in (ORCID, GitHub, Google):  $SITE/account/callback"
echo "  Connect Zenodo:                   $SITE/zenodo/callback"

ask_app ORCID "ORCID"
ask_app GITHUB "GitHub"
ask_app GOOGLE "Google"
ask_app ZENODO_PRODUCTION "Zenodo (zenodo.org)"
ask_app ZENODO_SANDBOX "Zenodo Sandbox (sandbox.zenodo.org, for test publications)"

put PUBLIC_SITE_ORIGIN "$SITE"
valid_key "$(current ACCOUNTS_KEY)" || put ACCOUNTS_KEY "$(openssl rand -base64 32)"
valid_key "$(current ZENODO_CONNECTION_KEY)" || put ZENODO_CONNECTION_KEY "$(openssl rand -base64 32)"

SIGN_IN=()
for pair in "ORCID:ORCID" "GITHUB:GitHub" "GOOGLE:Google"; do
  has "${pair%%:*}" && SIGN_IN+=("${pair#*:}")
done
ZENODO=()
for pair in "ZENODO_PRODUCTION:Zenodo" "ZENODO_SANDBOX:Zenodo Sandbox"; do
  has "${pair%%:*}" && ZENODO+=("${pair#*:}")
done
if ((${#SIGN_IN[@]})); then put ACCOUNTS_ENABLED true; else put ACCOUNTS_ENABLED false; fi
if ((${#ZENODO[@]})); then put VISITOR_ZENODO_ENABLED true; else put VISITOR_ZENODO_ENABLED false; fi
chmod 600 "$ENV"

if [[ "${POLYMATH_SKIP_RESTART:-}" != "1" ]]; then
  echo
  echo "→ Restarting with the new settings"
  PROFILES=()
  ((${#ZENODO[@]})) && PROFILES=(--profile zenodo)
  (cd "$DIR" && docker compose -f deploy/compose.production.yaml --env-file "$ENV" \
    "${PROFILES[@]}" up -d --wait)
fi

echo
if ((${#SIGN_IN[@]})); then
  echo "✓ Sign in is on: $(IFS=,; echo "${SIGN_IN[*]}" | sed 's/,/, /g')"
else
  echo "Sign in is off (no provider saved). Visitors carry on as guests."
fi
if ((${#ZENODO[@]})); then
  echo "✓ Connect Zenodo is on: $(IFS=,; echo "${ZENODO[*]}" | sed 's/,/, /g')"
else
  echo "Connect Zenodo is off (no Zenodo app saved)."
fi
echo "Open $SITE and look for Sign in at the top right."
