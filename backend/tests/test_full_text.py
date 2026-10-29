"""Synthetic full-text fixtures: no native arXiv/model requests or external writes."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError
from test_literature import atom_entry, atom_feed, openalex_work, route

from polymath.full_text_contracts import ExtractedFullText, FullTextHeading, FullTextIndex
from polymath.literature import OpenLiteratureTools
from polymath.literature.errors import LiteratureError
from polymath.literature.full_text import (
    MAX_HTML_BYTES,
    ArxivHtmlClient,
    FullTextError,
    extract_arxiv_html,
)
from polymath.literature.identity import distinct_paper_count
from polymath.research.meta import ModelReply
from polymath.research.models import (
    ArxivFullTextLocator,
    FullTextAvailability,
    ReadFullTextWindowAction,
    SearchResults,
    SourceBibliography,
    SourceCoverage,
    SourceDocument,
    SourceReference,
)

NOW = datetime(2026, 10, 28, tzinfo=UTC)
SNAPSHOT_ID = "12345678-1234-1234-1234-123456789abc"
LOCATOR = ArxivFullTextLocator(arxiv_id="2401.01234", arxiv_version=2)


def full_text_fixture_transport(requests=None):
    """Reusable real-parser transport for browser/worker journeys; never native HTTP."""
    requests = requests if requests is not None else []

    def handler(request):
        requests.append(request)
        if request.url.host == "api.openalex.org":
            return httpx.Response(
                200,
                json={
                    "results": [
                        openalex_work(doi="10.1234/synthetic"),
                        openalex_work(
                            id="https://openalex.org/W654321",
                            doi="10.1234/biology",
                            display_name="Synthetic biological pathway study",
                            abstract_inverted_index={
                                "Independent": [0],
                                "synthetic": [1],
                                "pathways": [2],
                                "provide": [3],
                                "redundant": [4],
                                "responses.": [5],
                            },
                        ),
                    ]
                },
            )
        if request.url.host == "export.arxiv.org":
            return httpx.Response(200, text=atom_feed(atom_entry(doi="10.1234/synthetic")))
        assert str(request.url) == "https://arxiv.org/html/2401.01234v2"
        return httpx.Response(
            200,
            content=html(
                "<h1>Background</h1><p>Synthetic message encodings retain redundant structure "
                "under a specified noise model.</p><h2>Further findings</h2><p>Redundancy "
                'has a measurable representation cost <math alttext="n+1"><mi>n</mi></math> '
                "in this synthetic example; equivalence to biology is unverified.</p>"
            ),
            headers={"content-type": "text/html; charset=utf-8"},
        )

    return httpx.MockTransport(handler)


class FullTextFixtureModel:
    """Adapts to real index/text observations, selecting two windows plus another paper."""

    def __init__(self):
        self.calls = 0

    async def complete(self, messages, *, max_output_tokens):
        self.calls += 1
        observations = []
        for message in messages:
            if message["role"] != "user":
                continue
            try:
                value = json.loads(message["content"])
            except ValueError:
                continue
            if isinstance(value, dict) and isinstance(value.get("observation"), dict):
                observations.append(value["observation"])
        searches = [value for value in observations if value.get("kind") == "search_results"]
        purposes = {value["purpose"] for value in searches}
        if "discovery" not in purposes:
            action = {
                "kind": "search_literature",
                "query": "synthetic redundancy mechanism",
                "purpose": "discovery",
            }
        else:
            discovery = next(value for value in searches if value["purpose"] == "discovery")
            candidate = discovery["full_text"][0]["source_id"]
            indexes = [value for value in observations if value.get("kind") == "full_text_index"]
            documents = {
                value["source_id"]: value
                for value in observations
                if value.get("kind") == "source_text"
            }
            if not indexes:
                action = {"kind": "open_full_text", "source_id": candidate}
            else:
                index = indexes[0]
                split = index["headings"][1]["start"]
                ranges = ((0, split), (split, index["total_chars"]))
                read_ranges = {
                    (value["coverage"]["start"], value["coverage"]["end"])
                    for value in documents.values()
                    if value.get("coverage")
                }
                unread = next((span for span in ranges if span not in read_ranges), None)
                independent = next(
                    source for source in discovery["sources"] if source["source_id"] != candidate
                )
                if unread is not None:
                    action = {
                        "kind": "read_full_text_window",
                        "snapshot_id": index["snapshot_id"],
                        "start": unread[0],
                        "end": unread[1],
                    }
                elif independent["source_id"] not in documents:
                    action = {"kind": "read_source", "source_id": independent["source_id"]}
                elif "counterevidence" not in purposes:
                    action = {
                        "kind": "search_literature",
                        "query": "synthetic redundancy counterevidence",
                        "purpose": "counterevidence",
                    }
                elif "prior_work" not in purposes:
                    action = {
                        "kind": "search_literature",
                        "query": "synthetic redundancy prior analogy",
                        "purpose": "prior_work",
                    }
                else:
                    action = {
                        "kind": "conclude",
                        "conclusion": {
                            "outcome": "bridge",
                            "title": "Synthetic redundancy analogy from saved HTML excerpts",
                            "field_left": "Information theory",
                            "field_right": "Biology",
                            "explanation": "Saved excerpts suggest a conditional analogy.",
                            "mappings": [
                                {
                                    "left": "Redundant encodings",
                                    "right": "Redundant pathways",
                                    "assumption": "Noise models must align.",
                                }
                            ],
                            "open_question": "Does the analogy survive a realistic noise model?",
                            "strongest_objection": "Different mechanisms may invalidate it.",
                            "objection_verdict": "This analogy remains unverified.",
                            "novelty_limitations": (
                                "Fixtures and excerpts cannot establish novelty, proof, "
                                "or whole-paper coverage."
                            ),
                            "evidence": [
                                {
                                    "source_id": value["source_id"],
                                    "side": "left" if value.get("coverage") else "right",
                                    "quote": value["text"].strip(),
                                    "supports": "Conditional redundancy analogy.",
                                }
                                for value in documents.values()
                            ],
                        },
                    }
        return ModelReply(json.dumps(action), input_tokens=10, output_tokens=15)


def html(
    body="<h1>Findings</h1><p>Exact synthetic evidence appears here.</p>",
    *,
    version="2401.01234v2",
    head=None,
):
    if head is None:
        head = f'<meta name="citation_arxiv_id" content="{version}">'
    return (
        "<!DOCTYPE html><html><head>" + head + "</head><body>"
        '<nav>Nonarticle navigation</nav><article class="ltx_document">'
        + body
        + "</article><footer>Site footer</footer></body></html>"
    ).encode()


def extraction(body=None):
    return extract_arxiv_html(html() if body is None else html(body), LOCATOR, NOW)


def coverage(**overrides):
    return SourceCoverage(
        **(
            {
                "snapshot_id": SNAPSHOT_ID,
                "snapshot_sha256": "a" * 64,
                "arxiv_id": "2401.01234",
                "arxiv_version": 2,
                "start": 0,
                "end": 15,
                "total": 30,
            }
            | overrides
        )
    )


def window(**overrides):
    span = coverage()
    data = {
        "source_id": f"arxiv-html:{span.snapshot_id}:{span.start}:{span.end}",
        "url": "https://arxiv.org/html/2401.01234v2",
        "title": "Synthetic paper",
        "bibliography": SourceBibliography(
            arxiv_id="2401.01234", arxiv_version=2, doi="10.1234/synthetic"
        ),
        "coverage": span,
        "text": "Exact evidence.",
        "content_kind": "full_text",
        "retrieved_at": NOW,
        "extraction_method": "arxiv_html_latexml/v1",
    }
    return SourceDocument(**(data | overrides))


def test_snapshot_contains_exact_text_headings_and_distinct_raw_and_extracted_hashes():
    payload = html("<h1>  Findings 😀 </h1><p>A <em>synthetic</em>\n claim.</p>")
    result = extract_arxiv_html(payload, LOCATOR, NOW)
    assert result.text == "Findings 😀\nA synthetic claim."
    assert result.headings[0].title == "Findings 😀"
    assert result.text[result.headings[0].start : result.headings[0].end] == "Findings 😀"
    assert result.raw_html_sha256 == hashlib.sha256(payload).hexdigest()
    assert result.snapshot_sha256 == hashlib.sha256(result.text.encode()).hexdigest()
    assert result.total_chars == len(result.text)
    assert result.retrieved_at == NOW
    assert "navigation" not in result.text and "footer" not in result.text


def test_math_is_emitted_once_from_alttext_or_tex_annotation_with_adjacent_prose():
    result = extraction(
        '<p>Before <math alttext="n+1"><mi>n</mi><mo>+</mo><mn>1</mn>'
        '<annotation encoding="application/x-tex">duplicate latex</annotation></math> after.</p>'
        '<p>Next <math><semantics><mi>x</mi><annotation encoding="application/x-tex">'
        r"x\InvisibleTimes y</annotation></semantics></math> done.</p>"
    )
    assert (
        result.text
        == r"Before [math: n+1] after." + "\n" + r"Next [math: x\InvisibleTimes y] done."
    )
    assert "duplicate" not in result.text
    assert "semantics are unverified" in " ".join(result.limitations)


def test_unsupported_math_has_explicit_marker_and_missing_scope():
    result = extraction("<p>Before <math><mi>x</mi></math> after.</p>")
    assert result.text == "Before [unavailable equation] after."
    assert any("1 equations" in note for note in result.limitations)


def test_assets_scripts_and_embedded_instructions_are_not_followed_or_extracted():
    result = extraction(
        "<p>Kept <span>prose.</span></p><script>steal()</script><style>hidden</style>"
        '<iframe src="http://127.0.0.1/private">hidden frame</iframe>'
        '<svg><text>hidden SVG</text></svg><img src="https://evil.test/pixel" alt="not read">'
        '<a href="http://127.0.0.1/private">A visible link label.</a>'
    )
    assert result.text == "Kept prose.\nA visible link label."
    assert any("figures" in item for item in result.limitations)


def test_table_cells_never_merge_into_an_invented_number():
    result = extraction(
        "<table><tr><td>1</td><td>2</td></tr><tr><th>34</th><td>5</td></tr></table>"
    )
    assert result.text == "1\n2\n34\n5"
    assert "12" not in result.text
    assert any("Table layout" in note for note in result.limitations)


def test_heading_index_is_bounded_without_truncating_article_text():
    result = extraction(
        "".join(f"<h2>Heading {index}</h2><p>Paragraph {index}.</p>" for index in range(82))
    )
    assert len(result.headings) == 80
    assert result.headings_truncated
    assert "Paragraph81" not in result.text
    assert "Paragraph 81." in result.text
    assert any("first80" in note for note in result.limitations)


def test_long_heading_preserves_text_and_indexes_exact_prefix():
    title = "h" * 201
    result = extraction(f"<h2>{title}</h2><p>After heading.</p>")
    assert result.headings[0].title == title[:200]
    assert title in result.text
    assert any("exact prefixes" in note for note in result.limitations)


def test_exact_250000_character_body_is_accepted_without_counting_terminal_separator():
    result = extraction("x" * 250000)
    assert len(result.text) == 250000


def test_invisible_times_and_non_bmp_math_are_plain_unicode_with_exact_offsets():
    result = extraction('<h2>😀 Findings</h2><p><math alttext="x&InvisibleTimes;😀"></math></p>')
    assert result.text == "😀 Findings\n[math: x\u2062😀]"
    assert result.headings[0].end == len("😀 Findings")


@pytest.mark.parametrize(
    "prefix",
    [
        '<html><template><head><meta name="citation_arxiv_id" '
        'content="2401.01234v2"></head></template>',
        '<html><body><head><meta name="citation_arxiv_id" content="2401.01234v2"></head>',
    ],
)
def test_nested_or_template_heads_cannot_certify_response_identity(prefix):
    payload = (
        prefix + '<article class="ltx_document"><p>Unverified.</p></article></body></html>'
    ).encode()
    with pytest.raises(FullTextError, match="full_text_invalid_html"):
        extract_arxiv_html(payload, LOCATOR, NOW)


def test_node_budget_is_enforced_during_parsing(monkeypatch):
    from polymath.literature import full_text

    monkeypatch.setattr(full_text, "MAX_HTML_NODES", 10)
    with pytest.raises(FullTextError, match="full_text_structure_limit"):
        extraction("<p>Many nodes.</p>" * 20)


@pytest.mark.parametrize(
    "head",
    [
        '<link rel="canonical" href="https://arxiv.org/html/2401.01234v2">',
        '<meta name="citation_arxiv_id" content="arXiv:2401.01234v2">',
    ],
)
def test_exact_document_head_identity_is_accepted(head):
    assert extract_arxiv_html(html(head=head), LOCATOR, NOW).text


@pytest.mark.parametrize(
    "payload,code",
    [
        (html(version="2401.01234v1"), "full_text_version_mismatch"),
        (html(version="2401.99999v2"), "full_text_version_mismatch"),
        (html(version="2401.01234"), "full_text_version_mismatch"),
        (html(head=""), "full_text_version_unverified"),
        (
            html(
                head='<meta name="citation_arxiv_id" content="2401.01234v2"><link rel="canonical" href="https://evil.test/">'
            ),
            "full_text_version_mismatch",
        ),
        (
            html('<meta name="citation_arxiv_id" content="2401.01234v2"><p>Prose.</p>', head=""),
            "full_text_version_unverified",
        ),
        (
            html(
                head='<template><meta name="citation_arxiv_id" content="2401.01234v2"></template>'
            ),
            "full_text_version_unverified",
        ),
        (
            b"<html><head><meta name='citation_arxiv_id' content='2401.01234v2'></head>"
            b"<p>Not an article.</p></html>",
            "full_text_invalid_html",
        ),
        (html(b"bad".decode()).replace(b"bad", b"bad\x00"), "full_text_invalid_html"),
        (b"\xff\xfe", "full_text_invalid_html"),
        (html("<![PRIVATE_REJECT[>"), "full_text_invalid_html"),
        (
            html().replace(b"<!DOCTYPE html>", b'<!DOCTYPE html [<!ENTITY bad "secret">]>'),
            "full_text_invalid_html",
        ),
    ],
)
def test_wrong_missing_or_injected_html_identity_is_rejected(payload, code):
    with pytest.raises(FullTextError, match=code):
        extract_arxiv_html(payload, LOCATOR, NOW)


@pytest.mark.parametrize(
    "body,code",
    [
        ("x" * 250001, "full_text_text_too_large"),
        ("<div>" * 130 + "text" + "</div>" * 130, "full_text_structure_limit"),
        ('<math alttext="' + "x" * 4001 + '"></math>', "full_text_math_limit"),
        ('<math><math alttext="x"></math></math>', "full_text_invalid_html"),
        ('<p id="a" id="b">Ambiguous attributes.</p>', "full_text_invalid_html"),
    ],
)
def test_extraction_is_incrementally_bounded(body, code):
    with pytest.raises(FullTextError, match=code):
        extraction(body)


def test_snapshot_and_index_contracts_reject_fabricated_heading_spans():
    result = extraction()
    bad = result.model_dump() | {
        "headings": (FullTextHeading(title="Unrelated", level=1, start=0, end=9),)
    }
    with pytest.raises(ValidationError, match="exact snapshot"):
        ExtractedFullText(**bad)
    index = FullTextIndex(
        **{key: value for key, value in result.model_dump().items() if key != "text"},
        snapshot_id=SNAPSHOT_ID,
        source=SourceReference(
            source_id="arxiv:2401.01234", title="Source", url="https://arxiv.org/abs/2401.01234"
        ),
        snapshot_sha256=result.snapshot_sha256,
        total_chars=result.total_chars,
    )
    assert "text" not in index.model_dump(mode="json")


def test_absent_optional_fields_preserve_old_nested_source_and_search_serialization():
    source = SourceDocument(
        source_id="old:source",
        title="Original",
        url="https://example.test/paper",
        text="Saved old text.",
        content_kind="abstract",
        retrieved_at=NOW,
        extraction_method="old/v1",
    )
    expected = {
        "source_id": "old:source",
        "title": "Original",
        "url": "https://example.test/paper",
        "text": "Saved old text.",
        "content_kind": "abstract",
        "retrieved_at": "2026-10-28T00:00:00Z",
        "extraction_method": "old/v1",
    }
    assert source.model_dump(mode="json") == expected
    assert json.loads(SearchResults(sources=(source,)).model_dump_json()) == {
        "sources": [
            {"source_id": "old:source", "title": "Original", "url": "https://example.test/paper"}
        ],
        "provider_failures": [],
    }


def test_exact_window_metadata_retains_alias_identity_without_counting_as_a_new_paper():
    first = window()
    second_span = coverage(start=15, end=30)
    second = window(source_id=f"arxiv-html:{SNAPSHOT_ID}:15:30", coverage=second_span)
    abstract = SourceReference(
        source_id="doi:10.1234/synthetic",
        title="Frozen old title",
        url="https://doi.org/10.1234/synthetic",
    )
    assert distinct_paper_count((abstract, first, second)) == 1
    assert first.model_dump(mode="json")["coverage"]["snapshot_sha256"] == "a" * 64


@pytest.mark.parametrize(
    "overrides",
    [
        {"content_kind": "abstract"},
        {"text": "Short"},
        {"source_id": "arxiv:2401.01234"},
        {"url": "https://arxiv.org/html/2401.01234v1"},
        {"url": "https://arxiv.org/html/2401.01234v2#range"},
        {"bibliography": None},
        {"bibliography": SourceBibliography(arxiv_id="2401.01234", arxiv_version=1)},
    ],
)
def test_windows_require_matching_exact_locator_body_and_bibliography(overrides):
    with pytest.raises(ValidationError):
        window(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"start": 15, "end": 15},
        {"end": 31},
        {"end": 8001, "total": 9000},
        {"total": 250001},
        {"arxiv_id": "2401.01234v2"},
        {"arxiv_version": None},
    ],
)
def test_coverage_ranges_are_strict_and_bounded(overrides):
    with pytest.raises(ValidationError):
        coverage(**overrides)


def test_model_window_actions_reject_oversized_or_reversed_ranges():
    for start, end in ((0, 8001), (9, 8), (1, 1)):
        with pytest.raises(ValidationError):
            ReadFullTextWindowAction(
                kind="read_full_text_window", snapshot_id=SNAPSHOT_ID, start=start, end=end
            )


@pytest.mark.asyncio
async def test_client_constructs_only_fixed_versioned_html_get_without_credentials_or_queries():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200, content=html(), headers={"content-type": "text/html; charset=utf-8"}
        )

    result = await ArxivHtmlClient(transport=httpx.MockTransport(handler)).fetch(LOCATOR)
    assert result.text
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://arxiv.org/html/2401.01234v2"
    assert request.method == "GET" and "authorization" not in request.headers
    assert request.headers["accept-encoding"] == "identity"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code",
    [
        (301, "invalid_response"),
        (302, "invalid_response"),
        (401, "access_denied"),
        (403, "access_denied"),
        (404, "source_unavailable"),
        (410, "source_unavailable"),
        (429, "rate_limited"),
        (503, "invalid_response"),
    ],
)
async def test_http_denials_and_redirects_are_sanitized_without_retries(status, code):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            status, text="fixture-secret-error", headers={"location": "http://127.0.0.1/private"}
        )

    with pytest.raises(FullTextError, match="full_text_" + code) as caught:
        await ArxivHtmlClient(transport=httpx.MockTransport(handler)).fetch(LOCATOR)
    assert "secret" not in str(caught.value) and "127.0.0.1" not in str(caught.value)
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {"content-type": "application/pdf"},
        {"content-type": "text/html", "content-encoding": "gzip"},
        {"content-type": "text/html", "content-length": "-1"},
        {"content-type": "text/html", "content-length": "nonsense"},
    ],
)
async def test_nonhtml_encoded_and_malformed_length_responses_fail_closed(headers):
    with pytest.raises(FullTextError, match="full_text_invalid_response"):
        await ArxivHtmlClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, content=html(), headers=headers)
            )
        ).fetch(LOCATOR)


class OversizedChunks(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"x" * (MAX_HTML_BYTES // 2)
        yield b"x" * (MAX_HTML_BYTES // 2 + 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("declared", [False, True])
async def test_stream_and_declared_byte_limits_precede_parsing(declared):
    headers = {"content-type": "text/html"}
    if declared:
        headers["content-length"] = str(MAX_HTML_BYTES + 1)
    response = httpx.Response(200, stream=OversizedChunks(), headers=headers)
    with pytest.raises(FullTextError, match="full_text_response_too_large"):
        await ArxivHtmlClient(transport=httpx.MockTransport(lambda r: response)).fetch(LOCATOR)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,code",
    [(httpx.ConnectError("secret"), "transport_error"), (httpx.ReadTimeout("secret"), "timeout")],
)
async def test_transport_errors_are_static(error, code):
    def handler(request):
        raise error

    with pytest.raises(FullTextError, match="full_text_" + code):
        await ArxivHtmlClient(transport=httpx.MockTransport(handler)).fetch(LOCATOR)


@pytest.mark.asyncio
async def test_wall_timeout_is_bounded_and_cancellation_is_not_swallowed(monkeypatch):
    from polymath.literature import full_text

    async def slow(request):
        await asyncio.sleep(1)

    monkeypatch.setattr(full_text, "FULL_TEXT_TIMEOUT_SECONDS", 0.001)
    with pytest.raises(FullTextError, match="full_text_timeout"):
        await ArxivHtmlClient(transport=httpx.MockTransport(slow)).fetch(LOCATOR)

    async def cancelled(request):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await ArxivHtmlClient(transport=httpx.MockTransport(cancelled)).fetch(LOCATOR)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entry,accepted",
    [
        (atom_entry(), True),
        (atom_entry(source_id="http://arxiv.org/abs/2401.99999v2"), False),
        (atom_entry(source_id="http://arxiv.org/abs/2401.01234"), False),
        (None, False),
    ],
)
async def test_missing_version_resolution_uses_only_fixed_matching_metadata(entry, accepted):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text=atom_feed(*([entry] if entry else [])))

    client = ArxivHtmlClient(transport=httpx.MockTransport(handler))
    if accepted:
        assert await client.resolve(ArxivFullTextLocator(arxiv_id="2401.01234")) == LOCATOR
    else:
        with pytest.raises(FullTextError, match="full_text_version_unavailable"):
            await client.resolve(ArxivFullTextLocator(arxiv_id="2401.01234"))
    assert len(requests) == 1
    assert requests[0].url.host == "export.arxiv.org"
    assert dict(requests[0].url.params) == {"id_list": "2401.01234", "max_results": "1"}


@pytest.mark.asyncio
async def test_versioned_locator_needs_no_resolution_request_and_unversioned_html_is_refused():
    requests = []
    client = ArxivHtmlClient(transport=httpx.MockTransport(lambda r: requests.append(r)))
    assert await client.resolve(LOCATOR) == LOCATOR
    with pytest.raises(FullTextError, match="full_text_version_required"):
        await client.fetch(ArxivFullTextLocator(arxiv_id="2401.01234"))
    assert requests == []


@pytest.mark.asyncio
async def test_doi_dedup_keeps_frozen_openalex_abstract_but_preserves_arxiv_fulltext_locator():
    transport, requests = route(
        works=[openalex_work(doi="10.1234/synthetic")],
        entries=[atom_entry(doi="10.1234/synthetic")],
    )
    tools = OpenLiteratureTools(transport=transport)
    results = await tools.search("synthetic", "discovery")
    source = results.sources[0]
    assert len(results.sources) == 1 and source.bibliography.arxiv_id is None
    assert results.full_text[0].locator == LOCATOR
    assert (await tools.read(source.source_id)).text == "Shared structures appear"
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_saved_discovery_restores_exact_locator_without_refresh_or_abstract_mutation():
    transport, _ = route(works=[], entries=[atom_entry()])
    original = OpenLiteratureTools(transport=transport)
    result = await original.search("synthetic", "discovery")
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "arxiv.org"
        return httpx.Response(200, content=html(), headers={"content-type": "text/html"})

    restored = OpenLiteratureTools(transport=httpx.MockTransport(handler))
    restored.restore_discovery(SearchResults.model_validate_json(result.model_dump_json()))
    source = result.sources[0]
    assert restored.get_discovered_reference(source.source_id) == source
    locator = await restored.resolve_full_text_locator(source.source_id)
    assert requests == []
    assert (await restored.fetch_full_text(source.source_id, locator=locator)).arxiv_version == 2
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_authoritative_restore_overlays_refreshed_locator_but_keeps_exact_matching_abstract():
    original_transport, _ = route(
        works=[openalex_work(doi="10.1234/synthetic")],
        entries=[atom_entry(doi="10.1234/synthetic")],
    )
    original = OpenLiteratureTools(transport=original_transport)
    saved = await original.search("synthetic", "discovery")
    fresh_transport, requests = route(
        works=[openalex_work(doi="10.1234/synthetic")],
        entries=[
            atom_entry(doi="10.1234/synthetic", source_id="http://arxiv.org/abs/2401.01234v3")
        ],
    )
    fresh = OpenLiteratureTools(transport=fresh_transport)
    refreshed = await fresh.search("synthetic", "discovery")
    assert refreshed.full_text[0].locator.arxiv_version == 3
    fresh.restore_discovery(saved)
    assert fresh.get_full_text_availability(saved.sources[0].source_id) == saved.full_text[0]
    assert await fresh.resolve_full_text_locator(saved.sources[0].source_id) == LOCATOR
    assert (await fresh.read(saved.sources[0].source_id)).text == "Shared structures appear"
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_authoritative_restore_discards_refreshed_body_with_different_reference_metadata():
    transport, _ = route(works=[openalex_work()], entries=[])
    saved = await OpenLiteratureTools(transport=transport).search("synthetic", "discovery")
    fresh_transport, requests = route(
        works=[openalex_work(display_name="Changed upstream title")], entries=[]
    )
    fresh = OpenLiteratureTools(transport=fresh_transport)
    await fresh.search("synthetic", "discovery")
    fresh.restore_discovery(saved)
    assert fresh.get_discovered_reference(saved.sources[0].source_id) == saved.sources[0]
    with pytest.raises(LiteratureError, match="source_text_unavailable"):
        await fresh.read(saved.sources[0].source_id)
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_unknown_ambiguous_and_different_locator_reads_make_no_html_requests():
    transport, requests = route(
        works=[],
        entries=[
            atom_entry(doi="10.1234/synthetic"),
            atom_entry(source_id="http://arxiv.org/abs/2401.99999v2", doi="10.1234/synthetic"),
        ],
    )
    tools = OpenLiteratureTools(transport=transport)
    with pytest.raises(LiteratureError, match="source_not_discovered"):
        await tools.resolve_full_text_locator("arxiv:2401.01234")
    result = await tools.search("synthetic", "discovery")
    assert result.full_text[0].status == "ambiguous"
    with pytest.raises(LiteratureError, match="full_text_locator_ambiguous"):
        await tools.fetch_full_text(result.sources[0].source_id, locator=LOCATOR)
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_fulltext_fetch_cannot_select_a_different_version_from_a_frozen_candidate():
    transport, requests = route(works=[], entries=[atom_entry()])
    tools = OpenLiteratureTools(transport=transport)
    result = await tools.search("synthetic", "discovery")
    with pytest.raises(LiteratureError, match="full_text_locator_mismatch"):
        await tools.fetch_full_text(
            result.sources[0].source_id,
            locator=ArxivFullTextLocator(arxiv_id="2401.01234", arxiv_version=3),
        )
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_later_conflicting_version_cannot_silently_replace_the_first_locator_or_abstract():
    searches = 0

    def handler(request):
        nonlocal searches
        if request.url.host == "api.openalex.org":
            return httpx.Response(200, json={"results": []})
        searches += 1
        entry = atom_entry(source_id=f"http://arxiv.org/abs/2401.01234v{searches + 1}")
        return httpx.Response(200, text=atom_feed(entry))

    tools = OpenLiteratureTools(transport=httpx.MockTransport(handler))
    first = await tools.search("synthetic", "discovery")
    document = await tools.read(first.sources[0].source_id)
    second = await tools.search("synthetic", "counterevidence")
    assert first.full_text[0].locator == LOCATOR
    assert second.full_text[0].status == "ambiguous"
    assert second.sources == first.sources
    assert await tools.read(first.sources[0].source_id) == document
    with pytest.raises(LiteratureError, match="full_text_locator_ambiguous"):
        await tools.resolve_full_text_locator(first.sources[0].source_id)


@pytest.mark.parametrize(
    "identifier",
    [
        "https://arxiv.org/html/2401.01234v2",
        "2401.01234?redirect=evil",
        "../2401.01234",
        "2401.01234v2",
        "2400.01234",
        "2413.01234",
        "user@arxiv.org/2401.01234",
    ],
)
def test_arxiv_locators_never_accept_arbitrary_urls_or_paths(identifier):
    with pytest.raises(ValidationError):
        ArxivFullTextLocator(arxiv_id=identifier, arxiv_version=2)


def test_availability_is_not_an_invented_html_success_and_cannot_reference_unknown_sources():
    source = SourceReference(
        source_id="arxiv:2401.01234", title="Paper", url="https://arxiv.org/abs/2401.01234"
    )
    candidate = FullTextAvailability(
        source_id=source.source_id, status="candidate", locator=LOCATOR
    )
    assert (
        SearchResults(sources=(source,), full_text=(candidate,)).full_text[0].status == "candidate"
    )
    with pytest.raises(ValidationError):
        SearchResults(sources=(), full_text=(candidate,))
    with pytest.raises(ValidationError):
        FullTextAvailability(source_id=source.source_id, status="ambiguous", locator=LOCATOR)


@pytest.mark.asyncio
async def test_reusable_fixture_adaptively_reads_two_windows_and_an_independent_paper():
    from polymath.research.controller import ResearchController

    class FixtureTools(OpenLiteratureTools):
        async def open_full_text(self, source_id):
            self.reference = self.get_discovered_reference(source_id)
            locator = await self.resolve_full_text_locator(source_id)
            self.extracted = await self.fetch_full_text(source_id, locator=locator)
            self.index = FullTextIndex(
                **{
                    key: value
                    for key, value in self.extracted.model_dump().items()
                    if key != "text"
                },
                snapshot_id=SNAPSHOT_ID,
                source=self.reference,
                snapshot_sha256=self.extracted.snapshot_sha256,
                total_chars=self.extracted.total_chars,
            )
            return self.index

        async def read_full_text_window(self, snapshot_id, start, end):
            assert snapshot_id == self.index.snapshot_id
            return SourceDocument(
                source_id=f"arxiv-html:{snapshot_id}:{start}:{end}",
                title=self.reference.title,
                url=self.index.url,
                bibliography=SourceBibliography(
                    **(
                        self.reference.bibliography.model_dump()
                        | {
                            "arxiv_id": self.index.arxiv_id,
                            "arxiv_version": self.index.arxiv_version,
                        }
                    )
                ),
                coverage=SourceCoverage(
                    snapshot_id=snapshot_id,
                    snapshot_sha256=self.index.snapshot_sha256,
                    arxiv_id=self.index.arxiv_id,
                    arxiv_version=self.index.arxiv_version,
                    start=start,
                    end=end,
                    total=self.index.total_chars,
                ),
                text=self.extracted.text[start:end],
                content_kind="full_text",
                retrieved_at=self.index.retrieved_at,
                extraction_method=self.index.extractor_version,
            )

    requests = []
    model = FullTextFixtureModel()
    result = await ResearchController(
        model, FixtureTools(transport=full_text_fixture_transport(requests))
    ).investigate("Explore synthetic redundancy across information theory and biology.")
    assert result.status == "bridge"
    assert len(result.sources) == len(result.quotes) == 3
    assert distinct_paper_count(result.sources) == 2
    assert model.calls == 8
    assert sum(request.url.host == "arxiv.org" for request in requests) == 1
    assert len([source for source in result.sources if source.coverage is not None]) == 2


def live_shaped_html(body="<h1>Findings</h1><p>Exact synthetic evidence appears here.</p>"):
    """Mirrors live arXiv pages: no head identity tags and a chrome duplicate attribute."""
    return (
        '<!DOCTYPE html><html lang="en"><head><title>Synthetic</title></head><body>'
        '<header><a class="header-button" title="Report" href="#" title="Report an issue">'
        'Report</a></header><article class="ltx_document">' + body + "</article></body></html>"
    ).encode()


def live_handler(link_headers, *, body=None):
    def handler(request):
        headers = [("content-type", "text/html; charset=utf-8")]
        headers += [("link", value) for value in link_headers]
        return httpx.Response(200, content=body or live_shaped_html(), headers=headers)

    return handler


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "link",
    [
        "<https://arxiv.org/html/2401.01234>; rel='canonical'",
        '<https://arxiv.org/html/2401.01234v2>; rel="canonical"',
    ],
)
async def test_live_shaped_page_is_identified_by_exact_request_and_canonical_header(link):
    client = ArxivHtmlClient(transport=httpx.MockTransport(live_handler([link])))
    result = await client.fetch(LOCATOR)
    assert "Exact synthetic evidence appears here." in result.text
    assert "Report" not in result.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "links",
    [
        [],
        ["<https://arxiv.org/html/2401.99999>; rel='canonical'"],
        ["<https://arxiv.org/html/2401.01234v1>; rel='canonical'"],
        ["<https://evil.test/html/2401.01234>; rel='canonical'"],
        ["<https://arxiv.org/html/2401.01234>; rel='alternate'"],
        [
            "<https://arxiv.org/html/2401.01234>; rel='canonical'",
            "<https://arxiv.org/html/2401.01234>; rel='canonical'",
        ],
    ],
)
async def test_missing_or_mismatched_canonical_header_stays_unverified(links):
    client = ArxivHtmlClient(transport=httpx.MockTransport(live_handler(links)))
    with pytest.raises(FullTextError, match="full_text_version_unverified"):
        await client.fetch(LOCATOR)


@pytest.mark.asyncio
async def test_canonical_header_cannot_override_conflicting_document_identity():
    body = html(version="2401.01234v1")
    link = "<https://arxiv.org/html/2401.01234>; rel='canonical'"
    client = ArxivHtmlClient(transport=httpx.MockTransport(live_handler([link], body=body)))
    with pytest.raises(FullTextError, match="full_text_version_mismatch"):
        await client.fetch(LOCATOR)


def test_duplicate_attributes_inside_the_article_are_still_rejected():
    body = '<p class="a" class="b">Ambiguous.</p>'
    with pytest.raises(FullTextError, match="full_text_invalid_html"):
        extract_arxiv_html(live_shaped_html(body), LOCATOR, NOW, transport_identified=True)


@pytest.mark.parametrize(
    "head",
    [
        '<meta name="citation_arxiv_id" name="x" content="2401.01234v2">',
        '<link rel="canonical" rel="x" href="https://arxiv.org/html/2401.01234v2">',
    ],
)
def test_duplicate_attributes_on_identity_tags_are_still_rejected(head):
    with pytest.raises(FullTextError, match="full_text_invalid_html"):
        extract_arxiv_html(html(head=head), LOCATOR, NOW)


def test_document_without_identity_is_unverified_unless_transport_identified():
    with pytest.raises(FullTextError, match="full_text_version_unverified"):
        extract_arxiv_html(live_shaped_html(), LOCATOR, NOW)
    result = extract_arxiv_html(live_shaped_html(), LOCATOR, NOW, transport_identified=True)
    assert result.text.endswith("Exact synthetic evidence appears here.")
