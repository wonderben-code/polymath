"""Atomic evidence stages and scoped, replayable original-evidence observations."""

from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.budget import OPEN_STATES
from polymath.budget_models import BudgetReservation
from polymath.discovery_contracts import (
    DiscoveryFinishPacket,
    DiscoveryOutcome,
    StageCommitReceipt,
    StageContext,
    StageParentFinding,
    StageParentQuote,
)
from polymath.discovery_models import (
    DiscoveryStop,
    StageCommit,
    StageContextRecord,
    StageFinalSelection,
    StageObservation,
    StageParent,
)
from polymath.evidence import (
    EvidenceError,
    EvidenceStore,
    _bound_sources,
    _canonical,
    _document,
    _hash,
    _metadata,
    _StageValidation,
    _validate_document,
)
from polymath.evidence_models import EvidenceQuote, EvidenceResult, SourceRetrieval, SourceVersion
from polymath.jobs import _clear_lease, _event, _owned
from polymath.literature.identity import paper_aliases, validate_selected_paper_versions
from polymath.meta_readiness import _clock
from polymath.models import ResearchExternalRequest, ResearchRun
from polymath.research.models import (
    MemoryReadReference,
    MemorySourceReference,
    ResearchResult,
    SearchResults,
    SourceDocument,
    SourceReference,
    VerifiedQuote,
)


class DiscoveryError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class StageValidationError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _id(value):
    try:
        parsed = uuid.UUID(str(value))
        if str(parsed) != str(value):
            raise ValueError
        return parsed
    except (ValueError, TypeError, AttributeError):
        raise DiscoveryError("discovery_not_found") from None


def _typed(cls, value):
    try:
        return cls.model_validate_json(
            value.model_dump_json() if isinstance(value, cls) else json.dumps(value)
        )
    except (ValueError, TypeError, AttributeError):
        raise StageValidationError("discovery_invalid_payload") from None


def _stage_protocol(session, run):
    from polymath.stage_owners import StageOwnerError
    from polymath.stage_protocols import StageProtocolError, resolve_stage_protocol

    try:
        return resolve_stage_protocol(session, run)
    except (StageOwnerError, StageProtocolError, ValueError, TypeError, AttributeError):
        raise DiscoveryError("discovery_storage_unavailable") from None


def _stage_packet(protocol, value):
    try:
        return protocol.validate_result_branch_fields(value)
    except (ValueError, TypeError, AttributeError):
        raise StageValidationError("discovery_invalid_payload") from None


def _stage_context_action(protocol, context, action):
    from polymath.stage_protocols import StageProtocolError

    try:
        protocol.validate_context_action(context, action)
    except StageProtocolError as error:
        raise DiscoveryError(error.code) from None


def _stage_commit_action(protocol, packet, action):
    from polymath.stage_protocols import StageProtocolError

    try:
        protocol.validate_commit_action(packet, action)
    except StageProtocolError as error:
        raise DiscoveryError(error.code) from None


def _stage_memory_keys(protocol, session, run, result, reads):
    from polymath.stage_protocols import StageProtocolError

    try:
        return protocol.memory_keys(session, run, result, reads)
    except StageProtocolError as error:
        raise StageValidationError(error.code) from None


def _intent(session, run):
    # Historical Surprise wrapper: never reinterpret Explore as a ladder.
    protocol = _stage_protocol(session, run)
    return protocol.intent if protocol is not None and protocol.mode == "surprise" else None


def _owner(session, run_id):
    return session.scalar(
        select(ResearchRunAccess.session_hash)
        .join(BrowserSession, BrowserSession.token_hash == ResearchRunAccess.session_hash)
        .where(
            ResearchRunAccess.run_id == run_id, BrowserSession.expires_at > func.clock_timestamp()
        )
    )


def _owned_ladder(session, lease):
    run = _owned(session, lease)
    if _intent(session, run) is None or _owner(session, run.id) is None:
        raise DiscoveryError("discovery_not_found")
    return run


def _action(session, run, request_id):
    row = session.get(ResearchExternalRequest, (run.id, request_id))
    if row is None or row.status != "observed" or not isinstance(row.observation, dict):
        raise DiscoveryError("discovery_action_not_observed")
    try:
        return _stage_protocol(session, run).parse_action(row.observation["content"])
    except (ValueError, TypeError, KeyError):
        raise DiscoveryError("discovery_action_not_observed") from None


def _context(session, run, ordinal):
    row = session.get(StageContextRecord, (run.id, ordinal))
    if row is None or _hash(_canonical(row.context)) != row.context_sha256:
        raise DiscoveryError("discovery_context_missing")
    return _typed(StageContext, row.context)


def _observation(session, run_id, key):
    row = session.scalar(
        select(StageObservation).where(
            StageObservation.run_id == run_id, StageObservation.observation_key == key
        )
    )
    if row is not None and _hash(_canonical(row.payload)) != row.payload_sha256:
        raise DiscoveryError("discovery_storage_unavailable")
    return row


def _save_observation(session, run, context, kind, key, model_request_id, payload):
    prior = _observation(session, run.id, key)
    if prior is not None:
        if prior.payload != payload:
            raise DiscoveryError("discovery_replay_conflict")
        return prior
    row = StageObservation(
        run_id=run.id,
        context_ordinal=context.ordinal,
        kind=kind,
        observation_key=key,
        model_request_id=model_request_id,
        payload=payload,
        payload_sha256=_hash(_canonical(payload)),
    )
    session.add(row)
    session.flush()
    return row


def _receipt(row):
    if _hash(_canonical(row.receipt)) != row.receipt_sha256:
        raise DiscoveryError("discovery_storage_unavailable")
    receipt = _typed(StageCommitReceipt, row.receipt)
    if (
        receipt.stage_id != str(row.id)
        or receipt.run_id != str(row.run_id)
        or receipt.result_id != str(row.result_id)
        or receipt.result_sha256 != row.content_sha256
        or receipt.ordinal != row.ordinal
        or receipt.depth != row.depth
        or receipt.assessment.model_dump(mode="json") != row.assessment
    ):
        raise DiscoveryError("discovery_storage_unavailable")
    return receipt


def _alias_context(session, run_id, *, identity_limit=256):
    from polymath.full_text import FullTextError, owned_identity_context
    from polymath.research.discovery import discovery_search_identity_refs

    try:
        refs = list(
            _document(*row) for row in session.execute(_bound_sources(session, run_id)).all()
        ) + list(owned_identity_context(session, run_id, identity_limit=identity_limit))
    except FullTextError as error:
        if str(error) == "full_text_identity_context_limit":
            raise StageValidationError("source_identity_context_limit") from None
        raise
    observations = session.scalars(
        select(StageObservation).where(StageObservation.run_id == run_id)
    ).all()
    for observation in observations:
        if _hash(_canonical(observation.payload)) != observation.payload_sha256:
            raise DiscoveryError("discovery_storage_unavailable")
        if observation.kind == "search":
            results = _typed(SearchResults, observation.payload["results"])
            refs.extend(discovery_search_identity_refs(results.sources, results.full_text))
        elif observation.kind == "parent_read":
            parent = _typed(StageParentFinding, observation.payload)
            refs.extend(source.source for source in parent.sources)
        elif observation.kind == "source_read":
            refs.append(_typed(SourceDocument, observation.payload["document"]))
    run = session.get(ResearchRun, run_id)
    protocol = _stage_protocol(session, run)
    if protocol is not None:
        from polymath.stage_memory import StageMemoryError

        try:
            refs.extend(
                protocol.additional_identity_references(session, run, identity_limit=identity_limit)
            )
        except StageMemoryError as error:
            if error.code == "source_identity_context_limit":
                raise StageValidationError(error.code) from None
            raise DiscoveryError("discovery_storage_unavailable") from None
    dedup = {}
    for ref in refs:
        dedup.setdefault(paper_aliases(ref), ref)
        if len(dedup) > identity_limit:
            raise StageValidationError("source_identity_context_limit")
    return tuple(dedup.values())


def _stored_result(session, result):
    if _hash(_canonical(result.payload)) != result.content_sha256:
        raise DiscoveryError("discovery_storage_unavailable")
    return _typed(ResearchResult, EvidenceStore._stored_result(session, result).payload)


def stage_current_supported(session, result):
    stage = session.scalar(select(StageCommit).where(StageCommit.result_id == result.id))
    if stage is None:
        from polymath.stage_owners import has_stage_intent

        return not has_stage_intent(session, result.run_id)
    if stage.run_id != result.run_id or stage.content_sha256 != result.content_sha256:
        raise DiscoveryError("discovery_storage_unavailable")
    _receipt(stage)
    packet = _stored_result(session, result)
    run = session.get(ResearchRun, result.run_id)
    protocol = _stage_protocol(session, run)
    if protocol is None:
        raise DiscoveryError("discovery_storage_unavailable")
    protocol.validate_result_scope(_context(session, run, stage.context_ordinal), packet)
    try:
        source_map = {}
        for _quote, version, retrieval in session.execute(
            select(EvidenceQuote, SourceVersion, SourceRetrieval)
            .join(SourceVersion, SourceVersion.id == EvidenceQuote.version_id)
            .join(SourceRetrieval, SourceRetrieval.id == EvidenceQuote.retrieval_id)
            .where(EvidenceQuote.result_id == result.id)
        ).all():
            source_map[version.source_id] = (version, retrieval)
        context = _alias_context(session, result.run_id)
        EvidenceStore._validate_quotes(packet, source_map, alias_context=context)
        cited = {quote.source_id for quote in packet.quotes}
        validate_selected_paper_versions(
            tuple(doc for doc in packet.sources if doc.source_id in cited), context=context
        )
        return True
    except (EvidenceError, StageValidationError, ValueError):
        return False


def require_stage_current_supported(session, result):
    if not stage_current_supported(session, result):
        raise StageValidationError("discovery_stage_needs_review")


class _AtomicStageStore:
    """Shared transaction; concrete wrappers choose one stored protocol only."""

    _stage_mode = "surprise"

    def _owned_run(self, session, lease):
        run = _owned(session, lease)
        protocol = _stage_protocol(session, run)
        if protocol is None or protocol.mode != self._stage_mode or _owner(session, run.id) is None:
            raise DiscoveryError("discovery_not_found")
        return run

    def __init__(self, engine):
        if engine.dialect.name != "postgresql":
            raise ValueError("DiscoveryStore requires PostgreSQL")
        self.engine = engine
        self.evidence = EvidenceStore(engine)

    def load_intent(self, lease):
        with Session(self.engine) as session, session.begin():
            protocol = _stage_protocol(session, _owned(session, lease))
            return (
                protocol.intent
                if protocol is not None and protocol.mode == self._stage_mode
                else None
            )

    def begin_context(self, lease, context):
        context = _typed(StageContext, context)
        payload = context.model_dump(mode="json")
        with Session(self.engine) as session, session.begin():
            run = self._owned_run(session, lease)
            prior = session.get(StageContextRecord, (run.id, context.ordinal))
            if prior is not None:
                if prior.context != payload or prior.context_sha256 != _hash(_canonical(payload)):
                    raise DiscoveryError("discovery_replay_conflict")
                return
            action = _action(session, run, context.model_request_id)
            _stage_context_action(_stage_protocol(session, run), context, action)
            count = session.scalar(
                select(func.count())
                .select_from(StageContextRecord)
                .where(StageContextRecord.run_id == run.id)
            )
            if context.ordinal != count:
                raise DiscoveryError("discovery_context_order_invalid")
            previous = session.scalar(
                select(StageContextRecord)
                .where(StageContextRecord.run_id == run.id)
                .order_by(StageContextRecord.ordinal.desc())
                .limit(1)
            )
            if previous is not None:
                earlier = _typed(StageContext, previous.context)
                if (
                    context.model_request_id <= earlier.model_request_id
                    or context.action_ordinal <= earlier.action_ordinal
                ):
                    raise DiscoveryError("discovery_context_order_invalid")
            session.add(
                StageContextRecord(
                    run_id=run.id,
                    ordinal=context.ordinal,
                    context=payload,
                    context_sha256=_hash(_canonical(payload)),
                )
            )

    def read_parent(
        self, lease, result_id, context_ordinal, *, visible_stage_ids=(), model_request_id=None
    ):
        from polymath.assessment_eligibility import EligibilityError, require_current_supported

        identifier = _id(result_id)
        key = f"parent:{context_ordinal}:{identifier}"
        with Session(self.engine) as session, session.begin():
            run = self._owned_run(session, lease)
            context = _context(session, run, context_ordinal)
            prior = _observation(session, run.id, key)
            if prior is not None:
                return _typed(StageParentFinding, prior.payload)
            action = _action(session, run, model_request_id)
            if action.kind != "read_stage" or action.result_id != str(identifier):
                raise DiscoveryError("discovery_read_action_mismatch")
            result = session.get(EvidenceResult, identifier)
            if (
                result is None
                or result.status != "bridge"
                or _owner(session, result.run_id) != _owner(session, run.id)
            ):
                raise StageValidationError("discovery_parent_not_found")
            stage = session.scalar(select(StageCommit).where(StageCommit.result_id == result.id))
            same_run = result.run_id == run.id
            if same_run:
                if (
                    stage is None
                    or str(stage.id) not in tuple(map(str, visible_stage_ids))
                    or stage.action_ordinal >= context.action_ordinal
                    or stage.model_request_id >= context.model_request_id
                ):
                    raise StageValidationError("discovery_parent_not_observed")
            elif result.created_at >= run.created_at:
                raise StageValidationError("discovery_parent_not_found")
            try:
                require_current_supported(session, result.id)
            except (ValueError, EligibilityError):
                raise StageValidationError("discovery_parent_not_supported") from None
            require_stage_current_supported(session, result)
            scientific = _stored_result(session, result)
            sources = {}
            quotes = []
            for quote, version, retrieval in session.execute(
                select(EvidenceQuote, SourceVersion, SourceRetrieval)
                .join(SourceVersion, SourceVersion.id == EvidenceQuote.version_id)
                .join(SourceRetrieval, SourceRetrieval.id == EvidenceQuote.retrieval_id)
                .where(EvidenceQuote.result_id == result.id)
                .order_by(EvidenceQuote.ordinal)
            ).all():
                doc = _document(version, retrieval)
                sources.setdefault(
                    str(retrieval.id),
                    MemorySourceReference(
                        retrieval_id=str(retrieval.id),
                        source=doc,
                        content_sha256=version.content_sha256,
                        content_kind=version.content_kind,
                    ),
                )
                quotes.append(
                    StageParentQuote(
                        retrieval_id=str(retrieval.id),
                        version_id=str(version.id),
                        quote=VerifiedQuote(
                            source_id=version.source_id,
                            side=quote.side,
                            claim_id=quote.claim_id,
                            quote=quote.quote,
                            supports=quote.supports,
                            start=quote.start_offset,
                            end=quote.end_offset,
                            source_sha256=version.content_sha256,
                            content_kind=version.content_kind,
                        ),
                    )
                )
            value = StageParentFinding(
                reference=MemoryReadReference(
                    result_id=str(result.id),
                    run_id=str(result.run_id),
                    content_sha256=result.content_sha256,
                ),
                stage_id=str(stage.id) if same_run else None,
                ordinal=stage.ordinal if same_run else None,
                depth=stage.depth if same_run else 0,
                finding=scientific.finding,
                sources=tuple(sources.values()),
                quotes=tuple(quotes),
            )
            _save_observation(
                session,
                run,
                context,
                "parent_read",
                key,
                model_request_id,
                value.model_dump(mode="json"),
            )
            return value

    def read_parent_source(
        self,
        lease,
        result_id,
        retrieval_id,
        context_ordinal,
        *,
        visible_stage_ids=(),
        model_request_id=None,
    ):
        key = f"source:{context_ordinal}:{_id(result_id)}:{_id(retrieval_id)}"
        with Session(self.engine) as session, session.begin():
            run = self._owned_run(session, lease)
            context = _context(session, run, context_ordinal)
            prior = _observation(session, run.id, key)
            if prior is not None:
                return _typed(SourceDocument, prior.payload["document"])
            action = _action(session, run, model_request_id)
            if (
                action.kind != "read_stage_source"
                or action.result_id != result_id
                or action.retrieval_id != retrieval_id
            ):
                raise DiscoveryError("discovery_read_action_mismatch")
            parent = _observation(session, run.id, f"parent:{context_ordinal}:{_id(result_id)}")
            if parent is None:
                raise StageValidationError("discovery_parent_not_observed")
            finding = _typed(StageParentFinding, parent.payload)
            if not any(item.retrieval_id == retrieval_id for item in finding.sources):
                raise StageValidationError("discovery_parent_source_not_observed")
            row = session.execute(
                select(SourceVersion, SourceRetrieval)
                .join(SourceRetrieval, SourceRetrieval.version_id == SourceVersion.id)
                .where(SourceRetrieval.id == _id(retrieval_id))
            ).one()
            doc = _document(*row)
            version_id = row[0].id
        self.evidence.save_source(lease, doc)
        with Session(self.engine) as session, session.begin():
            run = self._owned_run(session, lease)
            context = _context(session, run, context_ordinal)
            payload = {
                "parent_result_id": result_id,
                "retrieval_id": retrieval_id,
                "version_id": str(version_id),
                "document": doc.model_dump(mode="json"),
            }
            _save_observation(session, run, context, "source_read", key, model_request_id, payload)
            return doc

    def record_search(self, lease, context_ordinal, model_request_id, query, purpose, results):
        results = _typed(SearchResults, results)
        key = f"search:{context_ordinal}:{model_request_id}"
        payload = {"query": query, "purpose": purpose, "results": results.model_dump(mode="json")}
        with Session(self.engine) as session, session.begin():
            run = self._owned_run(session, lease)
            context = _context(session, run, context_ordinal)
            prior = _observation(session, run.id, key)
            if prior is not None:
                if prior.payload != payload:
                    raise DiscoveryError("discovery_replay_conflict")
                return
            action = _action(session, run, model_request_id)
            if (
                action.kind != "search_literature"
                or action.query != query
                or action.purpose != purpose
                or model_request_id <= context.model_request_id
            ):
                raise DiscoveryError("discovery_search_action_mismatch")
            digest = hashlib.sha256(
                json.dumps(
                    {"kind": "search", "query": query, "purpose": purpose},
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
            ).hexdigest()
            if not any(
                item.get("hash") == digest and item.get("payload") == payload["results"]
                for item in run.checkpoint.get("tool_observations", {}).values()
                if isinstance(item, dict)
            ):
                raise DiscoveryError("discovery_search_not_observed")
            _save_observation(session, run, context, "search", key, model_request_id, payload)

    def commit(self, lease, packet):
        from polymath.assessment_eligibility import EligibilityError, require_current_supported

        with Session(self.engine) as session, session.begin():
            run = self._owned_run(session, lease)
            protocol = _stage_protocol(session, run)
            packet = _stage_packet(protocol, packet)
            digest = protocol.packet_sha256(packet)
            prior = session.scalar(
                select(StageCommit).where(
                    StageCommit.run_id == run.id,
                    StageCommit.model_request_id == packet.model_request_id,
                )
            )
            if prior is not None:
                if prior.packet_sha256 != digest or prior.action_ordinal != packet.action_ordinal:
                    raise DiscoveryError("discovery_replay_conflict")
                return _receipt(prior)
            context = _context(session, run, packet.context.ordinal)
            if context != packet.context:
                raise DiscoveryError("discovery_replay_conflict")
            latest_context = session.scalar(
                select(func.max(StageContextRecord.ordinal)).where(
                    StageContextRecord.run_id == run.id
                )
            )
            if (
                context.ordinal != latest_context
                or session.scalar(
                    select(StageCommit.id).where(
                        StageCommit.run_id == run.id, StageCommit.context_ordinal == context.ordinal
                    )
                )
                is not None
            ):
                raise StageValidationError("discovery_context_closed")
            action = _action(session, run, packet.model_request_id)
            _stage_commit_action(protocol, packet, action)
            stages = session.scalars(
                select(StageCommit)
                .where(StageCommit.run_id == run.id)
                .order_by(StageCommit.ordinal)
            ).all()
            if len(stages) >= protocol.max_stages:
                raise StageValidationError("discovery_stage_limit")
            observations = session.scalars(
                select(StageObservation).where(
                    StageObservation.run_id == run.id,
                    StageObservation.context_ordinal == context.ordinal,
                )
            ).all()
            searches = [
                row
                for row in observations
                if row.kind == "search"
                and row.model_request_id < packet.model_request_id
                and len(row.payload["results"].get("provider_failures", [])) < 2
            ]
            if not {"counterevidence", "prior_work"}.issubset(
                {row.payload["purpose"] for row in searches}
            ):
                raise StageValidationError("discovery_search_scope_missing")
            prior_refs = tuple(
                _typed(SourceReference, ref)
                for row in searches
                if row.payload["purpose"] == "prior_work"
                for ref in row.payload["results"]["sources"]
            )
            if packet.assessment.novelty in ("known", "partly_known"):
                from polymath.research.discovery import cited_prior_work_matches

                if not cited_prior_work_matches(
                    packet.result.quotes,
                    {doc.source_id: doc for doc in packet.result.sources},
                    prior_refs,
                    alias_context=_alias_context(session, run.id),
                ):
                    raise StageValidationError("discovery_prior_work_quote_missing")
            reads = {
                row.payload["reference"]["result_id"]: _typed(StageParentFinding, row.payload)
                for row in observations
                if row.kind == "parent_read"
            }
            selected = []
            logical = set()
            for reference in context.parent_refs:
                read = reads.get(reference.result_id)
                if read is None or read.reference != reference:
                    raise StageValidationError("discovery_parent_not_observed")
                result = session.get(EvidenceResult, _id(reference.result_id))
                if (
                    result is None
                    or result.content_sha256 != reference.content_sha256
                    or _owner(session, result.run_id) != _owner(session, run.id)
                ):
                    raise StageValidationError("discovery_parent_not_supported")
                try:
                    require_current_supported(session, result.id)
                except EligibilityError:
                    raise StageValidationError("discovery_parent_not_supported") from None
                require_stage_current_supported(session, result)
                root_id = _logical_finding(session, result.id)
                if root_id in logical:
                    raise StageValidationError("discovery_parent_revision_duplicate")
                logical.add(root_id)
                selected.append((reference, read))
            depth = protocol.commit_depth(selected)
            if depth > protocol.max_depth:
                raise StageValidationError("discovery_depth_limit")
            # Every parent must have its original retrieval explicitly reread;
            # child quotes may choose another saved retrieval of EXACT version.
            for reference, read in selected:
                covered = False
                for original in read.quotes:
                    source = _observation(
                        session,
                        run.id,
                        f"source:{context.ordinal}:{reference.result_id}:{original.retrieval_id}",
                    )
                    if source is None:
                        continue
                    for quote in packet.result.quotes:
                        if (
                            quote.source_id,
                            quote.source_sha256,
                            quote.content_kind,
                            quote.quote,
                            quote.start,
                            quote.end,
                        ) != (
                            original.quote.source_id,
                            original.quote.source_sha256,
                            original.quote.content_kind,
                            original.quote.quote,
                            original.quote.start,
                            original.quote.end,
                        ):
                            continue
                        versions = session.scalars(
                            select(SourceVersion.id)
                            .join(SourceRetrieval, SourceRetrieval.version_id == SourceVersion.id)
                            .where(
                                SourceVersion.id == _id(original.version_id),
                                SourceRetrieval.id == _id(original.retrieval_id),
                            )
                        ).all()
                        if versions and source.payload["version_id"] == original.version_id:
                            covered = True
                if not covered:
                    raise StageValidationError("discovery_parent_original_quote_missing")
            scientific_sha = protocol.scientific_sha256(context, packet.result)
            if (
                session.scalar(
                    select(StageCommit.id).where(
                        StageCommit.run_id == run.id,
                        StageCommit.scientific_sha256 == scientific_sha,
                    )
                )
                is not None
            ):
                raise StageValidationError("discovery_duplicate_scientific_body")
            if protocol.revision_reference(packet) is not None:
                original = reads.get(protocol.revision_reference(packet).result_id)
                if original is None or original.reference != protocol.revision_reference(packet):
                    raise StageValidationError("discovery_revision_not_observed")
            keys = _stage_memory_keys(protocol, session, run, packet.result, reads)
            cited = {q.source_id for q in packet.result.quotes}
            # The immutable packet may contain audit-only documents. Validate
            # those reads too, then persist only cited bodies in this finding.
            try:
                self.evidence._validate_result_shape(packet.result)
            except EvidenceError as error:
                raise StageValidationError(str(error)) from None
            source_map = {}
            for document in packet.result.sources:
                document = _validate_document(document)
                observed = session.execute(
                    _bound_sources(session, run.id).where(
                        SourceVersion.source_id == document.source_id,
                        SourceVersion.content_sha256 == document.content_sha256,
                        SourceVersion.content_kind == document.content_kind,
                        SourceRetrieval.metadata_sha256 == _hash(_canonical(_metadata(document))),
                    )
                ).first()
                if observed is None:
                    raise StageValidationError("source_not_observed")
                if document.source_id in source_map:
                    raise StageValidationError("ambiguous_source_version")
                source_map[document.source_id] = observed
            # Metadata contributes identity only, never authority to quote a body.
            alias_context = _alias_context(session, run.id)
            try:
                self.evidence._validate_quotes(
                    packet.result, source_map, alias_context=alias_context
                )
                validate_selected_paper_versions(
                    tuple(doc for doc in packet.result.sources if doc.source_id in cited),
                    context=alias_context,
                )
            except (EvidenceError, ValueError) as error:
                raise StageValidationError(str(error)) from None
            result = packet.result.model_copy(
                update={
                    "sources": tuple(doc for doc in packet.result.sources if doc.source_id in cited)
                }
            )
            try:
                saved = self.evidence._save_result_in_session(
                    session, lease, result, _stage_validation=_StageValidation(run.id, keys)
                )
            except EvidenceError as error:
                raise StageValidationError(str(error)) from None
            identifier = uuid.uuid4()
            receipt = StageCommitReceipt(
                stage_id=str(identifier),
                run_id=str(run.id),
                ordinal=len(stages),
                result_id=str(saved.id),
                result_sha256=_hash(_canonical(session.get(EvidenceResult, saved.id).payload)),
                depth=depth,
                assessment=packet.assessment,
                parent_refs=context.parent_refs,
                action_ordinal=packet.action_ordinal,
                model_request_id=packet.model_request_id,
            )
            body = receipt.model_dump(mode="json")
            stage = StageCommit(
                id=identifier,
                run_id=run.id,
                ordinal=len(stages),
                context_ordinal=context.ordinal,
                model_request_id=packet.model_request_id,
                action_ordinal=packet.action_ordinal,
                action_sha256=_hash(_canonical(action.model_dump(mode="json"))),
                packet_sha256=digest,
                result_id=saved.id,
                content_sha256=receipt.result_sha256,
                depth=depth,
                scientific_sha256=scientific_sha,
                assessment=packet.assessment.model_dump(mode="json"),
                parent_basis_sha256=_hash(
                    _canonical(
                        {
                            "context": context.model_dump(mode="json"),
                            "observations": sorted(row.payload_sha256 for row in observations),
                        }
                    )
                ),
                receipt=body,
                receipt_sha256=_hash(_canonical(body)),
            )
            session.add(stage)
            session.flush()
            for ordinal, (reference, read) in enumerate(selected):
                session.add(
                    StageParent(
                        stage_id=stage.id,
                        ordinal=ordinal,
                        parent_result_id=_id(reference.result_id),
                        parent_run_id=_id(reference.run_id),
                        parent_sha256=reference.content_sha256,
                        parent_stage_id=_id(read.stage_id) if read.stage_id else None,
                        kind="support",
                    )
                )
            if protocol.revision_reference(packet) is not None:
                reference = protocol.revision_reference(packet)
                read = reads[reference.result_id]
                session.add(
                    StageParent(
                        stage_id=stage.id,
                        ordinal=len(selected),
                        parent_result_id=_id(reference.result_id),
                        parent_run_id=_id(reference.run_id),
                        parent_sha256=reference.content_sha256,
                        parent_stage_id=_id(read.stage_id) if read.stage_id else None,
                        kind="revision",
                    )
                )
            checkpoint = dict(run.checkpoint)
            acks = dict(checkpoint.get("discovery_acks", {}))
            acks[packet.model_request_id] = body
            checkpoint["discovery_acks"] = acks
            run.checkpoint = checkpoint
            run.checkpoint_version += 1
            _event(
                session,
                run,
                "discovery_commit:" + packet.model_request_id,
                "stage_committed",
                f"Saved validated discovery stage {receipt.ordinal + 1}",
                [saved.artifact_ref],
            )
            return receipt

    def finish(self, lease, packet):
        # Explore's model-authored ranking/finish is its own immutable protocol.
        if self._stage_mode != "surprise":
            raise DiscoveryError("explore_collection_ranking_required")
        packet = _typed(DiscoveryFinishPacket, packet)
        payload = packet.model_dump(mode="json")
        with Session(self.engine) as session, session.begin():
            run = self._owned_run(session, lease)
            prior = session.get(StageFinalSelection, run.id)
            if prior is not None:
                if prior.packet != payload or prior.packet_sha256 != _hash(_canonical(payload)):
                    raise DiscoveryError("discovery_replay_conflict")
                return
            action = _action(session, run, packet.model_request_id)
            stage = session.scalar(
                select(StageCommit).where(
                    StageCommit.run_id == run.id,
                    StageCommit.result_id == _id(packet.stage.result_id),
                    StageCommit.content_sha256 == packet.stage.content_sha256,
                )
            )
            if stage is None or (
                action.kind != "finish_saved"
                and not (
                    action.kind == "conclude" and stage.model_request_id == packet.model_request_id
                )
            ):
                raise DiscoveryError("discovery_finish_action_mismatch")
            if action.kind == "finish_saved" and (
                action.stage != packet.stage or action.reason != packet.reason
            ):
                raise DiscoveryError("discovery_finish_action_mismatch")
            session.add(
                StageFinalSelection(
                    run_id=run.id,
                    stage_id=stage.id,
                    model_request_id=packet.model_request_id,
                    action_ordinal=packet.action_ordinal,
                    packet=payload,
                    packet_sha256=_hash(_canonical(payload)),
                )
            )

    def record_stop(self, lease, outcome):
        if self._stage_mode != "surprise":
            raise DiscoveryError("explore_collection_ranking_required")
        outcome = _typed(DiscoveryOutcome, outcome)
        with Session(self.engine) as session, session.begin():
            run = self._owned_run(session, lease)
            rows = session.scalars(
                select(StageCommit)
                .where(StageCommit.run_id == run.id)
                .order_by(StageCommit.ordinal)
            ).all()
            if tuple(_receipt(row) for row in rows) != outcome.stages:
                raise DiscoveryError("discovery_stop_replay_conflict")
            selection = session.get(StageFinalSelection, run.id)
            result_id = None
            if outcome.selected_stage is not None:
                if selection is None or selection.stage_id != _id(outcome.selected_stage.stage_id):
                    raise DiscoveryError("discovery_finish_missing")
                result_id = outcome.selected_stage.result_id
            elif selection is not None:
                raise DiscoveryError("discovery_stop_replay_conflict")
            elif not rows and outcome.result is not None:
                saved = self.evidence._save_result_in_session(
                    session,
                    lease,
                    outcome.result,
                    _stage_validation=_StageValidation(run.id, _context_memory_reads(session, run)),
                )
                result_id = str(saved.id)
            summary = {
                "reason": outcome.reason,
                "error_code": outcome.error_code,
                "status": outcome.status,
                "usage": outcome.usage.model_dump(mode="json"),
            }
            session.add(
                DiscoveryStop(
                    run_id=run.id, payload=summary, payload_sha256=_hash(_canonical(summary))
                )
            )
            run.status = (
                "interrupted"
                if outcome.status == "limit_reached"
                else "failed"
                if outcome.status == "failed"
                else "succeeded"
            )
            run.finished_at = _clock(session)
            run.interruption_reason = outcome.error_code or (
                "discovery_limit_reached" if outcome.status == "limit_reached" else None
            )
            run.outcome = {
                "result_id": result_id,
                "research_status": "bridge" if outcome.selected_stage else outcome.status,
                "discovery_status": outcome.status,
                "error_code": outcome.error_code,
            }
            _clear_lease(run)
            _event(
                session,
                run,
                "discovery_stopped",
                run.status,
                "Discovery stopped; committed stages and total usage retained",
            )

    def record_interruption(self, lease, reason):
        with Session(self.engine) as session, session.begin():
            run = self._owned_run(session, lease)
            run.status = "interrupted"
            run.lease_token += 1
            run.interruption_reason = (
                "external_outcome_unknown" if run.external_request_in_flight else reason
            )
            _clear_lease(run)

    def _owned_read(self, session, owner_hash, run_id):
        run = session.get(ResearchRun, _id(run_id))
        protocol = _stage_protocol(session, run) if run is not None else None
        if (
            run is None
            or _owner(session, run.id) != owner_hash
            or protocol is None
            or protocol.mode != self._stage_mode
        ):
            raise DiscoveryError("discovery_not_found")
        return run

    def summary(self, owner_hash, run_id):
        with Session(self.engine) as session:
            run = self._owned_read(session, owner_hash, run_id)
            rows = session.scalars(
                select(StageCommit)
                .where(StageCommit.run_id == run.id)
                .order_by(StageCommit.ordinal)
            ).all()
            selection = session.get(StageFinalSelection, run.id)
            stop = session.get(DiscoveryStop, run.id)
            if stop is not None and _hash(_canonical(stop.payload)) != stop.payload_sha256:
                raise DiscoveryError("discovery_storage_unavailable")
            totals, money = _totals(session, run, stop)
            return {
                "run_id": str(run.id),
                "run_status": run.status,
                "discovery_intent": _intent(session, run).model_dump(mode="json"),
                "stages": [_receipt(row).model_dump(mode="json") for row in rows],
                "selected_final_stage": next(
                    (
                        _receipt(row).model_dump(mode="json")
                        for row in rows
                        if selection is not None and row.id == selection.stage_id
                    ),
                    None,
                ),
                "stop_reason": stop.payload.get("reason") or stop.payload.get("error_code")
                if stop
                else run.interruption_reason
                or (run.status if run.status in ("cancelled", "interrupted", "failed") else None),
                "total_usage": totals,
                "accounting": money,
            }

    def list_stages(self, owner_hash, run_id):
        with Session(self.engine) as session:
            run = self._owned_read(session, owner_hash, run_id)
            rows = session.scalars(
                select(StageCommit)
                .where(StageCommit.run_id == run.id)
                .order_by(StageCommit.ordinal)
            ).all()
            return {"run_id": str(run.id), "stages": [_detail(session, row) for row in rows]}

    def get_stage(self, owner_hash, run_id, stage_id):
        with Session(self.engine) as session:
            run = self._owned_read(session, owner_hash, run_id)
            stage = session.get(StageCommit, _id(stage_id))
            if stage is None or stage.run_id != run.id:
                raise DiscoveryError("discovery_not_found")
            return _detail(session, stage)


class DiscoveryStore(_AtomicStageStore):
    """Existing Surprise API and worker entry point; unchanged terminal protocol."""

    _stage_mode = "surprise"


class ExploreStageStore(_AtomicStageStore):
    """Shared begin/search/commit/read path, inherited by Explore ranking store."""

    _stage_mode = "explore"

    def summary(self, owner_hash, run_id):
        raise DiscoveryError("explore_collection_ranking_required")


def _logical_finding(session, result_id):
    from polymath.assessment_eligibility import result_assessment_state

    seen = set()
    while result_id not in seen:
        seen.add(result_id)
        state = result_assessment_state(session, result_id)
        root = _id(state["root_result_id"])
        stage = session.scalar(select(StageCommit).where(StageCommit.result_id == root))
        edge = (
            session.scalar(
                select(StageParent).where(
                    StageParent.stage_id == stage.id, StageParent.kind == "revision"
                )
            )
            if stage
            else None
        )
        if edge is None:
            return root
        result_id = edge.parent_result_id
    raise DiscoveryError("discovery_revision_cycle")


def _context_memory_reads(session, run):
    ordinal = session.scalar(
        select(func.max(StageContextRecord.ordinal)).where(StageContextRecord.run_id == run.id)
    )
    if ordinal is None:
        return ()
    rows = session.scalars(
        select(StageObservation).where(
            StageObservation.run_id == run.id,
            StageObservation.context_ordinal == ordinal,
            StageObservation.kind == "parent_read",
        )
    ).all()
    return tuple(
        (
            row.payload["reference"]["result_id"],
            row.payload["reference"]["run_id"],
            row.payload["reference"]["content_sha256"],
        )
        for row in rows
    )


def _totals(session, run, stop):
    rows = session.scalars(
        select(BudgetReservation).where(BudgetReservation.run_id == run.id)
    ).all()
    usage = stop.payload["usage"] if stop is not None else {}
    journals = session.scalars(
        select(ResearchExternalRequest).where(
            ResearchExternalRequest.run_id == run.id,
            ResearchExternalRequest.request_id.like("model-%"),
        )
    ).all()
    by_reservation = {row.id: row for row in rows}
    reported_input = reported_output = unknown_calls = 0
    for journal in journals:
        facts = journal.observation or {}
        first, second = facts.get("input_tokens"), facts.get("output_tokens")
        row = by_reservation.get(journal.reservation_id)
        if not (
            type(first) is int
            and type(second) is int
            and 0 <= first <= 2**63 - 1
            and 0 <= second <= 2**63 - 1
        ):
            first, second = (row.prompt_tokens, row.completion_tokens) if row else (None, None)
        if first is None or second is None:
            unknown_calls += 1
        else:
            reported_input += first
            reported_output += second
    return (
        {
            "model_calls": max(len(journals), usage.get("model_calls", 0)),
            "input_tokens_reported": str(
                max(
                    reported_input,
                    usage.get("input_tokens_reported", 0),
                )
            ),
            "output_tokens_reported": str(
                max(
                    reported_output,
                    usage.get("output_tokens_reported", 0),
                )
            ),
            "calls_without_usage": max(unknown_calls, usage.get("calls_without_usage", 0)),
        },
        {
            "counted_spend_microdollars": str(
                sum(row.actual_microdollars or 0 for row in rows if row.status == "settled")
            ),
            "open_hold_microdollars": str(
                sum(row.estimated_microdollars for row in rows if row.status in OPEN_STATES)
            ),
            "unknown_hold_microdollars": str(
                sum(row.estimated_microdollars for row in rows if row.status == "unknown")
            ),
        },
    )


def _paper_groups(result, context):
    aliases = []
    for ref in context:
        values = set(paper_aliases(ref))
        touching = [group for group in aliases if group.intersection(values)]
        for group in touching:
            values.update(group)
            aliases.remove(group)
        aliases.append(values)
    sources = {source.source_id: source for source in result.sources}
    groups = []
    for quote in result.quotes:
        ref = sources[quote.source_id]
        identity = next(
            (group for group in aliases if group.intersection(paper_aliases(ref))),
            set(paper_aliases(ref)),
        )
        index = next((i for i, pair in enumerate(groups) if pair[0] == identity), None)
        if index is None:
            groups.append((identity, [quote.source_id]))
        elif quote.source_id not in groups[index][1]:
            groups[index][1].append(quote.source_id)
    return [
        {"paper_id": f"paper-{index + 1}", "source_ids": values}
        for index, (_, values) in enumerate(groups)
    ]


def _detail(session, stage):
    from polymath.assessment_eligibility import result_assessment_state

    row = session.get(EvidenceResult, stage.result_id)
    result = _stored_result(session, row)
    state = result_assessment_state(session, row.id)
    status = (
        "unsupported"
        if state["state"] == "unsupported"
        else "superseded"
        if not state["is_current"]
        else "current_supported"
    )
    if status == "current_supported" and not stage_current_supported(session, row):
        status = "needs_review"
    parents = session.scalars(
        select(StageParent).where(StageParent.stage_id == stage.id).order_by(StageParent.ordinal)
    ).all()
    return {
        "stage": _receipt(stage).model_dump(mode="json"),
        "result": result.model_dump(mode="json"),
        "parent_edges": [
            {
                "kind": edge.kind,
                "ordinal": edge.ordinal,
                "reference": {
                    "result_id": str(edge.parent_result_id),
                    "run_id": str(edge.parent_run_id),
                    "content_sha256": edge.parent_sha256,
                },
                "stage_id": str(edge.parent_stage_id) if edge.parent_stage_id else None,
            }
            for edge in parents
        ],
        "status": status,
        # History preserves every connector even after the smaller current-write
        # identity limit is exceeded. This does not grant new quote/read scope.
        "primary_paper_groups": _paper_groups(
            result, _alias_context(session, row.run_id, identity_limit=4096)
        ),
    }
