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
   - The footer reads *Intelligence by Meta's frontier Muse models* and
     *Created, designed and built by Ekram Alam · © 2026*, with *Privacy* and
     *Terms* links.
   - Sign in still lists ORCID, GitHub and Google (no need to sign in again).
   - Research shows a calm *paused* notice. That's expected until Part B.

**CHECKPOINT A.** Report to Eks: the setup's final line, the readiness result,
and whether each check above passed. Copy any error text exactly. Never include
settings values.

## Part B: switch on live Meta research, Studio pictures and free use (paid)

**Start Part B only after Eks has said, in his own words, that paid testing may
begin, and with what cap.** The plan below uses a USD 145 total cap (USD 5 below
the USD 150 credits as a margin) and USD 75 a day. Nothing spends money until
step B3.

### Six facts that shape the plan

1. **Some account limits are permanent.** The hard cap (USD 145), the daily cap
   (USD 75) and the concurrent-call limit (4) are fixed by the first saved
   proposal and can never change. They're set for the whole journey (testing,
   demo and judging). Visitor limits sit underneath them and can be changed
   later. Enter them carefully in B2.
2. **A private operator research window lasts about 20 minutes.** Signing in
   again ends it. Use it only for the quick first check (B5). For real testing,
   including Studio, use **visitor funding (B6)**, which lasts up to 14 days and
   also works in Eks's own browser.
3. **The verification call has a 5-minute window** from **Preview exact one-call
   manifest**. Have the worker command ready before clicking.
4. **Private research and visitor funding can't both be live.** Activating one
   replaces the other. Once B6 is live, never press *Activate research for THIS
   operator and browser*.
5. **An "unknown" outcome pauses everything until it's settled.** A call or
   picture whose outcome isn't known (a timeout, for example) stops new paid
   work. It's never assumed free. Resolve it with the hold tool in **If
   something goes wrong**.
6. **Never restart the site while pictures are painting** (they paint inside the
   app). The update line is safe to run once the gallery has finished.

### Console setup (run at the start of every console session)

```bash
cd /opt/polymath
dc() { docker compose -f deploy/compose.production.yaml --env-file deploy/production.env "$@"; }
ZEN=""; grep -q '^VISITOR_ZENODO_ENABLED=true' deploy/production.env && ZEN="--profile zenodo"
show() { grep -E '^(OPERATOR_ACTOR_ID|OPERATOR_DISPLAY_NAME|BUDGET_|NATIVE_|META_MODEL|META_IMAGE|PAID_|PUBLIC_META|PUBLIC_MAX|PUBLIC_RESOURCE|IMAGE_)' deploy/production.env; }
check() { dc run --rm --no-deps migrate /app/backend/.venv/bin/python -c "from polymath.config import Settings as S; s=S(); print('SETTINGS OK | readiness',s.budget_readiness_enabled,'| probe',s.paid_meta_probe_enabled,'| research',s.paid_meta_research_enabled,'| images',s.paid_meta_image_enabled,'| public',s.public_meta_research_enabled,'| account',s.native_budget_account_id,'| model',s.meta_model,'| image model',s.meta_image_model)"; }
```

`show` prints only non-secret switches. `check` validates the settings file
before any restart and prints only on/off values and names.

**Safe editing routine** (every time a step says "edit"):

```bash
cp -p deploy/production.env deploy/production.env.bak-$(date -u +%Y%m%dT%H%M%SZ)
nano deploy/production.env      # Ctrl+W search · Ctrl+O, Enter to save · Ctrl+X to exit
show                            # each name must appear exactly once, uncommented
check                           # must print SETTINGS OK; otherwise fix it or restore the backup, and do NOT restart
```

If a line exists commented out (`# NAME=...`), remove the `# ` rather than
adding a second copy.

### B0. Before starting (free)

1. **Meta dashboard (Eks):**
   - Confirm the credits are visible and the spend limit is USD 150.
   - Note the billing or team identifier, using only letters, digits and
     `. _ : -`.
   - Take a screenshot of the billing page.
2. **Server:**
   ```bash
   git -C /opt/polymath log -1 --format='%h %cd'
   bash scripts/production-backup.sh
   python3 scripts/meta-preflight.py --env-file deploy/production.env --check-key
   ```
   Expect `Summary: … 0 failed`, with `muse-spark-1.3` in the model list and
   `META_MODEL 'muse-spark-1.3' is in the list`. Also look for `muse-image-1.0`.

**CHECKPOINT B0.** Report the summary line, the model names (names only) and
the release hash.

### B1. Turn on budget preparation (free)

Edit `deploy/production.env` so these lines read exactly as below.
`MODEL_API_KEY` and `META_MODEL=muse-spark-1.3` stay unchanged.

```
OPERATOR_ACTOR_ID=owner
OPERATOR_DISPLAY_NAME=Owner
BUDGET_READINESS_ENABLED=true
BUDGET_ADMIN_ACTOR_IDS=["owner"]
BUDGET_MAX_HARD_CAP_MICRODOLLARS=145000000
NATIVE_BUDGET_ACCOUNT_ID=polymath-meta
PAID_META_PROBE_ENABLED=false
PAID_META_RESEARCH_ENABLED=false
PUBLIC_META_RESEARCH_ENABLED=false
PAID_META_IMAGE_ENABLED=false
```

`OPERATOR_ACTOR_ID` must appear in `BUDGET_ADMIN_ACTOR_IDS`, or the site won't
start (`check` catches this). Then:

```bash
check
dc $ZEN up -d --wait
curl -s https://askpolymath.com/api/v1/budget/capabilities; echo
```

Expect `"preparation_available":true` and `"reason":null`.

### B2. Save the readiness proposal (free; creates the locked account)

**Evidence hashes.** Polymath records these but never fetches them. Run on the
server:

```bash
for u in https://dev.meta.ai/docs https://dev.meta.ai/docs/models https://dev.meta.ai/docs/pricing-rate-limits https://dev.meta.ai/docs/reasoning; do printf '%s  ' "$u"; curl -fsSL "$u" | sha256sum | cut -d' ' -f1; done
date -u -d '2 minutes ago' +%Y-%m-%dT%H:%M:%SZ
```

Eks hashes the billing screenshot on his Mac with `shasum -a 256 <file>`.

1. Open https://askpolymath.com/operator/budget in the browser you'll keep
   using.
2. Eks runs `grep '^OPERATOR_BOOTSTRAP_TOKEN=' deploy/production.env` and types
   the code into **Operator access code** himself. Then press **Unlock operator
   access**. It should show "Signed in as Owner until …".
3. Open the tab **Preparation and accounts**.
4. In **Reviewed readiness proposal JSON**, paste this, filling in the `<…>`
   values:

```json
{
  "protocol": "polymath-meta-readiness-v1",
  "model_id": "muse-spark-1.3",
  "endpoint": "https://api.meta.ai/v1/chat/completions",
  "currency": "USD",
  "usage_contract": "prompt_completion_inclusive_v1",
  "observed_at": "<UTC time from the date command>",
  "valid_until": "2026-11-20T00:00:00Z",
  "billable_bounds": {
    "input_tokens_absolute": 1048576,
    "output_tokens_absolute": 8192,
    "evidence": [
      {"url": "https://dev.meta.ai/docs/models", "content_sha256": "<hash>"},
      {"url": "https://dev.meta.ai/docs/reasoning", "content_sha256": "<hash>"}
    ]
  },
  "pricing": {
    "input_microdollars_per_million": 1250000,
    "output_microdollars_per_million": 4250000,
    "evidence": [{"url": "https://dev.meta.ai/docs/pricing-rate-limits", "content_sha256": "<hash>"}]
  },
  "usage_evidence": [
    {"url": "https://dev.meta.ai/docs", "content_sha256": "<hash>"},
    {"url": "https://dev.meta.ai/docs/reasoning", "content_sha256": "<same hash as above>"}
  ],
  "application_limits": {"max_output_tokens": 4096, "max_context_characters": 90000, "max_model_calls": 12, "request_timeout_seconds": 60},
  "account": {"account_id": "polymath-meta", "hard_cap_microdollars": 145000000, "daily_cap_microdollars": 75000000, "run_cap_microdollars": 5000000, "max_in_flight": 4},
  "native_status": "not_run",
  "execution_available": false
}
```

5. Press **Preview disabled readiness proposal**. Expect:
   - Total reserve per call $1.345536.
   - Maximum full-run reservation $16.146432.
   - "No; the ledger may stop a run before its maximum call count". This is
     expected: each call is held separately and settles at its actual cost of a
     few cents.
6. Press **Save this disabled proposal**.
7. Press **Inspect account: polymath-meta**. Expect:
   - Hard cap $145, daily cap $75, max concurrent 4.
   - Execution Disabled, kill switch **On**, Frozen No, committed $0.

### B3. One paid verification call (the first spend: cents, with a $1.35 hold until it settles)

Edit `PAID_META_PROBE_ENABLED=true`. Then:

```bash
check
dc $ZEN up -d --wait
curl -s https://askpolymath.com/api/v1/budget/verification/capabilities; echo
```

Expect `"verification_available":true`.

**Prepare the attestation JSON now.** You'll reuse it in B5 and B6. Enter the
figures actually shown on Meta's dashboard. `provider_hard_cap_microdollars` is
Meta's spend limit (USD 150), which must be at least 145000000.

```json
{
  "provider_billing_account_id": "<Meta billing/team identifier>",
  "model_id": "muse-spark-1.3",
  "currency": "USD",
  "usage_contract": "prompt_completion_inclusive_v1",
  "observed_at": "<UTC now minus 2 minutes>",
  "valid_until": "2026-11-20T00:00:00Z",
  "available_credit_microdollars": 150000000,
  "remaining_provider_allowance_microdollars": 150000000,
  "provider_hard_cap_microdollars": 150000000,
  "evidence": [{"url": "<billing page URL, without ? or #>", "content_sha256": "<screenshot hash>"}]
}
```

**Timed sequence.** Have this worker command ready in the console first:

```bash
dc --profile research run --rm --no-deps research-worker /app/backend/.venv/bin/python -m polymath.meta_probe_worker --once
```

1. Reload the desk and **Unlock operator access** again.
2. Open the tab **One-call verification**.
3. Choose the saved `polymath-meta · muse-spark-1.3` attestation.
4. Paste the attestation JSON.
5. Press **Preview exact one-call manifest**. The 5 minutes start now.
6. Check the preview shows model `muse-spark-1.3`, $1.345536 and the $145 cap.
7. Tick the approval box.
8. Press **Arm and queue THIS one call**.
9. Immediately run the worker command. Expect `Meta verification worker result:
   finalized`. If it says `interrupted`, stop and do not retry.
10. In the desk, press **Inspect saved verification job**, then **Reload this
    saved job**. Expect:
    - state succeeded, model `muse-spark-1.3`, HTTP 200;
    - finish reason `stop`;
    - "Within attested billable bounds" Yes;
    - no freeze;
    - a total of a few cents.

    If you see **Finalize recorded response · no new request**, press it. It's
    free.
11. Lock the account again: go to **Preparation and accounts**, press
    **Inspect account: polymath-meta**, tick the box, then press **Disable
    future execution**. The kill switch should show On.

**CHECKPOINT B3.** Report: job state, finish reason, input and output tokens,
total amount, and the observation start and finish times.
- **Stop here** if:
  - the finish reason is `length`;
  - the action failed to parse;
  - the call took close to 60 seconds;
  - anything says unknown or freeze.
- These need an engineering change, not a retry.

### B4. Switch on research, the worker and Studio pictures

Edit:

```
PAID_META_PROBE_ENABLED=false
PAID_META_RESEARCH_ENABLED=true
PAID_META_IMAGE_ENABLED=true
META_IMAGE_MODEL=muse-image-1.0
```

Then:

```bash
check
dc $ZEN --profile research up -d --wait
dc ps research-worker
dc logs --tail=20 research-worker
curl -s https://askpolymath.com/api/v1/budget/research/capabilities; echo
curl -s https://askpolymath.com/api/v1/studio/capabilities; echo
```

Expect:
- `dc ps` shows the research worker running, not "Restarting" or "Exited".
- The logs don't say disabled or unavailable.
- The research capabilities show `"activation_available":true`.
- The Studio capabilities show `"images_available":true`.

From now on the standard update line also restarts the research worker.

### B5. A quick first investigation (private, about 20 minutes)

Prepare the review JSON, with observed_at set to UTC now minus 2 minutes:

```json
{"observed_at": "<UTC now minus 2 min>", "valid_until": "2026-11-20T00:00:00Z",
 "evidence": [{"url": "https://dev.meta.ai/docs/pricing-rate-limits", "content_sha256": "<hash>"},
              {"url": "https://dev.meta.ai/docs/models", "content_sha256": "<hash>"}]}
```

1. Reload the desk and **Unlock operator access**.
2. Open the tab **Research activation**.
3. Choose the current `polymath-meta` attestation and the passed
   `muse-spark-1.3` receipt.
4. Tick **Include Meta-ranked multi-bridge Explore** (maximum 3) and the
   scientific-context option.
5. Tick the three **Fresh human review** boxes.
6. Paste the review JSON.
7. Paste the B3 attestation, with its observed_at updated.
8. Press **Preview exact research activation**. Check the model, $1.345536 per
   call and the $145 / $75 caps.
9. Tick the approval box.
10. Press **Activate research for THIS operator and browser**.
11. Press **Open research workspace**. Run Explore: *self-organised
    criticality* and watch it finish.
12. On the desk, press **Inspect account: polymath-meta**, then **Refresh**.
    Check that:
    - committed has risen by cents;
    - **Open reserved holds $0** and **Unknown-outcome holds $0**;
    - Frozen No.

**CHECKPOINT B5.** Report: the outcome (a bridge found, or an honest
rejection), the sources count, the committed amount, and the holds.

### B6. Visitor funding: free use for everyone, including Eks, for up to 14 days

This is how judges, and Eks's own testing of Studio and its pictures, use live
research.

1. Disable the account first (kill switch On).
2. Edit:
   ```
   PUBLIC_RESOURCE_LIMITS_ENABLED=true
   PUBLIC_META_RESEARCH_ENABLED=true
   PUBLIC_MAX_DELEGATION_SECONDS=1209600
   PUBLIC_MAX_JOBS_PER_SESSION_DAY=30
   PUBLIC_MAX_JOBS_PER_DAY=200
   ```
3. Restart and check:
   ```bash
   check
   dc $ZEN --profile research up -d --wait
   curl -s https://askpolymath.com/api/v1/budget/research/public/capabilities; echo
   ```
   Expect `"activation_available":true`.
4. Unlock the desk and open the tab **Public visitor funding**.
5. Choose the current attestation and the receipt.
6. Tick multi-bridge Explore (maximum 3) and scientific context.
7. Tick the three **Fresh human review** boxes.
8. In **Exact public funding deadline in UTC**, enter a time up to 14 days from
   now. For example, activating on 2 Nov at 12:00 UTC reaches
   `2026-11-16T12:00:00Z`. The help text says "24 hours"; the server allows 14
   days.
9. Fill in **Reviewed public funding JSON**. Set observed_at to UTC now minus
   2 minutes, and keep both valid_until dates after the deadline:

```json
{
  "review": {"observed_at": "<UTC now − 2 min>", "valid_until": "2026-11-20T00:00:00Z",
             "evidence": [{"url": "https://dev.meta.ai/docs/pricing-rate-limits", "content_sha256": "<hash>"}]},
  "provider_account_attestation": { <the B3 attestation with observed_at updated> },
  "public_limits": {
    "public_total_cap_microdollars": 40000000,
    "public_daily_cap_microdollars": 30000000,
    "session_total_cap_microdollars": 20000000,
    "session_daily_cap_microdollars": 15000000,
    "max_jobs_per_session_day": 30,
    "max_jobs_per_day": 200
  },
  "discovery": {"protocol": "polymath-surprise-ladder-v1", "max_stages": 3, "max_depth": 2}
}
```

   These are the **testing** limits:
   - **Public:** USD 40 in total, USD 30 a day.
   - **Each visitor:** USD 20 in total, USD 15 a day, and up to 30
     investigations a day.
   - **Pictures:** up to USD 4 a day, on top, within the account caps.

   Before judging, Claude Code sends new, generous limits and this delegation is
   replaced.

10. Press **Preview exact public funding** and check it.
11. Tick the box.
12. Press **Activate THIS public funding delegation**.
13. Check:
    - **Inspect saved public funding** shows it current.
    - In a fresh browser tab without operator login, https://askpolymath.com
      offers funded research with an allowance.
    - Run one small Explore.
14. Press **Lock operator access**. Funding continues by design.

**CHECKPOINT B6.** Report: the deadline, the allowance shown to a visitor, and
the first visitor Explore's result.

**Then test Studio:** open Studio, tap *A submarine inspired by whales*, press
**Invent it** and keep the page open. It runs about eight investigations, then
paints the gallery, one picture at a time. Check the account after: holds $0.

### If something goes wrong

- **Stop all new paid work instantly:** go to **Preparation and accounts**,
  press **Inspect account**, then **Disable future execution**. Or use **Stop
  this public funding** and then **Disable the shared account** on the Public
  visitor funding tab. These stop research, verification and pictures. A call
  already sent may still finish and be billed.
- **An "unknown" hold** (new paid work refuses with "prior unresolved", or a
  picture says it is held):
  ```bash
  dc exec app /app/backend/.venv/bin/python -m polymath.budget_holds list
  ```
  This shows each hold's ID, kind and amount. Stop and report it. Eks checks
  the real charge in Meta's dashboard. Then settle it with that amount in
  microdollars (USD 0.01 = 10000; 0 if Meta billed nothing):
  ```bash
  dc exec app /app/backend/.venv/bin/python -m polymath.budget_holds settle <ID> --actual-microdollars <amount> --evidence meta-dashboard-<date> --confirm
  ```
  Nothing is ever resent.
- **To switch everything off:** set all four to false in one edit, then run
  `check` and `dc $ZEN up -d --wait`:
  ```
  PUBLIC_META_RESEARCH_ENABLED=false
  PAID_META_IMAGE_ENABLED=false
  PAID_META_RESEARCH_ENABLED=false
  PAID_META_PROBE_ENABLED=false
  ```
- **After judging** (after 16 Nov): **Stop this public funding**, then
  **Disable the shared account**, then switch everything off as above.

## Part C: the live test ladder (cheap; about USD 6–12 in total)

**Goal:** prove every feature works with real Meta intelligence, as cheaply as
possible, and save the results for the demo video. Run the tests **in order,
one at a time**, and stop at the first problem.

**Before C1:**
1. In the browser used for testing, open https://askpolymath.com and **Sign in**
   (for example with GitHub). Everything made while signed in is kept for a
   year and appears on any device, which makes it demo material.
2. On Eks's Mac, create a folder named **Polymath demo**. Save every download
   and screenshot into it, named with the test number (for example
   `C5-studio-dossier.html`).
3. Keep a second tab on the operator desk
   (https://askpolymath.com/operator/budget). After each test, press **Inspect
   account: polymath-meta** and then **Refresh this account**, and note:
   - **committed** (the total spent so far);
   - **Open reserved holds**;
   - **Unknown-outcome holds**.

**After every test, report in this format:**

```
C<n> <name>: WORKS / PROBLEM
What appeared: <one or two lines: fields, title, outcome, labels you saw>
Time taken: <minutes>
Spent so far: $<committed> · holds: open $<x>, unknown $<y>
Saved: <file names and links>
Anything odd: <exact text of any error, or "none">
```

**Stop immediately and report if:** any error appears, an unknown hold is above
$0, one test costs more than USD 3 (USD 5 for Studio), or the total passes
USD 15.

| Test | How | Working looks like | Save for the demo |
|---|---|---|---|
| **C1 Explore** | Research tab, **01 Explore**, type *self-organised criticality*, start. | Live activity shows searches and readings. It ends with **either** a bridge (two fields, a mapping, quoted passages, the strongest objection and its verdict) **or** an honest "not enough evidence". Both are valid. | Screenshot of the result. **Report after C1 and wait for Claude Code's OK** (it checks the real cost per call). |
| **C2 Ask** | **03 Ask**: *Why do phase transitions appear in how neural networks learn?* | Claims labelled Known, Postulated or Speculative, each with its own passages. | Screenshot. |
| **C3 Surprise me** | **04 Surprise me**, start. | The model picks distant fields itself and explains why. | Screenshot. |
| **C4 Solve** | **02 Solve**: *How could a battery keep its charge through ten thousand cycles without degrading?* | The lock and key picture; stages run one after another. The outcome is **found**, **forged**, **close fits** or **no key yet**, with evidence. | Screenshot of the outcome. |
| **C5 Studio** | **Studio** tab, tap **A submarine inspired by whales**, then **Invent it**. **Keep the page open** for about 10–25 minutes. | The progress line moves through Brief, Inspiration, Invention, Engineering and Stress test. The idea map fills; the invention appears with "Working:" calculations; then the gallery paints 5–7 pictures one by one. | **Download the design dossier** (`C5-studio-dossier.html`), open it, and **Print, then Save as PDF** (`C5-studio-dossier.pdf`). Screenshots of the top of the page and the gallery. **Report after C5.** |
| **C6 Converge** | Research, **05 Converge**. Theme *how systems remember*, **How many fields** 3, **Spending ceiling** 1.50, **Begin converging**. | The circle lights up as the fields connect. | Screenshot of the circle. |
| **C7 Frontier** | **Frontier** tab: *How could batteries be recycled without loss of capacity?*, start, **Run programme**. **Press Pause once 3 investigations have finished.** | A map of gaps and steps, each labelled by its evidence. Each step has a **Solve this step** button (don't press it). | Screenshot of the map. |
| **C8 Develop** | Open C1's finding (or any saved bridge) in **Your library**. Press **Formalise**, then **Write paper** with the conjecture included. | A conjecture with definitions, then a paper with exact citations. | Download the paper (`C8-paper`). |
| **C9 Publish** | On the paper's finding, **Publish this…**, choose **Zenodo sandbox** (connect it first in **Your library, Publishing** if needed), **Review the record**, then **Yes, publish**. | A sandbox record with a DOI link. Sandbox records are practice only. | The record link. |
| **C10 Share** | On the best finding, **Preview public snapshot**, then **Share this snapshot publicly**, then **Copy public link**. | A public page anyone can open, without signing in. | The public link. |

**Final report after C10:** list every test's result, the total spent, the
holds, the contents of the **Polymath demo** folder, and the public, Zenodo and
library links. Claude Code then reviews the quality and fixes anything, and
prepares **Part D: the demo run** (the most impressive investigations, recorded
for the video) and **Part E: judging** (generous visitor limits up to the
budget).

