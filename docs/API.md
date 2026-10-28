# Polymath HTTP API v1

The web workspace and external clients use the same `/api/v1` routes, ownership
checks, evidence and job controls. Public reads do not start research. The
exported application disables research execution even when a model key exists;
Meta access, real pricing/account limits and the owner's paid-call authorization
remain prerequisites. Local client tests use a real HTTP server and synthetic
workers, not native model or literature calls.

The API is served from the application's origin. Use HTTPS outside loopback
development. Credentials belong in server settings; clients never need a Meta
or Zenodo key. `/openapi.json` describes mounted request routes. The scientific
result, source-range and public projection contracts are described below and in
[FULL_TEXT.md](FULL_TEXT.md) and [PUBLIC_SHARING.md](PUBLIC_SHARING.md).

## Ownership and requests

`GET /api/v1/session` explicitly creates or reuses an anonymous seven-day owner
session. The response contains `csrf_token` and `expires_at`; the response cookie
`polymath_session` is HttpOnly, SameSite=Lax, scoped to `/api/v1` and Secure over
HTTPS. Only its hash is stored in PostgreSQL. The cookie is an owner capability:
another session cannot read the first session's investigations. General accounts,
API keys and cross-device recovery are not implemented.

For a private write, retain the cookie and send the returned CSRF value as
`X-CSRF-Token`. Send `Origin` equal to the API origin. Cross-site writes and
mismatched CSRF fail. Do not put cookies or CSRF values in URLs, source control,
logs or shell arguments. Public read clients omit cookies entirely and do not
call `/session`.

Every private/public API response uses `Cache-Control: no-store`, including
errors. Shared pages and endpoints also send `Referrer-Policy: no-referrer`,
`X-Content-Type-Options: nosniff` and `X-Robots-Tag: noindex, nofollow`.

## Research and results

| Method and path | Contract |
|---|---|
| `GET /health` | Process health; no database or model call |
| `GET /readiness` | Database connectivity and exact migration-head readiness |
| `GET /capabilities` | `research_available`, `meta_configured`, `reason`; a configured key is not execution authority |
| `POST /runs` | `{mode, goal, parent_result_ids?, discovery_intent?}`; owner+CSRF; returns queued run summary with HTTP201 |
| `GET /runs?limit=20&before=<run UUID>` | Owned research history; limit1–50; `{runs, next_cursor}` |
| `GET /runs/{run UUID}` | Owned status summary; no checkpoint, lease token or raw model state |
| `GET /runs/{run UUID}/events` | Owned persisted activity stream; see replay below |
| `POST /runs/{run UUID}/cancel` | Owner+CSRF cancellation; cannot unsend an in-flight external request |
| `GET /runs/{run UUID}/result` | Immutable saved research result and actually observed sources;404 until available |

Paths in this and subsequent tables are relative to `/api/v1`. Modes are
`explore`, `ask` and `surprise`. A trimmed goal is required for Explore/Ask and
may be empty for Surprise; the request string is limited to4,000 characters before trimming. Surprise lets
the research model select fields. Optional parent selections are2–4 distinct
canonical result UUIDs, only for Explore, and must be older owned completed
current-supported bridges. They pin earlier research context, not independent
corroboration.

Run summaries include `id`, `mode`, `task_kind`, `goal`, `status`, timestamps,
`result_id`, `artifact_id`, `error_code` and `external_outcome_unknown`. Read the
actual returned status rather than inferring success from an HTTP201 or stream
closure. Jobs can be queued, running, waiting_retry, cancel_requested,
cancelled, succeeded, failed or interrupted. An interrupted or ambiguous external
request requires the existing operator/recovery process; clients must not
recreate it automatically.

One active job per anonymous owner and100 globally bound the shared queue.
Admission also requires a live owner and enabled server policy. Ledger/token
limits remain separate checks at each worker dispatch. These controls do not
constitute complete production abuse protection.

A Bridge result preserves its mapping assumptions, objection/verdict, novelty
limitations and exact verified quotations. An Ask result preserves the original
question and individually cited Known/Postulated/Speculative claims, derivations
and caveats. Those labels are model assessments. Source IDs, hashes and Unicode
offsets establish saved quote integrity, not scientific truth or a formal proof.

Surprise may explicitly opt into `discovery_intent`:
`{protocol:"polymath-surprise-ladder-v1", max_stages:3, max_depth:2}`.
The bounds are1–4 stages and1–3 local depth; native admission additionally
requires a freshly approved matching ladder profile. The immutable initial
intent is returned only when present. Other modes reject it. Omitting it keeps
the ordinary protocol and response shape. The model can stop below either
ceiling; no new allowance is created per stage.

| Owned ladder read | Contract |
|---|---|
| `GET /runs/{run UUID}/discovery` | Whole-run stage references, explicit final selection, actual stop state, cumulative usage and accounted/held costs |
| `GET /runs/{run UUID}/discovery/stages` | Bounded committed-stage details, including partial/cancelled/interrupted runs |
| `GET /runs/{run UUID}/discovery/stages/{stage UUID}` | Exact stage receipt, normal saved result, parent edges, current provenance status and primary-paper groups |

These reads require the existing owner cookie and make no model/source calls.
A committed stage uses an ordinary finding result UUID. The owned finding
version reader adds `discovery_stage` with its exact full stage detail; its
head response adds a minimal stage binding/status only for a genuine committed
stage. Knowledge findings carry that same minimal binding and separate
`discovered_from`/`revised_from` ancestry edges. An uncommitted positive row
cannot become eligible through a later containing-run success.

Only an explicit model finish selects the final result. Partial stages remain
available through their owned stage/finding readers even when the ordinary
final-result route has no selection. Stage usage is an immutable commit-time
prefix; the ladder summary contains whole-run totals. `needs_review` preserves
saved content but denies fresh parent selection, generation and sharing.
Stage ceilings, local graph depth, integrity checks and uncalibrated confidence
scores do not establish scientific truth. See [DISCOVERY_LADDER.md](DISCOVERY_LADDER.md).

Full-text coverage identifies an exact versioned arXiv extraction and saved
window. Offsets are Unicode codepoints, zero-based, with an exclusive end; they
are not JavaScript UTF-16 indexes or PDF page numbers. Opening an index is not a
source read. Multiple windows/aliases of one paper are not independent papers.

## Activity and replay

Use `Accept: text/event-stream` on `/runs/{id}/events`. Set `after=<sequence>` or
`Last-Event-ID: <sequence>` to resume after a previously received event. When
both are supplied, the server uses the larger cursor. Cursors are integers
between0 and2^63−1. Activity IDs are persisted ordered sequence numbers.

```text
id: 12
event: activity
data: {"sequence":12,"kind":"source_read","summary":"Source text read","artifact_refs":[]}

event: end
data: {}

```

An `activity` contains its sequence, kind, concise public summary and artifact
references. `heartbeat` keeps an idle connection observable. `end` means a
terminal run's current events drained; it does not itself mean success.
`session_expired` ends lost authorization. An `error` event reports a static
storage failure. A disconnected browser/client does not cancel the job.

Persist the last activity ID after processing it. Reconnect explicitly with that
cursor to avoid duplicate display. Place independent byte/frame/event/time
bounds on a client stream; do not treat a stream failure as permission for a new
research POST. The reference client uses bounded single connections and no
automatic write retry or reconnect loop.

## Reference client

`scripts/polymath-client.py` uses only Python's standard library. Running it with
no arguments, or `make api-client`, prints help without making a request. Set
`POLYMATH_ORIGIN` to the intended Polymath origin, not the Meta endpoint.

Public reading needs no cookie jar:

```bash
python3 scripts/polymath-client.py --base-url "$POLYMATH_ORIGIN" public library --query 'error correction'
python3 scripts/polymath-client.py --base-url "$POLYMATH_ORIGIN" public map
python3 scripts/polymath-client.py --base-url "$POLYMATH_ORIGIN" public finding '<public-id>'
```

Private CLI commands require `--cookie-jar` to retain their owner capability
across invocations. They fail before making a request if it is absent. The
importable client can also retain a session in memory across calls. The
example directory below is ignored by Git. Keep it private and do not share its
contents:

```bash
mkdir -m 700 .polymath-client
python3 scripts/polymath-client.py --base-url "$POLYMATH_ORIGIN" --cookie-jar .polymath-client/owner.cookies session init
python3 scripts/polymath-client.py --base-url "$POLYMATH_ORIGIN" --cookie-jar .polymath-client/owner.cookies runs list
```

The jar is origin-bound and saved atomically with mode0600. Public
commands never load or send it. Private writes fetch CSRF in memory and reject
unexpected owner rotation before sending the POST. The client refuses redirects
and verifies TLS; plain HTTP is limited to local development. No command changes
server settings or accepts model/publisher credentials. Protected-file handling
is tested on Linux. The same standalone client is included in the application
image at `/app/scripts/polymath-client.py`.

Socket/stream deadlines bound network reads and interrupt stalled connections.
Operating-system DNS lookup cannot be cancelled by this client and may finish
after the requested timeout. If connection setup has already exhausted the
deadline, the client closes it before sending any HTTP request bytes.

Once a server is explicitly authorized and enabled for research, the following
is an explicit new investigation. It remains unavailable on the exported
default-disabled server:

```bash
python3 scripts/polymath-client.py --base-url "$POLYMATH_ORIGIN" --cookie-jar .polymath-client/owner.cookies runs create --mode explore --goal 'Error-correcting codes'
python3 scripts/polymath-client.py --base-url "$POLYMATH_ORIGIN" --cookie-jar .polymath-client/owner.cookies runs show '<run-uuid>'
python3 scripts/polymath-client.py --base-url "$POLYMATH_ORIGIN" --cookie-jar .polymath-client/owner.cookies runs events '<run-uuid>' --last-event-id 12 --max-events 100 --max-seconds 30
python3 scripts/polymath-client.py --base-url "$POLYMATH_ORIGIN" --cookie-jar .polymath-client/owner.cookies runs result '<run-uuid>'
```

Ask uses `--mode ask`; Surprise uses `--mode surprise` and permits an empty
goal. `--goal-file PATH` or `--goal-file -` accepts a bounded file/stdin goal.
Higher-level Explore accepts repeated `--parent-result <result-uuid>` options.
`runs cancel <run-uuid>` is an explicit write. There is no automatic create,
resume, write retry or paid-call recovery command.

`artifacts show <artifact-uuid>` reads metadata. `artifacts export
<artifact-uuid> --output PATH` verifies downloaded Markdown bytes before an
atomic private file save; `--output -` emits the verified bytes to stdout.
Do not use an API result or artifact UUID as a public share ID, or infer a
scientific result ID from a generation job's run UUID.

## Generated artifacts

| Method and path | Contract |
|---|---|
| `POST /artifacts/jobs` | Owner+CSRF; `{kind, source_result_id, formalisation_id?}` pins an exact current finding |
| `GET /artifacts/jobs` | Owned generation history; optional source-result filter, limit and cursor |
| `GET /artifacts/jobs/{run UUID}` | Generation status; use the same run event/cancel routes |
| `GET /artifacts/{artifact UUID}` | Owned exact structured preview, original passages and saved hashes |
| `GET /artifacts/{artifact UUID}/export` | Exact saved UTF-8 Markdown bytes |

Kinds are `formalise`, `write_summary` and `write_paper`. The resulting
`artifact_id` is distinct from a scientific `result_id`. Model proof attempts
and finite checks retain their original labels. Export clients compare the
downloaded bytes' SHA-256 with `markdown_sha256` from the exact artifact metadata
before saving; do not rerender Markdown or trust a caller-supplied filename.
Generation gives no publication authority.

## Shared public snapshots

`GET /public/library`, `/public/map` and `/public/findings/{public_id}` use no
session or CSRF token. The library accepts a literal `q` of at most200
characters, limit1–20 and an opaque public-ID cursor. A public ID is a43-character
random URL-safe resource identifier, distinct from every private result/run ID.

Public detail returns `public_id`, `projection_sha256`, the exact approved
`projection`, timestamps and a derived `current`, `superseded` or `unsupported`
badge. Library/map discovery includes only current supported active shares.
Content never changes to a newer private finding. Revoked/expired/disabled
handles give the same generic404. Public views expose complete model findings,
all citation associations, bounded short excerpts and reconstructed scholarly
links; raw paper bodies and private research history are excluded.

Owner preview, explicit hash-bound sharing, idempotency, history and revocation
routes are documented in [PUBLIC_SHARING.md](PUBLIC_SHARING.md). Public reading
grants no private source-reading, memory, generation or publication authority.

## Failures and retries

| HTTP status | Meaning and client action |
|---|---|
| `400` | Malformed `Last-Event-ID` header; correct it (invalid `after` query values give422) |
| `401` | Private session absent/expired; explicitly initialize a new session only if intended |
| `403` | CSRF/origin or privileged-authorization failure; correct authority |
| `404` | Foreign/missing resource, unavailable result or inactive public handle; no existence disclosure |
| `409` | Exact input/hash/current-version conflict; inspect and preview fresh inputs |
| `422` | Invalid bounded request; private input is not echoed |
| `429` | Active-job/share quota reached; inspect existing work and wait or cancel/revoke deliberately |
| `503` | Disabled execution/sharing or unavailable storage; inspect capabilities/readiness |

Operation errors generally contain a static `detail` code. Readiness503 retains
its structured `status`/`checks` response; an SSE storage failure arrives as an
`error` event with a static `code` inside an already-open stream. Clients should
not log arbitrary error bodies or HTTP headers. A timeout after sending a write may occur after
the server commits it. Research/generation creation has no general client
idempotency key: inspect owned history rather than blindly resending. Sharing
has its own exact request key; reuse the same key/body to resolve a known pending
share, and never reinterpret a failed response as a new public grant.

Budget readiness preparation/inspection/disable is documented in
[META_READINESS.md](META_READINESS.md). It requires a separately allowed operator
with both CSRF domains; proposals are not native proof or spending grants.
The separate one-call Meta approval, job, cancellation, receipt and free
finalization routes are specified in [META_VERIFICATION.md](META_VERIFICATION.md).
They require the budget operator role and cannot activate ordinary research.

Operator, scheduled collection, reassessment and exact Zenodo approval/delivery
routes use separate contracts in [DEVELOPMENT.md](DEVELOPMENT.md),
[REASSESSMENT.md](REASSESSMENT.md) and [PUBLIC_SHARING.md](PUBLIC_SHARING.md).
Ordinary API access cannot authorize a publisher or bypass a worker's budget.

Native Meta activation and private operator-browser admission are described in
[META_RESEARCH.md](META_RESEARCH.md). `native_research_required` makes a configured
path distinct from spending authority. Private activation retains both CSRFs.
The separate public-session audience uses an explicitly reviewed durable funding
delegation, the visitor's ordinary cookie/CSRF and `expected_activation_sha256`;
it never gives visitors administrator credentials. Both use the same worker and
ledger. See [PUBLIC_RESEARCH_ACCESS.md](PUBLIC_RESEARCH_ACCESS.md).

## Funded visitor research

`GET /api/v1/research/access` requires an existing owner session and returns a
strict `polymath-research-access-v1` projection: audience, funding availability,
static reason, current activation hash/deadline, session limits and own remaining
allowance/jobs. It creates no owner, job, finding, grant or reservation. Enabled
operational rate counters still apply. A private `operator_browser` hint contains
no private activation metadata; its privileged current route remains separate.

An explicit SDK create or resume first checks cookie-free capabilities. Native
public actions read owned access and attach the displayed hash to one POST.
Legacy execution is allowed only when the server explicitly states native
binding is unnecessary. No stale-version, rate-limit or uncertain-write response
automatically resubmits a POST. The SDK does not collect operator credentials.

All three enqueue routes and original-run resume use this access context. An
exhausted new-job allowance does not consume another admission for resume;
the original hash and canonical decimal lease token still apply. Unknown work
blocks further spending and cannot resume. Funding closure keeps saved owned
results, source inspection, stage history, cancellation and exact exports
available until the owner session expires. It never becomes a false login error.

Public deployments use persistent rate/queue/body/SSE controls. Rate responses
use static codes and bounded `Retry-After` values; retry time does not promise
renewed funding. One event connection lasts at most300 seconds; retain its
cursor and reconnect without creating another job. Stream closure alone is
not a terminal result. Private and public input ownership remains unchanged.

`python3 scripts/polymath-client.py --help` documents `research access`, artifact
creation, finding reassessment and exact resume. The CLI's funded public journey
is verified locally through actual HTTP/PostgreSQL and the Meta adapter with
mock transport. Public deployment and real native scientific evaluation remain
unverified, and paid calls are still disabled.

## Standalone discovery ladder commands

Explicit ladder creation uses the same bounded research admission:

```bash
python3 scripts/polymath-client.py --base-url "$POLYMATH_ORIGIN" --cookie-jar .polymath-client/owner.cookies runs create --mode surprise --discovery-ladder --max-stages 3 --max-depth 2
python3 scripts/polymath-client.py --base-url "$POLYMATH_ORIGIN" --cookie-jar .polymath-client/owner.cookies discovery show '<run-uuid>'
python3 scripts/polymath-client.py --base-url "$POLYMATH_ORIGIN" --cookie-jar .polymath-client/owner.cookies discovery stages '<run-uuid>'
python3 scripts/polymath-client.py --base-url "$POLYMATH_ORIGIN" --cookie-jar .polymath-client/owner.cookies discovery stage '<run-uuid>' '<stage-uuid>'
```

`--discovery-ladder` is valid only for Surprise. Its default ceilings are three
stages and depth two; the client accepts at most four stages and depth three.
Meta chooses how far to investigate within the single run's approved limits.
Omitting the flag preserves the original request body and research protocol.

The three discovery reading commands require the existing owner cookie. They
perform only bounded GET requests and never initialize a session or spend
credits. `show` preserves whole-run usage/accounting independently of the final
selection; a cancelled or interrupted run can have saved stages and no selected
final stage. Exact stage reads retain the current `needs_review`, superseded or
unsupported status alongside the original immutable result. Receipt/run/stage
identifiers, hash formats, ancestry bindings and selected-stage membership are
validated by the client. Scientific support remains a model judgement subject
to the server's saved-evidence checks.

Creation is verified through real loopback HTTP and PostgreSQL with synthetic
intelligence. The client supports ordinary visitor creation only under a current
explicit public funding delegation; it does not collect private operator-browser
credentials. Flags alone cannot authorize a Meta call. Owner reads of saved
stages require no new paid authorization.
See [DISCOVERY_LADDER.md](DISCOVERY_LADDER.md) for stage provenance and recovery.
