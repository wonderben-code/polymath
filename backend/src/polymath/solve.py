"""Solve: key finder and key maker (docs/SOLVE.md).

problem → (find: Ask) → a key that already fits, or the gaps that remain
→ (fit: Explore, up to three) → close fits from other fields
→ (forge: Explore built on 2–4 close fits) → a new key made from them.

Every stage is an ordinary owned research run created by the visitor's browser
through the existing admission, approval and budget path, so Meta chooses what
to search and read and every quote is checked. This module never calls a model
or spends; it plans stages and reads their saved, verified results.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from polymath.access_models import ResearchRunAccess
from polymath.frontier import _saved_finding, release_interrupted_run
from polymath.frontier_models import FrontierNode, FrontierProgramme
from polymath.lineage_models import ResearchRunParent
from polymath.models import ResearchRun
from polymath.research.models import AnswerFinding, BridgeFinding
from polymath.solve_models import SolveProgramme, SolveStep

TERMINAL = ("succeeded", "failed", "cancelled", "interrupted")
STAGE_MODES = {"find": "ask", "fit": "explore", "forge": "explore"}
MAX_FITS = 3
MAX_FORGE_PREMISES = 4
MAX_FAILED = 2
MAX_PROGRAMMES_PER_OWNER = 20
GOAL_LIMIT = 4000
# With no named gaps, close fits are sought in three deliberately distant places.
ANGLES = (
    "Look especially in the physical sciences and mathematics.",
    "Look especially in the life sciences and medicine.",
    "Look especially in engineering and computing.",
)


class SolveError(Exception):
    """Sanitized code; never echoes stored or submitted text."""


def _text(value: object, maximum: int) -> str:
    if not isinstance(value, str):
        raise SolveError("invalid_solve_request")
    text = " ".join(value.split())
    if not 1 <= len(text) <= maximum:
        raise SolveError("invalid_solve_request")
    return text


def _clip(value: str, maximum: int) -> str:
    return value if len(value) <= maximum else value[: maximum - 1].rstrip() + "…"


def goal_for(stage: str, target: str, problem: str) -> str:
    """The research goal for one stage, built server-side from stored text."""
    context = _clip(problem, 1500)
    if stage == "find":
        goal = (
            "Has this problem already been solved, in any field of science? Find existing "
            "solutions that directly answer it, say how well each fits, and list exactly what "
            f"remains unsolved: {context}"
        )
    elif stage == "fit":
        goal = (
            "Find a method or result from another field whose structure could solve this: "
            f"{_clip(target, 1500)} State precisely where it fits and where it falls short. "
            f"Overall problem: {context}"
        )
    elif stage == "forge":
        goal = (
            "Combine these partial solutions from different fields into one candidate "
            "solution to the problem. Show what each contributes and how they fit together, "
            "test the combined solution against the original evidence, and state what still "
            f"does not fit. Problem: {context}"
        )
    else:
        raise SolveError("invalid_solve_stage")
    return _clip(goal, GOAL_LIMIT)


def _programme(session: Session, owner_hash: str, programme_id: uuid.UUID, *, lock=False):
    statement = select(SolveProgramme).where(
        SolveProgramme.id == programme_id, SolveProgramme.owner_session_hash == owner_hash
    )
    row = session.scalar(statement.with_for_update() if lock else statement)
    if row is None:
        raise SolveError("solve_not_found")
    return row


def _steps(session: Session, programme: SolveProgramme) -> list[SolveStep]:
    return list(
        session.scalars(
            select(SolveStep)
            .where(SolveStep.programme_id == programme.id)
            .order_by(SolveStep.ordinal)
        )
    )


def _add(session, programme, stage, ordinal, target, premises=()) -> None:
    session.add(
        SolveStep(
            id=uuid.uuid4(),
            programme_id=programme.id,
            stage=stage,
            ordinal=ordinal,
            target=_clip(" ".join(target.split()), 4000),
            premise_result_ids=[str(value) for value in premises],
        )
    )
    session.flush()


class SolveStore:
    def __init__(self, engine: Engine):
        if engine.dialect.name != "postgresql":
            raise ValueError("Solve requires PostgreSQL")
        self.engine = engine

    def create(
        self,
        owner_hash: str,
        problem: str | None = None,
        *,
        frontier_programme_id: uuid.UUID | None = None,
        frontier_node_id: uuid.UUID | None = None,
    ) -> dict:
        with Session(self.engine) as session, session.begin():
            if frontier_node_id is not None:
                node = session.scalar(
                    select(FrontierNode)
                    .join(FrontierProgramme, FrontierProgramme.id == FrontierNode.programme_id)
                    .where(
                        FrontierNode.id == frontier_node_id,
                        FrontierProgramme.id == frontier_programme_id,
                        FrontierProgramme.owner_session_hash == owner_hash,
                    )
                )
                if node is None:
                    raise SolveError("solve_frontier_step_not_found")
                problem = problem or node.statement
            elif frontier_programme_id is not None:
                raise SolveError("invalid_solve_request")
            problem = _text(problem, 2000)
            count = session.scalar(
                select(func.count())
                .select_from(SolveProgramme)
                .where(SolveProgramme.owner_session_hash == owner_hash)
            )
            if count >= MAX_PROGRAMMES_PER_OWNER:
                raise SolveError("solve_limit_reached")
            programme = SolveProgramme(
                id=uuid.uuid4(),
                owner_session_hash=owner_hash,
                problem=problem,
                frontier_programme_id=frontier_programme_id,
                frontier_node_id=frontier_node_id,
            )
            session.add(programme)
            session.flush()
            _add(session, programme, "find", 0, problem)
            return self._snapshot(session, programme)

    def list(self, owner_hash: str) -> list[dict]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(SolveProgramme)
                .where(SolveProgramme.owner_session_hash == owner_hash)
                .order_by(SolveProgramme.created_at.desc(), SolveProgramme.id)
                .limit(MAX_PROGRAMMES_PER_OWNER)
            ).all()
            return [self._summary(row) for row in rows]

    def get(self, owner_hash: str, programme_id: uuid.UUID) -> dict:
        with Session(self.engine) as session, session.begin():
            programme = _programme(session, owner_hash, programme_id, lock=True)
            self._absorb(session, programme)
            return self._snapshot(session, programme)

    def set_status(self, owner_hash: str, programme_id: uuid.UUID, status: str) -> dict:
        if status not in ("active", "paused", "stopped"):
            raise SolveError("invalid_solve_request")
        with Session(self.engine) as session, session.begin():
            programme = _programme(session, owner_hash, programme_id, lock=True)
            if programme.status in ("completed", "stopped"):
                raise SolveError("solve_finished")
            programme.status = status
            programme.updated_at = func.clock_timestamp()
            session.flush()
            return self._snapshot(session, programme)

    def next_action(self, owner_hash: str, programme_id: uuid.UUID) -> dict:
        """What the visitor's browser should queue next, or why nothing is due."""
        with Session(self.engine) as session, session.begin():
            programme = _programme(session, owner_hash, programme_id, lock=True)
            self._absorb(session, programme)
            return self._next(session, programme)

    def attach(
        self,
        owner_hash: str,
        programme_id: uuid.UUID,
        step_id: uuid.UUID,
        stage: str,
        run_id: uuid.UUID,
    ) -> dict:
        """Link a just-created owned run to the stage it carries out."""
        with Session(self.engine) as session, session.begin():
            programme = _programme(session, owner_hash, programme_id, lock=True)
            due = self._next(session, programme)
            if due.get("step_id") != str(step_id) or due.get("stage") != stage:
                raise SolveError("solve_step_not_due")
            run = session.scalar(
                select(ResearchRun)
                .join(ResearchRunAccess, ResearchRunAccess.run_id == ResearchRun.id)
                .where(ResearchRun.id == run_id, ResearchRunAccess.session_hash == owner_hash)
            )
            if (
                run is None
                or run.mode != STAGE_MODES[stage]
                or run.task_kind != "research"
                or session.scalar(select(SolveStep.id).where(SolveStep.run_id == run_id))
            ):
                raise SolveError("solve_run_invalid")
            step = session.get(SolveStep, step_id)
            parents = set(
                session.scalars(
                    select(ResearchRunParent.parent_result_id).where(
                        ResearchRunParent.run_id == run.id
                    )
                )
            )
            if parents != {uuid.UUID(value) for value in step.premise_result_ids}:
                raise SolveError("solve_run_invalid")
            step.run_id, step.status = run.id, "running"
            step.updated_at = programme.updated_at = func.clock_timestamp()
            session.flush()
            return self._snapshot(session, programme)

    # Internal steps -----------------------------------------------------------

    def _next(self, session: Session, programme: SolveProgramme) -> dict:
        steps = _steps(session, programme)
        running = next((step for step in steps if step.status == "running"), None)
        if running is not None:
            return {"state": "waiting", "run_id": str(running.run_id)}
        if programme.status != "active":
            return {"state": programme.status}
        step = next((step for step in steps if step.status == "pending"), None)
        if step is None:
            self._finish(session, programme, steps)
            return {"state": "completed", "outcome": programme.outcome}
        return {
            "state": "due",
            "step_id": str(step.id),
            "stage": step.stage,
            "mode": STAGE_MODES[step.stage],
            "goal": goal_for(step.stage, step.target, programme.problem),
            "parent_result_ids": list(step.premise_result_ids),
        }

    def _absorb(self, session: Session, programme: SolveProgramme) -> None:
        for step in _steps(session, programme):
            if step.status != "running" or step.run_id is None:
                continue
            run = session.get(ResearchRun, step.run_id)
            if run is None or run.status not in TERMINAL:
                continue
            release_interrupted_run(session, run)
            step.updated_at = func.clock_timestamp()
            if run.status != "succeeded":
                step.failed_attempts += 1
                step.run_id, step.outcome = None, run.status
                step.status = "failed" if step.failed_attempts >= MAX_FAILED else "pending"
                continue
            result_id, finding = _saved_finding(session, run)
            step.result_id, step.status = result_id, "done"
            step.outcome = (
                "bridge"
                if isinstance(finding, BridgeFinding)
                else "answer"
                if isinstance(finding, AnswerFinding)
                else "insufficient_evidence"
            )
            self._plan(session, programme, step, finding)
        session.flush()
        self._maybe_forge(session, programme)

    def _maybe_forge(self, session: Session, programme: SolveProgramme) -> None:
        """Once every close-fit search is settled, forge from two or more fits."""
        steps = _steps(session, programme)
        fits = [item for item in steps if item.stage == "fit"]
        if (
            programme.status == "completed"
            or not fits
            or any(item.stage == "forge" for item in steps)
            or any(item.status in ("pending", "running") for item in fits)
        ):
            return
        bridges = [item.result_id for item in fits if item.outcome == "bridge"]
        if len(bridges) >= 2:
            _add(
                session,
                programme,
                "forge",
                1 + MAX_FITS,
                programme.problem,
                bridges[:MAX_FORGE_PREMISES],
            )

    def _plan(self, session, programme, step, finding) -> None:
        if step.stage == "find":
            if isinstance(finding, AnswerFinding):
                known = any(claim.label == "known" for claim in finding.claims)
                gaps = [gap for gap in finding.unanswered_questions if gap.strip()]
                if known and not gaps:
                    # A key already exists in the literature.
                    programme.outcome, programme.status = "found", "completed"
                    return
                targets = gaps[:MAX_FITS]
            else:
                targets = []
            if targets:
                for index, gap in enumerate(targets):
                    _add(session, programme, "fit", 1 + index, gap)
            else:
                for index, angle in enumerate(ANGLES):
                    _add(session, programme, "fit", 1 + index, f"{programme.problem} {angle}")
            return
        if step.stage == "forge":
            programme.outcome = "forged" if isinstance(finding, BridgeFinding) else "close"
            programme.status = "completed"

    def _finish(self, session: Session, programme: SolveProgramme, steps) -> None:
        if programme.outcome is None:
            fits = [item for item in steps if item.stage == "fit" and item.outcome == "bridge"]
            find = next((item for item in steps if item.stage == "find"), None)
            programme.outcome = (
                "close" if fits or (find is not None and find.outcome == "answer") else "none"
            )
        programme.status = "completed"
        programme.updated_at = func.clock_timestamp()

    def _summary(self, programme: SolveProgramme) -> dict:
        return {
            "id": str(programme.id),
            "problem": programme.problem,
            "status": programme.status,
            "outcome": programme.outcome,
            "frontier_programme_id": str(programme.frontier_programme_id)
            if programme.frontier_programme_id
            else None,
            "frontier_node_id": str(programme.frontier_node_id)
            if programme.frontier_node_id
            else None,
            "created_at": programme.created_at.isoformat(),
            "updated_at": programme.updated_at.isoformat(),
        }

    def _view(self, session: Session, step: SolveStep) -> dict | None:
        """The saved finding behind a stage, as plain text for the visitor."""
        if step.result_id is None or step.run_id is None:
            return None
        run = session.get(ResearchRun, step.run_id)
        if run is None:
            return None
        _, finding = _saved_finding(session, run)
        if isinstance(finding, BridgeFinding):
            return {
                "kind": "bridge",
                "title": finding.title,
                "field_left": finding.field_left,
                "field_right": finding.field_right,
                "explanation": _clip(finding.explanation, 900),
                "falls_short": _clip(finding.strongest_objection, 600),
                "verdict": _clip(finding.objection_verdict, 600),
                "open_question": _clip(finding.open_question, 400),
            }
        if isinstance(finding, AnswerFinding):
            return {
                "kind": "answer",
                "summary": _clip(finding.summary, 900),
                "known": [claim.text for claim in finding.claims if claim.label == "known"][:4],
                "other": [
                    {"label": claim.label, "text": claim.text}
                    for claim in finding.claims
                    if claim.label != "known"
                ][:4],
                "unsolved": [gap for gap in finding.unanswered_questions][:4],
            }
        return {"kind": "insufficient_evidence"}

    def _snapshot(self, session: Session, programme: SolveProgramme) -> dict:
        summary = self._summary(programme)
        summary["steps"] = [
            {
                "id": str(step.id),
                "stage": step.stage,
                "ordinal": step.ordinal,
                "target": step.target,
                "status": step.status,
                "outcome": step.outcome,
                "run_id": str(step.run_id) if step.run_id else None,
                "result_id": str(step.result_id) if step.result_id else None,
                "premise_result_ids": list(step.premise_result_ids),
                "finding": self._view(session, step),
            }
            for step in _steps(session, programme)
        ]
        return summary
