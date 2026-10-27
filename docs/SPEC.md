# Polymath: What We're Building

*Author: Ekram Alam. Concept first written 14 Oct 2026 and extended into a platform 20 Oct 2026. Built in this repo from 27 Oct 2026.*

> This document covers **WHAT** Polymath is and what it lets people do. **HOW** it's built (architecture, models, tooling, infrastructure) is deliberately left open, to be designed separately and recorded in `docs/ARCHITECTURE.md` once decided.

---

## 1. The problem

Science has split into thousands of specialisms that no longer read each other. Many of the most valuable discoveries open to us today are not new facts. They are **connections between facts that already exist**, sitting in different fields and written in different vocabularies. A result in condensed-matter physics may already answer a question in biology, and neither community knows.

Whole new fields have been born exactly this way (biophysics, computational neuroscience, econophysics, bioinformatics), each when someone noticed two communities solving the same problem. No human can read widely enough to find these connections systematically.

## 2. What Polymath is

**Polymath is an AI research partner that reads across every field at once, finds where different fields are working on the same problem in different words, checks those connections hard, and helps you turn the real ones into knowledge: answers, conjectures and published papers.**

It behaves like a brilliant, tireless human polymath, not a script. It decides where to look, follows surprising leads, digs deeper where the signal is strong, drops dead ends, argues with itself, and tells you honestly how confident it is.

## 3. What it lets people do

### 3.1 Explore a concept → discover bridges
Give it any concept ("self-organised criticality", "error-correcting codes", "explore vs exploit"). Polymath goes looking across the whole of science for places where the same underlying structure shows up under other names, and comes back with **bridges**.

Each bridge shows:
- the two (or more) fields it connects, and the specific papers on each side
- a plain-English explanation of *why* these are the same problem in disguise
- a side-by-side **mapping**: what in field A corresponds to what in field B
- the **open question** this connection might help answer
- **evidence**: exact quotes from the papers, linked to the source
- the **strongest objection** to the connection and whether the bridge survives it
- whether the connection is **new**, partly known, or already known
- a **confidence** score

### 3.2 Ask "what is the truth about X?" → a search engine for the unknown
Ask a question nobody has fully answered ("Why is sleep necessary?", "What really limits how fast cities grow?"). Ordinary search returns what has already been written. Polymath puts together an answer from connections across fields, including **things that follow from the literature but haven't been written anywhere yet**.

Every statement in the answer is clearly marked:
- **Known**: stated in the literature, with the source
- **Postulated**: follows from a verified connection but isn't yet stated anywhere
- **Speculative**: an interesting possibility with weaker support

### 3.3 Watch it think
While it works, you can watch the research happen live: which fields it's searching, which papers it's reading, which hunches it's forming, which ones it throws out, and why. You don't just get a result. You can see how it got there.

### 3.4 Go up the ladder: connections between connections
Once Polymath has found bridges, it can look for **patterns across the bridges themselves** (meta-connections), then patterns across those, and so on. This is how new fields could be discovered automatically.

### 3.5 A knowledge base that keeps growing
Everything Polymath reads and finds is kept. It stays connected to the places where research is published (arXiv, OpenAlex, Semantic Scholar, and more over time), so it keeps learning as new papers appear, and re-checks whether new work connects to what it has already found. Users can browse everything it has discovered as a map of fields and the bridges between them.

### 3.6 Turn a hunch into mathematics
Where a connection has mathematical content, Polymath can turn it into a **precise mathematical conjecture**, try to **prove or disprove** it, and report the result honestly: proved, disproved, or stated-but-open. Even a clearly stated open conjecture is a contribution.

### 3.7 Write it up and publish it
With one click, any bridge or answer becomes either:
- an **easy plain-English summary**, or
- a **formal research paper** (abstract, background, the connection, evidence, limitations, references).

It can then be **published to Zenodo with a permanent DOI**, but only after the user explicitly approves. Nothing is ever published without a human saying yes.

### 3.8 For everyone
Open to anyone through the website, with an API so researchers and developers can point Polymath at their own field. The tool is open, and findings are open by default.

## 4. Product principles
1. **True, not just plausible.** No connection is shown without real evidence and a serious attempt to break it. Weak connections are labelled as weak.
2. **Checkable in minutes.** Every claim links to the exact passage and source.
3. **Intelligent, not scripted.** The system reasons about where to look and what to believe, the way a great researcher would.
4. **Honest.** If nothing strong turns up, it says so.
5. **Humans decide what gets published.**

## 5. Suggested initial user flow *(draft, to be reviewed)*

**Flow A: Explore (the main demo)**
1. The user lands on the homepage, which has one search box with two modes: **Explore a concept** / **Ask a question**.
2. They type a concept and press Explore.
3. The **live research view** opens and shows the work as it happens: fields being searched, papers being read, hunches forming, objections raised, leads dropped.
4. A few minutes later, **ranked bridge cards** appear.
5. Opening a bridge shows the papers side by side, the mapping, the evidence quotes, the strongest objection and verdict, novelty and confidence.
6. Optional: **Formalise**, to turn it into a mathematical conjecture and attempt a proof.
7. **Write**: plain summary or formal paper, previewed in the app.
8. **Approve & publish**, which gives a DOI.

**Flow B: Ask**
1. The user asks "What is the truth about X?"
2. The live research view shows Polymath checking what it already knows and exploring where it needs to.
3. An answer appears with each statement colour-coded Known / Postulated / Speculative, each linked to its bridges and papers, plus an honest "what we don't know" section.
4. Write / publish options, as in Flow A.

**Flow C: Map**
1. The user opens the map of everything Polymath has found: fields, bridges, and bridges between bridges.
2. Clicking any bridge opens its card. Clicking "go up a level" looks for connections between the selected bridges.

## 6. Hackathon constraints (fixed)
- The intelligence must run on **Meta's AI** (Muse Spark via the Meta Model API), with creative use of Meta tooling worth 25% of the score.
- It must be **live on a public URL** that judges can try.
- It must have a **working, impressive demo** and a demo video.
- Budget: **$150 in Meta API credits**.
- Deadline: **Thu 5 Nov 2026, 09:00 Bangkok** (Wed 4 Nov, 9:00 PM ET).

## 7. What "done" looks like for the hackathon
- [ ] Explore works end to end on the live URL, with evidence and objections
- [ ] Ask works, with Known / Postulated / Speculative labels
- [ ] The live research view shows the agent thinking
- [ ] Write (summary + paper) and approve-gated publishing work
- [ ] Formalise produces at least one mathematical conjecture from a real bridge
- [ ] At least one bridge a domain expert would call non-obvious and defensible
- [ ] Public repo, demo video, submission filed

**Stretch:** connections between connections (3.4), and the always-growing knowledge base running on its own (3.5).

## 8. Out of scope
- The author's unpublished theoretical research and any unrelated ventures are never used, described or referenced here. Polymath works on the open literature only.
- Accounts, payments and scale. Those come after the hackathon.
