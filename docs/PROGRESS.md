# Polymath: Progress tracker

Last updated: 27 October 2026 (Bangkok). This file records verified outcomes; [BUILD_PLAN.md](BUILD_PLAN.md) holds task checkboxes and [ROADMAP.md](ROADMAP.md) holds target dates.

## Current position

**Stage: application foundation (A3), research-controller prototype (A4), internal durable-job/budget services, and abstract-reading literature adapters implemented and locally validated. Live research is not enabled.**

Scope: the complete product, including Surprise Me, the knowledge base, map, and connections between connections. Meta intelligence controls research. See [ARCHITECTURE.md](ARCHITECTURE.md).

**First engineering objective:** A1–A5 in the build plan: establish real Meta access and capabilities, create the application foundation, and complete one bounded research investigation with real sources, a structural mapping, an objection, and an evidence-backed conclusion or honest rejection.

**Next work:** integrate job ownership, cancellation, budget dispatch, and external-request recording into one worker execution boundary. Continue literature integration and evidence persistence. The owner explicitly requested continuing without paid calls because credits/account limits are not ready. Real Meta capabilities (A1/A2) and the live investigation (A5) remain blocked; a key appearing alone does not authorize paid tests. No fixture result counts as a completed live investigation.

## Milestone status

| Milestone | Status | Evidence / remaining acceptance |
|---|---|---|
| Repository and planning documents | Complete | Existing specification plus architecture, roadmap, task plan, and tracker in this change |
| Cloud tooling | Partially verified | Git read access worked; OpenTimestamps 0.7.2 installed; all four local proofs parsed and matched commit-hash payloads; install script reran successfully |
| A — Real Meta research path | A3/A4 complete; live checks blocked | Foundation and adaptive-controller prototype implemented; actual model authentication/capabilities and real-source investigation still unverified |
| B — Durable research and controls | B2/B3 internal services verified; execution integration pending | 12 real PostgreSQL job tests and 24 budget tests passed, including concurrency, fencing, recovery, unknown costs, and rollover; integrated worker, full evidence store, and public execution still pending |
| C — Explore | C1 partially implemented | Abstract adapters and explicit provider-coverage diagnostics tested; live access, full text, durable evidence storage, and the complete research journey pending |
| D — Public deployment and API | Not started | Host selection, public URL, quota controls, persistence/recovery |
| E — Ask and Surprise Me | Not started | Both modes working end to end |
| F — Knowledge base, map, higher-level research | Not started | Scheduled ingestion/reassessment and navigable evidence graph |
| G — Formalise, Write, Publish | Not started | Real feature journeys, sandbox integration, approved production publication |
| H — Quality, release, submission | Not started | Full regression, scientific audit, polished release, submission confirmation |

## Blockers and owner actions

| ID | Requirement | Current evidence | Action / unblocked work |
|---|---|---|---|
| X1 | Meta API authentication | Latest unauthenticated `GET https://api.meta.ai/v1/models` reached the service and returned HTTP 401; the earlier proxy block has changed. `MODEL_API_KEY` and `META_MODEL` remain absent. | Owner supplies the key securely in environment settings; engineering verifies authenticated access and available models. Never enter the key in chat or Git. Independent development continues. |
| X2 | Verified model behavior, pricing, and account spending cap | Owner answered that credits/account limits are not ready and instructed continuing without paid calls. No paid call executed; model names/prices remain unverified notes. | Continue local/fixture-based engineering. Paid tests remain disabled until the owner confirms credits/limits and permits them; then verify pricing and actual Meta capabilities. |
| X3 | Deployment account and hosting allowance | No host or infrastructure budget selected. | Evaluate host requirements/cost early; obtain authorization before provisioning paid resources. Local implementation continues. |
| X4 | Zenodo publisher identity, credentials, and artifact approval | Not configured or tested. | Set up sandbox first; secure production credentials and explicit approval of an exact artifact before production publication. Other research features continue independently. |
| X5 | Official submission details | Repository checklist contains unanswered fields. | Verify rules, submission fields, video requirements, and deadline before final submission; owner handles personal/account confirmations. |

## Evaluation baseline

Proposed initial seeds: self-organised criticality; error-correcting codes; phase transitions in learning. Include a weak/misleading input and an evidence-changing case. These are evaluation inputs, not prewritten discoveries. Confirm their suitability using the first live investigations.

Track: real source identity, quote integrity, claim support, mapping assumptions, objection quality, prior-work search coverage, useful rejection, adaptive next actions, latency, usage/cost, job recovery, and deployed completion of every feature. Confidence scores are not calibrated probabilities until evaluated as such.

## Evidence log

| Date (Bangkok) | Change or observation | Interpretation |
|---|---|---|
| 27 Oct 2026 | Inspected every existing text file and timestamp workflow; checkout had no application, manifest, build command, or test suite | Planning-stage project; application work remains ahead |
| 27 Oct 2026 | Read-only Git remote access succeeded; OpenTimestamps dependencies checked; 4/4 local proof payloads matched their commit hashes | Tooling/local integrity verified; Bitcoin confirmations were not checked |
| 27 Oct 2026 | Saved cloud install/start instructions plus API hostname/key requirements | Draft persistence confirmed; runtime application and environment publication not established |
| 27 Oct 2026 | Owner confirmed Meta-driven intelligence and full feature scope, including Surprise Me; approved saving the development direction | Architecture and build baseline recorded; earlier reduced-scope/after-v1 labels superseded |
| 27 Oct 2026 | Validated 8 Markdown documents, 25 local links, balanced code fences, and 40 unique task IDs; Git whitespace check passed; timestamp workflow/proofs unchanged | Documentation baseline checked; no application tests exist or were claimed |
| 27 Oct 2026 | A3: 14 backend unit tests and 1 real PostgreSQL integration test passed; frontend 6 tests and production build passed; container ran as UID 10001 with database/migrations ready and unknown API paths returning 404 | Foundation works locally; no live investigation or public deployment claimed. CI is defined but hosted outcome not yet verified. |
| 27 Oct 2026 | Container build initially failed certificate validation; mounted the injected cloud proxy CA for package-install steps and rebuilt successfully with verification enabled | Trust problem corrected without disabling verification; CA is not committed or retained in image layers |
| 27 Oct 2026 | Unauthenticated Meta models request returned HTTP 401; key/model variables absent. Literature and developer-doc requests were proxy-blocked; additive domain requirements saved | Meta network reachability improved; authentication/model/pricing checks still pending. Draft persistence does not establish runtime access to the additional destinations. |
| 27 Oct 2026 | A4: 32 fixture/mock-HTTP research tests passed; root checks passed with 46 backend unit tests, 1 real PostgreSQL integration test, and 6 frontend tests; Ruff/type checks and production build passed | Prototype validates action flow, evidence identity/quotes, timeouts/limits, and sanitized transport errors. Actual Meta protocol and scientific quality remain unverified. |
| 27 Oct 2026 | Rebuilt and restarted the non-root container stack; Chromium exercised all three entry modes, prompt switching, real status/retry, disabled launch, and 390px mobile layout; zero console errors/failed requests | Packaged foundation works in a real browser; mode selection is not completed live Explore/Ask/Surprise functionality |
| 27 Oct 2026 | GitHub Actions API read returned Forbidden | Workflow definition committed and equivalent local checks passed; hosted CI outcome remains unverified, not inferred from Git push success |
| 27 Oct 2026 | Pushed application foundation `c259968` and research prototype `1b1e0d4` to main; preserved and integrated automatic timestamp commits | First two implementation milestones saved remotely |
| 27 Oct 2026 | Owner instructed continuing without paid calls; began durable-job, budget, and literature-tool work | No paid model execution is authorized at this stage |
| 27 Oct 2026 | B2/B3 internal services: 12 job and 24 budget tests passed on real PostgreSQL, including threaded races, stale workers, cancellation, ambiguous external outcomes, day rollover, and migration reversibility | Repository invariants verified. Integrated worker, authorization, real model usage/pricing, and actual paid dispatch remain unverified; B2/B3 full workflow gates stay open. |
| 27 Oct 2026 | Partial C1: 73 synthetic HTTP fixture cases passed; controller forwards partial-provider failures and rejects invented/unread evidence | Abstract retrieval/provenance, DOI deduplication, input/response limits, XML hardening, and snapshot stability verified with fixtures; no live literature/model integration claimed |
| 27 Oct 2026 | Full local check: `make check`, `make test` (120 backend + 6 frontend), `TEST_DATABASE_URL=... make integration` (37 real PostgreSQL tests), and `make build` passed | 157 backend tests plus 6 frontend tests passed; upstream Starlette TestClient deprecation warning remains. Hosted CI outcome is still unverified. |

For subsequent implementation, append the task IDs, commit, exact meaningful checks and outcomes, deployed evidence where applicable, and remaining limitations. Keep planned, passed, failed, blocked, skipped, and unrun results distinct.
