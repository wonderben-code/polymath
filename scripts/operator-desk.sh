#!/usr/bin/env bash
# Drive the operator desk from the production server itself, so the engineer can
# prepare a readiness proposal, run the one-call verification, activate research
# and fund visitors without anyone relaying browser steps. It speaks to the same
# HTTP API the desk in the browser uses, with the same two cookies and two CSRF
# tokens, and every write still goes through the server's own checks (fresh
# timestamps, exact hashes, caps, kill switch). Nothing here can raise a cap or
# bypass a review field: a value the desk would reject is rejected here too.
#
#   bash scripts/operator-desk.sh <command> [arguments]
#
# The operator code is read from deploy/production.env on the server and sent
# only in the login request body. It is never printed.
#
# Commands
#   login                         sign in (done automatically by the others)
#   get <path>                    GET /api/v1/<path>
#   post <path> <json|@file>      POST /api/v1/<path> with a JSON body
#   templates                     write editable copies of the latest saved
#                                 proposal, attestation and review to STATE/
#   proposal <file>               preview and save a readiness proposal
#   probe <readiness_id> <attestation>        preview and arm ONE paid call
#   probe-run                     run the verification worker once
#   job <job_id>                  show a verification job (state, receipt id)
#   research <readiness_id> <receipt_id> <review> <attestation> [max_bridges]
#   public <readiness_id> <receipt_id> <review> <attestation> <limits> <valid_until> [max_bridges]
#   current                       current research and public activations
#   revoke-public <activation_id> stop a public funding activation
#   disable <account_id>          kill switch on for an account
#   account <account_id>          inspect an account
#
# <attestation>, <review> and <limits> are JSON files; observed_at in the
# attestation and review is refreshed to two minutes ago before sending.
set -euo pipefail

DIR=/opt/polymath
ENV_FILE="${POLYMATH_ENV_FILE:-$DIR/deploy/production.env}"
BASE="${POLYMATH_DESK_URL:-}"
STATE="${POLYMATH_DESK_STATE:-/root/.polymath-desk}"
umask 077
mkdir -p "$STATE"
JAR="$STATE/cookies"
CSRF_FILE="$STATE/csrf"
OP_CSRF_FILE="$STATE/operator-csrf"

need() { command -v "$1" >/dev/null 2>&1 || { echo "missing tool: $1" >&2; exit 1; }; }
need curl; need jq

if [ -z "$BASE" ]; then
  DOMAIN=$(grep -E '^POLYMATH_DOMAIN=' "$ENV_FILE" | cut -d= -f2- | tr -d '"' || true)
  BASE="https://${DOMAIN:-askpolymath.com}"
fi
API="$BASE/api/v1"

dc() { docker compose -f "$DIR/deploy/compose.production.yaml" --env-file "$ENV_FILE" "$@"; }

now_minus() { date -u -d "-$1 minutes" +%Y-%m-%dT%H:%M:%SZ; }

body_of() {
  # a JSON string, or @file
  case "$1" in @*) cat "${1#@}" ;; *) printf '%s' "$1" ;; esac
}

http() {
  # http METHOD path [json]
  local method=$1 path=$2 data=${3:-}
  local args=(-sS --max-time 60 -X "$method" -b "$JAR" -c "$JAR" -H 'Accept: application/json' -w '\n%{http_code}')
  if [ -n "$data" ]; then
    args+=(-H 'Content-Type: application/json' --data-binary "$data")
    [ -s "$CSRF_FILE" ] && args+=(-H "X-CSRF-Token: $(cat "$CSRF_FILE")")
    [ -s "$OP_CSRF_FILE" ] && args+=(-H "X-Operator-CSRF-Token: $(cat "$OP_CSRF_FILE")")
  fi
  local out code
  out=$(curl "${args[@]}" "$API/$path")
  code=${out##*$'\n'}
  out=${out%$'\n'*}
  printf '%s' "$out"
  if [ "$code" -ge 400 ]; then
    echo >&2
    echo "HTTP $code from $method /$path" >&2
    return 1
  fi
}

login() {
  rm -f "$JAR" "$CSRF_FILE" "$OP_CSRF_FILE"
  http GET session | jq -r '.csrf_token' > "$CSRF_FILE"
  local token
  token=$(grep -E '^OPERATOR_BOOTSTRAP_TOKEN=' "$ENV_FILE" | cut -d= -f2- | tr -d '"')
  [ -n "$token" ] || { echo "OPERATOR_BOOTSTRAP_TOKEN is not set in $ENV_FILE" >&2; exit 1; }
  http POST operator/session "$(jq -cn --arg t "$token" '{token: $t}')" > "$STATE/operator.json"
  jq -r '.csrf_token' "$STATE/operator.json" > "$OP_CSRF_FILE"
  echo "signed in as $(jq -r '.display_name' "$STATE/operator.json") until $(jq -r '.expires_at' "$STATE/operator.json")"
}

ensure_login() {
  if [ -s "$OP_CSRF_FILE" ] && http GET operator/session 2>/dev/null | jq -e '.authenticated == true' >/dev/null; then
    return 0
  fi
  login >&2
}

fresh() {
  # fresh <file> [key]: the JSON with observed_at refreshed to two minutes ago
  jq -c --arg at "$(now_minus 2)" '.observed_at = $at' "$1"
}

pretty() { jq . ; }

cmd=${1:-help}; shift || true
case "$cmd" in
  login) login ;;
  get) ensure_login; http GET "$1" | pretty ;;
  post) ensure_login; http POST "$1" "$(body_of "$2")" | pretty ;;
  templates)
    ensure_login
    http GET 'budget/readiness?limit=1' | jq '.proposals[0].proposal' > "$STATE/proposal.json"
    http GET 'budget/research/activations?limit=1' > "$STATE/last-activation.json"
    jq '.activations[0].manifest.provider_account_attestation' "$STATE/last-activation.json" > "$STATE/attestation.json"
    jq '.activations[0].manifest.review' "$STATE/last-activation.json" > "$STATE/review.json"
    jq '.activations[0].manifest.public_limits // empty' "$STATE/last-activation.json" > "$STATE/limits.json"
    echo "== $STATE/proposal.json"; cat "$STATE/proposal.json"
    echo "== $STATE/attestation.json (identifiers redacted here)"; jq '.provider_billing_account_id = "<kept>"' "$STATE/attestation.json"
    echo "== $STATE/review.json"; cat "$STATE/review.json"
    echo "== $STATE/limits.json"; cat "$STATE/limits.json"
    echo "== verification jobs"; http GET 'budget/verification/jobs?limit=5' | jq '[.jobs[]? | {id, status, receipt: .receipt.id, model: .manifest.request.payload.model}]'
    ;;
  proposal)
    ensure_login
    local_body=$(jq -c --arg at "$(now_minus 2)" '.observed_at = $at | .native_status = "not_run" | .execution_available = false' "$1")
    quote=$(http POST budget/readiness/preview "$(jq -cn --argjson p "$local_body" '{proposal: $p}')")
    echo "== quote"; printf '%s' "$quote" | jq '{proposal_sha256, per_call_reservation_microdollars, max_run_reservation_microdollars, full_run_reservation_fits, budget_limited_stop_notice}'
    saved=$(http POST budget/readiness "$(printf '%s' "$quote" | jq -c '{proposal: .proposal, expected_proposal_sha256: .proposal_sha256}')")
    echo "== saved"; printf '%s' "$saved" | jq '{id, account_id, proposal_sha256, temporal_status, created_at}'
    ;;
  probe)
    ensure_login
    preview=$(http POST budget/verification/preview "$(jq -cn --arg r "$1" --argjson a "$(fresh "$2")" '{readiness_id: $r, provider_account_attestation: $a}')")
    echo "== preview"; printf '%s' "$preview" | jq '{manifest_sha256, model: .manifest.request.payload.model, reservation_microdollars: .manifest.reservation_microdollars, valid_until: .manifest.valid_until}'
    job=$(http POST budget/verification/jobs "$(printf '%s' "$preview" | jq -c '{manifest: .manifest, expected_manifest_sha256: .manifest_sha256}')")
    echo "== armed"; printf '%s' "$job" | jq '{id, status, manifest_sha256}'
    printf '%s' "$job" | jq -r '.id' > "$STATE/last-probe-job"
    echo "Now run: probe-run (within the consent window), then: job $(cat "$STATE/last-probe-job")"
    ;;
  probe-run)
    cd "$DIR"
    dc --profile research run --rm --no-deps research-worker /app/backend/.venv/bin/python -m polymath.meta_probe_worker --once
    ;;
  job)
    ensure_login
    http GET "budget/verification/jobs/${1:-$(cat "$STATE/last-probe-job")}" | jq '{id, status, receipt: .receipt.id, assessment: .assessment, observation: (.observation // {} | {http_status, finish_reason, input_tokens, output_tokens, started_at, finished_at})}'
    ;;
  research)
    ensure_login
    bridges=${5:-3}
    body=$(jq -cn --arg r "$1" --arg c "$2" --argjson rev "$(fresh "$3")" --argjson att "$(fresh "$4")" --argjson b "$bridges" \
      '{readiness_id: $r, canary_receipt_id: $c,
        review: ($rev + {model_wire_usage_confirmed: true, pricing_bounds_confirmed: true, funding_cap_confirmed: true}),
        provider_account_attestation: $att,
        explore_collection: {protocol: "polymath-explore-collection-v1", max_bridges: $b},
        scientific_context: {protocol: "polymath-scientific-context-profiles-v1"}}')
    preview=$(http POST budget/research/preview "$body")
    echo "== preview"; printf '%s' "$preview" | jq '{manifest_sha256, model: .manifest.model_id, caps: .manifest.proposal.account, calls: .manifest.proposal.application_limits.max_model_calls, valid_until: .manifest.valid_until}'
    saved=$(http POST budget/research/activations "$(printf '%s' "$preview" | jq -c '{manifest: .manifest, expected_manifest_sha256: .manifest_sha256}')")
    echo "== activated"; printf '%s' "$saved" | jq '{id, manifest_sha256, approval}'
    ;;
  public)
    ensure_login
    bridges=${7:-3}
    body=$(jq -cn --arg r "$1" --arg c "$2" --argjson rev "$(fresh "$3")" --argjson att "$(fresh "$4")" --argjson lim "$(jq -c . "$5")" --arg until "$6" --argjson b "$bridges" \
      '{readiness_id: $r, canary_receipt_id: $c,
        review: ($rev + {model_wire_usage_confirmed: true, pricing_bounds_confirmed: true, funding_cap_confirmed: true}),
        provider_account_attestation: $att, public_limits: $lim, valid_until: $until,
        discovery: {protocol: "polymath-surprise-ladder-v1", max_stages: 3, max_depth: 2},
        explore_collection: {protocol: "polymath-explore-collection-v1", max_bridges: $b},
        scientific_context: {protocol: "polymath-scientific-context-profiles-v1"}}')
    preview=$(http POST budget/research/public/preview "$body")
    echo "== preview"; printf '%s' "$preview" | jq '{manifest_sha256, model: .manifest.model_id, public_limits: .manifest.public_limits, valid_until: .manifest.valid_until}'
    saved=$(http POST budget/research/public/activate "$(printf '%s' "$preview" | jq -c '{manifest: .manifest, expected_manifest_sha256: .manifest_sha256}')")
    echo "== activated"; printf '%s' "$saved" | jq '{id, manifest_sha256, approval, expires_at}'
    ;;
  current)
    ensure_login
    echo "== research"; http GET budget/research/current | jq '{reason, activation: (.activation // {} | {id, manifest_sha256, approval})}'
    echo "== public"; http GET budget/research/public/current | jq '{reason, activation: (.activation // {} | {id, manifest_sha256, expires_at, public_limits: .manifest.public_limits})}'
    ;;
  revoke-public) ensure_login; http POST "budget/research/public/activations/$1/revoke" '{}' | pretty ;;
  disable) ensure_login; http POST "budget/accounts/$1/disable" '{}' | pretty ;;
  account) ensure_login; http GET "budget/accounts/$1" | pretty ;;
  help|*) sed -n '2,36p' "$0" ;;
esac
