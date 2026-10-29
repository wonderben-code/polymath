"""Experimental Converge programmes: many fields, their connections, and deeper ones.

A circle of fields (named by the owner, the rest spread across the map of
knowledge) is explored in steps, each an ordinary owned Explore run:

  thread  Meta's model chooses two fields on the circle not yet connected and
          tests one shared structure against the literature (level 1).
  meta    two to four held connections become the premises of a deeper
          Explore, which looks for the structure they all share (level n+1).

Threads continue until every field has been touched, the model stops finding
connections, or the owner's spending ceiling is reached; held connections are
then drawn inward, four at a time, until one deepest convergence remains or
nothing deeper holds. Every step is created through the existing admission,
approval and budget path; this module never calls a model or spends.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Engine, case, func, select
from sqlalchemy.orm import Session

from polymath.access_models import ResearchRunAccess
from polymath.budget_models import BudgetReservation, BudgetRunLimit
from polymath.converge_fields import CATALOGUE, clean, match, normalise, spread
from polymath.converge_models import ConvergeProgramme, ConvergeStep
from polymath.frontier import TERMINAL_RUN_STATUSES, _clip, _saved_finding
from polymath.lineage_models import ResearchRunParent
from polymath.models import ResearchRun
from polymath.research.models import BridgeFinding

MIN_FIELDS = 3
MAX_FIELDS = 200
MAX_CIRCLE = 240
MAX_PROGRAMMES_PER_OWNER = 20
MAX_STEPS = 400
MAX_PREMISES = 4
MIN_CEILING = 100_000
MAX_CEILING = 150_000_000
# A conservative planning figure per investigation until real usage is known;
# the larger of this and any recorded per-run cap is held back before each step.
STEP_ESTIMATE_MICRODOLLARS = 500_000
THREAD_FAILURE_STREAK = 3
GOAL_LIMIT = 4000


class ConvergeError(Exception):
    """Sanitized code; never echoes stored or submitted text."""


def _theme(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConvergeError("invalid_converge_request")
    value = " ".join(value.split())
    if not value:
        return None
    if len(value) > 2000 or any(ord(character) < 32 for character in value):
        raise ConvergeError("invalid_converge_request")
    return value


def _ceiling(value: object) -> int:
    if type(value) is not int or not MIN_CEILING <= value <= MAX_CEILING:
        raise ConvergeError("invalid_converge_ceiling")
    return value


def _chosen(values: object, room: int) -> list[str]:
    if not isinstance(values, list) or len(values) > room:
        raise ConvergeError("invalid_converge_fields")
    names: list[str] = []
    for value in values:
        name = clean(value)
        if name is None:
            raise ConvergeError("invalid_converge_fields")
        if normalise(name) not in {normalise(existing) for existing in names}:
            names.append(name)
    return names


def thread_goal(theme: str | None, fields: list[str], touched: set[int], pairs: list[str]) -> str:
    """One thread's research goal, built server-side from stored names only."""
    about = f" about {_clip(theme, 600)}" if theme else ""
    fresh = [name for index, name in enumerate(fields) if index not in touched]
    rest = [name for index, name in enumerate(fields) if index in touched]
    goal = (
        f"Converge across fields{about}. From the fields below, choose two distant fields that "
        "have not yet been connected and that most plausibly share one deep structure, then test "
        "that connection against the literature and report its limitations. Prefer fields not "
        f"yet touched. Not yet touched: {'; '.join(fresh) or 'none'}."
    )
    if rest:
        goal += f" Already touched: {'; '.join(rest)}."
    if pairs:
        goal += f" Already connected: {'; '.join(pairs[-30:])}."
    return _clip(goal, GOAL_LIMIT)


def meta_goal(theme: str | None, level: int) -> str:
    about = f", for the theme: {_clip(theme, 1200)}" if theme else ""
    return _clip(
        "Investigate one deeper structure shared across these connections between fields"
        f"{about}. Test it against their original literature and report its limitations. "
        f"This is convergence level {level}.",
        GOAL_LIMIT,
    )


def _programme(session: Session, owner_hash: str, programme_id: uuid.UUID, *, lock=False):
    statement = select(ConvergeProgramme).where(
        ConvergeProgramme.id == programme_id,
        ConvergeProgramme.owner_session_hash == owner_hash,
    )
    row = session.scalar(statement.with_for_update() if lock else statement)
    if row is None:
        raise ConvergeError("converge_not_found")
    return row


def _steps(session: Session, programme: ConvergeProgramme) -> list[ConvergeStep]:
    return list(
        session.scalars(
            select(ConvergeStep)
            .where(ConvergeStep.programme_id == programme.id)
            .order_by(ConvergeStep.created_at, ConvergeStep.id)
        )
    )


def _names(programme: ConvergeProgramme) -> list[str]:
    return [field["name"] for field in programme.fields]


class ConvergeStore:
    def __init__(self, engine: Engine):
        if engine.dialect.name != "postgresql":
            raise ValueError("Converge programmes require PostgreSQL")
        self.engine = engine

    def create(
        self,
        owner_hash: str,
        *,
        theme: str | None,
        field_count: int,
        fields: list[str],
        ceiling_microdollars: int,
    ) -> dict:
        if type(field_count) is not int or not MIN_FIELDS <= field_count <= MAX_FIELDS:
            raise ConvergeError("invalid_converge_fields")
        theme = _theme(theme)
        chosen = _chosen(fields, field_count)
        circle = [{"name": name, "origin": "chosen"} for name in chosen] + [
            {"name": name, "origin": "spread"} for name in spread(chosen, field_count)
        ]
        if len(circle) < MIN_FIELDS:
            raise ConvergeError("invalid_converge_fields")
        with Session(self.engine) as session, session.begin():
            count = session.scalar(
                select(func.count())
                .select_from(ConvergeProgramme)
                .where(ConvergeProgramme.owner_session_hash == owner_hash)
            )
            if count >= MAX_PROGRAMMES_PER_OWNER:
                raise ConvergeError("converge_limit_reached")
            programme = ConvergeProgramme(
                id=uuid.uuid4(),
                owner_session_hash=owner_hash,
                theme=theme,
                fields=circle,
                ceiling_microdollars=_ceiling(ceiling_microdollars),
                max_steps=MAX_STEPS,
            )
            session.add(programme)
            session.flush()
            return self._snapshot(session, programme)

    def list(self, owner_hash: str) -> list[dict]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(ConvergeProgramme)
                .where(ConvergeProgramme.owner_session_hash == owner_hash)
                .order_by(ConvergeProgramme.created_at.desc(), ConvergeProgramme.id)
                .limit(MAX_PROGRAMMES_PER_OWNER)
            ).all()
            return [self._summary(session, row) for row in rows]

    def get(self, owner_hash: str, programme_id: uuid.UUID) -> dict:
        with Session(self.engine) as session:
            return self._snapshot(session, _programme(session, owner_hash, programme_id))

    def set_status(self, owner_hash: str, programme_id: uuid.UUID, status: str) -> dict:
        if status not in ("active", "paused", "stopped"):
            raise ConvergeError("invalid_converge_request")
        with Session(self.engine) as session, session.begin():
            programme = _programme(session, owner_hash, programme_id, lock=True)
            if programme.status in ("completed", "stopped"):
                raise ConvergeError("converge_finished")
            programme.status = status
            programme.updated_at = func.clock_timestamp()
            session.flush()
            return self._snapshot(session, programme)

    def extend(
        self,
        owner_hash: str,
        programme_id: uuid.UUID,
        *,
        fields: list[str],
        add_count: int,
        ceiling_microdollars: int | None,
    ) -> dict:
        """Widen the circle or raise the ceiling, and continue a finished programme."""
        if type(add_count) is not int or not 0 <= add_count <= MAX_FIELDS:
            raise ConvergeError("invalid_converge_fields")
        with Session(self.engine) as session, session.begin():
            programme = _programme(session, owner_hash, programme_id, lock=True)
            if programme.status == "stopped":
                raise ConvergeError("converge_finished")
            existing = _names(programme)
            room = MAX_CIRCLE - len(existing)
            chosen = [
                name
                for name in _chosen(fields, room)
                if normalise(name) not in {normalise(value) for value in existing}
            ]
            extra = max(0, min(add_count, room) - len(chosen))
            added = [{"name": name, "origin": "chosen"} for name in chosen] + [
                {"name": name, "origin": "spread"}
                for name in spread(existing + chosen, len(existing) + len(chosen) + extra)
            ]
            if ceiling_microdollars is not None:
                ceiling = _ceiling(ceiling_microdollars)
                if ceiling < programme.ceiling_microdollars:
                    raise ConvergeError("invalid_converge_ceiling")
                programme.ceiling_microdollars = ceiling
            if not added and ceiling_microdollars is None:
                raise ConvergeError("invalid_converge_request")
            programme.fields = [*programme.fields, *added]
            # A widened circle earns fresh attempts at threads.
            programme.finished_reason = None
            programme.threads_from = func.clock_timestamp()
            programme.status = "active" if programme.status == "completed" else programme.status
            programme.updated_at = func.clock_timestamp()
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
        kind: str,
        premise_result_ids: list[str],
        run_id: uuid.UUID,
    ) -> dict:
        """Link a just-created owned run to the step that is due."""
        with Session(self.engine) as session, session.begin():
            programme = _programme(session, owner_hash, programme_id, lock=True)
            due = self._next(session, programme)
            if (
                due.get("state") != "due"
                or due.get("kind") != kind
                or due.get("parent_result_ids") != list(premise_result_ids)
            ):
                raise ConvergeError("converge_step_not_due")
            run = session.scalar(
                select(ResearchRun)
                .join(ResearchRunAccess, ResearchRunAccess.run_id == ResearchRun.id)
                .where(ResearchRun.id == run_id, ResearchRunAccess.session_hash == owner_hash)
            )
            if (
                run is None
                or run.mode != "explore"
                or run.task_kind != "research"
                or session.scalar(select(ConvergeStep.id).where(ConvergeStep.run_id == run_id))
            ):
                raise ConvergeError("converge_run_invalid")
            parents = set(
                session.scalars(
                    select(ResearchRunParent.parent_result_id).where(
                        ResearchRunParent.run_id == run.id
                    )
                )
            )
            if parents != {uuid.UUID(value) for value in premise_result_ids}:
                raise ConvergeError("converge_run_invalid")
            session.add(
                ConvergeStep(
                    id=uuid.uuid4(),
                    programme_id=programme.id,
                    kind=kind,
                    level=due["level"],
                    run_id=run.id,
                    premise_result_ids=list(premise_result_ids),
                )
            )
            programme.updated_at = func.clock_timestamp()
            session.flush()
            return self._snapshot(session, programme)

    # Internal steps -----------------------------------------------------------

    def _spent(self, session: Session, run_ids: list[uuid.UUID]) -> int:
        if not run_ids:
            return 0
        return int(
            session.scalar(
                select(
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    BudgetReservation.status == "settled",
                                    BudgetReservation.actual_microdollars,
                                ),
                                else_=BudgetReservation.estimated_microdollars,
                            )
                        ),
                        0,
                    )
                ).where(BudgetReservation.run_id.in_(run_ids))
            )
        )

    def _reserve(self, session: Session, run_ids: list[uuid.UUID]) -> int:
        cap = (
            session.scalar(
                select(func.max(BudgetRunLimit.cap_microdollars)).where(
                    BudgetRunLimit.run_id.in_(run_ids)
                )
            )
            if run_ids
            else None
        )
        return max(STEP_ESTIMATE_MICRODOLLARS, int(cap or 0))

    def _finish(self, programme: ConvergeProgramme, reason: str) -> dict:
        programme.status = "completed"
        programme.finished_reason = reason
        programme.updated_at = func.clock_timestamp()
        return {"state": "completed", "reason": reason}

    def _next(self, session: Session, programme: ConvergeProgramme) -> dict:
        steps = _steps(session, programme)
        running = next((step for step in steps if step.absorbed_at is None), None)
        if running is not None:
            return {"state": "waiting", "run_id": str(running.run_id)}
        if programme.status != "active":
            return {"state": programme.status}
        if len(steps) >= programme.max_steps:
            return self._finish(programme, "step_limit")
        run_ids = [step.run_id for step in steps]
        if self._spent(session, run_ids) + self._reserve(session, run_ids) > (
            programme.ceiling_microdollars
        ):
            return self._finish(programme, "ceiling")
        plan = self._plan(programme, steps)
        if plan is None:
            held = any(step.outcome == "bridge" for step in steps)
            return self._finish(programme, "converged" if held else "no_connections")
        kind, level, premises = plan
        fields = _names(programme)
        if kind == "thread":
            touched = {
                index for step in steps if step.kind == "thread" for index in step.field_indices
            }
            pairs = [
                f"{step.field_left} – {step.field_right}"
                for step in steps
                if step.kind == "thread" and step.outcome == "bridge"
            ]
            goal = thread_goal(programme.theme, fields, touched, pairs)
        else:
            goal = meta_goal(programme.theme, level)
        return {
            "state": "due",
            "kind": kind,
            "level": level,
            "mode": "explore",
            "goal": goal,
            "parent_result_ids": premises,
        }

    def _plan(
        self, programme: ConvergeProgramme, steps: list[ConvergeStep]
    ) -> tuple[str, int, list[str]] | None:
        held = [step for step in steps if step.outcome == "bridge" and step.result_id is not None]
        used = {value for step in steps if step.kind == "meta" for value in step.premise_result_ids}
        fresh = sorted(
            (step for step in held if str(step.result_id) not in used),
            key=lambda step: (step.level, step.created_at),
        )
        by_level: dict[int, list[ConvergeStep]] = {}
        for step in fresh:
            by_level.setdefault(step.level, []).append(step)
        for level in sorted(by_level):
            if len(by_level[level]) >= MAX_PREMISES:
                group = by_level[level][:MAX_PREMISES]
                return "meta", level + 1, [str(step.result_id) for step in group]
        threads = [step for step in steps if step.kind == "thread"]
        touched = {index for step in threads for index in step.field_indices}
        untouched = len(programme.fields) - len(touched)
        streak = 0
        for step in reversed(threads):
            if step.outcome == "bridge" or (
                programme.threads_from is not None and step.created_at < programme.threads_from
            ):
                break
            streak += 1
        if (
            untouched > 0
            and len(threads) < len(programme.fields)
            and streak < THREAD_FAILURE_STREAK
        ):
            return "thread", 1, []
        if len(fresh) >= 2:
            group = fresh[:MAX_PREMISES]
            return (
                "meta",
                max(step.level for step in group) + 1,
                [str(step.result_id) for step in group],
            )
        return None

    def _absorb(self, session: Session, programme: ConvergeProgramme) -> None:
        steps = _steps(session, programme)
        by_result = {str(step.result_id): step for step in steps if step.result_id is not None}
        absorbed = False
        for step in steps:
            if step.absorbed_at is not None:
                continue
            run = session.get(ResearchRun, step.run_id)
            if run is None or run.status not in TERMINAL_RUN_STATUSES:
                continue
            result_id, finding = (
                _saved_finding(session, run) if run.status == "succeeded" else (None, None)
            )
            step.result_id = result_id
            step.absorbed_at = func.clock_timestamp()
            absorbed = True
            if step.kind == "meta":
                step.field_indices = sorted(
                    {
                        index
                        for value in step.premise_result_ids
                        if value in by_result
                        for index in by_result[value].field_indices
                    }
                )
            if run.status != "succeeded":
                step.outcome = run.status
                continue
            if not isinstance(finding, BridgeFinding):
                step.outcome = "insufficient_evidence"
                continue
            step.outcome = "bridge"
            step.title = _clip(finding.title, 300)
            step.field_left = _clip(finding.field_left, 200)
            step.field_right = _clip(finding.field_right, 200)
            if step.kind == "thread":
                step.field_indices = [
                    self._field_index(programme, name)
                    for name in (finding.field_left, finding.field_right)
                ]
                step.field_indices = sorted({index for index in step.field_indices if index >= 0})
            if result_id is not None:
                by_result[str(result_id)] = step
        if absorbed:
            programme.updated_at = func.clock_timestamp()
            session.flush()

    def _field_index(self, programme: ConvergeProgramme, name: str) -> int:
        """The circle position of a field the model connected, adding it if it is new."""
        names = _names(programme)
        index = match(name, names)
        if index is not None:
            return index
        value = clean(_clip(name, 100))
        if value is None or len(names) >= MAX_CIRCLE:
            return -1
        programme.fields = [*programme.fields, {"name": value, "origin": "found"}]
        return len(names)

    def _summary(self, session: Session, programme: ConvergeProgramme) -> dict:
        held = session.scalar(
            select(func.count())
            .select_from(ConvergeStep)
            .where(ConvergeStep.programme_id == programme.id, ConvergeStep.outcome == "bridge")
        )
        return {
            "id": str(programme.id),
            "theme": programme.theme,
            "status": programme.status,
            "field_count": len(programme.fields),
            "connections_held": held,
            "created_at": programme.created_at.isoformat(),
            "updated_at": programme.updated_at.isoformat(),
        }

    def _snapshot(self, session: Session, programme: ConvergeProgramme) -> dict:
        steps = _steps(session, programme)
        run_ids = [step.run_id for step in steps]
        step_by_result = {str(step.result_id): step for step in steps if step.result_id}
        used = {value for step in steps if step.kind == "meta" for value in step.premise_result_ids}
        touched = {
            index
            for step in steps
            if step.kind == "thread" and step.outcome == "bridge"
            for index in step.field_indices
        }
        held = [step for step in steps if step.outcome == "bridge"]
        top = max((step.level for step in held), default=0)
        deepest = [
            str(step.id) for step in held if step.level == top and str(step.result_id) not in used
        ]

        def state(step: ConvergeStep) -> str:
            if step.absorbed_at is None:
                return "running"
            if step.outcome == "bridge":
                return "held"
            if step.outcome == "insufficient_evidence":
                return "not_held"
            return "stopped"

        summary = self._summary(session, programme)
        summary.update(
            {
                "finished_reason": programme.finished_reason,
                "fields": [
                    {**field, "touched": index in touched}
                    for index, field in enumerate(programme.fields)
                ],
                "ceiling_microdollars": programme.ceiling_microdollars,
                "spent_microdollars": self._spent(session, run_ids),
                "step_estimate_microdollars": STEP_ESTIMATE_MICRODOLLARS,
                "max_steps": programme.max_steps,
                "steps_used": len(steps),
                "deepest_step_ids": deepest if top >= 1 else [],
                "steps": [
                    {
                        "id": str(step.id),
                        "kind": step.kind,
                        "level": step.level,
                        "status": state(step),
                        "title": step.title,
                        "field_left": step.field_left,
                        "field_right": step.field_right,
                        "field_indices": list(step.field_indices),
                        "premise_step_ids": [
                            str(step_by_result[value].id)
                            for value in step.premise_result_ids
                            if value in step_by_result
                        ],
                        "run_id": str(step.run_id),
                        "result_id": str(step.result_id) if step.result_id else None,
                    }
                    for step in steps
                ],
            }
        )
        return summary


def catalogue_size() -> int:
    return len(CATALOGUE)
