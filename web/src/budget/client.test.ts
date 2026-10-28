import { afterEach, describe, expect, it, vi } from 'vitest'
import { BudgetError, disableBudgetAccount, getBudgetAccount, getReadinessProposal, listBudgetAccounts, listReadinessProposals, loadBudgetCapabilities, parseBudgetAccount, parseBudgetCapabilities, parseReadinessPreview, parseReadinessProposal, parseReadinessRecord, previewReadinessProposal, saveReadinessProposal } from './client'
import { ACCOUNT_ID, account, BROWSER_CSRF, capabilities, OPERATOR_CSRF, preview, proposal, record, RECORD_ID } from './test-support'
import { budgetMoney } from './ProposalDetails'

afterEach(() => vi.unstubAllGlobals())
describe('disabled readiness contracts', () => {
  it('checks separate component rounding and the honest full-run budget-limited result', () => {
    const parsed = parseReadinessPreview(preview())
    expect(parsed.input_reservation_microdollars).toBe(1); expect(parsed.output_reservation_microdollars).toBe(1)
    expect(parsed.per_call_reservation_microdollars).toBe(2); expect(parsed.max_run_reservation_microdollars).toBe(6)
    expect(parsed.full_run_reservation_fits).toBe(false); expect(parsed.budget_limited_stop_notice).not.toBeNull()
  })
  it.each(['native_status', 'execution_available'] as const)('never accepts upgraded %s authority in a proposal or quote', (field) => {
    const changed = { ...proposal(), [field]: field === 'native_status' ? 'verified' : true }
    expect(() => parseReadinessProposal(changed)).toThrow(BudgetError)
    expect(() => parseReadinessPreview({ ...preview(), [field]: changed[field] })).toThrow(BudgetError)
  })
  it.each(['model_id', 'billable_bounds', 'pricing', 'usage_evidence', 'account', 'observed_at', 'valid_until'])('requires explicit reviewed %s rather than guessing missing facts', (key) => {
    const changed = { ...proposal() } as Record<string, unknown>; delete changed[key]
    expect(() => parseReadinessProposal(changed)).toThrow('invalid_response')
  })
  it('rejects unsupported billing, missing evidence, guessed ratio fields and policy cap excesses', () => {
    expect(() => parseReadinessProposal({ ...proposal(), usage_contract: 'visible_output_only' })).toThrow()
    expect(() => parseReadinessProposal({ ...proposal(), usage_evidence: [] })).toThrow()
    expect(() => parseReadinessProposal({ ...proposal(), billable_bounds: { ...proposal().billable_bounds, characters_per_token: 4 } })).toThrow()
    expect(() => parseReadinessProposal({ ...proposal(), account: { ...proposal().account, hard_cap_microdollars: 150000001 } })).toThrow()
  })
  it('rejects altered monetary calculations and inconsistent immutable record binding', () => {
    expect(() => parseReadinessPreview({ ...preview(), per_call_reservation_microdollars: 1 })).toThrow()
    expect(() => parseReadinessPreview({ ...preview(), full_run_reservation_fits: true })).toThrow()
    expect(() => parseReadinessRecord({ ...record(), account_id: 'different-account' })).toThrow()
    expect(() => parseReadinessRecord({ ...record(), proposal_sha256: 'f'.repeat(64) })).toThrow()
  })
  it('retains documentary expiry without turning an immutable attestation into execution authority', () => {
    const saved = parseReadinessRecord(record(undefined, { temporal_status: 'expired' }))
    expect(saved.temporal_status).toBe('expired'); expect(saved.execution_available).toBe(false)
    expect(() => parseReadinessRecord({ ...record(), temporal_status: 'ready' })).toThrow()
  })
  it('retains large conservative money totals exactly and displays every microdollar without Number conversion', () => {
    const total = '900719925474099312345', current = account({ usage: { ...account().usage, committed_microdollars: total, counted_spend_microdollars: (BigInt(total) - 10n).toString() } })
    expect(parseBudgetAccount(current).usage.committed_microdollars).toBe(total)
    expect(budgetMoney(total)).toBe('$900,719,925,474,099.312345')
    expect(budgetMoney('1')).toBe('$0.000001')
    expect(() => parseBudgetAccount({ ...current, usage: { ...current.usage, committed_microdollars: 9007199254740993 } })).toThrow()
    expect(() => parseBudgetAccount({ ...current, usage: { ...current.usage, committed_microdollars: '001' } })).toThrow()
  })
  it('keeps prior enabled/pricing/frozen flags as actual ledger facts, distinct from native readiness', () => {
    const actual = parseBudgetAccount(account({ execution_enabled: true, pricing_verified: true, pricing_reference: 'Synthetic prior ledger fact', kill_switch: false, frozen: true, frozen_reason: 'synthetic_unknown_hold' }))
    expect(actual.execution_enabled).toBe(true); expect(actual.pricing_verified).toBe(true); expect(actual.frozen).toBe(true)
    expect(actual.native_status).toBe('not_run'); expect(actual.execution_available).toBe(false)
  })
  it('accepts only internally consistent default and inspection-only capabilities', () => {
    expect(parseBudgetCapabilities(capabilities('off')).preparation_available).toBe(false)
    expect(parseBudgetCapabilities(capabilities('inspect')).disable_available).toBe(true)
    expect(() => parseBudgetCapabilities({ ...capabilities(), execution_available: true })).toThrow()
    expect(() => parseBudgetCapabilities({ ...capabilities('off'), preparation_available: true })).toThrow()
  })
})
describe('bounded operator budget transport', () => {
  it('posts preview/save only with both CSRF tokens and saves the exact displayed proposal/hash', async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL) => Response.json(input.toString().endsWith('/preview') ? preview() : record())); vi.stubGlobal('fetch', fetch)
    const quote = await previewReadinessProposal(proposal(), BROWSER_CSRF, OPERATOR_CSRF)
    await saveReadinessProposal({ proposal: quote.proposal, expected_proposal_sha256: quote.proposal_sha256 }, BROWSER_CSRF, OPERATOR_CSRF)
    expect(fetch.mock.calls[0][0]).toBe('/api/v1/budget/readiness/preview')
    expect(fetch.mock.calls[1]).toEqual(['/api/v1/budget/readiness', expect.objectContaining({ method: 'POST', body: JSON.stringify({ proposal: quote.proposal, expected_proposal_sha256: quote.proposal_sha256 }), headers: expect.objectContaining({ 'X-CSRF-Token': BROWSER_CSRF, 'X-Operator-CSRF-Token': OPERATOR_CSRF }) })])
  })
  it('accepts only date/URL normalization in an authoritative preview while preserving exact reviewed numerical/model facts', async () => {
    const entered = proposal(); entered.observed_at = '2026-10-28T00:00:00.000+00:00'; entered.valid_until = '2026-11-04T00:00:00.000+00:00'; entered.usage_evidence[0].url = 'https://fixtures.example.test'
    const normalized = structuredClone(entered); normalized.observed_at = '2026-10-28T00:00:00Z'; normalized.valid_until = '2026-11-04T00:00:00Z'; normalized.usage_evidence[0].url += '/'
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(preview(normalized))))
    const result = await previewReadinessProposal(entered, BROWSER_CSRF, OPERATOR_CSRF)
    expect(result.proposal).toEqual(normalized)
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(preview({ ...normalized, model_id: 'different-model' }))))
    await expect(previewReadinessProposal(entered, BROWSER_CSRF, OPERATOR_CSRF)).rejects.toThrow('invalid_response')
  })
  it.each([[401, 'research_session_required', 'browser_expired'], [401, 'operator_session_required', 'operator_expired'], [403, 'operator_csrf_check_failed', 'operator_expired'], [403, 'csrf_check_failed', 'browser_expired'], [403, 'readiness_actor_forbidden', 'readiness_actor_forbidden'], [503, 'readiness_role_unavailable', 'readiness_role_unavailable']] as const)('keeps %s/%s separate as %s', async (status, detail, expected) => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ detail }, { status })))
    await expect(listBudgetAccounts()).rejects.toMatchObject({ code: expected })
  })
  it('marks a lost or malformed save receipt uncertain without inventing activation or idempotency fields', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Synthetic network loss') }))
    await expect(saveReadinessProposal({ proposal: proposal(), expected_proposal_sha256: preview().proposal_sha256 }, BROWSER_CSRF, OPERATOR_CSRF)).rejects.toMatchObject({ uncertain: true })
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ unexpected: 'Synthetic malformed write receipt' })))
    await expect(saveReadinessProposal({ proposal: proposal(), expected_proposal_sha256: preview().proposal_sha256 }, BROWSER_CSRF, OPERATOR_CSRF)).rejects.toMatchObject({ code: 'invalid_response', uncertain: true })
  })
  it('reads histories/accounts with GET only and never fetches an evidence URL', async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL) => { const path = input.toString(); return Response.json(path.endsWith('/capabilities') ? capabilities() : path.includes('/accounts?') ? { accounts: [account()], next_cursor: null } : path.includes('/readiness?') ? { proposals: [record()], next_cursor: null } : path.endsWith(RECORD_ID) ? record() : account()) }); vi.stubGlobal('fetch', fetch)
    await loadBudgetCapabilities(); await listReadinessProposals(); await listBudgetAccounts(); await getReadinessProposal(RECORD_ID); await getBudgetAccount(ACCOUNT_ID)
    expect(fetch.mock.calls).toHaveLength(5)
    for (const [path, options] of fetch.mock.calls as unknown as [string, RequestInit][]) { expect(path.startsWith('/api/v1/budget/')).toBe(true); expect(options.method).toBe('GET') }
  })
  it('disables only the exact encoded account, preserving actual money/frozen state in the receipt', async () => {
    const id = 'legacy/account', actual = account({ account_id: id, frozen: true, frozen_reason: 'synthetic_unknown_hold' })
    const fetch = vi.fn(async () => Response.json(actual)); vi.stubGlobal('fetch', fetch)
    expect(await disableBudgetAccount(id, BROWSER_CSRF, OPERATOR_CSRF)).toEqual(actual)
    expect(fetch.mock.calls[0]).toEqual([`/api/v1/budget/accounts/${encodeURIComponent(id)}/disable`, expect.objectContaining({ method: 'POST', body: '{}' })])
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ ...actual, kill_switch: false })))
    await expect(disableBudgetAccount(id, BROWSER_CSRF, OPERATOR_CSRF)).rejects.toMatchObject({ uncertain: true })
  })
})
