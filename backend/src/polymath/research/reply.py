"""Find the one JSON action object inside a model reply.

Polymath asks for a bare JSON action, but a frontier model sometimes adds a
short sentence before it ("Hypothesis: … — gathering evidence next.{...}") or
wraps it in a Markdown code fence. The first live Muse Spark run did exactly
that, and three such replies in a row ended the investigation. This takes the
first balanced top-level JSON object out of the reply and leaves everything
else to the strict action schema. The raw reply is still what gets recorded
and sent back to the model, so request hashes and replay are unchanged. When
no balanced object exists, the reply is returned untouched so the usual schema
error follows.
"""

from __future__ import annotations

import re

_FENCE = re.compile(r"^\s*```(?:json|JSON)?\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL)


def action_json(content: str) -> str:
    """The first balanced `{…}` in the reply, or the reply itself when there is none."""
    if not isinstance(content, str):
        return content
    text = content.strip()
    fenced = _FENCE.match(text)
    if fenced is not None:
        text = fenced.group(1).strip()
    start = text.find("{")
    if start < 0:
        return content
    depth, in_string, escaped = 0, False, False
    for index in range(start, len(text)):
        character = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return content
