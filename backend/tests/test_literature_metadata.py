"""Bibliography/identity fixtures make no live literature or model requests."""

import hashlib
import json
from datetime import UTC, date, datetime
from xml.sax.saxutils import escape

import httpx
import pytest
from pydantic import ValidationError
from test_artifact_controller import artifact_basis
from test_literature import atom_entry, atom_feed, openalex_work, route

from polymath.literature import ArxivClient, OpenAlexClient, OpenLiteratureTools
from polymath.literature.clients import (
    MAX_RESPONSE_BYTES,
    parse_arxiv_records,
    parse_openalex_records,
)
from polymath.literature.errors import ProviderError
from polymath.literature.identity import (
    canonical_arxiv_id,
    canonical_doi,
    canonical_openalex_id,
    canonical_orcid,
    canonical_paper_id,
    distinct_paper_count,
    paper_aliases,
    same_paper,
)
from polymath.research.models import (
    ResearchResult,
    SourceAuthor,
    SourceBibliography,
    SourceDocument,
    SourceReference,
    Usage,
)

RETRIEVED_AT = datetime(2026, 10, 28, tzinfo=UTC)
ORCID = "0000-0002-1825-0097"


def bibliography_work(**overrides):
    return (
        openalex_work(
            publication_year=2024,
            publication_date="2024-01-17",
            doi="https://doi.org/10.1234/SYNTHETIC",
            authorships=[
                {
                    "author": {
                        "display_name": " Synthetic Author One ",
                        "orcid": "https://orcid.org/" + ORCID,
                    }
                },
                {"author": {"display_name": "Synthetic Author Two", "orcid": None}},
            ],
            ids={
                "openalex": "https://openalex.org/W123456",
                "doi": "https://doi.org/10.1234/synthetic",
            },
            primary_location={
                "source": {"display_name": "Synthetic Journal"},
                "landing_page_url": "https://arxiv.org/abs/2401.01234v2",
                "license": "cc-by-4.0",
            },
        )
        | overrides
    )


def bibliography_entry(*, count=2, published="2024-01-17T09:00:00Z", **changes):
    entry = atom_entry(**changes)
    additions = "".join(
        f"<author><name>Synthetic Author {index}</name>"
        + (f"<arxiv:orcid>{ORCID}</arxiv:orcid>" if index == 0 else "")
        + "</author>"
        for index in range(count)
    )
    if published is not None:
        additions += f"<published>{escape(published)}</published>"
    additions += "<arxiv:journal_ref>Synthetic Journal 2, 17 (2024)</arxiv:journal_ref>"
    return entry.replace("</entry>", additions + "</entry>")


def reference(identifier, url, **metadata):
    return SourceReference(
        source_id=identifier,
        title="Same synthetic title never establishes identity",
        url=url,
        bibliography=SourceBibliography(**metadata) if metadata else None,
    )


async def test_openalex_preserves_declared_bibliography_and_aliases_without_fetching_locations():
    transport, requests = route(works=[bibliography_work()], entries=[])
    tools = OpenLiteratureTools(transport=transport)
    found = await tools.search("synthetic concept", "discovery")
    assert len(found.sources) == 1
    ref = found.sources[0]
    source = await tools.read(ref.source_id)
    bibliography = source.bibliography
    assert bibliography == ref.bibliography
    assert bibliography.authors == (
        SourceAuthor(name="Synthetic Author One", orcid=ORCID),
        SourceAuthor(name="Synthetic Author Two"),
    )
    assert bibliography.publication_year == 2024
    assert bibliography.publication_date == date(2024, 1, 17)
    assert bibliography.venue == "Synthetic Journal"
    assert bibliography.doi == "10.1234/synthetic"
    assert bibliography.arxiv_id == "2401.01234" and bibliography.arxiv_version == 2
    assert bibliography.openalex_id == "W123456" and bibliography.license == "cc-by-4.0"
    assert not bibliography.authors_truncated
    assert source.source_id == "doi:10.1234/synthetic"
    assert source.content_kind == "abstract"
    assert source.content_sha256 == hashlib.sha256(b"Shared structures appear").hexdigest()
    assert len(requests) == 2
    assert {request.url.host for request in requests} == {"api.openalex.org", "export.arxiv.org"}
    assert {"doi:10.1234/synthetic", "arxiv:2401.01234", "openalex:W123456"} <= paper_aliases(
        source
    )


async def test_arxiv_preserves_authors_publication_date_journal_and_version_separately():
    transport, requests = route(works=[], entries=[bibliography_entry(doi="10.1234/SYNTHETIC")])
    tools = OpenLiteratureTools(transport=transport)
    ref = (await tools.search("synthetic concept", "discovery")).sources[0]
    source = await tools.read(ref.source_id)
    assert ref.source_id == "doi:10.1234/synthetic"
    assert source.bibliography.arxiv_id == "2401.01234"
    assert source.bibliography.arxiv_version == 2
    assert source.bibliography.authors[0].orcid == ORCID
    assert source.bibliography.publication_date == date(2024, 1, 17)
    assert source.bibliography.publication_year == 2024
    assert source.bibliography.venue == "Synthetic Journal 2, 17 (2024)"
    assert source.bibliography.openalex_id is None and source.bibliography.license is None
    assert source.content_kind == "abstract" and len(requests) == 2


@pytest.mark.parametrize("provider", ["openalex", "arxiv"])
async def test_author_lists_are_bounded_and_truncation_is_explicit(provider):
    authors = [{"author": {"display_name": f"Synthetic Author {index}"}} for index in range(101)]
    transport, _ = route(
        works=[bibliography_work(authorships=authors)] if provider == "openalex" else [],
        entries=[bibliography_entry(count=101)] if provider == "arxiv" else [],
    )
    tools = OpenLiteratureTools(transport=transport)
    ref = (await tools.search("concept", "discovery")).sources[0]
    assert ref.bibliography.authors_truncated
    assert len(ref.bibliography.authors) == 100
    assert ref.bibliography.authors[-1].name == "Synthetic Author 99"


async def test_metadata_only_source_retains_bibliography_without_inventing_text():
    transport, _ = route(works=[bibliography_work(abstract_inverted_index=None)], entries=[])
    records = await OpenAlexClient(transport=transport).search("concept")
    assert records[0].document is None
    assert records[0].reference.bibliography.authors[0].name == "Synthetic Author One"
    assert canonical_paper_id(records[0].reference) == "doi:10.1234/synthetic"


async def test_unavailable_metadata_remains_unknown_and_does_not_infer_a_publication_year():
    transport, _ = route(works=[openalex_work()], entries=[atom_entry()])
    tools = OpenLiteratureTools(transport=transport)
    results = await tools.search("concept", "discovery")
    for ref in results.sources:
        bibliography = ref.bibliography
        assert bibliography.authors == ()
        assert bibliography.publication_year is None and bibliography.publication_date is None
        assert bibliography.venue is None and not bibliography.authors_truncated


def test_absent_metadata_preserves_legacy_source_serialization_and_content_hash():
    legacy = {
        "source_id": "fixture:historic",
        "title": "Historic synthetic snapshot",
        "url": "https://example.org/historic",
        "text": "The exact historic source text.",
        "content_kind": "abstract",
        "retrieved_at": "2026-10-28T00:00:00Z",
        "extraction_method": "synthetic_snapshot/v1",
    }
    source = SourceDocument.model_validate_json(json.dumps(legacy))
    assert source.bibliography is None
    assert source.model_dump(mode="json") == legacy
    assert source.model_dump(mode="json", exclude_none=False) == legacy
    assert source.content_sha256 == hashlib.sha256(legacy["text"].encode()).hexdigest()
    result = ResearchResult(status="limit_reached", sources=(source,), usage=Usage())
    assert result.model_dump(mode="json")["sources"] == [legacy]
    assert "bibliography" not in result.model_dump_json()
    original_passages = artifact_basis().model_dump(mode="json")["passages"]
    assert all("bibliography" not in passage for passage in original_passages)
    assert all(
        "claim_id" in passage and passage["claim_id"] is None for passage in original_passages
    )


def test_enriched_metadata_roundtrips_without_changing_text_content_identity():
    source = SourceDocument(
        source_id="arxiv:2401.01234",
        title="Synthetic metadata snapshot",
        url="https://arxiv.org/abs/2401.01234",
        text="The text remains exactly the same.",
        content_kind="abstract",
        retrieved_at=RETRIEVED_AT,
        extraction_method="synthetic_snapshot/v1",
    )
    enriched = source.model_copy(
        update={
            "bibliography": SourceBibliography(
                authors=(SourceAuthor(name="Synthetic Author", orcid=ORCID),),
                publication_year=2024,
                publication_date=date(2024, 1, 17),
                doi="HTTPS://DOI.ORG/10.1234/MIXED",
                arxiv_id="2401.01234",
                arxiv_version=2,
            )
        }
    )
    assert enriched.content_sha256 == source.content_sha256
    assert SourceDocument.model_validate_json(enriched.model_dump_json()) == enriched
    assert enriched.bibliography.doi == "10.1234/mixed"


def test_paper_aliases_preserve_same_url_even_with_different_known_identifiers():
    first = reference("doi:10.1234/one", "https://EXAMPLE.org:443/paper?view=1#passage")
    second = reference("arxiv:2401.01234", "https://example.org/paper?view=1#passage")
    assert "url:https://example.org/paper?view=1#passage" in paper_aliases(first)
    assert same_paper(first, second)
    assert distinct_paper_count((first, second)) == 1


def test_abstract_full_text_and_arxiv_versions_do_not_become_independent_papers():
    abstract = reference("arxiv:2401.01234", "https://arxiv.org/abs/2401.01234")
    full_text = reference(
        "fulltext:2401.01234v2:window:0",
        "https://arxiv.org/html/2401.01234v2",
        arxiv_id="2401.01234",
        arxiv_version=2,
    )
    pdf = reference("different-id", "https://arxiv.org/pdf/2401.01234v3.pdf")
    assert same_paper(abstract, full_text) and same_paper(full_text, pdf)
    assert distinct_paper_count((abstract, full_text, pdf)) == 1


def test_identifier_alias_overlap_is_transitive_across_all_supplied_references():
    first = reference("doi:10.1234/bridge", "https://doi.org/10.1234/bridge", arxiv_id="2401.01234")
    second = reference(
        "arxiv:2401.01234", "https://arxiv.org/abs/2401.01234", openalex_id="W123456"
    )
    third = reference("openalex:W123456", "https://openalex.org/W123456")
    assert not same_paper(first, third)
    assert distinct_paper_count((first, second, third)) == 1
    assert distinct_paper_count((third, first, second)) == 1
    other = reference("doi:10.1234/independent", "https://doi.org/10.1234/independent")
    assert distinct_paper_count((first, second, third, other)) == 2


def test_read_but_uncited_connector_still_prevents_false_independent_corroboration():
    first = reference("doi:10.1234/bridge", "https://doi.org/10.1234/bridge")
    second = reference("openalex:W123456", "https://openalex.org/W123456")
    connector = reference(
        "arxiv:2401.01234",
        "https://arxiv.org/abs/2401.01234",
        doi="10.1234/bridge",
        openalex_id="W123456",
    )
    unrelated = reference("doi:10.1234/other", "https://doi.org/10.1234/other")
    assert distinct_paper_count((first, second)) == 2
    assert distinct_paper_count((first, second), context=(connector, unrelated)) == 1
    # Context contributes alias knowledge, never extra evidence endpoints.
    assert distinct_paper_count((first,), context=(unrelated,)) == 1
    assert distinct_paper_count((), context=(unrelated,)) == 0


def test_identical_titles_and_distinct_query_resources_are_not_an_identity_match():
    first = reference("first", "https://example.org/paper?id=1")
    second = reference("second", "https://example.org/paper?id=2")
    assert first.title == second.title
    assert not same_paper(first, second)
    assert distinct_paper_count((first, second)) == 2


async def test_declared_id_alias_does_not_rewrite_the_existing_provider_source_id():
    item = openalex_work(ids={"doi": "https://doi.org/10.1234/DECLARED"})
    transport, _ = route(works=[item], entries=[])
    ref = (await OpenAlexClient(transport=transport).search("concept"))[0].reference
    assert ref.source_id == "openalex:W123456" and ref.url == "https://openalex.org/W123456"
    assert canonical_paper_id(ref) == "doi:10.1234/declared"


async def test_first_readable_bibliographic_snapshot_is_not_rewritten_by_later_searches():
    calls = 0

    def handler(request):
        nonlocal calls
        if request.url.host == "export.arxiv.org":
            return httpx.Response(200, text=atom_feed())
        calls += 1
        item = bibliography_work(publication_year=2024 if calls == 1 else 2025)
        return httpx.Response(200, json={"results": [item]})

    tools = OpenLiteratureTools(transport=httpx.MockTransport(handler))
    first = (await tools.search("concept", "discovery")).sources[0]
    original = await tools.read(first.source_id)
    again = (await tools.search("concept", "prior_work")).sources[0]
    assert again == first and await tools.read(first.source_id) == original
    assert again.bibliography.publication_year == 2024


@pytest.mark.parametrize(
    "changes",
    [
        {"publication_year": True},
        {"publication_date": "2024-02-31"},
        {
            "authorships": [
                {"author": {"display_name": "Synthetic", "orcid": "0000-0002-1825-0098"}}
            ]
        },
        {"authorships": [{"author": {"display_name": "bad\x00name"}}]},
        {"authorships": [{"author": None}]},
        {"ids": {"doi": "https://doi.org/10.1234/different"}},
        {"ids": {"openalex": "https://openalex.org/W999"}},
        {"primary_location": {"source": "not-an-object"}},
        {"primary_location": {"license": "x" * 101}},
    ],
)
async def test_malformed_declared_bibliography_is_an_explicit_provider_failure(changes):
    transport, _ = route(works=[bibliography_work(**changes)], entries=[])
    with pytest.raises(ProviderError, match="invalid_response"):
        await OpenAlexClient(transport=transport).search("concept")


@pytest.mark.parametrize("published", ["not-a-date", "2024-01-17", "2024-02-31T00:00:00Z"])
async def test_arxiv_publication_timestamps_require_a_real_timezone_aware_date(published):
    transport, _ = route(works=[], entries=[bibliography_entry(published=published)])
    with pytest.raises(ProviderError, match="invalid_response"):
        await ArxivClient(transport=transport).search("concept")


@pytest.mark.parametrize(
    "changes",
    [
        {"publication_year": True},
        {"publication_year": 999},
        {"publication_year": 2101},
        {"arxiv_id": "2413.01234"},
        {"arxiv_id": "2401.01234v2"},
        {"arxiv_version": 2},
        {"openalex_id": "https://attacker.invalid/W123"},
        {"doi": "https://secret@doi.org/10.1234/id"},
        {"doi": "https://do\ti.org/10.1234/id"},
        {"authors_truncated": True},
    ],
)
def test_source_bibliography_rejects_ambiguous_or_unbounded_identifier_metadata(changes):
    with pytest.raises(ValidationError):
        SourceBibliography(**changes)


@pytest.mark.parametrize(
    "value", ["0000-0002-1825-0098", "https://attacker.invalid/" + ORCID, True]
)
def test_source_orcid_rejects_wrong_check_digits_or_untrusted_identifier_urls(value):
    with pytest.raises(ValidationError):
        SourceAuthor(name="Synthetic Author", orcid=value)


def test_known_identifier_normalizers_preserve_canonical_aliases_without_network():
    assert canonical_doi("https://DOI.ORG/10.1234/UPPER") == "10.1234/upper"
    assert canonical_arxiv_id("hep-th/9901001v3") == ("hep-th/9901001", 3)
    assert canonical_openalex_id("https://openalex.org/W123456") == "W123456"
    assert canonical_orcid("https://orcid.org/" + ORCID) == ORCID


@pytest.mark.parametrize(
    "parser,body",
    [
        (parse_openalex_records, b'{"results":[],"results":[]}'),
        (parse_openalex_records, b'{"results":[],"unused":NaN}'),
        (parse_openalex_records, '{"results":[]}'.encode("utf-16")),
        (parse_arxiv_records, atom_feed().encode("utf-16")),
    ],
)
def test_feed_parser_reuse_retains_strict_utf8_and_unambiguous_data_boundary(parser, body):
    with pytest.raises(ProviderError, match="invalid_response"):
        parser(body, RETRIEVED_AT)


@pytest.mark.parametrize(
    "parser,payload",
    [
        (parse_openalex_records, b'{"results":[]}'),
        (parse_arxiv_records, atom_feed().encode()),
    ],
)
def test_pure_provider_parser_limits_are_bounded_and_require_retrieval_provenance(parser, payload):
    for limit in (0, 101, True):
        with pytest.raises(ProviderError, match="invalid_response"):
            parser(payload, RETRIEVED_AT, limit)
    with pytest.raises(ProviderError, match="invalid_response"):
        parser(payload, RETRIEVED_AT.replace(tzinfo=None))
    with pytest.raises(ProviderError, match="response_too_large"):
        parser(b"x" * (MAX_RESPONSE_BYTES + 1), RETRIEVED_AT)


def test_feed_parser_accepts_bounded_pages_without_relaxing_search_limit():
    works = [openalex_work(id=f"https://openalex.org/W{index + 1}") for index in range(6)]
    parsed = parse_openalex_records(json.dumps({"results": works}).encode(), RETRIEVED_AT, 6)
    assert len(parsed) == 6
    with pytest.raises(ProviderError, match="invalid_response"):
        parse_openalex_records(json.dumps({"results": works}).encode(), RETRIEVED_AT, 5)


def test_identity_count_is_bounded_for_external_iterables():
    values = (reference(f"fixture:{index}", f"https://example.org/{index}") for index in range(201))
    with pytest.raises(ValueError, match="reference limit"):
        distinct_paper_count(values)
    context = (
        reference(f"fixture:{index}", f"https://example.org/{index}") for index in range(201)
    )
    with pytest.raises(ValueError, match="reference limit"):
        distinct_paper_count((), context=context)


def test_multibyte_author_metadata_is_bounded_before_database_persistence():
    with pytest.raises(ValidationError, match="UTF-8 byte limit"):
        SourceBibliography(authors=tuple(SourceAuthor(name="🌱" * 300) for _ in range(100)))
    valid = SourceBibliography(authors=tuple(SourceAuthor(name="a" * 300) for _ in range(100)))
    assert len(valid.authors) == 100 and not valid.authors_truncated
