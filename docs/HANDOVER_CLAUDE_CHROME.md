# Handover for Claude in Chrome (3 November 2026)

Three owner-only tasks before submission, done in the owner's browser with his logins: reopen free research with the judging delegation, settle one unknown ledger hold, and publish one finding to Zenodo sandbox as a visitor. The text below is pasted as the first message to Claude in Chrome.

Rules: never paste operator codes, cookies, tokens or secrets into chat; if a step looks different from the description, stop and ask; report each checkpoint in plain English.

## Task 1. Reopen free research for visitors (Operator desk)

The testing delegation (USD 40 public total) was spent on 2 November; visitors see "Funded research is currently closed for this browser". A new delegation with judging limits reopens the site. The public total counts money already spent under earlier delegations (about USD 39), so USD 100 leaves about USD 60 for judging; the account's USD 145 hard cap still applies.

1. Operator desk (footer link), "Unlock operator access"; the owner enters the code.
2. Tab "Public visitor funding".
3. "Inspect saved public funding": copy the `provider_account_attestation` block of the current delegation.
4. Choose the current attestation and the receipt.
5. Tick multi-bridge Explore (maximum 3) and scientific context.
6. Tick the three "Fresh human review" boxes.
7. Exact public funding deadline in UTC: `2026-11-17T08:00:00Z`.
8. Reviewed public funding JSON, with `observed_at` = UTC now minus 2 minutes, the saved attestation with `observed_at`, `valid_until` (`2026-11-20T00:00:00Z`) and the credit figures updated to the Meta dashboard's current values in microdollars:

```json
{
  "review": {"observed_at": "<UTC now minus 2 min>", "valid_until": "2026-11-20T00:00:00Z",
             "evidence": [{"url": "https://dev.meta.ai/docs/pricing-rate-limits", "content_sha256": "<from the saved copy>"}]},
  "provider_account_attestation": { "<the saved block, dates and credit figures updated>": "" },
  "public_limits": {
    "public_total_cap_microdollars": 100000000,
    "public_daily_cap_microdollars": 20000000,
    "session_total_cap_microdollars": 4000000,
    "session_daily_cap_microdollars": 3000000,
    "max_jobs_per_session_day": 12,
    "max_jobs_per_day": 150
  },
  "discovery": {"protocol": "polymath-surprise-ladder-v1", "max_stages": 3, "max_depth": 2}
}
```

   Judging limits: public USD 100 total and USD 20 a day; each visitor USD 4 total, USD 3 a day, 12 investigations a day; 150 a day overall; until 17 November 08:00 UTC.
9. "Preview exact public funding": check the limits and the deadline.
10. Tick the confirmation and "Activate THIS public funding delegation".
11. "Inspect saved public funding" shows the new delegation current.
12. "Lock operator access".
13. A fresh private window at https://askpolymath.com/ offers funded research with an allowance.

Checkpoint 1: the deadline shown, the allowance a fresh visitor sees, anything the preview rejected.

## Task 2. Settle one unknown ledger hold (GitHub Actions)

A model call timed out on 2 November with no usage report; the ledger reserves USD 1.38 for it. If Meta's dashboard for 2 November is consistent with no extra charge, settle at zero.

1. Note the 2 November usage on Meta's developer dashboard.
2. https://github.com/wonderben-code/polymath/actions/workflows/server.yml → Run workflow (main), task `settle-hold`, argument exactly: `92a34b74-9665-4528-81ae-65598f671ede 0 meta-dashboard-daily-2026-11-02`.
3. Wait for the green tick.

Checkpoint 2: the run URL and result, and the 2 November figure.

## Task 3. Publish one finding to Zenodo sandbox as a visitor (after Task 1)

1. Sign in on https://askpolymath.com/ (GitHub, Google or ORCID).
2. Ask: "Why does time only run forward?"; keep the tab open until the numbered claims appear (about two minutes).
3. Your library → the finding → Write a summary; keep the tab open until done.
4. Under the summary, "Publish to your Zenodo" → Zenodo sandbox (practice) → Connect; sign in at https://sandbox.zenodo.org and authorise.
5. Level "Publish to practice", read the exact record, Publish.
6. Wait for the record link; open it on sandbox.zenodo.org.

Checkpoint 3: the sandbox record URL, the finding's title, any failure with its exact message.

## Task 4. Final judging delegation (console, two steps)

The judging delegation of 3 November (USD 100 public total, USD 20 a day; USD 4 / USD 3 a visitor, 12 investigations a day) is active until 17 November 08:00 UTC. The final delegation replaces it with the owner's chosen limits: public total USD 125 (the ledger brake, below the real Meta credit), public daily USD 75 (the account's own daily ceiling, so no separate daily brake), each visitor USD 12 total and a day with 24 investigations a day, 200 a day overall, same deadline. The account caps (USD 145 total, USD 75 a day) are unchanged. The attestation timestamps below only need to be in the past; the server accepts the block any time before the deadline.

Paste this whole block as one message to Claude in Chrome:

```
Activate Polymath's final judging delegation through the API from the page console (same method as this morning). Rules: never paste the operator code, cookies or tokens into chat; report the PREVIEW and ACTIVE lines verbatim; stop and report if a step errors.

1. Open https://askpolymath.com/operator/budget, press "Unlock operator access" and let Eks enter the operator code (paste it, no spaces). Confirm "Signed in as Owner until …".
2. In the page's JavaScript console run STEP A. It only previews; nothing is activated yet.
3. Check the PREVIEW line says: deadline 2026-11-17T08:00:00Z | public total 125000000 daily 75000000 | visitor total 12000000 daily 12000000 | jobs/visitor/day 24 | jobs/day 200. If so, run STEP B. Otherwise report the line verbatim and stop.
4. STEP B activates and prints "ACTIVE:". Report it.
5. Press "Lock operator access".

STEP A:

(async () => {
  const j = async (path, body, extra) => {
    const r = await fetch(path, { credentials: 'same-origin', cache: 'no-store', method: body ? 'POST' : 'GET', headers: { Accept: 'application/json', ...(body ? { 'Content-Type': 'application/json' } : {}), ...(extra || {}) }, body: body ? JSON.stringify(body) : undefined });
    const t = await r.text(); let v; try { v = JSON.parse(t) } catch { v = t }
    if (!r.ok) throw new Error(path + ' -> HTTP ' + r.status + ' ' + JSON.stringify(v).slice(0, 300));
    return v;
  };
  const browser = await j('/api/v1/session');
  const op = await j('/api/v1/operator/session');
  if (!op.authenticated || !op.csrf_token) throw new Error('Operator access is locked. Unlock first.');
  const H = { 'X-CSRF-Token': browser.csrf_token, 'X-Operator-CSRF-Token': op.csrf_token };
  const readiness = (await j('/api/v1/budget/readiness?limit=5')).proposals.find((p) => p.temporal_status === 'current');
  if (!readiness) throw new Error('No current readiness proposal found.');
  const jobs = (await j('/api/v1/budget/verification/jobs?limit=5')).jobs;
  const canary = jobs.find((x) => x.receipt && x.native_status === 'passed') || jobs.find((x) => x.receipt);
  if (!canary) throw new Error('No passed verification receipt found.');
  const body = {
    readiness_id: readiness.id,
    canary_receipt_id: canary.receipt.id,
    valid_until: '2026-11-17T08:00:00Z',
    review: {
      observed_at: '2026-11-03T13:40:00Z', valid_until: '2026-11-20T00:00:00Z',
      evidence: [
        { url: 'https://dev.meta.ai/docs/pricing-rate-limits', content_sha256: '607ad0b36382b69c02af48b210f22cb4e874ea167c2e0dd2e16504cafb9de5aa' },
        { url: 'https://dev.meta.ai/docs/models', content_sha256: '88d55e1cca6434d72be3766c96edc6930f29e2d5183cb676307a3410fd1abe27' }
      ],
      funding_cap_confirmed: true, pricing_bounds_confirmed: true, model_wire_usage_confirmed: true
    },
    provider_account_attestation: {
      provider_billing_account_id: '1610449200134358', model_id: 'muse-spark-1.3', currency: 'USD',
      usage_contract: 'prompt_completion_inclusive_v1',
      observed_at: '2026-11-03T13:40:00Z', valid_until: '2026-11-20T00:00:00Z',
      available_credit_microdollars: 129170000, remaining_provider_allowance_microdollars: 129170000,
      provider_hard_cap_microdollars: 150000000,
      evidence: [{ url: 'https://dev.meta.ai/billing', content_sha256: 'dbead56f687c96d4790fe3de7fa241d68c5963718d10b7d7eb4b71de9bd88322' }]
    },
    public_limits: {
      public_total_cap_microdollars: 125000000, public_daily_cap_microdollars: 75000000,
      session_total_cap_microdollars: 12000000, session_daily_cap_microdollars: 12000000,
      max_jobs_per_session_day: 24, max_jobs_per_day: 200
    },
    discovery: { protocol: 'polymath-surprise-ladder-v1', max_stages: 3, max_depth: 2 },
    explore_collection: { protocol: 'polymath-explore-collection-v1', max_bridges: 3 },
    scientific_context: { protocol: 'polymath-scientific-context-profiles-v1' }
  };
  const preview = await j('/api/v1/budget/research/public/preview', body, H);
  window.__polymathPreview = preview; window.__polymathHeaders = H;
  const m = preview.manifest, L = m.public_limits;
  console.log('PREVIEW: deadline ' + m.valid_until + ' | public total ' + L.public_total_cap_microdollars + ' daily ' + L.public_daily_cap_microdollars + ' | visitor total ' + L.session_total_cap_microdollars + ' daily ' + L.session_daily_cap_microdollars + ' | jobs/visitor/day ' + L.max_jobs_per_session_day + ' | jobs/day ' + L.max_jobs_per_day + ' | sha ' + preview.manifest_sha256.slice(0, 12));
})().catch((e) => console.log('ERROR: ' + e.message));

STEP B (only after the PREVIEW line matched):

(async () => {
  const p = window.__polymathPreview, H = window.__polymathHeaders;
  if (!p || !H) throw new Error('Run STEP A first.');
  const r = await fetch('/api/v1/budget/research/public/activate', { credentials: 'same-origin', cache: 'no-store', method: 'POST', headers: { Accept: 'application/json', 'Content-Type': 'application/json', ...H }, body: JSON.stringify({ manifest: p.manifest, expected_manifest_sha256: p.manifest_sha256 }) });
  const t = await r.text(); let v; try { v = JSON.parse(t) } catch { v = t }
  if (!r.ok) throw new Error('activate -> HTTP ' + r.status + ' ' + JSON.stringify(v).slice(0, 300));
  console.log('ACTIVE: id ' + v.id + ' | current ' + v.delegation_current + ' | deadline ' + v.manifest.valid_until + ' | sha ' + v.manifest_sha256.slice(0, 12));
})().catch((e) => console.log('ERROR: ' + e.message));
```

Checkpoint 4: the ACTIVE line. A fresh private window then shows a USD 12 allowance.

## During judging

Nothing to do. A model call that times out used to leave an "unknown" hold for a person to settle; since 3 Nov the research worker settles such holds itself at their full reserved amount within seconds, so the site never closes over them and no daily check is needed. The money protection is unchanged: the ledger counts the maximum the call could have cost.
