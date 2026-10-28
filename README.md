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
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Local development, tests, PostgreSQL migrations, and container startup |
| [docs/FULL_TEXT.md](docs/FULL_TEXT.md) | Model-selected arXiv HTML indexes, exact saved excerpts, citation scope and recovery |
| [docs/PUBLIC_SHARING.md](docs/PUBLIC_SHARING.md) | Explicit public snapshots, short cited excerpts, expiry, revocation and public-library API |
| [docs/API.md](docs/API.md) | Versioned HTTP contract, ownership/CSRF, event replay and standalone external client |
| [docs/META_READINESS.md](docs/META_READINESS.md) | Operator-attested model/billing proposals, conservative cost quotes, budget inspection and safe disable |
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
- [ ] B–C live gates: native model/billing verification, execution activation and scientific Explore evaluation
- [x] D3 local contract/client: documented v1 API and standalone HTTP client, verified with real loopback worker journeys
- [ ] D live gates: public URL, hosted access/operations and deployed external-client verification
- [x] E1–E3: Ask and Surprise Me implementation, verified through synthetic worker/browser journeys
- [ ] E4: live Meta/source evaluation and scientific quality review
- [x] F3–F5 implementation: higher-level research, scheduled collection and cited reassessment
- [x] Shared public reading: exact owner previews, cited snapshots, library/map, expiry and revocation
- [ ] F live gates: native scientific evaluation, authorized public-memory reuse and deployment
- [x] G1–G2: Formalise and Write implementation with bounded checks and exact source-backed exports
- [x] G3: authenticated exact-document publication preview, approval and revocation
- [x] G4 implementation: durable publication jobs, exact readback checks and safe reconciliation with mocked HTTP
- [ ] G4–G6 live gates: approved native sandbox/production publication and deployed verification
- [ ] H: complete-product validation, polish, and submission

Explore, Ask, and Surprise Me now share the worker, budget controls, immutable evidence, and session-owned activity/history APIs. Ask shows classified, individually cited claims; Surprise Me records model-chosen fields. A private knowledge search/map opens saved findings and evidence relationships. The intelligence can search those saved findings and reread their original sources, with immutable provenance and safe restart replay. Selecting 2–4 bridges launches a higher-level investigation whose citations must trace to original evidence from every selected bridge. Saved findings can become model-generated conjectures, summaries, and papers with original citations, bounded mathematical checks, preview/history, and exact Markdown exports. These paths are tested with synthetic models/sources. Exact publication previews and approvals require a separate publisher login; changed versions need fresh approval. A separate publishing worker verifies exact file bytes and metadata before and after publication, records every mutation grant, and reconciles uncertain outcomes without automatically resending. Scheduled literature watches now preserve bounded provider cursors and add original unassessed source versions to the knowledge map. Exact source inspection and provider-declared bibliography distinguish new reading from a scientific finding. Explicit Meta-led reassessment now compares exact new versions, preserves historical findings and applies cited revisions through the same budgeted worker. Stale or unsupported findings cannot silently become fresh writing/publication inputs. Meta can explicitly open versioned arXiv HTML indexes and choose exact saved excerpts; citation coverage follows those windows through research, memory, reassessment and writing. Native provider/Zenodo/Meta verification, scientific evaluation, real publication and public deployment remain incomplete; paid calls stay disabled. See the [development guide](docs/DEVELOPMENT.md) and [progress tracker](docs/PROGRESS.md).

Explicit owner sharing now creates an exact public finding snapshot with short cited excerpts, preserved uncertainty, an expiry date and a revoke control. A separate public library/search/map reads only those shared snapshots, without opening private investigations or starting research. Later reassessment updates the status badge while preserving the original shared content. This feature is separately disabled by default; enabling it never makes existing findings public automatically.

## Licence
Apache 2.0. See [LICENSE](LICENSE).
