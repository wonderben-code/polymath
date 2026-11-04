# Improvements plan, 1 November 2026

The owner's five ideas, each thought through and pushed further, then put in
order. Nothing here is built yet; this is the plan of record.

Deadline: Wed 4 Nov, 21:00 Bangkok (submit early). Live at https://askpolymath.com.

## The shape of the next three days

The biggest unknown is live Meta research, the heart of the demo. It needs the
owner's go-ahead for paid testing. Everything below either runs alongside it
or waits for its first real results.

| When | What | Why this order |
|---|---|---|
| Now, in parallel with live testing | 2 Story, 3 Badges, 5 Sources | Biggest first-impression gain for judges, low risk, no paid calls |
| Once the owner picks a direction | 1 Colour | A taste decision: the owner chooses from three shown directions |
| After the first real results | 4 Rigour | The largest build; real metadata shows what each level can truly promise |
| Tue–Wed | Demo video, submission | Record once the product is final |

## 1. Colour: an identity of its own

**Idea:** stop looking like the owner's other projects.

**10x:** give Polymath a visual identity that *means* something. Polymath takes
many fields and finds one shared structure: many colours resolving into one
light. Three directions to show the owner as live swatches on the real home
page, before anything is built:

- **Prism:** warm white paper, near-black ink, and a thin spectrum (several
  field colours) that appears only where fields connect. The colour *is* the
  connection.
- **Observatory:** deep night-blue ground, starlight cream text, one bright
  signal colour for live discovery.
- **Laboratory:** crisp white, graphite, and one confident accent (for example
  a vivid cobalt or vermilion), like a scientific instrument.

The calm principles (space, one idea per screen, quiet motion) stay. Only the
palette, accent behaviour and field colours change, all through the existing
design tokens, so it is one coherent change. Every combination is checked
against WCAG AA contrast.

## 2. A story that does not undersell

**Idea:** say clearly what Polymath is, the problem and the impact.

**10x:** a home page that lands the whole story in five seconds, then rewards
reading:

1. **The problem**, in one line: science is split into silos, and far more is
   published than anyone can read, so answers already found in one field go
   unseen in another.
2. **The shift:** AI can now read across every field at once.
3. **What Polymath does:** it finds where distant fields describe the same
   structure, *tries to break each connection*, builds deeper connections from
   the ones that hold, and takes on open problems (Frontier).
4. **The impact:** discoveries hiding *between* fields; open problems
   approached with tools from other disciplines; gaps named and attacked.
   Every claim is tied to an exact passage.

Every number used (papers per year, the size of the catalogue Polymath
searches) is checked against a cited source before it appears. Historical
examples stay (heat flow and option pricing, and others) because they *show*
the idea. The About page becomes the full version: the problem, how the agent
reasons, the honesty rules, and what it has found.

## 3. "Built for the Meta hackathon"

**Idea:** Meta and Cerebral Valley logos.

**10x and done properly:** a quiet, honest credit line, "Built for the Meta
Global AI Developer Hackathon with Cerebral Valley · Intelligence by Meta Muse
Spark", in the footer and the About page. Plus a visible "Powered by Muse
Spark" moment where the agent is visibly thinking, which is the strongest proof
for the 25% "Use of Meta AI tooling" score.

Logos: Meta's brand rules restrict using its logo in ways that could suggest
endorsement. The plan is to use an official hackathon or "Built with" badge if
the organisers provide one, and to ask in the hackathon Discord whether the
Meta and Cerebral Valley logos may be shown. Text credit goes live straight
away; logos follow once allowed.

## 4. A rigour bar

**Idea:** the visitor chooses how rigorous the literature must be.

**10x:** rigour becomes visible and controllable at every step, not just a
filter. Signals Polymath can genuinely check for each paper, from OpenAlex's
open metadata:

- **Retracted or not.** Retractions are flagged in OpenAlex.
- **Peer reviewed or preprint.** Is it published in a journal or conference, or
  only a preprint? (arXiv papers are preprints.)
- **Citation standing.** Citation counts, normalised for field and year where
  available.
- **Document type.** Review, article, preprint.

Four levels, chosen in the composer with one control and one plain sentence
each:

1. **Frontier.** Everything, including brand-new preprints. Fastest to the new
   things, least vetted.
2. **Standard** (default). Never retracted; preprints allowed but labelled.
3. **Peer reviewed.** Only published, peer-reviewed work.
4. **Established.** Peer reviewed and well cited for its field.

Beyond the filter:
- Every source in a finding shows its rigour badge (Peer reviewed · Preprint ·
  Retracted, and citations).
- The agent is told the bar and weighs evidence strength in its own verdict.
- The finding states the bar it was made under.
- The trade-off is said honestly: a higher bar means more established evidence
  but fewer brand-new connections, because frontier work often starts as
  preprints.

Built carefully: the level is recorded with each investigation (an optional
field, so existing records and their integrity checks stay exactly as they
are). Search filters are applied at the source (OpenAlex filters; arXiv is
treated as preprints). Fully tested before release. Built after the first live
results, so its promises match the real metadata.

## 5. Where the knowledge comes from

**Idea:** logos of arXiv, Zenodo (CERN) and the other sources, for credibility.

**10x:** credibility from *truth*, not decoration. A quiet "Reads from" strip
naming the sources Polymath actually uses:

- **arXiv** for preprints and full text
- **OpenAlex**, the open catalogue of the world's research

And a separate, honest line: **"Publishes to Zenodo (operated by CERN)"**,
because Polymath publishes there rather than reading from it. Each finding then
says exactly what it read: "This investigation read 14 papers from arXiv and
OpenAlex". That is credibility no logo can give.

Logos are used only within each organisation's published guidelines, and never
in a way that suggests endorsement. Otherwise their names are set in clean
type.

## 6. Idea to explore next (owner, 1 Nov): "Solve", lock and key

Not on the current build list; to be explored when the time is right.

The biggest value is not only finding connections but **solving problems across
fields**. The visitor states a problem (the lock). Polymath searches across
fields for an existing answer that fits (the key). If none fits fully, it
names the gap or hole in the best partial answers, then looks across fields,
and combinations of fields, to assemble a key that does fit, with every piece
tied to its evidence.

Frontier already surveys a problem, names gaps and looks for solutions in other
fields, so a Solve mode can build on that foundation: problem → candidate keys
→ the fit test → the gap → combined keys → evidence for each piece.

Key finder and key maker (owner, 1 Nov): if a key exists, find it. If none
fits, find the close fits (one or several), reason precisely about where each
falls short, and forge a new key from the literature by combining the
key-adjacent shapes, with each piece tied to its evidence and the fit tested.

Two ways in (owner, 1 Nov):
- **Solve on its own:** "Polymath as a problem solver for you". State the
  problem and get keys, gaps and combined keys.
- **Inside Frontier:** any Frontier step that needs an answer can call Solve,
  in addition to Frontier's existing survey, gap and sub-step structure.

## Questions for the owner

1. Which colour direction (shown as live swatches first)?
2. Problem statement for submission: "Push the Frontier" (recommended) or
   "Build for Your World"?
3. Go-ahead for paid testing, in your own words, with the cap (USD 120
   suggested).
