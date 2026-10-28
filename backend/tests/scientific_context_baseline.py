#!/usr/bin/env python3
"""Capture historical contracts ONLY from a verified, committed D2 archive.

Run with the repository's existing backend Python environment; this script never
installs dependencies or makes network/database calls. See --help for usage.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import importlib
import json
import socket
import subprocess
import sys
from pathlib import Path


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def capture(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    encoded = canonical(value)
    return {
        "value": value,
        "canonical_json": encoded,
        "sha256": hashlib.sha256(encoded.encode()).hexdigest(),
    }


def git(repository, *args):
    return subprocess.check_output(["git", "-C", str(repository), *args])


def verify_archive(repository, archive, source_commit):
    resolved = (
        git(repository, "rev-parse", "--verify", source_commit + "^{commit}").decode().strip()
    )
    if source_commit != resolved:
        raise ValueError("Supply the complete exact source commit, not a ref or short SHA")
    algorithm = git(repository, "rev-parse", "--show-object-format").decode().strip()
    if algorithm not in {"sha1", "sha256"}:
        raise ValueError("Unsupported Git object format")
    tree = git(repository, "rev-parse", source_commit + "^{tree}").decode().strip()
    entries = git(repository, "ls-tree", "-rz", "--full-tree", source_commit, "--", "backend")
    verified = {}
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        mode, kind, expected = metadata.decode().split()
        relative = raw_path.decode("utf-8")
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError("Unexpected backend Git entry: " + relative)
        path = archive / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError("Missing or non-regular archived input: " + relative)
        data = path.read_bytes()
        actual = hashlib.new(
            algorithm, b"blob " + str(len(data)).encode() + b"\0" + data
        ).hexdigest()
        if actual != expected:
            raise ValueError("Archive differs from exact Git commit: " + relative)
        verified[relative] = {
            "git_blob": expected,
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    if not verified:
        raise ValueError("Archive has no tracked backend inputs")
    # A fresh git archive contains no editable checkout additions or bytecode.
    # Refuse either so imports cannot execute an unverified source/cache entry.
    for base in (archive / "backend/src", archive / "backend/tests"):
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in {".py", ".pyc"}:
                relative = path.relative_to(archive).as_posix()
                if relative not in verified:
                    raise ValueError("Unexpected executable archive input: " + relative)
    return {"source_commit": resolved, "source_tree": tree, "verified_backend_inputs": verified}


def disable_connections():
    def forbidden(*_args, **_kwargs):
        raise RuntimeError("Historical baseline generation forbids network/database connections")

    socket.create_connection = forbidden
    socket.socket.connect = forbidden
    socket.socket.connect_ex = forbidden


class RecordingModel:
    def __init__(self, delegate):
        self.delegate = delegate
        self.requests = []

    async def complete(self, messages, *, max_output_tokens):
        self.requests.append(
            {"messages": copy.deepcopy(messages), "max_output_tokens": max_output_tokens}
        )
        return await self.delegate.complete(messages, max_output_tokens=max_output_tokens)


async def generate():
    from test_artifact_controller import (
        ArtifactFixtureModel,
        FixtureCheckTool,
        artifact_basis,
        encoded_conjecture,
        formalisation_draft,
    )
    from test_assessment_controller import (
        AssessmentFixtureModel,
        AssessmentFixtureTools,
        assessment_basis,
    )
    from test_full_text import window
    from test_meta_research_contracts import NOW, READINESS_ID, research_fixture
    from test_public_research_contracts import public_fixture
    from test_research_controller import AdaptiveFixture, Tools
    from test_research_modes import AskFixtureModel, ModeFixtureTools

    from polymath.artifact_contracts import ARTIFACT_ACTION_ADAPTER, ArtifactBasis
    from polymath.artifact_controller import ArtifactController
    from polymath.artifacts import render_artifact
    from polymath.assessment_controller import ASSESSMENT_ACTION_ADAPTER, AssessmentController
    from polymath.discovery_contracts import DISCOVERY_ACTION_ADAPTER, DiscoveryIntent
    from polymath.evidence import _canonical, _hash
    from polymath.math_checks import check_conjecture
    from polymath.meta_research_contracts import (
        TASK_KINDS,
        build_meta_research_request,
        build_research_manifest,
        canonical_research_manifest_json,
        controller_protocol_sha256,
        derive_discovery_ladder_profile,
        discovery_controller_sha256,
        minimum_controller_context_characters,
        research_manifest_sha256,
        research_step_sha256,
        task_profile_sha256,
    )
    from polymath.public_research_contracts import (
        canonical_public_research_manifest_json,
        public_research_manifest_sha256,
    )
    from polymath.research import ResearchController
    from polymath.research.discovery import discovery_initial_messages
    from polymath.research.models import ACTION_ADAPTER
    from polymath.worker import _digest

    proposal, native_policy, canary, review, attestation, private = research_fixture(now=NOW)
    intent = DiscoveryIntent(max_stages=4, max_depth=3)
    private_ladder = build_research_manifest(
        READINESS_ID,
        proposal,
        policy=native_policy,
        native_control_epoch=private.native_control_epoch,
        canary=canary,
        review=review,
        provider_account_attestation=attestation,
        valid_until=private.valid_until,
        discovery=intent,
    )
    public = public_fixture(now=NOW)
    public_ladder = public_fixture(now=NOW, discovery=intent)
    manifests = {}
    for name, manifest, canonicalizer, hasher in (
        ("private", private, canonical_research_manifest_json, research_manifest_sha256),
        (
            "private_ladder",
            private_ladder,
            canonical_research_manifest_json,
            research_manifest_sha256,
        ),
        (
            "public",
            public.manifest,
            canonical_public_research_manifest_json,
            public_research_manifest_sha256,
        ),
        (
            "public_ladder",
            public_ladder.manifest,
            canonical_public_research_manifest_json,
            public_research_manifest_sha256,
        ),
    ):
        manifests[name] = capture(manifest)
        if manifests[name]["canonical_json"] != canonicalizer(manifest):
            raise AssertionError("Manifest canonicalizer differs")
        if manifests[name]["sha256"] != hasher(manifest):
            raise AssertionError("Manifest digest differs")

    profiles = {
        item.profile.task_kind: {
            "binding": capture(item),
            "profile": capture(item.profile),
            "task_profile_sha256": task_profile_sha256(item.profile),
            "controller_protocol_sha256": controller_protocol_sha256(item.profile.task_kind),
            "minimum_context_characters": minimum_controller_context_characters(
                item.profile.task_kind
            ),
        }
        for item in private.task_profiles
    }
    if tuple(profiles) != TASK_KINDS:
        raise AssertionError("Historical task profile ordering changed")
    ladder_profile = derive_discovery_ladder_profile(proposal, intent)

    science = {}
    for mode, delegate, tools, goal in (
        ("explore", AdaptiveFixture(), Tools(), "Synthetic historical bridge baseline"),
        ("ask", AskFixtureModel(), ModeFixtureTools(), "What do synthetic sources support?"),
    ):
        model = RecordingModel(delegate)
        result = await ResearchController(model, tools).investigate(goal, mode=mode)
        if result.status != ("bridge" if mode == "explore" else "answer"):
            raise AssertionError("Historical synthetic controller did not complete: " + mode)
        science[mode] = {
            "goal": goal,
            "result": capture(result),
            "result_pydantic_json": result.model_dump_json(),
            "initial_request": capture(model.requests[0]),
            "all_model_requests": capture(model.requests),
            "evidence_store_canonical_json": _canonical(result.model_dump(mode="json")),
            "evidence_store_sha256": _hash(_canonical(result.model_dump(mode="json"))),
        }

    plain_basis = artifact_basis()
    ask_basis = artifact_basis(answer=True)
    fulltext_raw = plain_basis.model_dump(mode="json")
    excerpt = window()
    fulltext_raw["finding"]["evidence"][0].update(source_id=excerpt.source_id, quote=excerpt.text)
    passage = fulltext_raw["passages"][0]
    passage.update(
        source_id=excerpt.source_id,
        title=excerpt.title,
        url=excerpt.url,
        bibliography=excerpt.bibliography.model_dump(mode="json"),
        coverage=excerpt.coverage.model_dump(mode="json"),
        content_sha256=excerpt.content_sha256,
        content_kind=excerpt.content_kind,
        quote=excerpt.text,
    )
    fulltext_basis = ArtifactBasis.model_validate_json(json.dumps(fulltext_raw))
    conjecture = encoded_conjecture()
    incorporated = artifact_basis(
        formalisation=formalisation_draft(conjecture),
        formalisation_checks=(check_conjecture(conjecture),),
    )
    artifacts = {}
    for basis_name, basis, kinds in (
        ("bridge", plain_basis, ("formalise", "write_summary", "write_paper")),
        ("ask", ask_basis, ("write_summary", "write_paper")),
        ("fulltext", fulltext_basis, ("formalise", "write_summary", "write_paper")),
        ("with_formalisation", incorporated, ("write_summary", "write_paper")),
    ):
        for kind in kinds:
            model = RecordingModel(ArtifactFixtureModel())
            result = await ArtifactController(model, check_tool=FixtureCheckTool()).generate(
                kind, basis
            )
            if result.status != "ready":
                raise AssertionError(
                    "Historical artifact did not complete: " + basis_name + "/" + kind
                )
            markdown = render_artifact(result, basis)
            profile = next(
                item.profile for item in private.task_profiles if item.profile.task_kind == kind
            )
            request = build_meta_research_request(
                proposal,
                model.requests[0]["messages"],
                max_output_tokens=profile.max_output_tokens,
            )
            artifacts[basis_name + "/" + kind] = {
                "basis": capture(basis),
                "basis_pydantic_json": basis.model_dump_json(),
                "artifact_store_basis_sha256": _hash(_canonical(basis.model_dump(mode="json"))),
                "worker_artifact_basis_digest": _digest(basis.model_dump(mode="json")),
                "initial_request": capture(model.requests[0]),
                "all_model_requests": capture(model.requests),
                "native_initial_request": capture(request),
                "native_initial_step_sha256": research_step_sha256(
                    request,
                    activation_sha256=research_manifest_sha256(private),
                    profile_sha256=task_profile_sha256(profile),
                    request_id="model-0001",
                ),
                "result": capture(result),
                "markdown": markdown,
                "markdown_sha256": hashlib.sha256(markdown.encode()).hexdigest(),
            }

    assessments = {}
    for mode in ("explore", "ask"):
        for revised in (False, True):
            basis, sources = assessment_basis(mode=mode)
            model = RecordingModel(AssessmentFixtureModel(revised=revised))
            result = await AssessmentController(model, AssessmentFixtureTools(sources)).reassess(
                basis
            )
            if result.status != "completed":
                raise AssertionError("Historical reassessment did not complete")
            assessments[mode + ("_revised" if revised else "_unchanged")] = {
                "basis": capture(basis),
                "worker_assessment_basis_digest": _digest(basis.model_dump(mode="json")),
                "initial_request": capture(model.requests[0]),
                "all_model_requests": capture(model.requests),
                "result": capture(result),
            }

    return {
        "action_schemas": {
            "research": capture(ACTION_ADAPTER.json_schema()),
            "artifact": capture(ARTIFACT_ACTION_ADAPTER.json_schema()),
            "assessment": capture(ASSESSMENT_ACTION_ADAPTER.json_schema()),
            "discovery": capture(DISCOVERY_ACTION_ADAPTER.json_schema()),
        },
        "task_profiles": profiles,
        "ladder": {
            "intent": capture(intent),
            "profile_binding": capture(ladder_profile),
            "controller_sha256": discovery_controller_sha256(),
            "initial_messages": capture(
                discovery_initial_messages("Synthetic archived ladder goal", intent, False)
            ),
        },
        "manifests": manifests,
        "research": science,
        "artifacts": artifacts,
        "assessments": assessments,
    }


def main():
    sys.dont_write_bytecode = True
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository", type=Path, required=True, help="Local Git repository; read only"
    )
    parser.add_argument(
        "--archive", type=Path, required=True, help="Extracted exact git archive of D2 commit"
    )
    parser.add_argument("--source-commit", required=True, help="Full exact committed D2 object ID")
    parser.add_argument(
        "--output", type=Path, required=True, help="New JSON output; existing files refused"
    )
    args = parser.parse_args()
    archive = args.archive.resolve(strict=True)
    provenance = verify_archive(args.repository.resolve(strict=True), archive, args.source_commit)
    sys.path[:0] = [str(archive / "backend/src"), str(archive / "backend/tests")]
    disable_connections()
    package = importlib.import_module("polymath")
    if not Path(package.__file__).resolve().is_relative_to(archive / "backend/src"):
        raise ValueError("Application imports escaped the historical archive")
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    @event.listens_for(Engine, "before_cursor_execute")
    def forbid_database(*_args, **_kwargs):
        raise RuntimeError("Historical baseline generation forbids database statements")

    fixtures = asyncio.run(generate())
    for name, module in tuple(sys.modules.items()):
        filename = getattr(module, "__file__", None)
        if (
            name == "polymath" or name.startswith("polymath.") or name.startswith("test_")
        ) and filename:
            if not Path(filename).resolve().is_relative_to(archive):
                raise ValueError("Historical helper import escaped archive: " + name)
    payload = {
        "format": "polymath-pre-scientific-context-baseline-v1",
        "provenance": provenance,
        "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "fixture_scope": (
            "Pure synthetic controller fixtures; no external model, provider or database calls"
        ),
        "fixtures": fixtures,
        "omissions_requiring_postgresql": [
            "Actual native_run_input_sha256 for immutable persisted request/binding rows",
            "Actual complete worker checkpoint fingerprint including persisted native binding",
            "Persisted result/artifact envelope and row hashes with original run UUID bindings",
            "Applied reassessment ancestry, stage receipt, publication and ownership state",
        ],
    }
    with args.output.open("x", encoding="utf-8") as handle:
        handle.write(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            + "\n"
        )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "source_commit": args.source_commit,
                "artifact_cases": len(fixtures["artifacts"]),
                "assessment_cases": len(fixtures["assessments"]),
                "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
            }
        )
    )


if __name__ == "__main__":
    main()
