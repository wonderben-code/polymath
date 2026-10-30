# Design: a calm, clear Polymath

Owner direction, 29 October 2026: the experience should feel like world-class
product design. Simple, easy and clean; calm in the way great Japanese
craftsmanship feels calm; information clear; extremely intuitive. This document
is the bar every screen is judged against.

## Principles

| Principle | Meaning for Polymath |
|---|---|
| **Ma (間) — space** | Generous whitespace. One column for reading. Nothing competes with the question or the answer. |
| **Kanso (簡素) — simplicity** | Remove until only the essential remains. One primary action per view. |
| **Shibui (渋い) — quiet beauty** | Subtle colour, soft motion, no shouting. Beauty comes from proportion and restraint. |
| **Clarity** | Plain words. Every element answers "what is this and what can I do?" at a glance. |
| **Deference** | The research is the hero; chrome recedes. Controls appear when they are useful. |
| **Depth** | Progressive disclosure: the essential sentence first, evidence and technical detail one tap away, never hidden. |
| **Honesty, calmly** | Every integrity statement stays reachable ("How to read this", "Details"), but is not shouted on every panel. |

## Audit of the current experience

1. One long page holds about nine sections (desk, sidebar status, saved list, search, relationship map, literature watches, Frontier, approach, intelligence). A first visit has no obvious starting point.
2. Operator vocabulary reaches visitors: "native authority", "activation SHA-256", "ledger", "bounded provider windows", "unassessed source version".
3. Caveats, notices and fine print appear everywhere at once, mostly in 10–12px grey text.
4. Many bordered boxes and buttons of similar weight; weak hierarchy.
5. Waiting state: the Observatory is strong, but it sits above a long technical timeline.

## Information architecture

A quiet top navigation with four places, each with one job:

| Place | Path | One job |
|---|---|---|
| **Research** | `/` | Ask Polymath something; watch it work; read the result |
| **Your library** | `/your-research` | Everything you have saved: investigations, map, literature watches |
| **Frontier** | `/frontier` | Experimental programmes for unsolved problems |
| **About** | `/about` | How Polymath works, how to read its labels, and its use of Meta's model |

The public shared library stays at `/library`.

**Research (`/`)**: a centred composer (mode choice, one large text field, one
primary button), a short line of examples, and a quiet status line. Below it, a
compact "Recent" list. Selecting or starting an investigation brings the focus
view directly under the composer: the Observatory first, a one-line narration,
the result summary, then evidence and the step-by-step record behind
"Show details".

## Visual system

- **Type:** body 16px Inter/system at 1.65 line height; serif (Iowan Old Style,
  Palatino, Georgia) for headings and for the research itself (statements,
  quotes). Scale: 13 / 16 / 20 / 28 / 44. No visitor-facing text below 12px.
- **Space:** 8-point scale (8, 16, 24, 40, 64, 96). Reading column max 760px.
- **Colour:** paper `#f6f4ee`, ink `#1f332c`, one accent green `#266b58`; signal
  colours only inside visualisations (search blue, reading green, hypothesis
  gold, memory violet, setback coral). Borders are hairlines `#e2e4da`, used sparingly.
- **Surfaces:** fewer boxes. Prefer space and a single hairline over nested borders.
  Radius 12px for cards, 999px for chips.
- **Motion:** 150–400 ms, ease-out, only to show arrival or change; everything
  still under `prefers-reduced-motion`.
- **Buttons:** one filled primary per view; secondary actions are quiet text or
  outline buttons.

## Copy rules

1. Say what it is and what to do, in everyday words.
2. One sentence of caveat at most where the decision is made; the rest goes in
   "How to read this" or "Details".
3. Never show internal identifiers (hashes, activation IDs, ledger terms) in the
   visitor experience; keep them in operator pages and Details.
4. Labels are nouns people use: "Your library", "Saved investigations", "Paused".

## What changed

- **Places:** Research, Your library, Frontier and About replace the long single page. The public library and operator desk sit quietly in the footer.
- **First five seconds:** the home page says what Polymath is before anything else. A fixed headline ("Find the hidden connections between fields of science."), one plain sentence, and a quiet arc between two fields that cycles through long-established textbook links (heat flow and option pricing, ant colonies and internet routing, epidemics and rumours). The illustration is labelled as examples, never as Polymath findings, and stays still under reduced motion.
- **Research:** a centred composer with one primary button and one availability line. Opening or starting an investigation glides to it.
- **Focus view:** a finished investigation leads with its result. The Observatory and the step-by-step record fold into "How Polymath worked". A live run leads with the Observatory and offers "Notify me when it's done".
- **Calm honesty:** the ordinary current-finding status is one quiet line, and the claim-label guide folds away. The funded-access details fold away too. Anything that needs attention (a superseded or unsupported finding, a paused service) keeps its full notice.
- **Frontier:** Experimental is stated once at the top and once beside the result note. Map edges start after each label, the legend shows only the statuses present, and the list view folds away.

## Verification

Every view: web tests green, axe WCAG 2.x A/AA clean at 1280px and 390px,
screenshots reviewed against the principles above. Backend behaviour unchanged.

29 October 2026: 934 web tests pass; zero axe violations across 18 views
(home, bridge result, answer result, live run, library, Frontier with an open
programme and its list, About, public library and budget desk, each at 1280
and 390 px).

## Calm pass 2 (30 October 2026)

**Audit:**
- The public library and operator desk still used the old shell: "Research
  desk", a different header and footer, square boxes and a wall of notes. To a
  visitor they felt like a different website.
- The Share and Write panels, the Surprise and Explore collection views, and
  the publishing screens kept boxy buttons and small grey text.

**Changes:**
- **One site chrome:** `SiteHeader` and `SiteFooter` are shared by every page,
  so the public library, shared findings and the operator desk feel like the
  same place.
- **Public library:** a centred column, a shorter introduction, a one-line
  note instead of a boxed notice, a rounded search field and a quiet reload link.
- **Operator desk:** a plain-words introduction ("Nothing here spends until you
  arm it"), with the full safety notes folded behind *How this desk keeps
  spending safe*. The selected workspace is clearly marked, in 880 px cards.
- **The remaining panels share one language:** pill buttons, soft notices
  without hard borders, rounded cards and a 13–14 px floor for supporting text.
- **Wording:** "research desk" is gone from visitor-facing text.

**Verification:** 948 web tests pass; axe finds zero violations on 20 views at
1280 and 390 px; screenshots were reviewed. The Surprise ladder and Explore
collection views were restyled through shared rules but not seeded visually.
