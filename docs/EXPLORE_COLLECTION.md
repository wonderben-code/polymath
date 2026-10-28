# Model-directed Explore bridge collections

**EC1 complete; EC2–EC6 pending.** Strict pure collection/commit/ranking contracts
pass 67 cases. The actual saved SC archive `a95011a` supplies 40 compatibility
cases: 38 captured values and two provenance checks. The current backend unit
suite passes 1,970 tests. Collection runtime, migration, native profiles and UI
are not wired yet; the sections below describe that remaining integration.

The last full SC release passed 3,939 tests: 1,863 unit, 1,232 PostgreSQL and 844
frontend. PostgreSQL/frontend suites were not rerun for this pure-foundation
checkpoint. This contract grants no paid execution, public funding or
publication authority. No paid calls ran. Meta remains the scientific
decision-maker; the existing worker and budget ledger are retained.

## Product outcome and decisions

`SPEC.md` §§3.1/3.3 and Flow A require several inspectable bridges, ranked cards,
novelty/confidence and visible discarded hypotheses. Ordinary Explore currently
stops at one `BridgeFinding`. Surprise F6 already has adaptive hypotheses,
atomic stages, scoped challenges and durable partial results, but only a single
explicit preferred final stage, not a model-authored ordered collection.

Add an explicit `explore_collection_intent`:
`{protocol:"polymath-explore-collection-v1",max_bridges:3}`. Reviewed maximum is
1–4; it is a ceiling, never a promised number of discoveries. Empty and partial
outcomes are valid. Default UI selects this when its exact current activation
supports it. Absent intent keeps ordinary Explore completely unchanged.

First collection version is for ordinary seeded Explore, with no initial F3
parent IDs. All saved collection bridges remain selectable as inputs to the
existing separate F3 investigation and Surprise higher-level ladder. This
keeps the existing requirement to reread every explicitly selected F3 premise
intact. Do not silently reinterpret a parent-selected investigation as a search
whose individual stages may omit those parents.

Meta chooses fields/directions related to the actual user concept, hypotheses,
searches, source reads, objections, revisions/discards, which bridges to save,
their order/rank and when to stop. There is no fixed field list, fixed branch
sequence, application-written scientific ranking or score-based sort. Keep the
original run goal in every branch's initial scope; a field change cannot erase it.

## Actual reuse constraints and proposed storage

Verified source constraints, not an assumption of interchangeable modes:

| Existing seam | Constraint | Change |
| --- | --- | --- |
| `discovery_models.py`, migration 0022 | `StageContextRecord`, `StageObservation` and `StageCommit` each directly FK to `research_discovery_intents`; stage commits also bind normal result/run and context | Add a shared immutable stage-owner registry and repoint these three direct FKs after backfilling existing Surprise intents. Preserve every existing row, receipt and hash. |
| `discovery_admission.py`, `discovery_store._intent`, `StageCommitPacket` | Require research/Surprise; positive packet requires Surprise field selections | New immutable Explore intent and separate Explore commit packet; shared protocol-dispatch helpers validate the proper concrete intent, mode and observed action. Never save an Explore job under a fabricated Surprise intent. |
| `EvidenceStore._validate_result_shape` | Explore intentionally cannot carry Surprise `field_selections` | Retain that rule. Reuse `StageContext`'s model-selected fields for the scoped branch, while its normal Explore result has empty field selections. New packet independently checks finding fields against that branch. |
| `StageFinalSelection`, `DiscoveryStop` | Surprise-specific typed final preference/outcome | Keep unchanged, including their original intent FKs. New Explore ranking/final/stop records reference its own intent. |
| `assessment_eligibility`, generic evidence save | Only the Surprise intent currently excludes ordinary positive-result eligibility and generic saves | Treat either concrete collection intent as collection-scoped, even if a malformed registry row is missing. Only exact atomic commits may expose positives. |
| `scientific_context_builder.stage_origin` | Reads Surprise intent directly and computes original fields from its scoped context | Resolve validated stage owner/protocol centrally. Receipt, assessment and existing context shape can remain unchanged. |

Suggested registry: `ResearchStageOwner(run_id PK,mode,task_kind,protocol,
surprise_intent_run_id?,explore_intent_run_id?)`. Use SC's existing
`research_runs(id,mode,task_kind)` unique key for a composite FK. Require exactly
one concrete intent FK equal to `run_id`, with matching protocol/mode. A registry
row alone is not a scientific admission. Both intent and registry insert in the
original enqueue transaction. Backfill before replacing the three old FKs;
downgrade refuses to discard any Explore intent/ranking/history.
Fence paid work and stop the old API/worker generation before this migration:
older code cannot insert the new registry. Restart only the new compatible
generation; startup never rearms funding. Choose the migration revision from
the actual pushed SC head, not a reserved number in this draft.

Keep `EvidenceResult`, quotes, `StageCommit`, `StageParent`, scoped observations,
receipt and atomic checkpoint acknowledgements as the shared scientific store.
Explore v1 has base stages with local depth 1 and no stage-parent selection;
future depth is not implied. Reuse `StageAssessment` unchanged: original score
is explicitly an uncalibrated Meta judgement, with rationale and limitations.
Do not add rank or collection fields to `BridgeFinding`, `ResearchResult`, the
old action adapters or scientific-context v1 envelopes.

Extract a small internal stage-protocol strategy around the existing store/loop:
validate intent, parse observed action, validate commit packet and positive mode.
Default Surprise wrapper retains exact old calls/serialization. Reuse all source
identity, exact quote, scoped counterevidence/prior-work, evidence deduplication,
post-wait owner fencing and transaction code. Avoid copying the 1,000-line store
or building a second worker, financial ledger or source corpus. Preserve ordinary
Explore memory search and exact finding/source reads for both Bridge and Ask
leads. Explore stage validation must verify actual recorded ResearchMemoryRead
keys/hashes, linked original sources and complete alias metadata. Those earlier
findings remain leads, not automatic premises or additional independent papers.
This extension must not change the old Surprise-specific memory/parent rules.

## Model-authored ranking and partial outcomes

New Explore action adapter reuses search/read/full-text/memory, model field
selection and bounded `record_note` hypothesis decisions, plus `record_bridge`
with assessment. The pure contract defines terminal `finish_ranked` and
nonterminal `rank_saved`, which saves an interim model ranking while research
continues. Their controller/store integration remains pending.

Ranking contains ordered exact acknowledged `MemoryReadReference`s and a concise
model rationale per entry. Add explicit excluded/unranked references with reasons.
The ranked and excluded lists are disjoint and together cover the receipt set
visible to that model step. Unknown, duplicate, foreign, future/unacknowledged,
wrong-hash or non-bridge references fail before ranking commit. Rank order is the
model's array order; confidence never computes it. Model can exclude a saved
candidate without deleting its finding or evidence.

Save an immutable `ExploreRankingRecord`, keyed by run/model-request/action,
with packet SHA, covered-through stage ordinal and exact receipt bindings.
Commit ranking, public event and checkpoint acknowledgement atomically under
the current run lease. Exact acknowledgement replay returns the original record;
changed bytes under the same key are a conflict. No extra model call is invented
by the application to obtain a ranking after cancellation or budget closure.

Terminal `ExploreCollectionFinish` pins the exact final ranking and stop reason.
Its first ranked finding may supply the existing normal result pointer; this is
only an explicitly chosen model preference. Keep a no-ranked/empty outcome honest.
The collection endpoint remains the authoritative view of all saved findings.

If a run stops before ranking, show committed bridges in discovery order labelled
“not ranked by the model.” If a later bridge follows an interim ranking, retain
its original coverage and show the new bridge outside that ranking. A late alias
connector or reassessment may mark a ranked bridge needs-review; preserve its
historical rank and score, disable fresh use through existing eligibility checks,
and never silently remove/reorder it. All saved partial bridges remain readable.

Replay starts from original input and cached responses/receipts in order. Never
preload the latest stages or rankings into an earlier prompt. Keep whole-run
call/action/tool/event counters, reservations, usage and unknown holds unchanged.

## Approval, API and clients

Add an omitted-when-absent `explore_collection_profile` extension to private and
public manifests. Separate controller protocol/digest binds the new prompt,
adapter, scope/packet and ranking semantics. Derive from the existing research
quantitative profile: same calls, actions, timeouts, output/context/source and
money ceilings; bind approved maximum bridges in the new profile/hash. In
particular, do not raise the ordinary 16-action allowance to fit a desired count.
The model can return fewer bridges. Fresh authority is separately reviewed.

Admission is research/Explore-only, incompatible with Ask/Surprise intents and
initial F3 parents. Save intent before native scientific-input hash/binding;
conditionally hash it and include it in worker replay fingerprint. Original
profile reconstruction remains historical; current variant validation runs
before every fresh grant. One collection is one public job and one original
run budget. Resume retains its original intent/profile and unknown-call fences.

Keep strict D2 `/research/access` and SC `/research/protocols` v1 unchanged.
Use separate owner-only `GET /api/v1/research/explore-protocols`:
`{protocol:"polymath-explore-collection-access-v1",activation_sha256,
explore_collection_intent}`. Eligible legacy public grants return their SHA with
null intent; closed/private contexts return null/null. Null SHA plus nonnull
intent is invalid. No profile, model, operator, key, account or pricing data.
Cross-bind the returned SHA to the synchronous funding-access SHA; recheck final
admission. Normal resource/read limits apply; cache immutable capabilities by
activation and include refreshes in the existing read budget.

New owned GETs: `/runs/{id}/explore-collection`, `/bridges`,
`/bridges/{stage_id}`, `/rankings`. Summary includes actual run state, committed
receipts, ranking coverage/selected final ranking, stop reason and whole-run
accounting. Existing `/result` returns only a valid explicitly selected result,
never an application-invented top card. GETs create no session or scientific work.

UI reuses exact finding/evidence reader, stage assessment, activity, source and
downstream actions. Add model-ranked cards and separate unranked/partial/history
states; opening/reloading cards performs GETs only. SDK supports strict optional
intent, exact-hash capability selection, bounded collection/ranking reads and
one POST, with explicit unsupported intent failing closed. Legacy bodies remain
unchanged. Public sharing must not copy registry/ranking/private graph IDs. If
scores are included in a newly approved public snapshot, use an omitted optional
model-assessment projection bound to its exact shared finding, with no private
handles and unchanged historical public bytes.

## Implementation order and release evidence

- [x] **EC1 — Pure contracts and archived compatibility.** Implemented
  `explore_collection_contracts.py` with strict intent, commit, ordered ranking,
  finish and acknowledgement validation; 67 pure cases pass. The actual pushed
  SC archive `a95011ae46fcb3d66b784abab7444167e39c33fc` supplies 40 compatibility
  cases (38 captured values plus two provenance checks). Full backend unit suite:
  1,970 passed. No collection runtime or capability endpoint is wired.
- [ ] **EC2 — Registry, admission and shared stage protocol.** Add migration and
  concrete Explore ownership; verify FKs, immutable history and downgrade
  refusal. Reuse the stage transaction behind the unchanged Surprise wrapper.
- [ ] **EC3 — Controller and atomic ranking/stop store.** Exercise independent
  bridges, discarded weak leads, model ranking that differs from score order,
  empty/partial stops, ordinary Bridge/Ask memory leads, late alias review and
  lost stage/ranking receipts. Keep the original goal and whole-run counters.
- [ ] **EC4 — Native profiles and runtime.** Connect private/public activation
  and exact input/profile/worker binding; permit a tighter run bridge ceiling
  within the original approved profile. Prove old hashes, one-ledger limits,
  cached replay and DB-only late billing.
- [ ] **EC5 — Downstream eligibility and scientific context.** Integrate
  memory/map/F3/artifact/reassessment/sharing with exact original/reassessed
  Explore stage provenance.
- [ ] **EC6 — UI/SDK and connected verification.** Add separate capability
  discovery, ranked/unranked/partial/history readers and synthetic HTTP/browser
  journeys: mobile, zero-POST reload, foreign 404, funding stops, retained
  exports and full regressions. Live science/deployment remain separate gates.

Track task acceptance in [BUILD_PLAN.md](BUILD_PLAN.md) and exact checkpoint
evidence in [PROGRESS.md](PROGRESS.md).

Primary risks: accidentally exposing orphan Explore positives through the old
ordinary-success branch; rewriting a legacy protocol during generalization;
letting rank imply calibrated confidence; replay seeing future receipts; and
overspending the existing action budget to force a fixed result count. Use the
existing exact identity ceilings (256 fresh/4,096 bounded historical), no silent
truncation and no automatic reclassification, publication or paid follow-up.
Live scientific quality, hosting and native billing remain separate gates.
