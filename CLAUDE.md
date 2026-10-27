# CLAUDE.md: working on Polymath

You're picking up **Polymath**, built for the Meta Global AI Developer Hackathon 2026. The owner is Ekram Alam (Eks). He sets direction and taste; you design and write the code.

## Read first
1. `docs/SPEC.md`: **WHAT** we're building. The product, what it lets users do, principles, the suggested flow, the definition of done
2. `docs/ROADMAP.md`: milestones and dates
3. `docs/HACKATHON.md`: deadlines, judging, setup status, things to confirm
4. `docs/ARCHITECTURE.md`: agreed implementation baseline and unresolved technical prerequisites
5. `docs/BUILD_PLAN.md` and `docs/PROGRESS.md`: task-level acceptance gates, current evidence, blockers, and next work

## Current state
The owner approved recording the full-product development direction on 27 October 2026 and authorized implementation and incremental pushes. The architecture is in `docs/ARCHITECTURE.md`, executable tasks in `docs/BUILD_PLAN.md`, and verified progress in `docs/PROGRESS.md`. The application foundation (A3) is implemented. Read `docs/DEVELOPMENT.md` for actual setup and check commands. Live research remains unavailable. Continue A1–A5 toward a real Meta-led investigation, then the durable execution and spending controls in B. Use the progress tracker to distinguish completed implementation from unverified live integrations.

The full specified product is the delivery target: Explore, Ask, Surprise Me, live research, connections between connections, a growing knowledge base, a map, Formalise, Write, approval-gated Publish, and a public API. Earlier after-v1/stretch labels for these features are superseded. Sequence them around the shared engine; do not silently omit functionality or substitute simulated integrations.

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
