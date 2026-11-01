"""JSON schemas of the static action adapters and contract models, once per process.

The controller fingerprints, minimum prompt lengths and protocol payloads all
embed these schemas. Generating one takes tens of milliseconds, and a visitor
access read regenerated them over a hundred times, taking up to eight seconds,
longer than the browser waits. The adapters and models never change while the
process runs, so each schema is generated once per object and returned as a
fresh copy; a test that swaps in a different adapter object gets its own entry.
"""

from __future__ import annotations

import copy

_SCHEMAS: dict[int, tuple[object, dict]] = {}


def json_schema(target) -> dict:
    """`target.json_schema()` for a TypeAdapter, `model_json_schema()` for a model class."""
    entry = _SCHEMAS.get(id(target))
    if entry is None or entry[0] is not target:
        schema = target.model_json_schema() if isinstance(target, type) else target.json_schema()
        _SCHEMAS[id(target)] = (target, schema)
        entry = _SCHEMAS[id(target)]
    return copy.deepcopy(entry[1])
