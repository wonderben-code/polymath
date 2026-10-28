"""Exact checks over a small finite integer domain, never general mathematical proofs.

The language has no calls, parsing of mathematical source code, imports, or I/O.
Every expression, domain, arithmetic result, and evaluation has a hard bound.
The model supplies a conjecture encoding; checking it does not establish that
the encoding faithfully represents its scientific prose or the open literature.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, ValidationError, model_validator

from polymath.research.models import StrictModel

MAX_AST_DEPTH = 8
MAX_AST_NODES = 64
MAX_ASSIGNMENTS = 4096
MAX_INTEGER_BITS = 256
MAX_EVALUATION_NODES = 1_000_000

VariableName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,15}$")]
ContentHash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ExactInteger = Annotated[
    str,
    StringConstraints(pattern=r"^(0|[1-9][0-9]{0,77}|-[1-9][0-9]{0,77})$", max_length=79),
]


class IntegerVariable(StrictModel):
    name: VariableName
    lower: Annotated[int, Field(ge=-20, le=20)]
    upper: Annotated[int, Field(ge=-20, le=20)]

    @model_validator(mode="after")
    def ordered_bounds(self) -> IntegerVariable:
        if self.lower > self.upper:
            raise ValueError("finite_variable_bounds_invalid")
        return self


class ConstantExpression(StrictModel):
    op: Literal["constant"]
    value: Annotated[int, Field(ge=-1000, le=1000)]


class VariableExpression(StrictModel):
    op: Literal["variable"]
    name: VariableName


class AddExpression(StrictModel):
    op: Literal["add"]
    left: Expression
    right: Expression


class SubtractExpression(StrictModel):
    op: Literal["subtract"]
    left: Expression
    right: Expression


class MultiplyExpression(StrictModel):
    op: Literal["multiply"]
    left: Expression
    right: Expression


class PowerExpression(StrictModel):
    """Integer power; exponent zero returns one, including a zero base."""

    op: Literal["power"]
    base: Expression
    exponent: Annotated[int, Field(ge=0, le=6)]


Expression = Annotated[
    ConstantExpression
    | VariableExpression
    | AddExpression
    | SubtractExpression
    | MultiplyExpression
    | PowerExpression,
    Field(discriminator="op"),
]

for _expression_type in (
    AddExpression,
    SubtractExpression,
    MultiplyExpression,
    PowerExpression,
):
    _expression_type.model_rebuild()


class Relation(StrictModel):
    left: Expression
    operator: Literal["eq", "lt", "le"]
    right: Expression


def _children(expression: Expression) -> tuple[Expression, ...]:
    if isinstance(expression, (AddExpression, SubtractExpression, MultiplyExpression)):
        return expression.left, expression.right
    if isinstance(expression, PowerExpression):
        return (expression.base,)
    return ()


class FiniteConjecture(StrictModel):
    variables: Annotated[tuple[IntegerVariable, ...], Field(min_length=1, max_length=3)]
    assumptions: Annotated[tuple[Relation, ...], Field(max_length=8)] = ()
    conclusion: Relation

    @model_validator(mode="after")
    def bounded_language(self) -> FiniteConjecture:
        names = {variable.name for variable in self.variables}
        if len(names) != len(self.variables):
            raise ValueError("finite_variables_must_be_distinct")
        if self.total_assignments > MAX_ASSIGNMENTS:
            raise ValueError("finite_domain_limit")
        pending = [
            (expression, 1)
            for relation in (*self.assumptions, self.conclusion)
            for expression in (relation.left, relation.right)
        ]
        nodes = 0
        while pending:
            expression, depth = pending.pop()
            nodes += 1
            if nodes > MAX_AST_NODES or depth > MAX_AST_DEPTH:
                raise ValueError("finite_expression_limit")
            if isinstance(expression, VariableExpression) and expression.name not in names:
                raise ValueError("finite_variable_not_declared")
            pending.extend((child, depth + 1) for child in _children(expression))
        return self

    @property
    def total_assignments(self) -> int:
        return math.prod(variable.upper - variable.lower + 1 for variable in self.variables)

    @property
    def content_sha256(self) -> str:
        encoded = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class FiniteCheckResult(StrictModel):
    conjecture: FiniteConjecture
    conjecture_sha256: ContentHash
    status: Literal[
        "counterexample_found",
        "no_counterexample_in_domain",
        "no_applicable_cases",
        "limit_reached",
    ]
    assignments_tested: Annotated[int, Field(ge=0, le=MAX_ASSIGNMENTS)]
    applicable_cases: Annotated[int, Field(ge=0, le=MAX_ASSIGNMENTS)]
    total_assignments: Annotated[int, Field(ge=1, le=MAX_ASSIGNMENTS)]
    witness: Annotated[dict[VariableName, int], Field(max_length=3)] | None = None
    # Decimal strings retain exact 256-bit values in browsers and JSON exports.
    lhs: ExactInteger | None = None
    rhs: ExactInteger | None = None
    error_code: Literal["integer_size_limit", "evaluation_limit"] | None = None
    scope: Literal["finite_integer_domain"] = "finite_integer_domain"
    proof_status: Literal["not_a_general_proof"] = "not_a_general_proof"

    @model_validator(mode="after")
    def consistent_shape(self) -> FiniteCheckResult:
        if self.conjecture_sha256 != self.conjecture.content_sha256:
            raise ValueError("finite_check_hash_mismatch")
        if self.total_assignments != self.conjecture.total_assignments:
            raise ValueError("finite_check_domain_mismatch")
        if not self.applicable_cases <= self.assignments_tested <= self.total_assignments:
            raise ValueError("finite_check_counts_invalid")
        for value in (self.lhs, self.rhs):
            if value is not None and abs(int(value)).bit_length() > MAX_INTEGER_BITS:
                raise ValueError("finite_check_integer_limit")
        if self.status == "counterexample_found":
            if (
                not self.applicable_cases
                or self.witness is None
                or self.lhs is None
                or self.rhs is None
                or self.error_code is not None
            ):
                raise ValueError("finite_check_witness_required")
            expected = {variable.name for variable in self.conjecture.variables}
            if set(self.witness) != expected or any(
                not variable.lower <= self.witness[variable.name] <= variable.upper
                for variable in self.conjecture.variables
            ):
                raise ValueError("finite_check_witness_invalid")
        elif self.witness is not None or self.lhs is not None or self.rhs is not None:
            raise ValueError("finite_check_unexpected_witness")
        if self.status == "limit_reached":
            if self.error_code is None:
                raise ValueError("finite_check_limit_reason_required")
        elif self.error_code is not None:
            raise ValueError("finite_check_unexpected_limit")
        if self.status in ("no_counterexample_in_domain", "no_applicable_cases"):
            if self.assignments_tested != self.total_assignments:
                raise ValueError("finite_check_incomplete_enumeration")
            if (self.status == "no_applicable_cases") != (self.applicable_cases == 0):
                raise ValueError("finite_check_applicability_mismatch")
        return self


class _CheckLimit(Exception):
    """A static bounded-check code; no source or model text is included."""


class _Evaluator:
    def __init__(self) -> None:
        self.nodes = 0

    def expression(self, expression: Expression, assignment: dict[str, int]) -> int:
        self.nodes += 1
        if self.nodes > MAX_EVALUATION_NODES:
            raise _CheckLimit("evaluation_limit")
        if isinstance(expression, ConstantExpression):
            result = expression.value
        elif isinstance(expression, VariableExpression):
            result = assignment[expression.name]
        elif isinstance(expression, PowerExpression):
            result = self.expression(expression.base, assignment) ** expression.exponent
        else:
            left = self.expression(expression.left, assignment)
            right = self.expression(expression.right, assignment)
            if isinstance(expression, AddExpression):
                result = left + right
            elif isinstance(expression, SubtractExpression):
                result = left - right
            else:
                result = left * right
        if abs(result).bit_length() > MAX_INTEGER_BITS:
            raise _CheckLimit("integer_size_limit")
        return result

    def relation(self, relation: Relation, assignment: dict[str, int]) -> tuple[bool, int, int]:
        left = self.expression(relation.left, assignment)
        right = self.expression(relation.right, assignment)
        if relation.operator == "eq":
            satisfied = left == right
        elif relation.operator == "lt":
            satisfied = left < right
        else:
            satisfied = left <= right
        return satisfied, left, right


def _validated_conjecture(conjecture: FiniteConjecture) -> FiniteConjecture:
    try:
        if not isinstance(conjecture, FiniteConjecture):
            raise ValueError
        return FiniteConjecture.model_validate_json(conjecture.model_dump_json())
    except (ValueError, TypeError, ValidationError):
        raise ValueError("invalid_finite_conjecture") from None


def check_conjecture(conjecture: FiniteConjecture) -> FiniteCheckResult:
    """Enumerate exact integer assignments in a deterministic, bounded order.

    A witness refutes the supplied encoding on its explicit finite domain. An
    exhausted finite domain never produces a general or scientific proof label.
    """
    conjecture = _validated_conjecture(conjecture)
    evaluator = _Evaluator()
    tested = applicable = 0

    def result(status: str, **values) -> FiniteCheckResult:
        return FiniteCheckResult(
            conjecture=conjecture,
            conjecture_sha256=conjecture.content_sha256,
            status=status,
            assignments_tested=tested,
            applicable_cases=applicable,
            total_assignments=conjecture.total_assignments,
            **values,
        )

    domains = (range(variable.lower, variable.upper + 1) for variable in conjecture.variables)
    try:
        for values in itertools.product(*domains):
            assignment = dict(
                zip((item.name for item in conjecture.variables), values, strict=True)
            )
            tested += 1
            if not all(
                evaluator.relation(assumption, assignment)[0]
                for assumption in conjecture.assumptions
            ):
                continue
            applicable += 1
            satisfied, left, right = evaluator.relation(conjecture.conclusion, assignment)
            if not satisfied:
                return result(
                    "counterexample_found", witness=assignment, lhs=str(left), rhs=str(right)
                )
    except _CheckLimit as error:
        return result("limit_reached", error_code=str(error))
    return result("no_counterexample_in_domain" if applicable else "no_applicable_cases")


def validate_check(
    result: FiniteCheckResult, conjecture: FiniteConjecture | None = None
) -> FiniteCheckResult:
    """Recompute authoritative check data; never trust model-supplied outcomes."""
    try:
        if not isinstance(result, FiniteCheckResult):
            raise ValueError
        result = FiniteCheckResult.model_validate_json(result.model_dump_json())
        if conjecture is not None:
            conjecture = _validated_conjecture(conjecture)
            if conjecture.content_sha256 != result.conjecture_sha256:
                raise ValueError
        if check_conjecture(result.conjecture) != result:
            raise ValueError
        return result
    except (ValueError, TypeError, ValidationError):
        raise ValueError("finite_check_integrity_failed") from None
