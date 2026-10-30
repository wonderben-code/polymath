# CLAUDE.md: working on Polymath

You're picking up **Polymath**, built for the Meta Global AI Developer Hackathon 2026. The owner is Ekram Alam (Eks). He sets direction and taste; you design and write the code.

## Read first
1. `docs/SPEC.md`: **WHAT** we're building. The product, what it lets users do, principles, the suggested flow, the definition of done
2. `docs/ROADMAP.md`: milestones and dates
3. `docs/HACKATHON.md`: deadlines, judging, setup status, things to confirm
4. `docs/ARCHITECTURE.md`: agreed implementation baseline and unresolved technical prerequisites
5. `docs/BUILD_PLAN.md` and `docs/PROGRESS.md`: task-level acceptance gates, current evidence, blockers, and next work

## Current state
The owner approved the complete product direction and incremental main pushes. Read the specification, architecture and current progress before changing scope. The delivery target includes Explore, Ask, Surprise, live activity, connections between connections, growing memory/map, Formalise, Write, approved Zenodo publication and a public API. Earlier stretch/after-v1 labels are superseded.

Local implementation now includes all three research modes, original-source memory, selected-bridge higher-level research, full-text indexes/windows, exact evidence, mathematical conjectures/finite checks, cited writing/exports, publication approval/delivery/reconciliation, literature watches/collection, source-bound reassessment and opt-in public snapshots/library/map. The standalone HTTP API/client is documented and tested. These results have been verified with synthetic intelligence and mock HTTP, not live Meta science or real Zenodo publication.

The native Meta composition now uses the existing research/artifact/assessment controllers through one per-run policy resolver and recorded-model adapter. Exact operator-browser activation binds a saved proposal, passed protocol observation, fresh human billing/funding review, original task profiles and the same global ledger. Fresh grants recheck authority after waits; known usage is journalled/accounted before action parsing. Unknown work never resends automatically. Ordinary app submissions, all writing/reassessment tasks and explicit native resume use the exact activation context. Legacy replay protocol remains v8; optional native profile/binding fingerprints preserve original policy for accounting/replay. All live gates remain off by default and the owner still forbids paid calls.

The optional Surprise discovery ladder now lets Meta save multiple bridges, choose exact earlier stages as premises and investigate higher connections inside the same run and budget. Atomic stage commits survive partial stops; exact receipt replay does not resend paid calls. Stage confidence is an uncalibrated model judgement, and later alias overlap marks a stage as needing review without rewriting history. Ordinary protocol and absent-input hashes remain unchanged. See `docs/DISCOVERY_LADDER.md`.

Controlled public research access is implemented through schema 0023: explicit durable owner funding, ordinary visitor ownership, non-resetting same-ledger caps and persistent rate/queue/SSE controls. It uses the same native worker for all scientific tasks, including after the funding operator signs out; each reviewed public grant has an exact deadline and an explicit stop. Defaults remain off. See `docs/PUBLIC_RESEARCH_ACCESS.md`.

Pinned scientific-context and claim-linked Ask/reassessment variants are implemented locally through schema 0024, including immutable admission sidecars, original native profiles, independent persistence checks, browser/SDK readers and exact exports. Legacy fields/profiles/hashes remain exact when omitted. Final release verification is tracked in `docs/PROGRESS.md`; only explicitly listed browser legs are proven. See `docs/SCIENTIFIC_CONTEXT.md`.

Explore collection EC1–EC6 are implemented and locally verified through schema 0025: shared stage ownership, exact admission, model-ranked atomic bridge collections, original native profiles/runtime, downstream provenance and UI/SDK. EC1 was saved as `346d60f`; its 67 pure cases and 40 archived SC compatibility cases remain the foundation. Final release checks pass 4,423 distinct tests: 2,194 backend unit, 1,311 PostgreSQL and 918 frontend. The full database run passed without failures in41m04s. The composite actual browser and HTTP-client journeys pass with synthetic sources and Meta MockTransport. See `docs/EXPLORE_COLLECTION.md`.

The last full SC release `a95011a` passed 3,939 tests: 1,863 unit, 1,232 PostgreSQL and 844 frontend. That is historical evidence, not a full Explore release pass. The current local package is healthy as UID10001, schema 0025, with all eight execution gates false and no configured actual Meta key/model. The composite actual browser journey passed approval, collection/ranking/exclusion, both readers, Formalise, SC paper/export, reload, mobile390 and funding Stop. Browser partial-run Resume remains omitted; actual PG replay/accounting checks cover it separately. The SDK validates exact receipt references and ranking packet hashes; hydrated finding DTOs cannot reproduce the server's stored evidence-envelope hash. A backup/restore quarantine plan exists only under `/tmp` for review; no recovery tool is shipped. Deployment, real billing/provider behavior and scientific quality remain unverified. Follow `docs/PROGRESS.md`; do not claim the whole product is deployed or live-tested.

Meta intelligence chooses research directions and adapts to evidence. Application code enforces tool contracts, evidence integrity, durable execution, permissions, and budgets. Preferred model: Muse Spark; verify availability and capabilities, using another capable Meta model if needed. Do not silently use a non-Meta model. Changes to product scope or major architecture decisions should be explained to Eks before replacing this baseline.

## Agreed next build (30 Oct 2026)
Polymath becomes a public tool: sign in or continue as a guest, free use (shown as limited) or run your own copy, and Connect Zenodo for every visitor. Visitors never paste Meta keys (Meta ToS 3.1); "Connect your Meta account" is shown only as a labelled vision. The order, decisions and owner actions are in `docs/PUBLIC_TOOL_PLAN.md`; read it before continuing that work.

## Fixed constraints
- The intelligence runs on **Meta's AI** (Muse Spark via the Meta Model API). Creative, non-trivial use of Meta tooling is 25% of the judging.
- It must be a genuinely **intelligent system** that reasons about where to look and what to believe, not a fixed script with model calls slotted in.
- It must be **live on a public URL**.
- Budget: **$150 in API credits**. Protect it in development and on the public site.
- Current owner instruction: **continue without paid calls** until credits and the account spending limit are configured and the owner confirms paid testing may resume. Keep all model execution disabled by default; a key appearing alone is not authorization to start paid tests.
- Deadline: **Thu 5 Nov 2026, 09:00 Bangkok / Wed 4 Nov, 9:00 PM ET**. Target submitting by Wed 4 Nov, 21:00 Bangkok.

## Rules
- **Secrets** never get committed, sent to a browser or logged.
- **Truth over plausibility:** no connection is surfaced without real evidence and a serious attempt to break it. Never invent papers or sources.
- **Do not modify or break** `.github/workflows/bitcoin-timestamp.yml` or `timestamps/`. Every push to `main` is Bitcoin-stamped automatically.
- **Scope boundary:** Polymath works on the open literature only. Never add, describe or reference the owner's unpublished theoretical research or any unrelated venture, in code, docs, commits, prompts or demo content.
- Commit in small, meaningful steps, and keep `README.md` status current.
- Update task checkboxes and `docs/PROGRESS.md` with meaningful verification evidence. Planned, fixture-tested, live-tested, deployed, and submitted are different states.
- Use the existing checkout; tasks already run in isolated environments. Do not create Git worktrees unless requested.

## Working with Eks
He isn't hands-on with code. Explain things in plain English, show results, and ask him about product direction and taste, not implementation details.
