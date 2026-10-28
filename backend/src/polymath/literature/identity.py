"""Conservative paper aliases; content versions are not independent papers.

Aliases reflect provider-declared identifiers and exact source URLs. They are
not a global identity registry or a guarantee that every alias is correct. No
title matching, network requests, or source-ID rewrites occur here. Independence
checks use transitive overlap across all supplied references, rather than one
preferred identifier or a pairwise comparison alone.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

if TYPE_CHECKING:
    from polymath.research.models import SourceReference

MAX_SELECTED_REFERENCES = 200
MAX_IDENTITY_REFERENCES = 256


def canonical_doi(value: str) -> str:
    from polymath.research.models import _canonical_doi

    return _canonical_doi(value)


def canonical_arxiv_id(value: str) -> tuple[str, int | None]:
    from polymath.research.models import _canonical_arxiv

    return _canonical_arxiv(value)


def canonical_openalex_id(value: str) -> str:
    from polymath.research.models import _canonical_openalex

    return _canonical_openalex(value)


def canonical_orcid(value: str) -> str:
    from polymath.research.models import _canonical_orcid

    return _canonical_orcid(value)


def normalized_source_url(value: str) -> str:
    """Normalize HTTPS authority/default port without guessing query semantics."""
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return value
        port = parsed.port
        host = parsed.hostname.lower()
        if ":" in host:
            host = "[" + host + "]"
        authority = host + (f":{port}" if port not in (None, 443) else "")
        return urlunsplit(("https", authority, parsed.path or "/", parsed.query, parsed.fragment))
    except (TypeError, ValueError):
        # An unrecognised historic URL can still support exact equality; it
        # never becomes a fetch destination through this helper.
        return value


def identifier_from_url(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in ("http", "https")
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 80 if parsed.scheme == "http" else 443)
            or parsed.query
            or parsed.fragment
        ):
            return None
        host = (parsed.hostname or "").lower()
        if host in ("doi.org", "dx.doi.org"):
            return "doi:" + canonical_doi(value)
        if host == "openalex.org":
            return "openalex:" + canonical_openalex_id(parsed.path.removeprefix("/"))
        if host in ("arxiv.org", "export.arxiv.org"):
            match = re.fullmatch(r"/(?:abs|pdf|html)/(.+)", parsed.path)
            if match is not None:
                identifier = match.group(1).removesuffix(".pdf")
                base, _ = canonical_arxiv_id(identifier)
                return "arxiv:" + base
    except (TypeError, ValueError):
        return None
    return None


def _identifier_from_source_id(value: str) -> str | None:
    try:
        if value.startswith("doi:"):
            return "doi:" + canonical_doi(value.removeprefix("doi:"))
        if value.startswith("arxiv:"):
            base, _ = canonical_arxiv_id(value.removeprefix("arxiv:"))
            return "arxiv:" + base
        if value.startswith("openalex:"):
            return "openalex:" + canonical_openalex_id(value.removeprefix("openalex:"))
    except ValueError:
        return None
    return None


def paper_aliases(reference: SourceReference) -> frozenset[str]:
    # Keep package import independent from research.__init__'s controller
    # exports; the controller itself consumes these pure identity helpers.
    from polymath.research.models import SourceBibliography, SourceReference

    if not isinstance(reference, SourceReference):
        raise ValueError("paper identity requires a validated source reference")
    aliases = {"url:" + normalized_source_url(reference.url)}
    known_id = _identifier_from_source_id(reference.source_id)
    aliases.add(known_id or "source:" + reference.source_id)
    known_url = identifier_from_url(reference.url)
    if known_url is not None:
        aliases.add(known_url)
    bibliography = reference.bibliography
    if bibliography is not None:
        if not isinstance(bibliography, SourceBibliography):
            raise ValueError("paper identity requires validated bibliography")
        if bibliography.doi is not None:
            aliases.add("doi:" + canonical_doi(bibliography.doi))
        if bibliography.arxiv_id is not None:
            base, _ = canonical_arxiv_id(bibliography.arxiv_id)
            aliases.add("arxiv:" + base)
        if bibliography.openalex_id is not None:
            aliases.add("openalex:" + canonical_openalex_id(bibliography.openalex_id))
    return frozenset(aliases)


def canonical_paper_id(reference: SourceReference) -> str:
    """Preferred display key only; independence uses all aliases, not this key."""
    aliases = paper_aliases(reference)
    for prefix in ("doi:", "arxiv:", "openalex:", "source:", "url:"):
        values = sorted(value for value in aliases if value.startswith(prefix))
        if values:
            return values[0]
    raise ValueError("paper identity has no usable alias")


def same_paper(left: SourceReference, right: SourceReference) -> bool:
    """Pairwise alias overlap; use distinct_paper_count for transitive groups."""
    return bool(paper_aliases(left) & paper_aliases(right))


def _selected_paper_groups(
    references: Iterable[SourceReference], *, context: Iterable[SourceReference] = ()
) -> tuple[tuple[SourceReference, ...], tuple[int, ...]]:
    """Union bounded identity context without granting it source-read authority."""
    owners: dict[str, int] = {}
    parents: list[int] = []
    selected_references = []

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for selected, values, maximum in (
        (True, references, MAX_SELECTED_REFERENCES),
        (False, context, MAX_IDENTITY_REFERENCES),
    ):
        for position, reference in enumerate(values):
            if position >= maximum:
                raise ValueError("paper identity reference limit exceeded")
            index = len(parents)
            parents.append(index)
            if selected:
                selected_references.append(reference)
            for alias in paper_aliases(reference):
                prior = owners.get(alias)
                if prior is None:
                    owners[alias] = index
                else:
                    parents[root(index)] = root(prior)
    return tuple(selected_references), tuple(
        root(index) for index in range(len(selected_references))
    )


def distinct_paper_count(
    references: Iterable[SourceReference], *, context: Iterable[SourceReference] = ()
) -> int:
    """Count selected papers after unioning aliases from all bounded context."""
    _, groups = _selected_paper_groups(references, context=context)
    return len(set(groups))


def validate_selected_paper_versions(
    selected: Iterable[SourceReference], *, context: Iterable[SourceReference] = ()
) -> None:
    """Allow multiple primary windows only from one exact snapshot per paper.

    Identity connectors can join selected sources into one paper group; they
    never become selected evidence. Separate windows still count as one paper.
    """
    references, groups = _selected_paper_groups(selected, context=context)
    grouped: dict[int, list[SourceReference]] = {}
    for reference, group in zip(references, groups, strict=True):
        grouped.setdefault(group, []).append(reference)
    for values in grouped.values():
        if len(values) < 2:
            continue
        snapshot_keys = set()
        source_ids = set()
        for reference in values:
            coverage = getattr(reference, "coverage", None)
            if coverage is None or getattr(reference, "content_kind", "full_text") != "full_text":
                raise ValueError("selected_paper_version_conflict")
            expected_url = f"https://arxiv.org/html/{coverage.arxiv_id}v{coverage.arxiv_version}"
            if reference.url != expected_url or reference.source_id in source_ids:
                raise ValueError("selected_paper_version_conflict")
            source_ids.add(reference.source_id)
            snapshot_keys.add(
                (
                    coverage.snapshot_id,
                    coverage.snapshot_sha256,
                    coverage.arxiv_id,
                    coverage.arxiv_version,
                    reference.url,
                )
            )
        if len(snapshot_keys) != 1:
            raise ValueError("selected_paper_version_conflict")
