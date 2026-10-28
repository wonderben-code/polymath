# Automatic Surprise discovery ladder

Status: F6/C3 implementation contract, 28 October 2026. The controller, atomic
storage, native worker, owned API and browser workspace are implemented locally;
verification uses synthetic evidence and the actual Meta adapter with mock HTTP.
This feature does not enable execution or establish scientific quality.
The owner’s instruction to continue without paid calls remains in force.

## Product outcome

One press of **Surprise me** starts one bounded investigation. Meta chooses
fields, searches and reads original literature, challenges candidate mappings,
and can save more than one supported bridge. It can then select earlier bridges
as premises, look for a connection between them, investigate that connection
against the original papers, and either save it or explain why it stopped.
Discoveries appear as they are validated. A supported base bridge remains
useful when a higher connection fails, the budget runs out, or the user stops.

The model chooses whether to explore another field pair, deepen a promising
lead, reuse owned findings, climb, revise an uncommitted candidate, or stop.
Application code enforces evidence, ownership, provenance and resource limits.
There is no required sequence of discoveries, no scripted field pair and no
background child research job. A run may honestly finish with one bridge or
with insufficient evidence. Reaching the maximum depth is never a success
criterion.

This closes the automatic-ladder gap in [SPEC.md](SPEC.md), sections 3.3, 3.4
and 3.8. Existing F3 lets a person select two to four saved bridges and start a
new Explore investigation. The optional Surprise ladder adds automatic
multi-finding research within one bounded run; ordinary Surprise retains its
first-conclusion protocol. Explore and Ask retain their existing behavior in this
slice; richer claim search and explicitly authorized public-memory reuse remain
separate tracked work.

## One existing engine and one approved budget

Use the existing `ResearchController`, `ResearchWorker`, research run, native
activation, recorded tools and global ledger. The task remains
`task_kind='research'`, `mode='surprise'`. No scientific decision comes from a
fixture or fallback provider in production. The native model remains Meta.

An explicit optional initial input selects `polymath-surprise-ladder-v1` and its
bounded stage/depth configuration. Absent that input, the old protocol applies.
The initial configuration is immutable; generated stages are outputs, not
additional initial inputs. A browser cannot raise the approved call, money,
output, context, timeout or concurrency limits.

Pinned ladder limits:

| Limit | Value and meaning |
|---|---|
| Stage target | Default at most 3 committed stages; server ceiling 4 |
| Local depth | Default at most 2; server ceiling 3 |
| Model calls | Minimum of the approved proposal and 20, shared across the whole run |
| Actions | At most 20 for the new ladder profile, also bounded by its call allowance |
| Repairs | At most 2 across the whole run, including rejected stage proposals |
| Selected parents | 2–4 distinct logical findings for one higher bridge |
| Read documents | At most 200 across the whole run |
| Identity context | At most 256 read/index references; never discard alias connectors |
| Context/output | The exact reviewed native limits, within 90,000 characters / 4096 output tokens |
| Deadlines | Reviewed HTTP timeout plus the existing pinned 30-second local allowance |

Stage count and depth are ceilings, not requested findings. A smaller approved
call allowance or run cap can stop before them. Opening and reading full text,
memory retrieval, hypothesis notes, field changes and rejected actions all use
the same bounded loop. A fresh stage never replenishes calls, actions, repairs,
money, source capacity or deadlines. Per-call reservation, known accounting,
unknown holds, authority checks and single-send behavior remain those in
[META_RESEARCH.md](META_RESEARCH.md).

Local depth measures the work performed in this run. A stage with no same-run
parents has depth 1; otherwise its depth is one plus the maximum selected
same-run parent depth. Owned earlier-run findings are explicit anchor premises,
not newly created stages. Parent edges remain visible, so local depth cannot
be mistaken for the absolute depth of the complete knowledge graph. A later
explicit user investigation can continue from saved findings within a fresh
bounded run; this run never schedules that work automatically.

## Model actions and stage context

Keep the ordinary research action adapter and prompt unchanged. Add a separate
ladder adapter/instructions selected inside the same controller. Its schema
retains the existing search, read, full-text, memory and field-selection tools
and adds the following capabilities:

| Capability | Model decision and application observation |
|---|---|
| Record a research note | A bounded hypothesis handle, public summary, and `proposed`, `revised` or `discarded` state with a concise reason; this is a candidate, not a finding |
| Select bridge parents | Choose 2–4 exact owned result references, including already observed committed stages; record a public reason and begin a higher-bridge context |
| Record a bridge | Submit a normal complete `BridgeFinding` and structured model assessment; validate and atomically commit it, then return an immutable stage receipt |
| Finish with a saved stage | Choose an already observed committed stage and explain the stopping decision; return its exact result pointer |
| Conclude directly | Stop with one proposed bridge or an honest insufficient-evidence outcome; a positive proposal uses the same stage commit checks |

These capabilities do not prescribe an order. The model can investigate several
uncommitted hypotheses, change its field selection, abandon them, save a base
bridge, compare earlier findings, or stop. Limit candidate handles and notes
within the shared action/context bounds; use at most eight active candidate
handles per stage context. Candidate names, notes and rationales are untrusted
model text, never tool authority or scientific evidence.

A bridge-parent selection starts an explicit stage context. A successful commit
closes that context; the next action starts a fresh base context or explicitly
selects higher-level parents. Changing the selected fields or parent basis also
starts a fresh context. Context boundaries and their action ordinals are
deterministic and journalled. `ResearchResult.field_selections` stays bounded
per stage; the full run history lives in the activity/checkpoint records.
The existing per-result memory-reference bound also stays per stage: retain
that stage’s actual relevant reads rather than appending every earlier stage’s
memory history into each result. The durable run ledger retains the full audit.
Today `EvidenceStore._validate_memory_reads` requires equality with all ordinary
`ResearchMemoryRead` rows in a run and excludes same-run findings. Preserve that
contract. The private ladder commit path instead validates equality with its
exact stage-context receipt set, including its separate same-run stage reads;
do not weaken the ordinary validator to accept an arbitrary subset.

Each stage needs successful bounded `counterevidence` and `prior_work` searches
performed after that context started. An empty successful search is an attempt,
not proof that a connection is absent. Failed providers, searched queries and
truncation remain explicit. Earlier search-purpose flags do not carry over to
a new bridge, and a flag in model output cannot substitute for the actual
durable search observation.

Already fetched source bodies may be reused without external refetching, but a
new higher stage must explicitly reread the selected parent findings and their
original source passages in its own context. The returned bodies and their
hashes remain immutable. This establishes what the model actually observed
for that stage without pretending the literature was newly retrieved.

## Evidence and parent integrity

Every committed stage contains the existing complete bridge fields: the two
fields, explanation, mapping and assumptions, open question, strongest
objection, verdict, novelty limitations, and exact primary-source evidence.
The existing source/quote validators remain mandatory and independently run in
storage. Quotation integrity does not establish semantic support or truth.

A base bridge still needs two independent original papers with evidence on
both sides. A higher bridge has the same requirement and must additionally:

1. Select two to four exact current-supported owned parent findings.
2. Read every selected parent and its original evidence in the active context.
3. Reuse at least one exact original verified quotation from **each** parent in
   the child’s verified evidence: retrieval/version identity, content hash,
   content kind, quote text and Unicode offsets must all match.
4. Explain the higher mapping and attempt to break it with fresh stage-scoped
   counterevidence and prior-work searches.

Source-version intersection alone is insufficient: a different passage from
the same paper must not masquerade as coverage of a parent’s actual premise.
Additional original evidence is allowed. One exact quotation may genuinely
belong to several parents, but shared evidence never becomes independent
corroboration. The UI discloses overlapping ancestry and underlying papers.
The identity validator establishes distinct bibliographic paper groups, not
independence of authors, datasets or experiments. Two distinct papers can share
the same underlying evidence; the model must discuss that limitation where seen.

A later same-run read or uncited alias connector can reveal that an earlier
stage’s apparently distinct papers belong to one bibliographic identity group.
Preserve the immutable stage and its historical bytes, mark its current state
`needs_review` with `bibliographic_overlap`, and deny fresh parent selection,
Formalise/Write and new sharing from it. This is a provenance review condition,
not a judgement that the finding is scientifically unsupported. An existing
approved public projection keeps its exact approved bytes and a dynamic warning;
exclude it from public library/map views that show current findings only. Do
not fetch arbitrary future literature or expand this check across unrelated
runs to manufacture a new truth assessment.

Same-run parents must be committed lower-ordinal stages already observed at
the current replay point. External parents must satisfy the existing owned,
earlier-run/time and current-head rules. Preserve ordinary memory’s same-run
exclusion and F3’s earlier-only contract; use separate stage parent-read and
source-read receipts for this capability. A guessed result ID, future stage,
uncommitted candidate or foreign finding is ineligible.

All paper independence checks use the transitive alias closure of **all**
actual run reads, saved search/parent observations and owned full-text indexes,
including uncited connectors.
Abstracts, versions, windows and parents are never counted as additional
independent papers merely because their handles differ. Existing selected
version compatibility rules apply: several windows from one identical full
snapshot are permitted, incompatible selected versions are rejected. Parent
text is conceptual context, not a new `SourceDocument`.

Committed stages are immutable. Revise an uncommitted candidate freely within
the remaining limits. A revision of a committed finding creates a later stage
with an explicit `revision_of` reference/hash; it never overwrites that finding
or rewires descendants. Revision edges are distinct from supporting-parent
edges. Two versions of one logical finding cannot fill two selected-parent
slots. Detect exact duplicate scientific bodies separately from usage/events;
do not claim perfect detection of semantically equivalent wording. If a parent
later becomes unsupported, preserve history and show the current-head warning;
do not silently upgrade or rewrite descendants.

The 256-reference identity limit bounds fresh research and current eligibility.
If a later actual observation crosses it, stop fresh use and retain earlier
stages as `needs_review`. Read-only historical paper grouping uses the complete
saved context with a separate internal ceiling of4096, above the reachable
action/read/index bounds; it never truncates connectors or feeds that larger
context back into a model request. The stage receipt and finding stay unchanged.

## Novelty and confidence are model judgements

Keep the existing `BridgeFinding` schema/hashes intact. Store the new structured
assessment in the immutable stage envelope and bind it to the committed result:

| Field | Required interpretation |
|---|---|
| Novelty category | `known`, `partly_known`, `not_found_in_bounded_search` or `uncertain` |
| Novelty rationale | Concise model explanation tied to actual stage prior-work searches and read evidence; no claim of exhaustive coverage |
| Confidence category | `tentative`, `moderate` or `strong`, with a model-provided rationale and material limitations |
| Confidence score | Model-provided integer 0–100, explicitly labelled `uncalibrated_model_judgement` |

The numeric field fulfils the requested visible confidence score, but is never
presented as a calibrated probability, correctness measurement or application
verification. There is no script-assigned default. Stage count, graph depth,
quote matching and finite mathematical checks do not increase it automatically.
Malformed or absent assessment is a bounded model repair, not an invented
score. `known`/`partly_known` must refer to actual observed prior work; a paper
title or provider search hit alone is not a verified supporting quotation.
An actually read and cited full-text window may match a current-context
prior-work abstract or DOI hit through the observed transitive paper identity
closure. Hit metadata establishes identity only; it never substitutes for the
exact saved read evidence or becomes another independent paper.

Cards show the category, score’s uncalibrated label, rationale, objections and
search limitations together. The model may choose a preferred final stage and
explain that choice. Show discovery order and that preference; do not turn the
numeric score into a calibrated ranking algorithm. Live evaluation must later
test whether these judgements are useful and appropriately cautious.

## Persistence and result eligibility

The current database already permits several immutable `EvidenceResult` rows
in a run: its uniqueness is `(run_id, content_sha256)`, not `run_id`. Reuse
normal results and `EvidenceQuote` rows. Add an immutable stage descriptor and
parent edges rather than a second scientific-result store.

`StageCommit` binds run, ordinal, original model request/action identity,
canonical action hash, normal result ID/content hash, local depth, assessment,
and parent-basis/selection-ledger hash. `StageParent` records the ordered exact
parent result/hash and optional same-run stage ID. Add scoped durable reads
and context/search evidence needed for independent store validation. Stage
ordinal and model-action identity are unique per run; result ID belongs to one
stage. Result and stage ownership must agree through database constraints and
application checks.

Refactor evidence persistence into an internal shared-session helper. Save the
validated result, all quotes, stage descriptor, parent edges, event and
checkpoint acknowledgement in **one run-fenced transaction**. Do not commit a
positive result first and mark it as a stage in another transaction. Do not
hold authority/run locks over model or provider HTTP.
Reject generic positive `EvidenceStore.save_result` writes for opted-in ladder
runs. Only the private stage-store shared-transaction path can authorize their
positive writes and final pointer. Query filtering alone would not prevent an
accidental orphan result from being exposed by a final-result lookup.

Introduce one narrowly defined completed-result eligibility predicate:

- An ordinary positive result keeps its existing completed-run requirement.
- A ladder result requires an exact valid `StageCommit`, matching result/hash,
  ownership and current supported head, regardless of whether its containing
  run is still active, cancelled, interrupted or budget-limited.
- The ordinary succeeded-run branch explicitly excludes ladder runs. An orphan
  positive row must never become eligible merely because that run later ends.

Apply this predicate consistently to owned knowledge/map, memory, external F3
parents, artifact input, assessment and explicit sharing. It grants access to
the completed stage and its exact already authorized evidence, not the run’s
unread corpus, uncommitted drafts or another owner’s sources. Keep current-head
and privacy checks at every existing boundary.

## Replay, interruption and accounting

Replay starts from the deterministic original input and replays cached model
responses, tool observations and exact stage acknowledgements. **Never load
all latest persisted stages into the initial prompt or initialize logical stage
count from the latest database rows.** Those stages are future information at
earlier replay steps and would change paid request hashes.

Maintain global model, tool and activity-event sequence counters across stages.
A commit is keyed
by its original app-assigned `model_request_id`, action ordinal and canonical
action hash. On retry, the sink first finds that exact commit and returns the
same immutable receipt/result IDs and hashes, before applying fresh-stage
ordinal/parent checks. A different action under the same key is a hard replay
conflict. Same-run lists/reads expose only stages whose acknowledgements the
controller has already observed at that replay point.

Bind the native run input hash to its initial ladder intent/configuration and
approved profile, never to stages generated later. An acknowledgement or
cached stage read contains immutable commit-time facts; it cannot include
today’s run usage, ledger balance, latest head or latest stage list in place of
its original observation. Current authorization and supported-head checks may
stop a fresh operation, but do not rewrite a cached observation.

Controller preflight reconstructs the alias closure from source, index and
search metadata actually observed at that logical replay step, retaining every
observed metadata variant across stage boundaries. It must not consult a dynamic
database identity context containing future saved locators before replaying an
earlier commit. The sink returns an exact matching prior receipt first; a
genuinely fresh commit independently uses the complete current durable run
alias closure. This preserves historical packet bytes while still rejecting
fresh evidence whose paper identities overlap.

Distinguish repairable model rejection from control interruption. Bad proposed
citations, missing parent coverage or invalid assessment can consume an
existing bounded repair. Lease loss, cancellation, database response loss or
ambiguous stage commit raises the worker’s control-stop signal. It must never
be converted to an ordinary failed-tool observation followed by another paid
model call. An explicit compatible resume replays and recovers any committed
receipt without another model charge or duplicate stage.

Keep each stage’s `ResearchResult.usage` and events as the immutable prefix
snapshot at its commit. Add a separate ladder run summary with all stage
references, selected final stage, current run state, cumulative total usage and
accounted/held cost, plus the actual stop reason. Selecting an earlier stage
cannot hide later paid calls. Do not save a duplicate positive result merely
to update totals.

On deliberate model finish, the existing final result pointer selects the
exact committed stage. The ordinary result endpoint can return that unchanged
normal result; a separate owned ladder-summary endpoint carries the whole-run
view. On cancellation, budget exhaustion, authority expiry, unknown response
or interruption, retain all committed stages and label the run’s actual partial
state. A completed stage does not turn a cancelled or exhausted run into a
successful run. No stage plus an honest insufficient-evidence conclusion keeps
the existing useful negative-result path.

`selected_final_stage` is set only by an explicit model finish selection. If
limits, cancellation, unknown work or an insufficient next-stage conclusion
stop work after positive commits, it remains null. The summary and stage-detail
surfaces still expose every completed stage, but the old final-result endpoint
does not synthesize a selected positive. Do not choose a score-based “best” or
silently label the last committed bridge as the model’s preference. A deliberate
model decision to finish with one saved bridge uses the explicit finish action.

## Protocol and activation compatibility

Do not change the ordinary `SYSTEM_PROMPT`, `MODE_INSTRUCTIONS`, action adapter,
default `ResearchLimits`, legacy worker fingerprint or absent optional input
serialization. Add a separate ladder prompt/schema/orchestration fingerprint
and a narrowly selected limits extension. Ordinary Explore/Ask/Surprise and
their old journals keep their exact paths and hashes.

The existing `ResearchLimits`, `NativeTaskProfile` and native-limit validation
paths cap actions at 16. Give the opted-in ladder its own bounded profile/limit
validation rather than widening those ordinary contracts. Preflight the new
static prompt/schema against the reviewed context character allowance before
approval, using actual serialization rather than a token estimate.

Keep the existing five native task profiles unchanged. Add a truly optional
`discovery_ladder_profile` extension to the activation manifest; when absent it
is omitted recursively from canonical serialization and every old hash. Its
profile covers only explicitly opted-in Surprise research, with the new
adapter/prompt hash, stage/depth ceilings and action cap. The optional initial
discovery input is likewise absent from old intent hashes. Omission must cover
manifest/profile/binding hashes, worker fingerprints and exact stored API
projections. Ordinary `profile_for_task('research')` never silently selects the
extension: the original run binding must select it from the actual opted-in
Surprise input.

Enabling this profile requires a freshly reviewed activation. An old five-task
approval does not authorize it. Fresh admission/grants compare the current
ladder protocol fingerprint; historical manifests and known journals remain
validatable without recomputing against new prompts. The original per-run
resolver chooses and pins the exact ladder profile before configuration,
fingerprinting or controller construction. Finalizing known accounting remains
independent of a newer deployment’s ability to continue scientific replay.

The activation remains operator-browser scoped. Public research access is
separate D2 work; this feature neither grants anonymous spend nor publishes a
finding. Public sharing remains an explicit revocable owner action. No ladder
stage triggers reassessment, writing, formalisation or publication by itself.

## Workspace and downstream actions

The Surprise workspace streams real field selections, actual searches/reads,
bounded hypothesis decisions, parent choices, objections, committed stages and
stop decisions. These are public research summaries, not hidden chain of
thought. Tool events represent performed operations; model intentions must be
labelled separately. Failed and discarded candidates never look like supported
discoveries.

Stage cards open the normal evidence view and show parent links, primary-paper
coverage, depth, model assessment and commit-time scope. The owned map adds
finding-to-finding edges separately from source-evidence edges. Preserve exact
full-text ranges, abstract/full-text distinction and paper alias grouping.
The run header shows total calls/cost and partial/complete state independently
of the selected stage card. Reload/SSE replay must reconstruct the same stages
without model execution.

Formalise and Write target an eligible stage’s ordinary immutable result ID.
The current artifact basis includes that exact finding and its original quoted
evidence. Parent edges and stage novelty/confidence remain inspectable in the
source reader; they are not yet separate fields in the artifact model context.
An explicitly bound ancestry/assessment extension remains follow-up work and
must preserve historical basis/profile hashes through a versioned opt-in and
fresh approval. Ancestry itself is not independent support. Keep all existing
label, quote, bounded-check and publication-approval rules. The existing
one-active-owner quota still applies: a user may need to finish or stop the
investigation before starting another paid artifact task. Do not promise an
automatic concurrent follow-up.

## Implementation slices and acceptance

1. **Pure contracts and protocol.** Freeze optional input/profile serialization,
   ladder-only actions, stage assessment/receipt/summary, context boundaries and
   deterministic profile hashes. Prove old absent-field hashes remain exact.
2. **Atomic storage and eligibility.** Add stage/parent/read records and evidence
   shared-session persistence. Independently validate active context, exact
   quotes, all parents, aliases and current heads. Centralize the narrow
   completed-result rule and preserve old F3/memory restrictions.
3. **Controller and worker.** Extend the same loop and recorded-tool seams with
   global counters, scoped searches/reads, atomic sink acknowledgements and
   selected-stage finalization. Preserve native single-send/accounting rules.
4. **API and workspace.** Add owned bounded stage/summary reads, cards/map edges,
   partial-state totals, downstream stage selection and resumable activity.
   Update the external client only for explicit new surfaces.
5. **End-to-end verification.** Use synthetic models/providers through actual
   PostgreSQL, the real worker/native Meta observer with mock transport, actual
   HTTP and the browser. Include adaptive alternate actions so fixtures verify
   the controller rather than supplying a scripted production discovery.

Required journeys cover two independently validated base bridges followed by
a higher bridge chosen by Meta; reuse of eligible owned parents; changed fields
and discarded hypotheses; a weak proposed meta-connection that is rejected;
one-bridge and no-bridge honest stops; exact parent-passage mismatch; shared
aliases/windows; future/foreign/revised-parent abuse; stale heads; all limits;
and ordinary Explore/Ask/Surprise regressions. Do not require every valid run
to climb or force one field pair in production.

Crash/race coverage includes response loss after atomic stage commit, cancelled
commit, resume with later persisted stages hidden from earlier replay, exact
receipt recovery, no duplicate charge/result, stage-level eligibility while a
run is partial, orphan-positive exclusion, original-profile replay after a new
activation, known-invalid action repair, and unknown Meta response stopping
before any further call. Browser checks cover mobile, reload/SSE replay,
stage-to-original-evidence navigation, exact full-text coverage, private
ownership and Formalise/Write selection from a retained partial result.

Fixture implementation is a separate gate from live science. After explicit
owner authorization, native evaluation must assess actual Meta field choices,
source access, mapping validity, objection quality, novelty search coverage,
uncalibrated confidence, useful higher connections, latency and counted cost.
Deployment verification must separately prove SSE, worker restart, persistence,
quotas and recovery on the real public host. Until those gates pass, do not
claim a defensible scientific discovery, native billing proof or deployed
automatic discovery experience.
