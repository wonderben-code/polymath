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

## Official participant guide and submission form (read 29 Oct 2026, signed in)

**Problem statements (pick one in the submission form):**
- **Build for Your World:** bring Meta's models into your domain and solve a problem you know firsthand.
- **Push the Frontier:** use Meta's models on challenges in software, robotics, manufacturing, energy, materials and beyond; "accelerate a scientific experiment". *This is the natural fit for Polymath. The owner makes the final choice.*

**Allowed Meta models:** Muse Spark series, Muse Wonder, Muse Image, Muse Voice Transcribe and SAM 3.1 via the Meta Model API; open-weights Muse Glimmer and Muse Spark Open; the Muse Code coding agent.

**Rules:**
- **Repositories must be public.**
- **New work only:** everything shown must be built during the event (building period 27 Oct – 4 Nov), and judges must be able to clearly identify what was built during the hackathon, or the project is disqualified at the judges' discretion.
- Anti-projects to avoid looking like: **basic RAG applications**, simple image analysers, medical advice, education chatbots, job screeners, nutrition/sports coaches, personality analysers, political/war topics. Polymath must clearly come across as an agentic discovery system, not a RAG search box.

**Submission form fields (Cerebral Valley "Submit Project" dialog):**
1. Team name
2. Team members (Ekram Alam / jackson2045 pre-filled)
3. Track: Indie Developer / Startups
4. Problem statement: Build for Your World / Push the Frontier
5. X post link (optional, for Most Viral)
6. Project description
7. Public GitHub repository URL
8. **3-minute demo video** URL (YouTube), showing only features built during the hackathon. It's one of the primary judging artefacts, along with the code and the **live product**
9. How you used Meta products
10. Why Meta's models are important to your solution
11. Feedback on Meta products
12. Feedback on partner technologies (Fireworks, Modal, Baseten)
13. Consent checkbox (Meta/CV may use, publish and share)

**Deadline:** Wed 4 Nov, 9:00 PM ET (= Thu 5 Nov, 09:00 Bangkok). Late submissions are not considered. Tue 3 Nov is the last full day for submission questions.

**Judging:** Stage 1 is pass/fail (fits a theme and genuinely uses Meta models). Stage 2 scores 0–10 on the rubric above. Stage 3: top 5 per track, with finalists' videos watched live on Mon 9 Nov.

**Bonus prizes:** Most Viral on X ($20k, engagement until 8 Nov EOD), Best Build on Meta Open Weight Models ($10k), Community Vote ($10k, winner 16 Nov), **Best Agentic Use-Case ($5k, a strong fit)**, Best Creative Build with Muse Image/Wonder ($5k).

**Partner credits (optional, not guaranteed):** Baseten $100 + 2M TPM for open-weights (use model id `meta-models/muse-spark-open-1026`); Modal $100 (first 1,000); Fireworks $100 (shortlisted teams).

**Sessions (ET):** Agents 101 (Tue 27 Oct), **Agents 102: Best Practices for Verifiable Agents** (Wed 28 Oct, relevant to evidence integrity), Fireworks open-models session (Fri 30 Oct). Discord #office-hours runs 10–11 AM ET on weekdays.

**Billing note:** adding a card triggers a temporary pre-authorisation charge. The "Spend Limit" on the Billing page is described as a *notification*, so don't assume it's a hard cap.

## Still to confirm
- Model API supports `tools`? `____` · `response_format`? `____` · images? `____` · streaming? `____`
- Current Muse Spark model ID available to the account (third-party guides mention `muse-spark-1.3`): `____`

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
