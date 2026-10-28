"""One PostgreSQL pacing gate shared by research and feed transports."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Literal

from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from polymath.ingestion_models import IngestionProviderGate

PROVIDER_INTERVALS = {"openalex": 1.0, "arxiv": 3.0}
Provider = Literal["openalex", "arxiv"]
ProviderSlot = Callable[[Provider], Awaitable[None]]


class ProviderPacingError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def acquire_provider_slot(
    session: Session,
    provider: str,
    *,
    authorize: Callable[[Session], None] | None = None,
) -> float:
    """Reserve only a ready slot; finish this transaction before external HTTP.

    A caller's job/session checks run before and after waiting for the shared
    gate. They may fence the current job, but must not obtain financial or
    sponsor locks. Existing ingestion retains its own watch checks this way.
    """
    if provider not in PROVIDER_INTERVALS:
        raise ValueError("invalid_provider")
    if authorize is not None:
        authorize(session)
    gate = session.scalar(
        select(IngestionProviderGate)
        .where(IngestionProviderGate.provider == provider)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if gate is None:
        raise ProviderPacingError("provider_gate_unavailable")
    if authorize is not None:
        authorize(session)
    now = session.scalar(select(func.clock_timestamp()))
    if gate.next_allowed_at > now:
        return (gate.next_allowed_at - now).total_seconds()
    gate.next_allowed_at = now + timedelta(seconds=PROVIDER_INTERVALS[provider])
    gate.sequence += 1
    return 0.0


class DatabaseProviderPacer:
    """Bounded async transport callback; no connection/lock survives its return."""

    def __init__(
        self,
        engine: Engine,
        *,
        authorize: Callable[[Session], None] | None = None,
        max_wait_seconds: float = 10.0,
    ):
        if (
            type(max_wait_seconds) not in (int, float)
            or not math.isfinite(max_wait_seconds)
            or not 0 < max_wait_seconds <= 30
            or engine.dialect.name != "postgresql"
        ):
            raise ValueError("invalid_provider_pacing_policy")
        self.engine, self.authorize, self.max_wait_seconds = engine, authorize, max_wait_seconds

    def _acquire(self, provider: Provider) -> float:
        try:
            with Session(self.engine) as session, session.begin():
                session.execute(text("SET LOCAL lock_timeout = '1500ms'"))
                session.execute(text("SET LOCAL statement_timeout = '2500ms'"))
                return acquire_provider_slot(session, provider, authorize=self.authorize)
        except SQLAlchemyError:
            raise ProviderPacingError("provider_gate_unavailable") from None

    async def __call__(self, provider: Provider) -> None:
        deadline = asyncio.get_running_loop().time() + self.max_wait_seconds
        while True:
            delay = await asyncio.to_thread(self._acquire, provider)
            if type(delay) not in (int, float) or not math.isfinite(delay) or delay < 0:
                raise ProviderPacingError("provider_gate_unavailable")
            if delay == 0:
                return
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise ProviderPacingError("rate_limited")
            await asyncio.sleep(min(delay, remaining, 3.0))
