# Experience plan: making Polymath beautiful end to end

Owner direction, 29 October 2026: make every part of the experience visually
impressive and a joy to use. Waiting time should become something to watch,
showing what the AI is actually doing in a beautiful, information-art style.
People should also be able to leave and be told when the work is finished.

## Principles

1. **Show the real thing.** Every mark in a visualisation comes from a recorded
   research event or saved result. No decorative fake activity, no invented
   progress percentages. Honesty is part of the aesthetic.
2. **Waiting is watching.** Any state that lasts more than a moment gets motion
   and meaning: what is happening, what just happened, how long it has taken.
3. **One calm visual language.** Paper and ink, a deep research green, and a
   small set of signal colours, each with one meaning: search (sky), reading
   (sage), memory (violet), hypothesis (gold), objection or setback (coral),
   finding (green). The same colour means the same thing everywhere.
4. **Motion with purpose.** Things arrive, settle and connect. Durations are
   short (150–600 ms), easing is soft, and `prefers-reduced-motion` gets a
   still, equally informative version.
5. **Accessible by construction.** WCAG AA contrast (audited with axe), keyboard
   access, and a text alternative for every visual (the activity timeline stays).
6. **Respect attention.** Leave and be notified; come back to a clear summary.

## Signals the visualisation can use

From the persisted event stream (already delivered over SSE):

| Event kind | Meaning | Visual |
|---|---|---|
| `queued`, `running` | Waiting for a slot / started | Core ignites |
| `external_started` → `external_observed` | A Meta model call is in flight → returned | Core breathes; live "Muse Spark is thinking · 12 s" |
| `search_completed` (purpose, query, count) | Literature searched | Ripple from the core; found papers settle on the literature ring |
| `source_read`, `full_text_window_read`, `stage_source_read` | A paper or excerpt read | A paper on the ring brightens and is threaded to the core |
| `full_text_opened` | A paper's section index opened | Ring marker opens |
| `memory_searched`, `memory_finding_read`, `memory_source_read` | Revisiting earlier findings | Violet inner ring |
| `fields_selected` | Surprise chose fields | Field constellations appear around the core |
| `research_note` (proposed / revised / discarded) | A hypothesis formed or changed | Gold star; discarded ones dim and drift |
| `tool_failed`, `model_failed`, `action_rejected` | Setback | Brief coral flicker |
| `stage_committed`, `explore_ranked`, `concluded` | A finding saved / ranked / concluded | A bridge arc forms; the core settles |

Counts shown: papers found, papers read, searches, hypotheses, model calls and
elapsed time, all derived from events.

## Surfaces and states

| Surface | Today | Upgrade |
|---|---|---|
| App boot | Plain text "Opening…" | Branded loading mark that draws itself |
| Home / research desk | Elegant but static | Living constellation hero art; refined mode switch; tactile chips |
| Submitting / queued | Text only | Core ignition and a queued state with what happens next |
| **Running investigation** | Text timeline | **The Observatory**: live visualisation, a narration line ("Reading *Percolation models…*"), live stats and thinking timer, with the timeline beside it |
| Leaving | Nothing | **Notify me when it's done** (browser notification and tab title status), finish toast and summary |
| Bridge result | Table and text | Bridge diagram: two fields joined by mapping threads; objection and verdict as a visual pair |
| Ask result | Labelled claims | Claim spectrum (Known / Postulated / Speculative) with colour-coded claim cards |
| Saved research list | Text list | Cards with mode glyph, status and time |
| Knowledge map | Functional SVG | Softer constellation styling consistent with the Observatory |
| Public library and finding | Clean text | Matching visual language and finding cards |
| Errors / empty / loading | Mostly plain text | Friendly illustrated states with a clear next step |

## Delivery phases

1. Design tokens (colour, type, motion) and the Observatory with narration,
   stats and thinking timer.
2. Notify-when-done: browser notification permission, tab title status, finish
   toast; honest copy that the tab must stay open in the background.
3. Waiting, loading, empty and error states, including the boot mark.
4. Result visualisations: bridge diagram and claim spectrum.
5. Home hero art, history cards, map and public page styling, micro-interactions.
6. Full accessibility audit, mobile pass, screenshots and tests on every phase.

Each phase keeps the existing tests green, adds tests for new behaviour and is
checked with the axe audit at desktop and phone widths before it is pushed.

## Progress (29 October 2026)

| Phase | Status |
|---|---|
| 1. Tokens and the Observatory | Done: live star-chart map from recorded events, narration, stats, thinking timer |
| 2. Notify when done | Done: browser notification while the tab is in the background, tab title status, completion toast |
| 3. Waiting, loading and empty states | Done: boot mark, living desk illustration, Frontier and library empty states |
| 4. Result visualisations | Done: bridge diagram and claim spectrum |
| 5. Home, history, map and public pages | Done: investigation cards, knowledge map palette, library cards; Frontier Map in the same language |
| 6. Accessibility and mobile | Done for audited views: axe WCAG 2.x A/AA reports zero violations at 1280 and 390 px for new and returning visitors |

All checks used synthetic events and findings. The Observatory's thinking
timer only appears for model calls this page saw start. Remaining polish
candidates: Formalise/Write, discovery-ladder and Explore-collection panels,
and the operator budget desk.
