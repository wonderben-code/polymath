# Run your own Polymath

Polymath is open source (Apache 2.0). Running your own copy gives you
unlimited research on **your own Meta key**. The key stays on your own server
and is never shared with anyone, including the Polymath website.

This is the best option for heavy use or for a lab. Expect about half an hour,
and no programming: three commands and a few questions.

## What you need

1. **A Meta Model API key.** Create one in Meta's developer dashboard
   (dev.meta.ai). Set a spending limit there first.
2. **A small server.** For example a USD 12-a-month, 2 GB "Droplet" from
   DigitalOcean, or any Linux computer with Docker installed.
3. **A web address** (optional). Any domain you own works; without one you can
   use the server's address.

## Steps

1. **Create the server** (Ubuntu 24.04, 2 GB) and point your web address's
   `A` records (`@` and `www`) at it.
2. **Install Polymath** with one command on the server, as root:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/wonderben-code/polymath/main/scripts/setup-server.sh | bash -s -- your.domain
   ```

   It installs Docker, makes every secret on the server and starts the site
   with every spending switch off.
3. **Connect your Meta key** with one more command:

   ```bash
   bash /opt/polymath/scripts/connect-meta.sh
   ```

   It asks for your key (hidden), your model and its prices, your Meta billing
   account ID and spending limit, Polymath's own total and daily limits, each
   visitor's daily allowance, concept art yes or no, and whether to keep
   research on automatically. It then runs a free key check, ONE verification
   call to Meta (a few cents) and switches research on for your site. It drives
   the operator desk's own reviewed steps (`scripts/operator-desk.sh`), so every
   server-side cap and check still applies.
4. **Optional: Zenodo publishing and sign-in.** Register the free apps
   ([SIGN_IN_SETUP.md](SIGN_IN_SETUP.md), with your own domain in each callback
   address), then run `bash /opt/polymath/scripts/connect-sign-in.sh`.
5. **Research.** Open your address and start. Everything is saved on your
   server.

Everyday commands: `connect-meta.sh status` (what is on and what is spent),
`connect-meta.sh stop` (all research spending off at once), `connect-meta.sh
renew` (back on, or two more weeks), and rerunning `connect-meta.sh` to change
the key, model or limits. Public funding windows last at most 14 days; with
automatic renewal a daily cron check (`/etc/cron.d/polymath-renew`) renews them
without a model call. Reviewed prices and limits stay valid for 90 days, then
the helper asks again. One setup's total limit is at most USD 150 (the
server's ceiling); running the helper again starts a fresh budget account.

## Choosing your Meta model

Your copy uses whichever Meta model you name in `META_MODEL`:
- `muse-spark-1.3` is Meta's recommended model, and its price is what makes long research loops affordable;
- `muse-wonder-1.0` is Meta's most capable model, at about five times the price;
- any newer Meta model works the same way.

Change the setting, rerun the setup line, and approve the new model on the
operator desk. The approval is tied to the model, so switching models always
asks for a fresh review. Concept art in Studio uses `META_IMAGE_MODEL`
(`muse-image-1.0`), switched on with `PAID_META_IMAGE_ENABLED=true`.

On the shared site, visitors use the operator's model. A model picker for
visitors, and connecting your own Meta account, are on the roadmap.

## Good to know

- **Your key is used only by your own copy**, inside your own Meta account and
  limits. This is ordinary use of your own key in your own deployment.
- **Updating:** run `git pull`, then the start command again.
- **Stopping research at any time:** the operator desk has a Stop.
- The full production guide, with backups and HTTPS details, is in
  [DEPLOYMENT.md](DEPLOYMENT.md).
