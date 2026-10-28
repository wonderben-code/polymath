"""Fixed-window provider pagination through synthetic HTTP fixtures only."""

import asyncio
import hashlib
from datetime import UTC, date, datetime, timedelta, timezone

import httpx
import pytest
from test_literature import Chunks, atom_entry, openalex_work

from polymath.literature.clients import MAX_RESPONSE_BYTES, DiscoveredSource
from polymath.literature.errors import ProviderError
from polymath.literature.feed import FeedPage, OpenLiteratureFeed
from polymath.research.models import SourceDocument, SourceReference

SINCE = datetime(2026, 10, 20, 3, 14, 15, tzinfo=UTC)
UNTIL = datetime(2026, 10, 27, 11, 25, 36, tzinfo=UTC)


def arxiv_feed(*entries, total=None, start=None, per_page=None, extra=""):
    metadata = "".join(
        f"<opensearch:{tag}>{value}</opensearch:{tag}>"
        for tag, value in (
            ("totalResults", total),
            ("startIndex", start),
            ("itemsPerPage", per_page),
        )
        if value is not None
    )
    return (
        '<feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:arxiv="http://arxiv.org/schemas/atom" '
        'xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
        + metadata
        + extra
        + "".join(entries)
        + "</feed>"
    )


def client_with(handler):
    requests = []

    def capture(request):
        requests.append(request)
        return handler(request)

    return OpenLiteratureFeed(transport=httpx.MockTransport(capture)), requests


async def test_openalex_continuation_uses_frozen_dates_and_exact_opaque_cursor():
    def handler(request):
        if request.url.params["cursor"] == "*":
            return httpx.Response(
                200, json={"results": [openalex_work()], "meta": {"next_cursor": "NEXT+/="}}
            )
        assert request.url.params["cursor"] == "NEXT+/="
        return httpx.Response(
            200,
            json={
                "results": [openalex_work(id="https://openalex.org/W123457")],
                "meta": {"next_cursor": None},
            },
        )

    feed, requests = client_with(handler)
    first = await feed.fetch_page("openalex", "fixture concept", since=SINCE, until=UNTIL)
    assert first.next_cursor == first.cursor == "NEXT+/="
    assert not first.exhausted and len(first.records) == 1
    second = await feed.fetch_page(
        "openalex", "fixture concept", since=SINCE, until=UNTIL, cursor=first.next_cursor
    )
    assert second.exhausted and second.next_cursor is None
    assert first.since == second.since == SINCE
    assert first.until == second.until == UNTIL
    assert second.records[0].reference.source_id == "openalex:W123457"
    assert len(requests) == 2
    for request in requests:
        assert str(request.url).startswith("https://api.openalex.org/works?")
        assert request.url.params["filter"] == (
            "from_publication_date:2026-10-20,to_publication_date:2026-10-27"
        )
        assert request.url.params["search"] == "fixture concept"
        assert request.url.params["sort"] == "publication_date:asc"
        assert request.url.params["per-page"] == "5"
        assert "authorization" not in request.headers and "api_key" not in request.url.params
    assert first.window_precision == "day"


async def test_arxiv_short_page_with_more_results_does_not_skip_or_reset_offset():
    def handler(request):
        if request.url.params["start"] == "0":
            return httpx.Response(
                200,
                text=arxiv_feed(
                    atom_entry(),
                    atom_entry(source_id="http://arxiv.org/abs/2401.01235v1"),
                    total=3,
                    start=0,
                    per_page=5,
                ),
            )
        assert request.url.params["start"] == "2"
        return httpx.Response(
            200,
            text=arxiv_feed(
                atom_entry(source_id="http://arxiv.org/abs/2401.01236v1"),
                total=3,
                start=2,
                per_page=5,
            ),
        )

    feed, requests = client_with(handler)
    query = 'literal "quoted" OR submittedDate:anything\\'
    first = await feed.fetch_page("arxiv", query, since=SINCE, until=UNTIL)
    assert not first.exhausted and first.next_cursor == 2
    second = await feed.fetch_page("arxiv", query, since=SINCE, until=UNTIL, cursor=2)
    assert second.exhausted and second.next_cursor is None
    assert len(requests) == 2
    for request in requests:
        assert request.url.host == "export.arxiv.org" and request.url.path == "/api/query"
        assert request.url.params["search_query"] == (
            'all:"literal  quoted  OR submittedDate:anything " '
            "AND submittedDate:[202610200314 TO 202610271125]"
        )
        assert request.url.params["sortBy"] == "submittedDate"
        assert request.url.params["sortOrder"] == "ascending"
        assert request.url.params["max_results"] == "5"
    assert first.window_precision == "minute"


async def test_arxiv_without_count_metadata_continues_a_full_page_once():
    entries = [atom_entry(source_id=f"http://arxiv.org/abs/2401.0123{index}") for index in range(5)]
    feed, requests = client_with(
        lambda request: httpx.Response(
            200, text=arxiv_feed(*entries) if request.url.params["start"] == "0" else arxiv_feed()
        )
    )
    first = await feed.fetch("arxiv", "fixture", since=SINCE, until=UNTIL)
    assert first.next_cursor == 5 and not first.exhausted
    last = await feed.fetch("arxiv", "fixture", since=SINCE, until=UNTIL, cursor=5)
    assert last.exhausted and not last.records
    assert len(requests) == 2


async def test_metadata_only_discovery_is_explicit_not_fabricated_text():
    feed, requests = client_with(
        lambda _request: httpx.Response(
            200,
            json={
                "results": [openalex_work(abstract_inverted_index=None)],
                "meta": {"next_cursor": None},
            },
        )
    )
    page = await feed.fetch_page("openalex", "fixture", since=SINCE, until=UNTIL)
    assert page.exhausted and page.metadata_only == 1
    assert page.unreadable_source_ids == ("openalex:W123456",)
    assert page.records[0].document is None
    assert page.sources == page.records
    assert len(requests) == 1


async def test_feed_preserves_exact_abstract_bibliography_and_snapshot_hash():
    work = openalex_work(
        publication_year=2026,
        publication_date="2026-10-22",
        authorships=[
            {
                "author": {
                    "display_name": "Synthetic Researcher",
                    "orcid": "https://orcid.org/0000-0002-1825-0097",
                }
            }
        ],
        primary_location={"source": {"display_name": "Synthetic Journal"}, "license": "cc-by"},
    )
    feed, requests = client_with(
        lambda _request: httpx.Response(
            200, json={"results": [work], "meta": {"next_cursor": None}}
        )
    )
    before = datetime.now(UTC)
    page = await feed.fetch_page("openalex", "fixture", since=SINCE, until=UNTIL)
    record = page.records[0]
    assert record.reference.bibliography == record.document.bibliography
    bibliography = record.document.bibliography
    assert bibliography.publication_year == 2026
    assert bibliography.publication_date == date(2026, 10, 22)
    assert bibliography.authors[0].name == "Synthetic Researcher"
    assert bibliography.authors[0].orcid == "0000-0002-1825-0097"
    assert bibliography.venue == "Synthetic Journal"
    assert record.document.text == "Shared structures appear"
    assert record.document.content_sha256 == hashlib.sha256(b"Shared structures appear").hexdigest()
    assert record.document.content_kind == "abstract"
    assert before <= record.document.retrieved_at <= datetime.now(UTC)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "provider,cursor",
    [
        ("openalex", "https://attacker.invalid/cursor"),
        ("openalex", "a\nb"),
        ("openalex", "a" * 2001),
        ("openalex", "opaque%2Fcursor"),
        ("openalex", 5),
        ("openalex", True),
        ("arxiv", -1),
        ("arxiv", 100_001),
        ("arxiv", "5"),
        ("arxiv", True),
        ("arxiv", 1.5),
    ],
)
async def test_invalid_cursors_never_send_a_request(provider, cursor):
    feed, requests = client_with(lambda _request: pytest.fail("Invalid cursor was sent"))
    with pytest.raises(ValueError, match="invalid_feed_cursor"):
        await feed.fetch_page(provider, "fixture", since=SINCE, until=UNTIL, cursor=cursor)
    assert requests == []


@pytest.mark.parametrize(
    "since,until",
    [
        (SINCE.replace(tzinfo=None), UNTIL),
        (SINCE, UNTIL.replace(tzinfo=None)),
        (UNTIL, SINCE),
        ("2026-10-20", UNTIL),
        (SINCE, SINCE + timedelta(days=31, microseconds=1)),
    ],
)
async def test_invalid_windows_never_send_a_request(since, until):
    feed, requests = client_with(lambda _request: pytest.fail("Invalid window was sent"))
    with pytest.raises(ValueError, match="invalid_feed_window"):
        await feed.fetch_page("arxiv", "fixture", since=since, until=until)
    assert requests == []


@pytest.mark.parametrize("limit", [0, 6, True, 1.5])
async def test_page_size_is_strictly_bounded(limit):
    feed, requests = client_with(lambda _request: pytest.fail("Invalid page size was sent"))
    with pytest.raises(ValueError, match="result_limit"):
        await feed.fetch_page("openalex", "fixture", since=SINCE, until=UNTIL, limit=limit)
    assert requests == []


async def test_timezone_conversion_is_explicit_and_window_is_preserved():
    start = datetime(2026, 10, 20, 1, 2, tzinfo=timezone(timedelta(hours=7)))
    end = datetime(2026, 10, 21, 1, 2, tzinfo=timezone(timedelta(hours=7)))
    feed, requests = client_with(
        lambda _request: httpx.Response(200, json={"results": [], "meta": {"next_cursor": None}})
    )
    page = await feed.fetch_page("openalex", "fixture", since=start, until=end)
    assert page.since == start and page.until == end
    assert requests[0].url.params["filter"] == (
        "from_publication_date:2026-10-19,to_publication_date:2026-10-20"
    )


@pytest.mark.parametrize(
    "body",
    [
        {"results": [], "meta": {}},
        {"results": [], "meta": {"next_cursor": "NEXT"}},
        {"results": [openalex_work()], "meta": {"next_cursor": "*"}},
        {"results": [openalex_work()], "meta": {"next_cursor": "https://attacker.invalid"}},
        {"results": [openalex_work()], "meta": {"next_cursor": "a" * 2001}},
        {"results": [openalex_work()], "meta": {"next_cursor": True}},
    ],
)
async def test_unusable_openalex_continuations_fail_instead_of_claiming_exhaustion(body):
    feed, _ = client_with(lambda _request: httpx.Response(200, json=body))
    with pytest.raises(ProviderError, match="invalid_response"):
        await feed.fetch_page("openalex", "fixture", since=SINCE, until=UNTIL)


async def test_repeated_openalex_cursor_is_not_committed_as_progress():
    feed, _ = client_with(
        lambda _request: httpx.Response(
            200, json={"results": [openalex_work()], "meta": {"next_cursor": "SAME"}}
        )
    )
    with pytest.raises(ProviderError, match="invalid_response"):
        await feed.fetch_page("openalex", "fixture", since=SINCE, until=UNTIL, cursor="SAME")


@pytest.mark.parametrize(
    "body",
    [
        arxiv_feed(atom_entry(), total=2, start=1),
        arxiv_feed(atom_entry(), total=0, start=0),
        arxiv_feed(total=2, start=0),
        arxiv_feed(atom_entry(), total=1, per_page=6),
        arxiv_feed(atom_entry(), total="-1"),
        arxiv_feed(
            atom_entry(), total=1, extra="<opensearch:totalResults>1</opensearch:totalResults>"
        ),
    ],
)
async def test_inconsistent_arxiv_page_metadata_is_an_explicit_failure(body):
    feed, _ = client_with(lambda _request: httpx.Response(200, text=body))
    with pytest.raises(ProviderError, match="invalid_response"):
        await feed.fetch_page("arxiv", "fixture", since=SINCE, until=UNTIL)


async def test_arxiv_offset_cap_is_not_misrepresented_as_end_of_literature():
    feed, _ = client_with(
        lambda _request: httpx.Response(
            200,
            text=arxiv_feed(
                atom_entry(),
                total=100_002,
                start=100_000,
            ),
        )
    )
    with pytest.raises(ProviderError, match="invalid_response"):
        await feed.fetch_page("arxiv", "fixture", since=SINCE, until=UNTIL, cursor=100_000)


@pytest.mark.parametrize("provider", ["openalex", "arxiv"])
@pytest.mark.parametrize(
    "status,code", [(302, "invalid_response"), (403, "access_denied"), (429, "rate_limited")]
)
async def test_provider_errors_are_diagnostics_not_empty_successes(provider, status, code):
    feed, requests = client_with(
        lambda _request: httpx.Response(
            status,
            text="private-upstream-response",
            headers={"location": "http://127.0.0.1/private"},
        )
    )
    with pytest.raises(ProviderError, match=code) as caught:
        await feed.fetch_page(provider, "fixture", since=SINCE, until=UNTIL)
    assert caught.value.provider == provider
    assert "private" not in str(caught.value)
    assert len(requests) == 1


@pytest.mark.parametrize("provider", ["openalex", "arxiv"])
async def test_feed_transport_timeout_is_sanitized(provider):
    def timeout(_request):
        raise httpx.ReadTimeout("private-upstream-timeout")

    feed, requests = client_with(timeout)
    with pytest.raises(ProviderError, match="timeout") as caught:
        await feed.fetch_page(provider, "fixture", since=SINCE, until=UNTIL)
    assert "private" not in str(caught.value) and len(requests) == 1


async def test_feed_wall_clock_limit_has_no_automatic_retry(monkeypatch):
    import polymath.literature.clients as clients

    requests = []

    async def slow(request):
        requests.append(request)
        await asyncio.sleep(0.1)
        return httpx.Response(200, json={"results": [], "meta": {"next_cursor": None}})

    monkeypatch.setattr(clients, "REQUEST_TIMEOUT_SECONDS", 0.001)
    feed = OpenLiteratureFeed(transport=httpx.MockTransport(slow))
    with pytest.raises(ProviderError, match="timeout"):
        await feed.fetch_page("openalex", "fixture", since=SINCE, until=UNTIL)
    assert len(requests) == 1


@pytest.mark.parametrize("provider", ["openalex", "arxiv"])
async def test_response_byte_cap_applies_to_every_feed_page(provider):
    feed, _ = client_with(lambda _request: httpx.Response(200, stream=Chunks()))
    with pytest.raises(ProviderError, match="response_too_large"):
        await feed.fetch_page(provider, "fixture", since=SINCE, until=UNTIL)


async def test_declared_oversize_is_rejected_before_parsing():
    feed, _ = client_with(
        lambda _request: httpx.Response(
            200, content=b"{}", headers={"content-length": str(MAX_RESPONSE_BYTES + 1)}
        )
    )
    with pytest.raises(ProviderError, match="response_too_large"):
        await feed.fetch_page("openalex", "fixture", since=SINCE, until=UNTIL)


@pytest.mark.parametrize(
    "body",
    [
        b'{"results":[],"meta":{"next_cursor":null,"next_cursor":"MALICIOUS"}}',
        b'{"results":[],"meta":{"next_cursor":NaN}}',
        '{"results":[],"meta":{"next_cursor":null}}'.encode("utf-16"),
    ],
)
async def test_duplicate_or_non_utf8_json_cannot_supply_a_cursor(body):
    feed, _ = client_with(lambda _request: httpx.Response(200, content=body))
    with pytest.raises(ProviderError, match="invalid_response"):
        await feed.fetch_page("openalex", "fixture", since=SINCE, until=UNTIL)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-32"])
async def test_arxiv_entities_and_non_utf8_xml_are_rejected(encoding):
    payload = (
        '<!DOCTYPE feed [<!ENTITY x "expanded">]>'
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry><summary>&x;</summary></entry></feed>'
    ).encode(encoding)
    feed, _ = client_with(lambda _request: httpx.Response(200, content=payload))
    with pytest.raises(ProviderError, match="invalid_response"):
        await feed.fetch_page("arxiv", "fixture", since=SINCE, until=UNTIL)


def test_feed_page_validates_source_identity_and_exhaustion_contract():
    reference = SourceReference(
        source_id="fixture:a", title="Fixture title", url="https://example.org/a"
    )
    document = SourceDocument(
        **reference.model_dump(),
        text="Synthetic source text.",
        content_kind="abstract",
        retrieved_at=SINCE,
        extraction_method="synthetic_fixture",
    )
    mismatched = DiscoveredSource(
        reference, document.model_copy(update={"title": "Different title"})
    )
    with pytest.raises(ValueError, match="feed_source_identity_mismatch"):
        FeedPage("openalex", (mismatched,), None, True, SINCE, UNTIL)
    with pytest.raises(ValueError, match="invalid_feed_cursor"):
        FeedPage("openalex", (), "NEXT", True, SINCE, UNTIL)
    with pytest.raises(ValueError, match="invalid_feed_cursor"):
        FeedPage("arxiv", (), None, False, SINCE, UNTIL)
