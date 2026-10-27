# Polymath

**The convergence engine.** Polymath reads across every scientific field at once and finds the connections nobody has made yet. When two fields are working on the same problem in different words, it spots that, checks it, and writes it up.

> Science's hardest open problems increasingly sit *between* disciplines. A result in condensed-matter physics may already answer a question in biology, and neither community knows. The literature is too large for any human to span, but not for a long-running agent.

Built by Ekram Alam for the **Meta Global AI Developer Hackathon 2026** (Indie Developer track). Muse Spark, through the Meta Model API, is the brain throughout.

Created 27 October 2026. Every push to `main` is Bitcoin-timestamped through OpenTimestamps (`.github/workflows/bitcoin-timestamp.yml`, proofs in `timestamps/`).

---

## The end product

A live website where anyone can:

1. **Explore a concept.** Type something like "self-organised criticality" or "error-correcting codes". Polymath runs a long research loop across fields and returns ranked **bridges**. Each bridge comes with the papers on both sides, a plain-English argument, a side-by-side mapping, the open question it might resolve, a confidence score, and the evidence.
2. **Ask "what is the truth about X?"** It's a search engine for answers nobody has written yet, put together from verified convergences. Every claim carries a label: **Known**, **Postulated**, or **Speculative**.
3. **Watch it think.** A live run view streams each step as it happens: which fields it's searching, which papers it's reading, which bridge ideas it's forming, and which ones the critic breaks.
4. **Formalise.** Mathematical bridges get turned into a Lean conjecture, with a proof attempt where one is feasible.
5. **Write it up.** One click gives a plain summary or a formal paper draft. After you approve it, it publishes to Zenodo with a DOI. Nothing goes out without a human saying yes.
6. **Climb the ladder.** Polymath then looks for bridges between bridges (meta-convergences), and keeps going up a level at a time.

It's deployed on a public URL, runs on Muse Spark, and is open-source.

## Docs

| Doc | What's in it |
|---|---|
| [docs/SPEC.md](docs/SPEC.md) | Product spec: what we're building, every feature, user flows, what "done" looks like |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Tech stack, modules, data model, API, prompts, Meta tooling, deployment, secrets |
| [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md) | Phase-by-phase, day-by-day roadmap to the 5 Nov deadline, with acceptance criteria |
| [docs/HACKATHON.md](docs/HACKATHON.md) | Dates, judging criteria, setup checklist, submission requirements, IP notes |
| [CLAUDE.md](CLAUDE.md) | Instructions for any coding agent picking this project up |

## Status

- [x] Repo created, Bitcoin timestamping live
- [x] Spec, architecture and build plan written
- [ ] Phase 0: setup (Meta API key, credits, Discord)
- [ ] Phase 1: engine core
- [ ] Phase 2: web app and live deploy
- [ ] Phase 3: Ask, Formalise, Write
- [ ] Phase 4: polish, demo video, submission

## Quick start (once Phase 1 lands)

```bash
cp .env.example .env          # add MODEL_API_KEY from dev.meta.ai
pip install -e ".[dev]"
polymath explore "self-organised criticality"
polymath serve                # http://localhost:8000
```

## Licence

Apache 2.0 (to be added with the first code commit).
