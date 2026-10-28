# Public-literature adapters

Status: partial C1 implementation. All automated tests use explicitly synthetic HTTP fixtures. Live OpenAlex/arXiv access has not been verified; production research remains disabled and the worker is tested through explicit injection. No model calls or paid requests are made by the test suite.

## Contract

`OpenLiteratureTools` implements the controller's literature protocol:

- `await search(query, purpose)` returns the typed `SearchResults` contract from `polymath.research.models`: source references plus provider failure diagnostics.
- `await read(source_id)` returns a `SourceDocument` snapshot discovered by this instance, or raises a sanitized `LiteratureError`. It never fetches a supplied URL.
- Create a new instance for each investigation. Previously undiscovered IDs, URLs, and missing abstract text are explicit errors.

Each search makes at most one request to each provider. A partial result includes the failed provider and a machine-readable reason, even when the successful provider returns zero matches. Failure of both providers raises `LiteratureError("all_providers_unavailable")` with typed `provider_failures`; an access failure is not an empty literature search. The controller must retain these diagnostics when describing search coverage.

## Retrieval and provenance

The only network destinations are fixed HTTPS endpoints:

- OpenAlex: `https://api.openalex.org/works`
- arXiv: `https://export.arxiv.org/api/query`

Queries become encoded request parameters. No redirects or arbitrary document URL requests are followed. Returned URLs and IDs must match supported identity patterns. Query and retrieval work are bounded: 500-character queries, at most 5 results per provider (10 combined), 512,000 response bytes per provider, a 15-second wall-clock request limit, and 200 discovered identities per tool instance. No automatic retries run here.

OpenAlex's contiguous abstract inverted index is reconstructed with validated token positions. arXiv Atom summaries are parsed from bounded, strictly UTF-8 XML, with entity/doctype declarations and NUL bytes rejected. Whitespace normalization is explicit in the extraction method. Unsupported encodings or malformed responses fail rather than producing invented source text.

Every readable result is **abstract-only**: `content_kind="abstract"`. Its extraction method, original retrieval time, canonical identity/URL, and content hash accompany the text. An abstract is not evidence that full text was read. Full-text retrieval and live semantic evidence assessment remain incomplete. Exact snapshot persistence is handled by the evidence store.

Provider-declared `SourceBibliography` now preserves author names/validated ORCIDs, publication year/date, venue, DOI, arXiv base/version, OpenAlex work ID, and a declared license when available. Missing values stay unknown. Author lists retain at most 100 names and explicitly mark truncation. Bibliography is also capped at 60,000 UTF-8 bytes before persistence. A declared license is metadata, not an independent grant of redistribution rights. Absent bibliography is omitted from source serialization so historical source/artifact hashes retain their original shape; content hashes still cover exact text only.

Pure `parse_openalex_records(payload, retrieved_at, limit)` and `parse_arxiv_records(...)` helpers allow bounded feed pages of up to 100 records while preserving the same 512,000-byte/encoding/provenance boundaries. They make no requests and do not change per-run discovery state. Search itself remains capped at 5 results per provider.

The first readable snapshot of a source is frozen within an investigation. Repeated searches cannot silently replace text already given to the research model. An initially metadata-only source may gain its first readable snapshot. `read` returns the cached snapshot without another request; later investigations can retrieve newer versions.

## Identity and limitations

When a validated DOI is present, both providers use the same normalized DOI identity and URL so one paper is not counted twice merely because it appears in two provider responses. Otherwise use validated OpenAlex work IDs or versionless arXiv IDs/URLs. arXiv version variants therefore do not count as separate papers in this adapter.

`literature.identity` additionally exposes `paper_aliases`, `canonical_paper_id`, `same_paper`, and `distinct_paper_count`. Independence checks retain normalized exact URL aliases and provider-declared DOI/arXiv/OpenAlex aliases, with transitive overlap across selected references and all bounded observed context. A read but uncited alias connector still prevents false independent corroboration. An abstract, a full-text snapshot, and arXiv revisions of one identified paper do not become independent corroboration. Preferred display IDs alone are insufficient; matching titles never merge papers. Source IDs/URLs and previously retrieved snapshots are not rewritten.

This is bounded identity resolution, not a guarantee that all duplicate papers are recognized. Missing or inconsistent provider identifiers, distinct preprint/publication DOIs, metadata errors, and scientific equivalence need further resolution. A canonical source identifier and an exact quote do not establish scientific truth or novelty.

## Authentication and error handling

OpenAlex authentication is **not implemented** until a supported transport that avoids credentials in request URLs/logs is verified. Supplying the optional `SecretStr` key argument raises an explicit configuration error before any request. Never put a key directly in a query, source ID, or URL. Provider access requirements remain unverified and may prevent unauthenticated OpenAlex requests; partial arXiv availability remains visible through typed diagnostics.

Timeouts, access denials, rate limits, malformed/oversized responses, and unavailable sources use sanitized machine codes. Provider response bodies and transport error details are not copied into tool errors. All source text remains untrusted research data, never tool instructions.

## Verification

From the repository root:

```sh
uv --project backend run pytest backend/tests/test_literature.py backend/tests/test_literature_metadata.py
uv --project backend run ruff check backend/src/polymath/literature backend/tests/test_literature.py
```

The fixtures exercise canonical identities, abstract provenance, DOI deduplication, first-snapshot behavior, discovery-before-read boundaries, fixed destinations, hostile IDs, malformed metadata/XML, UTF-16/32 entity attempts, response limits, access errors, rate limits, transport failures, and partial provider availability. Passing these tests does not establish live connectivity or provider API compatibility.
