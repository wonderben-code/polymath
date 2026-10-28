"""Fixed-host arXiv HTML transport and bounded plain-text extraction.

Only synthetic HTML/HTTP fixtures have been verified. Native arXiv HTML shape,
availability, and transport compatibility remain unverified. No PDF, script,
asset, link target, or provider/user supplied URL is fetched by this module.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser

import httpx

from polymath.full_text_contracts import (
    MAX_FULL_TEXT_CHARACTERS,
    MAX_FULL_TEXT_HEADINGS,
    ArxivFullTextLocator,
    ExtractedFullText,
    FullTextHeading,
)
from polymath.literature.clients import ArxivClient, parse_arxiv_records
from polymath.literature.errors import LiteratureError, ProviderError
from polymath.provider_pacing import ProviderPacingError, ProviderSlot
from polymath.research.models import _canonical_arxiv

MAX_HTML_BYTES = 2 * 1024 * 1024
FULL_TEXT_TIMEOUT_SECONDS = 20.0
MAX_HTML_NODES = 100_000
MAX_HTML_DEPTH = 128
MAX_MATH_CHARACTERS = 4000


class FullTextError(LiteratureError):
    """Static safe errors: no provider body, requested URL, or credentials."""


_BLOCKS = frozenset(
    (
        "article",
        "p",
        "div",
        "section",
        "li",
        "table",
        "tr",
        "td",
        "th",
        "blockquote",
        "pre",
        "dl",
        "dt",
        "dd",
    )
    + tuple(f"h{level}" for level in range(1, 7))
)
_OMIT = frozenset(
    (
        "script",
        "style",
        "nav",
        "footer",
        "iframe",
        "object",
        "embed",
        "svg",
        "canvas",
        "form",
        "audio",
        "video",
        "noscript",
        "template",
    )
)
_VOID = frozenset(
    (
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    )
)


@dataclass
class _Frame:
    tag: str
    skip: bool
    root: bool = False
    heading_start: int | None = None


class _ArxivParser(HTMLParser):
    def __init__(self, locator: ArxivFullTextLocator):
        super().__init__(convert_charrefs=True)
        self.locator = locator
        self.expected_url = f"https://arxiv.org/html/{locator.arxiv_id}v{locator.arxiv_version}"
        self.frames: list[_Frame] = []
        self.characters: list[str] = []
        self.pending_space = False
        self.nodes = 0
        self.roots = 0
        self.root_open = False
        self.root_closed = False
        self.identity_found = False
        self.heads = 0
        self.headings: list[FullTextHeading] = []
        self.headings_truncated = False
        self.long_heading = False
        self.math: dict | None = None
        self.missing_math = 0

    def _node(self):
        self.nodes += 1
        if self.nodes > MAX_HTML_NODES:
            raise FullTextError("full_text_structure_limit")

    def _append_character(self, character):
        if len(self.characters) >= MAX_FULL_TEXT_CHARACTERS:
            raise FullTextError("full_text_text_too_large")
        self.characters.append(character)

    def _write(self, text):
        for character in text:
            if character.isspace():
                self.pending_space = bool(self.characters and self.characters[-1] != "\n")
            else:
                if self.pending_space:
                    self._append_character(" ")
                self.pending_space = False
                self._append_character(character)

    def _boundary(self):
        self.pending_space = False
        if self.characters and self.characters[-1] != "\n":
            # A terminal separator is discarded by final rstrip. At the exact
            # cap it can be omitted; any further substantive text still fails.
            if len(self.characters) < MAX_FULL_TEXT_CHARACTERS:
                self._append_character("\n")

    def _identity(self, tag, attrs):
        # Only document-head metadata can identify this HTML response. A tag in
        # article/template prose cannot certify the response's exact version.
        ancestry = tuple(frame.tag for frame in self.frames)
        if self.roots or ancestry not in (("head",), ("html", "head")):
            return
        if tag == "meta" and attrs.get("name", "").lower() == "citation_arxiv_id":
            value = attrs.get("content", "")
            if value.startswith("arXiv:"):
                value = value[6:]
            try:
                base, version = _canonical_arxiv(value)
            except ValueError:
                raise FullTextError("full_text_version_mismatch") from None
            if (base, version) != (self.locator.arxiv_id, self.locator.arxiv_version):
                raise FullTextError("full_text_version_mismatch")
            self.identity_found = True
        if tag == "link" and "canonical" in attrs.get("rel", "").lower().split():
            if attrs.get("href") != self.expected_url:
                raise FullTextError("full_text_version_mismatch")
            self.identity_found = True

    def handle_starttag(self, tag, attrs):
        self._node()
        if len(attrs) > 64 or any(len(value or "") > 16_000 for _, value in attrs):
            raise FullTextError("full_text_structure_limit")
        if len({key for key, _ in attrs}) != len(attrs):
            raise FullTextError("full_text_invalid_html")
        attrs = {key: value or "" for key, value in attrs}
        if tag == "head":
            self.heads += 1
            if self.heads != 1 or tuple(frame.tag for frame in self.frames) not in ((), ("html",)):
                raise FullTextError("full_text_invalid_html")
        self._identity(tag, attrs)
        parent_skip = bool(self.frames and self.frames[-1].skip)
        root = tag == "article" and "ltx_document" in attrs.get("class", "").split()
        if root:
            self.roots += 1
            if self.roots != 1 or parent_skip:
                raise FullTextError("full_text_invalid_html")
            self.root_open = True
        skip = parent_skip or tag in _OMIT
        frame = _Frame(tag, skip, root)
        if self.root_open and not skip:
            if self.math is not None:
                if tag == "math":
                    raise FullTextError("full_text_invalid_html")
                if tag == "annotation":
                    self.math["annotation_open"] = attrs.get("encoding", "").lower() in (
                        "application/x-tex",
                        "application/x-latex",
                    )
            elif tag == "math":
                self.math = {
                    "alt": attrs.get("alttext"),
                    "annotation": [],
                    "annotation_open": False,
                }
            elif tag == "br":
                self._boundary()
            elif tag in _BLOCKS:
                self._boundary()
                if tag in {f"h{level}" for level in range(1, 7)}:
                    frame.heading_start = len(self.characters)
        if tag not in _VOID:
            self.frames.append(frame)
            if len(self.frames) > MAX_HTML_DEPTH:
                raise FullTextError("full_text_structure_limit")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        self._node()
        position = next(
            (
                index
                for index in range(len(self.frames) - 1, -1, -1)
                if self.frames[index].tag == tag
            ),
            None,
        )
        if position is None:
            return
        closing = self.frames[position:]
        del self.frames[position:]
        for frame in reversed(closing):
            if self.math is not None and not frame.skip:
                if frame.tag == "annotation":
                    self.math["annotation_open"] = False
                if frame.tag == "math":
                    latex = self.math["alt"] or "".join(self.math["annotation"])
                    latex = " ".join(latex.split())
                    self.math = None
                    if not latex:
                        self.missing_math += 1
                        self._write(" [unavailable equation] ")
                    elif len(latex) > MAX_MATH_CHARACTERS:
                        raise FullTextError("full_text_math_limit")
                    else:
                        self._write(" [math: " + latex + "] ")
            if self.root_open and not frame.skip and self.math is None:
                if frame.heading_start is not None:
                    title = "".join(self.characters[frame.heading_start :]).rstrip()
                    if title:
                        if len(self.headings) < MAX_FULL_TEXT_HEADINGS:
                            if len(title) > 200:
                                self.long_heading = True
                            title = title[:200].rstrip()
                            self.headings.append(
                                FullTextHeading(
                                    title=title,
                                    level=int(frame.tag[1]),
                                    start=frame.heading_start,
                                    end=frame.heading_start + len(title),
                                )
                            )
                        else:
                            self.headings_truncated = True
                if frame.tag in _BLOCKS:
                    self._boundary()
            if frame.root:
                self.root_open = False
                self.root_closed = True

    def handle_data(self, data):
        self._node()
        if not self.root_open or (self.frames and self.frames[-1].skip):
            return
        if self.math is not None:
            if self.math["annotation_open"]:
                if sum(map(len, self.math["annotation"])) + len(data) > MAX_MATH_CHARACTERS:
                    raise FullTextError("full_text_math_limit")
                self.math["annotation"].append(data)
            return
        self._write(data)


def extract_arxiv_html(
    payload: bytes, locator: ArxivFullTextLocator, retrieved_at: datetime
) -> ExtractedFullText:
    """Parse one bounded version-identified article; never silently truncate its body."""
    try:
        locator = ArxivFullTextLocator.model_validate_json(locator.model_dump_json())
        if locator.arxiv_version is None or not isinstance(payload, bytes):
            raise ValueError
        if len(payload) > MAX_HTML_BYTES:
            raise FullTextError("full_text_response_too_large")
        html = payload.decode("utf-8", errors="strict")
        if "\x00" in html or "<!ENTITY" in html.upper():
            raise ValueError
        parser = _ArxivParser(locator)
        parser.feed(html)
        parser.close()
        if not parser.identity_found:
            raise FullTextError("full_text_version_unverified")
        if not parser.root_closed or parser.math is not None:
            raise FullTextError("full_text_invalid_html")
        text = "".join(parser.characters).rstrip()
        limitations = [
            "HTML article text only; figures, assets, and non-article navigation are omitted.",
            "Mathematical alttext/TeX is plain extracted text; its semantics are unverified.",
            "Table layout is flattened; row/column associations require checking "
            "the original paper.",
        ]
        if parser.missing_math:
            limitations.append(
                f"{parser.missing_math} equations lacked supported alttext/TeX "
                "and have explicit unavailable markers."
            )
        if parser.headings_truncated:
            limitations.append(
                "Only the first80 headings are indexed; the saved article text is complete."
            )
        if parser.long_heading:
            limitations.append(
                "Heading titles longer than200 characters are indexed by exact prefixes."
            )
        return ExtractedFullText(
            arxiv_id=locator.arxiv_id,
            arxiv_version=locator.arxiv_version,
            url=f"https://arxiv.org/html/{locator.arxiv_id}v{locator.arxiv_version}",
            text=text,
            raw_html_sha256=hashlib.sha256(payload).hexdigest(),
            retrieved_at=retrieved_at,
            headings=tuple(parser.headings),
            headings_truncated=parser.headings_truncated,
            limitations=tuple(limitations),
        )
    except FullTextError:
        raise
    except Exception:
        raise FullTextError("full_text_invalid_html") from None


class ArxivHtmlClient:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        provider_slot: ProviderSlot | None = None,
    ):
        self._transport = transport
        self._provider_slot = provider_slot

    async def resolve(self, locator: ArxivFullTextLocator) -> ArxivFullTextLocator:
        try:
            locator = ArxivFullTextLocator.model_validate_json(locator.model_dump_json())
            if locator.arxiv_version is not None:
                return locator
            async with asyncio.timeout(FULL_TEXT_TIMEOUT_SECONDS):
                payload, retrieved_at = await ArxivClient(
                    transport=self._transport, provider_slot=self._provider_slot
                )._request({"id_list": locator.arxiv_id, "max_results": 1})
                records = parse_arxiv_records(payload, retrieved_at, limit=1)
            if len(records) != 1:
                raise ValueError
            bibliography = records[0].reference.bibliography
            if (
                bibliography is None
                or bibliography.arxiv_id != locator.arxiv_id
                or bibliography.arxiv_version is None
            ):
                raise ValueError
            return ArxivFullTextLocator(
                arxiv_id=locator.arxiv_id, arxiv_version=bibliography.arxiv_version
            )
        except (TimeoutError, httpx.TimeoutException):
            raise FullTextError("full_text_timeout") from None
        except ProviderError as error:
            raise FullTextError("full_text_" + error.code) from None
        except (ValueError, TypeError, AttributeError):
            raise FullTextError("full_text_version_unavailable") from None

    async def fetch(self, locator: ArxivFullTextLocator) -> ExtractedFullText:
        try:
            locator = ArxivFullTextLocator.model_validate_json(locator.model_dump_json())
            if locator.arxiv_version is None:
                raise FullTextError("full_text_version_required")
            async with asyncio.timeout(FULL_TEXT_TIMEOUT_SECONDS):
                if self._provider_slot is not None:
                    await self._provider_slot("arxiv")
                async with httpx.AsyncClient(
                    transport=self._transport,
                    timeout=httpx.Timeout(15.0, connect=5.0),
                    follow_redirects=False,
                ) as client:
                    async with client.stream(
                        "GET",
                        f"https://arxiv.org/html/{locator.arxiv_id}v{locator.arxiv_version}",
                        headers={
                            "Accept": "text/html",
                            "Accept-Encoding": "identity",
                            "User-Agent": "Polymath/0.1 (open-literature research)",
                        },
                    ) as response:
                        code = {
                            401: "access_denied",
                            403: "access_denied",
                            404: "source_unavailable",
                            410: "source_unavailable",
                            429: "rate_limited",
                        }.get(response.status_code)
                        if code:
                            raise FullTextError("full_text_" + code)
                        if response.status_code != 200:
                            raise FullTextError("full_text_invalid_response")
                        if (
                            response.headers.get("content-type", "")
                            .split(";", 1)[0]
                            .strip()
                            .lower()
                            != "text/html"
                            or response.headers.get("content-encoding", "identity").lower()
                            != "identity"
                        ):
                            raise FullTextError("full_text_invalid_response")
                        length = response.headers.get("content-length")
                        if length is not None and (
                            len(length) > 10 or not length.isascii() or not length.isdigit()
                        ):
                            raise FullTextError("full_text_invalid_response")
                        if length is not None and int(length) > MAX_HTML_BYTES:
                            raise FullTextError("full_text_response_too_large")
                        payload = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(payload) + len(chunk) > MAX_HTML_BYTES:
                                raise FullTextError("full_text_response_too_large")
                            payload.extend(chunk)
                        return extract_arxiv_html(bytes(payload), locator, datetime.now(UTC))
        except ProviderPacingError as error:
            code = "rate_limited" if error.code == "rate_limited" else "transport_error"
            raise FullTextError("full_text_" + code) from None
        except FullTextError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise FullTextError("full_text_timeout") from None
        except httpx.DecodingError:
            raise FullTextError("full_text_invalid_response") from None
        except httpx.HTTPError:
            raise FullTextError("full_text_transport_error") from None
        except (ValueError, TypeError, AttributeError):
            raise FullTextError("full_text_invalid_locator") from None
