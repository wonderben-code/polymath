"""Immutable document/source/check integrity with synthetic PostgreSQL evidence."""

import hashlib
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_answer_persistence import QUESTION, answer_result
from test_artifact_api import (
    artifact_request,
    complete_artifact,
    draft_result,
)
from test_artifact_api import (
    artifacts_api as artifacts_api_fixture,
)
from test_knowledge import seed
from test_run_api import bootstrap, submit

from polymath.access_models import ResearchRunAccess
from polymath.artifact_contracts import ArtifactResult, CitedText, DocumentSection
from polymath.artifact_models import ArtifactRequest, GeneratedArtifact
from polymath.artifacts import ArtifactError, ArtifactStore
from polymath.evidence import EvidenceStore
from polymath.evidence_models import EvidenceResult, RunEvidenceSource
from polymath.jobs import JobRepository, LeaseLost
from polymath.math_checks import FiniteConjecture, check_conjecture
from polymath.research.models import Usage

artifacts_api = artifacts_api_fixture
pytestmark = pytest.mark.integration


def prepare(engine, client, *, kind="write_summary", source_id=None, **changes):
    if source_id is None:
        _, source_id = seed(engine, client)
    response = artifact_request(client, source_id, kind=kind, **changes)
    assert response.status_code == 201, response.text
    run_id = uuid.UUID(response.json()["id"])
    lease = JobRepository(engine).claim_next("synthetic-artifact-store", lease_seconds=300)
    assert lease.run_id == run_id
    return source_id, lease, ArtifactStore(engine)


def finite_conjecture():
    return FiniteConjecture.model_validate_json(
        '{"variables":[{"name":"x","lower":-1,"upper":1}],'
        '"assumptions":[],"conclusion":{"left":{"op":"variable","name":"x"},'
        '"operator":"le","right":{"op":"constant","value":0}}}'
    )


def replace_paragraph(result, paragraph):
    draft = result.draft.model_copy(
        update={
            "abstract": (paragraph,),
            "sections": (
                DocumentSection(heading="First", paragraphs=(paragraph,)),
                DocumentSection(heading="Second", paragraphs=(paragraph,)),
            ),
        }
    )
    return result.model_copy(update={"draft": draft})


def test_pinned_basis_imports_original_quotes_and_preserves_metadata(artifacts_api):
    engine, factory = artifacts_api
    client = factory(enabled=True)
    source_id, lease, store = prepare(engine, client)
    assert EvidenceStore(engine).list_sources(lease.run_id) == ()
    basis = store.load_basis(lease)
    assert basis.reference.result_id == str(source_id)
    assert len(basis.passages) == 2
    assert [passage.ordinal for passage in basis.passages] == [0, 1]
    assert all(passage.content_kind == "abstract" for passage in basis.passages)
    originals = EvidenceStore(engine).list_sources(uuid.UUID(basis.reference.run_id))
    current = EvidenceStore(engine).list_sources(lease.run_id)
    assert len(originals) == 3  # One source was read, but never quoted by the finding.
    assert len(current) == 2
    assert {source.retrieval_id for source in current} < {
        source.retrieval_id for source in originals
    }
    for source in current:
        original = EvidenceStore(engine).get_document(
            uuid.UUID(basis.reference.run_id), source.retrieval_id
        )
        assert EvidenceStore(engine).get_document(lease.run_id, source.retrieval_id) == original
    assert store.load_basis(lease) == basis


def test_save_requires_prior_durable_source_reads_and_is_idempotent(artifacts_api):
    engine, factory = artifacts_api
    _, lease, store = prepare(engine, factory(enabled=True))
    result = draft_result()
    with pytest.raises(ArtifactError, match="artifact_source_not_observed"):
        store.save(lease, result)
    store.load_basis(lease)
    saved = store.save(lease, result)
    assert store.save(lease, result).id == saved.id
    assert store.get(lease.run_id, saved.id) == saved
    assert hashlib.sha256(saved.markdown.encode()).hexdigest() == saved.markdown_sha256
    assert saved.artifact_ref == f"artifact:{saved.id}"
    with pytest.raises(ArtifactError, match="artifact_version_conflict"):
        store.save(lease, draft_result(text="A changed version requires a new job."))
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(GeneratedArtifact)) == 1


@pytest.mark.parametrize(
    "operation", ["update_request", "delete_request", "update_draft", "delete_draft"]
)
def test_request_and_generated_document_are_immutable(artifacts_api, operation):
    engine, factory = artifacts_api
    _, lease, store = prepare(engine, factory(enabled=True))
    store.load_basis(lease)
    saved = store.save(lease, draft_result())
    statement = {
        "update_request": update(ArtifactRequest)
        .where(ArtifactRequest.run_id == lease.run_id)
        .values(source_sha256="0" * 64),
        "delete_request": delete(ArtifactRequest).where(ArtifactRequest.run_id == lease.run_id),
        "update_draft": update(GeneratedArtifact)
        .where(GeneratedArtifact.id == saved.id)
        .values(payload_sha256="0" * 64),
        "delete_draft": delete(GeneratedArtifact).where(GeneratedArtifact.id == saved.id),
    }[operation]
    with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
        session.execute(statement)
    assert store.get(lease.run_id, saved.id) == saved


def test_lease_loss_blocks_input_import_and_final_document_write(artifacts_api):
    engine, factory = artifacts_api
    _, lease, store = prepare(engine, factory(enabled=True))
    JobRepository(engine).request_cancel(lease.run_id)
    with pytest.raises(LeaseLost):
        store.load_basis(lease)
    with pytest.raises(LeaseLost):
        store.save(lease, draft_result())
    assert EvidenceStore(engine).list_sources(lease.run_id) == ()


def test_unknown_quote_ordinal_cannot_become_a_generated_citation(artifacts_api):
    engine, factory = artifacts_api
    _, lease, store = prepare(engine, factory(enabled=True))
    store.load_basis(lease)
    forged = replace_paragraph(
        draft_result(),
        CitedText(text="Synthetic invented citation.", label="postulated", citation_ordinals=(63,)),
    )
    with pytest.raises(ArtifactError, match="artifact_validation_failed"):
        store.save(lease, forged)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(GeneratedArtifact)) == 0


def test_one_sided_prose_cannot_represent_a_complete_bridge_artifact(artifacts_api):
    engine, factory = artifacts_api
    _, lease, store = prepare(engine, factory(enabled=True))
    store.load_basis(lease)
    one_sided = replace_paragraph(
        draft_result(),
        CitedText(text="Only one side was included.", label="postulated", citation_ordinals=(0,)),
    )
    with pytest.raises(ArtifactError, match="artifact_validation_failed"):
        store.save(lease, one_sided)


def test_answer_claim_classification_cannot_be_upgraded_by_writing(artifacts_api):
    engine, factory = artifacts_api
    client = factory(enabled=True)
    assert submit(client, bootstrap(client), mode="ask", goal=QUESTION).status_code == 201
    jobs = JobRepository(engine)
    lease = jobs.claim_next("synthetic-answer-artifact-basis")
    answer = answer_result()
    for source in answer.sources:
        EvidenceStore(engine).save_source(lease, source)
    source = EvidenceStore(engine).save_result(lease, answer)
    jobs.finish(lease, status="succeeded", outcome={"result_id": str(source.id)})
    _, lease, store = prepare(engine, client, source_id=source.id)
    basis = store.load_basis(lease)
    speculative = {claim.id for claim in basis.finding.claims if claim.label == "speculative"}
    ordinal = next(passage.ordinal for passage in basis.passages if passage.claim_id in speculative)
    forged = replace_paragraph(
        draft_result(),
        CitedText(text="This is asserted as a fact.", label="known", citation_ordinals=(ordinal,)),
    )
    with pytest.raises(ArtifactError, match="artifact_validation_failed"):
        store.save(lease, forged)


def test_forged_finite_check_and_unchecked_final_encoding_are_rejected(artifacts_api):
    engine, factory = artifacts_api
    _, lease, store = prepare(engine, factory(enabled=True), kind="formalise")
    store.load_basis(lease)
    result = draft_result("formalise")
    conjecture = finite_conjecture()
    encoded = result.model_copy(
        update={"draft": result.draft.model_copy(update={"finite_conjecture": conjecture})}
    )
    with pytest.raises(ArtifactError, match="artifact_validation_failed"):
        store.save(lease, encoded)
    check = check_conjecture(conjecture)
    assert check.status == "counterexample_found"
    forged = check.model_copy(update={"conjecture_sha256": "0" * 64})
    with pytest.raises(ArtifactError, match="invalid_artifact_result"):
        store.save(lease, encoded.model_copy(update={"checks": (forged,)}))
    saved = store.save(lease, encoded.model_copy(update={"checks": (check,)}))
    assert saved.payload["checks"][0]["status"] == "counterexample_found"
    assert isinstance(saved.payload["checks"][0]["lhs"], str)
    assert "counterexample found" in saved.markdown
    assert "No general proof is certified" in saved.markdown


def test_prior_formalisation_preserves_its_actual_counterexample_for_writing(
    artifacts_api, monkeypatch
):
    import polymath.artifacts as artifacts

    engine, factory = artifacts_api
    client = factory(enabled=True)
    source_id, lease, store = prepare(engine, client, kind="formalise")
    store.load_basis(lease)
    conjecture = finite_conjecture()
    result = draft_result("formalise")
    result = result.model_copy(
        update={
            "draft": result.draft.model_copy(update={"finite_conjecture": conjecture}),
            "checks": (check_conjecture(conjecture),),
        }
    )
    saved = store.save(lease, result)
    JobRepository(engine).finish(lease, status="succeeded", outcome={"artifact_id": str(saved.id)})
    _, next_lease, next_store = prepare(
        engine, client, source_id=source_id, kind="write_paper", formalisation_id=str(saved.id)
    )
    basis = next_store.load_basis(next_lease)
    assert basis.formalisation_id == str(saved.id)
    assert basis.formalisation.finite_conjecture == conjecture
    assert basis.formalisation_checks[0].status == "counterexample_found"
    paper = next_store.save(next_lease, draft_result("write_paper"))
    assert paper.basis["formalisation_checks"][0]["status"] == "counterexample_found"
    assert "## Checks on the incorporated conjecture" in paper.markdown
    assert "counterexample found" in paper.markdown
    assert 'Counterexample: `{"x": 1}`.' in paper.markdown
    assert "Exact left/right values: `1` / `0`." in paper.markdown
    assert basis.formalisation_checks[0].conjecture_sha256 in paper.markdown
    assert "domain size: 3" in paper.markdown
    assert "unverified proof attempt remains unverified" in paper.markdown
    detail = client.get(f"/api/v1/artifacts/{paper.id}").json()
    assert detail["formalisation_checks"][0]["status"] == "counterexample_found"
    assert detail["formalisation_checks"][0]["witness"] == {"x": 1}
    assert detail["payload"]["checks"] == []  # Prior checks remain separately attributed.
    original_bytes, original_hash = paper.markdown.encode("utf-8"), paper.markdown_sha256
    monkeypatch.setattr(artifacts, "render_artifact", lambda *_: "Changed renderer output.\n")
    downloaded = client.get(f"/api/v1/artifacts/{paper.id}/export")
    assert downloaded.content == original_bytes
    assert hashlib.sha256(downloaded.content).hexdigest() == original_hash
    assert next_store.get(next_lease.run_id, paper.id).markdown_sha256 == original_hash


def test_nonpositive_artifact_outcome_is_saved_without_a_fabricated_document(artifacts_api):
    engine, factory = artifacts_api
    _, lease, store = prepare(engine, factory(enabled=True), kind="formalise")
    store.load_basis(lease)
    result = ArtifactResult(
        kind="formalise",
        status="insufficient_evidence",
        reason="The synthetic finding has no supported mathematical encoding.",
        usage=Usage(),
    )
    saved = store.save(lease, result)
    assert saved.payload["draft"] is None
    assert "insufficient evidence" in saved.markdown
    assert "## Conjecture" not in saved.markdown


def test_missing_child_read_cannot_be_replaced_by_a_shared_source_version(artifacts_api):
    engine, factory = artifacts_api
    _, lease, store = prepare(engine, factory(enabled=True))
    store.load_basis(lease)
    with Session(engine) as session, session.begin():
        source = session.scalar(
            select(RunEvidenceSource).where(RunEvidenceSource.run_id == lease.run_id)
        )
        session.delete(source)
    with pytest.raises(ArtifactError, match="artifact_source_not_observed"):
        store.save(lease, draft_result())


def test_existing_export_survives_a_future_renderer_change(artifacts_api, monkeypatch):
    import polymath.artifacts as artifacts

    engine, factory = artifacts_api
    client = factory(enabled=True)
    _, source_id = seed(engine, client)
    run_id, saved = complete_artifact(engine, client, source_id)
    monkeypatch.setattr(artifacts, "render_artifact", lambda *_: "A future renderer changed.\n")
    assert ArtifactStore(engine).get(run_id, saved.id).markdown == saved.markdown
    assert client.get(f"/api/v1/artifacts/{saved.id}/export").content == saved.markdown.encode()


def test_migration_downgrade_refuses_to_discard_document_history(artifacts_api):
    engine, factory = artifacts_api
    client = factory(enabled=True)
    _, source_id = seed(engine, client)
    complete_artifact(engine, client, source_id)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    with engine.connect() as connection:
        original_head = connection.scalar(text("SELECT version_num FROM alembic_version"))
    with pytest.raises(Exception, match="Cannot discard saved artifact"):
        command.downgrade(config, "0009_research_parents")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (original_head)
    command.check(config)


def test_oversized_basis_admission_rolls_back_job_ownership_and_quota(artifacts_api, monkeypatch):
    import polymath.artifacts as artifacts

    engine, factory = artifacts_api
    client = factory(enabled=True)
    _, source_id = seed(engine, client)
    # Exercise the admission policy with a small configured byte ceiling; the
    # exact same canonical-byte check protects the production 262 KiB ceiling.
    monkeypatch.setattr(artifacts, "MAX_ARTIFACT_BYTES", 128)
    response = artifact_request(client, source_id)
    assert response.status_code == 404
    assert response.json() == {"detail": "artifact_source_not_found"}
    with Session(engine) as session:
        from polymath.models import ResearchRun

        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 1
        assert session.scalar(select(func.count()).select_from(ResearchRunAccess)) == 1
        assert session.scalar(select(func.count()).select_from(ArtifactRequest)) == 0


def test_forged_pinned_hash_is_rejected_before_source_copy(artifacts_api):
    engine, factory = artifacts_api
    client = factory(enabled=True)
    _, source_id = seed(engine, client)
    jobs = JobRepository(engine)
    run = jobs.create(mode="explore", goal="Synthetic corrupted request", task_kind="write_summary")
    with Session(engine) as session, session.begin():
        source = session.get(EvidenceResult, source_id)
        owner_hash = session.scalar(
            select(ResearchRunAccess.session_hash).where(ResearchRunAccess.run_id == source.run_id)
        )
        session.add(ResearchRunAccess(run_id=run.id, session_hash=owner_hash))
        session.add(
            ArtifactRequest(
                run_id=run.id,
                kind="write_summary",
                source_result_id=source.id,
                source_run_id=source.run_id,
                source_sha256="0" * 64,
            )
        )
    lease = jobs.claim_next("synthetic-corrupt-artifact-request")
    assert lease.run_id == run.id
    with pytest.raises(ArtifactError, match="artifact_input_integrity_failed"):
        ArtifactStore(engine).load_basis(lease)
    assert EvidenceStore(engine).list_sources(run.id) == ()
