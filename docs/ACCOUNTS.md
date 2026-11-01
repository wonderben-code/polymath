# Accounts: sign in, or continue as a guest

Step 4 of [PUBLIC_TOOL_PLAN.md](PUBLIC_TOOL_PLAN.md). Built 1 November 2026.

## What a visitor sees

Everyone starts as a guest and can use everything. A quiet **Sign in** in the
header offers *Continue with ORCID* (for researchers; it fills their name and
ORCID on anything they publish), *Continue with GitHub* and *Continue with
Google*, and says plainly: "Or simply carry on as a guest. Your work stays in
this browser for 7 days."

Signed in, the header shows their name. Its panel offers *Sign out* and
*Delete my account*, which asks once and keeps the browser's work as a guest
for 7 days.

## How it works

- **Guest work carries over automatically.** Everything in Polymath belongs to
  a browser session. The first time someone signs in, the session they are
  using becomes their account's home, so nothing has to move. It is then kept
  for a year from each sign-in instead of 7 days.
- **Another device** that signs in opens the same home session, so the same
  research, library and Zenodo connections appear there. That device's own
  guest session is kept aside, in a separate cookie, and comes back on
  *Sign out*.
- **A browser holding one account** must sign out before signing in to another;
  two accounts' work is never mixed.
- **Secrets.** The home session's token is stored only as AES-256-GCM
  ciphertext under `ACCOUNTS_KEY`. Provider tokens are used once to read a
  stable ID and a display name, then dropped. Polymath never sees a password.
  The OAuth `state` is one-time, expires after ten minutes, is bound to the
  browser that started sign-in, and is stored only as a hash. The return page
  (`/account/callback`) is not cached and sends no referrer.
- **What is stored:** the provider, its stable user ID, a short plain-text
  display name and, for ORCID, the ORCID iD. *Delete my account* removes it.
- Free-use limits stay per visitor: one account is one visitor on every device.

## Switches (all off by default)

```
ACCOUNTS_ENABLED=false
ACCOUNTS_KEY=                 # 32 random bytes, base64: openssl rand -base64 32
PUBLIC_SITE_ORIGIN=https://polymath.example.org
ORCID_CLIENT_ID= / ORCID_CLIENT_SECRET=     # ORCID_BASE_URL=https://sandbox.orcid.org to test
GITHUB_CLIENT_ID= / GITHUB_CLIENT_SECRET=
GOOGLE_CLIENT_ID= / GOOGLE_CLIENT_SECRET=
```

A provider appears only when both of its credentials are set.

## Owner action after deployment

Step by step, with a one-command helper that saves the secrets on the server:
[SIGN_IN_SETUP.md](SIGN_IN_SETUP.md).


Register a free OAuth app with each provider you want to offer, using the
redirect URI `https://<your-domain>/account/callback`:

- ORCID: https://orcid.org/developer-tools (Public API, scope `/authenticate`)
- GitHub: https://github.com/settings/developers → New OAuth App
- Google: https://console.cloud.google.com/apis/credentials → OAuth client ID
  (Web application; scopes `openid` and `profile`)

## What is verified

All verification is local. Twelve PostgreSQL tests use fake providers:
- the first sign-in carries guest work over;
- another device opens the same work and gets its own guest work back on
  sign-out;
- a sign-in link is one-time and bound to its browser;
- ORCID and Google identities are read correctly;
- a browser holding one account cannot sign in to a second;
- a refused sign-in changes nothing;
- deletion keeps the browser's work as a guest;
- every write needs CSRF.

Seven web tests and a real-browser check at 1440 and 390 pixels wide (axe
clean, Escape closes the panel, a cancelled sign-in changes nothing) cover the
browser side. Real provider sign-in needs the app registrations after
deployment.
