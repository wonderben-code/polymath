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

## During judging: a two-minute daily check (3 to 17 November)

A model call that times out leaves an "unknown" hold: its money stays reserved at the maximum, and after three such holds the site closes new investigations until a person settles them (activation of a new delegation refuses while even one stands). Nobody settles automatically, by design. Once a day:

1. GitHub Actions → Server task → Run workflow (main), task `holds`. The output is encrypted for Claude Code; paste it to Claude Code, or simply ask Claude Code "any unknown holds?".
2. For each hold listed as `unknown`, check the Meta dashboard's daily usage for that day, then run the Server task `settle-hold` with the argument `<hold id> 0 meta-dashboard-daily-<YYYY-MM-DD>` (the id is the first field of the holds line).

Claude Code also reads the holds at its own check-ins while its session is alive and will say when one needs settling.
