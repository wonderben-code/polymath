# Submission pack

Drafts for the Meta Global AI Developer Hackathon 2026 submission (Indie
Developer track). **Deadline: Thu 5 Nov 2026, 09:00 Bangkok (Wed 4 Nov, 9:00 PM
ET). Target: Wed 4 Nov, 21:00 Bangkok.** Confirm the official fields and video
limit on the Cerebral Valley portal before final edits ([HACKATHON.md](HACKATHON.md)).

Everything below must describe only what is true at submission time. Replace
each `⟨…⟩` with recorded evidence; delete any line the release cannot support.

## Short description (about 50 words)

> Polymath is an AI research partner, powered by Meta's Muse Spark, that reads
> across scientific fields at once. It finds where distant disciplines describe
> the same hidden structure, tries hard to break every connection, and links each
> claim to the exact source passage. Real connections become conjectures, papers
> and, with your approval, DOIs.

## Long description

**The problem.** Many valuable discoveries are not new facts but connections
between facts that already exist in different fields and vocabularies. No human
can read widely enough to find them systematically.

**What Polymath does.**

- **Explore** a concept and get *bridges*: each has papers on both sides, a
  structural mapping with its assumptions, the open question it could answer, the
  strongest objection and whether the bridge survives it.
- **Ask** a hard question and get an answer whose every claim is labelled
  Known, Postulated or Speculative, each with its own cited passages.
- **Surprise me**: the model chooses distant fields itself, hunts for shared
  structure and can climb to connections between connections.
- **Watch it think**: every search, reading, hypothesis and setback streams live.
- **Keep growing**: saved findings form a searchable memory and map; literature
  watches collect new papers, and findings can be reassessed against them.
- **Formalise and write**: findings become conjectures with bounded checks, plain
  summaries or full papers with exact citations.
- **Publish**: Zenodo DOIs only after a human approves the exact document.
- **Public API and library**: a documented HTTP API, a standalone client and a
  public library of findings their owners chose to share.

**Truth over plausibility.** A finding can only cite text Polymath actually
retrieved and saved, and every quotation is matched against the saved source.
When the evidence is weak, Polymath says so; an honest rejection is a valid
result.

## How Polymath uses Meta AI (judging: 25%)

Meta's model is the research intelligence, not a summariser bolted onto a
pipeline. The application supplies tools, evidence integrity, durable execution
and budgets; the model decides what to do.

1. **Model-directed research loop.** At every step Muse Spark chooses the next
   action from a typed set: search the literature for discovery, counterevidence
   or prior work; read an abstract; open an arXiv paper's section index; read a
   chosen full-text window; search or revisit its own saved findings; record a
   hypothesis or discard it; or conclude. It adapts to what each observation
   shows.
2. **Autonomous field selection.** In Surprise me the model picks two to four
   fields and explains why. No field list is supplied.
3. **Self-critique.** Each bridge must carry the strongest objection the model
   could find, its verdict on that objection and a prior-work search.
4. **Discovery ladder and ranked collections.** The model decides whether to save
   another bridge, deepen one, connect saved bridges into a higher-level
   connection, rank or exclude bridges, or stop, all inside one budget.
5. **Formalise, Write and reassess.** The same model turns findings into
   conjectures, summaries and papers, and reassesses saved findings against newly
   collected papers. Its citations must resolve to original saved passages.
6. **Safe at public scale.** Every call is reserved against a hard USD ledger
   before it is sent and recorded with its reported usage, so one $150 credit
   pool can fund visitors without surprises.

Model: `⟨muse-spark-1.3 or the verified model⟩` through
`https://api.meta.ai/v1/chat/completions`. Verified behaviour:
`⟨probe receipt and first investigations from KEY_DAY.md⟩`.

## Demo video script (target 2:50; confirm the limit)

| Time | Show | Say |
|---|---|---|
| 0:00–0:15 | Home page lede | "Science's best discoveries often sit between fields. Polymath reads across them at once." |
| 0:15–0:45 | Press **Surprise me**; the live timeline shows the model choosing fields and searching | "Muse Spark chooses the fields itself and explains why. Every search, reading and hypothesis streams live." |
| 0:45–1:15 | Open a bridge: mapping table, a source quote, the strongest objection | "Every claim links to the exact passage it came from. Polymath also argues against itself: here is the strongest objection, and why the bridge survives it." |
| 1:15–1:40 | **Ask** result with Known / Postulated / Speculative labels | "Ask gives you an answer where every statement is labelled by how well it is supported." |
| 1:40–2:00 | Knowledge map; select two bridges and start a higher-level investigation | "Saved findings become a map, and Polymath can look for connections between connections." |
| 2:00–2:20 | A literature watch adds a paper; the reassessment marker | "It keeps reading new papers and re-checks what it found." *(Label a recorded example as recorded.)* |
| 2:20–2:40 | Formalise → conjecture; Write → paper; the exact approval screen | "Real connections become conjectures and papers. Nothing is published without a human approving the exact document." |
| 2:40–2:50 | Public URL, "Powered by Meta Muse Spark", the GitHub repository | "Polymath: an open research partner for discoveries between fields." |

Recording notes: record against the deployed site; use real runs from the three
evaluation seeds. A recorded or sped-up segment must be labelled on screen.
Do not show operator tokens, cookies or the budget desk's credentials.

## Release checklist

Owner:

- [ ] Meta credits claimed, $150 spend limit set, key stored in environment settings
- [ ] Paid testing authorized (in your own words)
- [ ] Host approved and domain chosen
- [ ] Zenodo sandbox account, then production approval for one exact artifact
- [ ] Official rules, submission fields and video limit confirmed
- [ ] Repository visibility and licence confirmed for submission

Engineering (record evidence in [PROGRESS.md](PROGRESS.md)):

- [ ] `make preflight` and `--check-key` pass; model chosen
- [ ] Probe receipt passed; research activated ([KEY_DAY.md](KEY_DAY.md))
- [ ] Three seed investigations and one weak input run live and reviewed
- [ ] Production stack deployed; HTTPS, Secure cookies and SSE reconnect verified
- [ ] Public research funding reviewed with an exact deadline, or left off
- [ ] Full regression: backend unit, PostgreSQL and web suites green on the release commit
- [ ] Every feature journey exercised on the deployed site
- [ ] Backup taken; restore quarantine rehearsed on a copy
- [ ] Demo video recorded and uploaded; README links to it
- [ ] Release commit, image digest, schema and public URL recorded
- [ ] Submitted; confirmation saved
