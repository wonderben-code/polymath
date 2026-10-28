# Meta readiness preparation

B3.1 provides a working operator flow to prepare, inspect and disable a proposed
Meta budget configuration. It makes no native request, constructs no model
client, runs no canary and activates nothing. The owner has not authorized paid
calls. A model key, a saved proposal or an operator login does not change that.

The operator supplies an exact provider model identifier and evidence for USD
pricing and absolute billable-token bounds. Polymath checks their shape and
arithmetic, then saves an immutable operator attestation. This is not independent
verification that the model, endpoint, prices or billing interpretation work.
Every response retains `native_status: "not_run"` and
`execution_available: false`.

## Authority and configuration

The separate browser page is `/operator/budget`; its HTTP routes are under
`/api/v1/budget`. It reuses the existing operator login. The bootstrap credential
is entered only into that login and is never part of a proposal, a URL, a log,
browser storage or an exported configuration. Cookie values and both CSRF tokens
remain outside the proposal and its audit records.

The server explicitly composes `ReadinessPolicy` with:

- `preparation_enabled`, default `false`;
- `allowed_actor_ids`, default empty;
- `max_hard_cap_microdollars`, at most `150000000`, which can only lower the
  application's USD 150 ceiling.

The corresponding settings are `BUDGET_READINESS_ENABLED=false`,
`BUDGET_ADMIN_ACTOR_IDS=[]` and
`BUDGET_MAX_HARD_CAP_MICRODOLLARS=150000000`. Enabling preparation requires a
configured `OperatorPolicy` and its actor in the explicit budget allowlist.
Publication authority alone does not grant budget authority.

Every account/proposal read requires the same live browser and operator session,
the current operator credential-policy fingerprint and the budget actor
allowlist. Every POST, including preview, also requires the normal Origin and
browser `X-CSRF-Token` checks plus `X-Operator-CSRF-Token`. A request cannot name
another actor or supply authority in its body. Unknown and unauthorized account
or proposal identifiers have a generic response.

Turning preparation off prevents previews and new proposals. Authorized account
inspection, proposal history and disable remain available. Removing an actor or
rotating/revoking the operator credential removes its authority immediately.

## Provider facts and arithmetic

Only the proposed two-counter billing contract is supported: all billable input
must be included in `prompt_tokens`, and all billable output must be included in
`completion_tokens`. Pricing is USD per one million tokens, represented as
integer microdollars. The operator must attest that no additional chargeable
counter, hidden reasoning charge, cached-input category or other fee is omitted.
Unsupported or uncertain billing belongs to a later contract, not an invented
conversion or a guessed surcharge.

Billable input and output bounds are absolute counts for a single permitted
request. They are not a characters-per-token ratio, an average, a visible-output
estimate or a deduction from `max_output_tokens`. Evidence must cover every
billable component under the proposed request limits. Application limits are
separate: output 16–4096 tokens, context 1000–90000 characters, model calls 1–20 and
request timeout 0.001–60 seconds reflect the currently implemented local bounds.
They do not prove the provider enforces equivalent billable limits.

For input bound `I`, output bound `O`, and integer per-million microdollar rates
`Pi` and `Po`, compute with integer arithmetic:

```text
input reservation  = (I * Pi + 999999) // 1000000
output reservation = (O * Po + 999999) // 1000000
per-call reserve   = input reservation + output reservation
full-run reserve   = per-call reserve * max_model_calls
```

Round each component up separately; rounding only the final sum can understate
the conservative quote. The per-call reserve must be positive and fit the
proposed run cap. Require:

```text
per-call reserve <= run cap <= daily cap <= hard cap <= server ceiling
```

The full-run reserve and whether it fits the run cap are displayed separately.
It is valid for the ledger to stop a run before its maximum call count. Do not
describe a maximum call count as a promise that all those calls fit the budget.
No amount or model identifier is prefilled as a verified provider fact.

## Preview, persistence and disable

The operator enters the complete proposal, requests an exact preview, reviews
its facts, evidence, validity interval and derived quote, then explicitly saves
that displayed proposal SHA-256. The canonical proposal contract and route DTOs
are specified below. Dynamic current spending and account flags are observations
outside the immutable proposal hash; a later usage change does not rewrite the
operator's attestation.

### Canonical proposal and preview

The exact type is `MetaReadinessProposal`. The following is a field map, **not a
valid submission or recommended configuration**. Empty/null values must be
replaced with operator-attested facts. Evidence arrays cannot remain empty.

```json
{
  "protocol": "polymath-meta-readiness-v1",
  "model_id": "",
  "endpoint": "https://api.meta.ai/v1/chat/completions",
  "currency": "USD",
  "usage_contract": "prompt_completion_inclusive_v1",
  "observed_at": null,
  "valid_until": null,
  "billable_bounds": {
    "input_tokens_absolute": null,
    "output_tokens_absolute": null,
    "evidence": []
  },
  "pricing": {
    "input_microdollars_per_million": null,
    "output_microdollars_per_million": null,
    "evidence": []
  },
  "usage_evidence": [],
  "application_limits": {
    "max_output_tokens": null,
    "max_context_characters": null,
    "max_model_calls": null,
    "request_timeout_seconds": null
  },
  "account": {
    "account_id": "",
    "hard_cap_microdollars": null,
    "daily_cap_microdollars": null,
    "run_cap_microdollars": null,
    "max_in_flight": null
  },
  "native_status": "not_run",
  "execution_available": false
}
```

`endpoint` is the fixed proposed Meta completion endpoint, not a caller-selected
fetch destination or a statement that native compatibility was verified. All
numeric inputs are strict bounded values; booleans and numeric strings are not
substitutes for integer amounts/counts. Each rate is a positive integer at most
`9000000000000000`; each absolute token bound is a positive integer at most
`2^63-1`. Account concurrency is 1–100. The smaller account/call cap checks above
still apply to every derived quote. Application limits are explicit rather than
provider defaults. A form can offer the implemented 12-call application default
as a local UI choice, but this supplies no provider pricing or token fact.

New account identifiers match `[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}`. Model
identifiers match `[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}` so explicit provider
namespaces remain possible. Inspection and disable also support existing ledger
account identifiers containing a slash; preparation does not rename them.

`observed_at` and `valid_until` are explicit timezone-aware UTC timestamps with
zero offset, and `observed_at < valid_until`. Preview/save use the database clock
to reject future observations or an expired proposal, including after lock
waits. B3.1 invents no validity duration or automatic freshness guarantee. A
saved expired attestation remains inspectable as history and cannot become an
execution authorization.

Each `billable_bounds.evidence`, `pricing.evidence` and `usage_evidence` contains
1–8 `ReadinessEvidence` records with exactly `url` and `content_sha256`. The URL
is bounded HTTPS with no credentials, query, fragment or non-default port;
`content_sha256` is the operator-supplied lowercase SHA-256 of the reviewed
document bytes. Polymath never fetches that URL or verifies that those bytes
exist. A syntactically valid URL/hash is not evidence of authenticity. Pricing
evidence covers the ceiling rates; bounds evidence covers the absolute billable
limits. `usage_evidence` covers the proposed exact model/endpoint/wire contract
and the inclusive two-counter billing interpretation. There is no separate
`protocol_evidence` field or duplicated `checked_at` timestamp.

Compute `proposal_sha256` from the **proposal only**: serialize its complete
validated DTO as UTF-8 JSON with `ensure_ascii=False`, `sort_keys=True`,
`separators=(',', ':')` and `allow_nan=False`, then apply SHA-256. The fixed
protocol version binds the ceiling formula. Derived quote fields and volatile
account spending are not added to the proposal hash. A policy/cap check can
reject a proposal without silently changing its facts.

The exact preview (`MetaReadinessQuote`) keys are:

| Key | Value |
|---|---|
| `proposal_sha256` | Canonical proposal hash |
| `proposal` | Complete validated `MetaReadinessProposal` |
| `input_reservation_microdollars` | Separately rounded input component |
| `output_reservation_microdollars` | Separately rounded output component |
| `per_call_reservation_microdollars` | Sum of those two components |
| `max_run_reservation_microdollars` | Per-call reserve multiplied by proposed maximum calls |
| `full_run_reservation_fits` | Whether that full-run amount fits the proposed run cap |
| `budget_limited_stop_notice` | Null when it fits; otherwise a static notice that the ledger may stop earlier |
| `native_status` | Always `not_run` |
| `execution_available` | Always false |

Migration `0019` introduces immutable proposal and audit records. A saved record
binds the account, exact canonical proposal and quote, content hash, actor and
current operator-policy provenance, and creation time. These are administrative
records, never public-library material. UPDATE/DELETE is rejected; downgrade
refuses to discard their history. No raw bootstrap credential, cookie, CSRF token
or model credential is stored in either table.

Saving is idempotent by account ID and proposal SHA-256. A lost-response retry
returns the same saved proposal, even if its validity has since expired; this
does not create a new grant or change account state. Changed facts require
another exact preview and a different immutable proposal. There is no independent
activation receipt. Read current account flags and usage through the account
inspection routes rather than inferring them from a historical proposal.

For a new account, save creates the `BudgetAccount` and proposal in one
transaction with the proposed hard/daily/concurrency caps and these flags:

```json
{"execution_enabled":false,"pricing_verified":false,"kill_switch":true,"frozen":false}
```

For an existing account, preparation requires matching existing hard, daily and
concurrency caps and disabled execution. It never changes those caps or resets
any execution, pricing, kill, frozen or accounting state. A different proposed
run cap or application limit is merely another attestation; no run is configured
or enqueued by preparation.

Disable is an explicit, idempotent safety action. It sets only
`execution_enabled=false` and `kill_switch=true`, and appends an audit record.
It does not clear `frozen`, mark pricing verified, release reservations, settle
unknown calls, reduce counted spending, or retract already authorized requests.
Actual late usage/accounting remains recordable through the existing ledger.

Use the lock order **Operator → Account** for save and disable. Serialize absent
account creation with a bounded account-ID advisory lock after the operator
lock, then acquire any existing account row. Recheck live browser/operator
expiry, current credential-policy fingerprint, allowed actor and preparation
policy after waits and immediately before a new proposal/account commit.
Disable remains possible when preparation is off but still needs live allowed
operator authority. Do not acquire research, head, manifest or publication-job
locks, and never call `BudgetService.authorize_execution` or
`BudgetService.configure_run` from this flow.

## HTTP routes and capabilities

`GET /api/v1/budget/capabilities` requires no database access or session. Its
exact keys are:

```json
{
  "preparation_available": false,
  "inspection_available": false,
  "disable_available": false,
  "native_status": "not_run",
  "execution_available": false,
  "reason": "readiness_unavailable"
}
```

Missing operator configuration or an actor absent from the configured allowlist
gives the generic response above without exposing actor IDs. With authorized
configuration but preparation off, inspection/disable are true and reason is
`preparation_disabled`. With authorized preparation enabled, those three local
capabilities are true and reason is null. Actual requests still require their
live sessions and CSRF authority. Execution remains false in every case.

| Route | Purpose |
|---|---|
| `POST /api/v1/budget/readiness/preview` | `{proposal}` → exact proposal, canonical hash and conservative quote; creates no account or audit grant |
| `POST /api/v1/budget/readiness` | `{proposal, expected_proposal_sha256}` → immutable saved proposal receipt |
| `GET /api/v1/budget/readiness` | Bounded proposal history; optional account filter, limit and UUID cursor |
| `GET /api/v1/budget/readiness/{proposal_id}` | Exact immutable proposal, saved quote and current validity status |
| `GET /api/v1/budget/accounts` | Bounded account inspection list in the explicitly authorized administrative scope |
| `GET /api/v1/budget/accounts/{account_id}` | Current configured caps, flags and counted spending/holds |
| `POST /api/v1/budget/accounts/{account_id}/disable` | Empty body; disable and audit, without altering ledger amounts |

Errors are static, input-sanitized codes: 401 for lost sessions, 403 for role/CSRF
denial, 404 for unavailable resources, 409 for exact-input/account conflicts, 422
for invalid attestations and 503 for disabled preparation or unavailable storage.
Every response uses no-store/nosniff headers. No route calls Meta, fetches an
evidence URL, invokes a model factory or creates a research/publication job.

Account inspection reports committed and daily committed microdollars, open
reservation/unknown holds, reservation count and actual current flags. It uses
the ledger's conservative counting: open holds remain counted, and known settled
charges are counted on the appropriate dispatch/settlement dates without being
counted twice globally. A saved proposal is never a reset of prior spending.

## Later stages and verification

B3.2 is a separate native canary/usage verification milestone. B3.3 is a separate
activation milestone requiring real native facts, appropriate worst-case bounds,
provider/account cap confirmation and explicit permission for paid work. Neither
exists in this slice. No native-verified boolean, activation endpoint, probe
button, credential upload or production Meta factory is added here.

Verification uses synthetic attestations and PostgreSQL only. Cover exact
rounding and overflow/bounds, unsupported billing rejection, preview/hash/save,
immutable idempotence, new-account disabled flags, existing-account preservation,
actor/CSRF/expiry/rotation and post-lock waits, conservative settled/unknown
counts, disable during pending work, sanitized responses, disabled defaults and
browser refresh without model/provider requests. The browser must show the
attestation's status honestly and offer inspection/disable without a new paid
action.
