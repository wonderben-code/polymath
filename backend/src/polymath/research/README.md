# Experimental research core

This is task A4's in-process prototype. It is not a public research endpoint, durable job runner, literature-provider integration, or proof that a real Meta API accepts the proposed protocol. No live model request has been verified. The web application must continue reporting research unavailable until the durable execution, pricing verification, and spending controls are ready.

## Contracts

`ResearchController(model, tools, limits).investigate(goal)` is asynchronous. Inject a `ResearchModel` and `LiteratureTools`; the controller does not obtain credentials or make source requests itself. The model chooses each next action from previous observations. Actions are `search_literature`, `read_source`, and `conclude`, with a strict JSON schema. Invalid actions and unsupported evidence consume a bounded repair allowance.

The tool boundary discovers source IDs before allowing reads. Read results must retain their discovered ID and canonical HTTPS URL. Each document records text kind, extraction method, retrieval time, and a computed content hash. Candidate bridges require source-matched exact quotations from distinct canonical source URLs, a mapping, assumptions, an objection/verdict, novelty limitations, and successful counterevidence/prior-work search attempts. An empty search is an attempt, not proof of absence. Quotation integrity does not establish scientific correctness or novelty.

Limits bound model calls, actions, repairs, context characters, per-call output tokens, and model/tool wall-clock time. Public events describe completed actions without exposing private model reasoning or raw exception text. These bounds are not a replacement for the planned transactional spending reservations, persistent checkpoints, cancellation API, or publication authorization.

## Meta transport

`MetaModel` requires an explicit model ID and a `SecretStr` API key. Its default `live_enabled=False` sends no request. An operator must verify access, current protocol support, pricing, and account spending controls before explicitly enabling it. Credentials are sent only to the fixed `https://api.meta.ai/v1/chat/completions` endpoint; redirects and automatic retries are disabled.

The proposed adapter uses non-streamed chat completions and a validated JSON action envelope. Actual Meta native-tool support, schema support, model availability, and this wire format remain unverified. Do not substitute another provider to make a test appear live. Existing proxy-backed credentials must use their supported destination; never print their values.

Returned token usage is recorded when the provider supplies valid counts. Missing usage and ambiguous failed calls remain explicitly counted in `calls_without_usage`; they do not mean zero usage or zero cost. Timeouts are not automatically retried because the provider may already have performed chargeable work.

## Checks

From the repository root:

```sh
UV_CACHE_DIR=/workspace/.cache/uv uv run --project backend pytest backend/tests/test_research_controller.py backend/tests/test_research_meta.py -q
UV_CACHE_DIR=/workspace/.cache/uv uv run --project backend ruff check backend/src/polymath/research backend/tests/test_research_controller.py backend/tests/test_research_meta.py
```

All sources and responses in those tests are explicitly synthetic fixtures. The adaptive fixture changes its conclusion when the tool observation changes; that verifies observation delivery and controller branching, not a live model's intelligence. Mock HTTP checks establish transport behavior, not real service compatibility.
