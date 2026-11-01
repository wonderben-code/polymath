# Solve: key finder and key maker

Owner's idea, 1 November 2026. Plan of record before building.

## The idea in one line

Describe a problem (the lock). Polymath searches all of science for a key that
already fits. If none fits, it finds the closest fits from different fields,
works out where each falls short, and **forges a new key from the literature**
by combining them, with every piece tied to its evidence.

## What the visitor sees

- **In the composer, a new mode: Solve** ("Describe a problem. Polymath looks
  for an answer anywhere in science, and if none fits, builds one from the
  closest answers in other fields.")
- **A lock-and-key picture** that fills in as it works:
  - **The lock:** the problem, in the visitor's words.
  - **Keys found:** existing answers that fit.
  - **Close fits:** keys from other fields that almost fit. Each shows where it
    fits and where it falls short.
  - **The forged key:** drawn in segments, one per contributing field, each in
    that field's Prism colour. It is literally made of the fields it came from.
- **One clear outcome at the top**, in plain words:
  - "A key already exists": the answer, its field and the exact passages.
  - "A new key was forged": the combined solution, what each field contributes,
    and what still does not fit.
  - "Close fits found": partial answers and the gap that remains.
  - "No key yet": nothing in the literature fits, which is itself a finding.
- **Inside Frontier:** any step can be sent to Solve ("Solve this step"), and
  the result links back to the step.

## How it works: Meta reasons, Polymath keeps it honest

Every stage is an ordinary Polymath investigation: Meta's Muse Spark chooses
what to search and read, quotes must match saved text, objections are
recorded, and every call is budgeted. Solve plans the stages and reads their
saved results; it never calls a model or spends by itself.

1. **Find the key (Ask).** "Has this problem already been solved, in any field?
   Find existing solutions and say exactly how well each fits." An answer with
   literature-stated (known) claims and no remaining open questions means a key
   exists. The open questions it names become the gaps to fill.
2. **Close fits (Explore, up to three).** For the problem, or for each gap:
   "Find methods or results in other fields whose structure could solve this.
   State where they fit and where they fall short." Each saved bridge is a close
   fit: its mapping is where it fits, its strongest objection is where it falls
   short.
3. **Forge the key (Explore, built on 2–4 close fits).** The existing
   higher-level investigation combines the chosen fits: "Combine these partial
   solutions from different fields into one candidate solution. Show what each
   contributes, test the combination against the original evidence, and state
   what still does not fit." Its verdict and strongest objection are the fit
   test.

At most five investigations per problem: one find, up to three close fits,
and one forge. They run one at a time while the visitor's page is open, like
Frontier and Converge, and every one appears in Your library.

## Data (schema 0032)

- `solve_programmes`: owner session, problem text, status (active, paused,
  completed), outcome (found, forged, close, none), optional Frontier programme
  and step link.
- `solve_steps`: programme, stage (find, fit, forge), the gap it targets, its
  exact run (unique), saved result, outcome, premises (for the forge), and when
  it was absorbed.

## Honesty rules

- A key is "found" only when the saved answer states it as known in the
  literature, with no open questions remaining.
- A forged key is labelled as a proposal: Polymath's combined reading of the
  evidence, tested against the strongest objection Meta found, and not
  independently verified.
- Every piece links to its saved finding and exact passages.

## Verification plan

PostgreSQL tests use synthetic results for every path:
- the key is found;
- close fits are found and then forged;
- only one close fit is found (close, not forged);
- nothing is found;
- step order, attach validation, ownership and the five-investigation limit hold.

Web tests cover the composer mode, the lock-and-key view and the autopilot
through the ordinary routes. A real-browser check covers desktop and phone
(axe, no overflow). Live quality is tested once Meta research is switched on.
