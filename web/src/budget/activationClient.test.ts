import { afterEach, describe, expect, it, vi } from 'vitest'
import { BudgetError, activateResearch, getCurrentResearchActivation, getResearchActivation, listResearchActivations, loadResearchCapabilities, parseCurrentResearchActivation, parseMetaResearchActivation, parseMetaResearchCapabilities, parseMetaResearchManifest, parseMetaResearchPreview, parseNativeResearchReview, parseNativeTaskProfile, previewResearchActivation, revokeResearchActivation } from './activationClient'
import { ACTIVATION_ID, BROWSER_CSRF, OPERATOR_CSRF, READINESS_ID, RECEIPT_ID, activationCapabilities, activationHistory, activationPreview, activationPreviewRequest, activationRequest, currentActivation, researchActivation, researchManifest, researchReview, taskProfile } from './activationFixtures'

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

describe('same-browser native research activation contracts', () => {
  it('keeps the exact reviewed proposal, historical canary and five separate scientific task profiles', () => {
    const parsed = parseMetaResearchManifest(researchManifest())
    expect(parsed.audience).toBe('operator_browser')
    expect(parsed.proposal.native_status).toBe('not_run'); expect(parsed.proposal.execution_available).toBe(false)
    expect(parsed.task_profiles.map(({ profile }) => [profile.task_kind, profile.max_model_calls])).toEqual([['research', 20], ['formalise', 12], ['write_summary', 12], ['write_paper', 12], ['reassess_finding', 12]])
    for (const { profile } of parsed.task_profiles) { expect(profile.max_output_tokens).toBe(4096); expect(profile.max_context_characters).toBe(90000); expect(profile.request_timeout_seconds).toBe(45.5); expect(profile.model_timeout_seconds).toBe(75.5); expect(profile.controller_sha256).toMatch(/^[a-f0-9]{64}$/) }
    expect(parseMetaResearchPreview(activationPreview()).research_execution_available).toBe(false)
  })
  it('accepts default-off and inspection-only policy without exposing actor, credentials, or public spend authority', () => {
    for (const mode of ['off', 'inspect', 'activate'] as const) expect(parseMetaResearchCapabilities(activationCapabilities(mode))).toEqual(activationCapabilities(mode))
    expect(() => parseMetaResearchCapabilities({ ...activationCapabilities(), public_research_available: true })).toThrow()
    expect(() => parseMetaResearchCapabilities({ ...activationCapabilities(), audience: 'public_session' })).toThrow()
    expect(() => parseMetaResearchCapabilities({ ...activationCapabilities(), api_key: 'Synthetic secret' })).toThrow()
    expect(() => parseMetaResearchCapabilities({ ...activationCapabilities('off'), activation_available: true })).toThrow()
  })
  it.each(['model_wire_usage_confirmed', 'pricing_bounds_confirmed', 'funding_cap_confirmed'])('requires a fresh explicit true confirmation for %s', (field) => {
    for (const value of [false, 1, 'true', undefined]) expect(() => parseNativeResearchReview({ ...researchReview(), [field]: value })).toThrow(BudgetError)
  })
  it('requires complete UTC review dates, bounded distinct evidence and no credential upload', () => {
    const value = researchReview()
    expect(() => parseNativeResearchReview({ ...value, evidence: [] })).toThrow()
    expect(() => parseNativeResearchReview({ ...value, evidence: [value.evidence[0], value.evidence[0]] })).toThrow()
    expect(() => parseNativeResearchReview({ ...value, observed_at: '2026-02-30T00:00:00Z' })).toThrow()
    expect(() => parseNativeResearchReview({ ...value, valid_until: value.observed_at })).toThrow()
    expect(() => parseNativeResearchReview({ ...value, observed_at: '2026-10-28T01:00:00+01:00' })).toThrow()
    expect(() => parseNativeResearchReview({ ...value, credential_binding_sha256: 'a'.repeat(64) })).toThrow()
    for (const url of ['http://fixtures.example.test/review', 'https://secret@fixtures.example.test/review', 'https://fixtures.example.test/review?key=secret', 'https://fixtures.example.test/a/../review']) expect(() => parseNativeResearchReview({ ...value, evidence: [{ ...value.evidence[0], url }] })).toThrow()
  })
  it.each(['account_id', 'model_id', 'credential_binding_sha256'])('binds canary %s to the same exact model, account and credential', (field) => {
    const value = researchManifest(), changed = field === 'credential_binding_sha256' ? 'f'.repeat(64) : 'different-synthetic-identity'
    expect(() => parseMetaResearchManifest({ ...value, canary: { ...value.canary, [field]: changed } })).toThrow()
  })
  it('refuses failed probes, alternate endpoints/audiences and an unreviewed kill-clear', () => {
    const value = researchManifest()
    expect(() => parseMetaResearchManifest({ ...value, canary: { ...value.canary, status: 'unknown' } })).toThrow()
    expect(() => parseMetaResearchManifest({ ...value, endpoint: 'https://other.example.test/completions' })).toThrow()
    expect(() => parseMetaResearchManifest({ ...value, canary: { ...value.canary, endpoint: 'https://other.example.test/completions' } })).toThrow()
    expect(() => parseMetaResearchManifest({ ...value, audience: 'public_session' })).toThrow()
    expect(() => parseMetaResearchManifest({ ...value, clear_kill_switch: false })).toThrow()
    expect(() => parseMetaResearchManifest({ ...value, native_control_epoch: Number.MAX_SAFE_INTEGER })).toThrow()
  })
  it('requires all five uniquely ordered profiles and rejects attempts to include free ingestion or verification', () => {
    const value = researchManifest()
    for (const task_profiles of [value.task_profiles.slice(0, 4), [...value.task_profiles, value.task_profiles[0]], [...value.task_profiles].reverse(), [value.task_profiles[0], value.task_profiles[0], ...value.task_profiles.slice(2)]]) expect(() => parseMetaResearchManifest({ ...value, task_profiles })).toThrow()
    for (const task_kind of ['verify_meta', 'ingest_literature']) expect(() => parseNativeTaskProfile({ ...taskProfile(), task_kind })).toThrow()
  })
  it('keeps full reviewed output/context and HTTP-plus-orchestration limits within each original profile', () => {
    const value = researchManifest(), binding = value.task_profiles[0]
    for (const changed of [{ max_output_tokens: 1200 }, { max_context_characters: 1000 }, { request_timeout_seconds: 44, model_timeout_seconds: 74 }]) expect(() => parseMetaResearchManifest({ ...value, task_profiles: [{ ...binding, profile: { ...binding.profile, ...changed } }, ...value.task_profiles.slice(1)] })).toThrow()
    for (const changed of [{ max_model_calls: 21 }, { max_actions: 17 }, { max_repairs: 3 }, { request_protocol: 'legacy-request' }, { model_timeout_seconds: 60 }, { tool_timeout_seconds: 5 }, { max_sources: 201 }, { controller_sha256: 'not-a-hash' }]) expect(() => parseNativeTaskProfile({ ...taskProfile(), ...changed })).toThrow()
    expect(() => parseNativeTaskProfile({ ...taskProfile('formalise'), max_model_calls: 13 })).toThrow()
  })
  it('preserves original historical controller hashes and smaller supported call profiles without recomputing today’s code', () => {
    const value = researchManifest(), task_profiles = value.task_profiles.map((binding) => ({ ...binding, profile: { ...binding.profile, max_model_calls: 3, controller_protocol: 'polymath-historical-controller-v0', controller_sha256: 'c'.repeat(64) } }))
    const parsed = parseMetaResearchActivation(researchActivation({ research_execution_available: false, eligible_for_current_browser: false, approval: { ...researchActivation().approval, valid: false } }, { ...value, task_profiles }))
    expect(parsed.manifest.task_profiles[0].profile.controller_protocol).toBe('polymath-historical-controller-v0')
    expect(parsed.manifest.task_profiles[0].profile.controller_sha256).toBe('c'.repeat(64))
    expect(parsed.research_execution_available).toBe(false)
  })
  it('limits authority to all proposal, review and provider deadlines and the current attested funds', () => {
    const value = researchManifest()
    for (const changed of [{ valid_until: '2026-11-05T00:00:00Z' }, { valid_until: value.review.observed_at }, { review: { ...value.review, valid_until: '2026-10-28T00:04:00Z' } }, { provider_account_attestation: { ...value.provider_account_attestation, available_credit_microdollars: 1 } }, { provider_account_attestation: { ...value.provider_account_attestation, provider_hard_cap_microdollars: 1000, remaining_provider_allowance_microdollars: 1000 } }]) expect(() => parseMetaResearchManifest({ ...value, ...changed })).toThrow()
    const exact = researchManifest({ valid_until: '2026-10-28T00:00:00.000002Z', review: researchReview({ observed_at: '2026-10-28T00:00:00.000001Z' }) })
    expect(parseMetaResearchManifest(exact).valid_until).toBe(exact.valid_until)
  })
  it('exposes only the eligible current-browser projection or a clear null reason, never a foreign activation', () => {
    const current = parseCurrentResearchActivation(currentActivation())
    expect(current.activation?.manifest_sha256).toBe(researchActivation().manifest_sha256)
    expect(current.activation).not.toHaveProperty('manifest'); expect(current.activation).not.toHaveProperty('account_id')
    for (const reason of ['meta_research_activation_required', 'meta_research_authority_expired', 'meta_research_unavailable', 'meta_research_prior_unresolved'] as const) expect(parseCurrentResearchActivation(currentActivation(null, reason))).toEqual({ activation: null, reason })
    expect(() => parseCurrentResearchActivation({ activation: null, reason: null })).toThrow()
    expect(() => parseCurrentResearchActivation({ ...currentActivation(), reason: 'meta_research_authority_expired' })).toThrow()
    expect(() => parseCurrentResearchActivation({ activation: { ...current.activation, research_execution_available: false }, reason: null })).toThrow()
    expect(() => parseCurrentResearchActivation({ activation: { ...current.activation, owner_hash: 'private-owner' }, reason: null })).toThrow()
  })
  it('distinguishes immutable historical receipts from current eligibility and rejects contradictory approval facts', () => {
    const value = researchActivation()
    expect(parseMetaResearchActivation(value).research_execution_available).toBe(true)
    expect(() => parseMetaResearchActivation({ ...value, eligible_for_current_browser: false })).toThrow()
    expect(() => parseMetaResearchActivation({ ...value, approval: { ...value.approval, valid: false } })).toThrow()
    expect(() => parseMetaResearchActivation({ ...value, approval: { ...value.approval, revoked_at: value.created_at } })).toThrow()
    expect(() => parseMetaResearchPreview({ ...activationPreview(), research_execution_available: true })).toThrow()
    const revoked = { ...value, approval: { ...value.approval, revoked_at: '2026-10-28T00:01:00Z', valid: false }, eligible_for_current_browser: false, research_execution_available: false }
    expect(parseMetaResearchActivation(revoked).approval.revoked_at).toBe(revoked.approval.revoked_at)
  })
  it('preserves the exact server timestamp bytes and trailing microsecond zeros throughout an immutable manifest', () => {
    const value = researchManifest({ valid_until: '2026-10-28T00:05:00.123000Z' })
    value.review.observed_at = '2026-10-28T00:00:00.123000Z'; value.review.valid_until = '2026-10-29T00:00:00.000000Z'
    value.provider_account_attestation.observed_at = '2026-10-28T00:00:00.123000+00:00'; value.provider_account_attestation.valid_until = '2026-10-29T00:00:00.000000+00:00'
    expect(parseMetaResearchPreview(activationPreview(value)).manifest).toEqual(value)
  })
})

describe('bounded private research activation transport', () => {
  it('gets anonymous capabilities and the separate current projection without creating a session or calling a provider', async () => {
    const fetch = vi.fn(async (path: RequestInfo | URL, _options?: RequestInit) => Response.json(path.toString().endsWith('/capabilities') ? activationCapabilities('off') : currentActivation(null))); vi.stubGlobal('fetch', fetch)
    await loadResearchCapabilities(); await getCurrentResearchActivation()
    expect(fetch.mock.calls[0]).toEqual(['/api/v1/budget/research/capabilities', expect.objectContaining({ credentials: 'omit', method: 'GET', cache: 'no-store' })])
    expect(fetch.mock.calls[1]).toEqual(['/api/v1/budget/research/current', expect.objectContaining({ credentials: 'same-origin', method: 'GET', cache: 'no-store' })])
    expect(fetch).toHaveBeenCalledTimes(2)
  })
  it('previews and activates only exact explicit bodies with both CSRF tokens and a pinned manifest hash', async () => {
    const fetch = vi.fn(async (path: RequestInfo | URL, _options?: RequestInit) => Response.json(path.toString().endsWith('/preview') ? activationPreview() : researchActivation())); vi.stubGlobal('fetch', fetch)
    const exact = await previewResearchActivation(activationPreviewRequest(), BROWSER_CSRF, OPERATOR_CSRF)
    await activateResearch({ manifest: exact.manifest, expected_manifest_sha256: exact.manifest_sha256 }, BROWSER_CSRF, OPERATOR_CSRF)
    expect(fetch.mock.calls[0]).toEqual(['/api/v1/budget/research/preview', expect.objectContaining({ method: 'POST', body: JSON.stringify(activationPreviewRequest()), headers: expect.objectContaining({ 'X-CSRF-Token': BROWSER_CSRF, 'X-Operator-CSRF-Token': OPERATOR_CSRF }), credentials: 'same-origin' })])
    expect(fetch.mock.calls[1]).toEqual(['/api/v1/budget/research/activations', expect.objectContaining({ method: 'POST', body: JSON.stringify(activationRequest()) })])
  })
  it('binds preview to exact saved readiness/canary IDs and human-reviewed facts while allowing canonical time/URL normalization', async () => {
    const entered = activationPreviewRequest(); entered.review.observed_at = '2026-10-28T00:00:00.000+00:00'; entered.review.evidence[0].url = 'https://fixtures.example.test'
    const normalized = researchManifest(); normalized.review.evidence[0].url = 'https://fixtures.example.test/'
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(activationPreview(normalized))))
    expect((await previewResearchActivation(entered, BROWSER_CSRF, OPERATOR_CSRF)).manifest.review.observed_at).toBe('2026-10-28T00:00:00Z')
    for (const change of [{ readiness_id: ACTIVATION_ID }, { canary: { ...researchManifest().canary, receipt_id: ACTIVATION_ID } }, { review: { ...researchReview(), evidence: [{ ...researchReview().evidence[0], content_sha256: 'f'.repeat(64) }] } }, { provider_account_attestation: { ...researchManifest().provider_account_attestation, available_credit_microdollars: 149999999 } }]) {
      vi.stubGlobal('fetch', vi.fn(async () => Response.json(activationPreview(researchManifest(change)))))
      await expect(previewResearchActivation(activationPreviewRequest(), BROWSER_CSRF, OPERATOR_CSRF)).rejects.toMatchObject({ code: 'invalid_response', uncertain: true })
    }
  })
  it('accepts an identical existing revoked receipt on an explicit retry without inventing new activation authority', async () => {
    const value = researchActivation({ approval: { ...researchActivation().approval, valid: false, revoked_at: '2026-10-28T00:01:00Z' }, eligible_for_current_browser: false, research_execution_available: false })
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(value)))
    expect((await activateResearch(activationRequest(), BROWSER_CSRF, OPERATOR_CSRF)).research_execution_available).toBe(false)
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(researchActivation({}, researchManifest({ native_control_epoch: 2 })))))
    await expect(activateResearch(activationRequest(), BROWSER_CSRF, OPERATOR_CSRF)).rejects.toMatchObject({ code: 'invalid_response', uncertain: true })
  })
  it('reads bounded history and exact details using only protected GETs without retrieving evidence URLs', async () => {
    const fetch = vi.fn(async (path: RequestInfo | URL, _options?: RequestInit) => Response.json(path.toString().includes('?') ? activationHistory() : researchActivation())); vi.stubGlobal('fetch', fetch)
    await listResearchActivations(); await getResearchActivation(ACTIVATION_ID)
    expect(fetch.mock.calls[0][0]).toBe('/api/v1/budget/research/activations?limit=5')
    for (const [path, options] of fetch.mock.calls) { expect(path.toString().startsWith('/api/v1/budget/research/')).toBe(true); expect(options).toMatchObject({ method: 'GET', credentials: 'same-origin', cache: 'no-store' }) }
    await expect(getResearchActivation(READINESS_ID)).rejects.toThrow('invalid_response')
  })
  it('rejects duplicate, oversized or unbound history cursors before showing private receipts', async () => {
    for (const value of [activationHistory([researchActivation(), researchActivation()]), activationHistory(Array.from({ length: 6 }, () => researchActivation())), activationHistory([], ACTIVATION_ID), activationHistory([researchActivation()], READINESS_ID)]) {
      vi.stubGlobal('fetch', vi.fn(async () => Response.json(value)))
      await expect(listResearchActivations()).rejects.toThrow('invalid_response')
    }
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(activationHistory())))
    await expect(listResearchActivations(undefined, ACTIVATION_ID)).rejects.toThrow('invalid_response')
  })
  it('revokes only the requested activation with an empty both-CSRF body and requires an actual revoked receipt', async () => {
    const value = researchActivation({ approval: { ...researchActivation().approval, revoked_at: '2026-10-28T00:01:00Z', valid: false }, eligible_for_current_browser: false, research_execution_available: false }), fetch = vi.fn(async (_path: RequestInfo | URL, _options?: RequestInit) => Response.json(value)); vi.stubGlobal('fetch', fetch)
    expect((await revokeResearchActivation(ACTIVATION_ID, BROWSER_CSRF, OPERATOR_CSRF)).approval.valid).toBe(false)
    expect(fetch.mock.calls[0]).toEqual([`/api/v1/budget/research/activations/${ACTIVATION_ID}/revoke`, expect.objectContaining({ method: 'POST', body: '{}', headers: expect.objectContaining({ 'X-CSRF-Token': BROWSER_CSRF, 'X-Operator-CSRF-Token': OPERATOR_CSRF }) })])
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(researchActivation())))
    await expect(revokeResearchActivation(ACTIVATION_ID, BROWSER_CSRF, OPERATOR_CSRF)).rejects.toMatchObject({ code: 'invalid_response', uncertain: true })
  })
  it.each([[401, 'research_session_required', 'browser_expired'], [401, 'operator_session_required', 'operator_expired'], [403, 'csrf_check_failed', 'browser_expired'], [403, 'origin_check_failed', 'browser_expired'], [403, 'operator_csrf_check_failed', 'operator_expired'], [403, 'meta_research_actor_forbidden', 'meta_research_actor_forbidden'], [503, 'readiness_role_unavailable', 'readiness_role_unavailable']] as const)('keeps %s/%s authority failures separate as %s', async (status, detail, expected) => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ detail }, { status })))
    await expect(getCurrentResearchActivation()).rejects.toMatchObject({ code: expected, uncertain: false })
  })
  it('never sends missing CSRF, malformed UUIDs, credential uploads or skipped review confirmations', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
    await expect(activateResearch(activationRequest(), '', OPERATOR_CSRF)).rejects.toMatchObject({ code: 'browser_expired' })
    await expect(activateResearch(activationRequest(), BROWSER_CSRF, '')).rejects.toMatchObject({ code: 'operator_expired' })
    await expect(getResearchActivation('../foreign')).rejects.toMatchObject({ code: 'meta_research_invalid_request' })
    await expect(previewResearchActivation({ ...activationPreviewRequest(), readiness_id: 'not-a-uuid' }, BROWSER_CSRF, OPERATOR_CSRF)).rejects.toMatchObject({ code: 'meta_research_invalid_request' })
    const malformed = { ...activationPreviewRequest(), provider_account_attestation: { ...activationPreviewRequest().provider_account_attestation, api_key: 'Synthetic never-send secret' } }
    await expect(previewResearchActivation(malformed, BROWSER_CSRF, OPERATOR_CSRF)).rejects.toMatchObject({ code: 'meta_research_invalid_request' })
    expect(fetch).not.toHaveBeenCalled()
  })
  it('marks lost or malformed activate responses uncertain without retrying or making scientific submissions', async () => {
    const fetch = vi.fn(async () => { throw new TypeError('Synthetic response lost after possible approval') }); vi.stubGlobal('fetch', fetch)
    await expect(activateResearch(activationRequest(), BROWSER_CSRF, OPERATOR_CSRF)).rejects.toMatchObject({ uncertain: true }); expect(fetch).toHaveBeenCalledTimes(1)
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ bearer_token: 'Synthetic invalid authority' })))
    await expect(activateResearch(activationRequest(), BROWSER_CSRF, OPERATOR_CSRF)).rejects.toMatchObject({ code: 'invalid_response', uncertain: true })
  })
  it('preserves bounded static conflicts and redacts untrusted server text without clearing a valid browser', async () => {
    for (const detail of ['meta_research_manifest_changed', 'meta_research_account_conflict', 'meta_research_prior_unresolved', 'meta_research_authority_expired', 'meta_research_activation_required', 'meta_research_input_changed', 'meta_research_outcome_unknown']) {
      vi.stubGlobal('fetch', vi.fn(async () => Response.json({ detail }, { status: 409 })))
      await expect(activateResearch(activationRequest(), BROWSER_CSRF, OPERATOR_CSRF)).rejects.toMatchObject({ code: detail, uncertain: false })
    }
    for (const [detail, expected, uncertain] of [['meta_research_unavailable', 'meta_research_unavailable', false], ['meta_research_storage_unavailable', 'meta_research_storage_unavailable', true], ['Synthetic private provider response', 'unavailable', true]] as const) {
      vi.stubGlobal('fetch', vi.fn(async () => Response.json({ detail }, { status: 503 })))
      await expect(activateResearch(activationRequest(), BROWSER_CSRF, OPERATOR_CSRF)).rejects.toMatchObject({ code: expected, uncertain })
    }
  })
  it('rejects oversized declared/streamed bodies and invalid UTF-8 before exposing current authority', async () => {
    for (const response of [new Response('{}', { headers: { 'content-length': '2097153' } }), new Response(' '.repeat(2097153)), new Response(new Uint8Array([0xc3, 0x28]))]) { vi.stubGlobal('fetch', vi.fn(async () => response)); await expect(getCurrentResearchActivation()).rejects.toThrow('invalid_response') }
  })
  it('aborts stale current-scope reads even if the transport ignores abort, and sends no pre-aborted activation', async () => {
    const controller = new AbortController(); let resolve!: (value: Response) => void
    const fetch = vi.fn(() => new Promise<Response>((done) => { resolve = done })); vi.stubGlobal('fetch', fetch)
    const pending = getCurrentResearchActivation(controller.signal); controller.abort(); resolve(Response.json(currentActivation()))
    await expect(pending).rejects.toMatchObject({ code: 'aborted', uncertain: false })
    await expect(activateResearch(activationRequest(), BROWSER_CSRF, OPERATOR_CSRF, controller.signal)).rejects.toMatchObject({ code: 'aborted', uncertain: false })
    expect(fetch).toHaveBeenCalledTimes(1)
  })
  it('retains unknown mutation outcomes after timeout or caller cancellation without resending', async () => {
    vi.useFakeTimers()
    const fetch = vi.fn(async (_path: RequestInfo | URL, options?: RequestInit) => new Promise<Response>((_resolve, reject) => { options?.signal?.addEventListener('abort', () => reject(new DOMException('Synthetic abort', 'AbortError')), { once: true }) })); vi.stubGlobal('fetch', fetch)
    const pending = activateResearch(activationRequest(), BROWSER_CSRF, OPERATOR_CSRF), assertion = expect(pending).rejects.toMatchObject({ code: 'unavailable', uncertain: true }); await vi.advanceTimersByTimeAsync(10000); await assertion
    const controller = new AbortController(), cancelled = activateResearch(activationRequest(), BROWSER_CSRF, OPERATOR_CSRF, controller.signal), cancelledAssertion = expect(cancelled).rejects.toMatchObject({ code: 'aborted', uncertain: true }); controller.abort(); await cancelledAssertion
    expect(fetch).toHaveBeenCalledTimes(2)
    expect(fetch.mock.calls.every(([path]) => path.toString() === '/api/v1/budget/research/activations')).toBe(true)
  })
  it('keeps IDs canonical and distinguishes a missing saved receipt without exposing private existence', async () => {
    const fetch = vi.fn(async () => Response.json({ detail: 'meta_research_not_found' }, { status: 404 })); vi.stubGlobal('fetch', fetch)
    await expect(getResearchActivation(RECEIPT_ID)).rejects.toMatchObject({ code: 'meta_research_not_found' })
    await expect(getResearchActivation(ACTIVATION_ID.toUpperCase().replace('66666666', 'ABCDEFAB'))).rejects.toMatchObject({ code: 'meta_research_invalid_request' })
    expect(fetch).toHaveBeenCalledTimes(1)
  })
})
