# Frontier mode (experimental): specification for review

Status: owner approved 29 October 2026; version 1 implemented (see §9). Experimental.
Frontier mode is an experimental fourth way of working with Polymath, beside
Explore, Ask and Surprise me.

## 1. What it is

You give Polymath a problem that has never been solved. Instead of one answer,
it runs a **research programme**:

1. **Converge.** Search all the literature, across fields, for anything that
   already solves the problem or parts of it, including convergences and
   connections between connections.
2. **Surface the gaps.** Whatever is not covered becomes an explicit, named gap:
   what is missing, why it matters and what would count as closing it.
3. **Attack each gap.** Look for the same structure solved in other fields
   (analogical transfer), combining the literature with Meta's frontier model.
4. **Decompose.** If a gap will not fall, plan a solve path from where we are (A)
   to the goal (D) through smaller, more tractable steps (B, C, …), each with its
   own acceptance test.
5. **Knock steps down one at a time,** using the literature, cross-field
   transfer, the model's own derivations and, for mathematical steps, Formalise
   and its bounded checks.
6. **Recurse and re-plan.** A step that will not fall becomes a new gap with its
   own path. A refuted step triggers re-planning. Patterns that recur across
   branches feed meta-convergences that can open new paths.

The output is either a complete solve path from question to answer, or a
precisely mapped frontier: what is established, what was derived, and the exact
open steps that remain. Both are real contributions.

## 2. How it differs from what exists

| | Ask | Surprise ladder | Formalise | **Frontier** |
|---|---|---|---|---|
| Starting point | A question | No input | A finding | An unsolved problem |
| Shape | One answer | A ladder of bridges | Conjectures | A growing tree: question → gaps → solve paths → steps |
| Gaps | Listed as unanswered questions | Not tracked | Not tracked | First-class objects that are attacked |
| Recursion | None | Stages within one run | None | Gaps spawn paths; failed steps spawn gaps |
| Model reasoning | Labelled claims | Bridges | Proof attempts | Labelled derivations as explicit steps |

Frontier reuses all three rather than replacing them.

## 3. The honesty model

Every node on the map carries one status, shown by colour and label:

| Status | Meaning |
|---|---|
| **Supported** | Established by exact quotations from saved sources |
| **Transferred** | A cross-field solution mapped onto this step, with mapping and objection |
| **Derived** | The model's own reasoning from supported premises, with its strongest objection |
| **Checked** | A mathematical statement that passed a bounded Formalise check (not a proof) |
| **Conjectured** | Proposed and plausible, not yet supported or derived |
| **Open** | Attempted; remains a frontier problem, with what was tried |
| **Refuted** | Evidence or checks contradicted it; kept, with the reason |

A solve path is only as strong as its weakest step, and the final answer
inherits that status. This lets Frontier push hard into the unknown while every
claim stays inspectable.

## 4. Methodology in detail

**Programme.** One root question, optional success criteria from the user, a
budget, maximum depth and maximum open branches.

**Nodes.** `question`, `gap`, `path` (an ordered plan) and `step`. Each step has
a statement, an acceptance test ("what would count as solved"), dependencies and
a status.

**Attempts.** Each node attempt is a bounded research task that runs through
the existing worker with a Frontier contract. Survey attempts use an Ask-style
controller that returns classified claims plus structured gaps. Gap and step
attempts use the Explore-style controller seeded with the gap, plus a
derivation action and an optional Formalise hand-off.

**Orchestrator.** A model-directed controller (like the discovery ladder)
chooses the next move after every attempt: attack a gap, decompose, attempt a
step, re-plan, look for meta-convergence across branches, or stop. Every
decision is recorded with its rationale, so the programme is replayable and
auditable.

**Prioritisation.** The model ranks gaps by expected value: how much closing
them advances the root, and how tractable they look. Steps on the most promising
path go first. The user can promote, pause or prune any branch.

**Stopping.** The path is complete, the budget or depth limit is reached, every
branch is open or refuted, or the user stops it. Partial programmes stay
readable.

## 5. Experience

- **Entry:** "Frontier (experimental)" beside the other modes. Type the problem,
  optionally add success criteria, preview the budget and depth, start.
- **The Frontier Map:** a live, beautiful tree that grows from the question on
  the left. Gaps branch out, solve paths unfold as chains of step nodes coloured
  by status, and the active node pulses. Selecting a node opens its evidence,
  derivation, objection and attempts. The Observatory shows the active attempt.
- **Steering:** pause, resume, promote a gap, prune a branch, add a hint
  (recorded as user input, never as evidence).
- **Notify me when it's done**, as for every long task.
- **Frontier report:** the solve-path diagram, the answer and its weakest-link
  status, the open frontier as a list of precisely stated problems, and one-click
  Write (paper) and approval-gated Zenodo publication.

## 6. Architecture

- **Reuse:** the existing worker, native Meta adapter, global ledger and
  per-call reservations, evidence store with exact quotations, memory, the
  discovery-stage registry pattern, Formalise and exports.
- **New schema (migration 0026):** `frontier_programmes`, `frontier_nodes`,
  `frontier_edges`, `frontier_attempts` (each linked to a research run and its
  saved result) and `frontier_decisions` (immutable orchestrator choices).
- **Authority:** a new optional native profile, separately reviewed like the
  Explore and discovery profiles, with programme-level call and spending caps on
  the same ledger. Off by default; public access stays off at first.
- **Replay and recovery:** every attempt and decision is recorded before it
  takes effect, so an interrupted programme resumes without resending paid
  calls.
- **API and SDK:** create, inspect, steer and stop a programme; stream its events;
  export the report.

## 7. Delivery plan

| Phase | Outcome |
|---|---|
| F0 | Pure contracts: node/edge/status types, gap and path schemas, orchestrator actions, with tests |
| F1 | Migration 0026 and store with PostgreSQL tests |
| F2 | Survey, gap, step and derivation controllers using synthetic models |
| F3 | Orchestrator loop with budgets, replay and stop |
| F4 | API, SDK and native profile |
| F5 | Frontier Map, node drill-down, steering and notifications |
| F6 | Frontier report, Write and publication hand-off |
| F7 | Full regression, accessibility audit, then live evaluation after key day |

## 8. Decisions for the owner

1. **Default limits:** suggested starting point of up to 40 model calls,
   depth 4 and 6 open branches per programme, adjustable per run.
2. **Answer threshold:** can a path that includes Derived steps (model
   reasoning, clearly labelled) be presented as "an answer", or only as a
   "proposed solve path"? Suggested: "proposed solve path", with the weakest
   step named.
3. **Name:** "Frontier" (suggested), or another name you prefer.
4. **Timing:** suggested F0–F5 before submission, presented as experimental,
   with live evaluation after key day.

## 9. What version 1 implements (29 October 2026)

Owner decisions: the suggested defaults (24 investigations, depth 4, 6
branches per node, adjustable per programme), the name "Frontier", results
presented as a proposed solve path named by its weakest step, and delivery
before submission marked Experimental.

**How it runs.** Every attempt is an ordinary Ask or Explore investigation
created by the owner's browser through the existing run API. All approval,
budget, evidence and replay guarantees therefore apply unchanged, and the
Frontier routes never start research or spend. The programme advances one
investigation at a time while the page is open.

| Node | Attempt | What the saved result does |
|---|---|---|
| Question | Survey (Ask) | Each unanswered question in the answer becomes a gap; with no answer, the whole question becomes the gap |
| Gap | Transfer (Explore across fields) | A saved bridge marks it Transferred; otherwise it is decomposed (or Open at the depth limit) |
| Gap or unresolved step | Decompose (Ask for intermediate results) | Each cited claim in the answer becomes a step |
| Step | Attempt (Ask) | Labelled by its weakest claim: Supported, Derived or Conjectured; Conjectured or unanswered steps are decomposed again while depth allows |

A decomposed gap shows its weakest step, or "in progress" while steps remain.
Failed investigations are retried once, then the node is marked Open.

**Honest scope of v1.** The model directs everything inside each
investigation: the gaps it names, the steps it proposes and the evidence it
cites. The order in which nodes are attempted is a fixed breadth-first
schedule, and the owner can pause, stop or skip branches. A model-directed
orchestrator choosing which branch to pursue, the Checked status from
Formalise, Refuted detection and a cross-branch meta-convergence pass are the
next steps. Node statements always come from saved, hash-checked results,
never from the browser.

**Verification.** Seven PostgreSQL/HTTP tests and five web tests use
synthetic runs. No live Frontier programme has run against Meta yet.
