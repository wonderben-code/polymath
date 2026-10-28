"""Stable persisted input fixtures, not native approval or scientific verification.

Both the verified D2 subprocess and current tests execute this exact helper.
Expected hashes are obtained ONLY by calling that version's real DB input reader.
No fingerprint algorithm, model factory or execution authority is implemented here.
"""

import uuid
from datetime import UTC, datetime, timedelta


def identifier(value):
    return uuid.UUID(int=value)


def seed_inputs(engine):
    from sqlalchemy.orm import Session
    from test_assessment_controller import assessment_basis

    from polymath.access_models import BrowserSession, ResearchRunAccess
    from polymath.artifact_models import ArtifactRequest, GeneratedArtifact
    from polymath.assessment_models import AssessmentInputSource, AssessmentRequest
    from polymath.discovery_admission import bind_discovery_intent
    from polymath.discovery_contracts import DiscoveryIntent
    from polymath.evidence import _canonical, _hash, _metadata
    from polymath.evidence_models import (
        EvidenceQuote,
        EvidenceResult,
        RunEvidenceSource,
        SourceRetrieval,
        SourceVersion,
    )
    from polymath.ingestion_models import LiteratureWatch
    from polymath.lineage_models import ResearchRunParent
    from polymath.models import ResearchRun
    from polymath.research.models import MemoryReadReference, ResearchResult, Usage

    timestamp = datetime(2026, 10, 27, tzinfo=UTC)
    owner_hash = "b" * 64
    original, snapshots = assessment_basis()
    historical = [r for r in original.sources if r.role == "historical"]
    result = ResearchResult(
        status="bridge",
        mode="explore",
        finding=original.prior_finding,
        quotes=original.prior_quotes,
        usage=Usage(),
    )
    source_refs = [{"version_id": r.version_id, "retrieval_id": r.retrieval_id} for r in historical]
    envelope = {"result": result.model_dump(mode="json"), "source_artifacts": source_refs}
    source_sha = _hash(_canonical(envelope))
    parent_run_ids, parent_result_ids = (
        (identifier(1001), identifier(1002)),
        (identifier(2001), identifier(2002)),
    )
    cases = {
        "research_explore": (101, "explore", "research", "Stable synthetic input: λ and 😀."),
        "research_ask": (102, "ask", "research", "What do synthetic sources support?"),
        "research_surprise": (103, "surprise", "research", ""),
        "research_surprise_ladder": (104, "surprise", "research", ""),
        "research_f3_parents": (
            105,
            "explore",
            "research",
            "Compare these exact saved hypotheses.",
        ),
        "formalise": (106, "explore", "formalise", "Synthetic formalisation input"),
        "write_summary": (107, "explore", "write_summary", "Synthetic summary input"),
        "write_paper": (108, "explore", "write_paper", "Synthetic paper input"),
        "reassess_finding": (109, "explore", "reassess_finding", "Synthetic reassessment input"),
    }
    with Session(engine) as session, session.begin():
        session.add(
            BrowserSession(token_hash=owner_hash, expires_at=datetime(2099, 1, 1, tzinfo=UTC))
        )
        for ordinal, (run_id, _result_id) in enumerate(
            zip(parent_run_ids, parent_result_ids, strict=True)
        ):
            session.add(
                ResearchRun(
                    id=run_id,
                    mode="explore",
                    task_kind="research",
                    status="succeeded",
                    goal="Synthetic saved premise",
                    created_at=timestamp + timedelta(seconds=ordinal),
                )
            )
        formal_run = ResearchRun(
            id=identifier(1003),
            mode="explore",
            task_kind="formalise",
            status="succeeded",
            goal="Synthetic stored formalisation metadata",
            created_at=timestamp + timedelta(seconds=2),
        )
        session.add(formal_run)
        for value, mode, kind, goal in cases.values():
            session.add(
                ResearchRun(
                    id=identifier(value),
                    mode=mode,
                    task_kind=kind,
                    goal=goal,
                    created_at=timestamp + timedelta(minutes=1),
                )
            )
        session.flush()
        for run_id in (*parent_run_ids, formal_run.id, *(identifier(v[0]) for v in cases.values())):
            session.add(ResearchRunAccess(run_id=run_id, session_hash=owner_hash))
        for snapshot in snapshots.values():
            doc = snapshot.document
            session.add(
                SourceVersion(
                    id=uuid.UUID(snapshot.version_id),
                    source_id=doc.source_id,
                    content_sha256=doc.content_sha256,
                    content_kind=doc.content_kind,
                    source_text=doc.text,
                    created_at=timestamp,
                )
            )
        session.flush()
        for snapshot in snapshots.values():
            doc = snapshot.document
            session.add(
                SourceRetrieval(
                    id=uuid.UUID(snapshot.retrieval_id),
                    version_id=uuid.UUID(snapshot.version_id),
                    metadata_sha256=_hash(_canonical(_metadata(doc))),
                    title=doc.title,
                    url=doc.url,
                    retrieved_at=doc.retrieved_at,
                    extraction_method=doc.extraction_method,
                    bibliography=doc.bibliography.model_dump(mode="json")
                    if doc.bibliography
                    else None,
                )
            )
        session.flush()
        for run_id, result_id in zip(parent_run_ids, parent_result_ids, strict=True):
            for ref in historical:
                session.add(
                    RunEvidenceSource(run_id=run_id, retrieval_id=uuid.UUID(ref.retrieval_id))
                )
            session.add(
                EvidenceResult(
                    id=result_id,
                    run_id=run_id,
                    content_sha256=source_sha,
                    status="bridge",
                    payload=envelope,
                    created_at=timestamp + timedelta(seconds=3),
                )
            )
        session.flush()
        by_source = {ref.source_id: ref for ref in historical}
        for run_id, result_id in zip(parent_run_ids, parent_result_ids, strict=True):
            for ordinal, quote in enumerate(original.prior_quotes):
                ref = by_source[quote.source_id]
                session.add(
                    EvidenceQuote(
                        result_id=result_id,
                        ordinal=ordinal,
                        run_id=run_id,
                        version_id=uuid.UUID(ref.version_id),
                        retrieval_id=uuid.UUID(ref.retrieval_id),
                        side=quote.side,
                        quote=quote.quote,
                        supports=quote.supports,
                        start_offset=quote.start,
                        end_offset=quote.end,
                        claim_id=quote.claim_id,
                    )
                )
        # This object is a stored metadata pointer fixture, not a generated
        # conjecture or claim of a completed controller/native journey.
        payload, basis, markdown = (
            {"fixture": "native-input-pointer-only"},
            {"fixture": "legacy-basis-pointer"},
            "Synthetic fixture; no scientific proof.\n",
        )
        formal_sha = _hash(_canonical(payload))
        formal_id = identifier(3001)
        session.add(
            GeneratedArtifact(
                id=formal_id,
                run_id=formal_run.id,
                kind="formalise",
                status="ready",
                payload=payload,
                payload_sha256=formal_sha,
                basis=basis,
                basis_sha256=_hash(_canonical(basis)),
                markdown=markdown,
                markdown_sha256=_hash(markdown),
                created_at=timestamp + timedelta(seconds=4),
            )
        )
        session.flush()
        for name in ("formalise", "write_summary", "write_paper"):
            session.add(
                ArtifactRequest(
                    run_id=identifier(cases[name][0]),
                    kind=name,
                    source_result_id=parent_result_ids[0],
                    source_run_id=parent_run_ids[0],
                    source_sha256=source_sha,
                    formalisation_id=formal_id if name == "write_paper" else None,
                    formalisation_sha256=formal_sha if name == "write_paper" else None,
                )
            )
        for ordinal, (run_id, result_id) in enumerate(
            zip(reversed(parent_run_ids), reversed(parent_result_ids), strict=True)
        ):
            session.add(
                ResearchRunParent(
                    run_id=identifier(cases["research_f3_parents"][0]),
                    parent_result_id=result_id,
                    parent_run_id=run_id,
                    ordinal=ordinal,
                    content_sha256=source_sha,
                )
            )
        ladder = session.get(ResearchRun, identifier(cases["research_surprise_ladder"][0]))
        bind_discovery_intent(session, ladder, DiscoveryIntent())
        watch_id = identifier(4001)
        session.add(
            LiteratureWatch(
                id=watch_id,
                owner_session_hash=owner_hash,
                label="Fixed metadata watch",
                query="Synthetic read-only input fixture",
                interval_seconds=21600,
                next_due_at=timestamp,
                expires_at=datetime(2099, 1, 1, tzinfo=UTC),
                provider_cursors={},
                source_result_id=parent_result_ids[0],
                source_sha256=source_sha,
            )
        )
        session.flush()
        pinned_basis = original.model_copy(
            update={
                "prior_reference": MemoryReadReference(
                    result_id=str(parent_result_ids[0]),
                    run_id=str(parent_run_ids[0]),
                    content_sha256=source_sha,
                )
            }
        ).model_dump(mode="json")
        assessment_run = identifier(cases["reassess_finding"][0])
        session.add(
            AssessmentRequest(
                run_id=assessment_run,
                root_result_id=parent_result_ids[0],
                source_result_id=parent_result_ids[0],
                source_sha256=source_sha,
                watch_id=watch_id,
                expected_head_revision=0,
                basis=pinned_basis,
                basis_sha256=_hash(_canonical(pinned_basis)),
            )
        )
        session.flush()
        for ordinal, ref in enumerate(original.sources):
            session.add(
                AssessmentInputSource(
                    run_id=assessment_run,
                    ordinal=ordinal,
                    role=ref.role,
                    retrieval_id=uuid.UUID(ref.retrieval_id),
                    version_id=uuid.UUID(ref.version_id),
                )
            )
    return {name: str(identifier(value[0])) for name, value in cases.items()}


def capture_inputs(engine, cases):
    from sqlalchemy.orm import Session

    from polymath.meta_research import native_run_input_sha256
    from polymath.models import ResearchRun

    with Session(engine) as session:
        return {
            name: native_run_input_sha256(session, session.get(ResearchRun, uuid.UUID(identifier)))
            for name, identifier in cases.items()
        }
