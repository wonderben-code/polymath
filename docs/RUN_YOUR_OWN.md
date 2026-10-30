# Run your own Polymath

Polymath is open source (Apache 2.0). Running your own copy gives you
unlimited research on **your own Meta key**. The key stays on your own server
and is never shared with anyone, including the Polymath website.

This is the best option for heavy use or for a lab. Expect about an hour, and
no programming. You will follow the steps and copy a few commands.

## What you need

1. **A Meta Model API key.** Create one in Meta's developer dashboard
   (dev.meta.ai). Set a spending limit there first.
2. **A small server.** For example a USD 12-a-month, 2 GB "Droplet" from
   DigitalOcean, or any Linux computer with Docker installed.
3. **A web address** (optional). Any domain you own works; without one you can
   use the server's address.

## Steps

1. **Install Docker** on the server. Most hosts offer a one-click "Docker"
   image; choose that.
2. **Download Polymath:**

   ```bash
   git clone https://github.com/wonderben-code/polymath.git && cd polymath
   ```

3. **Create your private settings file:**

   ```bash
   cp deploy/production.env.example deploy/production.env && chmod 600 deploy/production.env
   ```

   Open `deploy/production.env` and fill in:

   - your web address
   - a database password
   - an operator code (make one with `openssl rand -hex 32`)
   - the Meta section: your key as `MODEL_API_KEY`, `META_MODEL` and the budget settings, as [KEY_DAY.md](KEY_DAY.md) explains

   Your key is written only to this file on your own server.

4. **Start Polymath:**

   ```bash
   docker compose -f deploy/compose.production.yaml --env-file deploy/production.env up -d --wait
   ```

5. **Set your spending limits.** Open `https://your-address/operator/budget`,
   sign in with your operator code, and follow
   [KEY_DAY.md](KEY_DAY.md):
   1. A free check.
   2. Your limits.
   3. One verification call costing a few cents.
   4. Switch research on.

   Nothing spends until you do this.

6. **Research.** Open your address and start. Everything is saved on your
   server.

## Good to know

- **Your key is used only by your own copy**, inside your own Meta account and
  limits. This is ordinary use of your own key in your own deployment.
- **Updating:** run `git pull`, then the start command again.
- **Stopping research at any time:** the operator desk has a Stop.
- The full production guide, with backups and HTTPS details, is in
  [DEPLOYMENT.md](DEPLOYMENT.md).
