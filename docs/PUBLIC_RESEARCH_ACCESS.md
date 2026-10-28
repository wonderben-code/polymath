# Controlled public research access

Status: D2 implementation contract, 28 October 2026. The local API, native worker,
resource controls, browser and standalone client are implemented and locally
verified: 3,358 tests pass, plus isolated synthetic browser/client journeys. This does not enable public research,
authorize a paid request, or select a deployment/provider plan. Paid calls remain
forbidden. Existing private approvals retain their `operator_browser` scope and
hashes, as described in [META_RESEARCH.md](META_RESEARCH.md).

D2 lets an ordinary visitor use the website and HTTP API without an operator
credential, user account, payment method, Meta key or Zenodo key. The existing
research engine remains responsible for Ask, Explore, Surprise, the optional
[discovery ladder](DISCOVERY_LADDER.md), higher-level investigations, Formalise,
both writing tasks and finding reassessment. Every paid step still requires a
transactional native grant against the same global ledger.

## Explicit durable delegation

Unattended judging needs an authority lifetime different from the administrator's
login. Existing operator sessions last 20 minutes by default. Keep that behavior
and all `operator_browser` approvals unchanged. Introduce a separate public
delegation whose authentication is checked when it is issued and whose exact
spending deadline is reviewed explicitly.

The approval action must state:

> Allow public visitors to spend the displayed allowance until this exact UTC
> deadline, including after I sign out. Signing out does not stop this delegation.
> Stop public research or disable the account to prevent further model calls.

`continues_after_operator_logout` must be the strict literal `true`, supplied
only in this new approval contract. It is never inferred from a configured key,
server flag, passed canary, private activation or previous delegation. The UI
requires an explicit confirmation of the displayed manifest/hash; it sends no
request merely because the page opened or a checkbox changed.

Choose a maximum delegation lifetime of 24 hours for the first implementation.
The operator selects the actual deadline. It cannot exceed the proposal,
fresh human review, provider-funding attestation or server maximum. Authentication
must still be valid after lock waits and at issuance commit, but the public
deadline is not capped by the issuing login/browser's remaining lifetime.
There is no automatic renewal, background login or extension from visitor activity.

After issuance, ordinary logout, browser expiry and operator-session expiry do
not revoke this explicit durable grant. The issuing rows remain audit references;
they are not live public bearer credentials. Fresh calls still require the
configured sponsoring actor to retain the budget role, its original server
policy binding to match, and the public grant to remain current. Removing the
role or bootstrap configuration, rotating its policy/Meta credential, or disabling
public execution makes a process that has loaded that change reject new grants.
Replacing the activation, revoking the delegation, account disable, kill or
freeze provides the durable database fence checked by every worker. Never bypass
a kill/freeze or reconcile an unknown charge to keep the public service running.

Configuration files and environment variables are process-local facts. Editing
them does not instantly update a running worker, and an unobserved file change
is not revocation. The first implementation uses the existing account control
epoch as its durable configuration-generation fence, with this mandatory rollout
sequence:

1. Using the still-valid operator controls, explicitly disable the native account
   or revoke its public delegation **before** changing a sponsoring role,
   bootstrap/public-policy setting, Meta credential or relevant deployment
   configuration. Wait for the committed database stop and verify the advanced
   epoch/current ineligible state. The audit retains this stop; no spend or hold
   is reset.
2. Stop the old worker/API generation, then apply and load the new configuration.
   Any remaining old worker still sees the database epoch mismatch and cannot
   make a fresh grant. An already committed send may finish and be accounted.
3. Enabled services/workers validate the original scope, epoch and their loaded
   policy before fresh admission/dispatch. Startup never clears the stop, swaps
   credentials into an old grant or rearms an old approval. A current operator
   must explicitly review and activate a new delegation to resume public work.

Deployment must refuse a policy/credential rollout without the prior stop; if
the role/bootstrap is to be removed, perform that stop while it is still
available. A disabled worker CLI continues to exit before database/model
construction, so it cannot be used as an implicit configuration-sync daemon.
Automatic startup reconciliation is outside this initial design. Emergency
cross-worker closure uses the database stop controls, not a promise that all
processes reload settings simultaneously.

An already committed send cannot be unsent. Late observations and original-price
accounting remain allowed after closure, using Account → Run locks and no live
login/key. Free known-call finalization retains the current DB-only startup path.
An unknown journal/hold stays unknown; a diagnostic receipt does not release it.

## Versioned manifest and immutable identities

Add a new strict `PublicResearchManifest` protocol
`polymath-meta-public-research-v1`, with `audience='public_sessions'` and
`authorization_mode='durable_owner_delegation'`. Preserve the existing private
manifest, absent optional fields, canonical JSON and historical hashes exactly.
Do not reinterpret a private approval as public authority.

The public manifest contains:

| Binding | Exact facts |
|---|---|
| Quantitative basis | Existing readiness UUID, exact proposal/hash, model, fixed Meta endpoint, USD inclusive usage contract, attested rates and absolute billable bounds |
| Protocol observation | Exact passed canary receipt/hash for the same credential/model/ledger; protocol evidence only |
| Current human review | Explicit wire/usage, pricing/bounds and funding/cap confirmations, evidence hashes, UTC observation and expiry |
| Credential/account | Server-computed credential binding, existing configured native account and stable provider-billing registry |
| Scientific limits | The five existing task profiles; an optional explicitly reviewed discovery-ladder profile omitted when absent |
| Public delegation | Strict spending-after-logout confirmation, exact deadline and full `PublicLimits` |
| Current control | Previewed native epoch, explicit scoped kill-clear, sponsor-policy and abuse-policy bindings |

Canonicalize using the current sorted-key, compact, UTF-8 JSON convention with
`ensure_ascii=False` and `allow_nan=False`. The hash excludes its own database
UUID, approval/run IDs, volatile balances and receipts. Recompute every derived
field and profile on the server before approval.

Reuse the existing manifest/approval store with a strict protocol/audience parser
and an explicit authorization-mode discriminator. Existing approval rows default
to the historical live-operator interpretation without rewriting their JSON.
Public approval rows retain these immutable facts:

- Exact manifest ID/hash, account, actor and approved/deadline timestamps.
- Issuing operator-session and browser hashes, retained only as issuance audit.
- Server policy fingerprint, reviewed public policy and resulting armed epoch.
- `authorization_mode='durable_owner_delegation'` checked against the manifest.

The arm uses the existing epoch rule: preview `N`, approve to `N+1`, bind that
epoch and current manifest to the existing native account. Generic execution and
pricing flags remain unchanged. Identical-hash retries return the original
approval without renewing its deadline, changing quotas or clearing a later stop.
A replacement fences old fresh calls; it cannot move old runs to the new grant.

Separate the two browser identities in every public run:

| Identity | Authority |
|---|---|
| Sponsor operator/browser | Proved the human approval at issuance; identity and policy remain privately auditable |
| Requesting `BrowserSession` | Owns this run, its inputs, stages, results, sources, events and cancellation |

Keep the normal immutable `NativeResearchRunBinding` for original manifest,
proposal/profile, model, prices, task/mode, input hash and epoch. Its browser hash
is the requester. Add an immutable `PublicResearchRunAccess` row keyed by run ID,
binding the same approval/hash, requester hash, server-derived network bucket,
admission time and public-quota policy. A public run requires both exact rows;
a private run must not acquire public treatment from an optional request flag.
The existing private check that requester equals approving browser remains in
the private branch only.

No public request can name an actor, grant itself an audience, select a ledger,
upload a credential, alter a price/profile or attach permission after enqueue.
Discovery intent and scientific basis remain part of the original input;
generated stages never become initial input or replenish a quota.

## One ledger and non-resetting public limits

`PUBLIC_META_RESEARCH_ENABLED` defaults to `false`. Public admission requires
that flag, native-research configuration, a current explicit public approval and
all visitor/resource checks. Default construction has no delegation and cannot
claim paid work or construct Meta. No default monetary allowance is invented.

`PublicLimits` requires these positive integer microdollar ceilings explicitly:

- Total public allowance on this account and public UTC-day allowance.
- Total allowance for one seven-day visitor session and its UTC-day allowance.
- Existing reviewed per-run cap and per-call reservation remain independent bounds.

Each public allowance must fit the corresponding existing account cap; session
limits fit public limits, and an eligible session must have room for at least one
conservative call. Preview shows the derived reservation and whether a complete
maximum-call run fits. Acceptance does not promise that every run will finish.

These are projections of the existing `BudgetReservation` facts joined to
immutable public run bindings, not another balance or funded account. Count
settled actual amounts and the complete estimates for reserved, in-flight and
unknown work. Count all prior public delegations on this account for the public
total; reactivation, logout, a new proposal, midnight, cancellation and key
rotation do not reset that total. Per-session totals follow the same owner hash
across delegations. Daily accounting uses the ledger's existing conservative
day/hold rules. Global caps still count private research and the canary too.

Before every send, under the account lock, enforce the lowest applicable account,
daily, run, public and session ceiling, plus existing concurrency/call limits and
the attested provider funds. Derive reservations from the saved proposal, never
from a visitor estimate or visible text length. Reported valid overages are fully
counted and freeze as today; do not clamp them to a public allowance. Unknown
work continues to block fresh native calls and keeps its full hold.

The reviewed manifest also carries operational limits. Use these conservative
initial defaults, allowing only explicitly reviewed values within server ceilings:

| Control | Initial limit |
|---|---|
| Active scientific jobs per visitor | 1, including interrupted work, preserving the current shared owner rule |
| Public jobs per visitor / UTC day | 12 across all paid task kinds |
| Public jobs / account / UTC day | 50 |
| Public active jobs / account | 5, including queued/interrupted work |
| Queued public jobs / account | 4, inside the existing shared global limit of 100 |
| Active public jobs per network bucket | 2 |
| Submit attempts | Visitor 6/minute; network 10/minute; account 30/minute |
| New owner-session minting | Network 10/hour and 50/day; service 200/hour and 1,000/day |
| Inexpensive owned reads | Visitor 60/minute; network 240/minute; service 600/minute |
| Concurrent SSE streams | Visitor 2; network 4; service 20 |
| One SSE connection | At most 300 seconds; reconnect preserves the last event ID |

Operational defaults are resource controls, not assumed provider prices or proof
of hosting capacity. Their exact effective values and policy version are hashed
in the public approval. A stricter deployment policy can close admission; it
cannot silently enlarge an existing approval. Job-count limits remain necessary
even when a valid provider response reports zero tokens/cost.

Treat failed/invalid submit attempts as rate-limit traffic. Count successful job
admissions transactionally against job quotas; duplicate read requests never
create jobs. Queued jobs also expire at the delegated deadline. A bounded cleanup
can cancel expired queued public work while preserving all results, stages and
holds. Paused/unknown jobs never auto-resume. Cheap owned cancel/pause/revoke
controls use a separate rate bucket so a submit quota cannot prevent stopping.

Anonymous cookies and network limits do not establish unique humans. Cookie
clearing or a distributed attacker can exhaust availability; the independent
account/public money and job ceilings still bound exposure. Do not claim complete
bot resistance, add accounts/payments, or assume a paid CAPTCHA/edge service.

## Admission and fresh dispatch

Public visitors send the existing owner cookie, browser CSRF and same-origin
Origin. They never receive or supply operator cookies/CSRF. Admin issuance and
stop controls retain current operator authentication, explicit budget role and
both CSRF domains.

Use the existing scientific endpoints and transactional sequence:

1. Apply the short request/body/rate preflight and release those locks.
2. Resolve the exact public approval and lock its issuing operator row as the
   stable sponsor serialization point. Check durable mode, current actor role
   and policy binding; do not require that old login to remain live.
3. Lock the shared queue advisory, then Account, then the new Run and required
   scientific basis/head rows. Recheck requester expiry, public deadline, epoch,
   all quotas and native facts after waits.
4. Insert the normal run, owner, parent/artifact/assessment basis, optional initial
   discovery intent, native binding, public access binding, run limit and initial
   event in one transaction. Recheck authority and allowance before commit.

Fresh worker dispatch uses Sponsor Operator → Account → Run. It checks the same
durable sponsor policy and public deadline, the actual requester session, original
input/profile, key/model, registry, epoch, funding, quotas, lease and absence of
unknown/prepared work. A new grant captures the exact validated secret/model/
timeout in memory for the lazy native factory. Never acquire an operator after
an account/run lock or hold locks over HTTP.

The initial five active public jobs mean at most one running native job and
four waiting jobs on this account. Serialize native claims with a short
account-scoped advisory transaction lock and a fresh running-job/unknown-work
check before changing a queued run; release that lock before research. Another
worker leaves the next run queued instead of claiming it and immediately
interrupting at the existing prepared-call guard. Expired leases follow the
existing explicit interruption/recovery rules. Do not weaken single-send or
unknown-work checks to manufacture concurrency. Public queue admission may
accept bounded waiting work during a known in-flight call; it reserves no money
and promises no later grant. Unknown work pauses admission and dispatch.

Late facts remain Account → Run, with original prices and no live grant renewal.
The native worker still claims only bound runs and the legacy worker excludes
them. Reuse the current recorded model, controllers, stage sink, source/memory
validators and global per-run counters. No public-specific scientific engine,
scripted result, fallback model, financial reset or automatic paid follow-up.

## HTTP contract and clients

Introduce the strict public preview/activate shape under the existing budget desk:

| Route | Request/response and authority |
|---|---|
| `POST /api/v1/budget/research/public/preview` | Current operator + both CSRF domains; existing readiness/canary IDs, review, funding attestation, optional discovery intent, explicit public limits and `valid_until`; returns exact public manifest/hash; no arm or model |
| `POST /api/v1/budget/research/public/activate` | `{manifest, expected_manifest_sha256}` with current budget authority; exact durable approval/arm, idempotent by manifest hash |
| `GET /api/v1/budget/research/public/activations` | Bounded current-budget-operator history, cursor/limit at most 20 |
| `GET /api/v1/budget/research/public/activations/{id}` | Current budget operator; exact immutable private facts and current control state |
| `POST /api/v1/budget/research/public/activations/{id}/revoke` | Current budget operator + both CSRF domains; explicit stop, native epoch fencing, holds retained |
| `GET /api/v1/research/access` | Existing requester session only; no session creation, grants, model/provider calls or scientific/budget writes; operational read-rate counters apply |

Public administration history and stop remain available when public execution
is off, provided the current budget role is configured. Existing account disable
is the account-wide emergency stop. The UI places that stop beside the active
deadline and labels ordinary logout as leaving durable public spending active.

`GET /research/access` returns this bounded projection:

```text
protocol: "polymath-research-access-v1"
audience: "public_sessions" | "operator_browser" | null
available: boolean
reason: static code | null
expected_activation_sha256: lowercase SHA256 | null
expires_at: UTC timestamp | null
limits: public task/visitor limits | null
remaining_session_allowance_microdollars: decimal string | null
remaining_session_jobs_today: integer | null
retry_after_seconds: bounded integer | null
```

The public `limits` projection contains only session total/daily monetary caps
as decimal strings, `max_jobs_per_session_day`, `max_active_jobs_per_session:1`,
the five `task_kinds`, and a nullable `discovery_intent` ceiling. It contains no
rates, native task-profile internals, issuer or account facts. Available public
access requires a complete hash/deadline/limits binding and known own counters.
Closed public access may retain that binding with nullable unknown counters;
an absent binding has no counters. The private `operator_browser` projection is
always unavailable with `operator_required` and no private activation metadata;
the existing privileged current-activation route supplies private authority.

`available` describes current funding authority and financial room, not a
promise that a new admission will pass queue/rate/job-count checks. Exhausting
the visitor's daily new-job allowance disables new submissions without consuming
another admission for explicit original-run resume. The original activation,
financial limits and unknown-work checks still apply to that resume.

The hash identifies the displayed public version; it is not a bearer credential.
Never expose account IDs, issuer identities, operator/session hashes, key
bindings, private rates/attestations or other visitors' usage through this route.
Owned per-run cost is labelled counted cost at attested rates, not an invoice or
visitor payment. `/capabilities` remains a configuration signal; it does not grant
session authority or bootstrap a cookie.

The paid submission bodies keep `expected_activation_sha256` on the existing
`POST /runs`, `/artifacts/jobs` and `/assessments/jobs`. All task kinds use the
same access projection and public limits. Do not force a public visitor through
operator login. `POST /runs/{id}/resume` retains exact original activation and
lease token, owner+CSRF, quota checks and no unknown/prepared work; its public
branch verifies the durable sponsor internally. Private operator-only resume
continues requiring the request's live operator and operator CSRF.

Reads, events, source inspection, retained stages, ordinary owned cancellation
and exact exports remain usable after paid closure until requester expiry.
A funding deadline does not become `session_expired`. Cross-owner access still
returns a generic 404. Public/shared pages continue omitting cookies and never
opening private sessions. Rate/stream counters are operational metadata; reading
creates no jobs, findings, grants or financial reservations. Public research does not auto-share private inputs or
grant publication, operator, budget editing or unknown-cost reconciliation rights.

The web App opens its normal owner session, loads access with GET and supplies
the in-memory displayed hash to all paid workspace clients. Expiry/revocation
clears admission and shows the actual reason/deadline. Refresh is GET-only; it
does not resubmit a failed write or select a newer approval for an old run.

Extend the stdlib client with `research_access()` and optional expected-hash
arguments. During an explicit create/resume only, preflight the owner cookie,
read access and attach its hash before one POST. A stale-version error never
triggers an automatic second POST. Preserve explicit legacy-server support only
when capabilities affirm that no native binding is required. Preserve the
0600 origin-bound cookie jar, ephemeral CSRF, TLS verification, no redirects,
bounded SSE and exact export hashes. No model/ledger/key/admin flags are added.
All existing read-only/default-help and cookie-free public commands remain so.

## Resource gates and honest stops

Use PostgreSQL atomic rate buckets/stream leases shared across API processes;
process-local counters alone are insufficient. Increment attempt counters in a
short independent transaction so malformed/rejected submissions still consume
their rate allowance. Release them before Sponsor → Queue → Account locks.
Successful job quotas and money checks belong in the admission/grant transaction.
Resume consumes an attempt rate allowance, not another successful-job allocation.
The existing one-active-owner rule includes interrupted jobs and is never widened
for public access, stages or downstream tasks. It also protects publication and
source/alias chronology. The owner can cancel an interrupted job using the
existing safe control; that does not erase its unknown hold or grant another send.

Network identity comes from the configured trusted transport peer. Never trust
arbitrary `Forwarded` or `X-Forwarded-For` in application code. Deployment must
pin its actual reverse-proxy trust configuration. HMAC the canonical IPv4 address
or IPv6 /64. Derive the network HMAC key from the existing strong server bootstrap
secret using a separate `polymath-public-network-key-v1` domain; hash peer buckets
under `polymath-public-peer-v1`. Keep both the secret and raw address out of quota
rows and expire rate metadata. After the required epoch stop and process reload,
bootstrap rotation invalidates the old public policy binding; it may reset short
rate buckets, never
financial or admission history. A proxy that hides every visitor behind one
unverified peer fails closed into that shared bucket rather than accepting
caller-supplied identity.

Enforce a 64 KiB body ceiling before parsing public task submissions, preserving
the existing field bounds and sanitized 422s. Administrative proposal/manifest
routes retain their separately bounded larger contracts. Bound stream leases,
connection lifetime and read rates without changing event IDs or exposing private
metadata. Expired stream leases are recoverable after a process crash.

Rate-limit watch create/check/resume too: an unanchored watch can cause source/
storage work without spending Meta credit. Keep those free-write quotas separate
from paid admission. Share provider pacing across scientific research and feed
ingestion, including the existing arXiv gate; honor real provider requirements
and explicit partial/rate-limited diagnostics. Do not invent paid provider access,
fetch arbitrary source URLs or silently substitute a provider.

Return bounded static failures with `no-store`, `nosniff`, no-referrer and no input
echo. Use 429 plus `Retry-After` for rate/queue/day quotas, 409 for stale public
version or incompatible resume, and 503 for disabled/closed funding authority or
unknown-cost pause. `Retry-After` is an earliest retry time, not a promise of an
active delegation. A total cap has no automatic midnight reset.

Persist precise run stop codes for budget/session quota, deadline, kill, unknown
cost and interruption. Budget exhaustion stops further grants and retains actual
partial evidence/stages/activity; it does not manufacture an insufficient-evidence
finding or select the latest ladder stage. Whole-run usage/holds remain distinct
from a selected stage's historical prefix. Cancellation frees local queue control
where safe but never erases charges, releases unknown holds or resends a request.

## Minimal deliverable and acceptance

Implement D2 in four connected slices:

1. Versioned durable delegation/approval, separate immutable requester bindings,
   current-state public guard and same-ledger quota projections.
2. Persistent session/attempt/read/SSE gates, safe proxy identity and free-source
   quotas/pacing; bounded expired-queue cleanup and explicit stop reasons.
3. Existing three enqueue/resume surfaces, access projection, budget-desk approval/
   stop controls, ordinary App admission and compatible external client.
4. Real PostgreSQL/concurrent and loopback HTTP/browser verification using actual
   Meta with mock transport, followed by separately authorized deployment checks.

The initial deliverable includes every existing scientific task and the optional
ladder when reviewed. It has no user accounts, payments, public operator token,
automatic publication, unbounded background paid work or guessed hosting plan.

| Test group | Required evidence |
|---|---|
| Defaults/authority | Flags/key/canary/private approval alone cannot admit public work; no DB/model construction from disabled worker startup |
| Durable lifetime | Issuance requires a live role after waits; public work survives ordinary logout and both issuer expiries only until its exact approved deadline; no automatic renewal |
| Stops | A reloaded process rejects removed role/policy or rotated credentials; the mandatory prior database epoch stop also fences an unchanged old worker. Revoke, replacement, no-op kill/disable and freeze prevent later grants across workers; one already granted send is honestly retained |
| Visitor ownership | Two independent anonymous clients; no operator cookie; strict CSRF/Origin; foreign run/stage/source/result/export/resume cannot leak data |
| Monetary races | Concurrent last quota/call reservations; all public/private/canary spend and unknown holds share one account; full representable overage counted; no reset on reactivation |
| Abuse/resource races | Cookie rotation, zero usage, spoofed forwarding, network/day rollover, body bounds, final job slots and stream leases; failed attempts count; cancellation remains available |
| All scientific tasks | Ask/Explore/Surprise/ladder, higher-level, Formalise, summary/paper and watch-backed reassessment through the existing worker/native adapter with mock HTTP |
| Recovery | Late known facts and DB-only billing after all authority is removed; unknown never resent/reconciled; exact old-profile replay and stage acknowledgement recovery |
| Client compatibility | Current stdlib create/status/events/replay/cancel/result/export with only owner cookie+CSRF; single POST on unknown outcome; public reads/help create no session |
| Deployment gate | Actual HTTPS/proxy identity, SSE buffering/disconnects, persistence/restart, storage/backup recovery, owner stop and bounded funding closure verified on the chosen host |

Actual Meta identity/protocol, billable bounds, pricing, credits, provider cap,
literature-provider access/fees and deployment/hosting costs remain unverified
external prerequisites. Select no paid plan and make no live request under this
design. A local fixture, saved example or synthetic browser journey is labelled
as such and cannot establish native science, invoice accuracy or public deployment.
