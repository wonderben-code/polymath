# Polymath: Development plan

Implementation baseline: 27 October 2026. Product scope is in [SPEC.md](SPEC.md); technical decisions are in [ARCHITECTURE.md](ARCHITECTURE.md); target dates are in [ROADMAP.md](ROADMAP.md); current status and blockers are in [PROGRESS.md](PROGRESS.md).

All tasks below are initially uncompleted. Check a task only when its acceptance evidence exists; add the relevant commit, test command/result, or deployed check to the progress log. A mocked integration is not a live integration. Keep implementation commits small and update this plan and the progress tracker with each completed slice.

## A. Establish the real Meta research path

Dependencies: none for local scaffolding; live tests require usable Meta access and confirmed spending limits.

- [ ] **A1 — Confirm runtime access.** Recheck binding presence without printing values; retry HTTPS after settings propagate. Verify the account cap with the owner and keep live calls bounded. Record failures precisely.
- [ ] **A2 — Verify model capabilities.** Establish available Meta model, actual endpoint, tool calling/action protocol, structured output, streaming, context/output limits, usage reporting, errors, and current pricing. Record observed support separately from documentation claims.
- [ ] **A3 — Establish the application skeleton.** Create backend/frontend projects, pinned dependencies and lockfiles, PostgreSQL development configuration, migrations, health checks, configuration validation, and fixture-based CI. Add the planned Apache 2.0 license with the first code commit; preserve timestamp automation.
- [ ] **A4 — Implement the model adapter and minimal controller.** Validate actions, bound correction/retry attempts, record tool observations and usage, and let the Meta model choose subsequent actions.
- [ ] **A5 — Complete the first real investigation.** Search and read real literature on one seed concept; record a mapping and objection; return a supported conclusion or honest rejection. Preserve source evidence and actual cost. Capture a trace demonstrating that a tool observation affects the next action.

Gate: a real Meta-driven investigation works. Access blockers do not prevent A3 and fixture-based portions of A4, but they prevent claiming A2/A5 passed.

## B. Make research durable, bounded, and inspectable

Dependencies: A3; use A4 contracts and validate against A5.

- [ ] **B1 — Persist research artifacts.** Implement the core entities, provenance links, source versions, uniqueness rules, and migrations described in the architecture.
- [ ] **B2 — Implement job ownership and recovery.** Add transactional claims, leases, heartbeats, fenced checkpoints, retry states, cancellation, and reconciliation for ambiguous external requests.
- [ ] **B3 — Enforce spending controls.** Add atomic reservations, usage reconciliation, call/token/run/global limits, concurrency bounds, and a kill switch. Test races and unknown usage, not only successful calls.
- [ ] **B4 — Preserve and stream activity.** Store ordered public events; support SSE replay/reconnect and status/result endpoints. Verify refresh does not duplicate an investigation.
- [ ] **B5 — Secure tool and source boundaries.** Validate tool inputs, URLs and redirects; block internal destinations; limit fetch size/time; isolate mathematical checks; treat papers as untrusted data; redact credentials.

Gate: a research job survives a worker restart or fails explicitly with retained evidence; cancellation and concurrent spending controls pass meaningful tests.

## C. Complete Explore and the evidence experience

Dependencies: A5, B1–B5.

- [ ] **C1 — Integrate literature providers.** Start with OpenAlex discovery and accessible arXiv/open-access text. Verify provider requirements, canonical identifiers, deduplication, rate-limit handling, and abstract/full-text distinctions.
- [ ] **C2 — Build evidence validation.** Persist exact passages and locations; check quotations against retrieved text; reject nonexistent sources and unsupported references; version corrections.
- [ ] **C3 — Build the adaptive investigation policy.** Support branching hypotheses, counterevidence, prior-connection searches, revisions, stopping conditions, and useful empty outcomes.
- [ ] **C4 — Build the research workspace.** Implement concept entry, live events, cancellation, saved run history, bridge cards, side-by-side mappings, evidence inspection, objections, and assessment limitations. Make it accessible on desktop and mobile.
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

- [ ] **E1 — Implement Ask.** Investigate a question, assemble claim-level Known/Postulated/Speculative answers, link evidence and bridges, and show conflicting evidence and unanswered parts.
- [ ] **E2 — Implement autonomous field selection.** Let Meta choose fields from prior coverage, conceptual distance, and unresolved questions; preserve the selection and concise public rationale.
- [ ] **E3 — Complete Surprise Me.** Run selected fields through real research, stream actual activity, display supported discoveries or empty results, and allow follow-up investigation. Verify field selection is not just a rotating list of prewritten examples.
- [ ] **E4 — Verify both modes.** Add browser journeys, fixture regressions, and bounded live evaluations; check citations, labels, costs, cancellation, and persistence.

Gate: users can complete both modes on the deployed application using the same evidence and job infrastructure as Explore.

## F. Complete the knowledge base, map, and higher-level discovery

Dependencies: B1, C, E2/E3 for Surprise Me integration.

- [ ] **F1 — Build knowledge retrieval.** Search saved papers, fields, claims, and bridges; deduplicate source identities; preserve finding versions and provenance relationships.
- [ ] **F2 — Build the map.** Render bounded graph views of fields, papers, bridges, and higher-level connections; open evidence and launch follow-up research from a selected object.
- [ ] **F3 — Investigate connections between connections.** Have Meta form and test hypotheses across existing bridges; trace higher-level claims to original evidence; prevent circular support and compounded false certainty.
- [ ] **F4 — Add scheduled ingestion.** Persist watches/cursors; enqueue bounded, deduplicated searches for new literature; integrate rate limits and the same spending controls.
- [ ] **F5 — Reassess findings.** Detect potentially relevant new sources, schedule rechecks, preserve superseded conclusions, and display changes. Test using a controlled newly arriving source and a real bounded ingestion run.
- [ ] **F6 — Connect the complete experience.** Let Explore, Ask, and Surprise Me reuse memory and higher-level findings. Test map navigation, scheduled work, deduplication, and source-to-claim traceability.

Gate: the knowledge base actually gains new literature and can revise an existing finding; the map and higher-level research are usable in the deployed product.

## G. Complete Formalise, Write, and Publish

Dependencies: C evidence contracts; D operator authorization; credentials for live publishing.

- [ ] **G1 — Implement Formalise.** Store definitions, assumptions, conjectures, proof attempts, and bounded counterexample checks. Distinguish numerical evidence, proof sketches, open statements, and genuinely verified proofs.
- [ ] **G2 — Implement Write.** Generate a plain summary and formal paper from versioned evidence; validate references and unsupported additions; preview and export complete documents.
- [ ] **G3 — Implement publication approval.** Bind authorized approval to the exact document hash, metadata, publisher identity, and sandbox/production destination. Edits invalidate approval; research tools cannot approve.
- [ ] **G4 — Integrate Zenodo sandbox.** Create a draft, upload, preview, approve, publish, and verify the resulting record. Test rejection, timeout reconciliation, expired approval, and duplicate prevention.
- [ ] **G5 — Verify production publication.** Configure production credentials securely; obtain explicit approval for a specific final artifact; publish and verify the real DOI. A sandbox record is not production publication. External approval/credentials remain recorded prerequisites until supplied.
- [ ] **G6 — Complete browser journeys.** Exercise finding → Formalise → Write → preview → approval → publication status. Check recoverability after a process restart.

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
