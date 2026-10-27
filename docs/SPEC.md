# Polymath: Product Spec

*Author: Ekram Alam. Concept first written 14 Oct 2026 (as "Infinitography Agent") and extended into a platform 20 Oct 2026. This repo, created 27 Oct 2026, is where it gets built.*

## 1. Problem

Science has split into thousands of specialisms that no longer read each other. Many of the most valuable discoveries open to us today are not new facts. They are **connections between facts that already exist**, sitting in different silos and written in different vocabularies. Whole new fields have been born this way: biophysics, computational neuroscience, econophysics, bioinformatics. Each began when someone noticed two communities solving the same problem.

No human can read widely enough to find these connections systematically. A long-running agent can.

## 2. Product in one sentence

**Give Polymath a concept or a question. It reads across every field, finds work that is structurally the same problem in different words, stress-tests each connection, and returns verified, evidence-backed bridges and answers, with the option to formalise and publish them.**

## 3. Core concepts

| Term | Meaning |
|---|---|
| **Paper** | A normalised record from arXiv, OpenAlex, Semantic Scholar etc. (title, abstract, authors, year, URL, field) |
| **Structural signature** | A vocabulary-free description of a paper: the abstract *objects* involved, their *relations*, the *dynamics*, the *mathematical form*, and the *open problem*. This is the key trick. Two papers from different fields with similar signatures are candidates for being the same problem. |
| **Bridge** | A validated connection between work in field A and work in field B: papers on both sides, an argument, an explicit A↔B mapping, the open question it may resolve, evidence, the critic's verdict, novelty, and confidence |
| **Meta-bridge** | A bridge between bridges (level 2+). It's the same engine run over the bridge graph. |
| **Convergence graph** | Every paper, signature, bridge and meta-bridge, persisted. It grows with every run and it's the core asset. |
| **Epistemic label** | Every claim in an answer is **Known** (stated in the literature, cited), **Postulated** (follows from a verified bridge but isn't written anywhere yet), or **Speculative** (low confidence or an unverified chain) |

## 4. Features (the full set: "build it all")

### F1. Explore: concept → bridges *(core)*
- Input: a concept, e.g. "self-organised criticality".
- The agent picks the fields most likely to hold the same structure under different names, and translates the concept into each field's vocabulary.
- It retrieves papers per field, builds a structural signature for each, and pairs signatures **across** fields by similarity.
- For each top candidate pair: it hypothesises the bridge, a **critic agent** tries to break it, a **novelty check** looks for prior work that already links the two, and every claim is grounded in quoted evidence.
- Output: ranked bridges (0–1 confidence), each with papers, argument, mapping table, open question, evidence quotes, critic verdict and novelty.

### F2. Ask: "what is the truth about X?" *(core)*
- Input: a natural-language question.
- The agent breaks it into concepts, reuses bridges from the graph, and runs Explore where coverage is thin. It then writes an answer made of labelled claims (Known / Postulated / Speculative), each linked to its sources and bridges, plus an honest caveats section.
- This is the "search engine for the unknown".

### F3. Live run view *(core, and key to the demo)*
- Every step streams to the browser as it happens: fields chosen, queries, papers found, signatures, candidates, hypotheses, critic attacks, final ranking.
- It shows that this really is a *long-running agent*, which the judges explicitly asked for.

### F4. Meta-convergence *(stretch for the hackathon, core after it)*
- Run the engine over bridges: build signatures of bridges, pair them, and hypothesise level-2 bridges (patterns across the patterns).
- It can repeat to level 3+. This is the automated birth of fields.

### F5. Growing knowledge base *(core in a basic form)*
- Connectors for arXiv, OpenAlex and Semantic Scholar, with more to come (bioRxiv/medRxiv, PubMed, Zenodo, GitHub).
- Everything gets persisted. A scheduled job ingests new papers and re-runs convergence, so the graph gets richer on its own.

### F6. Formalise: thesis → maths *(visible stub for the hackathon)*
- For bridges with mathematical content, Muse Spark writes a Lean 4 statement of the conjecture the bridge implies, plus a proof attempt.
- If a Lean toolchain is available, a compile → read errors → repair loop runs.
- Statuses: **proved / disproved / open / unchecked**, and all of them are shown. Even a formalised-but-open conjecture is a contribution.

### F7. Write and publish *(core)*
- One click gives a **plain-English summary** (short sentences, no jargon) or a **formal paper** (abstract, background, the bridge, evidence, limitations, references).
- **Approve gate:** publishing to Zenodo needs an explicit human approval. It defaults to Zenodo *sandbox* until it's switched to live.

### F8. For everyone *(core)*
- A public web app, a JSON API, and a CLI. It's open-source, and open findings are the default.

## 5. User flows

**Flow A: Explore (the demo's opening)**
Land on the homepage → type a concept → press Explore → the live run view streams the agent's work (about 2–6 minutes) → ranked bridge cards appear → open one: papers side by side, mapping table, evidence, critic verdict → "Formalise" → "Write summary" or "Write paper" → "Approve & publish" → DOI.

**Flow B: Ask**
Type "What is the truth about why sleep is necessary?" → the run streams → you get an answer with colour-coded claims (Known / Postulated / Speculative) → each claim links to its bridges and papers.

**Flow C: Graph**
"Graph" tab → everything Polymath has found so far → bridges, meta-bridges and the fields they connect.

## 6. Quality bar
- **True, not just plausible.** No bridge is shown without evidence quotes and a critic verdict. Low-confidence bridges are labelled as such, never dressed up.
- **Checkable in minutes.** Every claim links to a passage and a source URL.
- **Honest.** When nothing strong turns up, Polymath says so.

## 7. Demo (25% of judging)
- Three **seed concepts**, picked in advance, where a real cross-field bridge is already suspected, so the video shows a genuine "huh" moment. (Pick during Phase 0, see BUILD_PLAN.md.)
- A video of 3 minutes or less: problem (20s) → live Explore run (60s) → bridge card deep-dive (40s) → Ask with labelled claims (30s) → formalise + write + approve (30s) → close.
- A live URL that judges can try themselves.

## 8. Definition of done (submission, Thu 5 Nov, 9:00 Bangkok time = Wed 4 Nov, 9:00 PM ET)
- [ ] Public GitHub repo with runnable code, README and Apache 2.0 licence
- [ ] Live URL running on Muse Spark
- [ ] Explore, Ask, live run view, Write and approve-gated publish all working end to end
- [ ] Formalise produces at least one Lean statement from a real bridge
- [ ] At least one bridge a domain expert would call non-obvious and defensible
- [ ] Demo video
- [ ] Submission written and filed on the Cerebral Valley portal

## 9. Out of scope (on purpose)
- The author's unpublished theoretical research and any unrelated ventures are never used, described or referenced here. Polymath works on the open literature only.
- User accounts, payments, and multi-tenant scale. Those come after the hackathon.
