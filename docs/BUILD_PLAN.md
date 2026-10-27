# Polymath: Build Plan

**Deadline: Thu 5 Nov 2026, 09:00 Bangkok (Wed 4 Nov, 9:00 PM ET).**
**Target: submit by Wed 4 Nov, 21:00 Bangkok, leaving a 12-hour buffer.**

The plan runs in phases, and each phase ends in something that works end to end. If time runs short, use the **cut list** at the bottom. The order is chosen so we always have something submittable.

---

## Phase 0: Setup (Tue 27 Oct)

Human tasks (Eks):
- [ ] Join the hackathon **Discord** (link in the Cerebral Valley emails)
- [ ] Fill in the **$150 Meta Model API credits form**
- [ ] Create an account at **dev.meta.ai**, generate an API key, add a card, and set a **$150 spend limit** on the Billing page
- [ ] Read the **event details page** while signed in. Note the **problem statements** and pick the one Polymath fits (likely "long-running agents" or "tools other developers will want")
- [ ] Note the **exact submission requirements** (repo? video length? live URL? write-up fields?) and add them to `docs/HACKATHON.md`
- [ ] Read the **Official Rules**, especially the IP clause
- [ ] Pick **three seed concepts** for the demo, where a cross-field bridge is already suspected. Candidates to consider:
  - self-organised criticality (physics ↔ neuroscience ↔ finance ↔ ecology)
  - error-correcting codes (information theory ↔ genetics ↔ quantum matter)
  - phase transitions in learning (statistical physics ↔ deep learning ↔ developmental biology)
  - optimal foraging / explore–exploit (ecology ↔ reinforcement learning ↔ economics)

Agent tasks:
- [ ] Smoke-test the key: `curl https://api.meta.ai/v1/chat/completions` with `muse-spark-1.1`
- [ ] Check whether the Model API supports `tools` (function calling), `response_format`, streaming and image input. Record the answers in `docs/HACKATHON.md`
- [ ] If the API is region-locked from Thailand, raise it in Discord office hours. The fallback is an open-weights Meta model on a hosted provider

**Done when:** one successful Muse Spark call from the dev machine, and the problem statement and submission requirements are written down.

---

## Phase 1: Engine core (Wed 28 – Thu 29 Oct)

Build `polymath/` as specified in ARCHITECTURE.md:
- [ ] `pyproject.toml`, `LICENSE` (Apache-2.0), `.env.example`, `.gitignore` (`.env`, `*.db`)
- [ ] `config.py`, `models.py`
- [ ] `llm.py`: OpenAI-compatible client, retries, defensive JSON parsing, usage meter, `FakeLLM`
- [ ] `prompts.py`: every prompt plus its JSON contract
- [ ] `store.py`: SQLite schema and accessors
- [ ] `sources/arxiv.py`, `sources/openalex.py` (then `semanticscholar.py`), with rate limiting, dedupe and caching
- [ ] `scan.py`: choose fields, translate the concept per field, retrieve
- [ ] `signatures.py`: batched and cached
- [ ] `similarity.py`: pure-Python TF-IDF cosine, cross-field pairing
- [ ] `converge.py`: hypothesise → critique → novelty → ground quotes → score → persist
- [ ] `cli.py`: `polymath explore "<concept>" [--fake] [--fields N]` prints the ranked bridges
- [ ] `tests/`: fixture papers from 3 fields, FakeLLM responders, tests for JSON parsing, similarity, grounding, scoring and the full pipeline

**Done when:** `pytest` is green offline, and `polymath explore "self-organised criticality"` on the live API returns at least one bridge whose evidence quotes are verified against the source text.

---

## Phase 2: Web app and live deploy (Fri 30 – Sat 31 Oct)

- [ ] `runs.py`: background worker, event bus, events persisted for replay
- [ ] `server.py`: FastAPI with the endpoints in ARCHITECTURE.md §7 and SSE for `/events`
- [ ] `web/`: homepage (Explore / Ask input), **live run view** (streaming timeline: fields → papers → signatures → candidates → hypotheses → critic → bridges), **bridge cards** and a **bridge detail** page (papers side by side, mapping table, evidence quotes, critic verdict, novelty, confidence)
- [ ] Design: clean, confident, "scientific instrument" feel. Light and dark mode, and it works on mobile
- [ ] `Dockerfile`, `render.yaml`, a persistent disk for SQLite
- [ ] Deploy to **Render** and set env vars in the dashboard
- [ ] Rate limiting (per IP plus a global daily cap) so the public URL can't drain the credits

**Done when:** a public URL runs a full Explore with the live stream visible, using Muse Spark.

---

## Phase 3: Ask, Formalise, Write, Publish (Sun 1 – Mon 2 Nov)

- [ ] `ask.py`: plan → reuse graph bridges → Explore where coverage is thin → labelled answer (Known / Postulated / Speculative), with a UI that colour-codes the claims and links them to bridges and papers
- [ ] `write.py`: summary and formal-paper markdown, with an in-app preview and download
- [ ] `publish.py`: Zenodo deposition (sandbox by default) **only** after an explicit Approve click. The API returns 403 without `approve: true`
- [ ] `formalise.py`: Lean 4 statement + proof sketch. If `LEAN_BIN` is set, run the compile → repair loop (max 3 rounds), and show the status badge
- [ ] Graph view (`/api/graph`): fields as clusters, bridges as edges
- [ ] **Stretch:** `meta.py` meta-convergence, level 2 over the stored bridges
- [ ] **Stretch:** agentic Explore using Muse Spark tool calling (if Phase 0 confirmed `tools` support), with long-run context compaction

**Done when:** Ask, Write, Approve → sandbox DOI, and Formalise all work on the live URL.

---

## Phase 4: Quality, demo, submission (Tue 3 – Wed 4 Nov)

- [ ] Run the three seed concepts live. Review the bridges by hand and tune prompts and scoring until at least one bridge is non-obvious and defensible
- [ ] `DEMO_MODE`: showcase runs cached so the homepage loads instantly with real results
- [ ] README polish: what it is, a GIF/screenshot, the live link, how to run, architecture diagram, how Meta tooling is used
- [ ] Make the repo **public**
- [ ] Record the **demo video** (3 minutes or less, script in SPEC.md §7)
- [ ] Publish a short write-up to Zenodo (live) → DOI = dated prior art
- [ ] Write the submission text and file it on the Cerebral Valley portal **by Wed 4 Nov, 21:00 Bangkok**
- [ ] Post on LinkedIn with the demo

**Done when:** submitted, with the live URL, the public repo and the video.

---

## Cut list (in this order, if behind)
1. Agentic tool-calling Explore → keep the fixed pipeline
2. Meta-convergence → describe it in the README as next
3. Semantic Scholar connector → arXiv + OpenAlex is enough
4. Lean compile loop → keep statement-only, status `unchecked`
5. Graph view → a bridges list is enough
6. Live Zenodo publish → sandbox only

**Never cut:** Explore with the critic and grounded evidence, the live run view, the live URL, the demo video.

---

## After the hackathon (roadmap)
- **Make it true:** stronger critic, passage-level grounding from full text, calibrated confidence, expert review loop
- **Formalise for real:** Lean + Mathlib autoformalisation with proved / disproved / open as first-class results
- **Make it continuous:** nightly ingest, convergence re-runs, and the meta-convergence ladder (level 2, 3, …)
- **More sources:** bioRxiv/medRxiv, PubMed, SSRN, Zenodo, GitHub, full-text PDFs and figures (multimodal)
- **Make it everyone's:** accounts, a public API, community verification and attribution of bridges
