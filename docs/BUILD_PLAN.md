# Polymath: Development plan

Implementation baseline: 27 October 2026. Product scope is in [SPEC.md](SPEC.md); technical decisions are in [ARCHITECTURE.md](ARCHITECTURE.md); target dates are in [ROADMAP.md](ROADMAP.md); current status and blockers are in [PROGRESS.md](PROGRESS.md).

Checkboxes record implementation acceptance, with verification evidence in the progress log. Check a task only when its acceptance evidence exists; add the relevant commit, test command/result, or deployed check to the progress log. A mocked integration is not a live integration. Keep implementation commits small and update this plan and the progress tracker with each completed slice.

## A. Establish the real Meta research path

Dependencies: none for local scaffolding; live tests require usable Meta access and confirmed spending limits.

- [ ] **A1 — Confirm runtime access.** Recheck binding presence without printing values; retry HTTPS after settings propagate. Verify the account cap with the owner and keep live calls bounded. Record failures precisely.
- [ ] **A2 — Verify model capabilities.** Establish available Meta model, actual endpoint, tool calling/action protocol, structured output, streaming, context/output limits, usage reporting, errors, and current pricing. Record observed support separately from documentation claims.
- [x] **A3 — Establish the application skeleton.** React/TypeScript and FastAPI projects, dependency locks, PostgreSQL development configuration, packaged Alembic migrations, health/readiness/capability checks, validated settings, application CI, and Apache 2.0 license implemented. Verified locally with backend/frontend tests, real PostgreSQL migration/persistence checks, and non-root container startup. Hosted CI execution is a separate check after pushing. Timestamp automation preserved.
- [x] **A4 — Implement the model adapter and minimal controller.** Typed adaptive controller and explicitly gated Meta transport implemented; 32 fixture/mock-HTTP tests cover observation-dependent actions, exact evidence checks, bounded corrections/timeouts, and sanitized failures. Actual Meta compatibility remains unverified in A2; real literature investigation remains A5. This prototype is not exposed publicly and does not replace B2/B3 controls.
- [ ] **A5 — Complete the first real investigation.** Search and read real literature on one seed concept; record a mapping and objection; return a supported conclusion or honest rejection. Preserve source evidence and actual cost. Capture a trace demonstrating that a tool observation affects the next action.

Gate: a real Meta-driven investigation works. Access blockers do not prevent A3 and fixture-based portions of A4, but they prevent claiming A2/A5 passed.

## B. Make research durable, bounded, and inspectable

Dependencies: A3; use A4 contracts and validate against A5.

- [ ] **B1 — Persist research artifacts.** Immutable source versions, retrieval provenance, run-scoped durable-read links, findings, and exact quote records implemented; 28 real PostgreSQL tests verify hashes, offsets, immutable history, fences, and migration reversibility. Immutable conjecture/document versions and exact exports are now implemented. Provider-declared bibliographic metadata and canonical alias checks are now implemented; the broader claim/knowledge graph remains. Exact approval/publication entities and immutable operation audits are now implemented.
- [x] **B2 — Implement job ownership and recovery.** Repository and injected worker integrated with atomic dispatch grants, heartbeat, cancellation, shutdown, replayable checkpoints/tool observations, and explicit interruption/reconciliation. Eight worker-level PostgreSQL tests verify a complete synthetic investigation, cancellation, shutdown, disabled execution, and restart without duplicate charges. Production Meta factory and live verification remain separately gated.
- [ ] **B3 — Enforce spending controls.** Ledger now bound to the worker through one transaction covering ownership, reservation, dispatch claim, and pending external request. Seventeen dispatch tests additionally cover races, late responses, recovery, and conflicting reconciliation audits. Remaining: verified real-model pricing/token bounds, account cap confirmation, and authenticated operator configuration. Fixed test reservations are not a verified live worst-case cost bound; paid calls remain forbidden.
- [x] **B4 — Preserve and stream activity.** Persisted session-owned SSE, replay cursors, named heartbeats, reconnect/terminal handling, status/results, and browser-owned history implemented. Real PostgreSQL/API tests and an isolated synthetic browser-to-worker journey verify replay and refresh without creating a second investigation. The production research gate remains off.
- [ ] **B5 — Secure tool and source boundaries.** Validate tool inputs, URLs and redirects; block internal destinations; limit fetch size/time; isolate mathematical checks; treat papers as untrusted data; redact credentials.

Gate: a research job survives a worker restart or fails explicitly with retained evidence; cancellation and concurrent spending controls pass meaningful tests.

## C. Complete Explore and the evidence experience

Dependencies: A5, B1–B5.

- [ ] **C1 — Integrate literature providers.** Fixed-host OpenAlex/arXiv abstract adapters implemented with canonical identities/DOI deduplication, bounded reads, preserved first snapshots, and explicit partial-provider failures; 73 synthetic fixture tests passed. Provider-declared bibliographic metadata and bounded chronological feed pages are implemented. Remaining: live provider verification, safe verified authentication where required, and accessible full-text retrieval. Durable source storage is implemented. Abstract support must never be described as full-text support.
- [ ] **C2 — Build evidence validation.** Immutable source/finding versions and run-bound exact-quote/hash/Unicode-offset checks implemented with 28 PostgreSQL tests; final findings cannot invent a durable read. Remaining: validate against actual accessible literature and assess source support in live investigations. Integrity checks alone do not establish scientific truth.
- [ ] **C3 — Build the adaptive investigation policy.** Support branching hypotheses, counterevidence, prior-connection searches, revisions, stopping conditions, and useful empty outcomes.
- [x] **C4 — Build the research workspace.** Mode entry, actual owned history, streamed events, cancellation, candidate bridges, mappings, evidence inspection, objections/limitations, and mobile/accessibility behavior implemented. Browser verification uses explicitly synthetic model/source fixtures; no live Explore/Ask/Surprise outcome is claimed.
- [ ] **C5 — Evaluate Explore.** Audit the three proposed seeds and a misleading/weak input; measure source support, mapping quality, latency, and cost. Add regression cases from observed failures.

Gate: complete browser-to-worker-to-source-to-result journey with inspectable evidence; no generated citations or activity masquerading as retrieved results.

## D. Deploy early and expose a controlled public API

Dependencies: B; deploy the skeleton before C is complete, then validate the full Explore path.

- [ ] **D1 — Package and deploy.** Create reproducible web/API and worker builds, migration/start commands, persistent storage, HTTPS, and environment-specific configuration. Verify host costs before provisioning paid resources.
- [ ] **D2 — Add public access controls.** Implement bounded sessions/API access, quotas, rate/queue limits, operator authorization, and clear exhausted-budget behavior. Saved examples must be labelled.
- [ ] **D3 — Publish the API contract.** Version research creation, status, events, cancellation, and result endpoints; document authentication, limits, and errors; exercise with an external client.
- [ ] **D4 — Verify operations.** Test deployed SSE, disconnects, worker restart, persistent artifacts, migrations, backup/restore, and credential redaction. Document startup, rollback, and recovery commands.

Gate: the real public URL runs Explore with controlled spending and survives the documented restart procedure. Creating a localhost page does not satisfy this gate.

## E. Complete Ask and Surprise Me

Dependencies: C; deploy incrementally through D.

- [x] **E1 — Implement Ask.** Question-bound answers, individually classified/cited claims, derivations, caveats, limits, and unanswered questions implemented through worker, immutable storage, and UI. Quote integrity and classified inference rules are tested; semantic correctness of the labels still requires live evaluation.
- [x] **E2 — Implement autonomous field selection.** The research model chooses/revises 2–4 fields with a concise rationale; no production field list is supplied. Selections are preserved and displayed. Revisions reset challenge/prior-work scope and positive findings must match the current selection. Actual Meta selection quality remains unverified.
- [x] **E3 — Implement Surprise Me.** Selected fields flow through shared tools, evidence checks, worker budgets, events, storage, and UI; empty outcomes remain allowed. Synthetic fixture tests verify the full path and restart without duplicate dispatch/selection. No prewritten discoveries or fixture fields are shipped as production outcomes.
- [ ] **E4 — Verify both modes live.** Fixture regressions and isolated browser journeys passed for both modes, including labels/citations/field selection, persistence, map navigation, and refresh without duplicates. Bounded live Meta/source evaluations, scientific review, and public deployment remain blocked/unrun; the owner still prohibits paid calls.

Gate: users can complete both modes on the deployed application using the same evidence and job infrastructure as Explore.

## F. Complete the knowledge base, map, and higher-level discovery

Dependencies: B1, C, E2/E3 for Surprise Me integration.

- [ ] **F1 — Build knowledge retrieval.** Session-owned search/projection over saved sources and bridge/answer findings is implemented with literal-query matching, source version identities, persisted quote associations, and bounded coverage. Owned model memory search, immutable finding reads, and separate original-source rereads are implemented with fenced replay. Remaining: richer claim-level retrieval and shared/public visibility.
- [ ] **F2 — Build the map.** Interactive accessible field/finding/source map, owned result navigation, privacy clearing, and truncation reporting implemented and browser-verified. Selecting 2–4 saved bridges now launches higher-level research; the graph displays immutable parent links separately from original-source evidence. Public deployment and live evaluation remain pending.
- [x] **F3 — Investigate connections between connections.** Model-led selected-bridge investigations run through the same worker, replay, and budget controls. Positive conclusions must reread every immutable parent and quote original evidence from each; server-side checks independently enforce this. Earlier-only versions prevent circular lineage, and parent links never imply independent support. Synthetic 2/4-parent and browser journeys pass; live Meta/scientific evaluation remains unverified.
- [x] **F4 — Add scheduled ingestion.** Session-owned watches, frozen provider windows/cursors, bounded scheduling and per-provider pages, pacing, exact source-version deduplication, expiry/cancellation and recovery are implemented through a separate collection worker with no model execution. Owned source inspection and map links identify additions as unassessed. PostgreSQL/mock/browser verification is recorded in the tracker; native provider access remains unverified, and reassessment uses the separate paid controls in F5.
- [ ] **F5 — Reassess findings.** Explicit version-bound Meta reassessment, exact historical/new citations, optional validated replacements, immutable audit, atomic current-head updates and before/after history are implemented. Stale/unsupported findings cannot silently drive fresh model/writing/publication work; exact history remains readable. Controlled newly arriving-source and contradictory-source browser journeys pass with synthetic models. Remaining: native bounded ingestion/Meta investigation and scientific evaluation. See [REASSESSMENT.md](REASSESSMENT.md).
- [ ] **F6 — Connect the complete experience.** Let Explore, Ask, and Surprise Me reuse memory and higher-level findings. Scheduled collection now adds unassessed versions to the owned map with exact source inspection. Explicit reassessment now reads additions and updates current findings with preserved history. Remaining: safe full-text retrieval, shared/public visibility and deployed source-to-claim verification.

Gate: the knowledge base actually gains new literature and can revise an existing finding; the map and higher-level research are usable in the deployed product.

## G. Complete Formalise, Write, and Publish

Dependencies: C evidence contracts; D operator authorization; credentials for live publishing.

- [x] **G1 — Implement Formalise.** Model-generated definitions, assumptions, conjectures, proof attempts, exact citations, and bounded finite integer checks are persisted through the shared durable worker. Checks preserve exact encodings and witnesses; finite success is never labelled a general proof. Generated conjectures remain unverified. Live Meta mathematical quality and broader formal verification remain unverified.
- [x] **G2 — Implement Write.** Model-generated summaries and complete papers use exact versioned passage citations, inherit claim uncertainty, and cover both sides of a bridge. Owned preview/history and exact saved Markdown export are implemented. Optional conjecture input preserves its check outcomes. Integrity checks do not establish semantic support; live source/model evaluation remains pending.
- [x] **G3 — Implement publication approval.** Separate authenticated, browser-bound operator sessions and two CSRF checks protect immutable approvals/revocations. Receipts bind exact saved bytes/count, artifact/payload/basis hashes, canonical metadata, publisher and sandbox/production destination. Edits, expiry, logout, credential rotation or policy changes invalidate authority; research tools cannot approve. Preview/approval browser journeys use fixtures; approval itself makes no external request.
- [ ] **G4 — Integrate Zenodo sandbox.** Fixed-host transport, durable separate publication queue, exact approval/send grants, upload/readback verification, explicit publishing and read-only reconciliation are implemented through PostgreSQL/mock-HTTP/browser journeys. Official documentation informed the adapter. Remaining: owner-configured credentials and a genuinely approved authenticated sandbox run; mock records are not real sandbox publications.
- [ ] **G5 — Verify production publication.** Configure production credentials securely; obtain explicit approval for a specific final artifact; publish and verify the real DOI. A sandbox record is not production publication. External approval/credentials remain recorded prerequisites until supplied.
- [ ] **G6 — Complete browser journeys.** Finding → Formalise → Write → preview/export is implemented with durable recovery. Exact-preview/operator approval/revocation now also work locally. The local fixture journey now includes actual queue/worker execution, verified mock publication status, cancellation and lost-response reconciliation. Remaining: native Meta/Zenodo validation and deployed end-to-end verification.

Gate: all three experiences work, publishing respects authorization, and any remaining production credential/approval blocker is reported without claiming full release completion.

## H. Release the complete product and submit

Dependencies: all prior feature gates; do not silently omit features to meet a date.

- [ ] **H1 — Run the full regression matrix.** Cover every mode, public API, citation integrity, adaptive decisions, source/model outages, budget exhaustion, concurrency, hostile document instructions, permissions, restarts, and publication retries.
- [ ] **H2 — Audit scientific quality.** Review representative evidence and mappings for the three seeds and weak input. Seek a qualified external assessment of at least one non-obvious bridge; record whether it was obtained. Do not invent an expert endorsement.
- [ ] **H3 — Polish and document.** Refine navigation, empty/loading/error states, mobile/accessibility behavior, fresh saved examples, API docs, developer setup, operational runbook, and known limitations.
- [ ] **H4 — Prepare the submission.** Verify official rules and fields, project/license visibility, live URL, Meta tooling explanation, short video requirement, attribution, and final submission materials with the owner.
- [ ] **H5 — Release and verify.** Run all feature journeys against the deployed release, record the release commit and evidence, verify restoration/recovery, and submit before the Bangkok target. Mark submission done only after confirmation.

Gate: the complete specified product is deployed and tested, the submission is confirmed, and limitations are stated accurately.

## Working protocol

- Start each task by checking the repository state and reading the relevant architecture section. Use the existing checkout; do not create worktrees unless requested.
- Use highest available reasoning for architecture, controller behavior, evidence correctness, concurrency/budget controls, and difficult diagnosis. High is sufficient for routine implementation. The owner changes the setting; do not claim it was changed automatically.
- Build through complete user journeys, keep tests meaningful, and distinguish fixture checks from live evidence. Never spend model credits just to repeat an unchanged successful check.
- Update task checkboxes and [PROGRESS.md](PROGRESS.md) when evidence changes. Link commits/checks and identify the next task. Never mark planned work complete.
- Protect `.github/workflows/bitcoin-timestamp.yml` and `timestamps/`. Keep secrets out of code, docs, frontend, commits, and logs. Use open literature only.
- Report blockers early and continue independent work. Dates are targets, not evidence. If the complete scope cannot meet a date, show the gap and request an explicit product decision rather than silently dropping features.
