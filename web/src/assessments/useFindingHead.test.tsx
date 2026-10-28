import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useFindingHead } from './useFindingHead'
import { ASSESSMENT, CANDIDATE, head, ROOT, RUN } from './fixtures.test-support'

function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done }); return { promise, resolve } }
function currentCandidate() { return head({ requested_result_id: CANDIDATE, current_result_id: CANDIDATE, current_run_id: RUN, revision: 1, last_assessment_id: ASSESSMENT }) }
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

describe('current finding authorization checks', () => {
  it('stays idle without an enabled exact result and fails closed until its read completes', async () => {
    const response = deferred<Response>(), fetch = vi.fn(() => response.promise), onSessionExpired = vi.fn()
    vi.stubGlobal('fetch', fetch)
    const { result, rerender } = renderHook(({ enabled, resultId }) => useFindingHead({ enabled, resultId, onSessionExpired }), { initialProps: { enabled: false, resultId: ROOT as string | null } })
    expect(result.current).toMatchObject({ head: null, phase: 'idle', canGenerate: false })
    expect(fetch).not.toHaveBeenCalled()
    rerender({ enabled: true, resultId: null })
    expect(fetch).not.toHaveBeenCalled()
    rerender({ enabled: true, resultId: ROOT })
    expect(result.current).toMatchObject({ head: null, phase: 'loading', canGenerate: false })
    await act(async () => response.resolve(Response.json(head())))
    expect(result.current).toMatchObject({ phase: 'ready', canGenerate: true, pollPaused: false })
    expect(fetch).toHaveBeenCalledExactlyOnceWith(`/api/v1/assessments/heads/${ROOT}`, expect.objectContaining({ method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error' }))
    expect(onSessionExpired).not.toHaveBeenCalled()
  })

  it.each([
    head({ current_result_id: CANDIDATE, current_run_id: RUN, revision: 1, last_assessment_id: ASSESSMENT, is_current: false, is_supported: false, is_historical: true, can_generate: false }),
    head({ state: 'unsupported', revision: 1, last_assessment_id: ASSESSMENT, is_supported: false, is_historical: true, can_generate: false }),
  ])('never authorizes historical or unsupported source generation', async (saved) => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(saved)))
    const { result } = renderHook(() => useFindingHead({ enabled: true, resultId: ROOT, onSessionExpired: vi.fn() }))
    await waitFor(() => expect(result.current.phase).toBe('ready'))
    expect(result.current.head).toEqual(saved)
    expect(result.current.canGenerate).toBe(false)
  })

  it('clears a previously supported head on polling failure and waits for an explicit refresh', async () => {
    vi.useFakeTimers()
    const fetch = vi.fn(async (_path: string, _init?: RequestInit) => Response.json(head())); vi.stubGlobal('fetch', fetch)
    const { result } = renderHook(() => useFindingHead({ enabled: true, resultId: ROOT, onSessionExpired: vi.fn() }))
    await act(async () => {})
    expect(result.current.canGenerate).toBe(true)
    fetch.mockImplementation(async () => Response.json({ detail: 'private_storage_error' }, { status: 503 }))
    await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
    expect(result.current).toMatchObject({ head: null, phase: 'error', canGenerate: false })
    await act(async () => { await vi.advanceTimersByTimeAsync(60000) })
    expect(fetch).toHaveBeenCalledTimes(2)
    fetch.mockImplementation(async () => Response.json(head()))
    await act(async () => result.current.refresh())
    expect(result.current).toMatchObject({ phase: 'ready', canGenerate: true })
    expect(fetch).toHaveBeenCalledTimes(3)
  })

  it('pauses after sixty read checks and withdraws authorization until an explicit refresh', async () => {
    vi.useFakeTimers()
    const fetch = vi.fn(async (_path: string, _init?: RequestInit) => Response.json(head())); vi.stubGlobal('fetch', fetch)
    const { result } = renderHook(() => useFindingHead({ enabled: true, resultId: ROOT, onSessionExpired: vi.fn() }))
    await act(async () => {})
    await act(async () => { await vi.advanceTimersByTimeAsync(590000) })
    expect(fetch).toHaveBeenCalledTimes(60)
    expect(result.current).toMatchObject({ phase: 'ready', pollPaused: true, canGenerate: false })
    expect(result.current.head).toEqual(head())
    await act(async () => { await vi.advanceTimersByTimeAsync(60000) })
    expect(fetch).toHaveBeenCalledTimes(60)
    await act(async () => result.current.refresh())
    expect(fetch).toHaveBeenCalledTimes(61)
    expect(result.current).toMatchObject({ phase: 'ready', pollPaused: false, canGenerate: true })
    expect(fetch.mock.calls.every(([, init]) => init?.method === 'GET')).toBe(true)
  })

  it.each([401, 403])('clears private head state on browser HTTP %s and stops polling', async (status) => {
    vi.useFakeTimers()
    const fetch = vi.fn(async () => Response.json(head())), onSessionExpired = vi.fn(); vi.stubGlobal('fetch', fetch)
    const { result } = renderHook(() => useFindingHead({ enabled: true, resultId: ROOT, onSessionExpired }))
    await act(async () => {})
    fetch.mockImplementation(async () => Response.json({ detail: 'private_session_error' }, { status }))
    await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
    expect(result.current).toMatchObject({ head: null, phase: 'expired', canGenerate: false })
    expect(onSessionExpired).toHaveBeenCalledTimes(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(60000) })
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('aborts stale reads and ignores late private responses after selection, disable and unmount', async () => {
    const first = deferred<Response>(), second = deferred<Response>(), third = deferred<Response>()
    const fetch = vi.fn<(path: string, init?: RequestInit) => Promise<Response>>().mockImplementationOnce(() => first.promise).mockImplementationOnce(() => second.promise).mockImplementationOnce(() => third.promise)
    vi.stubGlobal('fetch', fetch)
    const onSessionExpired = vi.fn()
    const { result, rerender, unmount } = renderHook(({ resultId, enabled }) => useFindingHead({ enabled, resultId, onSessionExpired }), { initialProps: { resultId: ROOT, enabled: true } })
    const firstSignal = fetch.mock.calls[0][1]?.signal
    rerender({ resultId: CANDIDATE, enabled: true })
    expect(firstSignal?.aborted).toBe(true)
    await act(async () => { second.resolve(Response.json(currentCandidate())); first.resolve(Response.json(head())) })
    expect(result.current.head?.requested_result_id).toBe(CANDIDATE)
    rerender({ resultId: ROOT, enabled: true })
    const thirdSignal = fetch.mock.calls[2][1]?.signal
    rerender({ resultId: ROOT, enabled: false })
    expect(thirdSignal?.aborted).toBe(true)
    await act(async () => third.resolve(Response.json(head())))
    expect(result.current).toMatchObject({ head: null, phase: 'idle', canGenerate: false })
    unmount()
    expect(fetch.mock.calls.every(([, init]) => init?.method === 'GET')).toBe(true)
    expect(onSessionExpired).not.toHaveBeenCalled()
  })
})
