"""Compare ladder extensions against real pre-F6 artifacts, not new self-derived hashes.

The synthetic fixture was captured using an isolated git archive of 9e32751.
It contains no real credential, native call or provider fact.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from polymath.meta_research import native_run_input_sha256
from polymath.meta_research_contracts import (
    MetaResearchManifest,
    canonical_research_manifest_json,
    controller_protocol_sha256,
    research_manifest_sha256,
    task_profile_sha256,
)

BASELINE = json.loads(
    (Path(__file__).parent / "fixtures" / "pre_discovery_ladder_manifest.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("kind", tuple(BASELINE["controller_sha256"]))
def test_ordinary_controller_protocol_matches_pre_ladder_revision(kind):
    assert controller_protocol_sha256(kind) == BASELINE["controller_sha256"][kind]


def test_saved_five_task_manifest_retains_exact_canonical_bytes_and_hash():
    raw = BASELINE["manifest"]
    parsed = MetaResearchManifest.model_validate_json(json.dumps(raw))
    expected = json.dumps(
        raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    assert canonical_research_manifest_json(parsed) == expected
    assert research_manifest_sha256(parsed) == BASELINE["manifest_sha256"]
    assert "discovery_ladder_profile" not in json.loads(expected)
    for saved, profile in zip(raw["task_profiles"], parsed.task_profiles, strict=True):
        assert task_profile_sha256(profile.profile) == saved["profile_sha256"]


def test_absent_initial_discovery_intent_keeps_original_scientific_input_hash():
    class EmptyIntentSession:
        def get(self, model, identifier):
            return None

        def scalar(self, statement):
            return None

        def scalars(self, statement):
            return SimpleNamespace(all=lambda: ())

    intent = BASELINE["intent"]
    run = SimpleNamespace(**(intent | {"id": UUID(intent["id"])}))
    assert native_run_input_sha256(EmptyIntentSession(), run) == BASELINE["intent_sha256"]
