"""SCI bytes captured from the pushed SC archive before Explore profile changes."""

import hashlib
import json
from pathlib import Path

import pre_explore_scientific_baseline
import pytest

BASELINE_PATH = Path(__file__).parent / "fixtures/pre_explore_scientific_baseline.json"
BASELINE = json.loads(BASELINE_PATH.read_text())
CASES = [(section, case) for section, values in BASELINE["fixtures"].items() for case in values]


@pytest.fixture(scope="module")
def current_scientific_values():
    return pre_explore_scientific_baseline.generate_scientific_values()


@pytest.mark.parametrize("section,case", CASES, ids=[f"{a}/{b}" for a, b in CASES])
def test_scientific_protocols_match_pushed_sc_archive(current_scientific_values, section, case):
    assert current_scientific_values[section][case] == BASELINE["fixtures"][section][case]


def test_scientific_fixture_has_exact_committed_provenance_and_unchanged_generator():
    assert BASELINE["format"] == "polymath-pre-explore-scientific-baseline-v1"
    provenance = BASELINE["provenance"]
    assert provenance["source_commit"] == "a95011ae46fcb3d66b784abab7444167e39c33fc"
    assert len(provenance["source_tree"]) == 40
    assert len(provenance["verified_backend_inputs"]) == 287
    for source in (
        "backend/src/polymath/meta_research_contracts.py",
        "backend/src/polymath/public_research_contracts.py",
        "backend/src/polymath/artifact_controller.py",
        "backend/src/polymath/artifact_contracts.py",
        "backend/src/polymath/research/ask_overview.py",
        "backend/src/polymath/assessment_overview.py",
        "backend/tests/test_artifact_context_controller.py",
        "backend/tests/test_ask_reassessment_overview.py",
    ):
        assert len(provenance["verified_backend_inputs"][source]["git_blob"]) == 40
        assert len(provenance["verified_backend_inputs"][source]["sha256"]) == 64
    assert (
        BASELINE["generator_sha256"]
        == hashlib.sha256(Path(pre_explore_scientific_baseline.__file__).read_bytes()).hexdigest()
    )
    assert BASELINE["omissions_requiring_postgresql"]


def test_archived_capture_json_and_digest_are_internally_exact():
    captures = []

    def visit(value):
        if isinstance(value, dict):
            if set(value) == {"value", "canonical_json", "sha256"}:
                captures.append(value)
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(BASELINE["fixtures"])
    assert len(captures) >= len(CASES)
    for value in captures:
        assert value["canonical_json"] == pre_explore_scientific_baseline.canonical(value["value"])
        assert (
            value["sha256"] == hashlib.sha256(value["canonical_json"].encode("utf-8")).hexdigest()
        )
