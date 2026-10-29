# Key day: from Meta key to first real investigation

This is the ordered checklist for the day the Meta API key arrives. It joins the
existing operator flows ([META_READINESS.md](META_READINESS.md),
[META_VERIFICATION.md](META_VERIFICATION.md), [META_RESEARCH.md](META_RESEARCH.md))
into one path. Nothing here grants spending authority: **paid calls start only
after the owner explicitly says paid testing may begin.** A key appearing in the
environment is not that permission.

## What we already know (no key needed)

Read from Meta's developer documentation on 29 October 2026. Recheck anything
marked here if the documentation changes before key day.

| Fact | Value | Source |
|---|---|---|
| Endpoint | `POST https://api.meta.ai/v1/chat/completions` (matches Polymath's fixed endpoint) | dev.meta.ai/docs, chat-completions reference |
| Auth | `Authorization: Bearer $MODEL_API_KEY` | dev.meta.ai/docs |
| Models | `muse-spark-1.3` (recommended), `muse-spark-1.2`, `muse-spark-1.1`; 1,048,576-token context | dev.meta.ai/docs/models |
| Standard price | USD 1.25 input / 4.25 output per million tokens; cached input 0.15 | dev.meta.ai/docs/pricing-rate-limits |
| Reasoning tokens | Included in billed `completion_tokens` at the output rate and count toward the output-token limit | dev.meta.ai/docs/reasoning |
| Usage fields | `prompt_tokens`, `completion_tokens` (plus detail breakdowns) | chat-completions schemas |
| Output limit field | `max_completion_tokens`; the deprecated `max_tokens` Polymath sends is still accepted | chat-completions reference |
| Rate limits | 3,000 requests and 4,000,000 tokens per minute per team for Muse Spark | pricing-rate-limits |
| Region | Thailand is not a restricted territory | dev.meta.ai/legal/geographic-use-policy |
| Spending cap | Not documented: confirm in the dashboard and set the $150 limit there | — |

Consequences for Polymath:

- The `prompt_completion_inclusive_v1` billing contract fits: reasoning is inside
  `completion_tokens`, and cached input is cheaper than full input, so the
  conservative quote over-reserves rather than under-reserves.
- **Watch the output limit.** Polymath caps each call at 4,096 output tokens.
  Because hidden reasoning shares that cap, a long reasoning pass could leave
  little room for the visible action. The probe receipt's finish reason and
  output-token count tell us. If calls finish on `length`, decide between a
  larger reviewed cap or a lower `reasoning_effort` before research activation;
  both change the reviewed request, so they need a fresh proposal.
- Avoid the `-contributor` models: they are cheaper because Meta may train on
  the content.

Live free checks that already pass from the cloud environment (29 October): the
Meta endpoint answers (401 without a key), and arXiv search, arXiv full-text HTML
and the Zenodo sandbox respond. OpenAlex's shared free budget for this network was
exhausted, so set a free `OPENALEX_API_KEY` (see below).

## Step 0 — Owner setup (no code)

1. Claim the hackathon credits and confirm they appear in the Meta dashboard.
2. Add a card if required and **set the account spend limit to USD 150**.
3. Create an API key in the Model API dashboard.
4. Put it into the environment settings as `MODEL_API_KEY` — never in chat or Git.
5. Optional but recommended: create a free OpenAlex key and store it as
   `OPENALEX_API_KEY`. Polymath sends it only as an Authorization header.
6. Tell the engineer, in your own words, whether paid testing may start and
   what spending ceiling applies.

## Step 1 — Free preflight (no credits)

```bash
make preflight
```

Checks configuration presence (never values), that every spending gate is still
off, and that Meta, arXiv, OpenAlex and the Zenodo sandbox answer. Then:

```bash
make preflight PREFLIGHT_ARGS=--check-key
```

This lists the models visible to the key with an authenticated `GET /v1/models`.
It requests no completion and sends no prompt. Pick the model (expected
`muse-spark-1.3`), set `META_MODEL` in the environment settings, and rerun until
the summary shows no failures.

## Step 2 — Readiness proposal (no credits)

Follow [META_READINESS.md](META_READINESS.md). In short: enable
`BUDGET_READINESS_ENABLED`, allow the operator in `BUDGET_ADMIN_ACTOR_IDS`, sign in
at `/operator/budget`, and enter the proposal from the facts above with their
evidence: model ID, pricing in microdollars per million (1,250,000 input and
4,250,000 output for Standard Muse Spark), absolute billable bounds, application
limits and the account caps. Preview, check the derived quote, then save it.
Saving activates nothing.

## Step 3 — One verification call (first paid call, cents)

Only with the owner's explicit go-ahead. Follow
[META_VERIFICATION.md](META_VERIFICATION.md): enable `PAID_META_PROBE_ENABLED`,
set `NATIVE_BUDGET_ACCOUNT_ID`, preview the manifest in the verification tab,
then **Arm and queue THIS one call** and run:

```bash
uv --project backend run python -m polymath.meta_probe_worker --once
```

Inspect the receipt: model identity, finish reason, reported usage and whether
the action parsed. Turn `PAID_META_PROBE_ENABLED` back off afterwards.

## Step 4 — Research activation

Follow [META_RESEARCH.md](META_RESEARCH.md): enable `PAID_META_RESEARCH_ENABLED`,
review the activation at `/operator/budget#research` (passed observation, pricing
and bounds, funding and cap), and activate. Start the worker:

```bash
make native-research-worker
```

## Step 5 — First real investigations

Run the three evaluation seeds one at a time from the research desk and watch
the live activity and spend after each:

1. Explore: *self-organised criticality*.
2. Explore: *error-correcting codes*.
3. Ask: a question about *phase transitions in learning*.
4. A deliberately weak or misleading input, to confirm an honest rejection.

Record each run's sources, mapping, objection, outcome, latency and cost in
[PROGRESS.md](PROGRESS.md). A useful rejection is a valid result; an invented
discovery is not.

## Stopping

The **Disable** control on `/operator/budget` stops new paid work immediately.
Unknown outcomes stay held and are never resent automatically. Turning any
`PAID_*` flag off also stops new work at the next process start.
