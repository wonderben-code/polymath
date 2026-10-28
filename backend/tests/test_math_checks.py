"""Checks verify the finite encoding, never a general mathematical theorem."""

import json

import pytest
from pydantic import ValidationError

import polymath.math_checks as checks
from polymath.math_checks import (
    FiniteCheckResult,
    FiniteConjecture,
    check_conjecture,
    validate_check,
)


def constant(value):
    return {"op": "constant", "value": value}


def variable(name="n"):
    return {"op": "variable", "name": name}


def binary(op, left, right):
    return {"op": op, "left": left, "right": right}


def power(base, exponent):
    return {"op": "power", "base": base, "exponent": exponent}


def relation(left, operator, right):
    return {"left": left, "operator": operator, "right": right}


def conjecture_data(*, lower=-2, upper=2, conclusion=None, assumptions=()):
    return {
        "variables": [{"name": "n", "lower": lower, "upper": upper}],
        "assumptions": list(assumptions),
        "conclusion": conclusion or relation(variable(), "eq", variable()),
    }


def conjecture(**values):
    return FiniteConjecture.model_validate_json(json.dumps(conjecture_data(**values)))


def test_actual_counterexample_satisfies_assumptions_and_retains_exact_values():
    statement = conjecture(
        assumptions=(relation(constant(0), "le", variable()),),
        conclusion=relation(power(variable(), 2), "le", variable()),
    )
    result = check_conjecture(statement)
    assert result.status == "counterexample_found"
    assert result.witness == {"n": 2}
    assert result.lhs == "4"
    assert result.rhs == "2"
    assert result.assignments_tested == 5
    assert result.applicable_cases == 3
    assert result.total_assignments == 5
    assert result.conjecture == statement
    assert result.conjecture_sha256 == statement.content_sha256
    assert result.scope == "finite_integer_domain"
    assert result.proof_status == "not_a_general_proof"
    assert validate_check(result, statement) == result


def test_exhaustive_finite_success_does_not_become_a_general_proof():
    statement = conjecture(conclusion=relation(power(variable(), 2), "le", constant(4)))
    result = check_conjecture(statement)
    assert result.status == "no_counterexample_in_domain"
    assert result.assignments_tested == result.total_assignments == 5
    assert result.applicable_cases == 5
    assert result.witness is result.lhs is result.rhs is None
    assert result.proof_status == "not_a_general_proof"
    # The same encoded inequality fails outside the original finite domain.
    wider = conjecture(lower=-3, upper=3, conclusion=statement.conclusion.model_dump())
    assert check_conjecture(wider).status == "counterexample_found"


def test_assumptions_with_no_applicable_cases_do_not_count_as_support():
    statement = conjecture(assumptions=(relation(variable(), "lt", constant(-10)),))
    result = check_conjecture(statement)
    assert result.status == "no_applicable_cases"
    assert result.applicable_cases == 0
    assert result.assignments_tested == result.total_assignments == 5
    assert result.proof_status == "not_a_general_proof"


@pytest.mark.parametrize("operator", ["eq", "lt", "le"])
def test_relation_operators_and_integer_arithmetic_are_exact(operator):
    left = binary(
        "subtract",
        binary("multiply", binary("add", variable(), constant(1)), constant(2)),
        constant(1),
    )
    statement = conjecture(lower=2, upper=2, conclusion=relation(left, operator, constant(6)))
    result = check_conjecture(statement)
    assert result.status == (
        "counterexample_found" if operator == "eq" else "no_counterexample_in_domain"
    )
    if operator == "eq":
        assert (result.lhs, result.rhs) == ("5", "6")


def test_zero_power_has_explicit_integer_language_semantics():
    statement = conjecture(conclusion=relation(power(constant(0), 0), "eq", constant(1)))
    assert check_conjecture(statement).status == "no_counterexample_in_domain"


def test_two_variable_counterexample_is_deterministic_in_declared_order():
    data = conjecture_data()
    data["variables"] = [
        {"name": "a", "lower": -1, "upper": 1},
        {"name": "b", "lower": -1, "upper": 1},
    ]
    data["conclusion"] = relation(variable("a"), "le", variable("b"))
    statement = FiniteConjecture.model_validate_json(json.dumps(data))
    first = check_conjecture(statement)
    assert first.witness == {"a": 0, "b": -1}
    assert first.assignments_tested == first.applicable_cases == 4
    assert first.total_assignments == 9
    assert check_conjecture(statement) == first


def test_domain_boundary_enumerates_every_assignment():
    data = conjecture_data(lower=0, upper=15)
    data["variables"].extend({"name": name, "lower": 0, "upper": 15} for name in ("m", "p"))
    statement = FiniteConjecture.model_validate_json(json.dumps(data))
    result = check_conjecture(statement)
    assert result.total_assignments == result.assignments_tested == 4096
    assert result.status == "no_counterexample_in_domain"


@pytest.mark.parametrize(
    "change",
    [
        {"variables": []},
        {"variables": [{"name": "n", "lower": 2, "upper": -2}]},
        {"variables": [{"name": "n", "lower": -21, "upper": 0}]},
        {"variables": [{"name": "n", "lower": 0, "upper": 21}]},
        {"variables": [{"name": "not a variable", "lower": 0, "upper": 1}]},
        {"variables": [{"name": "__import__", "lower": 0, "upper": 1}]},
        {"variables": [{"name": "n", "lower": 0, "upper": 1}] * 2},
        {"variables": [{"name": name, "lower": 0, "upper": 16} for name in ("n", "m", "p")]},
        {"variables": [{"name": name, "lower": 0, "upper": 0} for name in ("n", "m", "p", "q")]},
        {"assumptions": [relation(constant(0), "eq", constant(0))] * 9},
        {"conclusion": relation(variable("undeclared"), "eq", constant(1))},
        {"conclusion": relation(constant(1001), "eq", constant(1))},
        {"conclusion": relation(power(variable(), 7), "eq", constant(1))},
        {"conclusion": relation(power(variable(), -1), "eq", constant(1))},
        {"conclusion": relation(variable(), "__import__", constant(1))},
        {"conclusion": relation({"op": "call", "function": "eval"}, "eq", constant(1))},
        {
            "conclusion": relation(
                {"op": "constant", "value": "__import__('os')"}, "eq", constant(1)
            )
        },
        {"code": "raise RuntimeError('never executed')"},
    ],
)
def test_out_of_language_inputs_are_rejected_before_evaluation(change):
    data = conjecture_data()
    data.update(change)
    with pytest.raises(ValidationError):
        FiniteConjecture.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("value", [True, False, 1.0, "1"])
@pytest.mark.parametrize("position", ["bound", "constant", "exponent"])
def test_integer_contract_does_not_coerce_booleans_or_other_types(value, position):
    data = conjecture_data()
    if position == "bound":
        data["variables"][0]["lower"] = value
    elif position == "constant":
        data["conclusion"]["left"] = constant(value)
    else:
        data["conclusion"]["left"] = power(variable(), value)
    with pytest.raises(ValidationError):
        FiniteConjecture.model_validate_json(json.dumps(data))


def test_expression_depth_boundary_is_bounded_across_nested_powers():
    expression = constant(1)
    for _ in range(7):
        expression = power(expression, 1)
    valid = conjecture(conclusion=relation(expression, "eq", constant(1)))
    assert check_conjecture(valid).status == "no_counterexample_in_domain"
    with pytest.raises(ValidationError, match="finite_expression_limit"):
        conjecture(conclusion=relation(power(expression, 1), "eq", constant(1)))


def test_ast_node_limit_counts_all_assumptions_and_conclusion():
    def tree(level):
        return constant(0) if level == 0 else binary("add", tree(level - 1), tree(level - 1))

    # A depth-six full binary tree has63nodes; the other side adds the64th.
    statement = conjecture(conclusion=relation(tree(5), "eq", constant(0)))
    assert check_conjecture(statement).status == "no_counterexample_in_domain"
    with pytest.raises(ValidationError, match="finite_expression_limit"):
        conjecture(
            conclusion=statement.conclusion.model_dump(),
            assumptions=(relation(constant(0), "eq", constant(0)),),
        )


def test_runtime_integer_bound_returns_typed_limit_instead_of_an_unbounded_value():
    expression = power(power(constant(1000), 6), 6)
    statement = conjecture(conclusion=relation(expression, "eq", constant(0)))
    result = check_conjecture(statement)
    assert result.status == "limit_reached"
    assert result.error_code == "integer_size_limit"
    assert result.assignments_tested == result.applicable_cases == 1
    assert result.witness is result.lhs is result.rhs is None
    assert validate_check(result) == result


def test_evaluation_budget_is_a_typed_static_failure(monkeypatch):
    monkeypatch.setattr(checks, "MAX_EVALUATION_NODES", 2)
    result = check_conjecture(conjecture())
    assert result.status == "limit_reached"
    assert result.error_code == "evaluation_limit"
    assert result.assignments_tested == result.applicable_cases == 2


def test_large_exact_values_are_decimal_strings_in_json_for_browser_safety():
    expression = power(constant(1000), 6)
    result = check_conjecture(conjecture(conclusion=relation(expression, "eq", constant(0))))
    assert result.lhs == "1000000000000000000"
    raw = json.loads(result.model_dump_json())
    assert raw["lhs"] == "1000000000000000000"
    assert isinstance(raw["lhs"], str)
    assert validate_check(result) == result


def test_hash_and_result_ignore_input_json_key_order_but_bind_the_exact_encoding():
    data = conjecture_data()
    first = FiniteConjecture.model_validate_json(json.dumps(data))
    reordered = FiniteConjecture.model_validate_json(json.dumps(data, sort_keys=True))
    assert first.content_sha256 == reordered.content_sha256
    assert check_conjecture(first) == check_conjecture(reordered)
    changed = conjecture(upper=3)
    assert first.content_sha256 != changed.content_sha256
    with pytest.raises(ValueError, match="^finite_check_integrity_failed$"):
        validate_check(check_conjecture(first), changed)


@pytest.mark.parametrize(
    "update",
    [
        {"conjecture_sha256": "0" * 64},
        {"witness": {"n": -1}},
        {"lhs": "999"},
        {"rhs": "999"},
        {"assignments_tested": 1},
        {"applicable_cases": 1},
        {"proof_status": "proved"},
        {"scope": "all_integers"},
    ],
)
def test_check_integrity_recomputes_witness_counts_values_and_proof_labels(update):
    statement = conjecture(
        assumptions=(relation(constant(0), "le", variable()),),
        conclusion=relation(power(variable(), 2), "le", variable()),
    )
    result = check_conjecture(statement)
    forged = result.model_copy(update=update)
    with pytest.raises(ValueError, match="^finite_check_integrity_failed$"):
        validate_check(forged)


@pytest.mark.parametrize("number", ["-0", "01", "+1", "1.0", str(2**256), "1" * 100])
def test_result_decimal_integers_must_be_canonical_and_bounded(number):
    result = check_conjecture(conjecture(conclusion=relation(variable(), "eq", constant(0))))
    raw = result.model_dump(mode="json")
    raw["lhs"] = number
    with pytest.raises(ValidationError):
        FiniteCheckResult.model_validate_json(json.dumps(raw))


def test_model_copy_cannot_bypass_domain_validation_at_evaluation_boundary():
    invalid = conjecture().model_copy(update={"variables": ()})
    with pytest.raises(ValueError, match="^invalid_finite_conjecture$"):
        check_conjecture(invalid)
