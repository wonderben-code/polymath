# CLAUDE.md: working on Polymath

You're picking up **Polymath**, a convergence engine built for the Meta Global AI Developer Hackathon 2026. The owner is Ekram Alam (Eks). He sets direction and taste; you write the code.

## Read first
1. `docs/SPEC.md`: what we're building and what "done" means
2. `docs/ARCHITECTURE.md`: stack, modules, data model, API, prompts, deploy (decisions already made, so follow them)
3. `docs/BUILD_PLAN.md`: the phase-by-phase plan. **Work through it in order and tick the checkboxes as you finish items.**
4. `docs/HACKATHON.md`: deadlines, judging, setup status, and the API facts still to confirm

## Hard deadline
Submission is due **Thu 5 Nov 2026, 09:00 Bangkok / Wed 4 Nov, 9:00 PM ET**. Target submitting by **Wed 4 Nov, 21:00 Bangkok**. When time is short, use the cut list in BUILD_PLAN.md. Never cut Explore + critic + grounded evidence, the live run view, the live URL or the demo video.

## Rules
- **Muse Spark is the model** (Meta Model API, OpenAI-compatible, `https://api.meta.ai/v1`, `muse-spark-1.1`). Keep the client generic (`httpx` → `/chat/completions`) so it's testable.
- **Secrets:** the API key lives in `.env` / server env vars only. Never commit `.env`, never send the key to the browser, and never log it.
- **Credits are finite ($150).** Develop and test against `FakeLLM` and fixtures. Use live calls for integration checks and the demo. Keep run limits configurable, with low defaults.
- **Truth over plausibility:** no bridge is surfaced without a critic verdict and evidence quotes **verified in code** against the source text. Never invent papers or IDs.
- **Tests:** `pytest` must pass offline (no network, no API key) before every push.
- **Do not modify or break** `.github/workflows/bitcoin-timestamp.yml` or `timestamps/`. Every push to `main` gets Bitcoin-stamped automatically.
- **Scope boundary:** Polymath works on the open literature only. Never add, describe or reference the owner's unpublished theoretical research or any unrelated venture, in code, docs, commits, prompts or demo content.
- Keep the dependencies small: `fastapi`, `uvicorn`, `httpx`, and `pytest` for dev. Add more only if they clearly earn their place.
- Commit in small, meaningful steps with clear messages. Update the Status checklist in `README.md` as phases complete.

## Commands (once Phase 1 exists)
```bash
pip install -e ".[dev]"
pytest
polymath explore "self-organised criticality" --fake   # offline
polymath explore "self-organised criticality"          # live (needs MODEL_API_KEY)
polymath serve                                          # http://localhost:8000
```

## Working with Eks
He isn't hands-on with code. Explain progress in plain English, show results (screenshots, sample bridges, the live URL), and ask him about taste and direction (which seed concepts, how the UI should feel), not about implementation details.
