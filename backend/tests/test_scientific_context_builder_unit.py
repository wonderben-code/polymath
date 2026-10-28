"""Narrow no-database checks of saved-handle and exact-quote reconstruction."""

import uuid
from types import SimpleNamespace

import pytest
from test_answer_persistence import answer_result
from test_evidence import document, result_for

from polymath.evidence import _canonical, _hash
from polymath.evidence_models import EvidenceQuote, EvidenceResult
from polymath.scientific_context_builder import ScientificContextError, _Builder
from polymath.scientific_context_contracts import BoundAnswerDraft


def packet_builder(*, extra=True, result=None):
    if result is None:
        documents = [document("left"), document("right", text="Other synthetic evidence.")]
        result = result_for(*documents)
    else:
        documents = list(result.sources)
    if extra:
        documents.append(document("uncited", text="Private irrelevant uncited full source body."))
    run_id, result_id = uuid.uuid4(), uuid.uuid4()
    handles = [(uuid.uuid4(), uuid.uuid4()) for _ in documents]
    raw = result.model_dump(mode="json")
    raw["sources"] = []
    envelope = {
        "result": raw,
        "source_artifacts": [
            {"version_id": str(version), "retrieval_id": str(retrieval)}
            for version, retrieval in handles
        ],
    }
    row = EvidenceResult(
        id=result_id,
        run_id=run_id,
        status=result.status,
        payload=envelope,
        content_sha256=_hash(_canonical(envelope)),
    )
    quotes = [
        EvidenceQuote(
            result_id=result_id,
            run_id=run_id,
            ordinal=index,
            version_id=handles[
                next(i for i, doc in enumerate(documents) if doc.source_id == quote.source_id)
            ][0],
            retrieval_id=handles[
                next(i for i, doc in enumerate(documents) if doc.source_id == quote.source_id)
            ][1],
            side=quote.side,
            claim_id=quote.claim_id,
            quote=quote.quote,
            supports=quote.supports,
            start_offset=quote.start,
            end_offset=quote.end,
        )
        for index, quote in enumerate(result.quotes)
    ]

    class SavedRows:
        def all(self):
            return quotes

    class ReadOnlyQueries:
        def execute(self, query):
            return handles

        def scalars(self, query):
            return SavedRows()

    builder = _Builder(ReadOnlyQueries(), "a" * 64)
    builder.run = lambda identifier: SimpleNamespace(mode=result.mode)
    inspected = []

    def read_document(identifier, retrieval_id, version_id):
        index = handles.index((version_id, retrieval_id))
        inspected.append(index)
        return (SimpleNamespace(id=version_id), SimpleNamespace(id=retrieval_id), documents[index])

    builder.document = read_document
    return builder, row, quotes, handles, inspected


def test_only_exact_cited_bodies_are_loaded_not_uncited_envelope_handles():
    builder, row, _, _, inspected = packet_builder()
    result = builder.packet(row)
    assert [source.source_id for source in result.sources] == ["left", "right"]
    assert inspected == [0, 1]
    assert "Private irrelevant" not in result.model_dump_json()


def test_repeated_quote_reconstruction_cannot_substitute_a_different_saved_handle():
    builder, row, quotes, handles, _ = packet_builder()
    quotes[0].version_id, quotes[0].retrieval_id = handles[-1]
    with pytest.raises(ScientificContextError, match="artifact_context_integrity_failed"):
        builder.packet(row)


def test_envelope_duplicate_handles_rejected_without_reading_any_body():
    builder, row, _, _, inspected = packet_builder()
    row.payload["source_artifacts"].append(row.payload["source_artifacts"][0])
    with pytest.raises(ScientificContextError, match="artifact_context_integrity_failed"):
        builder.packet(row)
    assert inspected == []


def test_historical_packet_retains_exact_claim_bound_overview_metadata():
    original = answer_result()
    draft = BoundAnswerDraft(
        question=original.finding.question,
        claims=tuple(original.finding.claims),
        overview_claim_ids=("inference_1", "known_1"),
        limitations=tuple(original.finding.limitations),
        unanswered_questions=tuple(original.finding.unanswered_questions),
    )
    original = original.model_copy(
        update={"finding": draft.to_answer(), "overview_binding": draft.overview_binding()}
    )
    builder, row, _, _, _ = packet_builder(result=original)
    loaded = builder.packet(row)
    assert loaded.finding == original.finding
    assert loaded.overview_binding == original.overview_binding
    assert loaded.model_dump(mode="json")["overview_binding"]["claim_ids"] == [
        "inference_1",
        "known_1",
    ]
