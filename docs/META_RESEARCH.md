# Native Meta research activation

This is the B3.3 implementation contract, verified locally with mock HTTP. Paid
calls are still forbidden in the current session; tests use the actual Meta adapter
with mock transport. No real key/model or native pricing/billing facts have been
supplied. A passed B3.2 probe is one protocol observation, not research activation
or proof of prices, exhaustive billing, scientific quality or available funds.

The first native audience is `operator_browser`: the current authorized budget
operator and the same browser that approved activation. Ask, Explore, Surprise,
Formalise, Write summary/paper and finding reassessment must all use the existing
`ResearchWorker` and their existing controllers with Meta as their model. There
is no second scientific controller, scripted answer generator, fallback model or
alternate provider.

An explicit optional `discovery_ladder_profile` extends activation for bounded
multi-stage Surprise research. It binds its own prompt/action/orchestration
hashes, shared call/action limits and stage/depth ceilings. Preview accepts
`discovery` with the exact intent configuration. The field is omitted completely
when absent; the original five task-profile and legacy manifest hashes remain
unchanged. Ordinary research never silently selects the ladder profile. An
opted-in run pins it from its immutable initial `discovery_intent`, and a fresh
approval is required; generated stages cannot change the original input hash.
One ladder uses the same existing run, worker and global spending ledger.
See [DISCOVERY_LADDER.md](DISCOVERY_LADDER.md) for exact stage/replay semantics.

Public-session access remains part of the full product scope. It requires the
separate D2 access/abuse controls before admission can spend this account. This
first activation must not be described as public paid-research readiness. Free
reading, public shared findings and scheduled literature ingestion retain their
existing boundaries; activation alone does not create automatic paid jobs.

## One exact activation on the existing ledger

`MetaResearchManifest` binds these facts:

| Input | Binding |
|---|---|
| Audience | Exactly `operator_browser` and the approving browser/operator |
| Native account | The existing server-configured `NATIVE_BUDGET_ACCOUNT_ID` |
| Readiness | Exact saved proposal UUID/hash, model, endpoint, USD inclusive usage, prices and absolute bounds |
| Protocol observation | Exact passed canary receipt/hash for this credential and exact model/endpoint |
| Current human review | Explicit fresh confirmation of model/wire/usage, pricing/bounds, credits and provider cap, with evidence and expiry |
| Credential | Server-computed domain-separated binding; never a raw key |
| Limits | Exact derived task profiles and their hashes, including controller protocol and deadlines |
| Account control | Preview epoch and explicit scoped kill-clear |
| Expiry | No later than browser, operator, proposal, current attestation and server consent limits |

Activation requires current human review even when the canary passed. The
operator reviews the exact quantitative proposal and its evidence, not an
unlabelled success flag. The historical canary supplies protocol evidence only;
it cannot supply a missing model, guessed price, token ratio, billable bound,
provider-credit balance or provider hard limit. A changed key/model needs an
appropriate passed observation for that exact identity. A fresh proposal can
update independently reviewed prices/bounds without rewriting the canary.

The local hard ceiling remains at most USD 150. The same immutable credential
and stable provider billing-account registry used by verification must resolve
to this same ledger across proposals and credential rotations. No new research
account, reset, copied balance or per-activation USD 150 allowance is created.
Every probe/research settled charge and reserved/in-flight/unknown hold remains
counted against the account's existing hard, daily and concurrency limits.

Both attested credit and remaining provider allowance conservatively cover the
entire committed ledger plus each new reservation. Do not reuse an attestation
as though settled charges disappeared when their holds cleared. Unknown work
blocks new activation and fresh native calls; it is never inferred free or
silently reconciled by this feature.

The explicit activation action saves immutable manifest/approval records and
changes only the native scope/binding, control epoch and reviewed kill-clear.
`native_scope='research'` is a separate internally enforced authority.
`execution_enabled`/`pricing_verified` remain unchanged; new prepared accounts
retain false. Generic reservation, claim, dispatch, legacy flag-setting and
kill-clear methods cannot escape that scope, even if old flags are accidentally
true. Kill/freeze, existing money limits and current ownership are never bypassed.

Use the same epoch rule as B3.2: preview `N`, activate atomically to `N+1`, approval
pins that armed epoch. Every stop, including no-op disable/kill calls, advances
the generation. An identical-manifest retry returns the existing activation
without rearming, renewing expiry or clearing a later stop. A replacement
activation fences all older fresh grants. It does not rewrite their request,
pricing, controller profile or recorded accounting.

## Admission inside the existing task transaction

The activation is not a global availability callback that makes anonymous jobs
paid. Each paid-capable submission needs a current matching activation and an
immutable `NativeResearchRunBinding` inserted in the same transaction as its run,
browser ownership, parent/artifact/assessment request basis, quota admission and
initial event. A caller cannot submit first and attach permission later.

The three enqueue surfaces are:

| Existing route | Scientific work |
|---|---|
| `POST /api/v1/runs` | Ask, Explore and Surprise (`task_kind='research'`) |
| `POST /api/v1/artifacts/jobs` | `formalise`, `write_summary`, `write_paper` |
| `POST /api/v1/assessments/jobs` | `reassess_finding` with its pinned prior finding/watch sources |

Each native submission passes the currently displayed activation hash along
with its existing scientific inputs. The client can attach the active receipt
automatically; there is no extra human approval for every model step. It still
needs the live operator cookie, normal browser Origin/CSRF and operator CSRF.
The operator/browser must match the activation. Current source ownership,
positive/current-head eligibility, immutable evidence and optimistic assessment
revision checks remain required; activation does not weaken them.

Use a transaction-scoped helper sequence:

1. `begin_admission(session, request, expected_activation_sha256)` authenticates
   and locks the current operator, validates both CSRF domains and returns a
   native admission bound to this browser/activation.
2. Acquire the existing shared queue advisory lock and recheck ownership/quota.
   The account admission stage then locks Account before any helper that might
   acquire a run/finding-head lock, and validates current authority after waits.
3. Build the normal scientific run/request with its exact immutable basis.
   `bind_run_in_session(session, admission, run, input_sha256)` rechecks exact
   ledger/epoch/key/funding under the already held locks and writes the immutable
   run binding and exact budget run limit. Commit everything together. The
   account-admission stage may be a separate explicit helper; do not hide an
   Account → Operator lock inversion in a post-queue callback.

The binding pins approval/manifest/hash, account/armed epoch, browser owner,
task kind/mode, canonical scientific intent/basis hash, actual task limits and
the original worker/provider policy needed for replay. An omitted, foreign,
stale or mismatched activation is denied in the native production path. Trusted
test/programmatic legacy admission remains explicitly separate and cannot be a
fallback for a failed native authorization check.

All enqueue paths that touch both authority and the queue use:

```text
Operator → shared queue advisory → Account → Run (then required finding-head work)
```

B3.2 already uses Operator → queue → Account. Introducing Account → queue in a
different admission path can deadlock across different operator sessions.
Activation/revocation that never touches the queue needs only Operator → Account.
Fresh model dispatch uses Operator → Account → Run. Late observations/accounting
use Account → Run and never acquire an operator afterward. Existing run-only
heartbeat/cancellation must not acquire an account/operator after its run lock.
Do not hold these locks over HTTP or scientific tools.

## One worker, original per-run policy

Native and legacy jobs have the same scientific task kinds. Their claim filters
must distinguish the presence of `NativeResearchRunBinding`: the native worker
claims bound runs, the legacy fixture worker claims unbound runs. Both exclude
free `ingest_literature` and dedicated `verify_meta`. A global worker enabled flag
does not authorize unbound jobs against a native account.

Extend the existing worker with an original per-run policy resolver and one
recorded-model construction seam. The resolver must run **before** budget run
configuration, worker fingerprint and controller construction. The current code
uses global `self.policy` for these operations; changing only the recorded-model
factory would configure old jobs with the latest account/limits and break replay.

For each native-bound run, reconstruct its original `WorkerPolicy`, effective
controller limits, model identity, exact request protocol and original pricing/
bounds from the immutable binding. Never put a raw credential in those values or
the fingerprint. The native factory remains lazy and only constructs the actual
`MetaModel` for a newly committed, currently authorized send. Cached replay needs
no current key/model, operator session or model construction.

The current controller bounds are:

| Controller | Existing default calls | Supported call ceiling | Output/context ceilings |
|---|---:|---:|---|
| `ResearchController` | 12 | 20 | 4096 tokens / 90000 characters |
| `ArtifactController` | 6 | 12 | 4096 tokens / 90000 characters |
| `AssessmentController` | 12 | 12 | 4096 tokens / 90000 characters |

Derive effective native call counts as the minimum of the reviewed proposal and
the supported task ceiling. Pin action/repair/tool limits too; research actions
remain bounded at 16, artifact/assessment at 12, and repairs at their existing
bounded allowance. Use the full reviewed output allowance and context limit
within those supported ceilings for every task. Do not accidentally retain
research's legacy 1200-token output default when the approved native profile
permits 4096. The budget run limit must match the effective controller call cap
and the reviewed run cap; it cannot be silently reconfigured from global defaults.

HTTP uses the full reviewed request timeout, at most 60 seconds. The controller's
outer deadline must also cover grant database work and durable observation/
accounting. Pin a local orchestration allowance of 30 seconds: the native outer
deadline is the HTTP timeout plus 30 seconds, at most 90 seconds. The
existing controller default of 40 seconds/max 60 is insufficient for a reviewed
45–60-second HTTP request. Implement this as a reviewed native deadline extension
without changing the provider's approved HTTP cap or old legacy fingerprints.

The controller protocol fingerprint hashes the actual static system prompt,
mode/kind instructions, action JSON schema and relevant excerpt instructions,
plus an explicit orchestration protocol version. An unversioned label alone is
insufficient. Compare that fingerprint with current code before fresh admission
or a fresh grant, so an old activation cannot silently authorize a changed
controller protocol.

Historical profile integrity is separate from current-code equality. Old
well-formed immutable profiles/journals must remain loadable for billing without
rebuilding their hashes from newer prompts or limits. The worker fingerprint
binds its original activation/run profile, task/basis, controller protocol and
effective limits. A new activation cannot cause an old
known response to miss its hash merely because a worker was constructed from
newer settings. Legacy request/checkpoint hashes remain compatible; new optional
native fields cannot be indiscriminately added to historical hash inputs.

## Native observation before scientific action parsing

Add a strict `MetaResearchRequest` for the existing controller messages: fixed
Meta HTTPS POST endpoint, exact model, bounded `system`/`user`/`assistant`
messages, total content at most the pinned context limit, exact allowed output
cap, and `stream=false`. No caller endpoint override, alternate provider or raw
credential is part of the request descriptor. Hash the exact full request and
bind it to the original run activation/profile and step ID.

`MetaModel.observe_research` reuses the same strict bounded observer as B3.2.
Keep the existing probe request/hash and historical `MetaModel.complete`
semantics compatible. A pure `assess_meta_usage` helper prices observed usage;
it does not apply a research/answer/artifact action adapter.

At each `NativeRecordedModel.complete` step:

1. Compute the deterministic exact step/request hash from the original profile.
   Replay an identical durable known observation first, without current
   credentials, factory construction or another reservation.
2. For a genuinely fresh call, the distinct native dispatcher locks Operator →
   Account → Run and rechecks current role/browser/policy, original live
   activation/epoch, manifest/key/model/ledger identity, limits, funding, lease
   and no pending unknown operation. Reserve the internally derived separately
   rounded ceiling components and consume one durable send grant atomically.
3. Use the exact credential snapshot validated by that grant to construct Meta
   and send once. Timeouts, cancellation, crashes and lost replies retain the
   full hold; they never trigger an automatic resend.
4. Journal bounded actual transport/model/usage facts and settle or retain the
   hold **before** turning the response into `ModelReply` or parsing any
   scientific action. Known over-bound usage is fully counted and freezes;
   missing/mismatched model, invalid usage or unrepresentable cost remains
   unknown under the B3.2 rules. Do not use legacy aggregate rounding.
5. Only then pass usable content to the original scientific controller. A known
   malformed action can use its existing bounded repair path, but every repair
   is a new fresh authorized/budgeted grant. An invalid finish can be billed
   before returning `ModelError`; unknown billing, bound violation or revoked
   authority stops fresh model work instead of becoming an in-prompt repair.

Free replay uses original prices and bounds, never the newest activation's
rates. Preserve original controller-visible content/usage exactly so compatible replay
reconstructs the same action/tool sequence. Model output and tool observations
remain untrusted data; they cannot change approval, prices, scope or audience.
The exact-evidence, source identity, full-text window, finite-check and
assessment-CAS validators continue to determine what can be saved as a result.
If deployed controller code is incompatible with an old request sequence,
scientific replay stops on the exact protocol/request mismatch. Its known
billing facts still remain independently finalizable; do not manufacture a
successful scientific continuation under changed prompts.

## Lifecycle and the small operational surface

The activation API lives under `/api/v1/budget/research`: capabilities, exact
preview, activate, current/history and explicit revoke. Every protected read
requires current budget authority; every mutation requires both CSRF domains.
Capabilities expose audience/configuration honestly, without a key, actor list
or an implication that a random browser can spend.

| Route | Exact operation |
|---|---|
| `GET /capabilities` | Session-free configuration status and `audience='operator_browser'`; public paid research remains false |
| `POST /preview` | `{readiness_id, canary_receipt_id, review, provider_account_attestation}` → exact manifest/hash; no activation |
| `POST /activations` | `{manifest, expected_manifest_sha256}` → immutable approval and scoped activation |
| `GET /current` | Only activation currently eligible for this same operator/browser, or null with a reason |
| `GET /activations` | Bounded administrative history/cursor |
| `GET /activations/{id}` | Exact saved activation and current validity/eligibility observations |
| `POST /activations/{id}/revoke` | Empty object; revoke/fence without releasing any prior charges or holds |

The ordinary private App composes the existing `GET /api/v1/operator/session`
with `GET /api/v1/budget/research/current` after opening its browser session.
Operator CSRF stays in memory. The current projection contains only activation
eligible for that same browser, with exact hash/deadline/audience/effective
profiles; another browser's scope does not make this one ready. A shared App
context supplies the operator CSRF and `expected_activation_sha256` to research,
artifact and assessment submission clients. Nothing is inferred from a prior
budget-tab visit or persisted in browser storage. Expiry, role loss or stale
activation clears paid admission; any refresh is GET-only and never resubmits a
write. Public/library routes retain lazy loading with no private session or
operator bootstrap. Global configuration availability and current browser
eligibility remain visibly distinct.

Scientific read/result/SSE and ordinary browser-owned cancellation remain the
existing task surfaces. A safe run-only cancellation fences that run before any
later grant without enabling account work. General account/activation stop still
uses the shared native epoch and preserves all settled/unknown costs.

Add one explicit `POST /api/v1/runs/{id}/resume` for native-bound interrupted
scientific runs, including artifact/reassessment tasks. Its body is
`{expected_lease_token, expected_activation_sha256}`; the lease token is a
canonical decimal string so browsers preserve its integer range. Require the owning browser, current budget
operator and both CSRF domains; the original activation must still be live and
at the same epoch. Lock Operator → shared queue advisory → Account → Run,
recheck expiry/authority after waits, apply the same owner/global active quotas,
and reject every prepared/unknown request. Only then return the same run to the
queue. There is no new approval, epoch change, deadline renewal, replacement
budget or automatic retry. Exclude `verify_meta` and unbound legacy runs from
this first resume route, and display that restriction honestly.

A replacement activation cannot silently rebind the original run. Known calls
can replay/settle after revocation, but free recovery promises retained billing
facts and cached observations only. It must not promise scientific completion
when another source read or model call is required. A live original activation
is needed for an operator to resume the ordinary scientific workflow.

`PAID_META_RESEARCH_ENABLED=false` is the default. The native research CLI,
`python -m polymath.native_research_worker`, exits before database/model
construction when disabled. It
uses the existing worker with the native policy resolver/recorded model, not a
new scientific worker loop. A known-only recovery path must load only database
configuration and cannot require removed keys, operator config or stale enabled
flags to validate. `--finalize-only RUN_ID --request-id model-0003` addresses one
exact retained call; it grants no new send and never reconciles an unknown call.
The approved request descriptor is saved immutably before its send, so free
billing binds the original wire hash without reconstructing newer prompts.

The implementation pins manifest/profile DTOs, admission helper signatures
and native deadlines across store/worker/API/UI. Regression checks cover
all three modes and all artifact/reassessment tasks through the real Meta
adapter with mock transport; role/queue/account wait races; single sends;
unknown holds; exact old-profile replay after activation changes; all-task
limits; no native/legacy claim crossover; one shared ledger; immutable exact
input/price/usage bindings; safe stop/revoke; and disabled defaults. No live paid
call is needed or authorized to perform that work.

Before changing roles, credentials or server policy in a deployment, disable the
account or revoke its activation to advance the database epoch, verify the stop, stop old
worker processes, then reload configuration. Rearming requires a fresh explicit
approval. Per-process configuration comparisons cannot instantly revoke another
process that still has old settings; the persisted epoch fences all workers.
