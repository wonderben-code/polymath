# Pinned scientific context and claim-bound Ask overviews

Status: design for the increment after D2. This document authorizes no paid call,
activation, deployment, or migration. Meta remains the scientific model; the
existing controllers, worker, source store, and global budget ledger remain the
execution path.

## Intended behavior

Formalise, Write, artifact previews, and saved Markdown should retain the exact
scientific context of their source finding: its original discovery-stage
assessment, its parent ancestry, and the applied reassessments that were current
when the artifact was requested. That context must be pinned during admission,
not looked up again to construct a different prompt during replay.

An Ask overview should contain only model-selected claims that already have
classification, citations, and uncertainty disclosures. The model chooses the
claims and their order. The application copies their exact saved text into the
legacy summary field and renders the associated labels and citations. It does
not write a scientific summary or infer additional claims.

Existing results, artifacts, exports, activations, five ordinary task profiles,
optional ladder profiles, and known billing records retain their exact bytes and
hashes when the new intents are absent. Old results remain inspectable with their
original semantics. No historical summary is retroactively declared claim-bound.

## Dependencies verified in the current code

The following is a source trace, supplemented by actual adapter-schema
introspection. The D2 commit must be captured as a compatibility fixture before
implementation starts.

| Existing input | Where it is bound | Implication |
| --- | --- | --- |
| `ArtifactBasis` JSON | `ArtifactController.generate()` first user message; worker `artifact_basis` fingerprint; `GeneratedArtifact.basis_sha256`; Markdown renderer | A new context changes new requests, fingerprints, bases and exports. Its absence must serialize exactly as before. |
| Artifact source/formalisation pointers | `ArtifactRequest`; `native_run_input_sha256()` | These pointers currently do not bind an additional context snapshot. New requests need a conditional context SHA in the native scientific input. |
| Artifact system prompt, task instructions, action schema | `controller_protocol_sha256()` | `ArtifactBasis` is absent from the artifact action schema. Adding an omitted basis field alone does not change the three old static artifact profiles. New context instructions still require a separately approved profile. |
| `AnswerFinding` | Research `ConcludeAction`; assessment `revised_finding` action | Editing this DTO changes both research and reassessment action schemas and their native profiles. Leave it unchanged. |
| `AnswerFinding.summary` | Result payload, memory and knowledge summaries, public projection | It is free text today. Neither controller nor store binds it to the verified claims. A prompt instruction alone cannot enforce the desired restriction. |
| `ResearchResult` JSON | Evidence envelope and `EvidenceResult.content_sha256` | An omitted optional overview binding can preserve old result hashes. The store must independently validate a present binding. |
| Native per-step request | Exact model payload plus activation/profile/request-ID step hash; DB binding to run/input | New context or action semantics must be admitted before hashing. Known observation replay continues to use the original immutable request. |

The actual current `$defs` membership is:

| Adapter | `ArtifactBasis` | `ResearchResult` | `AnswerFinding` |
| --- | --- | --- | --- |
| `ACTION_ADAPTER` | absent | absent | present |
| `ARTIFACT_ACTION_ADAPTER` | absent | absent | absent |
| `ASSESSMENT_ACTION_ADAPTER` | absent | absent | present |

Current static controller digests, measured during the D2 worktree, are useful
cross-checks; they are not a substitute for archived canonical fixtures:

```text
research          1d596707f24f6786cefba5faf7b4eb6dbacee7a945727bac0be5b6e7bf118a56
formalise         4d54a08ef5446d11a649cea00fecde6fb1b8450c5f2d71070a34b4c9c8ac7a28
write_summary     1677478e453671ca7db26ac84b89d752366c3454a47f07b246a1d88e75cbe78e
write_paper       8f172bf33e8af22f9fab39afe87ab483932e24954961a350c92c6c15f1e1c9d0
reassess_finding  d51caa176d0bf82eac3e0a36f59e131f90f07ed4dab1a9cba8a0c0314bf961ba
discovery         53b6d8ff8e290a1dc971c65dca80008d9cfa8239a3e959c0e99b40bdb6b610d9
```

Relevant seams are `artifact_contracts.py:ArtifactBasis`,
`artifacts.py:bind_request/_context_unchecked/render_artifact`,
`meta_research.py:native_run_input_sha256`,
`meta_research_contracts.py:controller_protocol_sha256`,
`worker.py:ResearchWorker.run_once`,
`research/controller.py:_verify_answer_evidence`, and
`evidence.py:EvidenceStore.save_result/_validate_quotes`.

## First deliverable: artifact context pinned at admission

Use an explicit, versioned artifact intent:

```json
{"protocol":"polymath-artifact-scientific-context-v1"}
```

Add optional `scientific_context_intent` to `POST /api/v1/artifacts/jobs`.
The request selects the protocol only. Clients never supply context bodies,
confidence, assessment IDs, parent graphs, or context hashes as authority.
Absence follows the existing code path exactly.

Create a pure `ArtifactScientificContext` DTO and an immutable
`ArtifactContextBinding` sidecar keyed by artifact run ID. A sidecar avoids
rewriting existing `ArtifactRequest` rows. Its canonical envelope is:

```text
protocol: polymath-artifact-scientific-context-v1
source_reference: exact requested MemoryReadReference
source_revision:
  root_reference, current_reference, revision, state_at_admission
  applied_assessments: ordered AppliedAssessmentContext[]
stage_origin: StageOriginContext | null
ancestry:
  nodes: ScientificAncestor[]
  edges: ScientificParentEdge[]
prior_formalisation: PriorArtifactContextReference | null
```

The sidecar stores `run_id`, `protocol`, canonical `context`,
`context_sha256`, source result/run/hash, and creation time. Database constraints
bind it to the request/source, bound its JSON size, and prohibit update/delete.
The migration revision follows the D2 head; do not reserve a number prematurely.

`AppliedAssessmentContext` contains only applied scientific conclusions:
assessment ID/run/payload SHA, head revision, exact prior and candidate result
references, disposition, cited rationale, verified quote metadata, and
limitations. It excludes private events, raw model responses, watches/queries,
usage, budget data, and failed or CAS-losing candidates. Include the applied
chain relevant to the source through admission, including an applied
`unchanged` assessment. Preserve exact historical source handles and excerpt
coverage. A mutable `FindingHead` alone is not proof that a candidate was applied.

`StageOriginContext` contains the exact original stage ID, receipt SHA, original
stage result reference, ordinal/depth, `StageAssessment`, and its exact parent
references. Its relationship to the requested source is explicit:
`exact_stage_result` or `reassessed_descendant`. Confidence is attached to
`stage_result_reference.content_sha256`; it is never assigned to a revised
finding merely because both belong to the same logical finding.

`ScientificAncestor` contains its exact result reference and saved finding,
plus an original stage assessment when one exists. Edges retain their original
parent order and distinguish scientific premise from revision relationships.
Read `StageParent` for that particular stage, and ordinary
`ResearchRunParent` for an ordinary finding. Do not replace stage-specific
parents with the union of all parents used anywhere in a ladder run. Graph
nodes are earlier model hypotheses, not independent papers or fresh evidence.
Do not dereference external URLs or silently read additional source text while
building this context.

`PriorArtifactContextReference` pins the incorporated formalisation's artifact
ID, payload SHA, basis SHA, and its context SHA when present. A Write request
keeps the prior artifact's recorded context distinct from its own admission
context. An unchanged source result may have gained a later applied assessment;
that does not rewrite the earlier formalisation's provenance.

Use deterministic traversal and ordering, deduplicate by exact result ID/hash,
reject cycles and contradictory edges, and enforce maximum 128 nodes, 512 edges,
64 applied assessments, and 256 KiB for the complete context. These are hard
admission limits, not truncation rules. Exceeding them fails before enqueue or
reservation with `artifact_context_too_large`. Existing passage/basis/output
limits continue to apply; a context cannot displace or shorten original quotes.
Before committing admission, render the exact prospective initial messages with
the selected approved profile and reject a dynamic basis that exceeds its
context-character ceiling. The 256 KiB storage bound is not permission to send
more than the reviewed 90,000-character maximum. Use a shared pure message
builder so admission and execution measure the same prompt/schema/basis bytes.
Also apply the existing `MAX_ARTIFACT_BYTES` check to the complete prospective
`ArtifactBasis` before enqueue, including passages, prior formalisation and
context. A context-only byte limit and a character limit do not establish this
bound, particularly for non-ASCII text.

Admission remains one transaction:

1. Authenticate the live owner and existing native/private/public authority;
   apply the existing queue, account, and one-active-owner rules.
2. Resolve the requested supported current finding and exact prior
   formalisation using the existing eligibility checks.
3. Reconstruct and verify the immutable stage, ancestry and applied assessment
   records; build and hash the bounded DTO. Recheck current eligibility before
   finishing admission. Retain the established lock order; do not add
   `Run -> FindingHead` locking that conflicts with reassessment finalization.
4. Insert the artifact request and sidecar before computing native input hash
   and binding the run. Roll back everything on size, ownership, or integrity
   failure; no active-job slot or hold is consumed.

Add optional `ArtifactBasis.scientific_context` containing the verified envelope
and SHA. Its serializer omits the entire field when absent. Do not globally
change `StrictModel` serialization or remove old explicit nulls elsewhere.
For present context, `load_basis()` reads and validates the saved sidecar; it
never queries the latest head to rebuild prompt metadata. The existing live
source-eligibility gate may stop stale work; it must not substitute a new source
or silently update the pinned context. Historical artifact GET/export remains
available according to existing ownership rules.

Conditionally add `artifact.scientific_context_sha256` to
`native_run_input_sha256()` only when the sidecar exists. The same context
envelope enters the artifact user message and existing worker basis digest.
`ArtifactStore.save()` independently validates the sidecar/basis/source binding;
the model cannot emit or edit trusted context metadata.

The new artifact controller variant adds fixed instructions distinguishing
source evidence, model assessments, stage confidence, and inherited hypotheses.
Formal proof language and finite-domain checking restrictions remain unchanged.
Write output cannot upgrade a source claim label using ancestry, confidence
scores, an `unchanged` assessment, or repeated quotations.

For new artifacts only, render a provenance section from the saved context:
original stage assessment with its original result hash; parent links and
relationship kinds; applied reassessment disposition/rationale/limitations;
and the exact source/reference hashes. Preserve full-text excerpt disclosures.
Preview/detail includes the same optional context. Saved Markdown is exported
verbatim and hash checked; old Markdown is never rerendered.

## Second deliverable: Ask overview selected from classified claims

Use an explicit run intent:

```json
{"protocol":"polymath-ask-claim-overview-v1"}
```

Add optional `ask_overview_intent` to `POST /api/v1/runs`, legal only for an
ordinary `research` run in Ask mode. It cannot be combined with a Surprise
ladder intent. Pin it in an immutable per-run intent row and add it to native
initial-input hashing only when present.

Keep `AnswerFinding`, `ConcludeAction`, and `ACTION_ADAPTER` unchanged. Define a
separate `BoundAnswerDraft` and `ASK_OVERVIEW_ACTION_ADAPTER`. Reuse existing
tool actions; replace only the positive Ask conclusion with:

```text
outcome: answer
question: unchanged exact-question contract
claims: existing AnswerClaim[]
overview_claim_ids: ordered unique ClaimId[1..8]
limitations, unanswered_questions: existing contracts
```

There is no free `summary` in this draft. Every selected ID must name a claim in
the same conclusion. The existing claim/evidence validators, postulated
derivation plus two-paper requirement, speculative caveats, required searches,
exact quote checks, and source-version rules still run.

After validation, create the existing `AnswerFinding` using:

```text
summary = "\n\n".join(claim.text for each selected ID, in selected order)
```

Use the validated exact claim strings. Do not clip, paraphrase, add transitions,
or choose different claims. If the joined string exceeds the existing 3,000
character summary limit, reject it with `ask_overview_too_long` within the
existing bounded repair budget. No extra model calls are reserved by this
feature; the original per-run call and money caps still apply.

Add optional `ResearchResult.overview_binding`:

```text
protocol: polymath-ask-claim-overview-v1
claim_ids: ordered unique IDs
```

Omit the field entirely when absent. Store validation independently requires an
Ask answer, existing IDs, exact deterministic summary equality, and each
selected claim's already verified citations. A matching summary string without
the binding is a legacy answer, not proof of this protocol. A forged binding on
negative, bridge, or non-Ask results is rejected. The result envelope SHA binds
the selection, labels, text, and exact evidence together.

The store also checks the immutable admission intent in both directions: a
successful answer from a new Ask-overview run requires this binding, and a
legacy run cannot acquire it through a forged result object. Apply the same
rule to revised candidates from the new assessment variant. Pure historical
parsing validates the saved binding without requiring today's approval; fresh
result persistence verifies it against the actual original run intent/profile.

The Ask controller variant uses a distinct fixed prompt and action schema;
keep the old scope-only summary instruction and all old prompt bytes untouched.
Use the existing loop, tools, counters, events, and recorded Meta model with an
explicit protocol branch. The overview is a labelled/cited view of claims,
not another evidence source or a new classification.

Bound Ask revisions must retain the same guarantee. A separately selected
assessment variant accepts the bound answer draft for a revised Ask answer and
produces a fresh overview binding. Existing `ASSESSMENT_ACTION_ADAPTER` remains
unchanged. An old assessment profile cannot admit a bound-Ask revision while
claiming the new guarantee. Because disposition is unknown before the call,
reject the entire reassessment admission for a bound-Ask source unless its new
assessment intent and matching optional profile were selected. An unchanged
assessment retains the exact original result/binding. A revised legacy answer
stays legacy unless the new assessment intent/profile is explicitly selected.

The assessment request field is `assessment_overview_intent`, with protocol
`polymath-ask-reassessment-overview-v1`. This input protocol differs from the
saved output binding, which remains `polymath-ask-claim-overview-v1`.

Detect a bound source from its validated stored result envelope, never a client
flag. Pin the selected versioned assessment intent and an optional
`AssessmentBasis.prior_overview_binding` in the existing immutable assessment
basis at admission; omit that field for old bases. Validate it against the exact
prior result/hash. The existing assessment basis SHA then binds this source
semantics in native input, with the explicit new intent added conditionally.
This is an input-basis extension, not a mutation of `AnswerFinding` or the old
assessment action schema. Replay uses the saved basis/intent and never redetects
the protocol from a different current head.

## Approval and execution profiles

Keep the five existing `task_profiles`, their derivation and schemas unchanged.
Keep the optional ladder extension unchanged. Add a separately versioned,
optional `scientific_context_profiles` extension to private and public
activation manifests. Omit it completely when absent. It contains bindings for
the three artifact context variants, the Ask overview research variant, and
the bound-Ask reassessment variant. Each binding names its capability and
contains the original quantitative task profile plus its own static controller
digest. A research binding also restricts mode to Ask; it does not replace the
Explore/Surprise profile.

Fresh builders derive the extension from the proposal and exact new prompts,
schemas, and context/binding protocol. Include the new artifact input-context
schema in that variant's digest even though the old digest did not include
`ArtifactBasis`. Existing financial bounds, full output/context limits,
timeouts, and per-task call ceilings remain authoritative. Reject profiles
whose fixed prompt/schema cannot fit the approved context ceiling.

Admission selects a variant only from the explicit immutable run intent and a
matching approved extension. Never fall back to a legacy profile when a requested
extension is unavailable. No current context, current prompt hash, or latest
activation enters historical parsing or original-profile reconstruction.
Fresh native grants compare the selected current variant; journal replay and
free known billing use the original request, observation and profile.

The optional extension protocol is
`polymath-scientific-context-profiles-v1`, with exactly five ordered bindings:
three `artifact_scientific_context_v1` task bindings (Formalise, summary, paper),
then `ask_claim_overview_v1` (research/Ask), then
`ask_reassessment_overview_v1` (reassess/Ask). Each binding has capability,
mode, the original quantitative `NativeTaskProfile` and its exact profile SHA.
Preview accepts only an optional `scientific_context` protocol marker and derives
the profiles on the server; a browser never supplies its own profile derivation.
Partial extension sets are not supported in v1.

A new manifest requires explicit fresh private activation or public delegation.
This feature does not extend a deadline, revive stopped authority, change
sponsorship, clear unknown holds, reset quotas, or activate paid work. D2 public
delegation rules, same ledger, resource limits, and spending-after-logout
deadline remain in effect. Capability-specific controllers never construct a
fallback model or use a second worker/engine.

## API, UI, sharing, and SDK seams

| Surface | New behavior |
| --- | --- |
| Artifact admission | Optional versioned intent; server pins context. Client-supplied provenance is forbidden. |
| Artifact detail/preview/export | Present immutable context and SHA; render stage confidence only with original hash/scope. Old records omit the new field. |
| Research admission/summary | Optional Ask intent; show selected protocol without exposing budget or operator facts. |
| Result GET and Ask UI | Preserve overview binding. Render selected claims with labels, citations, derivations/caveats; clicking an overview claim opens that exact claim. |
| Knowledge cards and memory | Use the same selected claim references; avoid stripping labels while presenting derived summary as a standalone answer. Memory still treats findings as model hypotheses. |
| Reassessment UI | Distinguish original stage confidence from the current revised finding and its applied assessment; display only original-bound scores. |
| Public sharing | Never copy private context graphs, run/result IDs, assessment handles or owner data into a public projection. A newly shared bound Ask projection retains the overview selection using local claim IDs only, with independent validation and absent-field hash compatibility. Its UI retains claim labels and citations. Existing shares remain immutable. |
| SDK | Accept explicit Ask intent for create; preserve result/artifact optional fields. Export still verifies exact metadata SHA and byte count before writing. Do not invent client-generated overview text or context. |


Protocol discovery uses a separate existing-owner GET
`/api/v1/research/protocols`, preserving the strict D2 access response exactly:

```json
{
  "protocol": "polymath-research-protocols-v1",
  "activation_sha256": null,
  "capabilities": []
}
```

The canonical unique capability subset is `artifact_scientific_context_v1`,
`ask_claim_overview_v1`, `ask_reassessment_overview_v1`. The artifact capability
requires all three artifact bindings. An eligible public legacy delegation may
return its exact activation SHA with an empty list; closed/private contexts
return null and an empty list. Null with nonempty capabilities is invalid.
A private operator uses its existing privileged current manifest instead.
The GET creates no session, authority, reservation or scientific work, exposes no
model/account/key/pricing/profile details, and remains subject to normal read
resource controls. Operational rate counters are expected.

The browser and SDK require the returned SHA to equal the funding access
`expected_activation_sha256` used at the synchronous submit boundary. Ignore
stale responses on browser/hash changes. Cache immutable capability selections
by activation and include explicit rechecks in the existing read budget.
Discovery is not admission and cannot guarantee quota availability.

Ordinary Ask/Write actions automatically select the improved variant when the
current approved capability supports it. They send the explicit intent in their
single request; visitors do not need a technical protocol checkbox. If a selected
capability becomes unavailable, fail that request without a silent fallback or
POST retry. Legacy grants/history retain their earlier semantics and honest
provenance. The funding operator separately chooses and reviews the optional
extension before activation.

Add static capability/error codes for unsupported protocol/profile, invalid
context integrity, context too large, unknown/duplicate overview ID, overview
too long, and overview-binding mismatch. Existing private headers, CSRF/origin,
public resource controls, ownership checks, and sanitized errors apply. No new
public read of private ancestry is introduced.

## Compatibility proof and test matrix

First record the pushed D2 commit SHA. Generate baseline fixtures from a
read-only `git archive` of that commit in a separate temporary checkout/process,
using deterministic synthetic proposals, timestamps, IDs, quotes and model
responses. Do not generate expected old values from the modified implementation.

Capture old action schemas; all five controller/task profile hashes; optional
ladder profile; private and public canonical manifests/hashes; ordinary, Ask,
stage, and applied-reassessment result envelopes; artifact bases, initial model
messages, worker fingerprints, and exact Markdown bytes/hashes. Include ordinary
and full-text source coverage and an incorporated formalisation. New parsers
must reproduce every old value exactly when the intents/extensions are absent.

The meaningful new tests are:

1. **Admission and immutability:** exact stage/source hash binding, ordered
   stage-specific parent graph, applied-only reassessment chain, no candidate
   leakage, duplicate/cycle/conflicting-hash rejection, owned-only references,
   and size failure rolling back enqueue/sidecars/financial binding.
2. **Historical confidence:** a revised stage descendant receives its applied
   reassessment context and the original stage assessment labelled historical;
   its new result hash never inherits the original score. Unchanged assessment
   adds context without rewriting the source or prior formalisation.
3. **Replay:** commit admission, then alter the current head or add an applied
   assessment before resume. Prompt/basis/context hash stays pinned, or existing
   live eligibility stops work. No latest-context reconstruction, extra model
   call, or source substitution is permitted. A lost admission response remains
   an unknown POST outcome: inspect owned run history to recover the recorded
   run/context; do not automatically submit again or invent idempotency support.
4. **Ask integrity:** multiple labels, arbitrary ID order, duplicate/missing IDs,
   postulated/speculative restrictions, exact joined text, 3,000-character
   boundary, malformed bindings, and store rejection even when controller
   validation is bypassed. No path allows a separate uncited overview sentence.
5. **Revision integrity:** unchanged bound Ask retains bytes; revised bound Ask
   selects and validates new overview IDs through the approved variant;
   unsupported old profiles fail admission without a call.
6. **Native end to end:** actual `MetaModel` over MockTransport, real PG and
   fixed source-client mocks for context Formalise/Write, bound Ask and revised
   Ask, in private and durable-public modes. Assert original quantitative caps,
   one global ledger, original key snapshots, strict usage settlement before
   semantics, no resend on unknown/lost response, and no scripted fallback.
7. **Historical billing:** stopped/keyless/current-profile-changed runs still
   finalize known journal billing with database-only configuration. Unknown
   remains held. Historical science replay may stop on a request mismatch; it
   must not charge for a different current request.
8. **API/UI/SDK:** strict optional parsers, exact labelled overview navigation,
   immutable context preview/export, no private provenance in public sharing,
   absent-field compatibility, disabled capability, and actual loopback HTTP
   journeys. No paid/live provider calls occur in verification.

Implement in two reviewable slices: pinned artifact context and its three
optional profiles first; claim-bound Ask plus its revision profile second.
Each slice must pass archived compatibility fixtures before full regression and
packaging. Neither slice enables public or private paid execution by default.
