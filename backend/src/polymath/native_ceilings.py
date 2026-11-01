"""The widest per-task limits any native profile or controller may carry.

The readiness contracts bound what an operator may propose, and the research,
artifact and assessment controllers bound what a run may execute. Both must
agree: the first live visitor investigation under a 48-call profile was
interrupted before its first model call because the research controller still
stopped at the legacy ceiling of 20. Derivations of at most LEGACY_CALL_CEILING
calls stay byte-identical to the earlier releases.
"""

MAX_MODEL_CALLS = 80
MAX_CONTEXT_CHARACTERS = 240_000
LEGACY_CALL_CEILING = 20
