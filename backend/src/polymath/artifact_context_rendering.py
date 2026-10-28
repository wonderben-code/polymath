"""Pure provenance Markdown from a saved basis; no DB, fetch or current-head reads.

The caller passes the existing export text/link escapers to avoid an artifacts
module import cycle. Public-sharing projections must not use this private data.
"""

from __future__ import annotations

from collections.abc import Callable

from polymath.artifact_contracts import ArtifactBasis
from polymath.scientific_context_contracts import StageOriginContext


def render_artifact_scientific_context(
    basis: ArtifactBasis, *, text: Callable[[str], str], link: Callable[[str], str]
) -> str:
    """Return an append-only provenance section, or exact empty text if absent."""
    if not isinstance(basis, ArtifactBasis) or not callable(text) or not callable(link):
        raise ValueError("scientific_context_render_input_invalid")
    basis = ArtifactBasis.model_validate_json(basis.model_dump_json())
    binding = basis.scientific_context
    if binding is None:
        return ""
    context = binding.context
    revision = context.source_revision
    lines = [
        "## Pinned scientific context",
        "",
        "Historical model hypotheses and assessments are provenance, not additional primary "
        "evidence or independent scientific verification. Scores are uncalibrated judgements.",
        "",
        f"Context SHA-256: `{binding.context_sha256}`.",
        f"Source finding: `{context.source_reference.result_id}`; "
        f"run `{context.source_reference.run_id}`; "
        f"SHA-256 `{context.source_reference.content_sha256}`.",
        f"Finding revision at admission: {revision.revision}; "
        f"state at admission: {revision.state_at_admission}. "
        "This is the saved admission snapshot, not a fresh current-status lookup.",
        f"Root finding: `{revision.root_reference.result_id}`; "
        f"SHA-256 `{revision.root_reference.content_sha256}`.",
        "",
    ]

    def stage_assessment(origin: StageOriginContext) -> list[str]:
        receipt, assessment = origin.receipt, origin.receipt.assessment
        values = [
            f"Original stage `{receipt.stage_id}` "
            f"(ordinal {receipt.ordinal}, depth {receipt.depth}); "
            f"receipt SHA-256 `{origin.receipt_sha256}`.",
            f"Assessment applies only to original result `{receipt.result_id}`; "
            f"SHA-256 `{receipt.result_sha256}`.",
            "",
        ]
        if origin.relationship == "reassessed_descendant":
            values += [
                "The requested finding is a reassessed descendant. The following original-stage "
                "score is historical and is not the revised finding's confidence.",
                "",
            ]
        values += [
            f"Confidence: {text(assessment.confidence)}; {assessment.confidence_score}/100 "
            "(uncalibrated model judgement, not a probability).",
            text(assessment.confidence_rationale),
            "",
            "Novelty within bounded searched coverage: " + text(assessment.novelty) + ".",
            text(assessment.novelty_rationale),
            "",
        ]
        values += ["- " + text(value) for value in assessment.limitations]
        return values + [""]

    if context.stage_origin is not None:
        lines += ["### Original discovery-stage assessment", ""]
        lines += stage_assessment(context.stage_origin)

    lines += ["### Historical premise ancestry", ""]
    lines += [
        "These nodes retain exact saved findings. Their inclusion does not assert that each "
        "ancestor remains currently supported; revisions are not independent supporting premises.",
        "",
    ]
    for ordinal, node in enumerate(context.ancestry_nodes, start=1):
        title = getattr(node.finding, "title", None) or node.finding.question
        lines += [
            f"#### Ancestry node {ordinal}: {text(title)}",
            "",
            f"Finding `{node.reference.result_id}`; run `{node.reference.run_id}`; "
            f"SHA-256 `{node.reference.content_sha256}`.",
            "",
        ]
        # Avoid printing the source stage assessment twice. Parent stage scores
        # retain their own original result binding and scope as well.
        if node.stage_origin is not None and (
            context.stage_origin is None
            or node.stage_origin.receipt.stage_id != context.stage_origin.receipt.stage_id
        ):
            lines += stage_assessment(node.stage_origin)
        edges = [edge for edge in context.ancestry_edges if edge.child == node.reference]
        for edge in edges:
            values = (
                f"- Parent {edge.ordinal + 1}, {edge.kind}: finding `{edge.parent.result_id}`; "
                f"run `{edge.parent.run_id}`; SHA-256 `{edge.parent.content_sha256}`."
            )
            if edge.assessment_id is not None:
                values += (
                    f" Applied assessment `{edge.assessment_id}`; "
                    f"SHA-256 `{edge.assessment_sha256}`."
                )
            lines.append(values)
        if not edges:
            lines.append("No saved parent edge for this exact finding.")
        lines += [""]

    lines += ["### Applied reassessments at admission", ""]
    if not revision.applied_assessments:
        lines += ["No applied reassessment was recorded in this admission snapshot.", ""]
    for assessment in revision.applied_assessments:
        lines += [
            f"#### Revision {assessment.head_revision}: {assessment.disposition}",
            "",
            f"Assessment `{assessment.assessment_id}`; run `{assessment.run_id}`; "
            f"payload SHA-256 `{assessment.payload_sha256}`.",
            f"Prior finding `{assessment.prior_reference.result_id}`; "
            f"SHA-256 `{assessment.prior_reference.content_sha256}`.",
            "",
        ]
        if assessment.candidate_reference is not None:
            lines += [
                f"Applied replacement `{assessment.candidate_reference.result_id}`; "
                f"SHA-256 `{assessment.candidate_reference.content_sha256}`.",
                "",
            ]
        quote_ordinal = 0
        for statement in assessment.rationale:
            identifiers = []
            for _ in statement.citations:
                quote_ordinal += 1
                identifiers.append(f"[assessment {assessment.head_revision}.{quote_ordinal}]")
            lines += [text(statement.text) + " " + " ".join(identifiers), ""]
        for ordinal, quote in enumerate(assessment.quotes, start=1):
            scope = (
                "FULL TEXT EXCERPT"
                if quote.source.coverage
                else quote.content_kind.replace("_", " ")
            )
            lines += [
                f"**Assessment citation {assessment.head_revision}.{ordinal}: "
                f"{text(quote.source.title)}**",
                "",
                link(quote.source.url),
                "",
                f"Saved retrieval `{quote.retrieval_id}`; version `{quote.version_id}`; "
                f"source SHA-256 `{quote.source_sha256}`. "
                f"Read scope: {scope}. "
                f"Exact saved-source character span [{quote.start}, {quote.end}).",
                "",
                "Model interpretation: " + text(quote.supports),
                "",
            ]
            coverage = quote.source.coverage
            if coverage is not None:
                lines += [
                    f"Exact normalized paper window [{coverage.start}, {coverage.end}) of "
                    f"{coverage.total}; arXiv `{coverage.arxiv_id}v{coverage.arxiv_version}`. "
                    "This excerpt does not indicate that the whole paper was read.",
                    f"Full snapshot SHA-256 `{coverage.snapshot_sha256}`; "
                    f"snapshot `{coverage.snapshot_id}`.",
                    "Extracted HTML text may omit equations, figures and table layout. "
                    "Mathematical text may be incomplete or unverified.",
                    "",
                ]
            lines += ["> " + text(line) for line in quote.quote.splitlines()]
            lines += [""]
        lines += ["Assessment limitations:"]
        lines += ["- " + text(value) for value in assessment.limitations]
        lines += [""]

    prior = context.prior_formalisation
    if prior is not None:
        lines += [
            "### Incorporated formalisation provenance",
            "",
            f"Prior artifact `{prior.artifact_id}` remains an unverified conjecture/proof attempt.",
            f"Payload SHA-256 `{prior.payload_sha256}`; basis SHA-256 `{prior.basis_sha256}`.",
            (
                f"Its original context SHA-256: `{prior.context_sha256}`."
                if prior.context_sha256 is not None
                else "No scientific-context binding was recorded for that prior artifact."
            ),
            "Its original context is distinct from this artifact's saved admission context.",
            "",
        ]
    return "\n".join(lines).rstrip() + "\n"
