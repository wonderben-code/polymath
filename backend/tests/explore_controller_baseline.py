"""Capture old controller behavior from a verified pushed SC archive, without DB/HTTP."""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import re
import socket
import subprocess
import sys
from pathlib import Path


async def capture_controller_evidence():
    from test_discovery_controller import (
        DiscoveryFixtureModel,
        DiscoveryFixtureSink,
        DiscoveryFixtureTools,
    )
    from test_research_controller import AdaptiveFixture, Tools
    from test_research_modes import AskFixtureModel, ModeFixtureTools, SurpriseFixtureModel

    from polymath.artifact_contracts import ARTIFACT_ACTION_ADAPTER
    from polymath.assessment_controller import ASSESSMENT_ACTION_ADAPTER
    from polymath.assessment_overview import ASK_REASSESSMENT_OVERVIEW_ACTION_ADAPTER
    from polymath.discovery_contracts import DISCOVERY_ACTION_ADAPTER, DiscoveryIntent
    from polymath.meta_research_contracts import (
        SCIENTIFIC_PROFILE_ROWS,
        TASK_KINDS,
        controller_protocol_sha256,
        discovery_controller_sha256,
        scientific_controller_sha256,
    )
    from polymath.research.ask_overview import (
        ASK_OVERVIEW_ACTION_ADAPTER,
        ASK_OVERVIEW_SYSTEM_PROMPT,
    )
    from polymath.research.controller import MODE_INSTRUCTIONS, SYSTEM_PROMPT, ResearchController
    from polymath.research.discovery import DISCOVERY_SYSTEM_PROMPT, DiscoveryLimits
    from polymath.research.meta import ModelReply
    from polymath.research.models import ACTION_ADAPTER
    from polymath.scientific_context_contracts import AskOverviewIntent

    class Recording:
        def __init__(self, delegate, *, bound=False):
            self.delegate, self.bound = delegate, bound
            self.messages = []

        async def complete(self, messages, *, max_output_tokens):
            self.messages.append(copy.deepcopy(messages))
            reply = await self.delegate.complete(messages, max_output_tokens=max_output_tokens)
            if not self.bound:
                return reply
            action = json.loads(reply.content)
            finding = action.get("conclusion")
            if finding is not None and finding.get("outcome") == "answer":
                finding.pop("summary")
                finding["overview_claim_ids"] = [claim["id"] for claim in finding["claims"]][::-1]
            return ModelReply(json.dumps(action), reply.input_tokens, reply.output_tokens)

    value = {
        "profiles": {kind: controller_protocol_sha256(kind) for kind in TASK_KINDS},
        "discovery": discovery_controller_sha256(),
        "scientific_profiles": {
            f"{capability}:{kind}": scientific_controller_sha256(capability, kind)
            for capability, kind, _, _ in SCIENTIFIC_PROFILE_ROWS
        },
        "schemas": {
            "research": ACTION_ADAPTER.json_schema(),
            "discovery": DISCOVERY_ACTION_ADAPTER.json_schema(),
            "ask_overview": ASK_OVERVIEW_ACTION_ADAPTER.json_schema(),
            "artifacts": ARTIFACT_ACTION_ADAPTER.json_schema(),
            "assessment": ASSESSMENT_ACTION_ADAPTER.json_schema(),
            "assessment_overview": ASK_REASSESSMENT_OVERVIEW_ACTION_ADAPTER.json_schema(),
        },
        "system_prompt": SYSTEM_PROMPT,
        "mode_instructions": MODE_INSTRUCTIONS,
        "discovery_prompt": DISCOVERY_SYSTEM_PROMPT,
        "ask_overview_prompt": ASK_OVERVIEW_SYSTEM_PROMPT,
        "legacy_research": {},
    }
    goal = "How does synthetic redundancy protect structure?"
    for mode, delegate, tools in [
        ("explore", AdaptiveFixture(), Tools()),
        ("ask", AskFixtureModel(), ModeFixtureTools()),
        ("surprise", SurpriseFixtureModel(), ModeFixtureTools()),
    ]:
        model = Recording(delegate)
        result = await ResearchController(model, tools).investigate(goal, mode=mode)
        value["legacy_research"][mode] = {
            "result": result.model_dump(mode="json"),
            "messages": model.messages,
        }
    model = Recording(AskFixtureModel(), bound=True)
    result = await ResearchController(model, ModeFixtureTools()).investigate(
        goal, mode="ask", ask_overview_intent=AskOverviewIntent()
    )
    value["bound_ask"] = {"result": result.model_dump(mode="json"), "messages": model.messages}
    tools = DiscoveryFixtureTools()
    sink = DiscoveryFixtureSink(tools)
    model = Recording(DiscoveryFixtureModel(stop_after_stage=1))
    result = await ResearchController(
        model, tools, DiscoveryLimits(), discovery_sink=sink
    ).investigate("", mode="surprise", discovery=DiscoveryIntent())
    value["legacy_discovery"] = {
        "result": result.model_dump(mode="json"),
        "messages": model.messages,
    }
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--git-repository", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[0-9a-f]{40}", args.source_commit) is None:
        raise SystemExit("A full exact commit SHA is required")
    archive = args.archive_root.resolve()
    tree = subprocess.run(
        [
            "git",
            "-C",
            str(args.git_repository),
            "ls-tree",
            "-r",
            "-z",
            "--full-tree",
            args.source_commit,
            "--",
            "backend/src/polymath",
            "backend/tests",
        ],
        check=True,
        capture_output=True,
    ).stdout
    count = 0
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        mode, kind, digest = metadata.split(b" ")
        path = archive / raw_path.decode("utf-8")
        if (
            mode not in (b"100644", b"100755")
            or kind != b"blob"
            or not path.resolve().is_relative_to(archive)
        ):
            raise SystemExit("Archive contains an unsupported entry")
        body = path.read_bytes()
        actual = (
            hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body, usedforsecurity=False)
            .hexdigest()
            .encode()
        )
        if actual != digest:
            raise SystemExit("Archive does not match exact source commit")
        count += 1
    if count < 100:
        raise SystemExit("Archive source/test tree is incomplete")
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(archive / "backend/src"), str(archive / "backend/tests")]

    def blocked(*args, **kwargs):
        raise AssertionError("Compatibility capture forbids external access")

    socket.socket.connect = blocked
    socket.create_connection = blocked
    socket.getaddrinfo = blocked
    from sqlalchemy import Engine

    Engine.connect = blocked
    import polymath

    if not Path(polymath.__file__).resolve().is_relative_to(archive / "backend/src"):
        raise SystemExit("Wrong package import root")
    value = asyncio.run(capture_controller_evidence())
    value["source_commit"] = args.source_commit
    value["archive_verified_files"] = count
    with args.output.open("x") as stream:
        json.dump(value, stream, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        stream.write("\n")
    print(f"Captured controller baseline from {args.source_commit}; {count} archive files verified")


if __name__ == "__main__":
    main()
