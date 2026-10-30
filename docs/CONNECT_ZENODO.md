# Connect Zenodo: every visitor publishes under their own name

Step 3 of [PUBLIC_TOOL_PLAN.md](PUBLIC_TOOL_PLAN.md). Designed 30 October 2026.

## What a visitor sees

1. **Your library → Your Zenodo.** Two quiet rows:
   - *Zenodo sandbox*: a practice site where records are temporary.
   - *Zenodo*: real, permanent records with a DOI.

   Each row has one button, *Connect*. Zenodo opens, the visitor signs in there
   and approves Polymath, and they come straight back to the same place, now
   connected. *Disconnect* deletes the connection immediately.
2. **Under any written finding → Publish to your Zenodo.** The title and summary
   are already filled in. The visitor adds their name (and ORCID, if they have
   one), picks practice or real, reads the exact record, and presses *Publish*.
   For real records it asks once more: "This creates a permanent public record
   with a DOI under your Zenodo account."
3. **The finished record** shows its DOI and a link to it on Zenodo.

Nothing ever publishes to the site owner's Zenodo account: the owner's
publisher path (docs/PUBLISHING.md) is separate and unchanged, and a visitor
can only reach their own connection.

## How it works

- **Sign-in with Zenodo (OAuth).** Polymath asks Zenodo for only the
  `deposit:write` and `deposit:actions` permissions. Polymath never sees the
  visitor's Zenodo password.
- **Return path.** Zenodo returns to `https://<site>/zenodo/callback`. That page
  finishes the connection with an ordinary same-site request, so the usual
  CSRF and origin checks apply. A one-time random `state`, which only this
  browser session holds, expires after ten minutes. Polymath stores only its
  SHA-256 hash.
- **Tokens at rest.** Access and refresh tokens are encrypted with AES-256-GCM
  using `ZENODO_CONNECTION_KEY`, which never leaves the server. Each ciphertext
  is bound to its browser session and destination. Tokens are never logged,
  never sent to a browser, and deleted on *Disconnect* or when the browser
  session ends.
- **Exact records.** A publication binds the same exact manifest the owner's
  path uses: the artifact bytes, metadata, destination and a remote marker.
  Visitors publish only their own saved, current, written findings.
- **Where requests run.** The API makes exactly one outbound request per
  connection: the sign-in code exchange with zenodo.org. Everything else
  (token refresh, drafts, uploads, publishing) runs in the worker.
- **A durable worker** (`polymath.visitor_zenodo_worker`, compose profile
  `zenodo`) creates the draft,
  uploads the exact bytes, reads them back and verifies their SHA-256, then
  publishes and verifies again before reporting a DOI.
- **No duplicate records.** The only step that could duplicate a record is
  creating the draft. If its outcome is ever unknown (lost response, crash),
  Polymath never creates again on its own. It marks the record *Checking*,
  searches the visitor's own Zenodo read-only for the exact marker, and adopts
  the one matching draft. If none is found, the visitor sees "Nothing reached
  Zenodo" and decides whether to try again.
- **Limits.** Ten publications per visitor per day, and a real record always
  needs that one explicit confirmation.

## Switches (all off by default)

```
VISITOR_ZENODO_ENABLED=false
ZENODO_CONNECTION_KEY=            # 32 random bytes, base64 (openssl rand -base64 32)
PUBLIC_SITE_ORIGIN=https://polymath.example.org
ZENODO_SANDBOX_CLIENT_ID= / ZENODO_SANDBOX_CLIENT_SECRET=
ZENODO_PRODUCTION_CLIENT_ID= / ZENODO_PRODUCTION_CLIENT_SECRET=
```

A destination appears only when both of its OAuth credentials are set.

## Owner action after deployment

Register a free OAuth application on each site:
- https://sandbox.zenodo.org/account/settings/applications/
- https://zenodo.org/account/settings/applications/

Use the redirect URI `https://<your-domain>/zenodo/callback`, then copy each
client ID and secret into `deploy/production.env`. Set
`VISITOR_ZENODO_ENABLED=true`, a fresh `ZENODO_CONNECTION_KEY`, and
`PUBLIC_SITE_ORIGIN`. Then start the worker:

```bash
docker compose -f deploy/compose.production.yaml --env-file deploy/production.env --profile zenodo up -d --wait
```

Where Connect Zenodo is on, each written finding shows *Publish to your
Zenodo* in place of the site publisher's review panel. The owner's publishing
levels and autopilot in Your library are unchanged.

## What is verified

All verification so far is local, against mock HTTP. That covers the token
exchange, encryption binding, ownership, the full publish path, the
unknown-outcome handling and Disconnect. Native Zenodo OAuth and a live sandbox
publication still need to be tested after deployment.
