import { afterEach, describe, expect, it, vi } from 'vitest'
import { BudgetError, armMetaVerification, cancelMetaVerificationJob, finalizeMetaVerificationJob, getMetaVerificationJob, getMetaVerificationReceipt, listMetaVerificationJobs, loadVerificationCapabilities, parseMetaProbeCapabilities, parseMetaProbeJob, parseMetaProbeManifest, parseMetaProbePreview, parseMetaProbeReceipt, parseProviderAccountAttestation, previewMetaVerification } from './verificationClient'
import { APPROVAL_ID, BROWSER_CSRF, JOB_ID, OPERATOR_CSRF, READINESS_ID, RECEIPT_ID, armRequest, attestation, completedJob, manifest, previewRequest, receipt, unknownReceipt, verificationCapabilities, verificationHistory, verificationJob, verificationPreview } from './verificationFixtures'
import type { MetaProbeJob } from './verificationTypes'

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

describe('one-call verification contract', () => {
  it('keeps fixed protocol, separate rounded components and disabled research explicit', () => {
    const parsed = parseMetaProbePreview(verificationPreview())
    expect(parsed.manifest.request.payload.messages).toHaveLength(2)
    expect(parsed.manifest.input_reservation_microdollars).toBe(1)
    expect(parsed.manifest.output_reservation_microdollars).toBe(1)
    expect(parsed.manifest.reservation_microdollars).toBe(2)
    expect(parsed.research_execution_available).toBe(false)
    expect(parsed.manifest.provider_account_attestation).not.toHaveProperty('credential_binding_sha256')
    expect(parseMetaProbeJob(verificationJob()).research_execution_available).toBe(false)
  })
  it('accepts disabled, inspection-only and explicit verification capabilities without actor or credential data', () => {
    for (const mode of ['off', 'inspect', 'verify'] as const) expect(parseMetaProbeCapabilities(verificationCapabilities(mode))).toEqual(verificationCapabilities(mode))
    expect(() => parseMetaProbeCapabilities({ ...verificationCapabilities('off'), verification_available: true })).toThrow(BudgetError)
    expect(() => parseMetaProbeCapabilities({ ...verificationCapabilities(), research_execution_available: true })).toThrow(BudgetError)
    expect(() => parseMetaProbeCapabilities({ ...verificationCapabilities(), credential_binding_sha256: 'a'.repeat(64) })).toThrow(BudgetError)
  })
  it.each(['provider_billing_account_id', 'model_id', 'available_credit_microdollars', 'remaining_provider_allowance_microdollars', 'provider_hard_cap_microdollars', 'evidence'])('requires explicit human account attestation field %s', (field) => {
    const value = { ...attestation() } as Record<string, unknown>; delete value[field]
    expect(() => parseProviderAccountAttestation(value)).toThrow('invalid_response')
  })
  it('rejects impossible account allowance, non-UTC times and duplicated or credentialed evidence', () => {
    expect(() => parseProviderAccountAttestation(attestation({ remaining_provider_allowance_microdollars: 150000001 }))).toThrow()
    expect(() => parseProviderAccountAttestation(attestation({ observed_at: '2026-10-28T01:00:00+01:00' }))).toThrow()
    expect(() => parseProviderAccountAttestation(attestation({ observed_at: '2026-02-30T00:00:00Z' }))).toThrow()
    expect(() => parseProviderAccountAttestation(attestation({ valid_until: attestation().observed_at }))).toThrow()
    expect(() => parseProviderAccountAttestation(attestation({ evidence: [attestation().evidence[0], attestation().evidence[0]] }))).toThrow()
    for (const url of ['http://fixtures.example.test/evidence', 'https://secret@fixtures.example.test/evidence', 'https://fixtures.example.test/evidence?key=secret', 'https://fixtures.example.test/a/../evidence', 'https://fixtures.example.test:8443/evidence']) expect(() => parseProviderAccountAttestation(attestation({ evidence: [{ ...attestation().evidence[0], url }] }))).toThrow()
  })
  it('requires the exact isolated prompt, fixed endpoint, output limit and kill-clear consent', () => {
    const value = manifest()
    const requestChanges = [{ ...value.request, endpoint: 'https://elsewhere.example.test' }, { ...value.request, payload: { ...value.request.payload, stream: true } }, { ...value.request, payload: { ...value.request.payload, max_tokens: 15 } }, { ...value.request, payload: { ...value.request.payload, messages: [{ role: 'user', content: 'Search private papers' }, value.request.payload.messages[1]] } }]
    for (const request of requestChanges) expect(() => parseMetaProbeManifest({ ...value, request })).toThrow()
    expect(() => parseMetaProbeManifest({ ...value, clear_kill_switch: false })).toThrow()
    expect(() => parseMetaProbeManifest({ ...value, reservation_microdollars: 1 })).toThrow()
    expect(() => parseMetaProbeManifest({ ...value, native_control_epoch: Number.MAX_SAFE_INTEGER })).toThrow()
    expect(() => parseMetaProbeManifest({ ...value, provider_account_attestation: attestation({ model_id: 'different-model' }) })).toThrow()
  })
  it('compares microsecond deadlines without losing sub-millisecond authority boundaries', () => {
    const value = manifest({ valid_until: '2026-10-28T00:00:00.000002Z', provider_account_attestation: attestation({ observed_at: '2026-10-28T00:00:00.000001Z', valid_until: '2026-10-28T00:00:00.000003Z' }) })
    expect(parseMetaProbeManifest(value).valid_until).toBe(value.valid_until)
    expect(() => parseMetaProbeManifest({ ...value, valid_until: value.provider_account_attestation.observed_at })).toThrow()
    expect(() => parseMetaProbeManifest({ ...value, valid_until: '2026-10-28T00:00:00.000004Z' })).toThrow()
  })
  it('preserves the exact-model action summary and counted components, without raw generated text', () => {
    const parsed = parseMetaProbeReceipt(receipt())
    expect(parsed.assessment.input_tokens).toBe('2'); expect(parsed.assessment.output_tokens).toBe('3')
    expect(parsed.assessment.actual_microdollars).toBe(2)
    expect(parsed.assessment.action).toMatchObject({ kind: 'conclude', outcome: 'insufficient_evidence' })
    expect(parsed.bindings.approval_id).toBe(APPROVAL_ID)
    expect(parsed.observation.observed_model_id).toBe(manifest().request.payload.model)
    expect(parsed).not.toHaveProperty('content')
  })
  it.each(['content', 'body', 'headers', 'api_key'])('rejects private provider field %s rather than exposing it', (field) => {
    const value = receipt()
    expect(() => parseMetaProbeReceipt({ ...value, [field]: 'Synthetic private journal text' })).toThrow()
    expect(() => parseMetaProbeReceipt({ ...value, observation: { ...value.observation, [field]: 'Synthetic private journal text' } })).toThrow()
  })
  it('keeps unknown usage as unknown and large exact counters as decimal strings', () => {
    const unknown = unknownReceipt()
    expect(parseMetaProbeReceipt(unknown).assessment.actual_microdollars).toBeNull()
    const enormous = { ...receipt(), native_status: 'unknown', assessment: { ...receipt().assessment, status: 'unknown', reason_code: 'counted_cost_unrepresentable', input_tokens: '9223372036854775807', output_tokens: '9223372036854775807', actual_input_microdollars: null, actual_output_microdollars: null, actual_microdollars: null, usage_within_bounds: null, freeze_required: true, action_sha256: null, action: null } }
    expect(parseMetaProbeReceipt(enormous).assessment.input_tokens).toBe('9223372036854775807')
    for (const counter of [2, '02', '9223372036854775808']) expect(() => parseMetaProbeReceipt({ ...enormous, assessment: { ...enormous.assessment, input_tokens: counter } })).toThrow()
    expect(() => parseMetaProbeReceipt({ ...unknown, assessment: { ...unknown.assessment, actual_microdollars: 0 } })).toThrow()
  })
  it('never accepts substitute models, incomplete bodies or contradictory successful accounting', () => {
    const value = receipt()
    for (const observation of [{ ...value.observation, observed_model_id: 'substitute-model' }, { ...value.observation, body_complete: false }, { ...value.observation, http_status: 500 }, { ...value.observation, finish_reason: 'length' }]) expect(() => parseMetaProbeReceipt({ ...value, observation })).toThrow()
    expect(() => parseMetaProbeReceipt({ ...value, assessment: { ...value.assessment, actual_microdollars: 1 } })).toThrow()
    expect(() => parseMetaProbeReceipt({ ...value, assessment: { ...value.assessment, freeze_required: true } })).toThrow()
    expect(() => parseMetaProbeReceipt({ ...value, assessment: { ...value.assessment, research_execution_available: true } })).toThrow()
    expect(() => parseMetaProbeReceipt({ ...value, observation_sha256: 'f'.repeat(64) })).toThrow()
  })
  it('retains full counted charges for failed bounds while refusing invented success or zero-cost claims', () => {
    const value = receipt(), assessment = { ...value.assessment, status: 'failed', reason_code: 'billable_bound_exceeded', input_tokens: '2000000', actual_input_microdollars: 2, actual_output_microdollars: 1, actual_microdollars: 3, usage_within_bounds: false, freeze_required: true, action_sha256: null, action: null }
    expect(parseMetaProbeReceipt({ ...value, native_status: 'failed', assessment }).assessment.actual_microdollars).toBe(3)
    expect(() => parseMetaProbeReceipt({ ...value, native_status: 'failed', assessment: { ...assessment, freeze_required: false } })).toThrow()
    expect(() => parseMetaProbeReceipt({ ...value, native_status: 'failed', assessment: { ...assessment, reason_code: 'observed_model_missing' } })).toThrow()
  })
  it.each(['proposal_sha256', 'request_sha256', 'credential_binding_sha256', 'approval_id', 'armed_epoch'])('binds a saved receipt to job %s', (field) => {
    const value = completedJob(), binding = value.receipt!.bindings
    expect(() => parseMetaProbeJob({ ...value, receipt: { ...value.receipt, bindings: { ...binding, [field]: field === 'armed_epoch' ? 2 : field === 'approval_id' ? READINESS_ID : 'f'.repeat(64) } } })).toThrow()
  })
  it('preserves late observed facts after cancellation and server-authoritative local finalization controls', () => {
    const value = completedJob()
    expect(parseMetaProbeJob({ ...value, status: 'cancelled' }).receipt?.native_status).toBe('passed')
    expect(parseMetaProbeJob(verificationJob({ status: 'cancelled', can_cancel: false, can_finalize: true, approval: { ...verificationJob().approval, valid: false } })).can_finalize).toBe(true)
    expect(() => parseMetaProbeJob({ ...value, can_finalize: true })).toThrow()
    expect(() => parseMetaProbeJob({ ...value, can_cancel: true })).toThrow()
    expect(() => parseMetaProbeJob({ ...value, receipt: { ...value.receipt, run_id: READINESS_ID } })).toThrow()
  })
})

describe('bounded verification transport', () => {
  it('reads capabilities anonymously with no session creation, model call, or private scope disclosure', async () => {
    const fetch = vi.fn(async (_path: RequestInfo | URL, _options?: RequestInit) => Response.json(verificationCapabilities('off'))); vi.stubGlobal('fetch', fetch)
    expect((await loadVerificationCapabilities()).verification_available).toBe(false)
    expect(fetch.mock.calls).toEqual([['/api/v1/budget/verification/capabilities', expect.objectContaining({ credentials: 'omit', cache: 'no-store', method: 'GET', redirect: 'error' })]])
  })
  it('previews and arms only the exact explicit body with both CSRF tokens', async () => {
    const fetch = vi.fn(async (path: RequestInfo | URL, _options?: RequestInit) => Response.json(path.toString().endsWith('/preview') ? verificationPreview() : verificationJob())); vi.stubGlobal('fetch', fetch)
    const preview = await previewMetaVerification(previewRequest(), BROWSER_CSRF, OPERATOR_CSRF)
    await armMetaVerification({ manifest: preview.manifest, expected_manifest_sha256: preview.manifest_sha256 }, BROWSER_CSRF, OPERATOR_CSRF)
    expect(fetch.mock.calls[0]).toEqual(['/api/v1/budget/verification/preview', expect.objectContaining({ method: 'POST', body: JSON.stringify(previewRequest()), credentials: 'same-origin', cache: 'no-store', headers: expect.objectContaining({ 'X-CSRF-Token': BROWSER_CSRF, 'X-Operator-CSRF-Token': OPERATOR_CSRF }) })])
    expect(fetch.mock.calls[1]).toEqual(['/api/v1/budget/verification/jobs', expect.objectContaining({ method: 'POST', body: JSON.stringify(armRequest()) })])
    expect(fetch).toHaveBeenCalledTimes(2)
  })
  it('allows only canonical date and evidence-URL normalization during preview, rejecting altered attested facts', async () => {
    const request = previewRequest(); request.provider_account_attestation.observed_at = '2026-10-28T00:00:00.000+00:00'; request.provider_account_attestation.evidence[0].url = 'https://fixtures.example.test'
    const normalized = manifest(); normalized.provider_account_attestation.evidence[0].url = 'https://fixtures.example.test/'
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(verificationPreview(normalized))))
    expect((await previewMetaVerification(request, BROWSER_CSRF, OPERATOR_CSRF)).manifest.provider_account_attestation.evidence[0].url).toBe('https://fixtures.example.test/')
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(verificationPreview(manifest({ provider_account_attestation: attestation({ available_credit_microdollars: 149999999 }) })))))
    await expect(previewMetaVerification(previewRequest(), BROWSER_CSRF, OPERATOR_CSRF)).rejects.toMatchObject({ code: 'invalid_response', uncertain: true })
  })
  it('marks altered arm receipts uncertain and accepts the same immutable cancelled job after idempotent retry', async () => {
    const value = verificationJob({ status: 'cancelled', can_cancel: false, approval: { ...verificationJob().approval, valid: false } })
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(value)))
    expect((await armMetaVerification(armRequest(), BROWSER_CSRF, OPERATOR_CSRF)).id).toBe(JOB_ID)
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(verificationJob({}, manifest({ native_control_epoch: 1 })))))
    await expect(armMetaVerification(armRequest(), BROWSER_CSRF, OPERATOR_CSRF)).rejects.toMatchObject({ code: 'invalid_response', uncertain: true })
  })
  it.each([[401, 'research_session_required', 'browser_expired'], [401, 'operator_session_required', 'operator_expired'], [403, 'csrf_check_failed', 'browser_expired'], [403, 'origin_check_failed', 'browser_expired'], [403, 'operator_csrf_check_failed', 'operator_expired'], [403, 'readiness_actor_forbidden', 'readiness_actor_forbidden'], [403, 'meta_probe_actor_forbidden', 'meta_probe_actor_forbidden'], [503, 'readiness_role_unavailable', 'readiness_role_unavailable']] as const)('distinguishes %s/%s as %s', async (status, detail, expected) => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ detail }, { status })))
    await expect(listMetaVerificationJobs()).rejects.toMatchObject({ code: expected, uncertain: false })
  })
  it('never sends malformed bodies or missing CSRF tokens', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
    await expect(armMetaVerification(armRequest(), '', OPERATOR_CSRF)).rejects.toMatchObject({ code: 'browser_expired' })
    await expect(armMetaVerification(armRequest(), BROWSER_CSRF, '')).rejects.toMatchObject({ code: 'operator_expired' })
    await expect(previewMetaVerification({ ...previewRequest(), readiness_id: 'not-a-uuid' }, BROWSER_CSRF, OPERATOR_CSRF)).rejects.toMatchObject({ code: 'meta_probe_invalid_request' })
    await expect(getMetaVerificationJob('../private')).rejects.toMatchObject({ code: 'meta_probe_invalid_request' })
    expect(fetch).not.toHaveBeenCalled()
  })
  it('retrieves bounded saved jobs and receipts and binds every response to its requested identity', async () => {
    const fetch = vi.fn(async (path: RequestInfo | URL, _options?: RequestInit) => Response.json(path.toString().includes('receipts') ? receipt() : path.toString().includes('?') ? verificationHistory() : verificationJob())); vi.stubGlobal('fetch', fetch)
    await listMetaVerificationJobs(); await getMetaVerificationJob(JOB_ID); await getMetaVerificationReceipt(RECEIPT_ID)
    expect(fetch.mock.calls[0][0]).toBe('/api/v1/budget/verification/jobs?limit=5')
    for (const [path, options] of fetch.mock.calls) { expect(path.toString().startsWith('/api/v1/budget/verification/')).toBe(true); expect(options?.method).toBe('GET'); expect(options?.credentials).toBe('same-origin') }
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(verificationJob())))
    await expect(getMetaVerificationJob(READINESS_ID)).rejects.toThrow('invalid_response')
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(receipt())))
    await expect(getMetaVerificationReceipt(READINESS_ID)).rejects.toThrow('invalid_response')
  })
  it('rejects duplicate, oversized, repeated-anchor or incorrectly bound history cursors', async () => {
    for (const value of [verificationHistory([verificationJob(), verificationJob()]), verificationHistory(Array.from({ length: 6 }, () => verificationJob())), verificationHistory([verificationJob()], READINESS_ID), verificationHistory([], JOB_ID)]) {
      vi.stubGlobal('fetch', vi.fn(async () => Response.json(value)))
      await expect(listMetaVerificationJobs()).rejects.toThrow('invalid_response')
    }
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(verificationHistory())))
    await expect(listMetaVerificationJobs(undefined, JOB_ID)).rejects.toThrow('invalid_response')
  })
  it('uses explicit cancel/finalize controls only with empty bodies and preserves late receipts', async () => {
    const cancelled: MetaProbeJob = { ...completedJob(), status: 'cancelled' }, fetch = vi.fn(async (_path: RequestInfo | URL, _options?: RequestInit) => Response.json(cancelled)); vi.stubGlobal('fetch', fetch)
    expect((await cancelMetaVerificationJob(JOB_ID, BROWSER_CSRF, OPERATOR_CSRF)).receipt?.native_status).toBe('passed')
    await finalizeMetaVerificationJob(JOB_ID, BROWSER_CSRF, OPERATOR_CSRF)
    expect(fetch.mock.calls.map(([path]) => path)).toEqual([`/api/v1/budget/verification/jobs/${JOB_ID}/cancel`, `/api/v1/budget/verification/jobs/${JOB_ID}/finalize`])
    for (const [, options] of fetch.mock.calls) expect(options).toMatchObject({ method: 'POST', body: '{}', headers: { 'X-CSRF-Token': BROWSER_CSRF, 'X-Operator-CSRF-Token': OPERATOR_CSRF } })
  })
  it('treats lost and malformed successful write receipts as uncertain, with no automatic retry', async () => {
    const fetch = vi.fn(async () => { throw new TypeError('Synthetic connection lost after possible grant') }); vi.stubGlobal('fetch', fetch)
    await expect(armMetaVerification(armRequest(), BROWSER_CSRF, OPERATOR_CSRF)).rejects.toMatchObject({ uncertain: true })
    expect(fetch).toHaveBeenCalledTimes(1)
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ raw_content: 'Synthetic untrusted response' })))
    await expect(armMetaVerification(armRequest(), BROWSER_CSRF, OPERATOR_CSRF)).rejects.toMatchObject({ code: 'invalid_response', uncertain: true })
  })
  it('redacts unknown server errors and distinguishes unavailable authority from an uncertain storage failure', async () => {
    for (const [detail, uncertain] of [['meta_probe_unavailable', false], ['operator_unavailable', false], ['meta_probe_storage_unavailable', true], ['Synthetic private provider body', true]] as const) {
      vi.stubGlobal('fetch', vi.fn(async () => Response.json({ detail }, { status: 503 })))
      await expect(armMetaVerification(armRequest(), BROWSER_CSRF, OPERATOR_CSRF)).rejects.toMatchObject({ code: detail.startsWith('Synthetic') ? 'unavailable' : detail, uncertain })
    }
  })
  it('rejects oversized advertised/streamed bodies and malformed UTF-8 before parsing private records', async () => {
    const bodies = [new Response('{}', { headers: { 'content-length': '2097153' } }), new Response(' '.repeat(2097153)), new Response(new Uint8Array([0xc3, 0x28]))]
    for (const response of bodies) { vi.stubGlobal('fetch', vi.fn(async () => response)); await expect(getMetaVerificationJob(JOB_ID)).rejects.toThrow('invalid_response') }
  })
  it('aborts stale reads even if a transport ignores cancellation and never sends pre-aborted writes', async () => {
    const controller = new AbortController()
    let resolve!: (response: Response) => void
    const fetch = vi.fn(() => new Promise<Response>((done) => { resolve = done })); vi.stubGlobal('fetch', fetch)
    const pending = getMetaVerificationJob(JOB_ID, controller.signal); controller.abort(); resolve(Response.json(verificationJob()))
    await expect(pending).rejects.toMatchObject({ code: 'aborted', uncertain: false })
    await expect(armMetaVerification(armRequest(), BROWSER_CSRF, OPERATOR_CSRF, controller.signal)).rejects.toMatchObject({ code: 'aborted', uncertain: false })
    expect(fetch).toHaveBeenCalledTimes(1)
  })
  it('retains uncertainty on timeout after dispatch and on caller-aborted writes', async () => {
    vi.useFakeTimers()
    const fetch = vi.fn(async (_path: RequestInfo | URL, options?: RequestInit) => new Promise<Response>((_resolve, reject) => { options?.signal?.addEventListener('abort', () => reject(new DOMException('Synthetic abort', 'AbortError')), { once: true }) })); vi.stubGlobal('fetch', fetch)
    const timed = armMetaVerification(armRequest(), BROWSER_CSRF, OPERATOR_CSRF), assertion = expect(timed).rejects.toMatchObject({ code: 'unavailable', uncertain: true })
    await vi.advanceTimersByTimeAsync(10000); await assertion
    const controller = new AbortController(), aborted = armMetaVerification(armRequest(), BROWSER_CSRF, OPERATOR_CSRF, controller.signal), abortAssertion = expect(aborted).rejects.toMatchObject({ code: 'aborted', uncertain: true }); controller.abort(); await abortAssertion
    expect(fetch).toHaveBeenCalledTimes(2)
  })
})
