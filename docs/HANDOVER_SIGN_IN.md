# Handover: switch on Sign in and Connect Zenodo for askpolymath.com

**For:** Claude in Chrome, working alongside the owner (Eks).
**From:** Claude Code, which built Polymath and wrote this handover.
**Time needed:** about 30 to 40 minutes in total.

## What we are doing, and why

Polymath (https://askpolymath.com) is a public AI research tool built for the
Meta hackathon. Its code already includes two features for **every visitor**:

- **Sign in** with ORCID, GitHub or Google, so a visitor's research follows
  them across devices (guests can still use everything).
- **Connect Zenodo**, so a visitor can publish their findings to *their own*
  Zenodo account (zenodo.org for real records, sandbox.zenodo.org for practice).

These features stay hidden until Polymath is registered once with each
provider as an "OAuth app". Each registration gives a **client ID** (not secret)
and a **client secret** (secret). Your job is to:

1. update the server;
2. register the five apps;
3. save each ID and secret on the server with a helper command;
4. test that sign-in and Connect Zenodo work end to end, so the hackathon
   judges can use them.

These are Polymath's apps, registered under the owner's accounts. Visitors
never see the owner's accounts; each visitor signs in with their own.

## Ground rules (please follow exactly)

1. **Client secrets never pass through you.** Don't read a secret aloud, type
   it, write it in chat, notes or any document, or include it in a screenshot
   description. When a secret is on screen, pause and ask Eks to copy it (with
   the provider's copy button) and paste it into the server's hidden prompt.
   Client IDs are not secret; you may type those.
2. **Passwords, two-factor codes and account sign-ins are Eks's.** If any site
   asks for one, hand over to Eks.
3. **Change only what this handover says.** Leave every other setting alone:
   droplet, firewall, DNS, Meta settings and Polymath's spending or publishing
   switches. Never turn on anything with "META", "PAID" or "PUBLICATION" in its
   name.
4. **Don't publish anything real on zenodo.org.** Testing uses Connect only,
   or the Sandbox site.
5. **Don't upload a logo** to any provider (on Google it triggers a review that
   takes weeks).
6. **If something doesn't match this handover,** stop and describe what you
   see rather than guessing.

## The values to paste

| Field (whatever the site calls it) | Value |
|---|---|
| App / application name | `Polymath` |
| Homepage / website / application URL | `https://askpolymath.com` |
| Description (where asked) | `Polymath finds discoveries between fields of science. Sign in to keep your research across devices, and publish your findings to your own Zenodo.` |
| **Sign-in** callback / redirect URI (ORCID, GitHub, Google) | `https://askpolymath.com/account/callback` |
| **Zenodo** redirect URI (zenodo.org and sandbox) | `https://askpolymath.com/zenodo/callback` |

Paste the addresses exactly: `https`, no `www`, no trailing slash. One wrong
character causes a "redirect URI mismatch" error later.

## Part A: Open the server console and update Polymath (about 12 minutes)

1. Go to https://cloud.digitalocean.com and open **Droplets**. Open the Polymath
   droplet (the one serving askpolymath.com, IP `167.172.85.85`).
2. Open the in-browser terminal: the **Console** button at the top right (or
   **Access → Launch Droplet Console**). A black terminal opens, logged in as
   `root`. Keep this tab open for the whole session; this handover calls it
   **the console tab**.
3. Paste this single line and press Enter:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/wonderben-code/polymath/main/scripts/setup-server.sh | bash -s -- askpolymath.com
   ```

4. Wait until it prints `✓ Polymath is live at https://askpolymath.com`. This
   takes up to 10 minutes; long build output is normal. It keeps all existing
   settings. If it ends with an error instead, stop and copy the last 20 lines
   for Eks to pass to Claude Code.

**What the helper looks like.** Later steps run this command in the console
tab:

```bash
bash /opt/polymath/scripts/connect-sign-in.sh
```

It asks these five questions, in this order:

```
ORCID (not set up). Add or replace it now? [y/N]
GitHub (not set up). Add or replace it now? [y/N]
Google (not set up). Add or replace it now? [y/N]
Zenodo (zenodo.org) (not set up). Add or replace it now? [y/N]
Zenodo Sandbox (sandbox.zenodo.org, for test publications) (not set up). Add or replace it now? [y/N]
```

- For the provider you have just registered, type `y` and press Enter.
- It then asks for the **client ID**: you type it.
- Then it asks for the **client secret (hidden)**: hand to Eks, who pastes it
  and presses Enter. Nothing shows while pasting; that is expected.
- For every other provider, just press Enter to skip; anything already saved
  stays saved.
- It then restarts Polymath (about a minute) and prints which features are on,
  for example `✓ Sign in is on: GitHub`.

**Do one provider at a time:** register it, then run the helper for it
straight away. Some sites (GitHub, Google) show the secret only once, so saving
it right away means nothing gets lost.

## Part B: GitHub (about 3 minutes)

1. In a new tab, open https://github.com/settings/developers and choose
   **OAuth Apps**, then **New OAuth App** (or **Register a new application**).
   The account should be `wonderben-code`.
2. Fill in:
   - Application name: `Polymath`
   - Homepage URL: `https://askpolymath.com`
   - Application description: the description above
   - Authorization callback URL: `https://askpolymath.com/account/callback`
   - Leave **Enable Device Flow** unticked.
3. Click **Register application**. Note the **Client ID**.
4. Click **Generate a new client secret**. GitHub may ask Eks to confirm with a
   password or 2FA (hand over). The secret is shown **once**; leave it on
   screen.
5. In the console tab, run the helper. Type `y` at **GitHub**, type the client
   ID, then hand to Eks to paste the secret. Press Enter for everything else.
6. Check that the helper prints `✓ Sign in is on: GitHub`.

## Part C: Google (about 8 minutes)

1. Open https://console.cloud.google.com (Eks signs in to Google if needed).
2. Project: use the project picker at the top, then **New project**, named
   `Polymath`, then **Create**, then select it.
3. Open **Google Auth Platform** (search the top bar for "Google Auth
   Platform" or "OAuth consent screen") and click **Get started**:
   - App information: app name `Polymath`; user support email: Eks's email
     (choose from the dropdown).
   - Audience: **External**.
   - Contact information: Eks's email.
   - Agree to the policy, then **Continue** and **Create**.
4. **Clients → Create client** (or **Create OAuth client**):
   - Application type: **Web application**
   - Name: `Polymath`
   - Authorized JavaScript origins: leave empty.
   - **Authorized redirect URIs → Add URI:**
     `https://askpolymath.com/account/callback`
   - **Create.**
5. A dialog shows the **Client ID** and **Client secret**. Click **Download
   JSON** first as a backup; Eks keeps that file private. Leave the dialog open.
6. In the console tab, run the helper. Type `y` at **Google**, type the client
   ID (ends in `.apps.googleusercontent.com`), then hand to Eks to paste the
   secret.
7. **Important, so anyone can sign in:** go to **Google Auth Platform →
   Audience** and click **Publish app**, then **Confirm**. The status must read
   **In production**. Without this, only listed test users could sign in, and
   judges would be blocked.
   - Polymath asks only for `openid` and `profile` (name and a stable ID; not
     even the email address), so no verification review is needed.
   - If Google insists on extra fields before publishing (a privacy policy, for
     example), stop and tell Eks exactly what it asks for. Claude Code can add
     a page for it.
   - Privacy policy link: `https://askpolymath.com/privacy`; terms of service
     link: `https://askpolymath.com/terms` (both live after Part A).

## Part D: ORCID (about 4 minutes)

1. Open https://orcid.org/signin (Eks signs in; any ORCID record works, and a
   free one takes a minute to create). Then open
   https://orcid.org/developer-tools.
2. If asked, verify the email address on the record first (Eks), then accept
   the Public API terms and **Register for the free ORCID public API**.
3. Fill in:
   - Name of your application: `Polymath`
   - Application URL: `https://askpolymath.com`
   - Description: the description above
   - Redirect URIs: `https://askpolymath.com/account/callback`
4. **Save.** The page shows the **Client ID** (starts `APP-`) and the **Client
   secret** (sometimes behind "Show details").
5. In the console tab, run the helper. Type `y` at **ORCID**, type the client
   ID, then hand to Eks to paste the secret.

## Part E: Zenodo, real site (about 4 minutes)

1. Open https://zenodo.org and sign in (Eks), then open
   https://zenodo.org/account/settings/applications/.
2. Under **Developer Applications**, click **New application**:
   - Name: `Polymath`
   - Description: the description above
   - Website URL: `https://askpolymath.com`
   - Redirect URIs: `https://askpolymath.com/zenodo/callback` (note: **zenodo**,
     not account)
   - Client type: **Confidential**
3. **Create.** Note the **Client ID**; the **Client Secret** is on the same
   page.
4. In the console tab, run the helper. Type `y` at **Zenodo (zenodo.org)**,
   type the client ID, then hand to Eks to paste the secret.

## Part F: Zenodo Sandbox, the practice site (about 4 minutes)

The Sandbox is a separate site with a separate account. Records there are
practice only, which makes it the safe place for judges to try publishing.

1. Open https://sandbox.zenodo.org and sign up or sign in (Eks; "Sign up with
   GitHub" is quickest).
2. Open https://sandbox.zenodo.org/account/settings/applications/ and repeat
   Part E exactly, with the same values.
3. In the console tab, run the helper. Type `y` at **Zenodo Sandbox**, type the
   client ID, then hand to Eks to paste the secret.
4. The helper should now print:

   ```
   ✓ Sign in is on: ORCID, GitHub, Google
   ✓ Connect Zenodo is on: Zenodo, Zenodo Sandbox
   ```

## Part G: Test it end to end (about 8 minutes)

Use a **new incognito/private window** so you test as a fresh visitor.

1. Open https://askpolymath.com. At the top right, **Sign in** opens a panel
   listing *Continue with ORCID*, *Continue with GitHub* and *Continue with
   Google*.
2. Click **Continue with GitHub** and approve. Eks may need to sign in. You
   return to Polymath, and the top right now shows a name with a small dot.
3. Open the name panel and **Sign out**. Then repeat with **Google** and
   **ORCID**: each returns signed in.
4. **Across devices** (optional but lovely): sign in with the same provider on
   Eks's phone. The same library appears.
5. **Connect Zenodo:** while signed in, open **Your library → Publishing**. The
   **Zenodo** and **Zenodo sandbox** rows each show **Connect**. Click **Connect** on
   **Zenodo sandbox**, approve on Zenodo's page (**Authorize**), and you return
   to Polymath showing **Connected.** Repeat for the real Zenodo row; this only
   connects and publishes nothing.
6. Optionally **Disconnect** both and sign out, so the owner's test
   connections aren't left behind. Either way is fine.

## Report back to Eks (for Claude Code)

Please send a short summary. Don't include any secret:

- which providers show under Sign in, and which sign-ins completed;
- whether Connect worked for Zenodo and Zenodo Sandbox;
- the exact wording of any error, and on which step;
- whether Google's audience shows **In production**.

Claude Code then records the live checks in `docs/PROGRESS.md`.

## If something goes wrong

| What you see | What to do |
|---|---|
| `redirect_uri_mismatch` / "redirect URI is not valid" / "invalid redirect" | The callback address at that provider has a typo. Fix it there to exactly match the table above (no trailing slash, no www, `account` vs `zenodo`). No rerun needed. |
| A provider is missing from Sign in | The helper didn't save it (skipped, or the secret wasn't pasted). Rerun the helper and type `y` for that one. |
| The helper says `That does not look like a client ID/secret` | Something extra was pasted (a space, quotes, a label). Paste just the value. |
| The helper says `No settings at …` | Part A hasn't run on this server. Run Part A first. |
| Google: "Access blocked" or "only test users" | Part C step 7: **Publish app**. |
| Google won't show the secret again | Create a new secret for the client (or a new client) and rerun the helper for Google. |
| The restart prints an error | Copy the last 20 lines (they never contain secrets) for Claude Code. |
| A secret was ever shown in chat or a document | Generate a new secret with that provider, then rerun the helper with `y` for that provider. |

## Checklist

- [ ] Part A: server updated, `✓ Polymath is live`
- [ ] Part B: GitHub registered and saved
- [ ] Part C: Google registered, saved, and **In production**
- [ ] Part D: ORCID registered and saved
- [ ] Part E: Zenodo registered and saved
- [ ] Part F: Zenodo Sandbox registered and saved
- [ ] Part G: three sign-ins and two Zenodo connections tested
- [ ] Summary sent to Eks for Claude Code
