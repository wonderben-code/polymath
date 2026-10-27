# Polymath: Progress tracker

Last updated: 27 October 2026 (Bangkok). This file records verified outcomes; [BUILD_PLAN.md](BUILD_PLAN.md) holds task checkboxes and [ROADMAP.md](ROADMAP.md) holds target dates.

## Current position

**Stage: architecture and development baseline saved; application implementation has not started.**

Scope: the complete product, including Surprise Me, the knowledge base, map, and connections between connections. Meta intelligence controls research. See [ARCHITECTURE.md](ARCHITECTURE.md).

**First engineering objective:** A1–A5 in the build plan: establish real Meta access and capabilities, create the application foundation, and complete one bounded research investigation with real sources, a structural mapping, an objection, and an evidence-backed conclusion or honest rejection.

**Next independent task:** A3, application scaffolding and repeatable local/CI checks. Meta-dependent live work remains blocked until access is verified. No fixture result counts as a completed live investigation.

## Milestone status

| Milestone | Status | Evidence / remaining acceptance |
|---|---|---|
| Repository and planning documents | Complete | Existing specification plus architecture, roadmap, task plan, and tracker in this change |
| Cloud tooling | Partially verified | Git read access worked; OpenTimestamps 0.7.2 installed; all four local proofs parsed and matched commit-hash payloads; install script reran successfully |
| A — Real Meta research path | Blocked for live checks; local work available | Key/access/capabilities unverified; no application or live model test yet |
| B — Durable research and controls | Not started | Jobs, evidence storage, activity, security, budgets |
| C — Explore | Not started | Complete real-source browser investigation |
| D — Public deployment and API | Not started | Host selection, public URL, quota controls, persistence/recovery |
| E — Ask and Surprise Me | Not started | Both modes working end to end |
| F — Knowledge base, map, higher-level research | Not started | Scheduled ingestion/reassessment and navigable evidence graph |
| G — Formalise, Write, Publish | Not started | Real feature journeys, sandbox integration, approved production publication |
| H — Quality, release, submission | Not started | Full regression, scientific audit, polished release, submission confirmation |

## Blockers and owner actions

| ID | Requirement | Current evidence | Action / unblocked work |
|---|---|---|---|
| X1 | Meta API authentication and runtime network access | During onboarding, `MODEL_API_KEY` was absent and HTTPS to `api.meta.ai` received a proxy CONNECT 403. Draft requirements were saved; runtime propagation and publication are unverified. | Owner supplies the key securely in environment settings and saves relevant access changes; engineering rechecks actual access. Never enter the key in chat or Git. A3 and fixture-based work can proceed. |
| X2 | Verified model behavior, pricing, and account spending cap | Model names/prices in HACKATHON.md are unverified notes; no paid call executed. | Engineer verifies official documentation and actual API behavior; owner confirms account cap/credits before bounded live tests. |
| X3 | Deployment account and hosting allowance | No host or infrastructure budget selected. | Evaluate host requirements/cost early; obtain authorization before provisioning paid resources. Local implementation continues. |
| X4 | Zenodo publisher identity, credentials, and artifact approval | Not configured or tested. | Set up sandbox first; secure production credentials and explicit approval of an exact artifact before production publication. Other research features continue independently. |
| X5 | Official submission details | Repository checklist contains unanswered fields. | Verify rules, submission fields, video requirements, and deadline before final submission; owner handles personal/account confirmations. |

## Evaluation baseline

Proposed initial seeds: self-organised criticality; error-correcting codes; phase transitions in learning. Include a weak/misleading input and an evidence-changing case. These are evaluation inputs, not prewritten discoveries. Confirm their suitability using the first live investigations.

Track: real source identity, quote integrity, claim support, mapping assumptions, objection quality, prior-work search coverage, useful rejection, adaptive next actions, latency, usage/cost, job recovery, and deployed completion of every feature. Confidence scores are not calibrated probabilities until evaluated as such.

## Evidence log

| Date (Bangkok) | Change or observation | Interpretation |
|---|---|---|
| 27 Oct 2026 | Inspected every existing text file and timestamp workflow; checkout had no application, manifest, build command, or test suite | Planning-stage project; application work remains ahead |
| 27 Oct 2026 | Read-only Git remote access succeeded; OpenTimestamps dependencies checked; 4/4 local proof payloads matched their commit hashes | Tooling/local integrity verified; Bitcoin confirmations were not checked |
| 27 Oct 2026 | Saved cloud install/start instructions plus API hostname/key requirements | Draft persistence confirmed; runtime application and environment publication not established |
| 27 Oct 2026 | Owner confirmed Meta-driven intelligence and full feature scope, including Surprise Me; approved saving the development direction | Architecture and build baseline recorded; earlier reduced-scope/after-v1 labels superseded |
| 27 Oct 2026 | Validated 8 Markdown documents, 25 local links, balanced code fences, and 40 unique task IDs; Git whitespace check passed; timestamp workflow/proofs unchanged | Documentation baseline checked; no application tests exist or were claimed |

For subsequent implementation, append the task IDs, commit, exact meaningful checks and outcomes, deployed evidence where applicable, and remaining limitations. Keep planned, passed, failed, blocked, skipped, and unrun results distinct.
