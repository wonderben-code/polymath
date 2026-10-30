# Publishing discoveries to Zenodo: design

Owner direction, 30 October 2026: connecting Zenodo should be easy. Polymath
should be able to write up and publish discoveries automatically, if the
person wants and approves, with a choice of approval levels.

## What already exists

A careful pipeline that leaves every authority decision to a person:

1. A finding is written up as a summary or paper. This is an ordinary paid
   task with its own evidence checks.
2. The write-up is **prepared**: exact exported bytes, plus metadata (title,
   description, creators, licence) and a destination (Zenodo sandbox or
   production), are frozen into a manifest with a SHA-256.
3. A **publisher** signs in and **approves that exact manifest**. The approval
   expires, can be revoked, and is rechecked whenever anything changes.
4. A separate **publication worker** creates the Zenodo deposit, uploads the
   file, verifies it, publishes, then verifies the DOI and the bytes again. A
   lost response is never retried blindly; it is reconciled.

Zenodo access is one server-wide credential belonging to the service owner,
the configured publisher. Everything is switched off by default.

## The gap

- There is no "connect" moment: credentials live in server settings.
- Nothing happens automatically: writing, preparing, approving and queuing
  are each a separate manual step.
- There is one level, "approve each one".

## Design principles

- **One decision, made once, honoured exactly.** The person chooses how much
  Polymath may do on its own. Polymath does exactly that and no more, and
  every automatic step records the standing choice that allowed it.
- **Permanent things get a deliberate yes.** A Zenodo DOI is permanent and
  public, so publishing permanently on its own requires a one-time,
  explicit confirmation, a daily cap and an evidence bar.
- **Nothing new in the trust core.** Automatic steps call the same
  prepare, approve and queue paths, under the same live publisher session,
  so every existing check, expiry and revocation still applies.
- **Calm, legible state.** One place shows: connected or not, the chosen
  level, what is being written, what is ready, and what is published.

## Phase 1 (built 30 October 2026): approval levels and the publishing autopilot

For the publisher, the person signed in to publish under the server's
Zenodo connection. A **Publishing** section in Your library holds four
choices:

| Level | What Polymath does on its own | What the person does |
|---|---|---|
| **1. Ask me every time** (default) | Nothing | Writes, prepares and approves by hand, as today |
| **2. Draft for me** | When a finding holds, writes a summary or paper and prepares the Zenodo record with the person's default authors and licence | One tap: *Publish* |
| **3. Publish to the sandbox** | As level 2, then approves and queues it for **Zenodo sandbox** (a test space; nothing permanent) | Nothing; can revoke |
| **4. Publish for real** | As level 3 to **Zenodo production**, only for findings that meet the evidence bar, up to a daily cap | A one-time confirmation when choosing this level |

Settings beside the levels: default authors (name, affiliation, ORCID),
licence (CC BY 4.0 or CC0), keywords, summary or paper, an optional
"full-text evidence only" bar, and the daily cap (1–10).

**How it runs.** It works like Converge and Frontier: the open browser
advances one step at a time, because paid writing must be authorised by a
live session.

1. The server picks the next candidate: an owned, current, supported
   finding, saved after the level was switched on (switching on never sweeps
   up old work), not yet handled, meeting the bar and within the cap.
2. The browser writes it through the ordinary artifact API.
3. The browser prepares the manifest through the ordinary publication API,
   using the default metadata.
4. Level 2 stops here, and the item shows *Ready to publish*.
5. Levels 3 and 4 approve through the ordinary approval endpoint, under the
   publisher's live session, then queue the publication job.
6. The worker publishes and verifies, and the item shows the DOI.

Each item is recorded in `publishing_items`: finding, level, artifact,
manifest, approval, job, status and reason. So "why was this published?"
always has an exact answer. Turning the level down stops new automatic
steps at once. Anything already queued can still be cancelled with the
existing controls.

**Connection status.** The section says plainly whether Zenodo is connected
for the sandbox, production, both or neither, and under which publisher
name. Connecting the owner's account stays a server setting, which the
deployment guide covers in one step.

## Phase 2: "Connect Zenodo" for everyone

**Built 30 October 2026 as a separate visitor path; see
[CONNECT_ZENODO.md](CONNECT_ZENODO.md).** It leaves the operator publisher code
below untouched instead of adding a second authority to it. Visitors publish
one finding at a time today; the four levels for visitors come next. The
original proposal follows for reference.

A **Connect Zenodo** button lets any person sign in with their own Zenodo
account, using Zenodo's standard OAuth login and the `deposit:write` scope,
then publish under their own name at any of the four levels.

What it involves:

- Registering a Polymath OAuth application on Zenodo (the owner, in about two
  minutes) with the public site's callback URL, so it follows deployment.
- Storing each person's Zenodo access token **encrypted** with a
  server-only key, for the life of their browser session; *Disconnect*
  deletes it immediately.
- A second kind of publisher authority, "the connected account holder",
  alongside the existing operator publisher. Approval validity, publication
  jobs and the worker's client choice learn to use the token for that
  connection. This touches the most security-sensitive code, so it gets its
  own tests and its own review.
- Publishing under the person's own Zenodo account and name; Polymath never
  publishes as the person without their connection and level.

Why it is a separate decision: it changes who can publish through Polymath
(from the owner to any visitor) and means holding third-party credentials.
It also cannot be fully verified until the site is live on a public URL with
Zenodo reachable.

## Connecting Zenodo for the owner (Phase 1)

On the server, alongside the existing publication settings, set:

- `PUBLICATION_APPROVAL_ENABLED=true`
- `OPERATOR_BOOTSTRAP_TOKEN`: a strong, server-only code
- `PUBLICATION_PUBLISHER_ID` and `PUBLICATION_PUBLISHER_NAME`
- `PUBLICATION_DESTINATIONS`, for example `["sandbox"]` first
- `ZENODO_SANDBOX_TOKEN`: a personal token from sandbox.zenodo.org with
  `deposit:write` and `deposit:actions`
- `PUBLICATION_EXECUTION_ENABLED=true` for the API and the publication worker

Then sign in under *Publish to Zenodo* in Your library and choose a level.
Production adds `"production"` and `ZENODO_PRODUCTION_TOKEN`.

## Verification plan (Phase 1)

- PostgreSQL tests:
  - The levels and their permissions.
  - Candidate selection (current supported only, after enabling, the bar and
    the cap).
  - Attaching only exact linked artifacts, manifests, approvals and jobs.
  - Turning the level down stops new steps.
  - Production requires the confirmation.
- Web tests: the settings, the queue, and the autopilot calling the
  existing APIs in order and stopping at level 2.
- Accessibility audit and screenshots.
- Live Zenodo sandbox publication remains for key day and deployment.

## Verification (30 October 2026)

- **PostgreSQL:** 8 tests cover:
  - every level, with level 2 stopping at ready
  - level 3 approving, queuing and recording publication
  - the production confirmation, and lowering the level never letting a
    production-ready item through
  - exact links only, settings validation, the full-text bar and the daily cap
  - switching on never sweeping up earlier work
  - routes needing a publisher session, and the plain status route
- **0024 round trip:** passes, now excluding the 0028 tables.
- **Web:** 4 tests cover:
  - the ordinary routes called in order, with each exact result attached
  - refusing a changed record
  - the not-connected wording, with no operator request
  - the four levels, the one-time confirmation, and a one-tap Publish
- **Local server:** with publishing configured for the sandbox and execution
  off, signing in, choosing *Draft for me*, entering an author and saving
  worked; the preference was stored. Screenshots were reviewed at 1440 px and
  390 px.
- **Not yet done:** a live Zenodo sandbox publication, and live Meta writing
  (key day and deployment).
