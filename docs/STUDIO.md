# Innovation Studio (proposal)

Owner's idea, 1 November 2026. **Status: proposed; waiting on the owner's
decisions below before building.** Visual mockup:
[mockups/studio.html](mockups/studio.html) (illustrative placeholders only).

## The idea in one line

Describe something you want to make, with the specs it must meet. Polymath
searches distant and unusual corners of science for mechanisms that already
achieve each requirement somewhere else (an owl's silent wing, a dragonfly's
pleated wing, a maple seed's spin), designs something new from them, and tests
the design against your specs, the literature and its own strongest objection.

## How it differs from the other modes

| Mode | You bring | You get |
|---|---|---|
| Explore | a concept | the same idea in other fields |
| Solve | a problem (a lock) | an existing answer, or one forged from close fits |
| **Studio** | **a thing to make, plus specs** | **design concepts built from cross-field inspiration, checked against the specs** |

Solve answers "how could this work?" Studio answers "what should we build?"
They feed each other: every *still to invent* gap in a design can be sent to
Solve with one tap.

## How it works: Meta's intelligence plus the literature

Each stage is an ordinary Polymath investigation, like Solve and Frontier:
Meta's Muse Spark chooses every search and reading, quotes must match saved
text, objections are recorded, every call is budgeted, and the programme itself
never spends.

1. **Understand the brief (Ask).** "What must a [thing] achieve, which of these
   specs are hardest for today's designs, and why?" The answer's known claims
   give the state of the art (the baseline to beat). Its open questions become
   the **functions to find inspiration for** (for example *quiet lift*,
   *light stiff blades*, *safe descent without power*). This is the
   function-first method that biomimicry and TRIZ use: abstract the function,
   and the search can reach any field.
2. **Inspiration sweep (Explore, up to four).** For each function: "Find how
   distant fields achieve [function]: living things, geology, materials, other
   engineering, mathematics." The visitor's **wildness dial** (*nearby fields*,
   *far fields*, *strange corners*) sets how distant. Each saved bridge is one
   inspiration: the mechanism, the passage behind it, how it maps across, and
   where it may not transfer.
3. **Design (Explore, built on two to four inspirations).** The existing
   higher-level investigation combines them: "Integrate these into one design
   that meets the specs. Say what each source contributes, and what conflicts."
   A second combination can give a second concept.
4. **Stress test (Ask).** "Judge this design against each spec, check for prior
   art, and estimate the key numbers." Every verdict is labelled Known,
   Postulated or Speculative with its own passages. The strongest objection
   always covers **scale**, the classic trap of bio-inspired design: what works
   at insect size can fail at helicopter size. Optional **Formalise** turns the
   key quantities into explicit back-of-envelope checks with their formulas.

At most eight investigations per design; one at a time while the page is open;
every one also appears in Your library.

## What the visitor sees (see the mockup)

- **Composer:** the brief, spec chips (*+ Add a spec*), the wildness dial,
  *Design it*.
- **Design sheet** on a faint engineering grid:
  - the concept's name and one-line pitch, with an honest status label
    (*theoretical concept, not built or tested*);
  - **where the ideas came from:** a constellation with each source field in
    its Prism colour, and dashed lines for ideas considered and set aside;
  - an **inspiration card** for each idea, with the exact passage behind it;
  - **against your specs:** met by design, plausible or open, with reasons;
  - **the strongest objection** and its verdict, plus the prior art check;
  - **still to invent**, each gap with *Solve this*;
  - **back-of-envelope checks**;
  - actions: see concept 2, write it up as a concept note, publish to your
    Zenodo, open as a Frontier programme.

Publishing a concept note on Zenodo also creates public, dated prior art, which
is useful for inventors.

## Honesty rules

- Every inspiration must quote a real saved passage; no invented sources.
- A design is always labelled a proposal; the label never says *works*.
- Spec verdicts use the evidence labels, and numbers are labelled estimates.
- Ideas that failed the stress test stay visible as *considered, set aside*.

## Build plan (reuses the Solve architecture almost one for one)

1. Owner decisions below, then this document becomes the plan of record.
2. Engine: `studio_programmes` and `studio_steps` (schema 0034); planner
   (understand → inspire → design → stress test); attach validation;
   PostgreSQL tests for every path (inspirations found, none found, one design,
   two designs, failures retried once, ownership, limits).
3. API `/api/v1/studio` (same shape as Solve).
4. Web: composer, design sheet, constellation, autopilot, *Solve this*, write
   and publish; tests; real-browser check on desktop and phone.
5. Docs and submission: Studio in the demo script.
6. Live quality, once Meta research is switched on, with three briefs: a quiet
   city helicopter, a deep-sea submarine and a theoretical Dyson swarm.

Steps 2 to 5 are about one focused day, like Solve.

## Decisions for the owner

1. **Name:** *Studio* in the mode row, *Innovation Studio* as the page title
   (recommended); or *Invent* or *Design*.
2. **Place:** a mode in the main composer like Solve (recommended: one box,
   one place to start), or its own tab beside Frontier.
3. **Concept sketch:** if a Meta image model is available through the Meta
   Model API, add an *artist's impression* of each design. It would be clearly
   labelled and would be another creative use of Meta. Needs checking first.
