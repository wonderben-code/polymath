# One-call native Meta verification contract

This is the B3.2 implementation contract. The verification lane uses the actual
Meta adapter with mock transport in tests; live native compatibility remains
unverified. Paid calls remain forbidden in the current session. No Meta key or
real model identifier has been supplied. This document does not authorize a
probe, activate research, or establish native API compatibility.

B3.2 introduces one explicitly approved, bounded protocol probe through the
actual `MetaModel` adapter. There is no scripted research, substitute model,
fallback provider, literature search or scientific result. B3.3 general research
activation is separate. A successful probe observes one response and its reported
usage; it does not verify scientific quality, pricing authenticity, exhaustive
billing, available credits, or a provider-side spending cap.

## Exact input and honest result

Start from an existing immutable `MetaReadinessProposalRecord`; never create a
new account or reset its ledger for verification. The proposal must still be
current, match its canonical hash, obey the current server ceiling and identify
the server-configured native budget account. The model and endpoint come from
that exact proposal. Neither a browser request nor a CLI argument can substitute
a model, endpoint, credential, prompt, price, token bound or reservation amount.

Use versioned, server-owned messages with no user goal, paper, source, attachment
or private workspace text. The prompt asks for exactly one JSON action of this
shape:

```json
{"kind":"conclude","conclusion":{"outcome":"insufficient_evidence","reason":"Protocol check only."}}
```

The reply is parsed through `ACTION_ADAPTER`, then restricted to `ConcludeAction`
with `InsufficientFinding`. A nonempty bounded reason can differ from the example.
Search/read/other actions, positive conclusions, extra JSON, prose wrappers and
invalid text fail the probe. No `ResearchController`, repair loop, tool call,
second completion or generated `ResearchResult` is involved.

The fixed wire payload is:

```text
model      = exact readiness proposal model_id
messages   = PROBE_MESSAGES_V1, a fixed server constant
max_tokens = proposal.application_limits.max_output_tokens
stream     = false
```

Use the complete output allowance explicitly reviewed in the proposal rather
than imposing an artificial small cap on a frontier model. A short request or
reply does not establish a cheaper billing bound. An incomplete response fails
honestly and triggers no repair call. Use the pinned proposal's timeout, bounded
by the adapter's existing 60-second ceiling. The fixed request must also fit the
proposal's application context limit. Retain normal TLS verification, the fixed
HTTPS endpoint, bounded response reads, no redirects and no automatic retries.

Hash the canonical request descriptor `{method, endpoint, payload}`. Bind the
timeout and adapter/probe protocol version in the manifest as well. The real
adapter must construct that exact request from the manifest; it must not rebuild
its prompt from a newer constant or reread a different model/key after approval.

## Manifest, attestation and approval

`MetaProbeManifest` is immutable and includes:

| Field | Meaning |
|---|---|
| `protocol` | A fixed version binding the prompt, request, quote and result rules |
| `readiness_id`, `proposal_sha256` | Exact existing saved proposal |
| `account_id` | The server-configured native global ledger |
| `credential_binding_sha256` | Domain-separated credential binding computed by the server |
| `native_control_epoch` | Current account control generation, pinned before approval |
| `clear_kill_switch` | Explicitly true for this reviewed one-probe arm only |
| `request`, `request_sha256` | Complete fixed request descriptor and canonical hash |
| `request_timeout_seconds` | The exact approved local timeout |
| `input_reservation_microdollars`, `output_reservation_microdollars` | Derived ceiling components |
| `reservation_microdollars` | Their sum; never a caller estimate |
| `provider_account_attestation` | Current human statements and evidence for credits and provider cap |
| `valid_until` | The reviewed manifest deadline, no later than proposal validity |

The credential binding is a one-way, domain-separated digest, not an API key,
authorization header or credential upload. Compute and compare it only on the
server. Keep the raw key in secret configuration, out of database rows, response
bodies, URLs, events, logs and browser storage. Key rotation invalidates fresh
dispatch under the old manifest. The worker sends only with the exact credential
whose binding was checked at the committed grant boundary.

The attestation identifies the provider billing-account scope and records its
observation/expiry times, available credit, configured provider-side hard cap and
reviewed evidence references/content hashes. Its USD amounts use integer
microdollars. It must cover the same credential/model/billing scope and the
proposed inclusive two-counter contract. The application's global account cap
must fit the attested provider cap and the server's ceiling of at most USD 150.
For B3.2, both attested available credit and remaining provider allowance must
cover the account's **entire committed ledger plus the new reservation**. This
deliberately overcounts charges that may predate the attestation; it avoids
reusing the same attested credit after a previous probe settles and its open
hold disappears. Checking only current open holds is insufficient. These are
explicit human attestations, not
server-verified native facts; recording a dashboard/document hash proves neither
its truth nor that the provider enforces a hard limit.

Canonicalize the complete validated manifest as UTF-8 JSON using the same sorted
keys, compact separators, `ensure_ascii=False` and `allow_nan=False` convention as
readiness. Hash only that manifest. Do not include its own database UUID,
approval UUID, job UUID, volatile ledger usage or receipt in its hash. The
reference chain is manifest → approval → job → journal → receipt, without hash
cycles. Rebuild and compare every derived field on the server before approval.

The operator first receives an exact preview, then explicitly approves that
manifest/hash for one paid protocol probe. Preview never constructs a model,
sends a request, arms an account or grants authority. Approval requires a live
browser, current operator policy, explicit budget actor allowlist, same-origin
browser CSRF and operator CSRF. The body cannot name an actor, supply a role or
claim native success. Approval lifetime is bounded by the current browser,
operator, proposal, manifest and attestation deadlines and the server's consent
lifetime. No deadline is extended during a retry.

Approval and enqueue are one explicit **Arm and queue THIS one call** POST
transaction: save the immutable manifest and human approval, create its dedicated
job and bind the exact account
scope atomically. A unique manifest-to-job and approval-to-job binding makes a
lost-response retry return the same work, not another permission to spend. The
arm atomically advances the previewed epoch from `N` to `N+1`; the approval pins
that resulting `armed_epoch`. An identical manifest retry returns the original
work without advancing the generation, clearing kill or restoring authority,
even if that work was subsequently stopped. A new arm is blocked by any queued,
running, prepared or unknown predecessor on the account. A new
probe needs a new exact human approval. A model, saved receipt, publisher login,
previously passed probe or browser session cannot issue that approval.

## One global ledger and a probe-only authority

Reuse `BudgetAccount`, `BudgetRunLimit`, `BudgetReservation` and
`ResearchExternalRequest`. The dedicated `verify_meta` run has a one-call limit
and a run cap equal to its manifest reservation. Probe cost, previous settled
cost, research cost and all reserved/in-flight/unknown holds count against the
same account's existing global and daily caps. Do not create a temporary
verification account, subtract prior spending, release unknown holds or fork the
account when a credential rotates.

Native policy selects one explicit server-configured ledger ID. Immutable unique
credential and stable provider billing-account identity bindings must resolve
to that same ledger; a conflicting binding fails closed. A credential rotation
retains the existing provider-account/ledger mapping. A different saved
preparation record does not entitle the caller to a fresh USD 150 native budget.

The account needs a dedicated native scope and exact manifest binding. A
probe-only account and a `verify_meta` run must be rejected by generic research
reservation/dispatch paths, even if legacy execution/pricing flags are
accidentally true. The ordinary `ResearchWorker` excludes that task kind
and cannot use its approval, account scope or budget run limit. Trusted internal
legacy fixtures can retain their existing Account → Run path; `native_scope=NULL`
is never authorization for the native model factory.

The exact arm visibly and atomically clears the kill switch only for this
manifest and dedicated scope. That action is explicitly included in the reviewed
manifest; saving B3.1 preparation never performs it. Ordinary `execution_enabled`
and `pricing_verified` remain unchanged; both are false for new prepared accounts.
The native reservation/claim can bypass only those two general flags, using an internally
validated one-manifest permit. It never bypasses kill, freeze, money caps,
concurrency limits, current ownership or the single-call limit. An account with
ordinary execution already enabled is ineligible for this preparation lane.

Every relevant safety stop increments a shared monotonic `native_control_epoch`
under the account lock, including `BudgetService.disable_execution`,
`set_kill_switch(True)` and readiness safe-disable. Increment even if the
execution/kill flags already have the requested disabled values. Approval pins
the current generation; its request hash/manifest cannot be replayed to clear a
later kill or rearm an account after any later stop. Comparing only the latest
readiness audit is insufficient because internal budget stop paths must fence
the same authority. Cancelling/revoking the arm also fences it.

There is no generic kill-clear endpoint and no ordinary research activation.
Account caps/concurrency, `frozen`, prior pricing flags and ledger facts remain
unchanged. B3.2 cannot reconcile unknown work, release its hold or turn an
attestation into verified pricing. A global kill/freeze always prevents a new
probe. This narrowly scoped arming contract is not permission to arm or send in
the current session.

## Dispatch, lock order and cancellation

Only a distinct native dispatch method can mint the one-send grant. Do not add
an operator callback to the legacy path after its account lock. The native
method loads
immutable request bindings to find the approving operator, then locks:

```text
Operator → Account → verify_meta Run
```

Under those locks, revalidate all of the following after waits and again just
before commit: current operator/browser expiry, policy fingerprint, budget role,
approval not revoked/expired, paid-probe server flag, exact credential binding,
saved proposal/manifest hashes and validity, provider attestation validity,
native ledger/scope/manifest/disable-generation binding, no freeze/stop, current
job lease, task kind and absence of any prior send. Read the database clock.

The same transaction derives the cost, creates/validates the one-call run limit,
reserves the money, consumes the one-use dispatch claim, inserts the exact
`ResearchExternalRequest`, and marks the request pending. A failure rolls all of
that back. The generic caller-controlled `estimated_microdollars` path is not a
native verification API. A duplicate matching request is recovery metadata, not
a grant. Conflicting hashes fail closed.

Never acquire a previously unlocked operator after an account/run lock. Existing
budget operations and late accounting can retain Account → Run without entering
the human-authority guard. Cancellation/revocation that needs these locks follows
the native order. Plain run-only heartbeat operations must not later acquire an
account or operator in the same transaction.

The grant commit is the authorization point. Revocation/cancellation before it
prevents a send; after it, the system cannot promise to unsend an already
authorized operation. It can forbid every later grant and truthfully preserve
late facts. A post-grant timeout, lost worker, interrupted socket or cancelled
coroutine never implies zero cost. Do not automatically requeue a lost worker or
resend a prepared/unknown request.

## Observation, accounting and receipt

The real adapter supplies the durable observation. HTTP/browser/model input
cannot write a receipt, declare success, invent usage or settle a reservation.
Validation of generated JSON is separate from validation of the transport and
billing observation. Keep bounded sanitized response facts and hashes; do not
persist authorization headers or raw provider error bodies.

The existing `MetaModel.complete` keeps its historical semantics. A separate
observation method must expose trustworthy bounded transport/model/usage facts
before finish/action semantic validation. This avoids discarding chargeable
known usage merely because generated JSON is malformed or a finish reason is
wrong. The worker journals that observation before deriving its receipt. Never
reconstruct missing usage
from response length, a token ratio, the requested output cap, a guessed model
price, an error status or a timeout.

For strict nonnegative reported counters `i`, `o` and the manifest's ceiling rates
`Pi`, `Po`, calculate the counted amount separately per component:

```text
actual_input  = (i * Pi + 999999) // 1000000
actual_output = (o * Po + 999999) // 1000000
counted_cost  = actual_input + actual_output
```

Do not reuse the older `WorkerPolicy.actual_cost` aggregate-rounding formula.
The reserve always uses the proposal's absolute billable bounds, even for this
short prompt and the full reviewed output allowance. A reported billable counter is
checked against its attested absolute bound. A visible-output request limit is
not silently substituted for an inclusive billable-token ceiling.

Require the provider-returned model ID to equal the exact requested ID, without
guessed aliases. Known valid usage from that exact model permits honest
accounting even if the finish reason/action JSON is wrong; that probe fails
protocol validation and produces no successful verification claim. If known
valid counters exceed an attested billable bound, record the full separately
rounded count-derived cost and freeze the account; verification fails. Never
understate a representable known overage as the smaller original estimate.

A missing/mismatched model ID, missing/malformed counters or unrepresentable
cost keeps the full unknown hold, with an account freeze where contradictory
facts require it. Retain the bounded observation rather than clamping or hiding
it. Counts and rates do not prove the provider's final invoice; the receipt
states that limitation.

An immutable `MetaProbeReceipt` can report `passed`, `failed` or `unknown`.
Unknown preserves its hold; recording a receipt is never a release or approval.
A successful receipt is derived only from the exact bound
observed adapter journal with both valid counters and a valid insufficient-
evidence action. It binds manifest/proposal/request hashes, credential binding,
run/reservation IDs, observation/action hashes, reported counters, counted cost,
timings and explicitly limited protocol outcome. Record requested versus
observed model identity honestly. The new observation must include the exact
returned model ID; the existing `ModelReply` alone is insufficient. A missing
provider request identifier can be marked absent rather than fabricated; it
does not replace request/hash/grant binding.
Never call the result `pricing_verified`, scientific proof or general execution
permission. `research_execution_available` remains false.

Observation/settlement uses Account → Run and remains allowed after owner expiry,
approval revocation, kill, cancellation or loss of the originating worker lease.
It grants no new send. Receipt finalization from an already known journal is
free and can also survive those events. Do not acquire operator authorization
after the accounting locks merely to preserve an actual response. A late
response cannot silently change a previously reconciled fact; retain a conflict
and freeze using the existing dispatch rules.

## States and recovery

| Durable state | Allowed next step |
|---|---|
| Preview only | Explicit exact approval/enqueue; no authority or charge exists |
| Queued, no send | Current authority may grant once; cancel/revoke prevents that grant |
| Request prepared | One grant has been consumed; never send again from recovery |
| Unknown/no usable usage | Keep full hold and stop; no automatic retry/reconciliation |
| Known observation, no receipt | Free deterministic validation/accounting/finalization only |
| Successful protocol receipt | Read immutable facts; research remains disabled |
| Known protocol failure | Retain charge/diagnostics; no repair call |
| Cancelled/expired/revoked | Preserve late observations and holds; no fresh permission |

An explicit resume may finish already recorded work or continue a queued job
that provably never received a grant, subject to the original still-live exact
approval. It must never renew approval or resend an unknown/prepared request.
Every generic `/runs/{id}` detail, result, events, cancel and resume route rejects
`verify_meta`, even for its browser owner; use budget-role verification routes.
The default research lists,
knowledge map, memory and public library must not treat this fixed probe as a
research finding.

## HTTP and runtime surface

All verification routes are under `/api/v1/budget/verification`. The controls
reuse `/operator/budget`; there is no browser credential upload. `POST /preview`
accepts `{readiness_id, provider_account_attestation}` and returns
`{manifest, manifest_sha256, research_execution_available:false}`. The explicit
arm accepts `{manifest, expected_manifest_sha256}`; cancel/finalize accept `{}`.

| Route | Operation |
|---|---|
| `GET /capabilities` | Session-free/no-DB configuration status, no key/fingerprint/actor leak |
| `POST /preview` | Existing readiness ID plus current attestation → exact derived manifest/hash |
| `POST /jobs` | Exact displayed manifest/hash → explicit scoped kill-clear, human approval and one-probe job |
| `GET /jobs` | Bounded authorized job history/cursor |
| `GET /jobs/{id}` | Bound job, current approval/accounting state and optional immutable receipt |
| `POST /jobs/{id}/cancel` | Revoke fresh authority/cancel work without removing holds |
| `POST /jobs/{id}/finalize` | Free finalization from an already observed journal only |
| `GET /receipts/{id}` | Exact immutable protocol observation with its limitations |

Except capabilities, require the current authorized budget operator for HTTP
inspection and control. Every POST requires both CSRF domains and Origin checks.
Return static sanitized errors and no-store/nosniff/no-referrer headers. Public
reads, ordinary browser ownership, publisher authority and generated model text
cannot use these routes. No activation or unknown-charge reconciliation endpoint
belongs here.

Capabilities require no database/session and expose exactly
`verification_available`, `inspection_available`, `cancel_available`,
`finalization_available`, `research_execution_available` and `reason`. Research
execution is always false. With no configured budget role, the four local
capabilities are false and reason is `meta_probe_unavailable`. An authorized
configured role can inspect, cancel and finalize when new verification is off.
Only explicit probe configuration makes `verification_available` true; it
describes configuration, not approval for an arbitrary call.

Job history is `{jobs, next_cursor}` with a bounded limit of 20. Job detail binds
its exact manifest, approval/deadlines/current validity, status, unknown-outcome
state and optional receipt. A receipt contains explicit proposal/request/
credential/reservation/approval/epoch bindings, bounded model/HTTP/finish/timing
facts and the derived assessment. It exposes no raw response body, generated
content or reason text; action/reason hashes and lengths remain inspectable.
Reported token counters in receipt JSON use canonical decimal strings to
preserve the full integer range in browsers.

`PAID_META_PROBE_ENABLED` defaults false. `NATIVE_BUDGET_ACCOUNT_ID` explicitly
selects the sole native ledger; there is no automatic account creation.
Key/model/credentials alone cannot turn
it on or enable research. A dedicated `MetaProbeWorker` claims only `verify_meta`
and performs at most one completion. Its normal CLI returns disabled before
building a database engine, claiming work or constructing a model. Never add CLI
endpoint/model/key overrides. An explicit `--finalize-only` mode may use
the database with no key/model/network and can consume only known journals.
Known in-flight observations always remain settleable independently of whether
the normal paid worker can start.

## Blocking facts and implementation verification

Before an actual probe, the owner must explicitly authorize paid work and supply
a real Meta key/model plus reliable native endpoint/wire, inclusive usage,
pricing, absolute billable bounds, credits and provider-cap evidence. None of
those facts is supplied by this design, a synthetic test or a previous model's
name. A deployment flag or operator click is not retrospective permission for
this agent to make a paid request.

Build and test using the actual adapter with `httpx.MockTransport`, PostgreSQL
and the fixed request. Prove byte/field hash binding, integer component rounding,
one grant under concurrency, no duplicate send after every response-loss point,
unknown holds, known late usage, role/flag/key/expiry changes during lock waits,
all stop-path fencing, ledger reuse across proposals/key rotations, reuse of a
credit attestation after earlier charges settle, generic
worker/scope rejection, no receipt forging, no false native/pricing/science
claims, disabled CLI before DB/model construction and free known finalization.
Use no live provider calls to obtain those tests.
