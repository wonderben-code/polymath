"""Field-by-field public snapshots from exact, privately authorized saved evidence.

Callers load/hash-check immutable result/source/quote rows and all pinned identity
context. This module has no database/network access and grants no source-reading
authority. Uncited identity connectors affect excerpt budgets only, never output.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from urllib.parse import urlsplit

from polymath.literature.identity import canonical_arxiv_id, identifier_from_url, paper_aliases
from polymath.research.controller import _verify_surprise_fields
from polymath.research.models import (
    AnswerFinding,
    BridgeFinding,
    ResearchResult,
    SourceReference,
)
from polymath.sharing_contracts import (
    MAX_PUBLIC_BYTES,
    MAX_PUBLIC_EXCERPT,
    MAX_PUBLIC_PAPER_EXCERPTS,
    PublicAnswer,
    PublicAuthor,
    PublicBibliography,
    PublicBridge,
    PublicCitation,
    PublicClaim,
    PublicCoverage,
    PublicFieldSelection,
    PublicFindingProjection,
    PublicIdentifiers,
    PublicMapping,
    PublicPaper,
    PublicQuoteExcerpt,
    PublicScholarlyLink,
    canonical_public_doi,
)


class SharingProjectionError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def canonical_projection_json(projection: PublicFindingProjection) -> str:
    projection = PublicFindingProjection.model_validate_json(projection.model_dump_json())
    encoded = json.dumps(
        projection.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(encoded.encode("utf-8")) > MAX_PUBLIC_BYTES:
        raise SharingProjectionError("public_projection_too_large")
    return encoded


def projection_sha256(projection: PublicFindingProjection) -> str:
    return hashlib.sha256(canonical_projection_json(projection).encode("utf-8")).hexdigest()


def _context(reference: SourceReference) -> SourceReference:
    if not isinstance(reference, SourceReference):
        raise SharingProjectionError("public_identity_context_invalid")
    return SourceReference.model_validate_json(
        reference.model_dump_json(
            include={
                "source_id",
                "title",
                "url",
                "bibliography",
                "coverage",
            }
        )
    )


def _groups(sources, context):
    parents = list(range(len(sources) + len(context)))
    owners = {}

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for index, source in enumerate((*sources, *context)):
        for alias in paper_aliases(source):
            if alias in owners:
                parents[root(index)] = root(owners[alias])
            else:
                owners[alias] = index
    return {source.source_id: root(index) for index, source in enumerate(sources)}


def _identifiers(documents):
    selected = {}
    for document in documents:
        for alias in sorted(paper_aliases(document)):
            for prefix, key in (
                ("doi:", "doi"),
                ("arxiv:", "arxiv_id"),
                ("openalex:", "openalex_id"),
            ):
                if alias.startswith(prefix) and key not in selected:
                    value = alias[len(prefix) :]
                    if key == "doi":
                        try:
                            value = canonical_public_doi(value)
                        except ValueError:
                            # Keep private identifiers and their alias grouping,
                            # but never emit a URL browsers resolve differently.
                            continue
                    selected[key] = value
    base = selected.get("arxiv_id")
    versions = set()
    for document in documents:
        bib = document.bibliography
        if bib is not None and bib.arxiv_id == base and bib.arxiv_version is not None:
            versions.add(bib.arxiv_version)
        if document.coverage is not None and document.coverage.arxiv_id == base:
            versions.add(document.coverage.arxiv_version)
        if base is not None and identifier_from_url(document.url) == "arxiv:" + base:
            path = urlsplit(document.url).path
            identifier = path.split("/", 2)[-1].removesuffix(".pdf")
            _, version = canonical_arxiv_id(identifier)
            if version is not None:
                versions.add(version)
        if document.source_id.startswith("arxiv:"):
            try:
                source_base, version = canonical_arxiv_id(document.source_id[len("arxiv:") :])
            except ValueError:
                continue
            if source_base == base and version is not None:
                versions.add(version)
    if len(versions) == 1:
        selected["arxiv_version"] = next(iter(versions))
    return PublicIdentifiers(**selected)


def _paper(identifier, documents):
    source = documents[0]
    bib = source.bibliography
    bibliography = (
        None
        if bib is None
        else PublicBibliography(
            authors=tuple(
                PublicAuthor(name=author.name, orcid=author.orcid) for author in bib.authors[:20]
            ),
            authors_truncated=bib.authors_truncated or len(bib.authors) > 20,
            publication_year=bib.publication_year,
            publication_date=bib.publication_date,
            venue=bib.venue,
        )
    )
    identifiers = _identifiers(documents)
    links = []
    if identifiers.doi is not None:
        links.append(PublicScholarlyLink(kind="doi", url="https://doi.org/" + identifiers.doi))
    if identifiers.arxiv_id is not None:
        html = identifiers.arxiv_version is not None and any(
            source.coverage is not None
            and source.coverage.arxiv_id == identifiers.arxiv_id
            and source.coverage.arxiv_version == identifiers.arxiv_version
            for source in documents
        )
        links.append(
            PublicScholarlyLink(
                kind="arxiv",
                url=("https://arxiv.org/html/" if html else "https://arxiv.org/abs/")
                + identifiers.arxiv_id
                + (
                    f"v{identifiers.arxiv_version}" if identifiers.arxiv_version is not None else ""
                ),
            )
        )
    if identifiers.openalex_id is not None:
        links.append(
            PublicScholarlyLink(
                kind="openalex", url="https://openalex.org/" + identifiers.openalex_id
            )
        )
    return PublicPaper(
        id=identifier,
        title=source.title,
        bibliography=bibliography,
        identifiers=identifiers,
        links=tuple(links),
    )


def _coverage(source):
    value = source.coverage
    return (
        None
        if value is None
        else PublicCoverage(
            snapshot_sha256=value.snapshot_sha256,
            arxiv_id=value.arxiv_id,
            arxiv_version=value.arxiv_version,
            start=value.start,
            end=value.end,
            total=value.total,
        )
    )


def build_public_projection(
    result: ResearchResult,
    *,
    identity_context: Iterable[SourceReference] = (),
) -> PublicFindingProjection:
    """Preserve every finding/citation association while bounding emitted exact quotes."""
    if (
        not isinstance(result, ResearchResult)
        or not 1 <= len(result.sources) <= 200
        or not 1 <= len(result.quotes) <= 64
    ):
        raise SharingProjectionError("public_source_integrity_failed")
    try:
        result = ResearchResult.model_validate_json(result.model_dump_json())
        context = []
        for index, reference in enumerate(identity_context):
            if index >= 256:
                raise SharingProjectionError("public_identity_context_limit")
            context.append(_context(reference))
        finding = result.finding
        if (
            isinstance(finding, BridgeFinding)
            and result.status == "bridge"
            and result.mode != "ask"
        ):
            expected = [
                (item.source_id, item.side, None, item.quote, item.supports)
                for item in finding.evidence
            ]
        elif (
            isinstance(finding, AnswerFinding)
            and result.status == "answer"
            and result.mode == "ask"
        ):
            expected = [
                (item.source_id, "answer", claim.id, item.quote, item.supports)
                for claim in finding.claims
                for item in claim.evidence
            ]
        else:
            raise SharingProjectionError("public_positive_finding_required")
        if result.mode == "surprise":
            _verify_surprise_fields(finding, result.field_selections)
        elif result.field_selections:
            raise SharingProjectionError("public_source_integrity_failed")
        sources = {source.source_id: source for source in result.sources}
        if len(sources) != len(result.sources) or len(expected) != len(result.quotes):
            raise SharingProjectionError("public_source_integrity_failed")
        for declared, quote in zip(expected, result.quotes, strict=True):
            source = sources.get(quote.source_id)
            if (
                source is None
                or (quote.source_id, quote.side, quote.claim_id, quote.quote, quote.supports)
                != declared
                or quote.content_kind != source.content_kind
                or quote.source_sha256 != source.content_sha256
                or not 0 <= quote.start < quote.end <= len(source.text)
                or quote.end - quote.start != len(quote.quote)
                or source.text[quote.start : quote.end] != quote.quote
            ):
                raise SharingProjectionError("public_source_integrity_failed")
        grouping = _groups(result.sources, context)
        paper_ids, documents = {}, {}
        for quote in result.quotes:
            group = grouping[quote.source_id]
            if group not in paper_ids:
                paper_ids[group] = f"paper-{len(paper_ids) + 1}"
                documents[group] = []
            source = sources[quote.source_id]
            if all(prior.source_id != source.source_id for prior in documents[group]):
                documents[group].append(source)
        claims = (
            {claim.id: f"claim-{index + 1}" for index, claim in enumerate(finding.claims)}
            if isinstance(finding, AnswerFinding)
            else {}
        )
        remaining = dict.fromkeys(paper_ids, MAX_PUBLIC_PAPER_EXCERPTS)
        citations = []
        for index, quote in enumerate(result.quotes):
            group = grouping[quote.source_id]
            source = sources[quote.source_id]
            count = min(MAX_PUBLIC_EXCERPT, len(quote.quote), remaining[group])
            excerpt = quote.quote[:count] if count >= 10 else None
            remaining[group] -= len(excerpt or "")
            citations.append(
                PublicCitation(
                    id=f"citation-{index + 1}",
                    paper_id=paper_ids[group],
                    side=quote.side,
                    claim_id=claims.get(quote.claim_id),
                    supports=quote.supports,
                    source_kind=source.content_kind,
                    source_sha256=source.content_sha256,
                    quote=PublicQuoteExcerpt(
                        excerpt=excerpt,
                        excerpted=excerpt is None or len(excerpt) < len(quote.quote),
                        excerpt_start=quote.start if excerpt is not None else None,
                        excerpt_end=quote.start + len(excerpt) if excerpt is not None else None,
                        full_quote_start=quote.start,
                        full_quote_end=quote.end,
                        full_quote_chars=len(quote.quote),
                        full_quote_sha256=hashlib.sha256(quote.quote.encode("utf-8")).hexdigest(),
                        excerpt_omission_reason="paper_limit" if excerpt is None else None,
                    ),
                    coverage=_coverage(source),
                )
            )
        if isinstance(finding, BridgeFinding):
            public_finding = PublicBridge(
                title=finding.title,
                field_left=finding.field_left,
                field_right=finding.field_right,
                explanation=finding.explanation,
                mappings=tuple(
                    PublicMapping(left=item.left, right=item.right, assumption=item.assumption)
                    for item in finding.mappings
                ),
                open_question=finding.open_question,
                strongest_objection=finding.strongest_objection,
                objection_verdict=finding.objection_verdict,
                novelty_limitations=finding.novelty_limitations,
                citation_ids=tuple(item.id for item in citations),
            )
        else:
            public_finding = PublicAnswer(
                question=finding.question,
                summary=finding.summary,
                claims=tuple(
                    PublicClaim(
                        id=claims[claim.id],
                        text=claim.text,
                        label=claim.label,
                        derivation=claim.derivation,
                        caveats=tuple(claim.caveats),
                        citation_ids=tuple(
                            item.id for item in citations if item.claim_id == claims[claim.id]
                        ),
                    )
                    for claim in finding.claims
                ),
                limitations=tuple(finding.limitations),
                unanswered_questions=tuple(finding.unanswered_questions),
            )
        selection = result.field_selections[-1] if result.mode == "surprise" else None
        projection = PublicFindingProjection(
            mode=result.mode,
            finding=public_finding,
            papers=tuple(_paper(paper_ids[group], docs) for group, docs in documents.items()),
            citations=tuple(citations),
            field_selection=None
            if selection is None
            else PublicFieldSelection(fields=selection.fields, rationale=selection.rationale),
        )
        canonical_projection_json(projection)
        return projection
    except SharingProjectionError:
        raise
    except (ValueError, TypeError, AttributeError, KeyError, UnicodeError) as error:
        code = (
            "public_projection_too_large"
            if "public_projection_too_large" in str(error)
            else "public_source_integrity_failed"
        )
        raise SharingProjectionError(code) from None
