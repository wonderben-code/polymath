#!/usr/bin/env python3
"""Capture SCI contracts from an exact committed archive before Explore changes.

Only standard-library imports occur until the archive has been verified and
placed first on sys.path. Generation uses its own pure helpers and test inputs.
No model, provider, SQL statement or socket connection is permitted.
"""

from __future__ import annotations

import argparse
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
        "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    }


def verify_archive(repository, archive, source_commit):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repository), *args])

    resolved = git("rev-parse", "--verify", source_commit + "^{commit}").decode().strip()
    if source_commit != resolved:
        raise ValueError("Full exact source commit is required")
    algorithm = git("rev-parse", "--show-object-format").decode().strip()
    if algorithm not in {"sha1", "sha256"}:
        raise ValueError("Unsupported Git object format")
    verified = {}
    for entry in git("ls-tree", "-rz", "--full-tree", source_commit, "--", "backend").split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        mode, kind, expected = metadata.decode().split()
        relative = raw_path.decode("utf-8")
        path = archive / relative
        if (
            kind != "blob"
            or mode not in {"100644", "100755"}
            or not path.is_file()
            or path.is_symlink()
        ):
            raise ValueError("Missing or invalid archived backend file: " + relative)
        data = path.read_bytes()
        blob = hashlib.new(algorithm, b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if blob != expected:
            raise ValueError("Archive differs from exact source commit: " + relative)
        verified[relative] = {"git_blob": expected, "sha256": hashlib.sha256(data).hexdigest()}
    if not verified:
        raise ValueError("No archived backend files were verified")
    for base in (archive / "backend/src", archive / "backend/tests"):
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in {".py", ".pyc"}:
                if path.relative_to(archive).as_posix() not in verified:
                    raise ValueError("Unexpected executable archive input")
    return {
        "source_commit": resolved,
        "source_tree": git("rev-parse", source_commit + "^{tree}").decode().strip(),
        "verified_backend_inputs": verified,
    }


def disable_connections():
    def forbidden(*_args, **_kwargs):
        raise RuntimeError("Scientific baseline forbids model/provider/database connections")

    socket.create_connection = forbidden
    socket.socket.connect = forbidden
    socket.socket.connect_ex = forbidden


def generate_scientific_values():
    """Compare these current pure values with the independently archived fixture."""
    from test_artifact_context_controller import context_basis
    from test_artifact_controller import document_draft, formalisation_draft
    from test_artifact_scientific_context import as_json_data, context_data, uid
    from test_ask_overview_controller import QUESTION
    from test_ask_reassessment_overview import pinned_basis
    from test_meta_research_contracts import NOW, research_fixture
    from test_public_research_contracts import public_fixture

    from polymath.artifact_contracts import ArtifactBasis, ArtifactResult, validate_artifact_result
    from polymath.artifact_controller import (
        artifact_initial_messages,
        artifact_scientific_context_protocol_payload,
    )
    from polymath.artifacts import render_artifact
    from polymath.assessment_contracts import AssessmentBasis
    from polymath.assessment_overview import (
        ASK_REASSESSMENT_OVERVIEW_ACTION_ADAPTER,
        ask_reassessment_overview_initial_messages,
        ask_reassessment_overview_protocol_payload,
    )
    from polymath.discovery_contracts import DiscoveryIntent
    from polymath.meta_research_contracts import (
        SCIENTIFIC_PROFILE_ROWS,
        ScientificContextIntent,
        build_meta_research_request,
        build_research_manifest,
        canonical_research_manifest_json,
        derive_scientific_context_profiles,
        minimum_scientific_context_characters,
        research_manifest_sha256,
        research_step_sha256,
        scientific_controller_sha256,
        task_profile_sha256,
    )
    from polymath.public_research_contracts import (
        build_public_research_manifest,
        canonical_public_research_manifest_json,
        public_research_manifest_sha256,
    )
    from polymath.research.ask_overview import (
        ASK_OVERVIEW_ACTION_ADAPTER,
        ask_overview_initial_messages,
        ask_overview_protocol_payload,
    )
    from polymath.research.models import Usage
    from polymath.scientific_context_contracts import (
        ArtifactScientificContext,
        PriorArtifactContextReference,
    )

    proposal, native, canary, review, attestation, old = research_fixture(now=NOW)
    extension = derive_scientific_context_profiles(proposal)
    manifests = {}
    private = None
    for ladder in (False, True):
        intent = DiscoveryIntent(max_stages=3, max_depth=2) if ladder else None
        value = build_research_manifest(
            old.readiness_id,
            proposal,
            policy=native,
            native_control_epoch=old.native_control_epoch,
            canary=canary,
            review=review,
            provider_account_attestation=attestation,
            valid_until=old.valid_until,
            discovery=intent,
            scientific_context=ScientificContextIntent(),
        )
        if not ladder:
            private = value
        key = "private_scientific" + ("_ladder" if ladder else "")
        manifests[key] = capture(value)
        assert manifests[key]["canonical_json"] == canonical_research_manifest_json(value)
        assert manifests[key]["sha256"] == research_manifest_sha256(value)
        public = public_fixture(now=NOW)
        value = build_public_research_manifest(
            public.manifest.readiness_id,
            proposal,
            research_policy=public.research_policy,
            public_policy=public.public_policy,
            sponsor_policy_sha256=public.sponsor_policy_sha256,
            native_control_epoch=public.manifest.native_control_epoch,
            canary=public.canary,
            review=public.review,
            provider_account_attestation=public.attestation,
            public_limits=public.public_limits,
            valid_until=public.manifest.valid_until,
            now=NOW,
            discovery=intent,
            scientific_context=ScientificContextIntent(),
        )
        key = "public_scientific" + ("_ladder" if ladder else "")
        manifests[key] = capture(value)
        assert manifests[key]["canonical_json"] == canonical_public_research_manifest_json(value)
        assert manifests[key]["sha256"] == public_research_manifest_sha256(value)

    payloads, profile_values = {}, {}
    by_kind = {}
    for binding, (capability, kind, mode, _protocol) in zip(
        extension.bindings, SCIENTIFIC_PROFILE_ROWS, strict=True
    ):
        if capability == "artifact_scientific_context_v1":
            payload = artifact_scientific_context_protocol_payload(kind)
        elif capability == "ask_claim_overview_v1":
            payload = ask_overview_protocol_payload()
        else:
            payload = ask_reassessment_overview_protocol_payload()
        key = capability + "/" + kind
        payloads[key] = capture(payload)
        profile_values[key] = {
            "binding": capture(binding),
            "controller_sha256": scientific_controller_sha256(capability, kind),
            "profile_sha256": task_profile_sha256(binding.profile),
            "fixed_context_characters": minimum_scientific_context_characters(capability, kind),
            "mode": mode,
        }
        assert binding.profile.controller_sha256 == payloads[key]["sha256"]
        by_kind[kind] = binding.profile

    def native_initial(messages, kind):
        profile = by_kind[kind]
        request = build_meta_research_request(
            proposal, messages, max_output_tokens=profile.max_output_tokens
        )
        return {
            "messages": capture(messages),
            "request": capture(request),
            "step_sha256": research_step_sha256(
                request,
                activation_sha256=research_manifest_sha256(private),
                profile_sha256=task_profile_sha256(profile),
                request_id="model-0001",
            ),
        }

    artifacts = {}
    bases = {
        "original": context_basis(),
        "parents": context_basis(parents=True),
        "revised": context_basis(revised=True),
        "revised_parents": context_basis(revised=True, parents=True),
    }
    context = ArtifactScientificContext.model_validate_json(
        json.dumps(as_json_data(context_data(revised=True, parents=True)))
    )
    prior = PriorArtifactContextReference(
        artifact_id=uid(900),
        source_reference=context.source_reference,
        payload_sha256="a" * 64,
        basis_sha256="b" * 64,
        context_sha256="e" * 64,
    )
    context = ArtifactScientificContext.model_validate_json(
        context.model_copy(update={"prior_formalisation": prior}).model_dump_json()
    )
    bases["prior_formalisation"] = context_basis(
        context=context, formalisation=formalisation_draft()
    )
    for case, basis in bases.items():
        for kind in ("formalise", "write_summary", "write_paper"):
            if case == "prior_formalisation" and kind == "formalise":
                continue
            draft = (
                formalisation_draft()
                if kind == "formalise"
                else document_draft("summary" if kind == "write_summary" else "paper")
            )
            result = ArtifactResult(kind=kind, status="ready", draft=draft, usage=Usage())
            validate_artifact_result(result, basis)
            markdown = render_artifact(result, basis)
            artifacts[case + "/" + kind] = {
                "basis": capture(basis),
                "basis_pydantic_json": basis.model_dump_json(),
                "initial": native_initial(artifact_initial_messages(kind, basis), kind),
                "draft_result": capture(result),
                "markdown": markdown,
                "markdown_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
            }

    asks = {
        "memory_" + str(memory).lower(): native_initial(
            ask_overview_initial_messages(QUESTION, memory_available=memory), "research"
        )
        for memory in (False, True)
    }
    assessments = {}
    for bound in (False, True):
        for state in ("supported", "unsupported"):
            basis, _ = pinned_basis(prior_state=state, bound=bound)
            assessments[("bound" if bound else "legacy") + "/" + state] = {
                "basis": capture(basis),
                "basis_pydantic_json": basis.model_dump_json(),
                "initial": native_initial(
                    ask_reassessment_overview_initial_messages(basis), "reassess_finding"
                ),
            }
    return {
        "schemas": {
            "bound_ask_action": capture(ASK_OVERVIEW_ACTION_ADAPTER.json_schema()),
            "bound_assessment_action": capture(
                ASK_REASSESSMENT_OVERVIEW_ACTION_ADAPTER.json_schema()
            ),
            "artifact_basis": capture(ArtifactBasis.model_json_schema()),
            "assessment_basis": capture(AssessmentBasis.model_json_schema()),
        },
        "profiles": profile_values,
        "protocol_payloads": payloads,
        "manifests": manifests,
        "artifact_contexts": artifacts,
        "ask_initial": asks,
        "assessment_initial": assessments,
    }


def main():
    sys.dont_write_bytecode = True
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    archive = args.archive.resolve(strict=True)
    provenance = verify_archive(args.repository.resolve(strict=True), archive, args.source_commit)
    sys.path[:0] = [str(archive / "backend/src"), str(archive / "backend/tests")]
    disable_connections()
    package = importlib.import_module("polymath")
    if not Path(package.__file__).resolve().is_relative_to(archive / "backend/src"):
        raise ValueError("Application imports escaped the source archive")
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    @event.listens_for(Engine, "before_cursor_execute")
    def forbid_statements(*_args, **_kwargs):
        raise RuntimeError("Scientific baseline forbids database statements")

    fixtures = generate_scientific_values()
    for name, module in tuple(sys.modules.items()):
        filename = getattr(module, "__file__", None)
        if (
            filename
            and (name == "polymath" or name.startswith("polymath.") or name.startswith("test_"))
            and not Path(filename).resolve().is_relative_to(archive)
        ):
            raise ValueError("Scientific helper import escaped the source archive: " + name)
    payload = {
        "format": "polymath-pre-explore-scientific-baseline-v1",
        "provenance": provenance,
        "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "fixture_scope": (
            "Pure SCI profiles, messages, drafts and rendering; no actual model/HTTP/DB"
        ),
        "fixtures": fixtures,
        "omissions_requiring_postgresql": [
            "Actual persisted native input and complete worker checkpoint fingerprints",
            "Actual owned stage/assessment/artifact bindings and current-head transitions",
            "Original request journal, unknown holds and keyless late settlement",
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
                "source_commit": args.source_commit,
                "output": str(args.output),
                "cases": sum(len(section) for section in fixtures.values()),
                "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
            }
        )
    )


if __name__ == "__main__":
    main()
