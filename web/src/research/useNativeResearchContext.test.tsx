import { privateAccess } from './accessFixtures.test-support'
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useNativeResearchContext } from './useNativeResearchContext'
import { BROWSER_CSRF, DATE, OPERATOR_CSRF } from '../budget/test-support'
import { currentActivation } from '../budget/activationFixtures'
import type { CurrentResearchActivation } from '../budget/activationTypes'
import { operatorFixture } from '../publications/test-fixtures'

type Handler = (path: string, options?: RequestInit) => Response | undefined | Promise<Response | undefined>
function server(handler?: Handler) {
  const state = { current: currentActivation(), operator: operatorFixture(true) }
  const fetch = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = input.toString(), response = await handler?.(path, options)
    if (response) return response
    if (path === '/api/v1/research/access') return Response.json(privateAccess())
    if (path === '/api/v1/operator/session') return Response.json(state.operator)
    if (path === '/api/v1/budget/research/current') return Response.json(state.current)
    throw new Error(`Unexpected synthetic native context route: ${path}`)
  }); vi.stubGlobal('fetch', fetch)
  return { fetch, state }
}
function settings() { return { enabled: true, csrfToken: BROWSER_CSRF as string | null, required: true, available: true, onBrowserExpired: vi.fn() } }
async function flush() { await act(async () => { await vi.advanceTimersByTimeAsync(0) }) }
beforeEach(() => vi.spyOn(Date, 'now').mockReturnValue(Date.parse(DATE)))
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('ephemeral same-browser native research context', () => {
  it.each([{ required: false }, { enabled: false }, { available: false }, { csrfToken: null }])('makes no private operator/current requests when admission is unnecessary or disabled: %s', async (change) => {
    const requests = server(), props = { ...settings(), ...change }, { result } = renderHook(() => useNativeResearchContext(props))
    expect(result.current.phase).toBe(props.required ? 'disabled' : 'not_required')
    expect(result.current.canSubmit).toBe(!props.required)
    expect(result.current.submission).toBeNull(); expect(result.current.activation).toBeNull()
    expect(requests.fetch).not.toHaveBeenCalled()
  })
  it('loads the operator and current-browser projection only by GET and keeps admission solely in memory', async () => {
    const requests = server(), props = settings(), { result } = renderHook(() => useNativeResearchContext(props))
    expect(result.current.canSubmit).toBe(false)
    await waitFor(() => expect(result.current.phase).toBe('ready'))
    expect(result.current.submission).toEqual({ operatorCsrfToken: OPERATOR_CSRF, expectedActivationSha256: requests.state.current.activation!.manifest_sha256 })
    expect(result.current.canSubmit).toBe(true)
    expect(requests.fetch.mock.calls.map(([path]) => path)).toEqual(['/api/v1/research/access', '/api/v1/operator/session', '/api/v1/budget/research/current'])
    for (const [, options] of requests.fetch.mock.calls) expect(options).toMatchObject({ method: 'GET', credentials: 'same-origin', cache: 'no-store' })
    expect(localStorage.getItem(OPERATOR_CSRF)).toBeNull(); expect(sessionStorage.getItem(OPERATOR_CSRF)).toBeNull()
    expect(props.onBrowserExpired).not.toHaveBeenCalled()
  })
  it('does not read protected activation scope for a browser without a live operator', async () => {
    const requests = server(); requests.state.operator = operatorFixture(false)
    const { result } = renderHook(() => useNativeResearchContext(settings()))
    await waitFor(() => expect(result.current.phase).toBe('operator_required'))
    expect(result.current.canSubmit).toBe(false); expect(result.current.submission).toBeNull()
    expect(requests.fetch.mock.calls.map(([path]) => path)).toEqual(['/api/v1/research/access', '/api/v1/operator/session'])
  })
  it('does not inherit another browser’s activation from configuration or operator identity', async () => {
    const requests = server(); requests.state.current = currentActivation(null)
    const { result } = renderHook(() => useNativeResearchContext(settings()))
    await waitFor(() => expect(result.current.phase).toBe('activation_required'))
    expect(result.current.activation).toBeNull(); expect(result.current.submission).toBeNull()
    expect(result.current.canSubmit).toBe(false); expect(result.current.reason).toBe('meta_research_activation_required')
    expect(requests.fetch.mock.calls).toHaveLength(3)
  })
  it.each([[401, 'research_session_required', 'expired', true], [401, 'operator_session_required', 'operator_required', false], [403, 'meta_research_actor_forbidden', 'operator_required', false], [503, 'readiness_role_unavailable', 'operator_required', false], [409, 'meta_research_authority_expired', 'error', false]] as const)('clears paid context on %s/%s while preserving browser expiry distinction', async (status, detail, expectedPhase, browser) => {
    let reject = false
    const requests = server((path) => reject && path.endsWith('/current') ? Response.json({ detail }, { status }) : undefined)
    const props = settings(), { result } = renderHook(() => useNativeResearchContext(props))
    await waitFor(() => expect(result.current.phase).toBe('ready'))
    reject = true; act(() => result.current.refresh())
    expect(result.current.canSubmit).toBe(false)
    await waitFor(() => expect(result.current.phase).toBe(expectedPhase))
    expect(result.current.activation).toBeNull(); expect(result.current.submission).toBeNull(); expect(result.current.canSubmit).toBe(false)
    expect(props.onBrowserExpired).toHaveBeenCalledTimes(browser ? 1 : 0)
    expect(requests.fetch.mock.calls.every(([, options]) => options?.method === 'GET')).toBe(true)
  })
  it('invalidates native authority after a rejected submission without expiring the owning browser or retrying a write', async () => {
    const requests = server(), props = settings(), { result } = renderHook(() => useNativeResearchContext(props))
    await waitFor(() => expect(result.current.phase).toBe('ready'))
    act(() => result.current.invalidate('meta_research_authority_expired'))
    expect(result.current.phase).toBe('expired'); expect(result.current.submission).toBeNull(); expect(result.current.activation).toBeNull(); expect(result.current.canSubmit).toBe(false)
    expect(props.onBrowserExpired).not.toHaveBeenCalled(); expect(requests.fetch).toHaveBeenCalledTimes(3)
  })
  it('expires an exact activation deadline without submitting or renewing paid authority', async () => {
    vi.useFakeTimers(); vi.spyOn(Date, 'now').mockRestore(); vi.setSystemTime(new Date(DATE))
    const requests = server(); requests.state.current.activation!.expires_at = '2026-10-28T00:00:01Z'
    const props = settings(), { result } = renderHook(() => useNativeResearchContext(props)); await flush()
    expect(result.current.phase).toBe('ready')
    await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    expect(result.current.phase).toBe('expired'); expect(result.current.activation).toBeNull(); expect(result.current.submission).toBeNull(); expect(result.current.canSubmit).toBe(false)
    expect(props.onBrowserExpired).not.toHaveBeenCalled(); expect(requests.fetch).toHaveBeenCalledTimes(3)
  })
  it('temporarily blocks for a prior in-flight request and restores only through bounded current GET polling', async () => {
    vi.useFakeTimers(); vi.spyOn(Date, 'now').mockRestore(); vi.setSystemTime(new Date(DATE))
    const requests = server(); requests.state.current = currentActivation(null, 'meta_research_prior_unresolved')
    const { result } = renderHook(() => useNativeResearchContext(settings())); await flush()
    expect(result.current.phase).toBe('activation_required'); expect(result.current.reason).toBe('meta_research_prior_unresolved')
    expect(result.current.submission).toBeNull(); expect(result.current.canSubmit).toBe(false)
    requests.state.current = currentActivation()
    await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
    expect(result.current.phase).toBe('ready'); expect(result.current.canSubmit).toBe(true); expect(result.current.submission?.operatorCsrfToken).toBe(OPERATOR_CSRF)
    expect(requests.fetch.mock.calls.map(([path]) => path)).toEqual(['/api/v1/research/access', '/api/v1/operator/session', '/api/v1/budget/research/current', '/api/v1/budget/research/current'])
    expect(requests.fetch.mock.calls.every(([, options]) => options?.method === 'GET')).toBe(true)
  })
  it('pauses after sixty successful reads and closes admission until an explicit GET-only refresh', async () => {
    vi.useFakeTimers(); vi.spyOn(Date, 'now').mockRestore(); vi.setSystemTime(new Date(DATE))
    const requests = server(); requests.state.current.activation!.expires_at = '2026-10-28T02:00:00Z'
    const { result } = renderHook(() => useNativeResearchContext(settings())); await flush()
    await act(async () => { await vi.advanceTimersByTimeAsync(59 * 10000) })
    expect(result.current.pollPaused).toBe(true); expect(result.current.canSubmit).toBe(false); expect(result.current.submission).toBeNull()
    expect(requests.fetch.mock.calls.filter(([path]) => path.toString().endsWith('/current'))).toHaveLength(60)
    await act(async () => { await vi.advanceTimersByTimeAsync(30000) })
    expect(requests.fetch).toHaveBeenCalledTimes(62)
    act(() => result.current.refresh()); await flush()
    expect(result.current.pollPaused).toBe(false); expect(result.current.canSubmit).toBe(true)
    expect(requests.fetch).toHaveBeenCalledTimes(65)
    expect(requests.fetch.mock.calls.every(([, options]) => options?.method === 'GET')).toBe(true)
  })
  it('fences delayed current reads across browser CSRF rotation and never exposes stale activation', async () => {
    let resolveOld!: (value: Response) => void, oldSignal: AbortSignal | null = null, reads = 0
    const requests = server((path, options) => path.endsWith('/current') && ++reads === 1 ? new Promise<Response>((resolve) => { oldSignal = options?.signal as AbortSignal; resolveOld = resolve }) : undefined)
    const props = settings(), { result, rerender } = renderHook((input) => useNativeResearchContext(input), { initialProps: props })
    await waitFor(() => expect(oldSignal).not.toBeNull())
    const original = structuredClone(requests.state.current)
    requests.state.current.activation!.manifest_sha256 = 'd'.repeat(64)
    rerender({ ...props, csrfToken: 'c'.repeat(64) })
    expect(result.current.canSubmit).toBe(false)
    await waitFor(() => expect(result.current.phase).toBe('ready'))
    expect(oldSignal!.aborted).toBe(true)
    await act(async () => { resolveOld(Response.json(original)) })
    expect(result.current.submission?.expectedActivationSha256).toBe('d'.repeat(64))
    expect(requests.fetch.mock.calls.every(([, options]) => options?.method === 'GET')).toBe(true)
  })
  it('clears ready authority immediately when entering a disabled/public scope and aborts a later read on unmount', async () => {
    let resolveOld!: (value: Response) => void, delayedSignal: AbortSignal | null = null, delay = false
    const requests = server((path, options) => delay && path.endsWith('/current') ? new Promise<Response>((resolve) => { delayedSignal = options?.signal as AbortSignal; resolveOld = resolve }) : undefined)
    const props = settings(), { result, rerender, unmount } = renderHook((input) => useNativeResearchContext(input), { initialProps: props })
    await waitFor(() => expect(result.current.phase).toBe('ready'))
    rerender({ ...props, enabled: false })
    expect(result.current.canSubmit).toBe(false); expect(result.current.activation).toBeNull(); expect(result.current.submission).toBeNull()
    delay = true; rerender(props)
    await waitFor(() => expect(delayedSignal).not.toBeNull()); unmount()
    expect(delayedSignal!.aborted).toBe(true)
    await act(async () => { resolveOld(Response.json(requests.state.current as CurrentResearchActivation)) })
    expect(requests.fetch.mock.calls.every(([, options]) => options?.method === 'GET')).toBe(true)
  })
})
