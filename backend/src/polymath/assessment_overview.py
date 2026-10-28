"""Explicit bound-Ask reassessment schema using the existing assessment loop."""

from __future__ import annotations

import json
from typing import Annotated

from pydantic import Field, TypeAdapter

from polymath.assessment_contracts import (
    AssessmentBasis,
    ConcludeAssessmentAction,
    ReadAssessmentLiteratureAction,
    ReadAssessmentSourceAction,
)
from polymath.assessment_controller import EXCERPT_LIMITATION, SYSTEM_PROMPT
from polymath.research.models import (
    AskOverviewBinding,
    OpenFullTextAction,
    ReadFullTextWindowAction,
    SearchAction,
)
from polymath.scientific_context_contracts import (
    ASK_OVERVIEW_PROTOCOL,
    ASK_REASSESSMENT_OVERVIEW_PROTOCOL,
    AskReassessmentOverviewIntent,
    BoundAnswerDraft,
)

ASK_REASSESSMENT_OVERVIEW_CONTROLLER_PROTOCOL = "polymath-meta-ask-reassessment-overview-v1"
ASK_REASSESSMENT_OVERVIEW_SYSTEM_PROMPT = (
    SYSTEM_PROMPT
    + """

This task uses the explicit claim-bound Ask reassessment protocol. For a revised
answer, emit revised_finding as the supplied BoundAnswerDraft: individually
classified/cited claims and ordered unique overview_claim_ids naming claims in
that same candidate. There is no summary field. The application copies those
exact selected texts, separated by blank lines, without adding or paraphrasing
scientific claims. Keep the joined text within3000 characters. Every selected
claim retains its label, evidence, derivation and caveats. Do not claim stronger
support merely because a claim appears in the overview. An unchanged disposition
retains the exact prior result and any prior overview binding; it does not create
a replacement or restore an unsupported state. A revised candidate must still
satisfy every original evidence, question, classification and search requirement.
"""
)


class AskReassessmentOverviewConcludeAction(ConcludeAssessmentAction):
    revised_finding: BoundAnswerDraft | None = None


AskReassessmentOverviewAction = Annotated[
    ReadAssessmentSourceAction
    | ReadAssessmentLiteratureAction
    | SearchAction
    | OpenFullTextAction
    | ReadFullTextWindowAction
    | AskReassessmentOverviewConcludeAction,
    Field(discriminator="kind"),
]
ASK_REASSESSMENT_OVERVIEW_ACTION_ADAPTER = TypeAdapter(AskReassessmentOverviewAction)


def ask_reassessment_overview_initial_messages(
    basis: AssessmentBasis | None = None,
) -> list[dict[str, str]]:
    """Exact execution messages; None exposes only the fixed sizing wrapper.

    Controller execution always requires a validated real AssessmentBasis.
    """
    schema = json.dumps(
        ASK_REASSESSMENT_OVERVIEW_ACTION_ADAPTER.json_schema(), separators=(",", ":")
    )
    return [
        {
            "role": "system",
            "content": ASK_REASSESSMENT_OVERVIEW_SYSTEM_PROMPT + "\nAction schema:\n" + schema,
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "assessment_basis": basis.model_dump(mode="json") if basis is not None else {},
                    "ask_reassessment_overview_intent": {
                        "protocol": ASK_REASSESSMENT_OVERVIEW_PROTOCOL
                    },
                }
            ),
        },
    ]


def normalise_ask_reassessment_conclusion(action) -> tuple[object, AskOverviewBinding | None]:
    if not isinstance(action, AskReassessmentOverviewConcludeAction):
        return action, None
    finding = action.revised_finding
    return (
        ConcludeAssessmentAction(
            kind=action.kind,
            disposition=action.disposition,
            rationale=action.rationale,
            limitations=action.limitations,
            selected_retrieval_ids=action.selected_retrieval_ids,
            revised_finding=finding.to_answer() if finding is not None else None,
        ),
        finding.overview_binding() if finding is not None else None,
    )


def ask_reassessment_overview_protocol_payload() -> dict:
    """Actual static variant inputs for the separately reviewed native profile."""
    return {
        "controller_protocol": ASK_REASSESSMENT_OVERVIEW_CONTROLLER_PROTOCOL,
        "intent_protocol": ASK_REASSESSMENT_OVERVIEW_PROTOCOL,
        "intent_schema": AskReassessmentOverviewIntent.model_json_schema(),
        "overview_binding_protocol": ASK_OVERVIEW_PROTOCOL,
        "overview_binding_schema": AskOverviewBinding.model_json_schema(),
        "assessment_basis_schema": AssessmentBasis.model_json_schema(),
        "system_prompt": ASK_REASSESSMENT_OVERVIEW_SYSTEM_PROMPT,
        "excerpt_limitation": EXCERPT_LIMITATION,
        "action_schema": ASK_REASSESSMENT_OVERVIEW_ACTION_ADAPTER.json_schema(),
    }
