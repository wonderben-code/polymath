# Submission pack

Drafts for the Meta Global AI Developer Hackathon 2026 submission (Indie
Developer track). **Deadline: Thu 5 Nov 2026, 09:00 Bangkok (Wed 4 Nov, 9:00 PM
ET). Target: Wed 4 Nov, 21:00 Bangkok.** Live at **https://askpolymath.com**.

Everything below must describe only what is true at submission time. Replace
each `⟨…⟩` with recorded evidence, and delete any line the release cannot
support (for example, if live research has not been switched on).

## Form answers (paste these into the Cerebral Valley "Submit Project" dialog)

The questions are captured word for word in [SUBMISSION_QUESTIONS.md](SUBMISSION_QUESTIONS.md). Text answers below are final; the opinion questions carry a recommended choice and the reason, and the owner picks.

**1. Team Name:** Polymath

**2. Team Members:** pre-filled. The form shows "Ekram Alam" and "jackson2045". Polymath is a solo project; if jackson2045 is not a teammate, remove that entry before submitting (the guide asks that everyone listed actually worked on the project).

**3. Track:** Indie Developer Track

**4. Problem statement:** Push the Frontier

**5. Most Viral Track X post link:** optional; leave blank unless a post exists.

**6. Project Description:**

> Polymath is an AI research partner that finds discoveries hiding between fields of science. Science is split into thousands of specialties, so an answer that already exists in one field often stays unknown in the field that needs it. Polymath gives Meta's Muse Spark the whole open record of science to read, lets it decide where to look, and checks every claim against the actual papers.
>
> Five tools, all live at https://askpolymath.com and free to try:
> • Ask: a question becomes numbered claims, each labelled Known, Postulated or Speculative and backed by the exact passage it came from, plus what remains unanswered.
> • Solve: a problem is answered from anywhere in science; if no single answer fits, Muse Spark borrows the closest fits from other fields and forges a new, combined solution, stating where it is still weak.
> • Converge: one theme, twelve distant fields; every connection is tested against the literature and only what survives is drawn inward into a deeper synthesis.
> • Frontier: an unsolved problem becomes a live research programme of gaps and steps (a six-step plan for a space elevator in nineteen minutes).
> • Studio: a brief becomes an invention built from mechanisms that already work in nature and other fields, engineered with calculations, stress-tested against your requirements, and painted by Muse Image.
>
> Truth over plausibility: a finding can only quote text Polymath actually retrieved and saved from arXiv and OpenAlex, every quotation is matched against the saved source, and an honest "not supported" is a valid result. Findings can be formalised, written up and published to Zenodo under the user's own name.
>
> Everything was built during the hackathon: the first commit is 27 October 2026, and every push to main is Bitcoin-timestamped (OpenTimestamps proofs in the repo), so the whole timeline is verifiable. Built solo.

**7. Public GitHub Repository:** https://github.com/wonderben-code/polymath

**8. 3-Minute Demo Video:** https://youtu.be/RSdVl98prVA (public; demo/film/polymath-demo-B-voiced.mp4, 2 min 57 s)

**9. Describe your use of Meta products to build the submitted project:**

> Meta's Muse Spark (muse-spark-1.3, through the Meta Model API chat completions endpoint) is the research intelligence, not a summariser bolted onto a pipeline. At every step of an investigation it chooses the next action from a typed set: search the literature for discovery, counter-evidence or prior work; read an abstract; open an arXiv paper's section index and read a chosen full-text window; search its own saved findings; record or discard a hypothesis; or conclude. Polymath enforces the tool contracts, evidence integrity, durable execution and the spending ledger; the model decides what to do.
>
> On top of that loop, Muse Spark does the creative work: it picks distant fields itself (Surprise and Converge), judges whether an existing answer fits a problem and forges combined solutions (Solve), raises the strongest objection to each of its own connections and rules on it, climbs from connections to connections between connections, breaks an open problem into gaps and steps (Frontier), and in Studio abstracts a brief into functions, hunts far fields for mechanisms, invents a design, works out the engineering numbers and stress-tests it. It also formalises findings into conjectures, writes summaries and papers with exact citations, and reassesses them against new papers.
>
> Meta Muse Image (muse-image-1.0) paints each Studio invention: concept art, the design in its world, an engineering cutaway and the key mechanism up close, from prompts Muse Spark writes for the design it invented.
>
> Every model call is reserved against a hard USD ledger before it is sent, with per-visitor and public caps, so one credit pool funds free public use without surprises. The whole live site ran on Muse Spark and Muse Image for every visitor during testing and judging.

**10. Why are Meta's models important to your solution?**

> Polymath only works if the model is doing real judgement at every step: which field to look in, which passage to read next, whether an objection defeats a connection, how partial answers from different fields combine, and what new design they make possible. That needs a frontier reasoning model that can run long, tool-using loops with structured outputs, reliably and cheaply enough to offer free to anyone. Muse Spark does that today: it plans searches, reads real papers, reasons beyond them and produces exact structured actions and citations, and Muse Image turns the inventions into pictures. A model on its own cannot read, quote and remember the world's science; a search engine on its own cannot judge, combine or invent. Meta's models are the half that thinks.

**11. Feedback from your experience using partner technologies:** Not used. Polymath runs on its own stack (FastAPI, PostgreSQL, React, Docker on DigitalOcean) and calls the Meta Model API directly.

**12. How likely are you to build with Meta Model API?** Recommended: Very likely (the product is built on it and ran live for visitors).

**13. Overall opinion of Meta Model API:** Recommended: Very favorable.

**14. Consent checkbox:** tick it (required to submit).

**15. Recommend the Meta Model API (0 to 5):** Recommended: 5.

**16. Recommend Muse Code (0 to 5):** Muse Code was not used to build Polymath. Recommended: leave unanswered (the question is optional), or 3 if the form insists.

**17. Biggest obstacle:** Recommended: "Something else (please specify)": Meta's content filter refusing ordinary scientific prompts (for example a clinic vaccine-cooling problem was refused with content_policy_violation), and occasional timed-out calls with no usage report, which the app has to settle on its own ledger. Alternative if a listed option is preferred: "Code or answers from the model that were wrong or not useful".

**18. Compared with other AI models for the same kind of work:** Recommended: "We haven't used other models for this kind of work" (Polymath used Meta's models only; no other provider was tried for this project).

**19. What matters most when choosing a model:** Recommended: "Agent abilities such as tool calling and multi-step tasks" (Polymath is a long tool-using loop; quality of answers is a close second).

**20. Hardest thing about switching models:** Recommended: "Our product is tuned to our current model's behavior and prompts" (the research prompts and action protocols are pinned and hashed per activation).

**21. What would most likely stop production use of Meta's models:** Recommended: "Missing production features, such as batch processing or spending limits" (the billing page's spend limit is a notification rather than a hard cap, so Polymath had to build its own ledger).

## Short description (about 60 words)

> Polymath × Meta: Meta's frontier Muse models bring the reasoning and
> creativity; Polymath brings an engine across the world's science that reads,
> checks every quote and remembers. Together they invent new things from
> mechanisms that already work in nature and other fields, with calculations;
> solve problems with answers from distant fields; and find discoveries hiding
> between disciplines. Every claim cites its passage.

## Long description

**The problem.** Science is split into thousands of specialties, and far more is
published each year than anyone could read. So an answer found in one field
often stays unknown in the field that needs it. The leaps that connected fields
(the mathematics of heat becoming the pricing of options; ant foraging inspiring
network routing) used to depend on rare people who happened to know both.

**What Polymath does.**

- **Innovation Studio:** ask for anything to be created ("a submarine inspired
  by whales"). Polymath turns the brief into the functions it needs, hunts
  distant and strange corners of science for mechanisms that already achieve
  each one, invents a design from them, engineers it with worked calculations,
  and stress-tests it against your requirements. Download the dossier, refine
  it, or continue in Meta AI.
- **Solve:** describe a problem, the lock. Polymath looks for a key anywhere in
  science. If none fits, it finds the closest fits in other fields, says where
  each fits and falls short, and forges a new key by combining them. A forged
  key is drawn in the colours of the fields it came from.
- **Explore** a concept: bridges to other fields, each with papers on both
  sides, a structural mapping, the open question it could answer, and the
  strongest objection with a verdict on it.
- **Ask** a hard question: every claim is labelled Known, Postulated or
  Speculative, with its own cited passages.
- **Surprise me:** the model chooses distant fields itself and can climb to
  connections between connections.
- **Converge:** up to 200 fields at once, connected and then converged inward,
  level after level.
- **Frontier:** an open problem becomes a live research programme of gaps and
  steps, each labelled by its evidence; any step can be sent to Solve.
- **Evidence bar:** choose Frontier, Standard, Peer reviewed or Established
  literature for each investigation.
- **Watch it think:** every search, reading, hypothesis and setback streams live.
- **Your library:** findings, a map of how they connect, literature watches and
  publishing. Sign in with ORCID, GitHub or Google, or simply carry on as a guest.
- **Write and publish:** conjectures, summaries and papers with exact citations;
  connect your own Zenodo and publish under your own name, at the level of
  automation you choose.

**Truth over plausibility.** A finding can only cite text Polymath actually
retrieved and saved, and every quotation is matched against the saved source.
When the evidence is weak, Polymath says so; an honest rejection is a valid
result.

## How Polymath uses Meta AI (judging: 25%)

Meta's model is the research intelligence, not a summariser bolted onto a
pipeline. The application supplies tools, evidence integrity, durable execution
and budgets; the model decides what to do.

1. **Model-directed research loop.** At every step Muse Spark chooses the next
   action from a typed set: search for discovery, counterevidence or prior work;
   read an abstract; open an arXiv paper's section index; read a chosen
   full-text window; search or revisit its own saved findings; record or discard
   a hypothesis; or conclude.
2. **Invention.** In Studio, the model abstracts a brief into functions, picks
   distant fields to search, then reasons beyond the sources to a new design,
   marking what is evidenced and what is its own invention, and works out the
   engineering numbers. **Meta Muse Image** then paints a gallery of five to
   seven pictures chosen for each invention (concept art, in its world, an
   engineering cutaway, the key mechanism up close, where the ideas came from,
   and how it works or its scale when they matter), all on the same hard
   budget ledger.
3. **Key finding and key forging.** In Solve, the model judges whether an
   existing answer fits, finds close fits in other fields and their exact
   shortfalls, then reasons over two to four of them to forge a combined
   solution, tested against the original evidence.
4. **Autonomous field selection.** In Surprise me the model picks distant fields
   and explains why. No field list is supplied.
5. **Self-critique.** Each bridge carries the strongest objection the model could
   find, its verdict on it, and a prior-work search.
6. **Connections between connections.** The model decides whether to save,
   deepen, combine, rank or exclude bridges, inside one budget.
7. **Formalise, write and reassess.** The same model turns findings into
   conjectures, summaries and papers, and reassesses them against new papers.
8. **Safe at public scale.** Every call is reserved against a hard USD ledger
   before it is sent, so one $150 credit pool can fund visitors without surprises.

Model: `muse-spark-1.3` through `https://api.meta.ai/v1/chat/completions`; pictures by Meta Muse Image.
Verified behaviour: probe receipt passed and live research activated on 1 Nov; every feature then run end to end by fresh visitors on askpolymath.com with real Meta intelligence on 1 and 2 Nov (Ask, Explore, Surprise, Solve with a forged key, Converge to a level-2 synthesis, Frontier to a six-step plan, Studio through all five stages with Muse Image galleries, exports, sharing, literature watches, leave-and-return), recorded in [PROGRESS.md](PROGRESS.md) and on film in [demo/README.md](demo/README.md).

## Why Meta's models matter

Polymath is Meta × the world's science. Neither half works alone: a model on its
own cannot read, quote and remember hundreds of millions of papers, and a search
engine on its own cannot judge, combine or invent. Polymath's value depends on
judgement at every step: which field to look in, which passage to read next,
whether an objection defeats a connection, how partial answers from different
fields combine, and what new design they make possible. That needs a frontier
reasoning model running long, tool-using loops cheaply enough to offer free to
visitors. Meta's Muse Spark does that today, and because the model is a server
setting, Polymath can move to Muse Wonder or any stronger Meta model as it
arrives.

## Demo video (3:00 maximum)

**The submission video is `demo/film/polymath-demo-B-voiced.mp4`** (cut B with the narration, 2 min 57 s; `demo/film/README.md` marks it final and cut A as the unused backup). Upload it to YouTube and paste the link into item 8 above.

The film is cut from live screen recordings made on 2 Nov by fresh visitors (every feature, real Meta intelligence, the live ledger). The narration, timings and shot list are in [demo/VIDEO_SCRIPT.md](demo/VIDEO_SCRIPT.md); the clips and frames are in [demo/](demo/README.md). Sped-up passages are visible as such; no operator codes, cookies or settings appear.

## Release checklist

Owner:

- [x] Meta credits claimed and key on the server (spend alert at £100; Polymath's own cap enforces the limit)
- [x] Host and domain: askpolymath.com on DigitalOcean (deployed 1 Nov)
- [x] Paid testing authorised (caps USD 145 total, USD 75 a day; about USD 39 counted by 2 Nov)
- [ ] OpenAlex logo file (openalex.org blocks automated downloads; Claude in Chrome can fetch it)
- [x] Sign-in (ORCID, GitHub, Google) and Zenodo (production, sandbox) apps registered, saved and tested live on 1 Nov ([SIGN_IN_SETUP.md](SIGN_IN_SETUP.md))
- [x] Privacy policy and terms live; Google sign-in app published (In production)
- [x] Demo video rendered (`demo/film/polymath-demo-B-voiced.mp4`, 4 Nov)
- [x] Demo video uploaded to YouTube: https://youtu.be/RSdVl98prVA (4 Nov)
- [x] Submission form completed and submitted (4 Nov 2026, before the 5 Nov 09:00 Bangkok deadline)

Known external windows: Zenodo scheduled maintenance Tue 3 Nov, 08:00 to 08:15 UTC
(Connect and publishing briefly unavailable). ORCID sign-in uses the free Public
API, which is for non-commercial use; a commercial Polymath would need ORCID
membership.

Engineering (evidence in [PROGRESS.md](PROGRESS.md)):

- [x] Free preflight: 11 passed, 0 failed (model `muse-spark-1.3`)
- [x] Production deployed with HTTPS, Secure cookies and redirects verified
- [x] Probe receipt passed; research activated 1 Nov ([KEY_DAY.md](KEY_DAY.md))
- [x] Live QA: every feature run end to end as a visitor on 1 and 2 Nov, defects fixed and redeployed the same day ([PROGRESS.md](PROGRESS.md), Live operations)
- [x] Rigour filters checked live against OpenAlex with the server's key
- [x] Public free-use funding reviewed with an exact deadline covering judging (judging delegation active 3 Nov until 2026-11-17T08:00Z: public USD 100 total, USD 20 a day; each visitor USD 4 total, USD 3 a day, 12 investigations a day)
- [x] Full regression green on main (Application checks on 50b0ce1, 3 Nov)
- [ ] Release commit, schema and public URL recorded at submission
