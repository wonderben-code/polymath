# Polymath as a public tool: the agreed plan

Owner decisions, 30 October 2026. This file is the source of truth if
conversation context is lost. Read it with [PROGRESS.md](PROGRESS.md).

## The vision

Polymath is a free public tool, like a research assistant anyone can open.
Judges and visitors see every option:

- **Sign in, or continue as a guest.** Both work fully; guests lose nothing.
- **Use it free (limited), or run your own copy** for heavier use; "Connect your Meta account" appears only as a labelled vision.
- **Connect your own Zenodo** to publish discoveries under your own name.
  Nobody can ever publish to the owner's Zenodo account except the owner.

## Business model (owner's strategy)

- **During the hackathon and judging:** free use is on, funded by the owner's
  Meta credits within per-visitor caps, and shown as limited.
- **After judging:** free use is reduced to a few runs, or switched off. Heavy
  users run their own copy, or connect their Meta account if Meta offers it.
  Polymath stays a free tool, not a billing business.

## Decision on own keys (30 October 2026): not built

Meta's Terms of Service (https://dev.meta.ai/legal/terms-of-service, read
30 October 2026):

- Section 3.1: "You may not share, sell, or transfer your Meta Model API key
  with any third party without our prior written permission."
- Section 10.1 forbids using the Services "on behalf of any third party", or
  providing access "through any model aggregator, API gateway, proxy, or
  similar offering … except as expressly authorized by Meta in writing."

A visitor giving Polymath their key, in any form, would breach these terms,
so the own-key option is **removed**. The owner's free mode is compliant:
Section 1 allows products in which "End Users … interface with the Services".

Ways forward:

1. Ask Meta through the hackathon (organisers, or Discord #office-hours) whether
   an approved "connect your Meta account" option or written permission
   exists. Build only what Meta approves.
2. After judging: a few free runs per visitor per day on the owner's key, with
   firm caps.
3. Later: a sponsor or partner path.

## What gets built, in order

1. **Ways to use Polymath** (before submission) · **built 30 Oct**
   - Free, right here: "a few investigations a day", showing how many are left
     today.
   - Run your own Polymath: an open-source guide; the visitor's Meta key never
     leaves their own server. Fully compliant.
   - Connect your Meta account: a clearly labelled "Coming soon · proposed Meta
     integration" card explaining how a Meta sign-in would let people use their
     own allowance without Polymath ever seeing, storing or sharing a key. It is
     not a working-looking button, because Meta does not offer this publicly.
   - Where: an About page section, plus a quiet line under the composer.

2. **Free-use switch** (before submission) · **built 30 Oct** (`PUBLIC_MAX_DELEGATION_SECONDS`)
   - Already exists: an owner-funded public delegation with per-visitor caps,
     an exact deadline and an explicit Stop (docs/PUBLIC_RESEARCH_ACCESS.md).
   - Change: the maximum delegation lifetime (currently 24 hours) becomes a
     server setting (for example up to 14 days), so one approval can cover the
     judging period. It keeps the exact deadline, the Stop and the caps.
   - After judging: stop the delegation, or issue one with "a few runs per
     visitor".

3. **Connect Zenodo for every visitor** (before submission; live test after
   deployment) · **built 30 Oct**, see [CONNECT_ZENODO.md](CONNECT_ZENODO.md):
   connect/disconnect, and publish any written finding with an exact review
   (practice or permanent). Still to build: the publishing levels/autopilot
   for visitors' own connections (today they publish one finding at a time).
   - A *Connect Zenodo* button uses Zenodo's OAuth sign-in (sandbox and
     production) with the `deposit:write` and `deposit:actions` scopes.
   - Tokens are stored encrypted per visitor and deleted on *Disconnect*.
   - Each visitor uses the four publishing levels (docs/PUBLISHING.md) with
     their own connection. Records are published under their own name.
   - The owner's publisher path stays separate and unchanged.
   - Needs the owner to register a free Polymath OAuth app on Zenodo sandbox
     and on Zenodo, using the live site's callback URL.

4. **Accounts: sign in or continue as a guest** (before submission if time
   allows; otherwise straight after)
   - Sign in with ORCID (researchers; it also fills their name and ORCID on
     papers), GitHub or Google.
   - Signing in keeps work forever and across devices; guest work is carried
     into the account.
   - Zenodo connections follow the account.
   - Needs the owner to register free OAuth apps with those providers after
     deployment.

## Already built (for reference)

- Research: Explore, Ask, Surprise me and Converge (experimental).
- Frontier (experimental) and Go deeper.
- Publishing levels and the publishing autopilot (Phase 1), for the owner.
- The calm design throughout (docs/DESIGN.md).
- Deployment kit (docs/DEPLOYMENT.md) and key-day kit (docs/KEY_DAY.md).

## Owner actions still needed

1. Meta key and credits, with a $150 spend limit, and permission to start paid
   testing.
2. A host: one $12-a-month server is recommended; Netlify alone cannot run the
   backend.
3. After deployment: free OAuth app registrations (Zenodo sandbox and
   production; ORCID, GitHub, Google).
4. Demo video (3 minutes) and the submission form, before Wed 4 Nov, 21:00
   Bangkok.
