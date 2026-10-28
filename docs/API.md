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
| `POST /runs` | `{mode, goal, parent_result_ids?}`; owner+CSRF; returns queued run summary with HTTP201 |
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

Operator, scheduled collection, reassessment and exact Zenodo approval/delivery
routes use separate contracts in [DEVELOPMENT.md](DEVELOPMENT.md),
[REASSESSMENT.md](REASSESSMENT.md) and [PUBLIC_SHARING.md](PUBLIC_SHARING.md).
Ordinary API access cannot authorize a publisher or bypass a worker's budget.
