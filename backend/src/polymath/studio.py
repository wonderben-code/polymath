"""Innovation Studio: design new things from mechanisms found across science.

brief → (brief: Ask) the state of the art and the hardest functions to achieve
→ (inspire: Explore, up to four) a mechanism from a distant field per function
→ (design: Explore built on 2–4 inspirations) one integrated design concept
→ (test: Ask) the design judged against its specs, prior art and estimates.

Every stage is an ordinary owned research run created by the visitor's browser
through the existing admission, approval and budget path, so Meta chooses what
to search and read and every quote is checked. This module never calls a model
or spends; it plans stages and reads their saved, verified results
(docs/STUDIO.md).
"""

from __future__ import annotations

import uuid

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from polymath.access_models import ResearchRunAccess
from polymath.frontier import _saved_finding
from polymath.lineage_models import ResearchRunParent
from polymath.models import ResearchRun
from polymath.research.models import AnswerFinding, BridgeFinding
from polymath.studio_models import StudioProgramme, StudioStep

TERMINAL = ("succeeded", "failed", "cancelled", "interrupted")
STAGES = ("brief", "inspire", "design", "test")
STAGE_MODES = {"brief": "ask", "inspire": "explore", "design": "explore", "test": "ask"}
WILDNESS = ("near", "far", "strange")
MAX_INSPIRATIONS = 4
MAX_SPECS = 6
MAX_FAILED = 2
MAX_PROGRAMMES_PER_OWNER = 20
MAX_INVESTIGATIONS = 1 + MAX_INSPIRATIONS + 2
GOAL_LIMIT = 4000
# Ordinals keep the stages in reading order whatever the plan becomes.
DESIGN_ORDINAL = 1 + MAX_INSPIRATIONS
TEST_ORDINAL = DESIGN_ORDINAL + 1

WHERE = {
    "near": "Look in neighbouring branches of engineering and the physical sciences.",
    "far": (
        "Look in distant fields: living things, the earth sciences, materials, medicine "
        "and mathematics."
    ),
    "strange": (
        "Look in the most unexpected corners of science: unusual organisms, deep-sea and "
        "extreme life, plants and fungi, geology, traditional crafts, mathematics and art. "
        "Prefer a surprising mechanism that truly works over an obvious one."
    ),
}
# With no named functions, inspiration is sought in three deliberately different places.
ANGLES = (
    "Look especially at living things: animals, plants, fungi and microbes.",
    "Look especially at the earth and physical sciences.",
    "Look especially at materials, mathematics and other branches of engineering.",
)


class StudioError(Exception):
    """Sanitized code; never echoes stored or submitted text."""


def _text(value: object, maximum: int) -> str:
    if not isinstance(value, str):
        raise StudioError("invalid_studio_request")
    text = " ".join(value.split())
    if not 1 <= len(text) <= maximum:
        raise StudioError("invalid_studio_request")
    return text


def _clip(value: str, maximum: int) -> str:
    return value if len(value) <= maximum else value[: maximum - 1].rstrip() + "…"


def _specs_line(specs: list[str]) -> str:
    return "; ".join(specs) if specs else "none stated beyond the brief"


def goal_for(stage: str, target: str, brief: str, specs: list[str], wildness: str) -> str:
    """The research goal for one stage, built server-side from stored text."""
    if wildness not in WILDNESS:
        raise StudioError("invalid_studio_request")
    context = _clip(brief, 1000)
    required = _clip(_specs_line(specs), 1300)
    if stage == "brief":
        goal = (
            f"Design brief: {context} Requirements: {required}. What do today's best designs "
            "of this kind achieve, and which of these requirements are hardest to meet, and "
            "why? List each hard requirement as an open question naming the function a new "
            "design would need, in the form 'How could a design achieve <function> while "
            "<constraint>?'"
        )
    elif stage == "inspire":
        goal = (
            "Find a mechanism that already achieves this in a field far from the design's "
            f"own, as raw material for a new invention: {_clip(target, 1000)} "
            f"{WHERE[wildness]} Describe the mechanism precisely, how it would map into the "
            "design, and where it may not transfer (scale, materials or environment). "
            f"Design brief: {context}"
        )
    elif stage == "design":
        sources = (
            "these mechanisms from different fields"
            if target == brief
            else f"this mechanism from another field: {_clip(target, 1200)}"
        )
        goal = (
            f"Invent one concrete, new {context} that meets these requirements: {required}. "
            f"Build it from {sources}, and be bold and original: reason beyond the sources to "
            "the new mechanism, structure or principle they make possible, not a mere list of "
            "parts. Name each component and the source it comes from, explain how the whole "
            "works and where the parts conflict, and state clearly which parts are supported "
            "by the cited evidence and which are your own invention. Test the design against "
            "its strongest objection, including whether each mechanism still works at the "
            "design's scale."
        )
    elif stage == "test":
        goal = (
            f"Stress-test this proposed design: {_clip(target, 1500)} Requirements: "
            f"{required}. For each requirement, say whether the literature supports that the "
            "design could meet it, and why. Check whether this design or a close one already "
            "exists. Give rough estimates of the key quantities where the literature allows, "
            f"and name what would still need to be invented. Design brief: {context}"
        )
    else:
        raise StudioError("invalid_studio_stage")
    return _clip(goal, GOAL_LIMIT)


def _programme(session: Session, owner_hash: str, programme_id: uuid.UUID, *, lock=False):
    statement = select(StudioProgramme).where(
        StudioProgramme.id == programme_id, StudioProgramme.owner_session_hash == owner_hash
    )
    row = session.scalar(statement.with_for_update() if lock else statement)
    if row is None:
        raise StudioError("studio_not_found")
    return row


def _steps(session: Session, programme: StudioProgramme) -> list[StudioStep]:
    return list(
        session.scalars(
            select(StudioStep)
            .where(StudioStep.programme_id == programme.id)
            .order_by(StudioStep.ordinal)
        )
    )


def _add(session, programme, stage, ordinal, target, premises=()) -> None:
    session.add(
        StudioStep(
            id=uuid.uuid4(),
            programme_id=programme.id,
            stage=stage,
            ordinal=ordinal,
            target=_clip(" ".join(target.split()), 4000),
            premise_result_ids=[str(value) for value in premises],
        )
    )
    session.flush()


def _bridge(session: Session, step: StudioStep) -> BridgeFinding | None:
    if step.run_id is None or step.outcome != "bridge":
        return None
    run = session.get(ResearchRun, step.run_id)
    finding = _saved_finding(session, run)[1] if run is not None else None
    return finding if isinstance(finding, BridgeFinding) else None


def _describe(finding: BridgeFinding) -> str:
    return f"{finding.title} ({finding.field_left} to {finding.field_right}): {finding.explanation}"


class StudioStore:
    def __init__(self, engine: Engine):
        if engine.dialect.name != "postgresql":
            raise ValueError("Studio requires PostgreSQL")
        self.engine = engine

    def create(
        self, owner_hash: str, brief: str, specs: list[str] | None = None, wildness: str = "far"
    ) -> dict:
        brief = _text(brief, 1000)
        if not isinstance(specs, list | None) or len(specs or []) > MAX_SPECS:
            raise StudioError("invalid_studio_request")
        specs = [_text(item, 200) for item in specs or []]
        if len(set(specs)) != len(specs) or wildness not in WILDNESS:
            raise StudioError("invalid_studio_request")
        with Session(self.engine) as session, session.begin():
            count = session.scalar(
                select(func.count())
                .select_from(StudioProgramme)
                .where(StudioProgramme.owner_session_hash == owner_hash)
            )
            if count >= MAX_PROGRAMMES_PER_OWNER:
                raise StudioError("studio_limit_reached")
            programme = StudioProgramme(
                id=uuid.uuid4(),
                owner_session_hash=owner_hash,
                brief=brief,
                specs=specs,
                wildness=wildness,
            )
            session.add(programme)
            session.flush()
            _add(session, programme, "brief", 0, brief)
            return self._snapshot(session, programme)

    def list(self, owner_hash: str) -> list[dict]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(StudioProgramme)
                .where(StudioProgramme.owner_session_hash == owner_hash)
                .order_by(StudioProgramme.created_at.desc(), StudioProgramme.id)
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
            raise StudioError("invalid_studio_request")
        with Session(self.engine) as session, session.begin():
            programme = _programme(session, owner_hash, programme_id, lock=True)
            if programme.status in ("completed", "stopped"):
                raise StudioError("studio_finished")
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
                raise StudioError("studio_step_not_due")
            run = session.scalar(
                select(ResearchRun)
                .join(ResearchRunAccess, ResearchRunAccess.run_id == ResearchRun.id)
                .where(ResearchRun.id == run_id, ResearchRunAccess.session_hash == owner_hash)
            )
            if (
                run is None
                or run.mode != STAGE_MODES[stage]
                or run.task_kind != "research"
                or session.scalar(select(StudioStep.id).where(StudioStep.run_id == run_id))
            ):
                raise StudioError("studio_run_invalid")
            step = session.get(StudioStep, step_id)
            parents = set(
                session.scalars(
                    select(ResearchRunParent.parent_result_id).where(
                        ResearchRunParent.run_id == run.id
                    )
                )
            )
            if parents != {uuid.UUID(value) for value in step.premise_result_ids}:
                raise StudioError("studio_run_invalid")
            step.run_id, step.status = run.id, "running"
            step.updated_at = programme.updated_at = func.clock_timestamp()
            session.flush()
            return self._snapshot(session, programme)

    # Internal steps -----------------------------------------------------------

    def _next(self, session: Session, programme: StudioProgramme) -> dict:
        steps = _steps(session, programme)
        running = next((step for step in steps if step.status == "running"), None)
        if running is not None:
            return {"state": "waiting", "run_id": str(running.run_id)}
        if programme.status == "completed":
            return {"state": "completed", "outcome": programme.outcome}
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
            "goal": goal_for(
                step.stage, step.target, programme.brief, programme.specs, programme.wildness
            ),
            "parent_result_ids": list(step.premise_result_ids),
        }

    def _absorb(self, session: Session, programme: StudioProgramme) -> None:
        for step in _steps(session, programme):
            if step.status != "running" or step.run_id is None:
                continue
            run = session.get(ResearchRun, step.run_id)
            if run is None or run.status not in TERMINAL:
                continue
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
        self._maybe_design(session, programme)
        self._maybe_test(session, programme)

    def _plan(self, session, programme, step, finding) -> None:
        if step.stage == "test":
            # The stress test is the last stage; the design is complete either way.
            programme.outcome, programme.status = "designed", "completed"
            return
        if step.stage != "brief":
            return
        functions = (
            [gap for gap in finding.unanswered_questions if gap.strip()][:MAX_INSPIRATIONS]
            if isinstance(finding, AnswerFinding)
            else []
        )
        if functions:
            for index, function in enumerate(functions):
                _add(session, programme, "inspire", 1 + index, function)
        else:
            for index, angle in enumerate(ANGLES):
                _add(session, programme, "inspire", 1 + index, f"{programme.brief} {angle}")

    def _maybe_design(self, session: Session, programme: StudioProgramme) -> None:
        """Once every inspiration search is settled, design from what held up."""
        steps = _steps(session, programme)
        inspirations = [item for item in steps if item.stage == "inspire"]
        if (
            not inspirations
            or any(item.stage == "design" for item in steps)
            or any(item.status in ("pending", "running") for item in inspirations)
        ):
            return
        held = [item for item in inspirations if item.outcome == "bridge"]
        if len(held) >= 2:
            premises = [item.result_id for item in held[:MAX_INSPIRATIONS]]
            _add(session, programme, "design", DESIGN_ORDINAL, programme.brief, premises)
        elif len(held) == 1 and (finding := _bridge(session, held[0])) is not None:
            # A single inspiration cannot be a lineage parent; it travels in the goal.
            _add(session, programme, "design", DESIGN_ORDINAL, _describe(finding))

    def _maybe_test(self, session: Session, programme: StudioProgramme) -> None:
        steps = _steps(session, programme)
        design = next((item for item in steps if item.stage == "design"), None)
        if design is None or any(item.stage == "test" for item in steps):
            return
        finding = _bridge(session, design)
        if finding is not None:
            _add(session, programme, "test", TEST_ORDINAL, _describe(finding))

    def _finish(self, session: Session, programme: StudioProgramme, steps) -> None:
        if any(item.stage == "design" and item.outcome == "bridge" for item in steps):
            programme.outcome = "designed"
        elif any(item.stage == "inspire" and item.outcome == "bridge" for item in steps):
            programme.outcome = "inspired"
        else:
            programme.outcome = "none"
        programme.status = "completed"
        programme.updated_at = func.clock_timestamp()

    def _summary(self, programme: StudioProgramme) -> dict:
        return {
            "id": str(programme.id),
            "brief": programme.brief,
            "specs": list(programme.specs),
            "wildness": programme.wildness,
            "status": programme.status,
            "outcome": programme.outcome,
            "created_at": programme.created_at.isoformat(),
            "updated_at": programme.updated_at.isoformat(),
        }

    def _view(self, session: Session, step: StudioStep) -> dict | None:
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
                "explanation": _clip(finding.explanation, 1200),
                "mappings": [
                    {"left": _clip(item.left, 200), "right": _clip(item.right, 200)}
                    for item in finding.mappings[:5]
                ],
                "objection": _clip(finding.strongest_objection, 700),
                "verdict": _clip(finding.objection_verdict, 700),
                "open_question": _clip(finding.open_question, 400),
            }
        if isinstance(finding, AnswerFinding):
            return {
                "kind": "answer",
                "summary": _clip(finding.summary, 1200),
                "claims": [
                    {"label": claim.label, "text": _clip(claim.text, 500)}
                    for claim in finding.claims[:8]
                ],
                "open_questions": [
                    _clip(gap, 300) for gap in finding.unanswered_questions[:MAX_INSPIRATIONS]
                ],
            }
        return {"kind": "insufficient_evidence"}

    def _snapshot(self, session: Session, programme: StudioProgramme) -> dict:
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
