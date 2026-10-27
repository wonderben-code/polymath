# Meta Global AI Developer Hackathon 2026: Notes

Organisers: Meta and Cerebral Valley. Virtual. Indie Developer track (individuals or teams of up to 4). Entrant: Ekram Alam (accepted 20 Oct 2026).

Implementation and verification status: [PROGRESS.md](PROGRESS.md). The full-product plan is in [ROADMAP.md](ROADMAP.md) and [BUILD_PLAN.md](BUILD_PLAN.md). Unchecked account and rules items below remain unconfirmed; planning does not mark them complete.

## Dates
| Bangkok (UTC+7) | ET | Event |
|---|---|---|
| Mon 26 Oct, 20:00 | Mon 26 Oct, 9:00 AM | Kickoff |
| 26 Oct – 4 Nov | | Workshops and Discord office hours |
| **Thu 5 Nov, 09:00** | **Wed 4 Nov, 9:00 PM** | **Submission deadline** (Cerebral Valley portal) |
| 5 – 9 Nov | 4 – 8 Nov | Judging |
| Mon 9 / Tue 10 Nov | Mon 9 Nov | Closing ceremony and winners |

## Judging criteria
| Criterion | Weight | What it means for Polymath |
|---|---|---|
| Impact | 30% | Speeds up cross-disciplinary discovery for every researcher. Open tool, open findings |
| Use of Meta AI tooling | 25% | Go well beyond a basic API call. *How* gets decided in Milestone 1 |
| Technical depth & execution | 20% | Sound, refined engineering, and connections that are genuinely true, not just plausible |
| Demo | 25% | Live URL where you can watch the agent think, plus a video of 3 minutes or less |

## Prizes (Indie track)
$150k / $100k / $75k / $50k / $25k, plus up to $50k in bonus prizes (community vote, most viral, …).

## What participants get
$150 in Meta Model API credits (claim via form), **Muse Code** (Meta's agentic coding harness), and workshops and office hours with Meta engineers.

## Setup checklist
- [ ] Discord joined
- [ ] Credits form submitted
- [ ] dev.meta.ai account + API key
- [ ] Card added + **$150 spend limit** set
- [ ] Test call succeeded from the dev machine
- [ ] Problem statement chosen: `________________`
- [ ] Official Rules read (IP clause)

## To confirm from the details page / Official Rules (fill in)
- Submission fields: `________________`
- Video length limit: `________________`
- Live URL required? `________________`
- Public repo required? `________________`
- Model API supports `tools`? `____` · `response_format`? `____` · images? `____` · streaming? `____`
- What SAM 3 and Glimmer offer, and whether either is useful here: `________________`

## Meta Model API quick reference

**Unverified setup notes:** the endpoint, exact model IDs, capabilities, and prices below must be checked against current official documentation and actual account access in build task A2. No successful model call has been recorded. Never treat these notes as a verified API contract or enable unbounded spending from them.

- Base URL: `https://api.meta.ai/v1` (OpenAI-compatible)
- Auth: `Authorization: Bearer $MODEL_API_KEY`
- Model: `muse-spark-1.1` (check whether `muse-spark-1.2` is available)
- Reported price: $1.25 / $4.25 per million input/output tokens
- At launch the hosted API was a US public preview. If access from Thailand fails, ask in Discord. The fallback is open-weights Meta models.

```bash
curl https://api.meta.ai/v1/chat/completions \
  -H "Authorization: Bearer $MODEL_API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"muse-spark-1.1","messages":[{"role":"user","content":"Say hello from Polymath"}]}'
```

## IP and terms (plain English, not legal advice)
- **We keep ownership** of the submission.
- Meta and Cerebral Valley get a **perpetual, irrevocable, worldwide, royalty-free, non-exclusive licence** to use, modify and publish it for any purpose, with no attribution owed.
- Anything shared during the event (code, chat, ideas) is **non-confidential**.
- That's fine for Polymath: it's an open tool we intend to release anyway.
- **Never** put the author's unpublished theoretical work or any unrelated venture into this repo, the Discord, the demo or the write-up.
- Prior art: Bitcoin-timestamped commits (this repo), a public repo, the demo video and a Zenodo DOI at submission.
