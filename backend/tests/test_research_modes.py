"""Mode contracts exercised with synthetic, observation-dependent fixtures only."""

import copy
import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from polymath.research import ModelReply, ResearchController, ResearchLimits
from polymath.research.models import (
    AnswerFinding,
    FieldSelection,
    SourceDocument,
    SourceReference,
)

LEFT_QUOTE = "Synthetic benchmark: redundancy improved transmission robustness."
RIGHT_QUOTE = "Synthetic benchmark: parallel pathways preserved signal under perturbation."
COUNTER_QUOTE = "Synthetic counterevidence: this pathway did not preserve the signal."


def fixture_source(side: str, *, contradictory=False) -> SourceDocument:
    return SourceDocument(
        source_id=side,
        title=f"Synthetic mode fixture {side}",
        url=f"https://example.org/synthetic-modes/{side}",
        text=LEFT_QUOTE if side == "left" else COUNTER_QUOTE if contradictory else RIGHT_QUOTE,
        content_kind="abstract",
        retrieved_at=datetime(2026, 10, 27, tzinfo=UTC),
        extraction_method="synthetic_mode_fixture",
    )


class ModeFixtureTools:
    """Synthetic source access for controller, worker, and browser integration tests."""

    def __init__(self, *, contradictory=False):
        self.contradictory = contradictory
        self.searches = []
        self.reads = []

    async def search(self, query, purpose):
        self.searches.append((query, purpose))
        if purpose != "discovery":
            return []
        return [
            SourceReference(
                source_id=side,
                title=f"Synthetic mode fixture {side}",
                url=f"https://example.org/synthetic-modes/{side}",
            )
            for side in ("left", "right")
        ]

    async def read(self, source_id):
        if source_id not in {"left", "right"}:
            raise ValueError("Unknown synthetic source")
        self.reads.append(source_id)
        return fixture_source(source_id, contradictory=self.contradictory)


def fixture_answer(question: str) -> dict:
    left = {"source_id": "left", "quote": LEFT_QUOTE, "supports": "Synthetic redundancy result"}
    right = {"source_id": "right", "quote": RIGHT_QUOTE, "supports": "Synthetic pathway result"}
    return {
        "kind": "conclude",
        "conclusion": {
            "outcome": "answer",
            "question": question,
            "summary": "Synthetic claims demonstrate evidence labels, not a scientific discovery.",
            "claims": [
                {
                    "id": "known_1",
                    "text": "The fixture reports robust redundant transmission.",
                    "label": "known",
                    "evidence": [left],
                    "caveats": ["This is a synthetic abstract, not independent verification."],
                },
                {
                    "id": "postulated_1",
                    "text": "A shared redundancy analogy may follow.",
                    "label": "postulated",
                    "evidence": [left, right],
                    "derivation": "If the fixture noise assumptions match, compare redundancy; "
                    "this remains an unverified inference.",
                },
                {
                    "id": "speculative_1",
                    "text": "The analogy might extend to other signals.",
                    "label": "speculative",
                    "evidence": [right],
                    "caveats": ["Unmeasured conditions could break the proposed extension."],
                },
            ],
            "limitations": ["Sources and inference are synthetic fixtures, not actual research."],
            "unanswered_questions": ["Would the analogy survive a measured noise model?"],
        },
    }


def fixture_bridge(fields=("Information theory", "Biology")) -> dict:
    return {
        "kind": "conclude",
        "conclusion": {
            "outcome": "bridge",
            "title": "Synthetic autonomous-field connection",
            "field_left": fields[0],
            "field_right": fields[1],
            "explanation": "A synthetic example comparing redundant representations.",
            "mappings": [
                {
                    "left": "Redundant messages",
                    "right": "Parallel pathways",
                    "assumption": "The noise models must be comparable.",
                }
            ],
            "open_question": "Does the fixture mapping survive realistic perturbations?",
            "strongest_objection": "The mechanisms may differ.",
            "objection_verdict": "The fixture suggests an analogy, not equivalence.",
            "novelty_limitations": "Synthetic searches cannot establish scientific novelty.",
            "evidence": [
                {
                    "source_id": "left",
                    "side": "left",
                    "quote": LEFT_QUOTE,
                    "supports": "Synthetic redundancy result",
                },
                {
                    "source_id": "right",
                    "side": "right",
                    "quote": RIGHT_QUOTE,
                    "supports": "Synthetic pathway result",
                },
            ],
        },
    }


def rejection(reason="Synthetic evidence does not support the proposed connection.") -> dict:
    return {
        "kind": "conclude",
        "conclusion": {"outcome": "insufficient_evidence", "reason": reason},
    }


class AskFixtureModel:
    """A test model that changes its answer when the observed text contradicts it."""

    def __init__(self):
        self.seen = []

    async def complete(self, messages, *, max_output_tokens):
        goal = json.loads(messages[1]["content"])["research_goal"]
        observation = json.loads(messages[-1]["content"]).get("observation")
        self.seen.append(observation)
        if observation is None:
            action = {
                "kind": "search_literature",
                "query": f"synthetic evidence: {goal[:200]}",
                "purpose": "discovery",
            }
        elif observation.get("kind") == "search_results" and observation["purpose"] == "discovery":
            action = {"kind": "read_source", "source_id": observation["sources"][0]["source_id"]}
        elif observation.get("kind") == "source_text" and observation["source_id"] == "left":
            action = {"kind": "read_source", "source_id": "right"}
        elif observation.get("kind") == "source_text" and "counterevidence" in observation["text"]:
            action = rejection("Synthetic retrieved text contradicts the planned inference.")
        elif observation.get("kind") == "source_text":
            action = {
                "kind": "search_literature",
                "query": f"synthetic objections: {goal[:200]}",
                "purpose": "counterevidence",
            }
        elif observation.get("purpose") == "counterevidence":
            action = {
                "kind": "search_literature",
                "query": f"synthetic prior work: {goal[:200]}",
                "purpose": "prior_work",
            }
        else:
            action = fixture_answer(goal)
        return ModelReply(json.dumps(action), 10, 15)


class SurpriseFixtureModel:
    """Test-only autonomous selection; the production controller supplies no field list."""

    def __init__(self, fields=("Information theory", "Biology")):
        self.fields = fields
        self.seen = []

    async def complete(self, messages, *, max_output_tokens):
        observation = json.loads(messages[-1]["content"]).get("observation")
        self.seen.append(observation)
        if observation is None:
            action = {
                "kind": "select_fields",
                "fields": self.fields,
                "rationale": "Synthetic rationale: compare distinct resilience mechanisms.",
            }
        elif observation.get("kind") == "fields_selected":
            action = {
                "kind": "search_literature",
                "query": f"synthetic {' '.join(observation['fields'])} structures",
                "purpose": "discovery",
            }
        elif observation.get("kind") == "search_results" and observation["purpose"] == "discovery":
            action = {"kind": "read_source", "source_id": observation["sources"][0]["source_id"]}
        elif observation.get("kind") == "source_text" and observation["source_id"] == "left":
            action = {"kind": "read_source", "source_id": "right"}
        elif observation.get("kind") == "source_text" and "counterevidence" in observation["text"]:
            action = rejection()
        elif observation.get("kind") == "source_text":
            action = {
                "kind": "search_literature",
                "query": "synthetic counterexamples",
                "purpose": "counterevidence",
            }
        elif observation.get("purpose") == "counterevidence":
            action = {
                "kind": "search_literature",
                "query": "synthetic known connections",
                "purpose": "prior_work",
            }
        else:
            action = fixture_bridge(self.fields)
        return ModelReply(json.dumps(action), 10, 15)


class SequenceFixtureModel:
    def __init__(self, actions):
        self.actions = iter(actions)
        self.observations = []

    async def complete(self, messages, *, max_output_tokens):
        self.observations.append(json.loads(messages[-1]["content"]).get("observation"))
        return ModelReply(json.dumps(next(self.actions)), 10, 15)


def research_prefix():
    return [
        {"kind": "search_literature", "query": "synthetic sources", "purpose": "discovery"},
        {"kind": "read_source", "source_id": "left"},
        {"kind": "read_source", "source_id": "right"},
        {
            "kind": "search_literature",
            "query": "synthetic objections",
            "purpose": "counterevidence",
        },
        {
            "kind": "search_literature",
            "query": "synthetic prior connections",
            "purpose": "prior_work",
        },
    ]


@pytest.mark.asyncio
async def test_ask_classifies_claims_and_preserves_per_claim_quote_provenance():
    events = []

    async def record(event):
        events.append(event)

    model, tools = AskFixtureModel(), ModeFixtureTools()
    result = await ResearchController(model, tools, on_event=record).investigate(
        "What does synthetic redundancy imply?", mode="ask"
    )
    assert result.status == "answer"
    assert result.mode == "ask"
    assert [claim.label for claim in result.finding.claims] == [
        "known",
        "postulated",
        "speculative",
    ]
    assert {quote.claim_id for quote in result.quotes} == {
        "known_1",
        "postulated_1",
        "speculative_1",
    }
    for quote in result.quotes:
        document = next(source for source in result.sources if source.source_id == quote.source_id)
        assert document.text[quote.start : quote.end] == quote.quote
        assert quote.source_sha256 == document.content_sha256
        assert quote.side == "answer"
        assert quote.content_kind == "abstract"
    assert result.finding.limitations
    assert result.finding.unanswered_questions
    assert events == list(result.events)
    assert tools.searches[0][0].endswith("What does synthetic redundancy imply?")


@pytest.mark.asyncio
async def test_ask_reacts_to_changed_evidence_and_retains_the_original_question():
    question = "Could this synthetic analogy be wrong?"
    result = await ResearchController(
        AskFixtureModel(), ModeFixtureTools(contradictory=True)
    ).investigate(question, mode="ask")
    assert result.status == "insufficient_evidence"
    assert "contradicts" in result.finding.reason
    assert result.quotes == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutate,error",
    [
        (
            lambda answer: answer["claims"][0]["evidence"][0].update(source_id="invented"),
            "unread_source",
        ),
        (
            lambda answer: answer["claims"][0]["evidence"][0].update(
                quote="An invented quotation."
            ),
            "quotation_not_found",
        ),
        (
            lambda answer: answer["claims"][1].update(evidence=answer["claims"][1]["evidence"][:1]),
            "postulated_requires_distinct_sources",
        ),
        (lambda answer: answer.update(question="A different question"), "answer_question_mismatch"),
    ],
)
async def test_ask_rejects_forged_or_misbound_answer_then_can_admit_insufficient_evidence(
    mutate, error
):
    action = fixture_answer("Synthetic question?")
    mutate(action["conclusion"])
    model = SequenceFixtureModel([*research_prefix(), action, rejection()])
    result = await ResearchController(model, ModeFixtureTools()).investigate(
        "Synthetic question?", mode="ask"
    )
    assert result.status == "insufficient_evidence"
    assert model.observations[-1]["error"] == error
    assert any(event.kind == "action_rejected" for event in result.events)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda answer: answer["claims"][1].pop("derivation"),
        lambda answer: answer["claims"][2].update(caveats=[]),
        lambda answer: answer["claims"][1].update(id="known_1"),
        lambda answer: answer["claims"][0].update(evidence=[]),
    ],
)
def test_answer_schema_requires_inference_uncertainty_unique_ids_and_citations(mutate):
    answer = fixture_answer("Synthetic question?")["conclusion"]
    mutate(answer)
    with pytest.raises(ValidationError):
        AnswerFinding.model_validate_json(json.dumps(answer))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fields", [("Information theory", "Biology"), ("Mycology", "Orbital dynamics")]
)
async def test_surprise_model_selects_its_own_fields_before_investigation(fields):
    model, tools = SurpriseFixtureModel(fields), ModeFixtureTools()
    result = await ResearchController(model, tools).investigate("", mode="surprise")
    assert result.status == "bridge"
    assert result.mode == "surprise"
    assert result.field_selections[0].fields == fields
    assert result.finding.field_left == fields[0]
    assert result.finding.field_right == fields[1]
    assert result.events[0].kind == "fields_selected"
    assert result.field_selections[0].rationale in result.events[0].summary
    assert fields[0] in tools.searches[0][0]


@pytest.mark.asyncio
async def test_surprise_cannot_search_before_selection_or_force_a_discovery():
    tools = ModeFixtureTools()
    model = SequenceFixtureModel(
        [
            {"kind": "search_literature", "query": "unselected fields", "purpose": "discovery"},
            rejection(),
        ]
    )
    result = await ResearchController(model, tools).investigate("", mode="surprise")
    assert result.status == "insufficient_evidence"
    assert tools.searches == []
    assert result.field_selections == ()
    assert model.observations[-1]["error"] == "fields_not_selected"


@pytest.mark.asyncio
async def test_surprise_field_revision_requires_fresh_challenge_searches():
    original = {
        "kind": "select_fields",
        "fields": ["Information theory", "Biology"],
        "rationale": "Synthetic original direction.",
    }
    revised = {
        "kind": "select_fields",
        "fields": ["Mycology", "Orbital dynamics"],
        "rationale": "Synthetic revision after weak evidence.",
    }
    model = SequenceFixtureModel(
        [
            original,
            *research_prefix(),
            revised,
            fixture_bridge(("Mycology", "Orbital dynamics")),
            rejection(),
        ]
    )
    result = await ResearchController(model, ModeFixtureTools()).investigate("", mode="surprise")
    assert result.status == "insufficient_evidence"
    assert len(result.field_selections) == 2
    assert model.observations[-1]["error"] == "missing_required_searches"


@pytest.mark.asyncio
async def test_surprise_bridge_must_use_current_fields_and_revisions_are_bounded():
    select = {
        "kind": "select_fields",
        "fields": ["Mycology", "Orbital dynamics"],
        "rationale": "Synthetic chosen direction.",
    }
    model = SequenceFixtureModel(
        [
            select,
            *research_prefix(),
            fixture_bridge(),
            rejection(),
        ]
    )
    result = await ResearchController(model, ModeFixtureTools()).investigate("", mode="surprise")
    assert result.status == "insufficient_evidence"
    assert model.observations[-1]["error"] == "bridge_outside_current_fields"
    repeated = SequenceFixtureModel([select, select, select, select, rejection()])
    limited = await ResearchController(repeated, ModeFixtureTools()).investigate(
        "", mode="surprise"
    )
    assert len(limited.field_selections) == 3
    assert repeated.observations[-1]["error"] == "field_selection_limit"


@pytest.mark.parametrize(
    "fields",
    [
        ["Biology", " biology "],
        ["Quantum  physics", "Quantum physics"],
        ["Only one"],
    ],
)
def test_field_selection_rejects_duplicate_or_insufficient_fields(fields):
    with pytest.raises(ValidationError):
        FieldSelection.model_validate_json(
            json.dumps({"fields": fields, "rationale": "Synthetic rationale"})
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,action",
    [
        ("ask", fixture_bridge()),
        ("explore", fixture_answer("Synthetic question?")),
        ("surprise", fixture_answer("Synthetic question?")),
    ],
)
async def test_positive_conclusion_kinds_cannot_cross_modes(mode, action):
    model = SequenceFixtureModel([copy.deepcopy(action), rejection()])
    result = await ResearchController(model, ModeFixtureTools()).investigate(
        "Synthetic question?", mode=mode
    )
    assert result.status == "insufficient_evidence"
    assert model.observations[-1]["error"] in {"answer_wrong_mode", "bridge_wrong_mode"}


@pytest.mark.asyncio
async def test_ask_and_surprise_stop_at_limits_without_inventing_findings():
    for mode in ("ask", "surprise"):
        model = SequenceFixtureModel([{"kind": "read_source", "source_id": "undiscovered"}])
        result = await ResearchController(
            model, ModeFixtureTools(), ResearchLimits(max_model_calls=1)
        ).investigate("Synthetic incomplete question?", mode=mode)
        assert result.status == "limit_reached"
        assert result.finding is None


@pytest.mark.asyncio
async def test_malformed_public_rationale_is_repaired_before_event_persistence():
    model = SequenceFixtureModel(
        [
            {
                "kind": "select_fields",
                "fields": ["Mycology", "Orbital dynamics"],
                "rationale": "Invalid\x00public rationale",
            },
            rejection(),
        ]
    )
    result = await ResearchController(model, ModeFixtureTools()).investigate("", mode="surprise")
    assert result.status == "insufficient_evidence"
    assert result.field_selections == ()
    assert model.observations[-1]["error"] == "invalid_action_schema"
    assert all("\x00" not in event.summary for event in result.events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    [
        {
            "kind": "conclude",
            "conclusion": {"outcome": "insufficient_evidence", "reason": "Bad\x00text"},
        },
        {
            "kind": "conclude",
            "conclusion": {"outcome": "insufficient_evidence", "reason": "Bad\ud800text"},
        },
    ],
)
async def test_invalid_conclusion_text_stays_in_bounded_model_repair_loop(action):
    model = SequenceFixtureModel([action, rejection()])
    result = await ResearchController(model, ModeFixtureTools()).investigate("Synthetic concept")
    assert result.status == "insufficient_evidence"
    assert model.observations[-1]["error"] == "invalid_action_schema"


@pytest.mark.asyncio
async def test_provider_outage_is_an_error_observation_not_a_successful_empty_search():
    class UnavailableTools(ModeFixtureTools):
        async def search(self, query, purpose):
            raise RuntimeError("Untrusted provider failure must not leak")

    for mode in ("ask", "surprise"):
        actions = []
        if mode == "surprise":
            actions.append(
                {
                    "kind": "select_fields",
                    "fields": ["Mycology", "Orbital dynamics"],
                    "rationale": "Synthetic field-selection rationale.",
                }
            )
        actions.extend(
            [
                {"kind": "search_literature", "query": "Synthetic query", "purpose": "discovery"},
                rejection("Synthetic provider access was unavailable."),
            ]
        )
        model = SequenceFixtureModel(actions)
        result = await ResearchController(model, UnavailableTools()).investigate(
            "Synthetic question?", mode=mode
        )
        assert result.status == "insufficient_evidence"
        assert model.observations[-1] == {"error": "literature_search_failed"}
        assert not any(event.kind == "search_completed" for event in result.events)
        assert "Untrusted provider" not in json.dumps(result.model_dump(mode="json"))
