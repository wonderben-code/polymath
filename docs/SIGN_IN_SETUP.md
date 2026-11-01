# Switching on Sign in and Connect Zenodo

Handing this to Claude in Chrome? Use the fuller [HANDOVER_SIGN_IN.md](HANDOVER_SIGN_IN.md).

Polymath already has the features **Sign in** (with ORCID, GitHub or Google) and
**Connect Zenodo** (each visitor publishes to their own Zenodo account). They
belong to every visitor, not to the owner.

To offer them, Polymath must be registered once with each provider as an
"app". This is free and takes a few minutes per provider. Each registration
gives Polymath a **client ID** (public) and a **client secret** (private), which
let any visitor sign in with their own account. Polymath never sees a visitor's
password.

Every provider is optional. A provider appears on the site only once its
app is saved.

## The two addresses you will paste

| For | Callback / redirect address |
|---|---|
| Sign in (ORCID, GitHub, Google) | `https://askpolymath.com/account/callback` |
| Connect Zenodo | `https://askpolymath.com/zenodo/callback` |

Homepage / website address everywhere: `https://askpolymath.com`.
App name everywhere: **Polymath**. Short description:
*"Polymath finds discoveries between fields of science. Sign in to keep your
research across devices."*

## 1. GitHub (about 2 minutes)

1. Open https://github.com/settings/developers, then **OAuth Apps → New OAuth App**.
2. Application name **Polymath**; Homepage URL `https://askpolymath.com`;
   Authorization callback URL `https://askpolymath.com/account/callback`.
3. **Register application**. Copy the **Client ID**.
4. **Generate a new client secret** and copy it straight away (GitHub shows it once).

## 2. Google (about 5 minutes)

1. Open https://console.cloud.google.com and create a project named **Polymath**.
2. Go to **Google Auth Platform** (also called *OAuth consent screen*) and
   **Get started**: app name **Polymath**, your support email, audience
   **External**, your contact email, then **Create**.
3. **Clients → Create client**: type **Web application**, name **Polymath**,
   *Authorized redirect URIs* → **Add URI** →
   `https://askpolymath.com/account/callback`. **Create**.
4. Copy the **Client ID** and **Client secret** (download the JSON too, as a backup).
5. **Audience → Publish app → Confirm.** This lets anyone sign in rather than
   only listed test users. Polymath asks only for `openid` and `profile` (a name
   and a stable ID; not even an email), so Google needs no review.

## 3. ORCID (about 3 minutes), the researchers' favourite

1. Sign in at https://orcid.org, then open https://orcid.org/developer-tools.
2. Register for the free **Public API**: name **Polymath**, website
   `https://askpolymath.com`, the description above, redirect URI
   `https://askpolymath.com/account/callback`.
3. Copy the **Client ID** (it starts `APP-`) and the **Client secret**.

A visitor who signs in with ORCID gets their name and ORCID iD filled in on
anything they publish.

## 4. Zenodo (about 3 minutes each)

Zenodo has two sites with separate accounts: **zenodo.org** (real, permanent
DOIs) and **sandbox.zenodo.org** (a practice copy for trying publishing safely).
Offering both is best: judges can try a publication in Sandbox.

On each site:
1. Sign in, then open **Account → Applications**
   (https://zenodo.org/account/settings/applications/ or
   https://sandbox.zenodo.org/account/settings/applications/).
2. Under **Developer applications**, choose **New application**: name
   **Polymath**, website `https://askpolymath.com`, redirect URI
   `https://askpolymath.com/zenodo/callback`, client type **Confidential**.
3. Copy the **Client ID** and **Client secret**.

## 5. Put them on the server (one command)

Connect to the server as usual, update it with the normal setup line (this also
brings the newest Polymath), then run the sign-in helper:

```bash
curl -fsSL https://raw.githubusercontent.com/wonderben-code/polymath/main/scripts/setup-server.sh | bash -s -- askpolymath.com
```

```bash
bash /opt/polymath/scripts/connect-sign-in.sh
```

For each provider it asks **"Add or replace it now? [y/N]"**. Type `y`, paste
the client ID, then paste the client secret. The secret stays hidden while you
paste, so nothing shows on screen. Press Enter to skip a provider. It saves
everything only on the server (readable by root alone), switches the features
on, restarts in about a minute, and tells you what is now on. Run it again at
any time to add another provider; what is already saved stays as it is.

## 6. Try it

Open https://askpolymath.com in a private window:
- **Sign in** (top right) lists each provider you saved. Sign in, run or open
  something, then open the site on your phone and sign in there too. The same
  library appears.
- Open a written finding and choose **Publish to your Zenodo** → **Connect**.

Tell Claude what you see; the live checks are then recorded in
[PROGRESS.md](PROGRESS.md).

## Safety notes

- Never paste a client secret into a chat, email or document; only into the
  hidden prompt on the server.
- To replace a secret (for example if it was ever shown), generate a new one
  with the provider, then rerun the helper and choose `y` for that provider.
