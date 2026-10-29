"""Synthetic provider fixtures; these tests make no live literature/API requests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from xml.sax.saxutils import escape

import httpx
import pytest
from pydantic import SecretStr

from polymath.literature import ArxivClient, LiteratureError, OpenAlexClient, OpenLiteratureTools
from polymath.literature.clients import MAX_RESPONSE_BYTES, reconstruct_openalex_abstract
from polymath.literature.errors import ProviderError


def openalex_work(**overrides):
    work = {
        "id": "https://openalex.org/W123456",
        "display_name": "Synthetic fixture paper",
        "doi": None,
        "abstract_inverted_index": {"Shared": [0], "structures": [1], "appear": [2]},
    }
    return work | overrides


def atom_entry(
    *,
    source_id="http://arxiv.org/abs/2401.01234v2",
    title="Synthetic arXiv fixture",
    summary="A synthetic abstract with supporting observations.",
    doi=None,
):
    summary_xml = f"<summary>{escape(summary)}</summary>" if summary is not None else ""
    doi_xml = f"<arxiv:doi>{escape(doi)}</arxiv:doi>" if doi is not None else ""
    return (
        f"<entry><id>{escape(source_id)}</id><title>{escape(title)}</title>"
        f"{summary_xml}{doi_xml}</entry>"
    )


def atom_feed(*entries):
    return (
        '<feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:arxiv="http://arxiv.org/schemas/atom">' + "".join(entries) + "</feed>"
    )


def route(*, works=None, entries=None, openalex_status=200, arxiv_status=200):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.host == "api.openalex.org":
            assert request.url.path == "/works"
            return httpx.Response(
                openalex_status, json={"results": works if works is not None else [openalex_work()]}
            )
        assert request.url.host == "export.arxiv.org"
        assert request.url.path == "/api/query"
        return httpx.Response(
            arxiv_status, text=atom_feed(*(entries if entries is not None else [atom_entry()]))
        )

    return httpx.MockTransport(handler), requests


async def test_combined_search_reads_only_discovered_abstract_snapshots_with_provenance():
    transport, requests = route()
    tools = OpenLiteratureTools(transport=transport)
    before = datetime.now(UTC)
    results = await tools.search("synthetic concept", "discovery")
    assert len(results.sources) == 2
    assert results.provider_failures == ()
    by_id = {source.source_id: source for source in results.sources}
    assert by_id["arxiv:2401.01234"].url == "https://arxiv.org/abs/2401.01234"
    abstract = await tools.read("openalex:W123456")
    assert abstract.text == "Shared structures appear"
    assert abstract.content_kind == "abstract"
    assert abstract.extraction_method == "openalex_abstract_inverted_index/v1"
    assert before <= abstract.retrieved_at <= datetime.now(UTC)
    assert abstract.content_sha256 == hashlib.sha256(abstract.text.encode()).hexdigest()
    assert abstract.url == by_id[abstract.source_id].url
    arxiv = await tools.read("arxiv:2401.01234")
    assert arxiv.content_kind == "abstract"
    assert arxiv.extraction_method == "arxiv_atom_summary/v1"
    assert len(requests) == 2  # Reading snapshots never fetches a user or provider URL.


@pytest.mark.parametrize(
    "source_id", ["http://127.0.0.1/private", "openalex:W999", "arxiv:2401.01234"]
)
async def test_read_before_discovery_is_refused_without_a_network_request(source_id):
    transport, requests = route()
    tools = OpenLiteratureTools(transport=transport)
    with pytest.raises(LiteratureError, match="source_not_discovered"):
        await tools.read(source_id)
    assert requests == []


async def test_query_is_encoded_to_fixed_https_hosts_and_never_dereferenced():
    transport, requests = route()
    query = 'http://169.254.169.254/latest?secret=yes&max_results=999" OR all:anything'
    await OpenLiteratureTools(transport=transport).search(query, "prior_work")
    assert {request.url.host for request in requests} == {"api.openalex.org", "export.arxiv.org"}
    assert all(request.url.scheme == "https" for request in requests)
    openalex = next(request for request in requests if request.url.host == "api.openalex.org")
    assert openalex.url.params["search"] == query
    assert openalex.url.params["per-page"] == "5"
    assert "api_key" not in openalex.url.params
    arxiv = next(request for request in requests if request.url.host == "export.arxiv.org")
    assert arxiv.url.params["max_results"] == "5"
    assert arxiv.url.params["search_query"] == (
        'all:"http://169.254.169.254/latest?secret=yes&max_results=999" AND all:"OR" '
        'AND all:"all:anything"'
    )
    assert len(requests) == 2


async def test_arxiv_search_requires_every_quoted_term_instead_of_one_exact_phrase():
    transport, requests = route()
    await ArxivClient(transport=transport).search('error-correcting  codes "genetics\\')
    assert requests[0].url.params["search_query"] == (
        'all:"error-correcting" AND all:"codes" AND all:"genetics"'
    )


@pytest.mark.parametrize("query", ["", " ", "x" * 501, "hello\x00world", 42])
async def test_invalid_queries_send_no_requests(query):
    transport, requests = route()
    with pytest.raises(LiteratureError, match="invalid_query"):
        await OpenLiteratureTools(transport=transport).search(query, "discovery")
    assert requests == []


async def test_invalid_search_purpose_sends_no_requests():
    transport, requests = route()
    with pytest.raises(LiteratureError, match="invalid_purpose"):
        await OpenLiteratureTools(transport=transport).search("concept", "publish")
    assert requests == []


@pytest.mark.parametrize("limit", [0, 6, True, 2.5])
def test_result_limits_are_strict(limit):
    with pytest.raises(ValueError, match="result_limit"):
        OpenLiteratureTools(results_per_provider=limit)


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "access_denied"),
        (403, "access_denied"),
        (429, "rate_limited"),
        (500, "invalid_response"),
    ],
)
async def test_partial_provider_failure_is_explicit_not_an_empty_success(status, code):
    transport, _ = route(openalex_status=status, entries=[])
    result = await OpenLiteratureTools(transport=transport).search("concept", "counterevidence")
    assert result.sources == ()
    assert len(result.provider_failures) == 1
    assert result.provider_failures[0].provider == "openalex"
    assert result.provider_failures[0].code == code


async def test_failure_of_both_providers_raises_with_safe_diagnostics():
    transport, _ = route(openalex_status=401, arxiv_status=429)
    with pytest.raises(LiteratureError, match="all_providers_unavailable") as caught:
        await OpenLiteratureTools(transport=transport).search("concept", "discovery")
    failures = caught.value.provider_failures
    assert [(failure.provider, failure.code) for failure in failures] == [
        ("openalex", "access_denied"),
        ("arxiv", "rate_limited"),
    ]


async def test_redirect_is_not_followed_and_remote_error_body_is_not_exposed():
    requests = []
    private = "fixture-secret-that-must-not-appear-in-errors"

    def handler(request):
        requests.append(request)
        return httpx.Response(302, text=private, headers={"location": "http://127.0.0.1/private"})

    with pytest.raises(ProviderError, match="invalid_response") as caught:
        await OpenAlexClient(transport=httpx.MockTransport(handler)).search("concept")
    assert private not in str(caught.value)
    assert "127.0.0.1" not in str(caught.value)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "exception,code",
    [
        (httpx.ConnectError("private fixture error"), "transport_error"),
        (httpx.ReadTimeout("private fixture error"), "timeout"),
    ],
)
async def test_transport_failures_are_sanitized(exception, code):
    def handler(_request):
        raise exception

    with pytest.raises(ProviderError, match=code) as caught:
        await ArxivClient(transport=httpx.MockTransport(handler)).search("concept")
    assert "private fixture" not in str(caught.value)


@pytest.mark.parametrize(
    "abstract",
    [
        [],
        {"word": [True]},
        {"word": [-1]},
        {"word": [4000]},
        {"word": [0, 0]},
        {"word": [0], "other": [0]},
        {"word": [1]},
        {"two words": [0]},
        {"": [0]},
        {"word": "0"},
        {"word": []},
        {"word": ["0"]},
        {"a" * 501: [0]},
    ],
)
def test_openalex_inverted_index_rejects_invalid_positions_and_tokens(abstract):
    with pytest.raises(ValueError):
        reconstruct_openalex_abstract(abstract)


def test_openalex_inverted_index_preserves_token_order_and_repetitions():
    assert reconstruct_openalex_abstract({"again": [0, 2], "and": [1]}) == "again and again"
    assert reconstruct_openalex_abstract(None) is None
    assert reconstruct_openalex_abstract({}) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": "http://127.0.0.1/private"},
        {"id": "https://openalex.org.evil/W123"},
        {"id": "https://user@openalex.org/W123"},
        {"id": "https://openalex.org/W123?next=private"},
        {"id": "https://openalex.org/../W123"},
        {"id": "https://openalex.org/W0"},
        {"display_name": "x" * 1001},
        {"doi": "https://evil.test/steal"},
        {"abstract_inverted_index": {"invented": [2]}},
    ],
)
async def test_openalex_malformed_metadata_is_not_cached_or_fetched(overrides):
    transport, requests = route(works=[openalex_work(**overrides)], entries=[])
    tools = OpenLiteratureTools(transport=transport)
    result = await tools.search("concept", "discovery")
    assert result.sources == ()
    assert result.provider_failures[0].code == "invalid_response"
    with pytest.raises(LiteratureError, match="source_not_discovered"):
        await tools.read("openalex:W123456")
    assert len(requests) == 2


@pytest.mark.parametrize(
    "source_id",
    [
        "http://127.0.0.1/private",
        "https://arxiv.org.evil/abs/2401.01234",
        "https://user@arxiv.org/abs/2401.01234",
        "https://arxiv.org/abs/2401.01234?next=private",
        "https://arxiv.org/abs/2413.01234",
        "https://arxiv.org/abs/2400.01234",
        "https://arxiv.org/abs/../../../private",
        "https://arxiv.org/abs/2401.01234v0",
    ],
)
async def test_arxiv_rejects_hostile_or_malformed_source_ids(source_id):
    transport, requests = route(works=[], entries=[atom_entry(source_id=source_id)])
    result = await OpenLiteratureTools(transport=transport).search("concept", "discovery")
    assert result.sources == ()
    assert result.provider_failures[0].provider == "arxiv"
    assert result.provider_failures[0].code == "invalid_response"
    assert len(requests) == 2


async def test_legacy_arxiv_ids_and_whitespace_are_canonicalized():
    transport, _ = route(
        works=[],
        entries=[
            atom_entry(
                source_id="http://arxiv.org/abs/hep-th/9901001v3",
                title=" An  example\n title ",
                summary=" A\n synthetic\t abstract. ",
            )
        ],
    )
    tools = OpenLiteratureTools(transport=transport)
    results = await tools.search("concept", "discovery")
    source = results.sources[0]
    assert source.source_id == "arxiv:hep-th/9901001"
    assert source.url == "https://arxiv.org/abs/hep-th/9901001"
    assert source.title == "An example title"
    assert (await tools.read(source.source_id)).text == "A synthetic abstract."


@pytest.mark.parametrize(
    "body",
    [
        "<not-a-feed/>",
        "<feed",
        '<feed xmlns="urn:wrong"/>',
        '<!DOCTYPE feed [<!ENTITY x "expanded">]><feed xmlns="http://www.w3.org/2005/Atom"/>',
    ],
)
async def test_arxiv_rejects_malformed_xml_and_entity_declarations(body):
    client = ArxivClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200, text=body)))
    with pytest.raises(ProviderError, match="invalid_response"):
        await client.search("concept")


async def test_same_doi_from_both_providers_is_one_canonical_source():
    transport, _ = route(
        works=[openalex_work(doi="https://doi.org/10.1234/SYNTHETIC")],
        entries=[atom_entry(doi="10.1234/synthetic")],
    )
    tools = OpenLiteratureTools(transport=transport)
    result = await tools.search("concept", "discovery")
    assert len(result.sources) == 1
    source = result.sources[0]
    assert source.source_id == "doi:10.1234/synthetic"
    assert source.url == "https://doi.org/10.1234/synthetic"
    document = await tools.read(source.source_id)
    assert document.url == source.url
    assert document.title == source.title
    assert document.content_kind == "abstract"


async def test_no_abstract_is_explicitly_unreadable_and_never_becomes_full_text():
    transport, requests = route(works=[openalex_work(abstract_inverted_index=None)], entries=[])
    tools = OpenLiteratureTools(transport=transport)
    result = await tools.search("concept", "discovery")
    assert len(result.sources) == 1
    with pytest.raises(LiteratureError, match="source_text_unavailable"):
        await tools.read(result.sources[0].source_id)
    assert len(requests) == 2


@pytest.mark.parametrize("provider", ["openalex", "arxiv"])
async def test_excess_results_are_rejected_instead_of_unbounded(provider):
    transport, _ = route(
        works=[openalex_work(), openalex_work(id="https://openalex.org/W2")]
        if provider == "openalex"
        else [],
        entries=[atom_entry(), atom_entry(source_id="http://arxiv.org/abs/2401.01235")]
        if provider == "arxiv"
        else [],
    )
    result = await OpenLiteratureTools(transport=transport, results_per_provider=1).search(
        "concept", "discovery"
    )
    assert result.sources == ()
    assert result.provider_failures[0].provider == provider
    assert result.provider_failures[0].code == "invalid_response"


class Chunks(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"a" * (MAX_RESPONSE_BYTES // 2)
        yield b"b" * (MAX_RESPONSE_BYTES // 2 + 1)


async def test_streaming_byte_limit_does_not_depend_on_content_length():
    client = OpenAlexClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, stream=Chunks()))
    )
    with pytest.raises(ProviderError, match="response_too_large"):
        await client.search("concept")


async def test_declared_oversized_body_is_rejected_before_reading():
    client = ArxivClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200, text="small", headers={"content-length": str(MAX_RESPONSE_BYTES + 1)}
            )
        )
    )
    with pytest.raises(ProviderError, match="response_too_large"):
        await client.search("concept")


async def test_oversized_abstract_cannot_overflow_model_source_document_limit():
    transport, _ = route(works=[], entries=[atom_entry(summary="a" * 20_001)])
    result = await OpenLiteratureTools(transport=transport).search("concept", "discovery")
    assert result.sources == ()
    assert result.provider_failures[0].code == "invalid_response"


async def test_openalex_key_travels_only_as_bearer_header_never_in_query_strings():
    secret = "fixture-secret-never-for-query-logs"
    transport, requests = route()
    tools = OpenLiteratureTools(transport=transport, openalex_api_key=SecretStr(secret))
    await tools.search("concept", "discovery")
    openalex = [request for request in requests if request.url.host == "api.openalex.org"]
    arxiv = [request for request in requests if request.url.host == "export.arxiv.org"]
    assert len(openalex) == 1 and len(arxiv) == 1
    assert openalex[0].headers["authorization"] == f"Bearer {secret}"
    assert secret not in str(openalex[0].url)
    assert "api_key" not in openalex[0].url.params
    assert "authorization" not in arxiv[0].headers


async def test_openalex_without_key_sends_no_authorization_header():
    transport, requests = route()
    await OpenAlexClient(transport=transport).search("concept")
    assert "authorization" not in requests[0].headers


async def test_openalex_key_never_appears_in_provider_failures():
    secret = "fixture-secret-never-for-errors"
    transport, _ = route(openalex_status=401)
    client = OpenAlexClient(api_key=SecretStr(secret), transport=transport)
    with pytest.raises(ProviderError, match="access_denied") as caught:
        await client.search("concept")
    assert secret not in str(caught.value) and secret not in repr(caught.value)


def test_empty_openalex_key_is_rejected_without_echoing_input():
    with pytest.raises(ValueError, match="OpenAlex API key is empty") as caught:
        OpenAlexClient(api_key=SecretStr("   "))
    assert "   " not in str(caught.value).removeprefix("OpenAlex API key is empty")


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-32"])
async def test_non_utf8_entity_declarations_cannot_bypass_xml_rejection(encoding):
    body = (
        '<!DOCTYPE feed [<!ENTITY x "expanded">]>'
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry><summary>&x;</summary></entry></feed>'
    ).encode(encoding)
    client = ArxivClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=body))
    )
    with pytest.raises(ProviderError, match="invalid_response"):
        await client.search("concept")


async def test_readable_source_snapshot_and_identity_are_frozen_within_an_investigation():
    searches = 0

    def handler(request):
        nonlocal searches
        if request.url.host == "export.arxiv.org":
            return httpx.Response(200, text=atom_feed())
        searches += 1
        if searches == 1:
            work = openalex_work()
        else:
            work = openalex_work(
                display_name="Changed upstream title",
                abstract_inverted_index={"Changed": [0], "upstream": [1], "text": [2]},
            )
        return httpx.Response(200, json={"results": [work]})

    tools = OpenLiteratureTools(transport=httpx.MockTransport(handler))
    first = await tools.search("concept", "discovery")
    document = await tools.read(first.sources[0].source_id)
    second = await tools.search("concept", "counterevidence")
    assert second.sources[0] == first.sources[0]
    assert await tools.read(first.sources[0].source_id) == document
    assert document.text == "Shared structures appear"


async def test_metadata_only_discovery_can_gain_its_first_readable_abstract():
    searches = 0

    def handler(request):
        nonlocal searches
        if request.url.host == "export.arxiv.org":
            return httpx.Response(200, text=atom_feed())
        searches += 1
        work = openalex_work(abstract_inverted_index=None) if searches == 1 else openalex_work()
        return httpx.Response(200, json={"results": [work]})

    tools = OpenLiteratureTools(transport=httpx.MockTransport(handler))
    first = await tools.search("concept", "discovery")
    with pytest.raises(LiteratureError, match="source_text_unavailable"):
        await tools.read(first.sources[0].source_id)
    await tools.search("concept", "prior_work")
    assert (await tools.read(first.sources[0].source_id)).text == "Shared structures appear"


async def test_slow_provider_hits_wall_clock_limit_without_automatic_retry(monkeypatch):
    import asyncio

    from polymath.literature import clients

    requests = []

    async def handler(request):
        requests.append(request)
        await asyncio.sleep(0.1)
        return httpx.Response(200, json={"results": []})

    monkeypatch.setattr(clients, "REQUEST_TIMEOUT_SECONDS", 0.001)
    client = OpenAlexClient(transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match="timeout"):
        await client.search("concept")
    assert len(requests) == 1
