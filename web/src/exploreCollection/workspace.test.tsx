import { act, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { pythonFixtures } from './fixtures'
import { parseExploreBridgeHistory, parseExploreCollectionSummary } from './parsers'
import { ExploreWorkspace } from './ExploreWorkspace'
import { useExploreCollectionWorkspace } from './useExploreCollectionWorkspace'
import { NativeResearchProvider } from '../research/nativeContext'
import type { ReactNode } from 'react'
import type { NativeResearchContextValue } from '../research/nativeContext'
import type { OwnedRun } from '../research/client'
const total = () => parseExploreCollectionSummary(structuredClone(pythonFixtures.summary))
const bridges = () => parseExploreBridgeHistory(structuredClone(pythonFixtures.bridges))
const RUN = total().run_id, CSRF = 'a'.repeat(64)
const native: NativeResearchContextValue = { required: true, phase: 'ready', activation: null, submission: null, access: { audience: 'public_sessions' } as NativeResearchContextValue['access'], canSubmit: false, reason: null, pollPaused: false, refresh: vi.fn(), invalidate: vi.fn() }
const wrapper = ({ children }: { children: ReactNode }) => <NativeResearchProvider value={native}>{children}</NativeResearchProvider>
const props = () => ({ runId: RUN, intent: total().explore_collection_intent, enabled: true, csrfToken: CSRF as string | null, activitySequence: 0, runStatus: 'running', onSessionExpired: vi.fn() })
const run = () => ({ id: RUN, task_kind: 'research', mode: 'explore', goal: 'Synthetic explicit Explore.', status: 'interrupted', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', result_id: null, error_code: null, external_outcome_unknown: false, explore_collection_intent: total().explore_collection_intent }) satisfies OwnedRun
async function flush() { await act(async () => { await vi.advanceTimersByTimeAsync(0) }) }
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })
describe('saved Explore collection workspace', () => {
  it('shows actual model order, exclusions, original scores and exact citations, then opens only the exact saved result', async () => {
    const summary = total(), saved = bridges(), onOpen = vi.fn(), fetch = vi.fn(async (path: RequestInfo | URL) => Response.json(String(path).endsWith('/bridges') ? saved : summary)); vi.stubGlobal('fetch', fetch)
    render(<ExploreWorkspace run={run()} events={[]} enabled csrfToken={CSRF} onSessionExpired={vi.fn()} onOpenFinding={onOpen} />)
    const ranked = await screen.findByRole('region', { name: 'Model-ranked bridges' })
    const cards = within(ranked).getAllByRole('article'); expect(cards[0]).toHaveTextContent('15/100'); expect(cards[1]).toHaveTextContent('55/100')
    expect(screen.getByRole('region', { name: 'Saved bridges excluded from this ranking' })).toHaveTextContent('95/100')
    expect(within(cards[0]).getAllByText(saved.stages[1].result.quotes[0].quote).length).toBeGreaterThan(0)
    fireEvent.click(within(cards[0]).getByRole('button')); expect(onOpen).toHaveBeenCalledExactlyOnceWith(summary.rankings.at(-1)!.ranked[0].reference.result_id)
    expect(fetch.mock.calls).toHaveLength(2); expect(fetch.mock.calls.every(([path]) => String(path).startsWith('/api/v1/runs/'))).toBe(true)
  })
  it('coalesces activity reads for15s and makes one immediate terminal pair while preserving final preference on cancellation', async () => {
    vi.useFakeTimers(); const summary = { ...total(), run_status: 'running' }, fetch = vi.fn(async (path: RequestInfo | URL) => Response.json(String(path).endsWith('/bridges') ? bridges() : summary)); vi.stubGlobal('fetch', fetch)
    const first = props(), view = renderHook((input) => useExploreCollectionWorkspace(input), { wrapper, initialProps: first }); await flush(); expect(fetch).toHaveBeenCalledTimes(2)
    for (let index = 1; index <= 30; index++) { view.rerender({ ...first, activitySequence: index }); await act(async () => { await vi.advanceTimersByTimeAsync(300) }) }
    expect(fetch).toHaveBeenCalledTimes(2); await act(async () => { await vi.advanceTimersByTimeAsync(6000) }); expect(fetch).toHaveBeenCalledTimes(4)
    summary.run_status = 'cancelled'; view.rerender({ ...first, runStatus: 'cancelled', activitySequence: 31 }); await flush(); expect(fetch).toHaveBeenCalledTimes(6)
    expect(view.result.current.summary?.run_status).toBe('cancelled'); expect(view.result.current.summary?.selected_final_ranking).toEqual(summary.selected_final_ranking)
    await act(async () => { await vi.advanceTimersByTimeAsync(60000) }); expect(fetch).toHaveBeenCalledTimes(6)
  })
  it('honors read Retry-After even for terminal or manual refresh and retains earlier exact records', async () => {
    vi.useFakeTimers(); let limited = false; const fetch = vi.fn(async (path: RequestInfo | URL) => limited ? Response.json({ detail: 'public_request_rate_limited' }, { status: 429, headers: { 'Retry-After': '27' } }) : Response.json(String(path).endsWith('/bridges') ? bridges() : { ...total(), run_status: 'running' })); vi.stubGlobal('fetch', fetch)
    const input = props(), view = renderHook((value) => useExploreCollectionWorkspace(value), { wrapper, initialProps: input }); await flush(); limited = true; await act(async () => { await vi.advanceTimersByTimeAsync(15000) }); expect(fetch).toHaveBeenCalledTimes(3); expect(view.result.current.stages).toHaveLength(3)
    act(() => view.result.current.refresh()); view.rerender({ ...input, runStatus: 'cancelled' }); await act(async () => { await vi.advanceTimersByTimeAsync(26000) }); expect(fetch).toHaveBeenCalledTimes(3)
    limited = false; await act(async () => { await vi.advanceTimersByTimeAsync(1000) }); expect(fetch).toHaveBeenCalledTimes(5)
  })
  it('clears and aborts private records on expiry and ignores a late prior owner response', async () => {
    let resolve!: (value: Response) => void, signal: AbortSignal | undefined
    const fetch = vi.fn(async (path: RequestInfo | URL, options?: RequestInit) => String(path).endsWith('/bridges') ? new Promise<Response>((done) => { resolve = done; signal = options?.signal as AbortSignal }) : Response.json(total())); vi.stubGlobal('fetch', fetch)
    const input = props(), view = renderHook((value) => useExploreCollectionWorkspace(value), { initialProps: input }); await waitFor(() => expect(resolve).toBeDefined()); view.rerender({ ...input, enabled: false, csrfToken: null }); await act(async () => resolve(Response.json(bridges()))); expect(signal?.aborted).toBe(true); expect(view.result.current.stages).toEqual([]); expect(view.result.current.summary).toBeNull(); expect(fetch).toHaveBeenCalledTimes(1)
  })
  it('rejects a changed immutable ranking across refreshes without erasing the prior saved decision', async () => {
    let mutated = false; vi.stubGlobal('fetch', vi.fn(async (path: RequestInfo | URL) => { const value = total(); if (mutated) value.rankings.at(-1)!.ranked[0].rationale = 'Changed immutable decision.'; return Response.json(String(path).endsWith('/bridges') ? bridges() : value) }))
    const view = renderHook(() => useExploreCollectionWorkspace(props())); await waitFor(() => expect(view.result.current.phase).toBe('ready')); const initial = view.result.current.summary
    mutated = true; act(() => view.result.current.refresh()); await waitFor(() => expect(view.result.current.paused).toBe(true)); expect(view.result.current.summary).toEqual(initial); expect(view.result.current.error).toMatch(/could not be verified/)
  })
})
