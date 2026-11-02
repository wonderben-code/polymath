"""Shared tolerance for unknown ledger holds.

An unknown hold (a timed-out or refused model call whose charge a person has not
yet settled) stays counted at its full reserved maximum against every cap, so a
single one does not stop everyone else's funded work. A run of them means the
provider is misbehaving: admission and job claims then close until a person has
settled them. Activation still refuses while any unknown hold stands.
"""

UNKNOWN_HOLD_ADMISSION_LIMIT = 3
