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

Next are pinned scientific context and claim-bound Ask overviews (`docs/SCIENTIFIC_CONTEXT.md`), broader Explore branching/ranking, claim/source/public-memory retrieval, deployment/operations and real scientific evaluation. Native model access, actual billing semantics/prices/bounds, provider credits/caps, hosted behavior and scientific quality remain unverified. Follow `docs/PROGRESS.md` for final regression/browser evidence and the exact saved commit; do not claim the whole product is deployed or live-tested.

Meta intelligence chooses research directions and adapts to evidence. Application code enforces tool contracts, evidence integrity, durable execution, permissions, and budgets. Preferred model: Muse Spark; verify availability and capabilities, using another capable Meta model if needed. Do not silently use a non-Meta model. Changes to product scope or major architecture decisions should be explained to Eks before replacing this baseline.

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
