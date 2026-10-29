# Converge (experimental)

Converge lets someone see connections across many fields at once (a handful, a
hundred, or up to two hundred), then what those connections share, level after
level, until one deepest convergence remains or nothing deeper honestly holds.
A question or theme is optional.

It is marked **Experimental**. It is built and tested with synthetic research
only; how well it works with Meta's live model is still to be shown.

## What the person does

1. Chooses **Converge**, the fourth research mode.
2. Optionally writes a question or theme.
3. Chooses **how many fields** (a slider marked 5, 12, 30, 100, with **More**
   up to 200), and optionally names fields as chips. Polymath spreads the rest
   across a catalogue of 202 disciplines in 16 domains, taking turns across
   domains so the circle holds distant fields, not near neighbours.
4. Sets a **spending ceiling**, and sees a plain estimate of how many
   investigations fit inside it.
5. Presses **Begin converging**, then watches, or turns on *Notify me when it's done*.

## What it shows: the Ensō

- **Fields** sit as stars on a brushed ensō circle. Chosen fields have a darker
  ring; fields the model connected that were not on the circle are added in
  gold ("found along the way").
- **Connections between two fields** are drawn across the circle as gently
  bowed threads once they hold.
- **Deeper levels** are gold knots on rings closer to the centre. Each knot is
  placed at the average direction of the fields it unites. In a finished circle,
  the single deepest convergence settles at the centre with a soft glow.
- While a step runs, the latest recorded activity of that investigation narrates
  beneath the title, and a small brush tip travels the rim during a thread.
- A quiet tally reads: fields connected, tested, holding, levels deep, and spend
  against the ceiling.

The reading view leads with the **deepest convergence**. Below it is each level,
showing the first six and then *Show all*. Any connection opens a detail with
**Read the evidence** (the ordinary investigation view) and **Go deeper here**
(opens Your library with that connection chosen). Under *Widen the circle or go
further* the person can add fields or raise the ceiling, and a finished circle
continues. *Read the circle as a list* gives every field in text, and
**Download report** writes an escaped Markdown report.

## How it works

Every step is an ordinary owned **Explore** investigation, created by the
person's browser through the normal run API. It uses the same admission,
activation, budget and evidence checks as every other mode. The Converge
routes never create runs or spend.

- **Thread (level 1).** The goal lists the untouched fields, the touched ones
  and the pairs already connected. Meta's model chooses two distant fields that
  plausibly share one structure, then tests that connection against the
  literature, with its strongest objection and exact quotations. The fields it
  names are matched back to the circle; an unmatched field is added as found.
- **Meta (level n+1).** Two to four held connections become the premises
  (`parent_result_ids`) of a deeper Explore. The deeper finding must revisit
  every premise's original evidence, the existing higher-level rule.
- **Order.**
  1. Whenever any level has four fresh held connections, they are drawn inward.
  2. Otherwise threads continue while fields remain untouched and the model
     keeps finding connections. Three failed threads in a row end the threads.
  3. Then the remaining held connections are combined, two to four at a time,
     until one is left.
- **Ceiling.** Before every step, recorded spend counts settled actual costs,
  plus estimates for anything unsettled. That figure, plus the larger of a
  $0.50 planning estimate and any recorded per-run cap, must stay within the
  ceiling, or the circle pauses at the ceiling. A raised ceiling or a wider
  circle continues it.
- **The browser drives.** Every paid step must be authorized by a live browser
  session, a deliberate safety property. The circle advances while the tab is
  open; if the tab is closed it simply waits, and the most recent circle reopens
  and continues on return.

## Honesty

A thread holds only when a saved bridge passes the existing evidence rules.
Deeper levels are model reasoning across held connections, tested against the
same original evidence; they are labelled as such. Untested pairs are never
shown as findings. With 100 fields there are 4,950 possible pairs; Converge
lets the model choose where to look within the ceiling, rather than testing all
of them.

## Data and API

Schema `0027_converge_programmes`:
- `converge_programmes`: owner, theme, fields (JSON), ceiling, status,
  `finished_reason` and `threads_from`.
- `converge_steps`: kind, level, run, premises, saved result, outcome, the two
  field names and field indices.

Routes under `/api/v1/converge` (session, CSRF and Origin on writes, the same
as Frontier):

| Route | What it does |
|---|---|
| `GET /capabilities` | What Converge supports on this server |
| `GET /fields?q=` | Catalogue suggestions |
| `POST /` | Create a circle |
| `GET /` and `GET /{id}` | List circles, or read one |
| `POST /{id}/next` | Absorb finished work and return the due step |
| `POST /{id}/attach` | Link a just-created run to the due step |
| `POST /{id}/extend` | Add fields or raise the ceiling |
| `POST /{id}/pause`, `/resume`, `/stop` | Pause, resume or stop the circle |

The setting `CONVERGE_ENABLED` defaults to true; it still creates nothing
without the ordinary research gates.

## Verification (29 October 2026)

- **Backend:** 8 PostgreSQL/HTTP tests. They cover the catalogue spread, the
  goals, threads to meta convergence, the deepest step, found fields, the
  failure streak and widening, the ceiling, validation and owner scope, and the
  API with session and CSRF checks. The 0024 round-trip test now excludes the
  0027 tables.
- **Web:** 6 tests covering parsing, geometry, the report, autopilot submit and
  attach, the reading view and the composer.
- **Accessibility:** axe finds zero violations on a seeded 30-field and
  100-field circle at 1280 and 390 px.
- **Local synthetic seed:** a 30-field circle converged to one level-4
  convergence in 20 steps.
- **Not yet done:** live Meta evaluation, real costs and timing per step.
