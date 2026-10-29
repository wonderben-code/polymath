# Polymath

**An AI research partner that reads across every field at once and finds the connections nobody has made yet.** When two fields are working on the same problem in different words, Polymath spots it, tests it hard, and helps turn the real connections into answers, conjectures and published papers.

> Science's hardest open problems increasingly sit *between* disciplines. A result in condensed-matter physics may already answer a question in biology, and neither community knows. The literature is too large for any human to span, but not for an intelligent agent.

Built by Ekram Alam for the **Meta Global AI Developer Hackathon 2026** (Indie Developer track). Meta Muse Spark is the target research intelligence; another verified capable Meta frontier model is allowed. Native access and billing remain unverified, and paid execution is disabled.

Created 27 October 2026. Every push to `main` is Bitcoin-timestamped through OpenTimestamps (`.github/workflows/bitcoin-timestamp.yml`, proofs in `timestamps/`).

## What it lets you do
- **Explore a concept.** Discover bridges to other fields, each with papers on both sides, a mapping, evidence, the strongest objection, novelty and confidence.
- **Ask "what is the truth about X?"** A search engine for answers not yet written, with every statement marked Known / Postulated / Speculative.
- **Watch it think.** See the research unfold live.
- **Go up the ladder.** Find connections between connections.
- **Keep growing.** A knowledge base that keeps reading new research.
- **Turn hunches into maths.** Conjectures, with proof attempts.
- **Write and publish.** Plain summary or formal paper → approve → DOI on Zenodo.
- **Surprise me**. Polymath picks the fields itself and goes hunting for convergences, and you watch it think.

## Docs
| Doc | What's in it |
|---|---|
| [docs/SPEC.md](docs/SPEC.md) | **What** we're building: features, principles, suggested user flow, definition of done |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | **How**: Meta-driven research, evidence, storage, execution, deployment, and budget protection |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Full-product milestones and release targets to the 5 Nov deadline |
| [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md) | Development tasks, dependencies, and acceptance gates |
| [docs/PROGRESS.md](docs/PROGRESS.md) | Verified progress, blockers, evidence log, and the next task |
| [docs/KEY_DAY.md](docs/KEY_DAY.md) | Key-day checklist: verified Meta facts, free preflight and the path to the first real investigation |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Local development, tests, PostgreSQL migrations, and container startup |
| [docs/FULL_TEXT.md](docs/FULL_TEXT.md) | Model-selected arXiv HTML indexes, exact saved excerpts, citation scope and recovery |
| [docs/PUBLIC_SHARING.md](docs/PUBLIC_SHARING.md) | Explicit public snapshots, short cited excerpts, expiry, revocation and public-library API |
| [docs/API.md](docs/API.md) | Versioned HTTP contract, ownership/CSRF, event replay and standalone external client |
| [docs/META_READINESS.md](docs/META_READINESS.md) | Operator-attested model/billing proposals, conservative cost quotes, budget inspection and safe disable |
| [docs/META_VERIFICATION.md](docs/META_VERIFICATION.md) | Exact one-call Meta approval, shared budget, observation receipts and free recovery |
| [docs/META_RESEARCH.md](docs/META_RESEARCH.md) | Native Meta activation, all-task worker composition, per-run limits and exact recovery |
| [docs/DISCOVERY_LADDER.md](docs/DISCOVERY_LADDER.md) | Model-directed multi-bridge Surprise runs, exact parent evidence, partial discoveries and recovery |
| [docs/EXPLORE_COLLECTION.md](docs/EXPLORE_COLLECTION.md) | Model-ranked Explore collections, exact replay and remaining release verification |
| [docs/PUBLIC_RESEARCH_ACCESS.md](docs/PUBLIC_RESEARCH_ACCESS.md) | Controlled visitor access, explicit durable owner funding, shared-ledger quotas and resource controls; disabled by default |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Production stack, host comparison, launch, backups and quarantined restore |
| [docs/SUBMISSION.md](docs/SUBMISSION.md) | Submission descriptions, how Polymath uses Meta AI, demo script and release checklist |
| [docs/HACKATHON.md](docs/HACKATHON.md) | Dates, judging, setup checklist, IP notes |
| [CLAUDE.md](CLAUDE.md) | Instructions for any coding agent picking this up |

The implementation baseline is recorded in `docs/ARCHITECTURE.md`. The target is the complete specified product, including Surprise Me and the growing knowledge base. Meta intelligence drives the research decisions; the application supplies tools, persistent evidence, and controls.

## Status
- [x] Repo created, Bitcoin timestamping live
- [x] What we're building, defined
- [x] Architecture, full-product roadmap, development plan, and progress tracker recorded
- [x] A3: application foundation, PostgreSQL migrations, health checks, and automated checks
- [x] A4: bounded adaptive research-controller prototype and Meta adapter, verified with fixtures
- [ ] A1–A2, A5: verified Meta capabilities and first real-source investigation
- [x] B3.1: disabled Meta readiness proposals, authenticated budget inspection and safe disable
- [x] B3.2 implementation: exact one-call Meta approval, observed usage/receipts and free recovery; verified with mock transport
- [x] B3.3 implementation: native Meta composition for all research/writing/reassessment tasks, exact activation and safe resume; verified with mock transport
- [ ] B–C live gates: native model/billing verification, execution activation and scientific Explore evaluation
- [x] D3 local contract/client: documented v1 API and standalone HTTP client, verified with real loopback worker journeys
- [x] D2 implementation: funded visitor sessions, persistent resource controls and public browser/API/client access; local mock verification
- [ ] D live gates: public URL, hosted access/operations and deployed external-client verification
- [x] E1–E3: Ask and Surprise Me implementation, verified through synthetic worker/browser journeys
- [ ] E4: live Meta/source evaluation and scientific quality review
- [x] F3–F5 implementation: higher-level research, scheduled collection and cited reassessment
- [x] F6/C3 implementation: automatic Surprise discovery ladders, public hypothesis decisions and model-assessed novelty/confidence; local verification
- [x] Shared public reading: exact owner previews, cited snapshots, library/map, expiry and revocation
- [ ] F live gates: native scientific evaluation, authorized public-memory reuse and deployment
- [x] G1–G2: Formalise and Write implementation with bounded checks and exact source-backed exports
- [x] G3: authenticated exact-document publication preview, approval and revocation
- [x] G4 implementation: durable publication jobs, exact readback checks and safe reconciliation with mocked HTTP
- [ ] G4–G6 live gates: approved native sandbox/production publication and deployed verification
- [x] SC1–SC6 implementation: pinned scientific provenance, claim-linked Ask/reassessment, exact exports and public/client readers; saved as `a95011a` with 3,939 passing tests
- [x] EC1–EC6: Explore collection registry/admission, model-ranked atomic stages, native profiles/runtime and downstream provenance; focused mock verification
- [x] Explore release verification: 4,423 tests pass (2,194 unit + 1,311 PostgreSQL + 918 frontend), with connected browser/client journeys
- [x] Key-day kit: free `make preflight`, verified Meta API facts and the path to the first investigation ([KEY_DAY.md](docs/KEY_DAY.md))
- [x] Live free source checks: arXiv search/full text fixed for real responses; optional OpenAlex key
- [x] Production stack, HTTPS proxy, backups and restore quarantine prepared ([DEPLOYMENT.md](docs/DEPLOYMENT.md)); not yet hosted
- [x] Term and claim-level search of saved research
- [ ] H: complete-product validation, polish, and submission ([SUBMISSION.md](docs/SUBMISSION.md))

Explore, Ask, and Surprise Me now share the worker, budget controls, immutable evidence, and session-owned activity/history APIs. Ask shows classified, individually cited claims; Surprise Me records model-chosen fields. A private knowledge search/map opens saved findings and evidence relationships. The intelligence can search those saved findings and reread their original sources, with immutable provenance and safe restart replay. Selecting 2–4 bridges launches a higher-level investigation whose citations must trace to original evidence from every selected bridge. Saved findings can become model-generated conjectures, summaries, and papers with original citations, bounded mathematical checks, preview/history, and exact Markdown exports. These paths are tested with synthetic models/sources. Exact publication previews and approvals require a separate publisher login; changed versions need fresh approval. A separate publishing worker verifies exact file bytes and metadata before and after publication, records every mutation grant, and reconciles uncertain outcomes without automatically resending. Scheduled literature watches now preserve bounded provider cursors and add original unassessed source versions to the knowledge map. Exact source inspection and provider-declared bibliography distinguish new reading from a scientific finding. Explicit Meta-led reassessment now compares exact new versions, preserves historical findings and applies cited revisions through the same budgeted worker. Stale or unsupported findings cannot silently become fresh writing/publication inputs. Meta can explicitly open versioned arXiv HTML indexes and choose exact saved excerpts; citation coverage follows those windows through research, memory, reassessment and writing. Native provider/Zenodo/Meta verification, scientific evaluation, real publication and public deployment remain incomplete; paid calls stay disabled. See the [development guide](docs/DEVELOPMENT.md) and [progress tracker](docs/PROGRESS.md).

Explicit owner sharing now creates an exact public finding snapshot with short cited excerpts, preserved uncertainty, an expiry date and a revoke control. A separate public library/search/map reads only those shared snapshots, without opening private investigations or starting research. Later reassessment updates the status badge while preserving the original shared content. This feature is separately disabled by default; enabling it never makes existing findings public automatically.

## Licence
Apache 2.0. See [LICENSE](LICENSE).

A separate, disabled one-call Meta verification lane now uses the actual adapter with exact human approval and the shared ledger. Its immutable receipt records protocol behavior and reported usage; it grants no research activation and proves no pricing, funding or scientific claim. All verification checks so far use mock HTTP. No paid Meta call has run.

The native research path connects every existing task to the Meta adapter through
exact approval, original per-run limits, one shared ledger and recorded usage
before action validation. Private operator-browser approvals keep their original
scope. A separate explicitly reviewed durable delegation lets ordinary visitors
use the same engine without administrator credentials. Session/global funding,
queue/rate/body/stream limits and unknown-cost stops apply. Public research stays
disabled by default, and all checks still use mock Meta/provider HTTP.

Surprise can now opt into a discovery ladder: Meta decides whether to save more bridges, select earlier discoveries as premises, investigate a higher connection or stop. Every stage keeps exact original quotations, objections, model-assessed novelty and explicitly uncalibrated confidence. Saved stages remain readable after cancellation or limits; only an explicit model decision selects a final stage. The whole run shares one approved budget and call allowance. This is implemented locally with synthetic verification, not a claim of live scientific discovery.

New explicitly reviewed scientific-context profiles pin discovery ancestry,
original-bound confidence and applied reassessments before writing begins.
New Ask overviews use exact model-selected classified claims rather than a
separate summary. Revised answers retain that guarantee; public snapshots use
local claim IDs without private context. Legacy approvals, results and exports
retain their original bytes. See [the versioned contract](docs/SCIENTIFIC_CONTEXT.md).

Explicitly approved Explore collections now let Meta save several bridges,
rank or exclude them, and stop within the same run and budget. Partial findings
and exact original ranking coverage survive interruptions. Schema 0025,
native private/public adapters, downstream readers and UI/SDK are implemented;
verification uses synthetic evidence and mock HTTP. All 4,423 release tests and
the composite browser/client journeys passed, including the full 1,311-case
database run.
The prior SC release's 3,939 tests remain historical evidence. See the
[EC1–EC6 roadmap](docs/EXPLORE_COLLECTION.md).
