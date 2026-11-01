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

## The gallery (Meta Muse Image, schemas 0035 and 0036)

Every invention gets a gallery of five to seven pictures painted by Meta's image
model, `muse-image-1.0`. Each picture is placed beside the part of the story it
shows, on the page and in the dossier.

| View | Shown with | When |
|---|---|---|
| Concept art | under the title | always |
| In its world | after the idea map | always |
| Where the ideas came from (a natural-history plate of the sources) | the inspirations | always |
| Engineering cutaway (blueprint) and the key mechanism up close | the invention, side by side | always |
| How it works (a three-panel sequence) | engineering | when the design is a process, or has three or more mapped parts |
| A sense of scale | the stress test | when the invention is enormous or tiny (a swarm, a city system, an implant, a cell) |

**How the gallery is chosen.** Each prompt is built on the server from that
invention's own brief, title, mechanism, parts and sources, never from browser
input, and always asks for no text, labels or logos. Muse Image then plans each
composition itself, so every invention gets its own pictures.

**How it paints.** An invention designed while the page is open fills its
gallery automatically, one picture at a time in reading order. Otherwise,
*Paint the gallery* does the same. A view that was refused or held is never
retried automatically. Each invention gets at most ten pictures, including
repaints.

**How the money is protected:**
- Every picture is one `render_image` run with its own ledger cap of one call
  at the flat price (USD 0.01).
- A single transaction checks:
  - the owner's switch (`PAID_META_IMAGE_ENABLED`, which requires paid research
    and `META_IMAGE_MODEL`);
  - that the account is armed for research;
  - the saved design;
  - the per-invention (ten), per-visitor (28 a day) and site (400 a day) limits.

  It then reserves and claims the call with a narrow image permit that covers
  only render runs. Kill switch, freeze and the hard and daily caps all apply.
- Exactly one request goes to `POST /v1/images/generations`, with no retries.
  The outcome is recorded one of three ways:
  - **A valid image** is settled at the flat price and stored in the same
    transaction.
  - **A request Meta rejects** is settled at zero: per Meta's pricing page,
    failed and safety-blocked images are not billed.
  - **A timeout, a malformed reply, or a render lost in flight** stays an
    *unknown* hold for the operator to reconcile, never assumed free.
- Images are served only to their owner, never cached, and checked to be
  WebP, PNG or JPEG of at most 8 MB.

Verified with six PostgreSQL tests against a mocked Meta endpoint:
- success, settled at the flat price and stored once;
- a refusal settled at zero;
- a timeout and a malformed reply held as unknown;
- every limit and the switch;
- a lost render becoming unknown;
- the permit refused for other run kinds;
- the routes, including owner-only serving.

A web test covers painting, display and the dossier embed. A real-browser
check used a synthetic stand-in image. The real Muse Image call is untested
until paid testing is approved.

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
