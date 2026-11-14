#!/usr/bin/env bash
# Connect your own Meta key to your own Polymath in one go (docs/RUN_YOUR_OWN.md).
# Run on your server as root, after setup-server.sh:
#
#   bash /opt/polymath/scripts/connect-meta.sh          set up: key, limits, one check call, research on
#   bash /opt/polymath/scripts/connect-meta.sh renew    keep research on for two more weeks (no model call)
#   bash /opt/polymath/scripts/connect-meta.sh status   what is on, and until when
#   bash /opt/polymath/scripts/connect-meta.sh stop     stop all research spending now
#
# It asks a few plain questions, saves your key only in deploy/production.env
# (readable by root alone) and never prints it. It then uses the operator desk's
# own reviewed steps (scripts/operator-desk.sh): a free key check, your limits as
# a readiness proposal, ONE verification call to Meta (a few cents), and research
# switched on for your site within your limits. Every server-side check still
# applies: nothing here can raise a cap the server would refuse.
set -euo pipefail

DIR="${POLYMATH_DIR:-/opt/polymath}"
ENV="${POLYMATH_ENV_FILE:-$DIR/deploy/production.env}"
STATE="${POLYMATH_META_STATE:-/root/.polymath-meta}"
export POLYMATH_DESK_STATE="$STATE/desk" POLYMATH_ENV_FILE="$ENV"
DESK=(bash "$DIR/scripts/operator-desk.sh")
CRON="${POLYMATH_CRON_FILE:-/etc/cron.d/polymath-renew}"
WINDOW_DAYS=14        # the longest public funding window the server allows
REVIEW_DAYS=90        # how long your reviewed prices and limits stay valid
umask 077

die() { echo "✗ $*" >&2; exit 1; }
say() { echo "→ $*"; }
[[ -f "$ENV" ]] || die "No settings at $ENV. Run setup-server.sh first."
[[ -w "$ENV" ]] || die "Please run as root."
TTY=/dev/tty
{ : < /dev/tty; } 2>/dev/null || TTY=/dev/stdin
mkdir -p "$STATE"

current() { grep -E "^$1=" "$ENV" | tail -n 1 | cut -d= -f2- || true; }
put() {
  KEY="$1" VAL="$2" awk 'BEGIN { k = ENVIRON["KEY"]; v = ENVIRON["VAL"]; done = 0 }
    index($0, k "=") == 1 { print k "=" v; done = 1; next } { print }
    END { if (!done) print k "=" v }' "$ENV" > "$ENV.next"
  cat "$ENV.next" > "$ENV" && rm -f "$ENV.next"
  chmod 600 "$ENV"
}
when() { date -u -d "$1" +%Y-%m-%dT%H:%M:%SZ; }
usd_to_micro() { awk -v v="$1" 'BEGIN { printf "%d", v * 1000000 + 0.5 }'; }
micro_to_usd() { awk -v v="$1" 'BEGIN { printf "%.2f", v / 1000000 }'; }

restart() {
  local profiles=()
  [[ "$(current VISITOR_ZENODO_ENABLED)" == true ]] && profiles+=(--profile zenodo)
  [[ "$(current PAID_META_RESEARCH_ENABLED)" == true ]] && profiles+=(--profile research)
  say "Restarting Polymath with the new settings (about a minute)"
  (cd "$DIR" && docker compose -f deploy/compose.production.yaml --env-file "$ENV" \
    "${profiles[@]}" up -d --wait) > "$STATE/last-restart.log" 2>&1 \
    || { tail -n 20 "$STATE/last-restart.log" >&2; die "Polymath did not restart cleanly (log above)."; }
}

ask() { # ask "Question" default -> REPLY
  local q="$1" d="${2:-}"
  if [[ -n "$d" ]]; then read -r -p "$q [$d] " REPLY < "$TTY"; REPLY="${REPLY:-$d}"
  else read -r -p "$q " REPLY < "$TTY"; fi
}
ask_usd() { # ask_usd "Question" default min max -> REPLY (dollars)
  while true; do
    ask "$1" "$2"
    if [[ "$REPLY" =~ ^[0-9]+([.][0-9]{1,2})?$ ]] && awk -v v="$REPLY" -v lo="$3" -v hi="$4" 'BEGIN { exit !(v >= lo && v <= hi) }'; then
      return 0
    fi
    echo "  Please enter an amount in US dollars between $3 and $4 (for example 25 or 12.50)."
  done
}
ask_int() { # ask_int "Question" default min max -> REPLY
  while true; do
    ask "$1" "$2"
    [[ "$REPLY" =~ ^[0-9]+$ ]] && ((REPLY >= $3 && REPLY <= $4)) && return 0
    echo "  Please enter a whole number between $3 and $4."
  done
}
confirm() { ask "$1" "${2:-y}"; [[ "$REPLY" =~ ^[Yy] ]]; }

need_tools() {
  command -v curl >/dev/null || die "curl is missing."
  command -v python3 >/dev/null || die "python3 is missing."
  if ! command -v jq >/dev/null; then
    say "Installing jq (a small JSON tool)"
    apt-get install -y jq >/dev/null 2>&1 || (apt-get update >/dev/null 2>&1 && apt-get install -y jq >/dev/null 2>&1) \
      || die "Could not install jq. Run: apt-get install -y jq"
  fi
  [[ -n "$(current OPERATOR_BOOTSTRAP_TOKEN)" ]] || die "OPERATOR_BOOTSTRAP_TOKEN is missing. Run setup-server.sh first."
  [[ -n "$(current POLYMATH_DOMAIN)" ]] || die "POLYMATH_DOMAIN is missing. Run setup-server.sh first."
}

public_until() { # the current public funding window's end, or empty
  "${DESK[@]}" get budget/research/public/current 2>/dev/null | jq -r '.activation.manifest.valid_until // empty' 2>/dev/null || true
}

activate_public() { # activate_public -> prints the new window's end
  # shellcheck disable=SC1091
  source "$STATE/saved.env"
  local until review_end
  review_end=$(jq -r '.valid_until' "$STATE/review.json")
  until=$(when "+$WINDOW_DAYS days -10 minutes")
  if [[ "$(date -u -d "$review_end" +%s)" -le "$(date -u -d "+1 hour" +%s)" ]]; then
    die "Your reviewed prices and limits expired on $review_end. Run this helper again without 'renew' to review them (one more check call, a few cents)."
  fi
  [[ "$(date -u -d "$until" +%s)" -gt "$(date -u -d "$review_end" +%s)" ]] && until="$review_end"
  local out
  if ! out=$("${DESK[@]}" public "$READINESS_ID" "$RECEIPT_ID" "$STATE/review.json" "$STATE/attestation.json" "$STATE/limits.json" "$until" 2>&1); then
    printf '%s\n' "$out" | tail -n 4 >&2
    if printf '%s' "$out" | grep -q meta_research_prior_unresolved; then
      die "A research call is still running. Try again in a few minutes."
    fi
    die "The server did not accept the activation (details above)."
  fi
  printf '%s' "$out" | grep -q '^== activated' || die "The server did not confirm the activation."
  echo "$until"
}

case "${1:-setup}" in
  status)
    need_tools
    echo "Site:      https://$(current POLYMATH_DOMAIN)"
    echo "Model:     $(current META_MODEL)"
    echo "Research:  paid=$(current PAID_META_RESEARCH_ENABLED) public=$(current PUBLIC_META_RESEARCH_ENABLED) concept-art=$(current PAID_META_IMAGE_ENABLED)"
    u=$(public_until); echo "Open until: ${u:-not open}"
    [[ -f "$CRON" ]] && echo "Automatic renewal: on (daily check)" || echo "Automatic renewal: off"
    acct=$(current NATIVE_BUDGET_ACCOUNT_ID)
    if [[ -n "$acct" ]]; then
      "${DESK[@]}" account "$acct" 2>/dev/null | jq -r '"Counted so far: USD \(((.usage.committed_microdollars | tonumber) / 10000 | round) / 100) of USD \(.hard_cap_microdollars / 1000000) (today: USD \(((.usage.daily_committed_microdollars | tonumber) / 10000 | round) / 100) of \(.daily_cap_microdollars / 1000000))"' 2>/dev/null || true
    fi
    exit 0 ;;

  stop)
    need_tools
    id=$("${DESK[@]}" get budget/research/public/current 2>/dev/null | jq -r '.activation.id // empty' || true)
    [[ -n "$id" ]] && "${DESK[@]}" revoke-public "$id" >/dev/null && say "Public funding window closed"
    put PUBLIC_META_RESEARCH_ENABLED false
    put PAID_META_IMAGE_ENABLED false
    rm -f "$CRON"
    restart
    echo "✓ Research spending is off. Turn it back on with: bash $DIR/scripts/connect-meta.sh renew"
    exit 0 ;;

  renew|renew-if-due)
    need_tools
    [[ -f "$STATE/saved.env" ]] || die "Nothing to renew yet. Run: bash $DIR/scripts/connect-meta.sh"
    if [[ "$1" == renew-if-due ]]; then
      u=$(public_until)
      if [[ -n "$u" ]] && [[ "$(date -u -d "$u" +%s)" -gt "$(date -u -d '+3 days' +%s)" ]]; then
        echo "$(date -u +%FT%TZ) open until $u; nothing to do"; exit 0
      fi
    fi
    if [[ "$(current PUBLIC_META_RESEARCH_ENABLED)" != true ]]; then
      put PUBLIC_META_RESEARCH_ENABLED true
      ART=$(grep -E '^ART=' "$STATE/saved.env" | cut -d= -f2- || true)
      [[ "$ART" == true ]] && put PAID_META_IMAGE_ENABLED true
      restart
    fi
    until=$(activate_public)
    echo "✓ $(date -u +%FT%TZ) research stays on at https://$(current POLYMATH_DOMAIN) until $until"
    exit 0 ;;

  setup) ;;
  *) sed -n '2,8p' "$0"; exit 2 ;;
esac

need_tools
DOMAIN=$(current POLYMATH_DOMAIN)
cat <<EOF

Connect your Meta key to your Polymath at https://$DOMAIN

You'll need, from Meta's developer dashboard (dev.meta.ai):
  • your Model API key
  • your billing account ID and the spending limit you set (Billing page)
This takes about five minutes. One check call to Meta costs a few cents.

EOF

# 1. The key
if [[ -n "$(current MODEL_API_KEY)" ]] && ! confirm "A Meta key is already saved. Keep it?" y; then KEY=""; else KEY="$(current MODEL_API_KEY)"; fi
while [[ -z "$KEY" ]]; do
  read -r -s -p "Paste your Meta Model API key (hidden): " KEY < "$TTY"; echo
  [[ "$KEY" =~ ^[A-Za-z0-9._~+/=:-]{16,500}$ ]] || { echo "  That does not look like an API key. Paste it again."; KEY=""; }
done

# 2. The model and its prices
ask "Which Meta model?" "$(current META_MODEL | grep . || echo muse-spark-1.3)"; MODEL="$REPLY"
[[ "$MODEL" =~ ^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}$ ]] || die "That is not a model name."
case "$MODEL" in
  muse-spark-1.3) IN_PRICE=1.25; OUT_PRICE=4.25 ;;
  *) IN_PRICE=""; OUT_PRICE="" ;;
esac
echo
echo "Prices for $MODEL: check them on https://dev.meta.ai/docs/pricing-rate-limits"
if [[ -n "$IN_PRICE" ]] && confirm "  Input USD $IN_PRICE and output USD $OUT_PRICE per million tokens. Is that what the page shows?" y; then :
else
  ask_usd "  Input price, USD per million tokens:" "${IN_PRICE:-}" 0.01 1000; IN_PRICE="$REPLY"
  ask_usd "  Output price, USD per million tokens:" "${OUT_PRICE:-}" 0.01 1000; OUT_PRICE="$REPLY"
fi

# 3. The Meta account
echo
echo "From the Billing page on dev.meta.ai:"
while true; do
  ask "  Your billing account ID:" "$(jq -r '.provider_billing_account_id // empty' "$STATE/attestation.json" 2>/dev/null || true)"; BILLING="$REPLY"
  [[ "$BILLING" =~ ^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$ ]] && break
  echo "  Use the ID exactly as shown (letters, numbers, dots, dashes, colons)."
done
ask_usd "  The spending limit you set on your Meta account, USD:" "" 2 1000000; META_LIMIT="$REPLY"
ask_usd "  Credit or allowance still available on that account now, USD:" "$META_LIMIT" 2 "$META_LIMIT"; META_AVAILABLE="$REPLY"

# 4. Polymath's own limits
echo
echo "Polymath's own limits (it reserves the most a call could cost before sending it):"
MAX_TOTAL=$(awk -v a="$META_LIMIT" 'BEGIN { print (a < 150 ? a : 150) }')
DEFAULT_TOTAL=$(awk -v a="$MAX_TOTAL" 'BEGIN { print (a < 50 ? a : 50) }')
ask_usd "  Most Polymath may ever spend on this setup, USD (up to $MAX_TOTAL):" "$DEFAULT_TOTAL" 2 "$MAX_TOTAL"; TOTAL="$REPLY"
ask_usd "  Most it may spend in one day, USD:" "$(awk -v t="$TOTAL" 'BEGIN { print (t < 20 ? t : 20) }')" 2 "$TOTAL"; DAILY="$REPLY"
RUN=$(awk -v d="$DAILY" 'BEGIN { print (d < 5 ? d : 5) }')
# The most one call can cost at these prices (the ledger reserves this before each call).
PER_CALL=$(awk -v i="$IN_PRICE" -v o="$OUT_PRICE" 'BEGIN { printf "%.2f", (1048576 * i + 16384 * o) / 1000000 + 0.005 }')
awk -v r="$RUN" -v a="$META_AVAILABLE" -v c="$PER_CALL" 'BEGIN { exit !(r >= c && a >= c) }' \
  || die "At these prices one call can cost up to USD $PER_CALL. Choose a daily limit and Meta credit of at least that."

echo
echo "Research on your copy runs for anyone who opens https://$DOMAIN, within these"
echo "limits. Keep the address to yourself, or give each visitor a smaller allowance."
ask_usd "  Allowance per visitor per day, USD:" "$DAILY" "$PER_CALL" "$DAILY"; VISITOR_DAILY="$REPLY"
ask_int "  Investigations per visitor per day:" 30 1 40; VISITOR_JOBS="$REPLY"
confirm "Paint concept art in Studio with Meta Muse Image (each picture is a cent or two)?" y && ART=true || ART=false
confirm "Keep research on automatically (a daily check renews it every two weeks, no model call)?" y && AUTO=true || AUTO=false

cat <<EOF

Ready:
  model $MODEL at USD $IN_PRICE / $OUT_PRICE per million tokens
  Polymath limits: USD $TOTAL in total, USD $DAILY a day; each visitor USD $VISITOR_DAILY a day
  concept art: $ART · automatic renewal: $AUTO
Next: a free key check, then ONE verification call to Meta (a few cents;
at most USD $PER_CALL is reserved for it), then research switches on.
EOF
confirm "Go ahead?" y || die "Nothing changed."

# 5. Settings (research stays off until the check call has passed)
FINISHED=0
on_exit() {
  [[ "$FINISHED" == 1 ]] && return 0
  echo "Research stays off until this helper finishes. Fix the problem above and run it again." >&2
  put PAID_META_PROBE_ENABLED false
  restart >/dev/null 2>&1 || true
}
trap on_exit EXIT
ACCOUNT="polymath-$(date -u +%Y%m%d%H%M)"
put MODEL_API_KEY "$KEY"; unset KEY
put META_MODEL "$MODEL"
[[ -n "$(current OPERATOR_ACTOR_ID)" ]] || put OPERATOR_ACTOR_ID owner
[[ -n "$(current OPERATOR_DISPLAY_NAME)" ]] || put OPERATOR_DISPLAY_NAME Owner
ACTOR=$(current OPERATOR_ACTOR_ID)
put BUDGET_READINESS_ENABLED true
put BUDGET_ADMIN_ACTOR_IDS "[\"$ACTOR\"]"
put BUDGET_MAX_HARD_CAP_MICRODOLLARS "$(usd_to_micro "$TOTAL")"
put NATIVE_BUDGET_ACCOUNT_ID "$ACCOUNT"
put PAID_META_PROBE_ENABLED true
put PAID_META_RESEARCH_ENABLED false
put PUBLIC_META_RESEARCH_ENABLED false
put PAID_META_IMAGE_ENABLED false
put PUBLIC_MAX_DELEGATION_SECONDS $((WINDOW_DAYS * 86400))
put PUBLIC_MAX_JOBS_PER_SESSION_DAY "$VISITOR_JOBS"
put PUBLIC_MAX_JOBS_PER_DAY 300
grep -qE '^META_IMAGE_MODEL=' "$ENV" || put META_IMAGE_MODEL muse-image-1.0
restart

say "Checking your key with Meta (free, no model call)"
python3 "$DIR/scripts/meta-preflight.py" --env-file "$ENV" --check-key > "$STATE/preflight.txt" 2>&1 || true
if ! grep -q " 0 failed" "$STATE/preflight.txt"; then
  grep -iE "fail|error|summary" "$STATE/preflight.txt" | head -n 8 >&2
  die "Meta did not accept the key or the model (above). Nothing was spent."
fi

say "Recording Meta's current documentation pages as your evidence"
hash_of() { curl -fsSL --max-time 30 "$1" | sha256sum | cut -d' ' -f1; }
H_DOCS=$(hash_of https://dev.meta.ai/docs) && H_MODELS=$(hash_of https://dev.meta.ai/docs/models) \
  && H_PRICES=$(hash_of https://dev.meta.ai/docs/pricing-rate-limits) && H_REASON=$(hash_of https://dev.meta.ai/docs/reasoning) \
  || die "Could not read Meta's documentation pages. Check the server can reach dev.meta.ai, then run this again."

NOW=$(when "-2 minutes"); UNTIL=$(when "+$REVIEW_DAYS days")
STATEMENT="Polymath owner attestation via connect-meta.sh at $NOW: Meta billing account $BILLING, model $MODEL, spending limit USD $META_LIMIT, available USD $META_AVAILABLE."
printf '%s\n' "$STATEMENT" > "$STATE/attestation-statement.txt"
H_STATEMENT=$(sha256sum "$STATE/attestation-statement.txt" | cut -d' ' -f1)

jq -n --arg model "$MODEL" --arg now "$NOW" --arg until "$UNTIL" --arg acct "$ACCOUNT" \
  --arg hd "$H_DOCS" --arg hm "$H_MODELS" --arg hp "$H_PRICES" --arg hr "$H_REASON" \
  --argjson pin "$(usd_to_micro "$IN_PRICE")" --argjson pout "$(usd_to_micro "$OUT_PRICE")" \
  --argjson total "$(usd_to_micro "$TOTAL")" --argjson daily "$(usd_to_micro "$DAILY")" --argjson run "$(usd_to_micro "$RUN")" '
  {protocol: "polymath-meta-readiness-v1", model_id: $model, endpoint: "https://api.meta.ai/v1/chat/completions",
   currency: "USD", usage_contract: "prompt_completion_inclusive_v1", observed_at: $now, valid_until: $until,
   billable_bounds: {input_tokens_absolute: 1048576, output_tokens_absolute: 16384,
     evidence: [{url: "https://dev.meta.ai/docs/models", content_sha256: $hm}, {url: "https://dev.meta.ai/docs/reasoning", content_sha256: $hr}]},
   pricing: {input_microdollars_per_million: $pin, output_microdollars_per_million: $pout,
     evidence: [{url: "https://dev.meta.ai/docs/pricing-rate-limits", content_sha256: $hp}]},
   usage_evidence: [{url: "https://dev.meta.ai/docs", content_sha256: $hd}, {url: "https://dev.meta.ai/docs/reasoning", content_sha256: $hr}],
   application_limits: {max_output_tokens: 16384, max_context_characters: 200000, max_model_calls: 48, request_timeout_seconds: 60},
   account: {account_id: $acct, hard_cap_microdollars: $total, daily_cap_microdollars: $daily, run_cap_microdollars: $run, max_in_flight: 4},
   native_status: "not_run", execution_available: false}' > "$STATE/proposal.json"

jq -n --arg billing "$BILLING" --arg model "$MODEL" --arg now "$NOW" --arg until "$UNTIL" --arg hs "$H_STATEMENT" \
  --argjson avail "$(usd_to_micro "$META_AVAILABLE")" --argjson limit "$(usd_to_micro "$META_LIMIT")" '
  {provider_billing_account_id: $billing, model_id: $model, currency: "USD", usage_contract: "prompt_completion_inclusive_v1",
   observed_at: $now, valid_until: $until, available_credit_microdollars: $avail,
   remaining_provider_allowance_microdollars: $avail, provider_hard_cap_microdollars: $limit,
   evidence: [{url: "https://dev.meta.ai/billing", content_sha256: $hs}]}' > "$STATE/attestation.json"

jq -n --arg now "$NOW" --arg until "$UNTIL" --arg hp "$H_PRICES" --arg hm "$H_MODELS" '
  {observed_at: $now, valid_until: $until,
   evidence: [{url: "https://dev.meta.ai/docs/pricing-rate-limits", content_sha256: $hp}, {url: "https://dev.meta.ai/docs/models", content_sha256: $hm}],
   funding_cap_confirmed: true, pricing_bounds_confirmed: true, model_wire_usage_confirmed: true}' > "$STATE/review.json"

jq -n --argjson total "$(usd_to_micro "$TOTAL")" --argjson daily "$(usd_to_micro "$DAILY")" \
  --argjson vday "$(usd_to_micro "$VISITOR_DAILY")" --argjson jobs "$VISITOR_JOBS" '
  {public_total_cap_microdollars: $total, public_daily_cap_microdollars: $daily,
   session_total_cap_microdollars: $total, session_daily_cap_microdollars: $vday,
   max_jobs_per_session_day: $jobs, max_jobs_per_day: 300}' > "$STATE/limits.json"

say "Saving your reviewed limits (free)"
OUT=$("${DESK[@]}" proposal "$STATE/proposal.json" 2>&1) || { printf '%s\n' "$OUT" | tail -n 4 >&2; die "The server did not accept the limits (details above). Nothing was spent."; }
READINESS_ID=$(printf '%s\n' "$OUT" | awk '/^== saved/{f=1; next} f' | jq -r '.id // empty')
[[ -n "$READINESS_ID" ]] || die "The server did not return the saved proposal."

say "Arming one verification call"
OUT=$("${DESK[@]}" probe "$READINESS_ID" "$STATE/attestation.json" 2>&1) || { printf '%s\n' "$OUT" | tail -n 4 >&2; die "The server did not accept the check call (details above). Nothing was spent."; }
JOB=$(cat "$POLYMATH_DESK_STATE/last-probe-job")
say "Making the verification call to Meta (a few cents)"
"${DESK[@]}" probe-run > "$STATE/probe-run.log" 2>&1 || true
put PAID_META_PROBE_ENABLED false
RESULT=$("${DESK[@]}" get "budget/verification/jobs/$JOB")
RECEIPT_ID=$(printf '%s' "$RESULT" | jq -r '.receipt.id // empty')
NATIVE=$(printf '%s' "$RESULT" | jq -r '.native_status // .status // empty')
if [[ -z "$RECEIPT_ID" || "$NATIVE" != passed ]]; then
  printf '%s' "$RESULT" | jq '{status, native_status, assessment}' >&2 || true
  die "The verification call did not pass (above). Research stays off."
fi
printf 'READINESS_ID=%s\nRECEIPT_ID=%s\nART=%s\n' "$READINESS_ID" "$RECEIPT_ID" "$ART" > "$STATE/saved.env"
echo "  ✓ Meta answered and the call was accounted"

put PAID_META_RESEARCH_ENABLED true
put PUBLIC_META_RESEARCH_ENABLED true
put PAID_META_IMAGE_ENABLED "$ART"
restart

say "Switching research on"
OPEN_UNTIL=$(activate_public)
FINISHED=1

if [[ "$AUTO" == true ]]; then
  printf '# Polymath: keep research on (connect-meta.sh). Remove this file to stop renewing.\n%s %s * * * root bash %s/scripts/connect-meta.sh renew-if-due >> /var/log/polymath-renew.log 2>&1\n' \
    "$((RANDOM % 60))" "$((RANDOM % 6))" "$DIR" > "$CRON"
  chmod 644 "$CRON"
else
  rm -f "$CRON"
fi

cat <<EOF

✓ Research is on at https://$DOMAIN until $OPEN_UNTIL
  Limits: USD $TOTAL in total, USD $DAILY a day, each visitor USD $VISITOR_DAILY a day.
EOF
if [[ "$AUTO" == true ]]; then
  echo "  It renews itself every two weeks. Your prices and limits are reviewed again in $REVIEW_DAYS days."
else
  echo "  Keep it on for two more weeks at any time: bash $DIR/scripts/connect-meta.sh renew"
fi
echo "  See what's on and what's spent:           bash $DIR/scripts/connect-meta.sh status"
echo "  Stop all spending now:                    bash $DIR/scripts/connect-meta.sh stop"
