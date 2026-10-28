# Polymath

**An AI research partner that reads across every field at once and finds the connections nobody has made yet.** When two fields are working on the same problem in different words, Polymath spots it, tests it hard, and helps turn the real connections into answers, conjectures and published papers.

> Science's hardest open problems increasingly sit *between* disciplines. A result in condensed-matter physics may already answer a question in biology, and neither community knows. The literature is too large for any human to span, but not for an intelligent agent.

Built by Ekram Alam for the **Meta Global AI Developer Hackathon 2026** (Indie Developer track), powered by Meta's Muse Spark.

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
- [ ] B–C: durable research, evidence, spending controls, and Explore
- [ ] D: public URL and controlled public API
- [x] E1–E3: Ask and Surprise Me implementation, verified through synthetic worker/browser journeys
- [ ] E4: live Meta/source evaluation and scientific quality review
- [ ] F: growing knowledge base, map, and connections between connections
- [x] G1–G2: Formalise and Write implementation with bounded checks and exact source-backed exports
- [x] G3: authenticated exact-document publication preview, approval and revocation
- [ ] G4–G6: Zenodo publication and live/deployed verification
- [ ] H: complete-product validation, polish, and submission

Explore, Ask, and Surprise Me now share the worker, budget controls, immutable evidence, and session-owned activity/history APIs. Ask shows classified, individually cited claims; Surprise Me records model-chosen fields. A private knowledge search/map opens saved findings and evidence relationships. The intelligence can search those saved findings and reread their original sources, with immutable provenance and safe restart replay. Selecting 2–4 bridges launches a higher-level investigation whose citations must trace to original evidence from every selected bridge. Saved findings can become model-generated conjectures, summaries, and papers with original citations, bounded mathematical checks, preview/history, and exact Markdown exports. These paths are tested with synthetic models/sources. Exact publication previews and approvals require a separate publisher login; changed versions need fresh approval. External publication, full-text retrieval, live Meta verification, public execution, and later product features remain incomplete; paid calls stay disabled. See the [development guide](docs/DEVELOPMENT.md) and [progress tracker](docs/PROGRESS.md).

## Licence
Apache 2.0. See [LICENSE](LICENSE).
