# Developing Polymath

The current release connects Explore, Ask, and Surprise Me through the injected worker, transactional dispatch/budget controls, durable evidence, session-owned APIs, and workspace. A session-owned knowledge search/map projects saved findings and citation relationships. These paths are verified with synthetic models/sources; live Meta research is not enabled. Follow [BUILD_PLAN.md](BUILD_PLAN.md) and [PROGRESS.md](PROGRESS.md) for status.

## Prerequisites

- Python 3.12, uv 0.12.23, Node.js 24, npm, Make.
- Docker Engine and Docker Compose v2 for the local PostgreSQL database and packaged application.
- Existing checkout; no worktree is needed. Application secrets belong in secure runtime settings or a local ignored `.env`, never Git.

Use the repository root as the working directory. Installation uses `backend/uv.lock` and `web/package-lock.json`. Make directs caches to `/tmp` so setup does not require a writable home directory.

## Local development

```bash
# Preserve any existing .env; the example contains only local development values.
test -e .env || cp .env.example .env
make install
make db-up
make migrate
make api
```

In another terminal at the repository root, run `make web`. The Vite development server proxies `/api` to the backend on port 8000. Both development servers bind loopback. Their terminal output gives the local addresses; cloud onboarding does not provide public previews for these addresses.

The initial page supports selecting Explore, Ask, and Surprise Me and displays actual application/database/configuration status. It does not submit research jobs, charge API credits, or invent results. A configured key alone does not enable a feature that has not passed its execution and budget gates.

The development database is PostgreSQL 17 with local-only credentials `polymath`/`polymath`, exposed on loopback port 5432. These are not production credentials. Compose keeps data in a named volume. `make db-stop` stops it without removing data; do not use destructive volume-removal commands on a database containing work you need.

## Verification

```bash
make check
make test
TEST_DATABASE_URL=postgresql+psycopg://polymath:polymath@127.0.0.1:5432/polymath make integration
make build
```

Integration tests require PostgreSQL and create their own isolated schema, then clean up that schema. Use a disposable local/test database, never a production database. Tests cover actual migration upgrade/repeatability, schema drift, persistence, constraints, downgrade/re-upgrade, and readiness. Without `TEST_DATABASE_URL`, integration tests are skipped; `make integration` explicitly fails if the variable is absent.

Research-controller tests use fixture models and injected tools. They validate contracts and decisions without paid calls; they do not establish live Meta protocol compatibility or scientific validity. CI runs backend tests with PostgreSQL, frontend tests/types, and the production frontend build. CI's status is distinct from locally passing these commands.

## Packaged application

```bash
make compose-up
```

This builds the frontend/backend image, starts the database, runs migrations as a separate one-shot service, and starts the API serving the built frontend on loopback port 8000. It is a local packaging check, not public deployment. Images are digest-pinned; dependency locks are retained. The application runs as a non-root user. Migration and application containers use the database's private Compose hostname.

The foundation's Compose application deliberately does not forward model credentials. Local process configuration and container configuration are separate; model bindings will be added to the worker when live execution controls are implemented.

`make image` builds the image separately. In Codex cloud, the build helper mounts the injected proxy CA for package downloads when available. TLS verification remains enabled; the CA is not committed or retained in image layers. Outside this cloud, ordinary system certificate trust is used. A failed certificate check must be corrected through trust configuration, never bypassed.

`make compose-stop` stops the stack without deleting the database volume. Stop a separately launched local API before starting the packaged app because they share port 8000. Compose intentionally starts no model worker or scheduler: the worker library is integrated and tested, but native compatibility and permission for paid calls remain unverified/absent. The separate native composition is described below.

## Health and configuration

- `GET /api/v1/health`: process liveness; does not imply database readiness.
- `GET /api/v1/readiness`: queries PostgreSQL and compares the actual Alembic revision with the packaged migration head; returns 503 for unavailable/pending dependencies.
- `GET /api/v1/capabilities`: explicitly reports research unavailable and whether both key/model settings are present; configuration presence is not verified authentication.
- FastAPI's generated `/docs` describes the implemented session/job/event/result routes. The research-submission route exists but rejects new jobs while its default availability gate is off.

Settings load from the process and optionally `.env` in the working directory. `DATABASE_URL` must use `postgresql+psycopg`. `STATIC_DIR` (or `FRONTEND_DIST`) enables serving a built frontend. API routes that do not exist return JSON 404s instead of the SPA page.

## Internal job and budget services

`polymath.jobs.JobRepository` provides PostgreSQL claims, fenced leases, checkpoints, ordered/idempotent events, cancellation, and explicit interruption/recovery. Expired jobs are never automatically replayed. Dispatched external requests have their own durable bounded observation/reconciliation history, so a full checkpoint cannot block recovery. Ambiguous requests must be reconciled before a run can resume.

`polymath.budget.BudgetService` stores integer microdollars and serializes mutations under an account lock. Accounts default to paid execution disabled and pricing unverified. Reservations account for worst-case cost before dispatch; a separate one-time dispatch claim prevents duplicate use. Unknown outcomes retain their full hold and concurrency slot until explicit evidence resolves them. Settled costs are conservatively counted on both dispatch and settlement UTC dates, while counted only once globally. Unexpected actual charges freeze further dispatch.

`polymath.dispatch.DispatchCoordinator` now binds live job ownership, budget reservation, one-time dispatch, payload identity, and pending external work in one transaction. A committed grant is the authorization point; cancellation after it cannot unsend an in-flight call, but blocks subsequent grants. Known late responses are accounted without allowing stale checkpoint writes. Conflicting operator reconciliation/late-response evidence is retained and freezes further dispatch for review.

`polymath.worker.ResearchWorker` uses that boundary, persists source reads/results, streams real controller actions, and can poll until stopped. It records tool observations and immutable model responses for replay. Restart requires an explicit safe resume; observed responses are replayed without another dispatch/charge, while unknown requests require reconciliation. A free bounded source-search refresh may restore a fresh provider's discovery state; the model still receives the saved observation. Source snapshots already read are loaded from durable retrieval IDs. Changing the worker policy or request identity stops replay rather than spending again.

The legacy injected worker defaults disabled and its module entry point refuses paid execution. The separate native composition uses the same worker/controllers under exact activation, as described below. It now executes all three research modes with separate conclusion contracts. Ask returns cited/classified claims; Surprise Me first lets the model choose fields. Tests inject synthetic models/tools/prices; no paid requests occur. Do not substitute fixed fixture reservations for verified model token/context/pricing limits.

Replay protocol is now `polymath-worker-full-text-v8`. Its checkpoint fingerprint includes the mode, contract version, and ordered parent IDs/run IDs/content hashes, task kind, artifact limits and exact artifact input basis. Earlier findings are model context, never primary evidence: memory-source actions retrieve exact original source versions and establish a durable read link in the new run. Memory search replay uses an immutable observation and an opaque session-scope fingerprint; finding/source bodies are reloaded by immutable identifiers. Interrupted jobs from earlier research protocols stop safely instead of silently making new paid requests; completed historical findings remain readable. Worker cleanup stops heartbeats before terminal transitions so a heartbeat cannot cancel its own shutdown/interruption record.

## Full-text reading

The research and reassessment controllers expose explicit `open_full_text` and `read_full_text_window` actions. Migration `0017_arxiv_full_text` stores exact versioned arXiv HTML snapshots and owned indexes. Only saved windows of at most 8,000 Unicode codepoints are source reads. Index metadata grants no quotation access; replay pins the original extraction. Coverage follows memory, reassessment, generated passages, map/source views and exports. See [FULL_TEXT.md](FULL_TEXT.md) for transport bounds, range semantics and recovery. Native arXiv HTML compatibility remains unverified.

## Public finding snapshots

`PUBLIC_SHARING_ENABLED=false` is independent of Meta execution, collection and Zenodo settings. The Compose app passes this server setting through explicitly. Enabling it allows an active owner to preview and explicitly share an exact current finding; it does not make existing investigations public. The public reader uses `/library` and `/shared/<public_id>` and calls only the public read API, without bootstrapping a private browser session.

Owner routes under `/api/v1/shares` retain normal session/CSRF/origin controls. Preview returns the exact allowlisted projection and SHA-256; the Share action sends that hash and a new UUID idempotency key. A lost-response retry reuses the same key/body; owner history resolves the actual grant. Revocation remains available while sharing is disabled. Shares expire with the owner's current anonymous session and can be revoked earlier. Only short cited excerpts, model findings and scholarly metadata are exposed. Raw source bodies, private investigation history and ancestor findings stay private. Later reassessment can change the public status badge but never silently replaces the shared content. See [PUBLIC_SHARING.md](PUBLIC_SHARING.md) for the complete content/API and privacy contract. Local PostgreSQL, frontend and two-browser verification passed. Public deployment remains pending.

## Meta readiness and operator budget desk

The `/operator/budget` page and `/api/v1/budget` routes prepare disabled operator-attested Meta proposals, inspect counted spending/holds and explicitly disable an account. `BUDGET_READINESS_ENABLED=false` and an empty `BUDGET_ADMIN_ACTOR_IDS` keep preparation unavailable by default. An explicitly allowed operator needs the current browser and operator sessions plus both CSRF domains. Preparation cannot activate execution, verify pricing, run a probe, clear a kill switch or release a hold. New accounts are disabled/unverified/killed; existing caps, flags and ledger history remain intact. Every response states native readiness has not run. The server ceiling is at most150,000,000 microdollars ($150); it is not proof of provider credits or a configured external spend limit. See [META_READINESS.md](META_READINESS.md) for exact facts, integer rounding, immutable audit and the separate native-verification/activation stages.

Compose forwards the optional server operator identity and budget preparation settings; defaults create no operator authority. Store bootstrap credentials in secure server settings or the ignored local `.env`, not in chat, Git or proposal JSON. This setting cannot enable research, publication or collection.

## External HTTP client

`make api-client` prints standalone client help without network activity. The stdlib-only script is also included in the container at `/app/scripts/polymath-client.py`. Public reads use no cookie/session; private CLI commands require an explicit protected origin-bound cookie jar. Research creation and cancellation are explicit writes with CSRF/Origin and no automatic retry. Event replay uses persisted cursors, and artifact exports verify exact bytes before saving. See [API.md](API.md) for the complete v1 contract and commands. Real loopback HTTP/PostgreSQL/synthetic-worker checks passed; deployed external access and native Meta/provider verification remain separate gates.

## Session-owned research API

The application now mounts the following routes, while **new research submission remains disabled by default**:

| Route | Behavior |
|---|---|
| `GET /api/v1/session` | Bootstrap a seven-day HttpOnly browser cookie; return a separate CSRF token; only a cookie hash is stored |
| `POST /api/v1/runs` | Validate mode/goal, optional immutable parent selection, session, CSRF, origin, availability, and queue quotas; create ownership/job/event atomically only if explicitly available |
| `GET /api/v1/runs` | Cursor-paginated history belonging to the current browser session |
| `GET /api/v1/runs/{id}` | Owned public status; no checkpoint, raw model state, or lease details |
| `POST /api/v1/runs/{id}/cancel` | CSRF-protected cancellation of an owned job |
| `GET /api/v1/runs/{id}/events` | Authenticated SSE activity with persisted IDs/replay, heartbeat, expiry checks, and terminal drain |
| `GET /api/v1/runs/{id}/result` | Owned immutable evidence-backed result, once saved |
| `GET /api/v1/knowledge` | Bounded literal search of the current session's saved source/finding metadata |
| `GET /api/v1/knowledge/graph` | Bounded field/finding/source projection grounded in persisted findings and quote records |
| `GET /api/v1/knowledge/findings/{id}` | Owned finding summary, with generic 404 for foreign records |

Foreign runs return generic 404s. API responses, including errors, are `no-store`; validation failures do not echo private research inputs. Cookies are Secure over HTTPS and SameSite=Lax. Cookie expiry ends access to that anonymous browser history; general accounts are not implemented. These session/queue limits are not complete public abuse protection, so paid/public launch remains gated. Operator pricing/ledger configuration is still internal and needs authenticated controls before production use.

`create_app(research_availability=...)` is a trusted composition/test seam. The exported application passes no policy and remains unavailable even if a key/model appear. It never constructs or runs a model from HTTP requests. Tests may inject an available policy and a synthetic worker in an isolated database to exercise the real pipeline; no fixture mode or default results are shipped in the product.

The evidence service stores immutable source versions and separate retrieval metadata with database hash/immutability enforcement. Final findings may cite only sources durably read by the same run. Source identity/hash/kind, Unicode offsets, exact quotations, and proposed evidence associations are validated before saving a finding. This establishes provenance and quote integrity, not scientific truth, novelty, or an actual proof.

Ask stores claim IDs on quote records. Known/Postulated/Speculative are model classifications, not guaranteed truth; postulated claims require explicit derivation and distinct source URLs, speculative claims require caveats, and every claim needs exact source quotations. Ask must answer the submitted question. Surprise bridges must use the latest recorded field selection. The migration to answer evidence preserves historical findings and refuses an incompatible downgrade when answer artifacts exist rather than deleting them.

Optional Surprise discovery ladders use the same research worker and Meta adapter.
An immutable `discovery_intent` selects the separate ladder action/profile; its
absence preserves the historical protocol and hashes. Schema0022 adds atomic
stage/context/observation/parent records around normal immutable findings and
quotes. Owned stage/summary reads expose partial discoveries and whole-run totals;
only an explicit model finish selects the normal final-result pointer. The
activation must explicitly include the ladder profile. No new run, stage or
reload replenishes its shared limits. See [DISCOVERY_LADDER.md](DISCOVERY_LADDER.md)
and the standalone commands in [API.md](API.md).

The map/search is private to the current browser session. Shared source versions never reveal another session's finding or retrieval metadata. Source bodies are excluded from graph/search payloads. Edges describe recorded mappings, selected directions, or persisted quote associations; they do not establish scientific correctness. Views are capped, disclose truncation, and link back to actual owned investigations. Select 2–4 bridge findings to investigate a shared structure. The same Explore job receives separate structured parent IDs, never saved prose concatenated into the goal. Parent selection is immutable, session-owned, and restricted to completed bridges older than the new job. The model must read each selected finding and quote its original source version before a positive higher-level result can be saved. A curved `investigated_from` edge records this research basis; `supported_by` still points directly to original sources. Parents are included in filtered graph views within the same bounded node/edge limits. Scheduled source collection and explicit reassessment are described below.

For a higher-level API request, add `"parent_result_ids": ["<first-result-uuid>", "<second-result-uuid>"]` to an Explore mode/goal body. The array accepts 2–4 distinct canonical UUID strings. Invalid/missing/foreign/uncompleted/answer selections cannot enqueue a job; CSRF, availability and quota rules still apply. Four disjoint parents require eleven actions. The default action ceiling is twelve, while the twelve-model-call cap and budget controls remain unchanged. Large contexts or failed repairs may stop honestly at their existing limits.

## Formalise and Write

`POST /api/v1/artifacts/jobs` accepts `{kind, source_result_id, formalisation_id?}`. Kinds are `formalise`, `write_summary`, `write_paper`. Formalise starts from a saved bridge; writing accepts a bridge or answer and may incorporate an exact earlier conjecture from that same source finding. Inputs are owner-scoped and hash-pinned inside the same quota/ownership transaction as enqueue. Large or malformed bases roll back without consuming a queue slot. The research modes remain Explore/Ask/Surprise; task_kind chooses the separate generation contract.

`GET /api/v1/artifacts/jobs` supports an optional source-result filter, cursor and limit; `GET /api/v1/artifacts/jobs/{id}` reports status. These jobs share `/runs/{id}/events` and `/cancel` with research. Research history excludes artifact jobs. Outcomes use `artifact_id`, leaving scientific `result_id` separate. `GET /api/v1/artifacts/{id}` returns owned structured preview, original passages and document hashes; `/export` downloads the exact stored Markdown with attachment/nosniff/no-store headers. No operator or publication authority is granted by generating a document.

The worker imports the original quoted source snapshots under its current lease. Model prose cites passage ordinals; unknown citations, inherited uncertainty upgrades, or omission of a bridge side are rejected. Papers require abstract, Background, Findings, Evidence and Limitations. These checks establish integrity, not semantic support. Existing source support remains limited to abstracts unless a stored version explicitly says full text.

Formalise can invoke only the finite integer expression DSL in `math_checks.py`: at most3 variables,4096 assignments,64 AST nodes, depth8,256-bit arithmetic and1million evaluated nodes. It cannot evaluate arbitrary code or access a shell, filesystem or network. Successful finite checks are not general proofs. Exact counterexamples and decimal-string results are preserved without browser integer rounding, and prior conjecture check outcomes remain available to later writing.

Generation has a separate six-model-call/six-action default with bounded schema repair and at most4096 output tokens; all calls still require the same atomic budget grant. Real model pricing and worst-case reservations remain unverified, so production generation stays disabled. Replay protocol v8 blocks incompatible interrupted protocols. A lost saved-version response can resume to the existing immutable version without a second paid dispatch.

## Publisher review and approval

These routes are mounted but unconfigured by default. To configure local approval, explicitly set `PUBLICATION_APPROVAL_ENABLED`, a strong server-only `OPERATOR_BOOTSTRAP_TOKEN`, `OPERATOR_ACTOR_ID`/`OPERATOR_DISPLAY_NAME`, `PUBLICATION_PUBLISHER_ID`/`PUBLICATION_PUBLISHER_NAME`, and `PUBLICATION_DESTINATIONS` as a JSON array such as `["sandbox"]`. Configuration validates the complete identity/destination combination; a key alone does not enable approval. These settings do not enable a Zenodo request, paid research, or a model tool. Keep credentials in secure server settings and out of Git/chat.

`GET/POST /api/v1/operator/session` reports or creates a short-lived browser-bound publisher session. Login requires the existing browser CSRF/origin check. `POST /operator/logout` and every approval/revocation also require `X-Operator-CSRF-Token`; the role bearer cookie is HttpOnly and only its hash is stored. The UI erases the login code after use and retains CSRF tokens only in memory. Bootstrap credential/actor changes invalidate prior role sessions through their stored policy fingerprint.

`GET /api/v1/publications/capabilities` describes local preparation availability and publisher/destinations. `POST /publications` prepares an owned ready summary/paper using `{artifact_id,metadata,destination}`. Metadata includes explicit title, description, creators, license and optional keywords/affiliation/ORCID; the latter has checksum validation. `GET /publications` supports owned pagination and an optional artifact filter; `GET /publications/{id}` returns the exact immutable manifest, hash and current receipt state.

`POST /publications/{id}/approve` accepts only the displayed `expected_manifest_sha256`, with both session/CSRF domains. `POST /publications/approvals/{id}/revoke` appends a withdrawal record. Browser ownership alone cannot approve. Receipts bind exact UTF-8 export bytes/count, metadata, payload/basis, publisher, destination and deterministic remote marker. Changes require a different manifest and new approval. Expiry, logout, revocation, credential rotation and changed server policy are rechecked transactionally. Approval itself makes no Zenodo call. Delivery status is reported separately from the receipt.

## Durable publication and recovery

Publication execution defaults off, separately from approval and research. `GET /api/v1/publication-jobs/capabilities` reports its availability. To enable a future approved sandbox test, configure the approval settings above, allow only `["sandbox"]`, securely provide `ZENODO_SANDBOX_TOKEN`, and explicitly set `PUBLICATION_EXECUTION_ENABLED=true` in both API and worker environments. Production requires its own destination and `ZENODO_PRODUCTION_TOKEN`. Tokens alone enable nothing. Native authenticated sandbox compatibility has not yet been tested; current owner authorization does not permit a real publication.

`make publication-worker` runs the separate server-composed worker; `uv --project backend run python -m polymath.publication_runtime --once` attempts at most one queued job. The disabled command exits 2 before connecting, claiming, or constructing a remote client. Compose starts neither publication nor research workers automatically. Use secure host settings; do not put credentials in command history, logs, browser bundles or Git.

`POST /api/v1/publication-jobs` accepts `{manifest_id, approval_id, expected_manifest_sha256}` with current publisher authority and both CSRF tokens. The UI requires a separate destination-specific publication confirmation after approval. The queue stores one job per immutable manifest. Each create/upload/publish request needs a unique durable stage grant after exact approval and fenced ownership checks; replaying a grant cannot send again. The client uses fixed Zenodo hosts, bearer headers, no redirects/retries, bounded responses, and exact file paths. Metadata, the single uploaded file, and downloaded bytes are verified before publication and again before displaying an actual DOI/record URL.

Owned `GET /publication-jobs`, `GET /publication-jobs/{id}` and `/events` expose bounded status/history without leases or raw provider payloads. `POST /{id}/cancel` prevents later grants but cannot unsend an already granted request. A native submission response alone remains unverified. A lost create/upload/publish response interrupts with an unknown outcome; it never automatically retries. Expired worker leases are converted to interrupted jobs when the enabled worker next polls, preserving any pending operation.

Use `POST /{id}/reconcile` for an explicit read-only verification job. It may read the pinned deposit or search a bounded deterministic marker; zero search matches cannot prove a create never happened. It verifies actual metadata/file bytes and retains late remote facts even after local cancellation or expired approval. A verified already-published artifact can become published without another mutation. Otherwise safe continuation requires explicit `POST /{id}/resume` with a current exact receipt, a resolved operation history, and fresh UI confirmation. Unresolved requests or conflicting observations block continuation for manual review. The UI's Verify action performs reconciliation, never an automatic second publish.

Official Zenodo documentation informed the adapter, including draft states and distinct deposit/record IDs. Mock HTTP and isolated browser checks exercise these paths; they do not establish native token scopes, license availability, actual record behavior, or a production DOI. Direct documentation/sandbox HTTPS was proxy-blocked during development; the public documentation repository was read through the normal Git proxy. Remote collaborators can change a deposit between verification and publication because the native API supplies no atomic compare-and-publish guarantee; post-publication checks expose mismatches instead of asserting exact publication.

## Scheduled literature collection

Literature watches use a separate collection worker that never constructs a model. `LITERATURE_INGESTION_ENABLED` defaults false in both API and worker settings. `make literature-worker` refuses while disabled before connecting or claiming; its `--once` module option schedules at most five due watches and processes at most one ingestion job. Compose starts no scheduler or worker. Enabling collection does not enable paid research or publication.

`/api/v1/knowledge/watches/capabilities` reports availability. Session-owned `POST/GET /watches`, `GET /watches/{id}`, and `POST /{id}/pause`, `/resume`, `/check` create and manage explicit queries. Watches may optionally pin a completed owned bridge/answer, without claiming that every matching paper changes it. Intervals range from 6 hours to 7 days. Watches expire with their anonymous browser session; this is not permanent account ownership or public shared knowledge. There are at most 10 live watches per owner, 100 globally and 2,000 saved source versions per watch. Manual checks have a 60-second minimum interval and share the research queue's active-job limits. Pausing fences and cancels active collection.

Each provider has a frozen UTC search window and continuation. The initial window covers the previous 7 days; successful exhaustion starts a later window with a one-day overlap, bounded to 31 days. A tick reads at most one page of up to 5 records per provider. OpenAlex uses day-precision publication dates and opaque cursors; arXiv uses minute-precision submission dates and bounded offsets. These are bounded provider windows, not exhaustive corpus coverage or a guarantee of discovering late-indexed old papers. Independent database pacing gates govern provider permits. Provider credentials are not used; unverified OpenAlex authentication remains refused.

Original readable abstracts enter the immutable evidence store under an `ingest_literature` job lease. Successful page observations, per-watch seen-version links and the next cursor commit together. Changed retrieval timestamps alone do not add a source version. Failed providers retain their own cursor even when another succeeds. Metadata-only records are counted explicitly without invented text. Hitting the source cap stops further reads/advancement and is displayed as a limit. Known alias changes across different stored source IDs can still produce distinct collected versions; these are never counted as independent papers merely because there are two entries.

`GET /watches/{id}/sources` and `/ticks` return bounded histories; `/sources/{version_id}` opens the exact owned saved abstract and its version/retrieval/content/metadata hashes. Collection additions appear as unassessed source versions in search/map, with links to this source inspector instead of an Explore result. No new scientific finding or model assessment is generated by collecting a paper. Bibliographic metadata remains provider-declared and immutable. Explicit Meta-led reassessment uses the separately budgeted workflow below; collection does not enqueue it automatically.

Lost workers become interrupted, preserving already committed pages. Explicit safe internal `JobRepository.resume_interrupted` can continue a tick while skipping committed providers; the UI can pause/cancel and start a fresh check from the saved provider cursors. No model work is involved. Research/artifact workers claim only their own task kinds, and the collection worker claims only ingestion. A provider outage is not evidence of an absence of relevant literature.

## Reassessing a finding

See [REASSESSMENT.md](REASSESSMENT.md) for the evidence, history and concurrency contract. `/api/v1/assessments/capabilities` reports the shared research availability gate; the exported app keeps it off. The same injected `ResearchWorker` claims `reassess_finding`, uses the existing budget/dispatch journal and never constructs a live Meta model by default.

`POST /api/v1/assessments/jobs` takes `{source_result_id, watch_id, new_source_version_ids, expected_head_revision}` with the current browser CSRF/origin checks and shared queue limits. Select one to five exact pending watched versions; the source finding and expected head are pinned transactionally. An unsupported current finding can be explicitly reassessed. Existing cited or already reviewed versions cannot be disguised as new inputs. The UI requires an explicit request and keeps collection separate from model spending.

`GET /assessments/jobs` and `/jobs/{id}` report owned status and the separate `assessment_id`; activity/cancellation use the existing run SSE/cancel routes. `GET /heads/{result_id}` and `/heads/{result_id}/history` expose current version/state and bounded owned history. `GET /{assessment_id}` returns the immutable cited judgement. `GET /findings/{result_id}` returns exact hydrated research/citation data for original or applied revised versions, with historical warnings; unapplied candidates cannot be opened as scientific findings.

The model chooses pinned historical/new reads, searches and newly discovered source reads. Every paid call needs a unique atomic grant and a live browser owner at its authorization boundary, including after budget/job lock waits. Successful search purposes have durable receipts. Exact retrieval UUIDs distinguish old/new versions of the same paper; replacement findings select one current snapshot per known paper identity. Source and judgement integrity checks do not establish scientific truth.

Unchanged, revised and unsupported outcomes can update the head only when the expected revision still matches. Inconclusive/failed/bounded attempts and concurrent losers preserve audit without replacing the current finding or consuming pending inputs. Head, assessment and terminal job state commit together; the worker stops heartbeats before that transaction. A lost final response is resolved from owned status/history, without another model dispatch. A timed-out database source read interrupts rather than becoming an ordinary failed read followed by another paid call; explicit safe resume replays existing model responses and exact free reads.

New model memory, selected parents, writing, watch anchors and publication authority require current supported inputs. Explicit historical records and exact exports remain available. Publication grants lock the source head before the publication job; late responses and read-only reconciliation retain verified remote facts even if a later assessment marks their source unsupported. New approval/send/resume is blocked for stale source documents. No real paid call or native publication is authorized by this local implementation.

## Literature tools

The [literature package](../backend/src/polymath/literature/README.md) implements fixed-host OpenAlex/arXiv searches, bounded feed pages and abstract snapshots. It does not fetch arbitrary paper URLs or claim to have read full text. Retrieved abstracts retain identity, time, extraction method, hash and available provider-declared authors, dates, venue, identifiers and license. Missing metadata stays unknown. First readable snapshots are frozen within an investigation. Known DOI/arXiv/OpenAlex and exact URL aliases are combined across actual reads when independently checking citation counts; versions of one known paper cannot establish independent support. This remains partial identity resolution.

`SearchResults` includes typed provider-failure diagnostics. The controller forwards partial coverage to the model; an all-provider outage is an error, not evidence of no relevant literature. Safe authenticated OpenAlex access, live provider checks, and full-text extraction remain incomplete. A bounded unauthenticated read-only attempt against each real provider failed at the cloud proxy connection, including when the injected CA was trusted; no provider response or native compatibility was established. All automated literature checks use synthetic HTTP fixtures without paid/model requests.

## Meta integration prerequisites

`MODEL_API_KEY` and an explicitly verified `META_MODEL` are required for real model access. The prototype targets the repository's proposed HTTPS endpoint on `api.meta.ai`; actual capabilities and model IDs still need live verification. No model ID is guessed by default, no non-Meta fallback is configured, and no paid calls run from startup, CI, or the web page.

Before an explicitly bounded live experiment: verify current pricing and account limits, supply authentication securely, and verify runtime access. Before enabling public investigations: implement and test durable jobs, atomic spending reservations, quotas, and operational authorization. The prototype's call/token limits are not a substitute for those controls. Read the [research package's usage notes](../backend/src/polymath/research/README.md) before invoking it manually.

**Current owner instruction: continue without paid calls.** Credits/account limits are not ready. Do not enable a model request merely because a key appears; the owner must confirm readiness and permit paid testing first.

Network destinations identified so far: `api.meta.ai`, `dev.meta.ai` for model documentation, `api.openalex.org` for discovery, and `export.arxiv.org`/`arxiv.org` for accessible literature. Saved environment requirements do not establish runtime connectivity or provider authorization.

## One-call Meta verification

The separate `/api/v1/budget/verification` routes and the verification tab in `/operator/budget` bind one fixed protocol request to a saved readiness proposal, current server key/model/ledger, provider-account attestation and exact human approval. Preview sends nothing. The explicit **Arm and queue THIS one call** action clears the account kill switch for that scope and advances its stop generation; ordinary research and pricing flags stay unchanged. Every later safe disable invalidates older authority, including repeated disables. All calls and uncertain holds count on the existing ledger.

`PAID_META_PROBE_ENABLED=false` is the default. Enabling it requires preparation, an explicitly allowed operator, an exact `NATIVE_BUDGET_ACCOUNT_ID` and server-only Meta model/key. No setting or key alone approves a call. These settings must remain off under the owner's current instruction to continue without paid calls. Compose forwards no model credentials and starts no verification worker.

`make meta-probe-worker` or `uv --project backend run python -m polymath.meta_probe_worker --once` exits2 while disabled, before connecting, claiming or constructing a model. An explicit `--finalize-only RUN_ID` can finish an already recorded response without a key, paid flag or network call; it cannot resend a prepared/unknown request. No CLI endpoint, model or credential overrides are accepted.

The adapter journals bounded wire facts and strict usage before action validation. Known costs use separate input/output ceiling arithmetic; invalid action text does not erase a charge. Missing/mismatched model identity or unusable usage preserves the unknown hold. A successful receipt proves only that one proposed wire/action/usage check passed. Pricing authenticity, inclusive billing, provider funding/caps, scientific quality and research activation remain separate gates. See [META_VERIFICATION.md](META_VERIFICATION.md) for the exact contract.

## Full native Meta research

`/operator/budget#research` previews and activates an exact saved Meta proposal, passed protocol observation and fresh human review of wire/usage, pricing/bounds and funding/cap facts. It retains the existing ledger, historical charges/holds and original task limits. A canary receipt never supplies missing prices or verifies scientific quality. Native activation initially authorizes only the same current budget operator/browser; anonymous public research is a separate D2 gate.

`PAID_META_RESEARCH_ENABLED=false` is independent of the probe and publication gates. The native worker uses the real Meta adapter and fixed OpenAlex/arXiv clients through the existing controllers for Explore/Ask/Surprise, higher-level memory research, Formalise, Write and reassessment. It loads each run’s original policy before configuring its budget/controller. Model construction is lazy after a fresh scoped grant; recorded replies and accounting do not need the current key.

The ordinary private app obtains the in-memory operator CSRF and owner-eligible activation, then binds its exact hash to all three scientific enqueue APIs. They authenticate before the shared queue lock and lock the native account before source-head work, writing run/ownership/basis/activation/budget together. Native and legacy workers claim separate bound/unbound job sets. Every fresh call rechecks operator, browser, current protocol, key/model, epoch, funding and lease after waits; late accounting uses only original immutable bindings.

`make native-research-worker` runs `python -m polymath.native_research_worker`; `--once` handles at most one scientific job. Disabled startup exits2 before database/model construction. The billing-only `--finalize-only RUN_ID --request-id model-0001` reads only database settings and cannot send or reconcile an unknown hold. It preserves accounting facts, not a promise to finish science without further work.

Explicit `POST /api/v1/runs/{id}/resume` accepts the exact original activation hash and decimal-string expected lease token, with owning browser/operator and both CSRFs. It requires the original live approval, unchanged input and no prepared/unknown/open charge. It renews no authority, budget or deadline; probe and unbound legacy jobs remain excluded. The app shows this control only for structurally recoverable native tasks with matching current context.

Native HTTP uses the full reviewed timeout up to60seconds, with a separately pinned30second orchestration allowance (outer limit90). Unknown billing—including an observed response with unusable usage—stops paid work and remains visibly held. A known malformed action can use the existing bounded repair path only through another fresh grant. All current integration evidence uses mock HTTP; the owner still prohibits real paid calls. See [META_RESEARCH.md](META_RESEARCH.md).
