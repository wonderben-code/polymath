# Innovation Studio

Owner's idea, 1 November 2026; built the same day. Studio is reached from the
**Studio** place in the header (`/studio`, marked Experimental). The first
visual mockup is kept at [mockups/studio.html](mockups/studio.html).

## The idea in one line

Ask for anything to be created. Meta's Muse Spark searches distant and unusual
corners of science for mechanisms that already work (an owl's silent wing, a
maple seed's spin, a whale's dive). It invents a new design from them, engineers
it with worked calculations, and stress-tests it against your requirements. You
can then download it, refine it, or continue in Meta AI.

**Frontier intelligence plus the literature.** The literature supplies proven
raw material, each piece tied to a saved passage. Meta supplies the inventive
leap: it is told to be bold, to reason beyond its sources, and to say which
parts are evidenced and which are its own invention.

## How it differs from the other modes

| Mode | You bring | You get |
|---|---|---|
| Explore | a concept | the same idea in other fields |
| Solve | a problem (a lock) | an existing answer, or one forged from close fits |
| **Studio** | **something to create, plus requirements** | **a new invention built from cross-field mechanisms, engineered and stress-tested** |

Every *still to invent* gap on a Studio design can be sent to Solve.

## How it works

Each stage is an ordinary Polymath investigation: Meta chooses every search
and reading, quotes must match saved text, objections are recorded, and every
call is budgeted. The programme itself never calls a model or spends. The
visitor's browser queues each due stage through the ordinary run API and
attaches the run (`backend/src/polymath/studio.py`).

1. **Brief (Ask).** It finds what today's best designs achieve and which
   requirements are hardest, and why. Each hard requirement comes back as a
   function to achieve: *"How could a design achieve quiet lift while…?"* Once
   a requirement is stated as a function, the search can reach any field.
2. **Inspiration (Explore, up to four).** For each function, it finds a
   mechanism that already achieves it in a field far from the design's own.
   How far is set by the wildness dial: *Nearby fields*, *Far fields* or
   *Strange corners*. Each result gives the mechanism, how it maps into the
   design, and where it may not transfer (scale, materials, environment). With
   no named functions, three deliberately different places are searched.
3. **Invention (Explore built on 2–4 inspirations).** The higher-level
   investigation invents one design from them. It names each component and
   its source, explains how the whole works and where the parts conflict,
   marks what is evidenced and what is invented, and tests the design against
   its strongest objection, always including scale. A single inspiration
   cannot be a lineage parent, so it is carried in the goal instead.
4. **Engineering (Ask).** It specifies the components, materials, dimensions
   and operating parameters, and works the key calculations step by step:
   formula, sourced inputs, result and units. It labels which values come from
   the literature and which are design choices or estimates.
5. **Stress test (Ask).** It judges each requirement, checks whether the
   design or a close one already exists, checks the numbers, and names what
   still needs inventing.

That makes at most eight investigations per invention. Outcomes:
- *designed*: a design was made and engineered;
- *inspired*: mechanisms were found, but no design held up;
- *none*: no mechanism was found.

## What the visitor sees

- **Composer:** "What would you like to create?", requirement chips (up to
  six), the wildness dial, and four starters:
  - a bio-compatible outdoor neural interface;
  - a whale-inspired submarine;
  - a hospital-quiet helicopter;
  - a Dyson swarm.
- **Design sheet** on an engineering grid:
  - live progress (Brief, Inspiration, Invention, Engineering, Stress test);
  - an honest label: *Theoretical invention, not built or tested*;
  - the **idea map**, with each source field in its Prism colour flowing into
    the invention, dashed lines for ideas considered and set aside, and live
    "searching" threads while running;
  - **where the ideas came from**, each with its limits and evidence;
  - **the invention**: how the sources become the design, plus the strongest
    objection and its verdict;
  - **engineering and calculations**, and the **stress test**, each claim
    labelled *Known*, *Postulated* or *Speculative*;
  - **still to invent**, each item with *Solve this*;
  - **Take it further**:
    - *Download the design dossier* (a complete Markdown document);
    - *Continue in Meta AI* (copies a design briefing and opens meta.ai);
    - *Refine this design* (starts a new round from it).
    - A labelled vision line: connecting a Meta account to iterate without
      leaving Polymath. Meta offers no such connection to apps today, and
      visitors never paste Meta keys.
- **Your inventions**: everything designed in this browser or account.

## Data (schema 0034)

- `studio_programmes`: owner session, brief, requirements (JSON, at most six),
  wildness, status and outcome.
- `studio_steps`: programme, stage (brief, inspire, design, engineer, test),
  target, exact run (unique), saved result, outcome, premises and failed
  attempts. A failed stage is retried once.

## Honesty rules

- Every inspiration cites a real saved passage; no invented sources.
- A design is always labelled a theoretical invention, and the design stage
  must say which parts are evidenced and which are invented.
- Numbers are labelled by their source, and the dossier says estimates must be
  checked before real use.
- Ideas that did not hold up stay visible as *considered, not used*.

## Verified (locally)

- Seven PostgreSQL tests cover:
  - the full path (functions, inspirations, invention, engineering, stress
    test);
  - three-angle search with a single inspiration;
  - nothing found;
  - a failed design retried once;
  - failed engineering still leading to the stress test;
  - validation, ownership and pause;
  - bounded goals;
  - the API (session, CSRF, no echo).
- Drift 0; downgrade and upgrade clean; migration round trip passes.
- Six web tests cover:
  - the composer, starters, chips and wildness;
  - autopilot through the ordinary route;
  - the full design sheet with every action;
  - the dossier and briefing;
  - the outcome when nothing held up;
  - label wrapping.
- A real-browser check at 1440 and 390 pixels with a seeded invention found
  axe clean, no overflow and no page errors.
- Live quality needs Meta research switched on. First briefs: the four
  starters.
