# Polymath as a public tool: the agreed plan

Owner decisions, 30 October 2026. This file is the source of truth if
conversation context is lost. Read it with [PROGRESS.md](PROGRESS.md).

## The vision

Polymath is a free public tool, like a research assistant anyone can open.
Judges and visitors see every option:

- **Sign in, or continue as a guest.** Both work fully; guests lose nothing.
- **Use it free, or connect your own Meta API key** for heavier use.
- **Connect your own Zenodo** to publish discoveries under your own name.
  Nobody can ever publish to the owner's Zenodo account except the owner.

## Business model (owner's strategy)

- **During the hackathon and judging:** free use is on, funded by the owner's
  Meta credits within per-visitor caps. The own-key option is visible and
  working.
- **After judging:** free use is switched off, or reduced to a few free runs.
  People bring their own Meta key, so the relationship is between each user and
  Meta. Polymath stays a free tool, not a business that pays for usage or
  manages billing.

## Open decision (30 October 2026)

The owner asked whether asking visitors for their Meta key is wise ("never
share your key"). Proposed instead: **Run it yourself.** Polymath is free to
use on the site, and for unlimited use people run their own copy with their
own key, which never leaves their machine. This needs a short guide and a
button on the site. It awaits the owner's answer. Until then, item 1 below is
on hold; items 2 to 4 are unaffected.

## What gets built, in order

1. **Bring your own Meta key** (before submission)
   - A visitor enters their key once over HTTPS. It is stored encrypted with a
     server-only key, never returned to the browser, never logged, and removed
     at once by *Forget my key*.
   - The key is checked before first use with a free, no-completion request.
   - The visitor sets their **own spending ceiling**; each run on their key is
     reserved and settled against it, just like the owner's ledger.
   - Every run says who pays: "Running on your Meta key" or "Running on
     Polymath's free allowance".
   - Technical shape:
     - A third research audience, `own_key`, beside `operator_browser` and
       `public_sessions`.
     - A per-visitor budget account with its own manifest and approval
       (the visitor is the authority for their own money).
     - The worker claims own-key runs and builds each call's client from that
       visitor's decrypted key. The model client is already created per call.
   - Constraint: the owner's existing paid path must stay byte-for-byte
     unchanged and regression-tested, because key day depends on it. No paid
     call is made before the owner authorises paid testing.

2. **Free-use switch** (before submission)
   - Already exists: an owner-funded public delegation with per-visitor caps,
     an exact deadline and an explicit Stop (docs/PUBLIC_RESEARCH_ACCESS.md).
   - Change: the maximum delegation lifetime (currently 24 hours) becomes a
     server setting (for example up to 14 days), so one approval can cover the
     judging period. It keeps the exact deadline, the Stop and the caps.
   - After judging: stop the delegation, or issue one with "a few runs per
     visitor".

3. **Connect Zenodo for every visitor** (before submission; live test after
   deployment)
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
   - Own key and Zenodo connections follow the account.
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
