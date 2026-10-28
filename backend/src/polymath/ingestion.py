"""Fenced free-literature ingestion, with no model calls or invented assessments.

Provider windows remain fixed while paging. Only an atomic successful page can
advance its continuation and add actually persisted source versions to a watch.
Saved metadata/abstracts are unassessed reading, never new scientific findings.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, func, select, text, tuple_
from sqlalchemy.orm import Session

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.assessment_eligibility import (
    EligibilityError,
    assessment_version_status,
    current_supported_condition,
    require_current_supported,
    result_assessment_state,
)
from polymath.evidence import EvidenceError, SourceArtifact, _canonical, _document, _hash
from polymath.evidence_models import (
    EvidenceResult,
    RunEvidenceSource,
    SourceRetrieval,
    SourceVersion,
)
from polymath.ingestion_models import (
    IngestionPage,
    IngestionProviderGate,
    IngestionTick,
    LiteratureWatch,
    WatchSeenSourceVersion,
)
from polymath.jobs import JobLease, _clear_lease, _event, _owned
from polymath.models import ResearchRun
from polymath.run_api import ACTIVE_STATES, QUEUE_LOCK_KEY

PROVIDERS = ("openalex", "arxiv")
WATCH_QUOTA_LOCK = 7_691_121_408
TERMINAL_STATES = ("succeeded", "failed", "cancelled")
PROVIDER_INTERVALS = {"openalex": 1.0, "arxiv": 3.0}
MAX_WATCH_SOURCE_VERSIONS = 2000


class IngestionError(Exception):
    """Static error code, never a query, source, or credential value."""


def _now(session: Session) -> datetime:
    return session.scalar(select(func.clock_timestamp()))


def _integer(value: int, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise IngestionError("invalid_watch_request")
    return value


def _text(value: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise IngestionError("invalid_watch_request")
    value = value.strip()
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise IngestionError("invalid_watch_request") from None
    if not 1 <= len(value) <= maximum or any(ord(character) < 32 for character in value):
        raise IngestionError("invalid_watch_request")
    return value


def _provider(value: str) -> str:
    if value not in PROVIDERS:
        raise IngestionError("invalid_ingestion_provider")
    return value


def _json(value: dict, maximum: int = 32768) -> dict:
    try:
        if not isinstance(value, dict):
            raise ValueError
        encoded = _canonical(value)
        if len(encoded.encode("utf-8")) > maximum:
            raise ValueError
        return json.loads(encoded)
    except (ValueError, TypeError, EvidenceError):
        raise IngestionError("invalid_ingestion_page") from None


def _browser(session: Session, owner_hash: str) -> BrowserSession:
    row = session.get(BrowserSession, owner_hash, populate_existing=True)
    if row is None or row.expires_at <= _now(session):
        raise IngestionError("watch_not_found")
    return row


@dataclass(frozen=True)
class ProviderCursor:
    since: datetime
    until: datetime
    cursor: str | int | None = None
    exhausted: bool = False
    revision: int = 0

    def to_dict(self) -> dict:
        if (
            not isinstance(self.since, datetime)
            or not isinstance(self.until, datetime)
            or (self.since.tzinfo is None or self.until.tzinfo is None)
        ):
            raise IngestionError("invalid_ingestion_cursor")
        return {
            "since": self.since.astimezone(UTC).isoformat(),
            "until": self.until.astimezone(UTC).isoformat(),
            "cursor": self.cursor,
            "exhausted": self.exhausted,
            "revision": self.revision,
        }


def _cursor(provider: str, value: ProviderCursor | dict) -> ProviderCursor:
    _provider(provider)
    try:
        data = value.to_dict() if isinstance(value, ProviderCursor) else _json(value, 8192)
        if set(data) != {"since", "until", "cursor", "exhausted", "revision"}:
            raise ValueError
        since, until = datetime.fromisoformat(data["since"]), datetime.fromisoformat(data["until"])
        if since.tzinfo is None or until.tzinfo is None or not since < until:
            raise ValueError
        if until - since > timedelta(days=31):
            raise ValueError
        continuation = data["cursor"]
        if provider == "openalex":
            if continuation is not None and (
                not isinstance(continuation, str)
                or not continuation.isascii()
                or not 1 <= len(continuation) <= 2000
                or any(ord(character) < 32 for character in continuation)
            ):
                raise ValueError
        elif continuation is not None:
            _integer(continuation, 0, 100_000)
        if type(data["exhausted"]) is not bool:
            raise ValueError
        revision = _integer(data["revision"], 0, 2**31 - 1)
        return ProviderCursor(since, until, continuation, data["exhausted"], revision)
    except (TypeError, ValueError, KeyError, AttributeError):
        raise IngestionError("invalid_ingestion_cursor") from None


@dataclass(frozen=True)
class WatchSnapshot:
    id: uuid.UUID
    label: str
    query: str
    enabled: bool
    interval_seconds: int
    next_due_at: datetime
    expires_at: datetime
    created_at: datetime
    updated_at: datetime
    source_result_id: uuid.UUID | None
    active_run_id: uuid.UUID | None
    active_status: str | None
    last_run_at: datetime | None
    source_count: int
    cursors: dict[str, ProviderCursor]

    @property
    def remaining_source_capacity(self) -> int:
        return max(0, MAX_WATCH_SOURCE_VERSIONS - self.source_count)

    def to_response(self) -> dict:
        return {
            "id": str(self.id),
            "label": self.label,
            "query": self.query,
            "enabled": self.enabled,
            "interval_seconds": self.interval_seconds,
            "next_due_at": self.next_due_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "source_result_id": str(self.source_result_id) if self.source_result_id else None,
            "active_run_id": str(self.active_run_id) if self.active_run_id else None,
            "active_status": self.active_status,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "source_count": self.source_count,
            "cursors": {provider: cursor.to_dict() for provider, cursor in self.cursors.items()},
            "coverage": "current_browser_session",
            "remaining_source_capacity": self.remaining_source_capacity,
            "source_limit": MAX_WATCH_SOURCE_VERSIONS,
        }


@dataclass(frozen=True)
class TickSnapshot:
    run_id: uuid.UUID
    watch_id: uuid.UUID
    status: str
    slot: datetime
    created_at: datetime
    updated_at: datetime
    added_sources: int
    diagnostics: dict
    completed_providers: tuple[str, ...]
    ingestion_status: str | None = None
    error_code: str | None = None

    @property
    def id(self) -> uuid.UUID:
        return self.run_id

    def to_response(self) -> dict:
        return {
            "id": str(self.run_id),
            "run_id": str(self.run_id),
            "watch_id": str(self.watch_id),
            "status": self.status,
            "slot": self.slot.isoformat(),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "added_sources": self.added_sources,
            "diagnostics": self.diagnostics,
            "completed_providers": list(self.completed_providers),
            "coverage": "bounded_provider_window",
            "ingestion_status": self.ingestion_status,
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class SourceSnapshot:
    id: uuid.UUID
    source_id: str
    title: str
    url: str
    content_kind: str
    content_sha256: str
    first_seen_at: datetime
    run_id: uuid.UUID
    assessment_status: str = "unassessed"
    assessment_root_result_id: uuid.UUID | None = None

    def to_response(self) -> dict:
        return {
            "id": str(self.id),
            "source_id": self.source_id,
            "title": self.title,
            "url": self.url,
            "content_kind": self.content_kind,
            "content_sha256": self.content_sha256,
            "first_seen_at": self.first_seen_at.isoformat(),
            "run_id": str(self.run_id),
            "assessment_status": self.assessment_status,
            "assessment_root_result_id": (
                str(self.assessment_root_result_id) if self.assessment_root_result_id else None
            ),
            "assessment_history_url": (
                f"/api/v1/assessments/heads/{self.assessment_root_result_id}/history"
                if self.assessment_root_result_id
                else None
            ),
        }


@dataclass(frozen=True)
class IngestionInput:
    watch: WatchSnapshot
    tick: TickSnapshot
    cursors: dict[str, ProviderCursor]
    completed_providers: tuple[str, ...]
    provider_results: dict


@dataclass(frozen=True)
class PageCommitSnapshot:
    added_sources: int
    sources_seen: int
    cursor: ProviderCursor


def _owned_watch(
    session: Session, owner_hash: str, watch_id: uuid.UUID, *, lock: bool = False
) -> LiteratureWatch:
    statement = select(LiteratureWatch).where(
        LiteratureWatch.id == watch_id, LiteratureWatch.owner_session_hash == owner_hash
    )
    if lock:
        statement = statement.with_for_update()
    row = session.scalar(statement.execution_options(populate_existing=True))
    if row is None:
        raise IngestionError("watch_not_found")
    _browser(session, owner_hash)
    return row


def _watch_snapshot(session: Session, row: LiteratureWatch) -> WatchSnapshot:
    active = session.get(ResearchRun, row.active_run_id) if row.active_run_id is not None else None
    return WatchSnapshot(
        row.id,
        row.label,
        row.query,
        row.enabled,
        row.interval_seconds,
        row.next_due_at,
        row.expires_at,
        row.created_at,
        row.updated_at,
        row.source_result_id,
        active.id if active and active.status not in TERMINAL_STATES else None,
        active.status if active and active.status not in TERMINAL_STATES else None,
        row.last_run_at,
        session.scalar(
            select(func.count())
            .select_from(WatchSeenSourceVersion)
            .where(WatchSeenSourceVersion.watch_id == row.id)
        ),
        {provider: _cursor(provider, row.provider_cursors[provider]) for provider in PROVIDERS},
    )


def _tick_snapshot(session: Session, tick: IngestionTick) -> TickSnapshot:
    run = session.get(ResearchRun, tick.run_id, populate_existing=True)
    pages = session.scalars(
        select(IngestionPage)
        .where(IngestionPage.run_id == run.id)
        .order_by(IngestionPage.created_at, IngestionPage.id)
    ).all()
    diagnostics, completed = {}, set()
    for page in pages:
        diagnostics[page.provider] = _json(page.observation)["diagnostics"]
        if page.status == "succeeded":
            completed.add(page.provider)
    count = session.scalar(
        select(func.count())
        .select_from(WatchSeenSourceVersion)
        .where(WatchSeenSourceVersion.run_id == run.id)
    )
    outcome = run.outcome if isinstance(run.outcome, dict) else {}
    status = outcome.get("ingestion_status")
    if status not in ("complete", "partial", "failed"):
        status = None
    code = outcome.get("error_code")
    if outcome.get("source_limit_reached") is True:
        code = "source_limit_reached"
    if code not in ("source_limit_reached", "all_providers_unavailable"):
        code = None
    return TickSnapshot(
        run.id,
        tick.watch_id,
        run.status,
        tick.slot,
        tick.created_at,
        run.updated_at,
        count,
        diagnostics,
        tuple(sorted(completed)),
        status,
        code,
    )


def _verified_cursors(
    session: Session, watch: LiteratureWatch, tick: IngestionTick | None
) -> dict[str, ProviderCursor]:
    expected = (
        dict(tick.initial_cursors)
        if tick is not None
        else {
            provider: ProviderCursor(
                watch.created_at - timedelta(days=7), watch.created_at
            ).to_dict()
            for provider in PROVIDERS
        }
    )
    if tick is not None:
        pages = session.scalars(
            select(IngestionPage).where(
                IngestionPage.run_id == tick.run_id, IngestionPage.status == "succeeded"
            )
        ).all()
        for page in pages:
            expected[page.provider] = page.observation["new_cursor"]
    if not isinstance(watch.provider_cursors, dict) or set(watch.provider_cursors) != set(
        PROVIDERS
    ):
        raise IngestionError("invalid_ingestion_cursor")
    cursors = {
        provider: _cursor(provider, watch.provider_cursors[provider]) for provider in PROVIDERS
    }
    if any(cursors[provider] != _cursor(provider, expected[provider]) for provider in PROVIDERS):
        raise IngestionError("ingestion_cursor_changed")
    if any(cursor.until > _now(session) for cursor in cursors.values()):
        raise IngestionError("invalid_ingestion_cursor")
    return cursors


def _worker_context(
    session: Session, lease: JobLease
) -> tuple[LiteratureWatch, IngestionTick, ResearchRun]:
    tick = session.get(IngestionTick, lease.run_id)
    if tick is None:
        raise IngestionError("ingestion_tick_not_found")
    # Every path needing both locks follows WATCH -> RUN. Source writes in the
    # EvidenceStore take only the run lock and never acquire a watch afterward.
    watch = session.scalar(
        select(LiteratureWatch)
        .where(LiteratureWatch.id == tick.watch_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if watch is None:
        raise IngestionError("watch_not_found")
    run = _owned(session, lease)
    _browser(session, watch.owner_session_hash)
    if run.task_kind != "ingest_literature" or watch.active_run_id != run.id:
        raise IngestionError("ingestion_tick_not_active")
    access = session.get(ResearchRunAccess, run.id)
    if access is None or access.session_hash != watch.owner_session_hash:
        raise IngestionError("watch_not_found")
    if not watch.enabled:
        raise IngestionError("watch_paused")
    if watch.expires_at <= _now(session):
        raise IngestionError("watch_expired")
    _verified_cursors(session, watch, tick)
    return watch, tick, run


class IngestionRepository:
    def __init__(self, engine: Engine):
        if engine.dialect.name != "postgresql":
            raise ValueError("Ingestion requires PostgreSQL transaction semantics")
        self.engine = engine

    def create(
        self,
        owner_hash: str,
        *,
        label: str,
        query: str,
        interval_seconds: int = 86400,
        source_result_id: uuid.UUID | None = None,
    ) -> WatchSnapshot:
        label, query = _text(label, 120), _text(query, 500)
        _integer(interval_seconds, 21600, 604800)
        with Session(self.engine) as session, session.begin():
            _browser(session, owner_hash)
            session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": WATCH_QUOTA_LOCK})
            browser = _browser(session, owner_hash)
            live = (
                select(func.count())
                .select_from(LiteratureWatch)
                .join(
                    BrowserSession, BrowserSession.token_hash == LiteratureWatch.owner_session_hash
                )
                .where(
                    LiteratureWatch.expires_at > _now(session),
                    BrowserSession.expires_at > _now(session),
                )
            )
            if session.scalar(live) >= 100:
                raise IngestionError("ingestion_watch_limit")
            if session.scalar(live.where(LiteratureWatch.owner_session_hash == owner_hash)) >= 10:
                raise IngestionError("ingestion_watch_limit")
            source = None
            if source_result_id is not None:
                source = session.scalar(
                    select(EvidenceResult)
                    .join(ResearchRun, ResearchRun.id == EvidenceResult.run_id)
                    .join(ResearchRunAccess, ResearchRunAccess.run_id == EvidenceResult.run_id)
                    .where(
                        EvidenceResult.id == source_result_id,
                        EvidenceResult.status.in_(("bridge", "answer")),
                        current_supported_condition(),
                        ResearchRun.task_kind.in_(("research", "reassess_finding")),
                        ResearchRunAccess.session_hash == owner_hash,
                    )
                )
                if source is None:
                    raise IngestionError("watch_not_found")
                try:
                    require_current_supported(session, owner_hash, source.id)
                except EligibilityError:
                    raise IngestionError("watch_not_found") from None
            now = _now(session)
            cursor = ProviderCursor(now - timedelta(days=7), now).to_dict()
            watch = LiteratureWatch(
                owner_session_hash=owner_hash,
                label=label,
                query=query,
                interval_seconds=interval_seconds,
                next_due_at=now,
                expires_at=browser.expires_at,
                provider_cursors={provider: dict(cursor) for provider in PROVIDERS},
                source_result_id=source.id if source else None,
                source_sha256=source.content_sha256 if source else None,
                created_at=now,
                updated_at=now,
            )
            session.add(watch)
            session.flush()
            return _watch_snapshot(session, watch)

    def get(self, owner_hash: str, watch_id: uuid.UUID) -> WatchSnapshot:
        with Session(self.engine) as session:
            return _watch_snapshot(session, _owned_watch(session, owner_hash, watch_id))

    def list(
        self, owner_hash: str, *, limit: int = 21, before: uuid.UUID | None = None
    ) -> tuple[WatchSnapshot, ...]:
        _integer(limit, 1, 21)
        with Session(self.engine) as session:
            _browser(session, owner_hash)
            statement = select(LiteratureWatch).where(
                LiteratureWatch.owner_session_hash == owner_hash
            )
            if before is not None:
                anchor = _owned_watch(session, owner_hash, before)
                statement = statement.where(
                    tuple_(LiteratureWatch.created_at, LiteratureWatch.id)
                    < tuple_(anchor.created_at, anchor.id)
                )
            rows = session.scalars(
                statement.order_by(
                    LiteratureWatch.created_at.desc(), LiteratureWatch.id.desc()
                ).limit(limit)
            ).all()
            return tuple(_watch_snapshot(session, row) for row in rows)

    def pause(self, owner_hash: str, watch_id: uuid.UUID) -> WatchSnapshot:
        with Session(self.engine) as session, session.begin():
            watch = _owned_watch(session, owner_hash, watch_id, lock=True)
            if watch.active_run_id is not None:
                run = session.scalar(
                    select(ResearchRun)
                    .where(ResearchRun.id == watch.active_run_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                _browser(session, owner_hash)
                if (
                    run is not None
                    and run.task_kind == "ingest_literature"
                    and run.status not in TERMINAL_STATES
                ):
                    run.lease_token += 1
                    run.status = "cancelled"
                    run.finished_at = _now(session)
                    _clear_lease(run)
                    _event(
                        session,
                        run,
                        f"watch_pause:{run.lease_token}",
                        "cancelled",
                        "Literature watch paused; no further reads or cursor writes are allowed.",
                    )
                watch.active_run_id = None
            watch.enabled = False
            watch.updated_at = _now(session)
            return _watch_snapshot(session, watch)

    def resume(self, owner_hash: str, watch_id: uuid.UUID) -> WatchSnapshot:
        with Session(self.engine) as session, session.begin():
            watch = _owned_watch(session, owner_hash, watch_id, lock=True)
            if watch.expires_at <= _now(session):
                raise IngestionError("watch_expired")
            watch.enabled = True
            watch.next_due_at = _now(session)
            watch.updated_at = _now(session)
            return _watch_snapshot(session, watch)

    @staticmethod
    def _enqueue_tick(session: Session, watch: LiteratureWatch, *, manual: bool) -> TickSnapshot:
        browser = _browser(session, watch.owner_session_hash)
        now = _now(session)
        if not watch.enabled:
            raise IngestionError("watch_paused")
        if watch.expires_at <= now or browser.expires_at <= now:
            raise IngestionError("watch_expired")
        if watch.active_run_id is not None:
            prior = session.get(ResearchRun, watch.active_run_id, populate_existing=True)
            if prior is not None and prior.status not in TERMINAL_STATES:
                raise IngestionError("ingestion_active_run_limit")
            watch.active_run_id = None
        if watch.last_run_at is not None and now < watch.last_run_at + timedelta(seconds=60):
            raise IngestionError("watch_check_throttled")
        source_count = session.scalar(
            select(func.count())
            .select_from(WatchSeenSourceVersion)
            .where(WatchSeenSourceVersion.watch_id == watch.id)
        )
        if source_count >= MAX_WATCH_SOURCE_VERSIONS:
            raise IngestionError("ingestion_watch_source_limit")
        active = (
            select(func.count())
            .select_from(ResearchRun)
            .where(ResearchRun.status.in_(ACTIVE_STATES))
        )
        if session.scalar(active) >= 100:
            raise IngestionError("ingestion_queue_full")
        own_active = active.join(
            ResearchRunAccess, ResearchRunAccess.run_id == ResearchRun.id
        ).where(ResearchRunAccess.session_hash == watch.owner_session_hash)
        if session.scalar(own_active) >= 1:
            raise IngestionError("ingestion_active_run_limit")
        cursors = {}
        latest_tick = session.scalar(
            select(IngestionTick)
            .where(IngestionTick.watch_id == watch.id)
            .order_by(IngestionTick.created_at.desc(), IngestionTick.run_id.desc())
            .limit(1)
        )
        _verified_cursors(session, watch, latest_tick)
        for provider in PROVIDERS:
            cursor = _cursor(provider, watch.provider_cursors[provider])
            if cursor.exhausted:
                start = cursor.until - timedelta(days=1)
                end = min(now, start + timedelta(days=31))
                if end <= start:
                    raise IngestionError("invalid_ingestion_cursor")
                # Starting the next window follows a successfully exhausted
                # prior window. Continuation/revision advance only on commit.
                cursor = ProviderCursor(start, end, None, False, cursor.revision)
            cursors[provider] = cursor.to_dict()
        _browser(session, watch.owner_session_hash)
        run = ResearchRun(mode="explore", task_kind="ingest_literature", goal=watch.query)
        session.add(run)
        session.flush()
        session.add(ResearchRunAccess(run_id=run.id, session_hash=watch.owner_session_hash))
        slot = now if manual else watch.next_due_at
        tick = IngestionTick(
            run_id=run.id, watch_id=watch.id, slot=slot, initial_cursors=cursors, created_at=now
        )
        session.add(tick)
        watch.provider_cursors = cursors
        watch.active_run_id = run.id
        watch.last_run_at = now
        watch.next_due_at = now + timedelta(seconds=watch.interval_seconds)
        watch.updated_at = now
        _event(
            session, run, "created", "queued", "Bounded literature reading queued; no model calls."
        )
        session.flush()
        return _tick_snapshot(session, tick)

    def run_now(self, owner_hash: str, watch_id: uuid.UUID) -> TickSnapshot:
        with Session(self.engine) as session, session.begin():
            _browser(session, owner_hash)
            session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": QUEUE_LOCK_KEY})
            watch = _owned_watch(session, owner_hash, watch_id, lock=True)
            return self._enqueue_tick(session, watch, manual=True)

    def schedule_due(self, *, limit: int = 5) -> tuple[TickSnapshot, ...]:
        _integer(limit, 1, 5)
        with Session(self.engine) as session, session.begin():
            session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": QUEUE_LOCK_KEY})
            now = _now(session)
            expired = session.scalars(
                select(LiteratureWatch)
                .join(
                    BrowserSession, BrowserSession.token_hash == LiteratureWatch.owner_session_hash
                )
                .where(
                    LiteratureWatch.active_run_id.is_not(None),
                    (LiteratureWatch.expires_at <= now) | (BrowserSession.expires_at <= now),
                )
                .order_by(LiteratureWatch.id)
                .with_for_update(of=LiteratureWatch, skip_locked=True)
                .limit(100)
            ).all()
            for watch in expired:
                run = session.scalar(
                    select(ResearchRun)
                    .where(ResearchRun.id == watch.active_run_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if (
                    run is not None
                    and run.task_kind == "ingest_literature"
                    and run.status not in TERMINAL_STATES
                ):
                    run.lease_token += 1
                    run.status = "cancelled"
                    run.finished_at = _now(session)
                    _clear_lease(run)
                    _event(
                        session,
                        run,
                        f"watch_expired:{run.lease_token}",
                        "cancelled",
                        "The literature watch expired; future free-provider reads were cancelled.",
                    )
                watch.active_run_id = None
                watch.enabled = False
                watch.updated_at = now
            busy_owner = (
                select(1)
                .select_from(ResearchRunAccess)
                .join(ResearchRun, ResearchRun.id == ResearchRunAccess.run_id)
                .where(
                    ResearchRunAccess.session_hash == LiteratureWatch.owner_session_hash,
                    ResearchRun.status.in_(ACTIVE_STATES),
                )
                .exists()
            )
            source_count = (
                select(func.count())
                .select_from(WatchSeenSourceVersion)
                .where(WatchSeenSourceVersion.watch_id == LiteratureWatch.id)
                .correlate(LiteratureWatch)
                .scalar_subquery()
            )
            rows = session.scalars(
                select(LiteratureWatch)
                .join(
                    BrowserSession, BrowserSession.token_hash == LiteratureWatch.owner_session_hash
                )
                .where(
                    LiteratureWatch.enabled.is_(True),
                    LiteratureWatch.next_due_at <= now,
                    LiteratureWatch.expires_at > now,
                    BrowserSession.expires_at > now,
                    ~busy_owner,
                    source_count < MAX_WATCH_SOURCE_VERSIONS,
                    LiteratureWatch.last_run_at.is_(None)
                    | (LiteratureWatch.last_run_at <= now - timedelta(seconds=60)),
                )
                .order_by(LiteratureWatch.next_due_at, LiteratureWatch.id)
                .with_for_update(of=LiteratureWatch, skip_locked=True)
                .limit(limit)
            ).all()
            ticks = []
            for watch in rows:
                try:
                    with session.begin_nested():
                        ticks.append(self._enqueue_tick(session, watch, manual=False))
                except IngestionError as error:
                    if str(error) not in {
                        "ingestion_active_run_limit",
                        "watch_check_throttled",
                        "watch_expired",
                        "watch_not_found",
                        "ingestion_queue_full",
                        "ingestion_watch_source_limit",
                    }:
                        raise
            return tuple(ticks)

    def enqueue_due(self, *, limit: int = 5) -> tuple[TickSnapshot, ...]:
        return self.schedule_due(limit=limit)

    def snapshot(self, lease: JobLease) -> IngestionInput:
        with Session(self.engine) as session, session.begin():
            watch, tick, _ = _worker_context(session, lease)
            saved = _tick_snapshot(session, tick)
            results = {}
            for provider, diagnostic in saved.diagnostics.items():
                results[provider] = {
                    "status": "ok" if diagnostic["status"] == "succeeded" else "failed",
                    "sources_seen": diagnostic.get("sources_seen", 0),
                    "added_sources": diagnostic.get("added_sources", 0),
                    **({"error_code": diagnostic["code"]} if diagnostic.get("code") else {}),
                }
            return IngestionInput(
                _watch_snapshot(session, watch),
                saved,
                {
                    provider: _cursor(provider, watch.provider_cursors[provider])
                    for provider in PROVIDERS
                },
                saved.completed_providers,
                results,
            )

    def check_owner(self, lease: JobLease) -> IngestionInput:
        return self.snapshot(lease)

    def acquire_provider_slot(self, lease: JobLease, provider: str) -> float:
        _provider(provider)
        with Session(self.engine) as session, session.begin():
            _worker_context(session, lease)
            gate = session.scalar(
                select(IngestionProviderGate)
                .where(IngestionProviderGate.provider == provider)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if gate is None:
                raise IngestionError("ingestion_provider_gate_unavailable")
            # Ownership may expire while another worker holds the global gate.
            watch, _, _ = _worker_context(session, lease)
            if (
                session.scalar(
                    select(func.count())
                    .select_from(WatchSeenSourceVersion)
                    .where(WatchSeenSourceVersion.watch_id == watch.id)
                )
                >= MAX_WATCH_SOURCE_VERSIONS
            ):
                raise IngestionError("ingestion_watch_source_limit")
            now = _now(session)
            if gate.next_allowed_at > now:
                return (gate.next_allowed_at - now).total_seconds()
            gate.next_allowed_at = now + timedelta(seconds=PROVIDER_INTERVALS[provider])
            gate.sequence += 1
            _browser(session, watch.owner_session_hash)
            return 0.0

    def commit_page(
        self,
        lease: JobLease,
        provider: str,
        expected_cursor: ProviderCursor | dict,
        new_cursor: str | int | None,
        source_artifacts: tuple[SourceArtifact, ...],
        diagnostics: dict | None = None,
        *,
        exhausted: bool = False,
    ) -> PageCommitSnapshot:
        _provider(provider)
        expected = _cursor(provider, expected_cursor)
        if (
            type(exhausted) is not bool
            or not isinstance(source_artifacts, (list, tuple))
            or len(source_artifacts) > 5
        ):
            raise IngestionError("invalid_ingestion_page")
        candidate = _cursor(
            provider,
            ProviderCursor(
                expected.since, expected.until, new_cursor, exhausted, expected.revision + 1
            ),
        )
        if not exhausted and (new_cursor is None or new_cursor == expected.cursor):
            raise IngestionError("invalid_ingestion_cursor")
        supplied = _json(diagnostics or {}, 8192)
        refs = []
        for artifact in source_artifacts:
            if not isinstance(artifact, SourceArtifact):
                raise IngestionError("invalid_ingestion_source")
            refs.append(
                {
                    "version_id": str(artifact.version_id),
                    "retrieval_id": str(artifact.retrieval_id),
                    "source_id": artifact.source_id,
                    "content_sha256": artifact.content_sha256,
                    "content_kind": artifact.content_kind,
                }
            )
        body = {
            "expected_cursor": expected.to_dict(),
            "new_cursor": candidate.to_dict(),
            "sources": refs,
            "diagnostics_input": supplied,
        }
        digest = _hash(_canonical(body))
        with Session(self.engine) as session, session.begin():
            watch, tick, run = _worker_context(session, lease)
            prior = session.scalar(
                select(IngestionPage).where(
                    IngestionPage.run_id == run.id,
                    IngestionPage.provider == provider,
                    IngestionPage.status == "succeeded",
                )
            )
            if prior is not None:
                if prior.page_sha256 != digest:
                    raise IngestionError("ingestion_page_conflict")
                saved = prior.observation["diagnostics"]
                return PageCommitSnapshot(
                    saved["added_sources"],
                    saved["sources_seen"],
                    _cursor(provider, prior.observation["new_cursor"]),
                )
            if (
                _cursor(provider, tick.initial_cursors[provider]) != expected
                or _cursor(provider, watch.provider_cursors[provider]) != expected
            ):
                raise IngestionError("ingestion_cursor_changed")
            added, distinct = 0, set()
            existing_count = session.scalar(
                select(func.count())
                .select_from(WatchSeenSourceVersion)
                .where(WatchSeenSourceVersion.watch_id == watch.id)
            )
            for artifact in source_artifacts:
                document = session.execute(
                    select(SourceVersion, SourceRetrieval)
                    .join(SourceRetrieval, SourceRetrieval.version_id == SourceVersion.id)
                    .join(RunEvidenceSource, RunEvidenceSource.retrieval_id == SourceRetrieval.id)
                    .where(
                        RunEvidenceSource.run_id == run.id,
                        SourceVersion.id == artifact.version_id,
                        SourceRetrieval.id == artifact.retrieval_id,
                    )
                ).first()
                if document is None or (
                    document[0].source_id,
                    document[0].content_sha256,
                    document[0].content_kind,
                ) != (artifact.source_id, artifact.content_sha256, artifact.content_kind):
                    raise IngestionError("ingestion_source_not_read")
                if artifact.version_id in distinct:
                    continue
                distinct.add(artifact.version_id)
                if session.get(WatchSeenSourceVersion, (watch.id, artifact.version_id)) is None:
                    session.add(
                        WatchSeenSourceVersion(
                            watch_id=watch.id,
                            version_id=artifact.version_id,
                            retrieval_id=artifact.retrieval_id,
                            run_id=run.id,
                            provider=provider,
                        )
                    )
                    added += 1
                    if existing_count + added > MAX_WATCH_SOURCE_VERSIONS:
                        raise IngestionError("ingestion_watch_source_limit")
            diagnostic = {
                **supplied,
                "status": "succeeded",
                "sources_seen": len(distinct),
                "added_sources": added,
                "exhausted": exhausted,
                "since": expected.since.isoformat(),
                "until": expected.until.isoformat(),
            }
            body["diagnostics"] = diagnostic
            session.add(
                IngestionPage(
                    run_id=run.id,
                    provider=provider,
                    status="succeeded",
                    page_sha256=digest,
                    observation=_json(body),
                    created_at=_now(session),
                )
            )
            # Cursor advancement and first-seen links commit together, after
            # every referenced source was actually read by this fenced run.
            watch.provider_cursors = {**watch.provider_cursors, provider: candidate.to_dict()}
            watch.updated_at = _now(session)
            _worker_context(session, lease)
            _event(
                session,
                run,
                f"ingestion:{provider}:{expected.revision}",
                "literature_page_saved",
                f"Saved a bounded {provider} page; added {added} unassessed source versions.",
            )
            session.flush()
            return PageCommitSnapshot(added, len(distinct), candidate)

    def record_provider_failure(
        self, lease: JobLease, provider: str, expected_cursor: ProviderCursor | dict, code: str
    ) -> None:
        _provider(provider)
        expected = _cursor(provider, expected_cursor)
        if not isinstance(code, str) or re.fullmatch(r"[a-z_]{1,80}", code) is None:
            raise IngestionError("invalid_ingestion_diagnostic")
        with Session(self.engine) as session, session.begin():
            watch, tick, run = _worker_context(session, lease)
            if (
                _cursor(provider, watch.provider_cursors[provider]) != expected
                or _cursor(provider, tick.initial_cursors[provider]) != expected
            ):
                raise IngestionError("ingestion_cursor_changed")
            observation = {
                "expected_cursor": expected.to_dict(),
                "diagnostics": {
                    "status": "failed",
                    "code": code,
                    "sources_seen": 0,
                    "added_sources": 0,
                    "exhausted": False,
                    "since": expected.since.isoformat(),
                    "until": expected.until.isoformat(),
                },
            }
            digest = _hash(_canonical(observation))
            prior = session.scalar(
                select(IngestionPage.id).where(
                    IngestionPage.run_id == run.id,
                    IngestionPage.provider == provider,
                    IngestionPage.page_sha256 == digest,
                )
            )
            if prior is None:
                session.add(
                    IngestionPage(
                        run_id=run.id,
                        provider=provider,
                        status="failed",
                        page_sha256=digest,
                        observation=observation,
                        created_at=_now(session),
                    )
                )
                session.flush()
                _worker_context(session, lease)
                _event(
                    session,
                    run,
                    f"ingestion_failure:{provider}:{digest}",
                    "literature_provider_failed",
                    f"The {provider} page failed; its saved cursor was not advanced.",
                )

    def list_ticks(
        self,
        owner_hash: str,
        watch_id: uuid.UUID,
        *,
        limit: int = 21,
        before: uuid.UUID | None = None,
    ) -> tuple[TickSnapshot, ...]:
        _integer(limit, 1, 21)
        with Session(self.engine) as session:
            _owned_watch(session, owner_hash, watch_id)
            statement = select(IngestionTick).where(IngestionTick.watch_id == watch_id)
            if before is not None:
                anchor = session.get(IngestionTick, before)
                if anchor is None or anchor.watch_id != watch_id:
                    raise IngestionError("watch_not_found")
                statement = statement.where(
                    tuple_(IngestionTick.created_at, IngestionTick.run_id)
                    < tuple_(anchor.created_at, anchor.run_id)
                )
            ticks = session.scalars(
                statement.order_by(
                    IngestionTick.created_at.desc(), IngestionTick.run_id.desc()
                ).limit(limit)
            ).all()
            return tuple(_tick_snapshot(session, tick) for tick in ticks)

    def list_sources(
        self,
        owner_hash: str,
        watch_id: uuid.UUID,
        *,
        limit: int = 21,
        before: uuid.UUID | None = None,
    ) -> tuple[SourceSnapshot, ...]:
        _integer(limit, 1, 21)
        with Session(self.engine) as session:
            watch = _owned_watch(session, owner_hash, watch_id)
            root_id = (
                uuid.UUID(
                    result_assessment_state(session, owner_hash, watch.source_result_id)[
                        "root_result_id"
                    ]
                )
                if watch.source_result_id is not None
                else None
            )
            statement = (
                select(WatchSeenSourceVersion, SourceVersion, SourceRetrieval)
                .join(SourceVersion, SourceVersion.id == WatchSeenSourceVersion.version_id)
                .join(SourceRetrieval, SourceRetrieval.id == WatchSeenSourceVersion.retrieval_id)
                .where(WatchSeenSourceVersion.watch_id == watch_id)
            )
            if before is not None:
                anchor = session.get(WatchSeenSourceVersion, (watch_id, before))
                if anchor is None:
                    raise IngestionError("watch_not_found")
                statement = statement.where(
                    tuple_(WatchSeenSourceVersion.first_seen_at, WatchSeenSourceVersion.version_id)
                    < tuple_(anchor.first_seen_at, anchor.version_id)
                )
            rows = session.execute(
                statement.order_by(
                    WatchSeenSourceVersion.first_seen_at.desc(),
                    WatchSeenSourceVersion.version_id.desc(),
                ).limit(limit)
            ).all()
            return tuple(
                SourceSnapshot(
                    version.id,
                    version.source_id,
                    retrieval.title,
                    retrieval.url,
                    version.content_kind,
                    version.content_sha256,
                    seen.first_seen_at,
                    seen.run_id,
                    assessment_version_status(session, root_id, version.id)
                    if root_id is not None
                    else "unassessed",
                    root_id,
                )
                for seen, version, retrieval in rows
            )

    def get_source_document(
        self, owner_hash: str, watch_id: uuid.UUID, version_id: uuid.UUID
    ) -> dict:
        with Session(self.engine) as session:
            watch = _owned_watch(session, owner_hash, watch_id)
            seen = session.get(WatchSeenSourceVersion, (watch_id, version_id))
            if seen is None:
                raise IngestionError("watch_not_found")
            rows = session.execute(
                select(SourceVersion, SourceRetrieval)
                .join(SourceRetrieval, SourceRetrieval.version_id == SourceVersion.id)
                .join(RunEvidenceSource, RunEvidenceSource.retrieval_id == SourceRetrieval.id)
                .where(
                    SourceVersion.id == seen.version_id,
                    SourceRetrieval.id == seen.retrieval_id,
                    RunEvidenceSource.run_id == seen.run_id,
                )
            ).first()
            if rows is None:
                raise IngestionError("watch_not_found")
            version, retrieval = rows
            root_id = (
                uuid.UUID(
                    result_assessment_state(session, owner_hash, watch.source_result_id)[
                        "root_result_id"
                    ]
                )
                if watch.source_result_id is not None
                else None
            )
            return {
                "id": str(version.id),
                "watch_id": str(watch_id),
                "run_id": str(seen.run_id),
                "version_id": str(version.id),
                "retrieval_id": str(retrieval.id),
                "content_sha256": version.content_sha256,
                "metadata_sha256": retrieval.metadata_sha256,
                "document": _document(version, retrieval).model_dump(mode="json"),
                "assessment_status": assessment_version_status(session, root_id, version.id)
                if root_id is not None
                else "unassessed",
                "assessment_root_result_id": str(root_id) if root_id else None,
                "assessment_history_url": (
                    f"/api/v1/assessments/heads/{root_id}/history" if root_id else None
                ),
            }
