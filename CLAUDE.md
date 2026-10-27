# CLAUDE.md: working on Polymath

You're picking up **Polymath**, built for the Meta Global AI Developer Hackathon 2026. The owner is Ekram Alam (Eks). He sets direction and taste; you design and write the code.

## Read first
1. `docs/SPEC.md`: **WHAT** we're building. The product, what it lets users do, principles, the suggested flow, the definition of done
2. `docs/ROADMAP.md`: milestones and dates
3. `docs/HACKATHON.md`: deadlines, judging, setup status, things to confirm

## Current state
WHAT is defined. **HOW is not decided yet.** Milestone 1 is to design the architecture: how the intelligence works, how Meta tooling is used, how truth is enforced, deployment, and budget protection. Propose it to Eks, and once he agrees, write it to `docs/ARCHITECTURE.md` and a task-level `docs/BUILD_PLAN.md`. Don't start coding before that.

## Fixed constraints
- The intelligence runs on **Meta's AI** (Muse Spark via the Meta Model API). Creative, non-trivial use of Meta tooling is 25% of the judging.
- It must be a genuinely **intelligent system** that reasons about where to look and what to believe, not a fixed script with model calls slotted in.
- It must be **live on a public URL**.
- Budget: **$150 in API credits**. Protect it in development and on the public site.
- Deadline: **Thu 5 Nov 2026, 09:00 Bangkok / Wed 4 Nov, 9:00 PM ET**. Target submitting by Wed 4 Nov, 21:00 Bangkok.

## Rules
- **Secrets** never get committed, sent to a browser or logged.
- **Truth over plausibility:** no connection is surfaced without real evidence and a serious attempt to break it. Never invent papers or sources.
- **Do not modify or break** `.github/workflows/bitcoin-timestamp.yml` or `timestamps/`. Every push to `main` is Bitcoin-stamped automatically.
- **Scope boundary:** Polymath works on the open literature only. Never add, describe or reference the owner's unpublished theoretical research or any unrelated venture, in code, docs, commits, prompts or demo content.
- Commit in small, meaningful steps, and keep `README.md` status current.

## Working with Eks
He isn't hands-on with code. Explain things in plain English, show results, and ask him about product direction and taste, not implementation details.
