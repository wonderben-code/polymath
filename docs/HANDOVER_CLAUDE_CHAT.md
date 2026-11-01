# Handover for Claude chat: update askpolymath.com, then switch on live Meta

**For:** Claude chat (with browser access), working alongside the owner, Eks.
**From:** Claude Code, which builds Polymath.
**Site:** https://askpolymath.com (DigitalOcean droplet `167.172.85.85`,
Ubuntu 24.04). The code lives at `/opt/polymath`, and its private settings at
`/opt/polymath/deploy/production.env`.

## Ground rules

1. **Secrets never pass through you.** Don't read, type, repeat or record a key,
   token or password. Eks pastes them into the server himself (hidden prompts or
   the editor). If a secret ever appears on screen, look away from it in your
   notes and tell Eks to rotate it.
2. **Paid steps happen only in Part B, and only after Eks says, in his own words,
   that paid testing may start, with a spending cap.** Part A spends nothing.
3. **Change only what this handover says.** Leave the droplet, firewall, DNS,
   sign-in and Zenodo settings alone.
4. **At each CHECKPOINT, stop.** Send Eks a short report to pass to Claude Code,
   then wait for the go-ahead before continuing.
5. **If something doesn't match this handover, stop and describe what you see.**
   Don't improvise on the server.

## Part A: update the site (free, about 12 minutes)

This brings the site up to date with everything built so far:
- **Innovation Studio:** a new Studio tab, marked Experimental, where you
  describe something to create, with four example starters. Each invention
  comes with its source evidence, engineering and calculations, a stress test,
  the dossier download and Continue in Meta AI.
- **Galleries of 5 to 7 Muse Image pictures per invention.** These stay
  switched off until Part B.
- **The Polymath × Meta story** on the home page, About, credits and footer.

Steps:

1. Open https://cloud.digitalocean.com, then **Droplets**, then the Polymath
   droplet, then **Console** (top right). A terminal opens, logged in as `root`.
2. Paste this line and press Enter:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/wonderben-code/polymath/main/scripts/setup-server.sh | bash -s -- askpolymath.com
   ```

3. Wait for `✓ Polymath is live at https://askpolymath.com`. It takes up to 10
   minutes, and long build output is normal. It keeps every existing setting
   and applies two new database updates automatically: Studio (`0034`) and its
   pictures (`0035`, `0036`).
4. Check, in a new private browser window:
   - https://askpolymath.com/api/v1/readiness shows
     `"status":"ready"` with `"migrations":"ok"`.
   - https://askpolymath.com shows the line *Meta frontier intelligence ×
     Polymath's engine across the world's science* under the headline.
   - The header has **Studio** with an *Experimental* badge. Its page reads
     *Invent something that doesn't exist yet.* and shows four example
     buttons, starting with *A neural interface for the outdoors*.
   - The footer reads *Intelligence by Meta's frontier Muse models*, with
     *Privacy* and *Terms* links.
   - Sign in still lists ORCID, GitHub and Google (no need to sign in again).
   - Research shows a calm *paused* notice. That's expected until Part B.

**CHECKPOINT A.** Report to Eks: the setup's final line, the readiness result,
and whether each check above passed. Copy any error text exactly. Never include
settings values.

## Part B: switch on live Meta research and Studio art (paid; only with Eks's go-ahead)

*(Claude Code adds this part once the exact production steps are checked
against the code. Wait for it.)*
