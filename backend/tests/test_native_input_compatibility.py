"""Persisted old native inputs keep their exact archived D2 hashes without intents."""

import hashlib
import json
from pathlib import Path

import native_input_compatibility as helper
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_worker import database as database_fixture

from polymath.budget_models import BudgetAccount, BudgetReservation
from polymath.meta_research_models import NativeResearchRunBinding
from polymath.scientific_context_models import SIDECAR_MODELS

database = database_fixture
pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parent / "fixtures/pre_scientific_context_native_inputs.json"


def test_all_persisted_legacy_native_inputs_match_verified_archive_without_scientific_sidecars(
    database,
):
    baseline = json.loads(FIXTURE.read_text())
    cases = helper.seed_inputs(database)
    assert {name: row["run_id"] for name, row in baseline["cases"].items()} == cases
    assert helper.capture_inputs(database, cases) == {
        name: row["native_input_sha256"] for name, row in baseline["cases"].items()
    }
    with Session(database) as session:
        for model in (*SIDECAR_MODELS, BudgetAccount, BudgetReservation, NativeResearchRunBinding):
            assert session.scalar(select(func.count()).select_from(model)) == 0
    assert (
        baseline["input_helper_sha256"]
        == hashlib.sha256(Path(helper.__file__).read_bytes()).hexdigest()
    )
    assert baseline["provenance"]["source_commit"] == "6e3d75b663ff65e1f9ae3b518efcbaeb71368f56"
    assert (
        baseline["generator_sha256"]
        == hashlib.sha256(
            Path(__file__).with_name("generate_native_input_baseline.py").read_bytes()
        ).hexdigest()
    )
    assert (
        baseline["verified_imports"]["polymath.meta_research"]
        == "backend/src/polymath/meta_research.py"
    )
    assert baseline["checkpoint_fingerprints"] is None
