# The rigour bar

Built 1 November 2026 (owner's idea). The visitor chooses how rigorous the
literature must be for an investigation. One control under the composer, the
**Evidence bar**, with one plain sentence per level:

| Level | Sources used | Applied as |
|---|---|---|
| Frontier | Everything, including brand-new preprints | No filter; OpenAlex and arXiv |
| Standard (default) | Never retracted; preprints included and labelled | OpenAlex `is_retracted:false`; arXiv included |
| Peer reviewed | Only work published in journals and conference proceedings | `is_retracted:false,primary_location.source.type:journal\|conference`; arXiv left out |
| Established | Peer reviewed and cited at least ten times | as Peer reviewed, plus `cited_by_count:>9` |

The trade-off is said plainly: a higher bar means more vetted evidence but fewer
brand-new ideas, because frontier work often starts as a preprint.

## How it works

- The level is recorded with each investigation in `research_run_rigour`
  (schema 0033), written in the same transaction that creates the run. A run
  without a record uses the standard bar.
- The research worker reads the level and builds the search tools with fixed
  filters from `polymath.rigour`; visitor text never becomes a filter.
- The bar narrows which sources can be found. It does not enter the model's
  instructions, so run inputs, protocols, replay and every integrity hash stay
  exactly as before.
- Each investigation shows "Evidence bar: …" when a bar was chosen. Solve,
  Frontier and Converge stages use the visitor's current bar too.

## What is verified

- Mock-HTTP tests check the exact OpenAlex filter for each level, that arXiv is
  left out above Standard, and that the default is Standard.
- An HTTP test checks that an investigation records its bar and reports it.
- Web tests check the control, the request (sent only when it is not the
  default, so earlier requests are unchanged) and reply parsing.
- The filter syntax follows OpenAlex's documentation. It could not be called
  live from the build sandbox, whose shared free allowance was used up; check it
  on the server with the configured OpenAlex key.

Next, when time allows: a per-source badge (Peer reviewed · Preprint ·
Retracted, citations), from a separate read-only metadata lookup.
