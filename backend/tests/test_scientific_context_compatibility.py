"""Old bytes captured from the actual pushed D2 archive, never new expected output."""

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
import scientific_context_baseline

BASELINE_PATH = Path(__file__).parent / "fixtures/pre_scientific_context_baseline.json"
BASELINE = json.loads(BASELINE_PATH.read_text())
CASES = [(section, case) for section, values in BASELINE["fixtures"].items() for case in values]


@pytest.fixture(scope="module")
def old_protocol_values():
    return asyncio.run(scientific_context_baseline.generate())


@pytest.mark.parametrize("section,case", CASES, ids=[f"{a}/{b}" for a, b in CASES])
def test_original_protocol_bytes_match_committed_d2_archive(old_protocol_values, section, case):
    assert old_protocol_values[section][case] == BASELINE["fixtures"][section][case]


def test_historical_fixture_records_exact_committed_provenance_and_explicit_omissions():
    assert BASELINE["format"] == "polymath-pre-scientific-context-baseline-v1"
    assert BASELINE["provenance"]["source_commit"] == "6e3d75b663ff65e1f9ae3b518efcbaeb71368f56"
    assert (
        BASELINE["generator_sha256"]
        == hashlib.sha256(Path(scientific_context_baseline.__file__).read_bytes()).hexdigest()
    )
    assert (
        "backend/src/polymath/meta_research_contracts.py"
        in BASELINE["provenance"]["verified_backend_inputs"]
    )
    assert BASELINE["omissions_requiring_postgresql"]
