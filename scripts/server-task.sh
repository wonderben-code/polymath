#!/usr/bin/env bash
# Named maintenance tasks for the production server, run over SSH by the
# "Server task" GitHub Actions workflow. Output never includes secrets: every
# NAME=value for a key, token, secret or password is redacted before it leaves.
set -uo pipefail

DIR=/opt/polymath
ENV="$DIR/deploy/production.env"
TASK="${1:-status}"
ARG="${2:-}"

redact() { sed -E 's/((KEY|TOKEN|SECRET|PASSWORD)[A-Z_]*=)[^[:space:]]*/\1<redacted>/g'; }
dc() { docker compose -f "$DIR/deploy/compose.production.yaml" --env-file "$ENV" "$@"; }
profiles() {
  local list=()
  grep -q '^VISITOR_ZENODO_ENABLED=true' "$ENV" && list+=(--profile zenodo)
  grep -q '^PAID_META_RESEARCH_ENABLED=true' "$ENV" && list+=(--profile research)
  echo "${list[@]:-}"
}
sql() { dc exec -T db psql -U polymath -d polymath -v ON_ERROR_STOP=1 "$@"; }
app() { dc exec -T app /app/backend/.venv/bin/python "$@"; }

cd "$DIR" || { echo "No checkout at $DIR"; exit 1; }
{
case "$TASK" in
  status)
    echo "== release"; git log -1 --format='%h %cd %s'
    echo "== switches"; grep -E '^(BUDGET_READINESS|PAID_|PUBLIC_META|PUBLIC_MAX|PUBLIC_RESOURCE|META_MODEL|META_IMAGE|IMAGE_|VISITOR_ZENODO|ACCOUNTS_ENABLED)' "$ENV"
    echo "== containers"; dc $(profiles) ps
    echo "== readiness"; curl -s --max-time 15 https://askpolymath.com/api/v1/readiness; echo
    echo "== capabilities"
    for path in budget/capabilities budget/research/capabilities budget/research/public/capabilities studio/capabilities; do
      printf '%s: ' "$path"; curl -s --max-time 15 "https://askpolymath.com/api/v1/$path"; echo
    done
    ;;
  update)
    git pull --ff-only
    dc $(profiles) up -d --build --wait
    git log -1 --format='updated to %h'
    ;;
  restart)
    dc $(profiles) up -d --wait
    ;;
  logs)
    dc logs --tail="${LINES:-200}" --since="${SINCE:-30m}" "${ARG:-app}"
    ;;
  diagnose)
    RUN="${ARG:-$(sql -Atc "SELECT id FROM research_runs WHERE task_kind='research' ORDER BY created_at DESC LIMIT 1")}"
    echo "== run $RUN"
    sql -c "SELECT id, mode, task_kind, status, created_at, outcome FROM research_runs WHERE id='$RUN'"
    echo "== events"
    sql -c "SELECT sequence, kind, left(summary, 160) AS summary FROM research_events WHERE run_id='$RUN' ORDER BY sequence" 2>/dev/null || true
    echo "== model replies"
    sql -c "SELECT request_id, status, coalesce(observation->>'finish_reason', observation->'observation'->>'finish_reason') AS finish, coalesce(observation->>'output_tokens', observation->'observation'->>'output_tokens') AS out_tokens, left(coalesce(observation->>'content', observation->'observation'->>'content'), 3000) AS reply FROM research_external_requests WHERE run_id='$RUN' ORDER BY request_id"
    echo "== last request's conversation (without the system prompt)"
    sql -c "SELECT ord, m->>'role' AS role, left(m->>'content', 2500) AS content FROM meta_research_call_requests r, jsonb_array_elements(r.request->'payload'->'messages') WITH ORDINALITY AS t(m, ord) WHERE r.run_id='$RUN' AND r.request_id=(SELECT max(request_id) FROM meta_research_call_requests WHERE run_id='$RUN') AND m->>'role' <> 'system' ORDER BY ord"
    ;;
  account)
    sql -c "SELECT id, hard_cap_microdollars, daily_cap_microdollars, max_in_flight, execution_enabled, kill_switch, frozen, frozen_reason, native_scope, native_control_epoch FROM budget_accounts"
    sql -c "SELECT status, count(*), sum(estimated_microdollars) AS held, sum(actual_microdollars) AS actual FROM budget_reservations GROUP BY status ORDER BY status"
    ;;
  holds)
    app -m polymath.budget_holds list
    ;;
  settle-hold)
    read -r ID AMOUNT EVIDENCE <<<"$ARG"
    app -m polymath.budget_holds settle "$ID" --actual-microdollars "$AMOUNT" --evidence "$EVIDENCE" --confirm
    ;;
  sql)
    sql -c "$ARG"
    ;;
  rotate-operator-code)
    cp -p "$ENV" "$ENV.bak-$(date -u +%Y%m%dT%H%M%SZ)"
    NEW="$(openssl rand -hex 32)"
    sed -i "s|^OPERATOR_BOOTSTRAP_TOKEN=.*|OPERATOR_BOOTSTRAP_TOKEN=$NEW|" "$ENV"
    dc $(profiles) up -d --wait
    echo "Operator code rotated. The owner reads it on the server with: grep '^OPERATOR_BOOTSTRAP_TOKEN=' $ENV"
    ;;
  shell)
    bash -c "$ARG"
    ;;
  *)
    echo "Unknown task: $TASK"; exit 2
    ;;
esac
} 2>&1 | redact
