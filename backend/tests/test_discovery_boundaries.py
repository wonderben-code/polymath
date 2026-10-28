"""Independent stage provenance and late-bibliography adversarial checks."""

import json
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_discovery_controller import DiscoveryFixtureModel, DiscoveryFixtureTools
from test_discovery_worker import queued, worker
from test_memory import assign
from test_research_controller import source
from test_worker import database as database_fixture

from polymath.artifacts import ArtifactError, source_for_request
from polymath.assessment_eligibility import (
    EligibilityError,
    completed_result_condition,
    require_current_supported,
)
from polymath.budget import BudgetService
from polymath.discovery_models import StageCommit
from polymath.discovery_store import DiscoveryStore, StageValidationError, stage_current_supported
from polymath.evidence import EvidenceError, EvidenceStore, _canonical, _hash
from polymath.evidence_models import EvidenceResult
from polymath.jobs import JobRepository
from polymath.lineage import LineageError, bind_parents, get_parent_refs
from polymath.memory import ResearchMemory
from polymath.models import ResearchRun
from polymath.research.discovery import DiscoveryState
from polymath.research.meta import ModelReply
from polymath.research.models import (
    ArxivFullTextLocator,
    FullTextAvailability,
    ResearchResult,
    SearchResults,
    SourceBibliography,
    SourceReference,
)
from polymath.sharing import SharingError, SharingPolicy, SharingStore

database = database_fixture
pytestmark = pytest.mark.integration


class BibliographicTools(DiscoveryFixtureTools):
    def __init__(self):
        super().__init__()
        self.documents["left"] = self.documents["left"].model_copy(
            update={"bibliography": SourceBibliography(doi="10.1234/synthetic-discovery")}
        )
        self.documents["right"] = self.documents["right"].model_copy(
            update={"bibliography": SourceBibliography(arxiv_id="2401.00001")}
        )

    async def search(self, query, purpose):
        self.searches.append((query, purpose))
        return (
            [
                SourceReference(
                    source_id=doc.source_id,
                    title=doc.title,
                    url=doc.url,
                    bibliography=doc.bibliography,
                )
                for doc in self.documents.values()
            ]
            if purpose == "discovery"
            else []
        )


async def partial(engine, *, bibliography=False):
    token, run_id = queued(engine)
    instance = worker(engine, DiscoveryFixtureModel(), calls=7)
    if bibliography:
        instance.tools_factory = lambda _: BibliographicTools()
    assert (await instance.run_once("adversarial-stage-prefix")).status == "interrupted"
    with Session(engine) as session:
        stage = session.scalar(select(StageCommit).where(StageCommit.run_id == run_id))
        stage_id, result_id = stage.id, stage.result_id
    snapshot = JobRepository(engine).get(run_id)
    JobRepository(engine).resume_interrupted(run_id, expected_token=snapshot.lease_token)
    lease = JobRepository(engine).claim_next("adversarial-stage-continuation", lease_seconds=300)
    assert lease.run_id == run_id
    return token, run_id, stage_id, result_id, lease


async def test_generic_positive_write_cannot_bypass_atomic_stage_commit(database):
    _, run_id, _, result_id, lease = await partial(database)
    existing = EvidenceStore(database).get_result(run_id, result_id)
    result = ResearchResult.model_validate_json(json.dumps(existing.payload))
    changed = result.model_copy(
        update={"finding": result.finding.model_copy(update={"title": "Uncommitted positive"})}
    )
    with pytest.raises(EvidenceError, match="discovery_stage_commit_required"):
        EvidenceStore(database).save_result(lease, changed)
    with Session(database) as session:
        assert session.scalars(select(EvidenceResult.id)).all() == [result_id]


async def test_orphan_positive_stays_ineligible_even_when_containing_run_later_succeeds(database):
    token, run_id, _, result_id, lease = await partial(database)
    with Session(database) as session, session.begin():
        original = session.get(EvidenceResult, result_id)
        payload = {
            **original.payload,
            "result": {
                **original.payload["result"],
                "finding": {**original.payload["result"]["finding"], "title": "Orphan"},
            },
        }
        orphan_id = uuid.uuid4()
        session.add(
            EvidenceResult(
                id=orphan_id,
                run_id=run_id,
                content_sha256=_hash(_canonical(payload)),
                status="bridge",
                payload=payload,
            )
        )
    JobRepository(database).finish(lease, status="succeeded", outcome={"result_id": str(orphan_id)})
    with Session(database) as session:
        eligible = session.scalars(
            select(EvidenceResult.id).where(completed_result_condition())
        ).all()
        assert result_id in eligible and orphan_id not in eligible
        with pytest.raises(EligibilityError):
            require_current_supported(session, token, orphan_id)
        with pytest.raises(ArtifactError, match="artifact_source_not_found"):
            source_for_request(session, token, str(orphan_id), "write_summary")


async def test_committed_partial_stage_can_be_developed_before_whole_run_success(database):
    token, _, _, result_id, _ = await partial(database)
    with Session(database) as session:
        require_current_supported(session, token, result_id)
        assert source_for_request(session, token, str(result_id), "write_summary").id == result_id


async def test_later_uncited_alias_preserves_history_but_requires_structural_review(database):
    token, run_id, stage_id, result_id, lease = await partial(database, bibliography=True)
    original = DiscoveryStore(database).get_stage(token, run_id, stage_id)
    assert original["status"] == "current_supported"
    assert len(original["primary_paper_groups"]) == 2
    connector = source("uncited-connector", "This later metadata connects two paper identifiers.")
    connector = connector.model_copy(
        update={
            "bibliography": SourceBibliography(
                doi="10.1234/synthetic-discovery", arxiv_id="2401.00001"
            )
        }
    )
    EvidenceStore(database).save_source(lease, connector)
    revised = DiscoveryStore(database).get_stage(token, run_id, stage_id)
    assert revised["stage"] == original["stage"]
    assert revised["result"] == original["result"]
    assert revised["status"] == "needs_review"
    assert len(revised["primary_paper_groups"]) == 1
    with Session(database) as session:
        assert not stage_current_supported(session, session.get(EvidenceResult, result_id))
        with pytest.raises(EligibilityError):
            require_current_supported(session, token, result_id)
        with pytest.raises(ArtifactError, match="artifact_source_not_found"):
            source_for_request(session, token, str(result_id), "write_summary")


async def test_already_approved_public_stage_keeps_exact_bytes_with_review_warning(database):
    token, _, _, result_id, lease = await partial(database, bibliography=True)
    sharing = SharingStore(database, SharingPolicy(enabled=True))
    preview = sharing.preview(token, result_id)
    grant = sharing.share(token, result_id, preview.projection_sha256, uuid.uuid4())
    original = sharing.get_public(grant.public_id)
    assert original["status"] == "current"
    connector = source("later-public-alias", "Identity metadata only; this is never quoted.")
    connector = connector.model_copy(
        update={
            "bibliography": SourceBibliography(
                doi="10.1234/synthetic-discovery", arxiv_id="2401.00001"
            )
        }
    )
    EvidenceStore(database).save_source(lease, connector)
    observed = sharing.get_public(grant.public_id)
    assert observed["projection"] == original["projection"]
    assert observed["projection_sha256"] == original["projection_sha256"]
    assert observed["status"] == "needs_review"
    assert sharing.list_public()["items"] == []
    assert sharing.public_map()["nodes"] == []


@pytest.mark.parametrize("with_locator", [True, False], ids=["locator", "plain-search-only"])
async def test_later_saved_locator_does_not_rewrite_an_earlier_committed_action_on_replay(
    database, monkeypatch, with_locator
):
    class LaterAliasTools(BibliographicTools):
        async def search(self, query, purpose):
            if query != "later alias metadata":
                return await super().search(query, purpose)
            return SearchResults(
                sources=(
                    SourceReference(
                        source_id="later-identity-connector",
                        title="Later identity metadata, never quoted",
                        url="https://example.org/later-identity",
                        bibliography=SourceBibliography(
                            doi="10.1234/synthetic-discovery",
                            arxiv_id=None if with_locator else "2401.00001",
                        ),
                    ),
                ),
                full_text=(
                    FullTextAvailability(
                        source_id="later-identity-connector",
                        status="candidate",
                        locator=ArxivFullTextLocator(arxiv_id="2401.00001", arxiv_version=1),
                    ),
                )
                if with_locator
                else (),
            )

    class LaterAliasModel(DiscoveryFixtureModel):
        async def complete(self, messages, *, max_output_tokens):
            observations = [
                json.loads(message["content"]).get("observation")
                for message in messages
                if message["role"] == "user"
            ]
            observations = [value for value in observations if value]
            if any(value.get("kind") == "stage_committed" for value in observations):
                self.calls += 1
                last = observations[-1]
                if last["kind"] == "stage_committed":
                    action = {
                        "kind": "select_fields",
                        "fields": ["Information theory", "Biology"],
                        "rationale": "Inspect later bibliographic metadata.",
                    }
                elif last["kind"] == "fields_selected":
                    action = {
                        "kind": "search_literature",
                        "query": "later alias metadata",
                        "purpose": "discovery",
                    }
                else:
                    action = {
                        "kind": "conclude",
                        "conclusion": {
                            "outcome": "insufficient_evidence",
                            "reason": "Later metadata prevents another supported candidate.",
                        },
                    }
                return ModelReply(json.dumps(action), 10, 15)
            return await super().complete(messages, max_output_tokens=max_output_tokens)

    original, lost = DiscoveryStore.record_search, False

    def lose_later_search_receipt(store, lease, ordinal, request_id, query, purpose, results):
        nonlocal lost
        original(store, lease, ordinal, request_id, query, purpose, results)
        if query == "later alias metadata" and not lost:
            lost = True
            raise RuntimeError("Synthetic late search acknowledgement loss")

    monkeypatch.setattr(DiscoveryStore, "record_search", lose_later_search_receipt)
    token, run_id = queued(database)
    initial_model = LaterAliasModel()
    instance = worker(database, initial_model)
    instance.tools_factory = lambda _: LaterAliasTools()
    initial = await instance.run_once("later-alias-before-response-loss")
    assert initial.status == "interrupted" and initial_model.calls == 9
    with Session(database) as session:
        stage_id = session.scalar(select(StageCommit.id).where(StageCommit.run_id == run_id))
    saved_detail = DiscoveryStore(database).get_stage(token, run_id, stage_id)
    snapshot = JobRepository(database).get(run_id)
    JobRepository(database).resume_interrupted(run_id, expected_token=snapshot.lease_token)
    replay = LaterAliasModel()
    instance.model_factory = lambda _: replay
    outcome = await instance.run_once("replay-historical-stage-before-alias")
    assert outcome.status == "succeeded" and outcome.result_id is None
    assert replay.calls == 1
    assert BudgetService(database).usage("fixture").reservations == 10
    actual = DiscoveryStore(database).get_stage(token, run_id, stage_id)
    assert actual["stage"] == saved_detail["stage"]
    assert actual["result"] == saved_detail["result"]
    assert actual["status"] == "needs_review"
    with pytest.raises(SharingError, match="sharing_source_not_current"):
        SharingStore(database, SharingPolicy(enabled=True)).preview(
            token, actual["stage"]["result_id"]
        )


async def test_store_rejects_different_passage_from_exact_same_parent_source_version(
    database, monkeypatch
):
    other_passage = "Another passage in this same immutable paper concerns a different premise."

    class DifferentPassageTools(DiscoveryFixtureTools):
        def __init__(self):
            super().__init__()
            doc = self.documents["left"]
            self.documents["left"] = doc.model_copy(
                update={"text": doc.text + "\n" + other_passage}
            )

    class DifferentPassageModel(DiscoveryFixtureModel):
        async def complete(self, messages, *, max_output_tokens):
            reply = await super().complete(messages, max_output_tokens=max_output_tokens)
            body = json.loads(reply.content)
            finding = body.get("finding", body.get("conclusion", {}))
            if finding.get("title", "").endswith("stage3"):
                finding["evidence"][0]["quote"] = other_passage
                return ModelReply(json.dumps(body), reply.input_tokens, reply.output_tokens)
            return reply

    # The store is an independent authority boundary. Deliberately bypass its
    # caller's matching pure validation to prove version equality is insufficient.
    monkeypatch.setattr(
        DiscoveryState,
        "validate_quotes",
        lambda state, quotes: (
            1 + max((parent.depth for parent in state.parents.values()), default=0)
        ),
    )
    observed = []
    original = DiscoveryStore.commit

    def capture_rejection(store, lease, packet):
        try:
            return original(store, lease, packet)
        except StageValidationError as error:
            observed.append(error.code)
            raise

    monkeypatch.setattr(DiscoveryStore, "commit", capture_rejection)
    _, run_id = queued(database)
    instance = worker(database, DifferentPassageModel())
    instance.tools_factory = lambda _: DifferentPassageTools()
    outcome = await instance.run_once("same-version-wrong-parent-passage")
    assert outcome.result_id is None
    assert "discovery_parent_original_quote_missing" in observed
    with Session(database) as session:
        stages = session.scalars(select(StageCommit).where(StageCommit.run_id == run_id)).all()
        assert len(stages) == 2


async def stage_parent_child(engine, *, keep_parent_active=False):
    owner, parent_run_id = queued(engine)
    instance = worker(engine, DiscoveryFixtureModel(), calls=12)
    instance.tools_factory = lambda _: BibliographicTools()
    assert (await instance.run_once("two-partial-stage-parents")).status == "interrupted"
    parent_lease = None
    if keep_parent_active:
        snapshot = JobRepository(engine).get(parent_run_id)
        JobRepository(engine).resume_interrupted(parent_run_id, expected_token=snapshot.lease_token)
        parent_lease = JobRepository(engine).claim_next("parent-later-alias", lease_seconds=300)
    else:
        JobRepository(engine).request_cancel(parent_run_id)
    with Session(engine) as session:
        stages = session.scalars(
            select(StageCommit)
            .where(StageCommit.run_id == parent_run_id)
            .order_by(StageCommit.ordinal)
        ).all()
        parent_ids = [str(stage.result_id) for stage in stages]
    assert len(parent_ids) == 2
    jobs = JobRepository(engine)
    child = jobs.create(mode="explore", goal="Synthetic F3 reuse of independently saved stages")
    assign(engine, child.id, owner)
    with Session(engine) as session, session.begin():
        refs = bind_parents(session, session.get(ResearchRun, child.id), owner, parent_ids)
    child_lease = jobs.claim_next("stage-child-final-save", lease_seconds=300)
    assert child_lease.run_id == child.id
    memory = ResearchMemory(engine, child_lease)
    store = EvidenceStore(engine)
    for reference in refs:
        finding = memory.read_finding(reference.result_id)
        for source_ref in finding.sources:
            doc = memory.read_source(reference.result_id, source_ref.retrieval_id)
            store.save_source(child_lease, doc)
    hydrated = store.get_result(parent_run_id, uuid.UUID(parent_ids[1]))
    result = ResearchResult.model_validate_json(json.dumps(hydrated.payload)).model_copy(
        update={"mode": "explore", "field_selections": (), "memory_reads": refs}
    )
    return owner, parent_run_id, parent_lease, child_lease, result


async def test_final_positive_f3_save_accepts_committed_stages_from_cancelled_parent_run(database):
    _, _, _, lease, result = await stage_parent_child(database)
    assert get_parent_refs(database, lease) == result.memory_reads
    saved = EvidenceStore(database).save_result(lease, result)
    assert saved.status == "bridge"
    assert saved.payload["memory_reads"] == [
        ref.model_dump(mode="json") for ref in result.memory_reads
    ]


async def test_final_positive_save_rechecks_later_parent_alias_not_just_admission(database):
    _, _, parent_lease, child_lease, result = await stage_parent_child(
        database, keep_parent_active=True
    )
    assert get_parent_refs(database, child_lease) == result.memory_reads
    connector = source("late-final-save-alias", "Later metadata is not scientific evidence.")
    connector = connector.model_copy(
        update={
            "bibliography": SourceBibliography(
                doi="10.1234/synthetic-discovery", arxiv_id="2401.00001"
            )
        }
    )
    EvidenceStore(database).save_source(parent_lease, connector)
    with pytest.raises(LineageError, match="research_basis_not_found"):
        get_parent_refs(database, child_lease)
    with pytest.raises(EvidenceError):
        EvidenceStore(database).save_result(child_lease, result)
    with Session(database) as session:
        assert (
            session.scalar(
                select(EvidenceResult.id).where(EvidenceResult.run_id == child_lease.run_id)
            )
            is None
        )
