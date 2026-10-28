import { afterEach, describe, expect, it, vi } from 'vitest'
import { activatePublicResearch, getPublicActivation, listPublicActivations, loadPublicResearchCapabilities, parsePublicLimits, parsePublicPreviewRequest, parsePublicResearchActivation, parsePublicResearchManifest, previewPublicResearch, revokePublicActivation } from './publicActivationClient'
import { publicCapabilities, publicLimits, publicManifest, publicPreview, publicPreviewRequest, publicRecord } from './publicActivationFixtures.test-support'
import { BROWSER_CSRF, OPERATOR_CSRF } from './test-support'
import { researchManifest } from './activationFixtures'
const BASE = '/api/v1/budget/research/public'
afterEach(() => vi.unstubAllGlobals())
describe('exact durable public delegation protocol', () => {
  it('keeps a separate public protocol, durable confirmation and all ordinary task bindings', () => {
    expect(parsePublicResearchManifest(publicManifest())).toEqual(publicManifest())
    expect(() => parsePublicResearchManifest(researchManifest())).toThrow()
    for (const value of [false, 1, 'true', undefined]) expect(() => parsePublicResearchManifest({ ...publicManifest(), continues_after_operator_logout: value })).toThrow()
    for (const [key, value] of [['protocol', 'polymath-meta-research-activation-v1'], ['audience', 'operator_browser'], ['authorization_mode', 'browser'], ['clear_kill_switch', false], ['public_policy_sha256', 'wrong'], ['sponsor_policy_sha256', 'wrong']] as const) expect(() => parsePublicResearchManifest({ ...publicManifest(), [key]: value })).toThrow()
  })
  it('accepts issuer-independent public expiry and rejects truncated approval deadlines', () => {
    const record = publicRecord(); expect(parsePublicResearchActivation(record).approval.expires_at).toBe('2026-10-28T12:00:00Z')
    expect(parsePublicResearchActivation({ ...record, approval: { ...record.approval, expires_at: '2026-10-28T12:00:00+00:00' } }).approval.expires_at).toBe('2026-10-28T12:00:00+00:00')
    expect(() => parsePublicResearchActivation({ ...record, approval: { ...record.approval, expires_at: '2026-10-28T00:05:00Z' } })).toThrow()
    expect(() => parsePublicResearchActivation({ ...record, eligible_for_current_browser: true })).toThrow()
  })
  it.each(['public_total_cap_microdollars', 'public_daily_cap_microdollars', 'session_total_cap_microdollars', 'session_daily_cap_microdollars'])('requires explicit positive safe integer money for %s', (field) => {
    for (const value of [undefined, 0, -1, '500', 150000001, 1.5, Number.MAX_SAFE_INTEGER + 1]) expect(() => parsePublicLimits({ ...publicLimits(), [field]: value })).toThrow()
  })
  it('binds nested caps, quotas and conservative full-call room to the existing shared ledger', () => {
    for (const change of [{ session_daily_cap_microdollars: 10001 }, { session_total_cap_microdollars: 50001 }, { max_active_jobs_per_session: true }, { max_jobs_per_day: 1 }, { max_active_jobs: 1 }, { resources: { ...publicLimits().resources, read_session_per_minute: 61 } }]) expect(() => parsePublicLimits({ ...publicLimits(), ...change })).toThrow()
    expect(() => parsePublicResearchManifest({ ...publicManifest(), public_limits: { ...publicLimits(), session_daily_cap_microdollars: 1 } })).toThrow()
  })
  it('supplies only documented operational ceilings when omitted, never money or dates', () => {
    const limits = publicLimits(), money = { public_total_cap_microdollars: limits.public_total_cap_microdollars, public_daily_cap_microdollars: limits.public_daily_cap_microdollars, session_total_cap_microdollars: limits.session_total_cap_microdollars, session_daily_cap_microdollars: limits.session_daily_cap_microdollars }
    expect(parsePublicLimits(money, true)).toEqual(limits)
    expect(() => parsePublicPreviewRequest({ ...publicPreviewRequest(), valid_until: '' })).toThrow()
    expect(() => parsePublicPreviewRequest({ ...publicPreviewRequest(), continues_after_operator_logout: true })).toThrow()
  })
  it('uses anonymous no-DB capabilities without a role or credential grant', async () => {
    const fetch = vi.fn(async (_path: RequestInfo | URL, _options?: RequestInit) => Response.json(publicCapabilities('off'))); vi.stubGlobal('fetch', fetch)
    expect(await loadPublicResearchCapabilities()).toEqual(publicCapabilities('off'))
    expect(fetch.mock.calls[0]).toEqual([`${BASE}/capabilities`, expect.objectContaining({ method: 'GET', credentials: 'omit' })])
  })
  it('accepts timestamp and evidence URL normalization without changing model, money or limits', async () => {
    const body = publicPreviewRequest(); body.valid_until = '2026-10-28T12:00:00.000Z'; body.review.observed_at = '2026-10-28T00:00:00.000Z'; body.review.evidence[0].url = 'https://fixtures.example.test'
    const preview = publicPreview(); preview.manifest.review.evidence[0].url = 'https://fixtures.example.test/'
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(preview)))
    expect((await previewPublicResearch(body, BROWSER_CSRF, OPERATOR_CSRF)).manifest.valid_until).toBe('2026-10-28T12:00:00Z')
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ ...preview, manifest: { ...preview.manifest, public_limits: { ...preview.manifest.public_limits, max_jobs_per_session_day: 1 } } })))
    await expect(previewPublicResearch(body, BROWSER_CSRF, OPERATOR_CSRF)).rejects.toMatchObject({ code: 'invalid_response', uncertain: true })
  })
  it('posts the exact displayed manifest/hash once with both CSRFs and no scientific enqueue', async () => {
    const record = publicRecord(), body = { manifest: record.manifest, expected_manifest_sha256: record.manifest_sha256 }
    const fetch = vi.fn(async (_path: RequestInfo | URL, _options?: RequestInit) => Response.json(record)); vi.stubGlobal('fetch', fetch)
    expect(await activatePublicResearch(body, BROWSER_CSRF, OPERATOR_CSRF)).toEqual(record)
    expect(fetch.mock.calls).toEqual([[`${BASE}/activate`, expect.objectContaining({ method: 'POST', body: JSON.stringify(body), headers: expect.objectContaining({ 'X-CSRF-Token': BROWSER_CSRF, 'X-Operator-CSRF-Token': OPERATOR_CSRF }) })]])
  })
  it.each([['research_session_required', 401, 'browser_expired'], ['operator_session_required', 401, 'operator_expired'], ['operator_csrf_check_failed', 403, 'operator_expired'], ['readiness_role_unavailable', 503, 'readiness_role_unavailable'], ['public_research_actor_forbidden', 403, 'public_research_actor_forbidden']])('keeps %s distinct from funding closure', async (detail, status, code) => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ detail }, { status: status as number })))
    await expect(getPublicActivation(publicRecord().id)).rejects.toMatchObject({ code })
  })
  it('marks lost commit/malformed saves uncertain and never automatically resends', async () => {
    const record = publicRecord(), fetch = vi.fn(async () => { throw new TypeError('synthetic commit response loss') }); vi.stubGlobal('fetch', fetch)
    await expect(activatePublicResearch({ manifest: record.manifest, expected_manifest_sha256: record.manifest_sha256 }, BROWSER_CSRF, OPERATOR_CSRF)).rejects.toMatchObject({ uncertain: true }); expect(fetch).toHaveBeenCalledTimes(1)
  })
  it('uses owned history and ID-bound stop receipts even while activation is disabled', async () => {
    const record = publicRecord(), stopped = { ...record, delegation_current: false, approval: { ...record.approval, valid: false, revoked_at: record.created_at } }
    const fetch = vi.fn(async (path: RequestInfo | URL) => Response.json(path.toString().includes('?') ? { activations: [record], next_cursor: null } : stopped)); vi.stubGlobal('fetch', fetch)
    expect((await listPublicActivations()).activations).toEqual([record]); expect(await revokePublicActivation(record.id, BROWSER_CSRF, OPERATOR_CSRF)).toEqual(stopped)
    expect(fetch).toHaveBeenCalledTimes(2)
  })
})
