import { act, render, renderHook, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getResearchAccess, parseResearchAccess } from './access'
import { publicAccess, privateAccess } from './accessFixtures.test-support'
import { useNativeResearchContext } from './useNativeResearchContext'
import { NativeResearchNotice } from './NativeResearchNotice'
import { createResearchRun, resumeNativeResearchRun } from './client'
import { createArtifactJob } from '../artifacts/client'
import { createAssessmentJob } from '../assessments/client'
import { job, request, ROOT } from '../assessments/fixtures.test-support'
import { nativeAdmissionFailure, validNativeSubmission } from './nativeAdmission'
import { BROWSER_CSRF, DATE } from '../budget/test-support'
const RUN = '11111111-1111-4111-8111-111111111111'
function settings() { return { enabled: true, csrfToken: BROWSER_CSRF as string | null, required: true, available: true, onBrowserExpired: vi.fn() } }
function run() { return { id: RUN, mode: 'explore', goal: 'Synthetic visitor investigation', task_kind: 'research', status: 'queued', created_at: DATE, updated_at: DATE, result_id: null, error_code: null, external_outcome_unknown: false } }
beforeEach(() => vi.spyOn(Date, 'now').mockReturnValue(Date.parse(DATE)))
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); vi.restoreAllMocks() })
describe('visitor-safe research access', () => {
  it('accepts minimal own-browser metadata and rejects private additions', () => {
    expect(parseResearchAccess(publicAccess())).toEqual(publicAccess()); expect(parseResearchAccess(privateAccess())).toEqual(privateAccess())
    for (const extra of ['profiles', 'actor_id', 'account_id', 'credential_binding_sha256']) expect(() => parseResearchAccess({ ...publicAccess(), [extra]: 'private' })).toThrow('invalid_response')
    expect(() => parseResearchAccess({ ...publicAccess({ available: false, reason: 'public_research_stopped', expected_activation_sha256: null, expires_at: null, limits: null }), remaining_session_allowance_microdollars: '1' })).toThrow('invalid_response')
    expect(() => parseResearchAccess({ ...privateAccess(), expected_activation_sha256: 'a'.repeat(64) })).toThrow('invalid_response')
  })
  it.each(['0', '01', '-1', '1e3', 500000])('rejects invalid live allowance %s', (remaining) => expect(() => parseResearchAccess({ ...publicAccess(), remaining_session_allowance_microdollars: remaining })).toThrow('invalid_response'))
  it('reads only existing ownership and no CSRF/session/operator endpoints', async () => {
    const fetch = vi.fn(async (_path: RequestInfo | URL, _options?: RequestInit) => Response.json(publicAccess())); vi.stubGlobal('fetch', fetch)
    expect(await getResearchAccess()).toEqual(publicAccess())
    expect(fetch.mock.calls).toEqual([['/api/v1/research/access', expect.objectContaining({ method: 'GET', credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } })]])
  })
  it('uses funded access without operator GET and displays exact UTC limits', async () => {
    const fetch = vi.fn(async (path: RequestInfo | URL) => Response.json(String(path).endsWith('/protocols') ? { protocol: 'polymath-research-protocols-v1', activation_sha256: publicAccess().expected_activation_sha256, capabilities: [] } : publicAccess())); vi.stubGlobal('fetch', fetch)
    const { result } = renderHook(() => useNativeResearchContext(settings())); await waitFor(() => expect(result.current.canSubmit).toBe(true))
    expect(result.current.activation).toBeNull(); expect(result.current.submission).toEqual({ audience: 'public_sessions', expectedActivationSha256: publicAccess().expected_activation_sha256 }); expect(fetch).toHaveBeenCalledTimes(3)
    render(<NativeResearchNotice context={result.current} />); expect(screen.getByText(/No operator sign-in is required/)).toBeInTheDocument(); expect(screen.getByText(publicAccess().expires_at!)).toBeInTheDocument(); expect(screen.queryByRole('link', { name: /operator/ })).not.toBeInTheDocument()
  })
  it('retains continuation authority when the daily new-job allowance reaches zero', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(publicAccess({ remaining_session_jobs_today: 0 }))))
    const { result } = renderHook(() => useNativeResearchContext(settings())); await waitFor(() => expect(result.current.canResume).toBe(true)); expect(result.current.canSubmit).toBe(false); expect(result.current.submission?.expectedActivationSha256).toBe(publicAccess().expected_activation_sha256)
  })
  it.each(['public_research_stopped', 'public_research_authority_expired', 'public_research_session_budget_exhausted', 'public_research_prior_unresolved'] as const)('retains owned data and grant facts when funding closes: %s', async (reason) => {
    const closed = publicAccess({ available: false, reason }), props = settings(), fetch = vi.fn(async () => Response.json(closed)); vi.stubGlobal('fetch', fetch)
    const { result } = renderHook(() => useNativeResearchContext(props)); await waitFor(() => expect(result.current.phase).toBe('activation_required'))
    expect(result.current.canSubmit).toBe(false); expect(result.current.submission).toBeNull(); expect(props.onBrowserExpired).not.toHaveBeenCalled(); expect(result.current.access?.limits).not.toBeNull(); expect(fetch).toHaveBeenCalledTimes(1)
  })
  it('distinguishes resource throttling from browser expiry', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ detail: 'public_research_read_rate_limited' }, { status: 429, headers: { 'Retry-After': '17' } })))
    await expect(getResearchAccess()).rejects.toMatchObject({ code: 'resource_limited', retryAfterSeconds: 17 })
    const props = settings(), { result } = renderHook(() => useNativeResearchContext(props)); await waitFor(() => expect(result.current.phase).toBe('error')); expect(props.onBrowserExpired).not.toHaveBeenCalled()
  })
  it('honors the access read cooldown without clearing ownership or automatically retrying', async () => {
    vi.useFakeTimers(); vi.spyOn(Date, 'now').mockRestore(); vi.setSystemTime(new Date(DATE))
    let limited = true
    const fetch = vi.fn(async (path: RequestInfo | URL) => String(path).endsWith('/protocols') ? Response.json({ protocol: 'polymath-research-protocols-v1', activation_sha256: publicAccess().expected_activation_sha256, capabilities: [] }) : limited ? Response.json({ detail: 'public_request_rate_limited' }, { status: 429, headers: { 'Retry-After': '17' } }) : Response.json(publicAccess()))
    vi.stubGlobal('fetch', fetch)
    const props = settings(), { result } = renderHook(() => useNativeResearchContext(props))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(result.current.reason).toBe('resource_limited'); expect(result.current.retryAfterSeconds).toBe(17)
    act(() => result.current.refresh()); expect(fetch).toHaveBeenCalledTimes(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(17000) })
    expect(result.current.retryAfterSeconds).toBeNull(); expect(fetch).toHaveBeenCalledTimes(1)
    limited = false; act(() => result.current.refresh()); await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(result.current.canSubmit).toBe(true); expect(fetch).toHaveBeenCalledTimes(4); expect(props.onBrowserExpired).not.toHaveBeenCalled()
  })
  it('aborts and fences late reads across browser rotation', async () => {
    let resolve!: (value: Response) => void, reads = 0, oldSignal: AbortSignal | null = null
    vi.stubGlobal('fetch', vi.fn(async (_path: RequestInfo | URL, options?: RequestInit) => ++reads === 1 ? new Promise<Response>((done) => { resolve = done; oldSignal = options?.signal as AbortSignal }) : Response.json(publicAccess({ expected_activation_sha256: 'd'.repeat(64) }))))
    const props = settings(), { result, rerender } = renderHook((input) => useNativeResearchContext(input), { initialProps: props }); await waitFor(() => expect(oldSignal).not.toBeNull()); rerender({ ...props, csrfToken: 'b'.repeat(64) }); await waitFor(() => expect(result.current.canSubmit).toBe(true)); await act(async () => resolve(Response.json(publicAccess())))
    expect(oldSignal!.aborted).toBe(true); expect(result.current.submission?.expectedActivationSha256).toBe('d'.repeat(64))
  })
  it('treats requester expiry as browser expiry rather than funding closure', async () => {
    expect(nativeAdmissionFailure(401, 'public_research_requester_expired')).toBe(false)
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ detail: 'public_research_requester_expired' }, { status: 401 })))
    await expect(createResearchRun('explore', 'Synthetic visitor investigation', BROWSER_CSRF, undefined, [], { audience: 'public_sessions', expectedActivationSha256: 'a'.repeat(64) })).rejects.toMatchObject({ code: 'session_expired' })
  })
  it('forbids an operator credential on public admission', () => {
    expect(validNativeSubmission({ audience: 'public_sessions', expectedActivationSha256: 'a'.repeat(64) })).toBe(true); expect(validNativeSubmission({ audience: 'public_sessions', expectedActivationSha256: 'a'.repeat(64), operatorCsrfToken: undefined })).toBe(false)
  })
  it.each(['research', 'artifact', 'assessment', 'resume'] as const)('sends one %s action with only browser CSRF and its exact public hash', async (kind) => {
    const hash = publicAccess().expected_activation_sha256!, admission = { audience: 'public_sessions' as const, expectedActivationSha256: hash }
    const fetch = vi.fn(async (_path: RequestInfo | URL, _options?: RequestInit) => Response.json(kind === 'assessment' ? job() : kind === 'artifact' ? { ...run(), task_kind: 'write_summary', artifact_id: null } : run())); vi.stubGlobal('fetch', fetch)
    if (kind === 'research') await createResearchRun('explore', 'Synthetic visitor investigation', BROWSER_CSRF, undefined, [], admission)
    else if (kind === 'artifact') await createArtifactJob('write_summary', ROOT, BROWSER_CSRF, undefined, undefined, admission)
    else if (kind === 'assessment') await createAssessmentJob(request(), BROWSER_CSRF, undefined, admission)
    else await resumeNativeResearchRun(RUN, '9223372036854775807', BROWSER_CSRF, admission)
    expect(fetch).toHaveBeenCalledTimes(1); const options = fetch.mock.calls[0][1]!
    expect(options.headers).toHaveProperty('X-CSRF-Token', BROWSER_CSRF); expect(options.headers).not.toHaveProperty('X-Operator-CSRF-Token'); expect(JSON.parse(options.body!.toString())).toHaveProperty('expected_activation_sha256', hash)
  })
})
