"""Public projection privacy/integrity fixtures; no database or native calls."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from polymath.research.models import (
    AnswerFinding,
    BridgeFinding,
    FieldSelection,
    ResearchEvent,
    ResearchResult,
    SourceAuthor,
    SourceBibliography,
    SourceCoverage,
    SourceDocument,
    SourceReference,
    Usage,
    VerifiedQuote,
)
from polymath.sharing_contracts import PublicFindingProjection, PublicScholarlyLink
from polymath.sharing_projection import (
    SharingProjectionError,
    build_public_projection,
    canonical_projection_json,
    projection_sha256,
)

NOW = datetime(2026, 10, 28, tzinfo=UTC)
PRIVATE_SNAPSHOT = "12345678-1234-1234-1234-123456789abc"


def source(identifier="private-source-a", *, text=None, **changes):
    return SourceDocument(
        **(
            {
                "source_id": identifier,
                "title": "Synthetic cited paper",
                "url": f"https://private.example.test/opaque/{identifier}?token=PRIVATE_URL_TOKEN",
                "text": text
                or "🌌 Exact synthetic source evidence supports a conditional analogy.",
                "content_kind": "abstract",
                "retrieved_at": NOW,
                "extraction_method": "private-extractor-v1",
            }
            | changes
        )
    )


def bridge_result(documents=None, *, quotes=None):
    documents = tuple(
        documents or (source(), source("private-source-b", title="Independent cited paper"))
    )
    if quotes is None:
        quotes = [
            (doc.source_id, "left" if index == 0 else "right", doc.text[2:])
            for index, doc in enumerate(documents)
        ]
    finding = BridgeFinding.model_validate_json(
        json.dumps(
            {
                "outcome": "bridge",
                "title": "Complete synthetic bridge",
                "field_left": "Information theory",
                "field_right": "Biology",
                "explanation": "Complete model explanation.",
                "mappings": [
                    {"left": "Messages", "right": "Pathways", "assumption": "Comparable noise."}
                ],
                "open_question": "Does the analogy survive?",
                "strongest_objection": "Mechanisms differ.",
                "objection_verdict": "The analogy is unverified.",
                "novelty_limitations": "No proof or novelty established.",
                "evidence": [
                    {
                        "source_id": identifier,
                        "side": side,
                        "quote": text,
                        "supports": "Original support statement.",
                    }
                    for identifier, side, text in quotes
                ],
            }
        )
    )
    docs = {doc.source_id: doc for doc in documents}
    verified = tuple(
        VerifiedQuote(
            source_id=identifier,
            side=side,
            quote=text,
            supports="Original support statement.",
            start=docs[identifier].text.find(text),
            end=docs[identifier].text.find(text) + len(text),
            source_sha256=docs[identifier].content_sha256,
            content_kind=docs[identifier].content_kind,
        )
        for identifier, side, text in quotes
    )
    return ResearchResult(
        status="bridge",
        finding=finding,
        sources=documents,
        quotes=verified,
        events=(ResearchEvent(sequence=1, kind="private-event", summary="PRIVATE_EVENT_CONTEXT"),),
        usage=Usage(model_calls=9, input_tokens_reported=12345),
    )


def answer_result(documents=None, *, claims=None):
    documents = tuple(documents or (source(), source("private-source-b")))
    if claims is None:
        claims = [
            {
                "id": "PRIVATE_KNOWN_ID",
                "text": "Original known claim.",
                "label": "known",
                "evidence": [
                    {
                        "source_id": documents[0].source_id,
                        "quote": documents[0].text[2:],
                        "supports": "Known support.",
                    }
                ],
                "caveats": ["Known caveat."],
            },
            {
                "id": "PRIVATE_POSTULATED_ID",
                "text": "Original postulated claim.",
                "label": "postulated",
                "derivation": "Complete original derivation.",
                "evidence": [
                    {
                        "source_id": doc.source_id,
                        "quote": doc.text[2:],
                        "supports": "Derived support.",
                    }
                    for doc in documents
                ],
                "caveats": ["Inference caveat."],
            },
            {
                "id": "PRIVATE_SPECULATIVE_ID",
                "text": "Original speculative claim.",
                "label": "speculative",
                "evidence": [
                    {
                        "source_id": documents[-1].source_id,
                        "quote": documents[-1].text[2:],
                        "supports": "Speculative support.",
                    }
                ],
                "caveats": ["Unverified extension."],
            },
        ]
    finding = AnswerFinding.model_validate_json(
        json.dumps(
            {
                "outcome": "answer",
                "question": "The exact original Ask question is public?",
                "summary": "Complete original answer summary.",
                "claims": claims,
                "limitations": ["Original answer limitation."],
                "unanswered_questions": ["Original unanswered question?"],
            }
        )
    )
    docs = {doc.source_id: doc for doc in documents}
    verified = []
    for claim in finding.claims:
        for quote in claim.evidence:
            doc = docs[quote.source_id]
            start = doc.text.find(quote.quote)
            verified.append(
                VerifiedQuote(
                    source_id=doc.source_id,
                    side="answer",
                    claim_id=claim.id,
                    quote=quote.quote,
                    supports=quote.supports,
                    start=start,
                    end=start + len(quote.quote),
                    source_sha256=doc.content_sha256,
                    content_kind=doc.content_kind,
                )
            )
    return ResearchResult(
        status="answer",
        mode="ask",
        finding=finding,
        sources=documents,
        quotes=tuple(verified),
        usage=Usage(),
    )


def test_bridge_projection_copies_complete_science_but_no_private_structure_or_source_urls():
    result = bridge_result()
    projection = build_public_projection(result)
    public = projection.model_dump(mode="json")
    for key in (
        "title",
        "field_left",
        "field_right",
        "explanation",
        "open_question",
        "strongest_objection",
        "objection_verdict",
        "novelty_limitations",
    ):
        assert public["finding"][key] == getattr(result.finding, key)
    assert public["finding"]["mappings"] == [item.model_dump() for item in result.finding.mappings]
    assert public["finding"]["citation_ids"] == ["citation-1", "citation-2"]
    assert [paper.id for paper in projection.papers] == ["paper-1", "paper-2"]
    assert projection.papers[0].links == () and projection.papers[0].identifiers.model_dump() == {}
    encoded = canonical_projection_json(projection)
    for private in (
        "private-source-a",
        "private-source-b",
        "PRIVATE_URL_TOKEN",
        "private.example.test",
        "PRIVATE_EVENT_CONTEXT",
        "private-extractor-v1",
        "input_tokens_reported",
        "retrieved_at",
        "events",
        "usage",
    ):
        assert private not in encoded
    assert "field_selection" not in public
    assert "evidence" not in public["finding"]
    assert "coverage" not in public["citations"][0]


def test_ask_keeps_every_claim_label_derivation_caveat_and_citation_association():
    result = answer_result()
    projection = build_public_projection(result)
    assert projection.finding.question == result.finding.question
    assert projection.finding.summary == result.finding.summary
    assert projection.finding.limitations == tuple(result.finding.limitations)
    assert projection.finding.unanswered_questions == tuple(result.finding.unanswered_questions)
    for index, (before, after) in enumerate(
        zip(result.finding.claims, projection.finding.claims, strict=True)
    ):
        assert after.id == f"claim-{index + 1}"
        assert (after.text, after.label, after.derivation, after.caveats) == (
            before.text,
            before.label,
            before.derivation,
            tuple(before.caveats),
        )
        assert len(after.citation_ids) == len(before.evidence)
    assert [citation.claim_id for citation in projection.citations] == [
        "claim-1",
        "claim-2",
        "claim-2",
        "claim-3",
    ]
    assert [citation.supports for citation in projection.citations] == [
        quote.supports for quote in result.quotes
    ]
    encoded = canonical_projection_json(projection)
    assert "PRIVATE_KNOWN_ID" not in encoded and "PRIVATE_POSTULATED_ID" not in encoded


def test_surprise_exposes_only_final_field_selection_and_rationale():
    result = bridge_result().model_copy(
        update={
            "mode": "surprise",
            "field_selections": (
                FieldSelection(
                    fields=("PRIVATE_EARLY_FIELD", "Other field"),
                    rationale="PRIVATE_EARLY_RATIONALE",
                ),
                FieldSelection(
                    fields=("Information theory", "Biology"), rationale="Exact final rationale."
                ),
            ),
        }
    )
    projection = build_public_projection(result)
    assert projection.field_selection.rationale == "Exact final rationale."
    assert projection.field_selection.fields == ("Information theory", "Biology")
    assert "PRIVATE_EARLY" not in canonical_projection_json(projection)


def test_unicode_excerpt_is_an_exact_280_codepoint_prefix_with_separate_full_offsets_and_hash():
    quote = "😀e\u0301" * 150
    left = source(text="🌌 " + quote + " PRIVATE_RAW_BODY_TAIL")
    result = bridge_result(
        (left, source("private-source-b")),
        quotes=[
            (left.source_id, "left", quote),
            (
                "private-source-b",
                "right",
                "Exact synthetic source evidence supports a conditional analogy.",
            ),
        ],
    )
    projected = build_public_projection(result).citations[0].quote
    assert projected.excerpt == quote[:280]
    assert len(projected.excerpt) == 280 and "…" not in projected.excerpt
    assert (projected.excerpt_start, projected.excerpt_end) == (2, 282)
    assert (projected.full_quote_start, projected.full_quote_end, projected.full_quote_chars) == (
        2,
        452,
        450,
    )
    assert projected.full_quote_sha256 == hashlib.sha256(quote.encode()).hexdigest()
    assert projected.excerpted and projected.excerpt_omission_reason is None
    assert "PRIVATE_RAW_BODY_TAIL" not in canonical_projection_json(build_public_projection(result))


def test_per_paper_budget_counts_repeats_and_keeps_citations_when_remaining_is_under_ten():
    left = source(text="x" * 500)
    right = source("private-source-b")
    quotes = [(left.source_id, "left", "x" * 400)] * 3 + [
        (left.source_id, "left", "x" * 155),
        (left.source_id, "left", "x" * 10),
        (right.source_id, "right", right.text[2:]),
    ]
    projection = build_public_projection(bridge_result((left, right), quotes=quotes))
    assert [len(item.quote.excerpt or "") for item in projection.citations[:5]] == [
        280,
        280,
        280,
        155,
        0,
    ]
    omitted = projection.citations[4].quote
    assert omitted.excerpt is omitted.excerpt_start is omitted.excerpt_end is None
    assert omitted.excerpted and omitted.excerpt_omission_reason == "paper_limit"
    assert omitted.full_quote_chars == 10
    assert len(projection.finding.citation_ids) == len(projection.citations) == 6


def test_uncited_transitive_alias_connectors_enforce_joint_budget_without_leaking_metadata():
    left = source(bibliography=SourceBibliography(doi="10.1234/a"), text="a" * 500)
    right = source(
        "private-source-b", bibliography=SourceBibliography(doi="10.1234/b"), text="b" * 500
    )
    connectors = (
        SourceReference(
            source_id="PRIVATE_CONNECTOR_A",
            title="PRIVATE_CONNECTOR_TITLE_A",
            url="https://private.example.test/connector-a",
            bibliography=SourceBibliography(doi="10.1234/a", arxiv_id="2401.01234"),
        ),
        SourceReference(
            source_id="PRIVATE_CONNECTOR_B",
            title="PRIVATE_CONNECTOR_TITLE_B",
            url="https://private.example.test/connector-b",
            bibliography=SourceBibliography(doi="10.1234/b", arxiv_id="2401.01234"),
        ),
    )
    quotes = [(left.source_id, "left", "a" * 400)] * 2 + [(right.source_id, "right", "b" * 400)] * 3
    result = bridge_result((left, right), quotes=quotes)
    projection = build_public_projection(result, identity_context=connectors)
    assert len(projection.papers) == 1
    assert [len(item.quote.excerpt or "") for item in projection.citations] == [
        280,
        280,
        280,
        160,
        0,
    ]
    encoded = canonical_projection_json(projection)
    assert "PRIVATE_CONNECTOR" not in encoded and "2401.01234" not in encoded
    assert projection.papers[0].identifiers.doi == "10.1234/a"


def test_uncited_read_sources_affect_identity_without_appearing_in_output():
    first = source(text="a" * 500, bibliography=SourceBibliography(doi="10.1234/a"))
    second = source(
        "private-source-b", text="b" * 500, bibliography=SourceBibliography(openalex_id="W2")
    )
    connector = source(
        "PRIVATE_UNUSED_SOURCE",
        title="PRIVATE_UNUSED_TITLE",
        text="PRIVATE_UNUSED_BODY",
        bibliography=SourceBibliography(doi="10.1234/a", openalex_id="W2"),
    )
    result = bridge_result(
        (first, second, connector),
        quotes=[(first.source_id, "left", "a" * 400)] * 2
        + [(second.source_id, "right", "b" * 400)] * 2,
    )
    projection = build_public_projection(result)
    assert len(projection.papers) == 1
    assert sum(len(item.quote.excerpt or "") for item in projection.citations) == 1000
    assert "PRIVATE_UNUSED" not in canonical_projection_json(projection)


def test_full_text_windows_share_budget_and_public_coverage_contains_no_uuid():
    snapshot = "a" * 1000 + "b" * 1000
    fullsha = hashlib.sha256(snapshot.encode()).hexdigest()
    documents = []
    for start, end in ((0, 1000), (1000, 2000)):
        documents.append(
            source(
                f"arxiv-html:{PRIVATE_SNAPSHOT}:{start}:{end}",
                text=snapshot[start:end],
                url="https://arxiv.org/html/2401.01234v2",
                content_kind="full_text",
                bibliography=SourceBibliography(
                    doi="10.1234/full", arxiv_id="2401.01234", arxiv_version=2
                ),
                coverage=SourceCoverage(
                    snapshot_id=PRIVATE_SNAPSHOT,
                    snapshot_sha256=fullsha,
                    arxiv_id="2401.01234",
                    arxiv_version=2,
                    start=start,
                    end=end,
                    total=2000,
                ),
            )
        )
    right = source("independent")
    quotes = (
        [(documents[0].source_id, "left", "a" * 400)] * 2
        + [(documents[1].source_id, "left", "b" * 400)] * 2
        + [(right.source_id, "right", right.text[2:])]
    )
    projection = build_public_projection(bridge_result((*documents, right), quotes=quotes))
    assert len(projection.papers) == 2
    assert (
        sum(
            len(item.quote.excerpt or "")
            for item in projection.citations
            if item.paper_id == "paper-1"
        )
        == 1000
    )
    public = projection.citations[2].coverage.model_dump()
    assert public == {
        "snapshot_sha256": fullsha,
        "arxiv_id": "2401.01234",
        "arxiv_version": 2,
        "start": 1000,
        "end": 2000,
        "total": 2000,
    }
    assert PRIVATE_SNAPSHOT not in canonical_projection_json(projection)
    assert projection.citations[0].source_sha256 == documents[0].content_sha256 != fullsha
    assert projection.papers[0].links[1].url == "https://arxiv.org/html/2401.01234v2"


def test_bibliography_uses_twenty_author_prefix_explicit_truncation_and_reconstructed_ids_only():
    bib = SourceBibliography(
        authors=tuple(SourceAuthor(name=f"Author {index}") for index in range(25)),
        publication_year=2025,
        publication_date=date(2025, 1, 2),
        venue="Synthetic venue",
        doi="10.1234/test",
        arxiv_id="2401.01234",
        arxiv_version=2,
        openalex_id="W123",
        license="https://private.example.test/?license=PRIVATE_LICENSE_TOKEN",
    )
    result = bridge_result((source(bibliography=bib), source("private-source-b")))
    projection = build_public_projection(result)
    first = projection.papers[0]
    assert len(first.bibliography.authors) == 20 and first.bibliography.authors_truncated
    assert first.bibliography.publication_date == date(2025, 1, 2)
    assert [item.url for item in first.links] == [
        "https://doi.org/10.1234/test",
        "https://arxiv.org/abs/2401.01234v2",
        "https://openalex.org/W123",
    ]
    assert "PRIVATE_LICENSE" not in canonical_projection_json(projection)


def test_canonical_hash_and_ordinals_are_stable_across_unused_context_and_source_order():
    result = bridge_result()
    first = build_public_projection(result)
    second = build_public_projection(
        result.model_copy(update={"sources": tuple(reversed(result.sources))}),
        identity_context=(
            SourceReference(source_id="unused", title="Unused", url="https://example.test/unused"),
        ),
    )
    assert canonical_projection_json(first) == canonical_projection_json(second)
    assert (
        projection_sha256(first)
        == hashlib.sha256(canonical_projection_json(first).encode()).hexdigest()
    )
    assert PublicFindingProjection.model_validate_json(first.model_dump_json()) == first


@pytest.mark.parametrize(
    "change",
    [
        {"source_sha256": "0" * 64},
        {"start": 3},
        {"end": 999},
        {"side": "right"},
        {"source_id": "private-unknown"},
        {"supports": "Rewritten support."},
        {"content_kind": "full_text"},
        {"quote": "Fabricated exact quote."},
    ],
)
def test_source_quote_hash_kind_offsets_and_associations_cannot_be_forged(change):
    result = bridge_result()
    bad = result.quotes[0].model_copy(update=change)
    with pytest.raises(SharingProjectionError, match="public_source_integrity_failed"):
        build_public_projection(result.model_copy(update={"quotes": (bad, *result.quotes[1:])}))


@pytest.mark.parametrize(
    "kind",
    [
        "duplicate_source",
        "extra_quote",
        "missing_quote",
        "wrong_order",
        "missing_source",
        "mismatched_status",
    ],
)
def test_complete_source_and_citation_integrity_is_required(kind):
    result = bridge_result()
    changes = {
        "duplicate_source": {"sources": (result.sources[0], result.sources[0])},
        "extra_quote": {"quotes": (*result.quotes, result.quotes[0])},
        "missing_quote": {"quotes": result.quotes[:1]},
        "wrong_order": {"quotes": tuple(reversed(result.quotes))},
        "missing_source": {"sources": result.sources[:1]},
        "mismatched_status": {"status": "answer"},
    }[kind]
    with pytest.raises(SharingProjectionError):
        build_public_projection(result.model_copy(update=changes))


def test_all_64_ask_citations_remain_visible_when_most_excerpts_are_omitted():
    document = source(text="x" * 500)
    claims = [
        {
            "id": f"PRIVATE_{index}",
            "text": f"Original claim {index}.",
            "label": "known",
            "evidence": [
                {
                    "source_id": document.source_id,
                    "quote": "x" * 400,
                    "supports": "Original evidence association.",
                }
                for _ in range(8)
            ],
        }
        for index in range(8)
    ]
    projection = build_public_projection(answer_result((document,), claims=claims))
    assert len(projection.citations) == 64 and len(projection.finding.claims) == 8
    assert sum(len(item.quote.excerpt or "") for item in projection.citations) == 1000
    assert len([item for item in projection.citations if item.quote.excerpt is None]) == 60
    assert projection.finding.claims[-1].citation_ids[-1] == "citation-64"


def test_utf8_projection_cap_counts_multibyte_bibliography_and_fails_closed():
    bib = SourceBibliography(authors=tuple(SourceAuthor(name="😀" * 300) for _ in range(20)))
    documents = tuple(source(f"source-{index}", bibliography=bib) for index in range(12))
    with pytest.raises(SharingProjectionError, match="public_projection_too_large"):
        build_public_projection(bridge_result(documents))


def test_identity_context_bound_does_not_silently_drop_connectors():
    result = bridge_result()
    context = SourceReference(source_id="unused", title="Unused", url="https://example.test/unused")
    with pytest.raises(SharingProjectionError, match="public_identity_context_limit"):
        build_public_projection(result, identity_context=(context for _ in range(257)))


@pytest.mark.parametrize(
    "kind,url",
    [
        ("doi", "https://doi.org/10.1234/test?private=token"),
        ("doi", "https://user:secret@doi.org/10.1234/test"),
        ("arxiv", "https://arxiv.org.evil/html/2401.01234v2"),
        ("arxiv", "https://arxiv.org/html/2401.01234"),
        ("openalex", "http://127.0.0.1/W123"),
        ("openalex", "https://openalex.org/W123#private"),
    ],
)
def test_public_links_reject_arbitrary_hosts_credentials_tokens_or_unversioned_html(kind, url):
    with pytest.raises(ValidationError):
        PublicScholarlyLink(kind=kind, url=url)


@pytest.mark.parametrize(
    "private_field", ["source_id", "run_id", "retrieval_id", "snapshot_id", "usage"]
)
def test_public_dto_rejects_private_fields_instead_of_silently_filtering_them(private_field):
    payload = build_public_projection(bridge_result()).model_dump(mode="json")
    payload[private_field] = "PRIVATE_SENTINEL"
    with pytest.raises(ValidationError, match="Extra inputs"):
        PublicFindingProjection.model_validate_json(json.dumps(payload))


def test_saved_dto_rejects_an_early_omission_or_shorter_than_allocated_excerpt():
    payload = build_public_projection(bridge_result()).model_dump(mode="json")
    quote = payload["citations"][0]["quote"]
    quote.update(
        excerpt=None,
        excerpted=True,
        excerpt_start=None,
        excerpt_end=None,
        excerpt_omission_reason="paper_limit",
    )
    with pytest.raises(ValidationError, match="deterministic paper budget"):
        PublicFindingProjection.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("location", ["paper", "citation", "finding", "quote"])
def test_private_nested_fields_are_forbidden_in_every_public_content_branch(location):
    payload = build_public_projection(bridge_result()).model_dump(mode="json")
    branch = {
        "paper": payload["papers"][0],
        "citation": payload["citations"][0],
        "finding": payload["finding"],
        "quote": payload["citations"][0]["quote"],
    }[location]
    branch["retrieval_id"] = PRIVATE_SNAPSHOT
    with pytest.raises(ValidationError, match="Extra inputs"):
        PublicFindingProjection.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "url,kind,expected",
    [
        ("https://doi.org/10.1234/SYNTHETIC", "doi", "https://doi.org/10.1234/synthetic"),
        (
            "https://arxiv.org/abs/hep-th/9901001v3",
            "arxiv",
            "https://arxiv.org/abs/hep-th/9901001v3",
        ),
        ("https://openalex.org/W123", "openalex", "https://openalex.org/W123"),
    ],
)
def test_known_scholarly_identifiers_can_be_recovered_from_cited_urls_without_echoing_paths(
    url, kind, expected
):
    first = source(url=url)
    projected = build_public_projection(bridge_result((first, source("other"))))
    assert [(link.kind, link.url) for link in projected.papers[0].links] == [(kind, expected)]


def test_token_bearing_scholarly_url_is_not_reused_as_a_public_link():
    first = source(url="https://doi.org/10.1234/synthetic?access=PRIVATE_ACCESS")
    projection = build_public_projection(bridge_result((first, source("other"))))
    assert projection.papers[0].links == ()
    assert "PRIVATE_ACCESS" not in canonical_projection_json(projection)
