"""Experimental Frontier programmes: an unsolved question becomes a growing tree.

question → (survey: Ask) → named gaps → (transfer: Explore across fields)
→ if not bridged, (decompose: Ask for intermediate results) → steps
→ (attempt: Ask) → supported / derived / conjectured, or decomposed again.

Every attempt is an ordinary owned research run created through the existing
admission, approval and budget path; this module never calls a model or spends.
Node statements come only from saved, owner-scoped results, never from the
browser, and every node keeps the honest status of its evidence.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from polymath.access_models import ResearchRunAccess
from polymath.evidence import _canonical, _hash
from polymath.evidence_models import EvidenceResult
from polymath.frontier_models import FrontierAttempt, FrontierNode, FrontierProgramme
from polymath.models import ResearchRun
from polymath.research.models import AnswerFinding, BridgeFinding

TERMINAL_RUN_STATUSES = ("succeeded", "failed", "cancelled", "interrupted")
ACTION_MODES = {"survey": "ask", "transfer": "explore", "decompose": "ask", "attempt": "ask"}
LABEL_STATUS = {"known": "supported", "postulated": "derived", "speculative": "conjectured"}
STATUS_STRENGTH = ("supported", "transferred", "derived", "conjectured", "open")
MAX_FAILED_ATTEMPTS = 2
MAX_PROGRAMMES_PER_OWNER = 20
GOAL_LIMIT = 4000


class FrontierError(Exception):
    """Sanitized code; never echoes stored or submitted text."""


@dataclass(frozen=True)
class FrontierLimits:
    max_attempts: int = 24
    max_depth: int = 4
    max_branches: int = 6

    def __post_init__(self):
        for value, low, high in (
            (self.max_attempts, 1, 60),
            (self.max_depth, 1, 6),
            (self.max_branches, 1, 8),
        ):
            if type(value) is not int or not low <= value <= high:
                raise FrontierError("invalid_frontier_limits")


def _text(value: object, maximum: int) -> str:
    if not isinstance(value, str):
        raise FrontierError("invalid_frontier_request")
    value = " ".join(value.split())
    if not 1 <= len(value) <= maximum or any(ord(character) < 32 for character in value):
        raise FrontierError("invalid_frontier_request")
    return value


def _clip(value: str, maximum: int) -> str:
    value = " ".join(value.split())
    return value if len(value) <= maximum else value[: maximum - 1] + "…"


def goal_for(action: str, statement: str, question: str) -> str:
    """The research goal for one attempt, built server-side from stored text."""
    context = _clip(question, 1200)
    if action == "survey":
        goal = statement
    elif action == "transfer":
        goal = f"{_clip(statement, 1800)} (an open gap within the problem: {context})"
    elif action == "decompose":
        goal = (
            "Break this open problem into an ordered sequence of smaller, more tractable "
            "intermediate results that together would resolve it, grounding each step in the "
            f"literature it builds on: {_clip(statement, 1800)} Overall goal: {context}"
        )
    elif action == "attempt":
        goal = f"{_clip(statement, 2200)} (an intermediate step toward: {context})"
    else:
        raise FrontierError("invalid_frontier_action")
    return _clip(goal, GOAL_LIMIT)


def weakest(statuses: list[str]) -> str:
    ranked = [status for status in statuses if status in STATUS_STRENGTH]
    return max(ranked, key=STATUS_STRENGTH.index) if ranked else "open"


def _programme(session: Session, owner_hash: str, programme_id: uuid.UUID, *, lock=False):
    statement = select(FrontierProgramme).where(
        FrontierProgramme.id == programme_id,
        FrontierProgramme.owner_session_hash == owner_hash,
    )
    row = session.scalar(statement.with_for_update() if lock else statement)
    if row is None:
        raise FrontierError("frontier_not_found")
    return row


def _saved_finding(session: Session, run: ResearchRun):
    """The run's own verified saved result, or None for an empty or invalid outcome."""
    result_id = (run.outcome or {}).get("result_id") if isinstance(run.outcome, dict) else None
    try:
        result_uuid = uuid.UUID(str(result_id)) if result_id else None
    except ValueError:
        return None, None
    if result_uuid is None:
        return None, None
    row = session.get(EvidenceResult, result_uuid)
    if row is None or row.run_id != run.id:
        return None, None
    try:
        if _hash(_canonical(row.payload)) != row.content_sha256:
            return row.id, None
        raw = row.payload["result"]["finding"]
        if row.status == "answer":
            return row.id, AnswerFinding.model_validate_json(json.dumps(raw))
        if row.status == "bridge":
            return row.id, BridgeFinding.model_validate_json(json.dumps(raw))
    except (KeyError, TypeError, ValueError, ValidationError):
        return row.id, None
    return row.id, None


class FrontierStore:
    def __init__(self, engine: Engine):
        if engine.dialect.name != "postgresql":
            raise ValueError("Frontier programmes require PostgreSQL")
        self.engine = engine

    def create(
        self,
        owner_hash: str,
        question: str,
        success_criteria: str | None = None,
        limits: FrontierLimits | None = None,
    ) -> dict:
        question = _text(question, 2000)
        criteria = _text(success_criteria, 1000) if success_criteria else None
        limits = limits or FrontierLimits()
        with Session(self.engine) as session, session.begin():
            count = session.scalar(
                select(func.count())
                .select_from(FrontierProgramme)
                .where(FrontierProgramme.owner_session_hash == owner_hash)
            )
            if count >= MAX_PROGRAMMES_PER_OWNER:
                raise FrontierError("frontier_limit_reached")
            programme = FrontierProgramme(
                id=uuid.uuid4(),
                owner_session_hash=owner_hash,
                question=question,
                success_criteria=criteria,
                max_attempts=limits.max_attempts,
                max_depth=limits.max_depth,
                max_branches=limits.max_branches,
            )
            session.add(programme)
            session.flush()
            survey = question if criteria is None else f"{question} Success criteria: {criteria}"
            session.add(
                FrontierNode(
                    id=uuid.uuid4(),
                    programme_id=programme.id,
                    parent_id=None,
                    kind="question",
                    statement=_clip(survey, 4000),
                    ordinal=0,
                    depth=0,
                    pending_action="survey",
                )
            )
            session.flush()
            return self._snapshot(session, programme)

    def list(self, owner_hash: str) -> list[dict]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(FrontierProgramme)
                .where(FrontierProgramme.owner_session_hash == owner_hash)
                .order_by(FrontierProgramme.created_at.desc(), FrontierProgramme.id)
                .limit(MAX_PROGRAMMES_PER_OWNER)
            ).all()
            return [self._summary(session, row) for row in rows]

    def get(self, owner_hash: str, programme_id: uuid.UUID) -> dict:
        with Session(self.engine) as session:
            return self._snapshot(session, _programme(session, owner_hash, programme_id))

    def set_status(self, owner_hash: str, programme_id: uuid.UUID, status: str) -> dict:
        if status not in ("active", "paused", "stopped"):
            raise FrontierError("invalid_frontier_request")
        with Session(self.engine) as session, session.begin():
            programme = _programme(session, owner_hash, programme_id, lock=True)
            if programme.status in ("completed", "stopped"):
                raise FrontierError("frontier_finished")
            programme.status = status
            programme.updated_at = func.clock_timestamp()
            session.flush()
            return self._snapshot(session, programme)

    def prune(self, owner_hash: str, programme_id: uuid.UUID, node_id: uuid.UUID) -> dict:
        """Skip a branch the owner does not want pursued; its history is kept."""
        with Session(self.engine) as session, session.begin():
            programme = _programme(session, owner_hash, programme_id, lock=True)
            nodes = {
                row.id: row
                for row in session.scalars(
                    select(FrontierNode).where(FrontierNode.programme_id == programme.id)
                )
            }
            try:
                target = nodes.get(uuid.UUID(str(node_id)))
            except ValueError:
                target = None
            if target is None or target.kind == "question":
                raise FrontierError("frontier_node_not_found")
            pending = [target]
            while pending:
                node = pending.pop()
                # A running attempt finishes and is absorbed; nothing new starts here.
                if node.pending_action is not None and node.status != "running":
                    node.status, node.pending_action = "skipped", None
                pending.extend(row for row in nodes.values() if row.parent_id == node.id)
            session.flush()
            return self._snapshot(session, programme)

    def next_action(self, owner_hash: str, programme_id: uuid.UUID) -> dict:
        """What the owner's browser should queue next, or why nothing is due."""
        with Session(self.engine) as session, session.begin():
            programme = _programme(session, owner_hash, programme_id, lock=True)
            self._absorb(session, programme)
            return self._next(session, programme)

    def attach(
        self,
        owner_hash: str,
        programme_id: uuid.UUID,
        node_id: uuid.UUID,
        action: str,
        run_id: uuid.UUID,
    ) -> dict:
        """Link a just-created owned run to the node it attempts."""
        with Session(self.engine) as session, session.begin():
            programme = _programme(session, owner_hash, programme_id, lock=True)
            due = self._next(session, programme)
            if due.get("node_id") != str(node_id) or due.get("action") != action:
                raise FrontierError("frontier_action_not_due")
            run = session.scalar(
                select(ResearchRun)
                .join(ResearchRunAccess, ResearchRunAccess.run_id == ResearchRun.id)
                .where(ResearchRun.id == run_id, ResearchRunAccess.session_hash == owner_hash)
            )
            if (
                run is None
                or run.mode != ACTION_MODES[action]
                or run.task_kind != "research"
                or session.scalar(
                    select(FrontierAttempt.id).where(FrontierAttempt.run_id == run_id)
                )
            ):
                raise FrontierError("frontier_run_invalid")
            node = session.get(FrontierNode, node_id)
            session.add(
                FrontierAttempt(
                    id=uuid.uuid4(),
                    programme_id=programme.id,
                    node_id=node.id,
                    action=action,
                    run_id=run.id,
                )
            )
            node.status = "running"
            node.updated_at = func.clock_timestamp()
            programme.updated_at = func.clock_timestamp()
            session.flush()
            return self._snapshot(session, programme)

    # Internal steps -----------------------------------------------------------

    def _next(self, session: Session, programme: FrontierProgramme) -> dict:
        running = session.scalar(
            select(FrontierAttempt)
            .where(
                FrontierAttempt.programme_id == programme.id,
                FrontierAttempt.absorbed_at.is_(None),
            )
            .limit(1)
        )
        if running is not None:
            return {"state": "waiting", "run_id": str(running.run_id)}
        if programme.status != "active":
            return {"state": programme.status}
        attempts = session.scalar(
            select(func.count())
            .select_from(FrontierAttempt)
            .where(FrontierAttempt.programme_id == programme.id)
        )
        node = session.scalar(
            select(FrontierNode)
            .where(
                FrontierNode.programme_id == programme.id,
                FrontierNode.pending_action.is_not(None),
            )
            .order_by(FrontierNode.depth, FrontierNode.ordinal, FrontierNode.created_at)
            .limit(1)
        )
        if node is None or attempts >= programme.max_attempts:
            programme.status = "completed"
            programme.updated_at = func.clock_timestamp()
            return {
                "state": "completed",
                "reason": "attempt_limit" if node is not None else "no_open_actions",
            }
        return {
            "state": "due",
            "node_id": str(node.id),
            "action": node.pending_action,
            "mode": ACTION_MODES[node.pending_action],
            "goal": goal_for(node.pending_action, node.statement, programme.question),
        }

    def _absorb(self, session: Session, programme: FrontierProgramme) -> None:
        attempts = session.scalars(
            select(FrontierAttempt)
            .where(
                FrontierAttempt.programme_id == programme.id,
                FrontierAttempt.absorbed_at.is_(None),
            )
            .order_by(FrontierAttempt.created_at)
        ).all()
        for attempt in attempts:
            run = session.get(ResearchRun, attempt.run_id)
            if run is None or run.status not in TERMINAL_RUN_STATUSES:
                continue
            node = session.get(FrontierNode, attempt.node_id)
            result_id, finding = (
                _saved_finding(session, run) if run.status == "succeeded" else (None, None)
            )
            attempt.result_id = result_id
            attempt.absorbed_at = func.clock_timestamp()
            node.updated_at = func.clock_timestamp()
            if run.status != "succeeded":
                attempt.outcome = run.status
                node.failed_attempts += 1
                node.status = "pending"
                if node.failed_attempts >= MAX_FAILED_ATTEMPTS:
                    node.status, node.pending_action = "open", None
                continue
            attempt.outcome = (
                "bridge"
                if isinstance(finding, BridgeFinding)
                else "answer"
                if isinstance(finding, AnswerFinding)
                else "insufficient_evidence"
            )
            self._apply(session, programme, node, attempt.action, result_id, finding)

    def _apply(self, session, programme, node, action, result_id, finding) -> None:
        deeper = node.depth + 1 < programme.max_depth
        node.pending_action = None
        if action == "survey":
            if isinstance(finding, AnswerFinding):
                node.status = "surveyed"
                gaps = list(finding.unanswered_questions)
                for index, gap in enumerate(gaps[: programme.max_branches]):
                    self._child(
                        session, programme, node, "gap", gap, index, result_id, f"gap:{index}"
                    )
                if not gaps:
                    node.status = weakest([LABEL_STATUS[claim.label] for claim in finding.claims])
            else:
                # Nothing in the literature answers it: the whole problem is the gap.
                node.status = "surveyed"
                self._child(
                    session, programme, node, "gap", programme.question, 0, None, "gap:root"
                )
            return
        if action == "transfer":
            if isinstance(finding, BridgeFinding):
                node.status = "transferred"
            elif deeper:
                node.status, node.pending_action = "pending", "decompose"
            else:
                node.status = "open"
            return
        if action == "decompose":
            if isinstance(finding, AnswerFinding) and finding.claims:
                node.status = "decomposed"
                for index, claim in enumerate(finding.claims[: programme.max_branches]):
                    self._child(
                        session,
                        programme,
                        node,
                        "step",
                        claim.text,
                        index,
                        result_id,
                        f"claim:{claim.id}",
                    )
            else:
                node.status = "open"
            return
        if action == "attempt":
            status = (
                weakest([LABEL_STATUS[claim.label] for claim in finding.claims])
                if isinstance(finding, AnswerFinding)
                else "open"
            )
            if status in ("conjectured", "open") and deeper:
                node.status, node.pending_action = "pending", "decompose"
            else:
                node.status = status

    def _child(self, session, programme, parent, kind, statement, ordinal, result_id, ref) -> None:
        text = " ".join(str(statement).split())
        if not text:
            return
        exists = session.scalar(
            select(FrontierNode.id).where(
                FrontierNode.programme_id == programme.id,
                FrontierNode.parent_id == parent.id,
                FrontierNode.origin_ref == ref,
                FrontierNode.origin_result_id == result_id
                if result_id is not None
                else FrontierNode.origin_result_id.is_(None),
            )
        )
        if exists is not None:
            return
        session.add(
            FrontierNode(
                id=uuid.uuid4(),
                programme_id=programme.id,
                parent_id=parent.id,
                kind=kind,
                statement=_clip(text, 4000),
                ordinal=ordinal,
                depth=parent.depth + 1,
                pending_action="transfer" if kind == "gap" else "attempt",
                origin_result_id=result_id,
                origin_ref=ref,
            )
        )
        session.flush()

    def _summary(self, session: Session, programme: FrontierProgramme) -> dict:
        counts = dict(
            session.execute(
                select(FrontierNode.status, func.count())
                .where(FrontierNode.programme_id == programme.id)
                .group_by(FrontierNode.status)
            ).all()
        )
        return {
            "id": str(programme.id),
            "question": programme.question,
            "status": programme.status,
            "created_at": programme.created_at.isoformat(),
            "updated_at": programme.updated_at.isoformat(),
            "node_counts": counts,
        }

    def _snapshot(self, session: Session, programme: FrontierProgramme) -> dict:
        nodes = session.scalars(
            select(FrontierNode)
            .where(FrontierNode.programme_id == programme.id)
            .order_by(FrontierNode.depth, FrontierNode.ordinal, FrontierNode.created_at)
        ).all()
        attempts = session.scalars(
            select(FrontierAttempt)
            .where(FrontierAttempt.programme_id == programme.id)
            .order_by(FrontierAttempt.created_at)
        ).all()
        by_parent: dict[uuid.UUID | None, list[FrontierNode]] = {}
        for node in nodes:
            by_parent.setdefault(node.parent_id, []).append(node)

        def resolved(node: FrontierNode) -> str:
            # A decomposed gap is only as strong as its weakest step.
            if node.status == "decomposed":
                children = [resolved(child) for child in by_parent.get(node.id, [])]
                children = [status for status in children if status != "skipped"]
                if any(status in ("pending", "running") for status in children):
                    return "pending"
                return weakest(children) if children else "open"
            return node.status

        summary = self._summary(session, programme)
        summary.update(
            {
                "success_criteria": programme.success_criteria,
                "limits": {
                    "max_attempts": programme.max_attempts,
                    "max_depth": programme.max_depth,
                    "max_branches": programme.max_branches,
                },
                "attempts_used": len(attempts),
                "nodes": [
                    {
                        "id": str(node.id),
                        "parent_id": str(node.parent_id) if node.parent_id else None,
                        "kind": node.kind,
                        "statement": node.statement,
                        "depth": node.depth,
                        "ordinal": node.ordinal,
                        "status": node.status,
                        "resolved_status": resolved(node),
                        "pending_action": node.pending_action,
                        "attempts": [
                            {
                                "action": attempt.action,
                                "run_id": str(attempt.run_id),
                                "result_id": str(attempt.result_id) if attempt.result_id else None,
                                "outcome": attempt.outcome,
                            }
                            for attempt in attempts
                            if attempt.node_id == node.id
                        ],
                    }
                    for node in nodes
                ],
            }
        )
        return summary
