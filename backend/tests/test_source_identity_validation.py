"""Independence checks count known papers, not URLs or content versions."""

import pytest
from test_artifact_controller import artifact_basis, document_draft
from test_evidence import document, result_for

from polymath.artifact_contracts import ArtifactResult
from polymath.artifacts import render_artifact
from polymath.research.controller import _verify_answer_evidence, _verify_evidence
from polymath.research.models import (
    AnswerClaim,
    AnswerFinding,
    ClaimQuote,
    SourceAuthor,
    SourceBibliography,
    Usage,
)


def versions():
    return (
        document(
            "arxiv:2401.00001v1",
            url="https://arxiv.org/abs/2401.00001v1",
            bibliography=SourceBibliography(doi="10.1234/fixture", arxiv_id="2401.00001"),
        ),
        document(
            "fixture:full-text",
            url="https://arxiv.org/html/2401.00001v2",
            content_kind="full_text",
            bibliography=SourceBibliography(arxiv_id="2401.00001", arxiv_version=2),
        ),
    )


def test_controller_rejects_abstract_and_full_text_of_one_known_paper_as_bridge_evidence():
    left, right = versions()
    with pytest.raises(ValueError, match="distinct_sources_required"):
        _verify_evidence(
            result_for(left, right).finding,
            {doc.source_id: doc for doc in (left, right)},
            {"counterevidence", "prior_work"},
        )


def test_uncited_read_aliases_still_prevent_false_independence():
    left = document("fixture:doi", bibliography=SourceBibliography(doi="10.1234/fixture"))
    right = document("fixture:arxiv", bibliography=SourceBibliography(arxiv_id="2401.00001"))
    connector = document(
        "fixture:connector",
        bibliography=SourceBibliography(doi="10.1234/fixture", arxiv_id="2401.00001"),
    )
    with pytest.raises(ValueError, match="distinct_sources_required"):
        _verify_evidence(
            result_for(left, right).finding,
            {doc.source_id: doc for doc in (left, right, connector)},
            {"counterevidence", "prior_work"},
        )


@pytest.mark.parametrize("label", ["postulated", "known"])
def test_postulated_answer_requires_independent_papers_while_known_claim_can_cite_one(label):
    docs = versions()
    claim = AnswerClaim(
        id="fixture",
        text="Synthetic fixture claim, not scientific evidence.",
        label=label,
        derivation="Synthetic derivation." if label == "postulated" else None,
        evidence=[
            ClaimQuote(source_id=doc.source_id, quote=doc.text, supports="Synthetic fixture")
            for doc in docs
        ],
    )
    finding = AnswerFinding(
        outcome="answer",
        question="What is supported by these synthetic sources?",
        summary="Synthetic fixture only.",
        claims=[claim],
        limitations=["No real scientific claim."],
    )
    sources = {doc.source_id: doc for doc in docs}
    if label == "postulated":
        with pytest.raises(ValueError, match="postulated_requires_distinct_sources"):
            _verify_answer_evidence(finding, sources, {"counterevidence", "prior_work"})
    else:
        with pytest.raises(ValueError, match="selected_paper_version_conflict"):
            _verify_answer_evidence(finding, sources, {"counterevidence", "prior_work"})
        # A Known model label can cite one paper, while preserving one primary
        # version instead of mixing its abstract and a different full-text version.
        single = finding.model_copy(
            update={"claims": (claim.model_copy(update={"evidence": (claim.evidence[0],)}),)}
        )
        verified = _verify_answer_evidence(single, sources, {"counterevidence", "prior_work"})
        assert len(verified) == 1 and verified[0].source_id == docs[0].source_id


def test_generated_references_include_only_available_escaped_provider_metadata():
    basis = artifact_basis()
    metadata = SourceBibliography(
        authors=(SourceAuthor(name="Synthetic <Author>"),),
        publication_year=2026,
        venue="Synthetic **Venue**",
        doi="10.1234/fixture",
        arxiv_id="2401.00001",
        arxiv_version=2,
    )
    passages = (
        basis.passages[0].model_copy(update={"bibliography": metadata}),
        *basis.passages[1:],
    )
    basis = basis.model_copy(update={"passages": passages})
    result = ArtifactResult(
        kind="write_summary", status="ready", draft=document_draft(), usage=Usage()
    )
    markdown = render_artifact(result, basis)
    assert "Publication year: 2026." in markdown
    assert r"DOI: 10\.1234/fixture." in markdown
    assert r"arXiv: 2401\.00001v2." in markdown
    assert "<Author>" not in markdown
    assert "**Venue**" not in markdown
    assert markdown.count("Authors:") == 1
