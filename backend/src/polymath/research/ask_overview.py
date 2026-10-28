"""Opt-in Ask conclusion protocol using exact model-selected classified claims.

The existing controller still chooses tools, enforces evidence and owns every
counter/repair/deadline. This module supplies a separate schema and fixed prompt;
it grants no admission, spending or source authority.
"""

from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, ValidationError, model_validator

from polymath.research.controller import MODE_INSTRUCTIONS, SYSTEM_PROMPT
from polymath.research.models import (
    AskOverviewBinding,
    ConcludeAction,
    InsufficientFinding,
    OpenFullTextAction,
    ReadAction,
    ReadFullTextWindowAction,
    ReadMemoryAction,
    ReadMemorySourceAction,
    SearchAction,
    SearchMemoryAction,
    SelectFieldsAction,
    StrictModel,
    _validate_public_text,
)
from polymath.scientific_context_contracts import (
    ASK_OVERVIEW_PROTOCOL,
    AskOverviewIntent,
    BoundAnswerDraft,
)

ASK_OVERVIEW_CONTROLLER_PROTOCOL = "polymath-meta-ask-claim-overview-v1"
_LEGACY_OVERVIEW_INSTRUCTION = (
    "The summary\nis only scope/orientation: put substantive answer assertions in the labelled,\n"
    "cited claims rather than introducing uncited claims in an overview."
)
_OVERVIEW_INSTRUCTION = (
    "The overview contains only claims you select from this same conclusion. "
    "Emit overview_claim_ids as an ordered list of existing claim IDs, with no duplicates. "
    "There is no summary field. The application copies those exact claim texts in that "
    "order, separated by blank lines; their labels, evidence, derivations and caveats "
    "remain attached. Select enough claims to answer usefully without exceeding 3000 "
    "characters across the joined exact texts. Do not supply extra overview prose, "
    "paraphrase a selected claim, or infer a stronger classification from its inclusion."
)
assert SYSTEM_PROMPT.count(_LEGACY_OVERVIEW_INSTRUCTION) == 1
ASK_OVERVIEW_SYSTEM_PROMPT = SYSTEM_PROMPT.replace(
    _LEGACY_OVERVIEW_INSTRUCTION, _OVERVIEW_INSTRUCTION
)


class AskOverviewConcludeAction(StrictModel):
    kind: Literal["conclude"]
    conclusion: Annotated[BoundAnswerDraft | InsufficientFinding, Field(discriminator="outcome")]

    @model_validator(mode="after")
    def public_conclusion(self):
        _validate_public_text(self.conclusion.model_dump(mode="json"))
        return self


AskOverviewAction = Annotated[
    SearchAction
    | ReadAction
    | OpenFullTextAction
    | ReadFullTextWindowAction
    | SearchMemoryAction
    | ReadMemoryAction
    | ReadMemorySourceAction
    | SelectFieldsAction
    | AskOverviewConcludeAction,
    Field(discriminator="kind"),
]
ASK_OVERVIEW_ACTION_ADAPTER = TypeAdapter(AskOverviewAction)


def ask_overview_initial_messages(goal: str, *, memory_available: bool) -> list[dict[str, str]]:
    """Same exact builder for profile sizing, admission preflight and execution."""
    schema = json.dumps(ASK_OVERVIEW_ACTION_ADAPTER.json_schema(), separators=(",", ":"))
    return [
        {
            "role": "system",
            "content": ASK_OVERVIEW_SYSTEM_PROMPT
            + "\nCurrent mode: ask. "
            + MODE_INSTRUCTIONS["ask"]
            + "\nAction schema:\n"
            + schema,
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "mode": "ask",
                    "research_goal": goal.strip(),
                    "memory_available": memory_available,
                    "parent_result_ids": [],
                    "ask_overview_intent": {"protocol": ASK_OVERVIEW_PROTOCOL},
                }
            ),
        },
    ]


def normalise_ask_overview_conclusion(action) -> tuple[object, AskOverviewBinding | None]:
    if not isinstance(action, AskOverviewConcludeAction):
        return action, None
    if isinstance(action.conclusion, InsufficientFinding):
        return ConcludeAction(kind="conclude", conclusion=action.conclusion), None
    return (
        ConcludeAction(kind="conclude", conclusion=action.conclusion.to_answer()),
        action.conclusion.overview_binding(),
    )


def ask_overview_validation_error(exc: ValidationError) -> str:
    """Expose only reviewed static codes, never model text or Pydantic inputs."""
    allowed = {
        "ask_claim_ids_not_unique",
        "ask_overview_duplicate_claim",
        "ask_overview_unknown_claim",
        "ask_overview_too_long",
    }
    for error in exc.errors(include_url=False, include_input=False):
        reason = error.get("ctx", {}).get("error")
        if isinstance(reason, ValueError) and str(reason) in allowed:
            return str(reason)
    return "invalid_action_schema"


def ask_overview_protocol_payload() -> dict:
    """Actual static variant inputs for the separately reviewed native profile."""
    return {
        "controller_protocol": ASK_OVERVIEW_CONTROLLER_PROTOCOL,
        "intent_protocol": ASK_OVERVIEW_PROTOCOL,
        "intent_schema": AskOverviewIntent.model_json_schema(),
        "overview_binding_protocol": ASK_OVERVIEW_PROTOCOL,
        "overview_binding_schema": AskOverviewBinding.model_json_schema(),
        "system_prompt": ASK_OVERVIEW_SYSTEM_PROMPT,
        "mode_instructions": MODE_INSTRUCTIONS["ask"],
        "action_schema": ASK_OVERVIEW_ACTION_ADAPTER.json_schema(),
    }
