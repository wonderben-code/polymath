import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useAssessmentActivity } from './useAssessmentActivity'

const RUN = '11111111-1111-4111-8111-111111111111'
class Stream extends EventTarget {
  static instances: Stream[] = []
  close = vi.fn()
  constructor(readonly url: string) { super(); Stream.instances.push(this) }
  send(kind: string, value?: unknown) { this.dispatchEvent(value === undefined ? new Event(kind) : new MessageEvent(kind, { data: JSON.stringify(value) })) }
}
function setup() { Stream.instances = []; vi.stubGlobal('EventSource', Stream); return { onStatus: vi.fn(), onSessionExpired: vi.fn() } }
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })
describe('read-only reassessment activity', () => {
  it('does not connect until an actual assessment run is selected', () => {
    const callbacks = setup()
    renderHook(() => useAssessmentActivity({ runId: null, enabled: true, ...callbacks }))
    expect(Stream.instances).toHaveLength(0)
    expect(callbacks.onStatus).not.toHaveBeenCalled()
  })
  it('preserves ordered actual events and debounces status reads without model requests', async () => {
    vi.useFakeTimers(); const callbacks = setup()
    const { result } = renderHook(() => useAssessmentActivity({ runId: RUN, enabled: true, ...callbacks }))
    const stream = Stream.instances[0]
    expect(stream.url).toBe(`/api/v1/runs/${RUN}/events?after=0`)
    act(() => { stream.send('open'); stream.send('activity', { sequence: 2, kind: 'assessment_reviewed', summary: 'Synthetic recorded comparison.' }); stream.send('activity', { sequence: 1, kind: 'assessment_started', summary: 'Synthetic recorded start.' }); stream.send('activity', { sequence: 2, kind: 'assessment_reviewed', summary: 'Synthetic recorded comparison.' }) })
    expect(result.current.events.map((event) => event.sequence)).toEqual([1, 2])
    expect(result.current.connection).toBe('live')
    await act(async () => { await vi.advanceTimersByTimeAsync(200) })
    expect(callbacks.onStatus).toHaveBeenCalledTimes(1)
  })
  it('reconnects only the read stream with the saved cursor, and stops after bounded attempts', async () => {
    vi.useFakeTimers(); const callbacks = setup()
    const { result } = renderHook(() => useAssessmentActivity({ runId: RUN, enabled: true, ...callbacks }))
    act(() => Stream.instances[0].send('activity', { sequence: 7, kind: 'checkpoint', summary: 'Saved progress.' }))
    for (const delay of [500, 1500, 4000]) {
      act(() => Stream.instances.at(-1)!.send('error'))
      await act(async () => { await vi.advanceTimersByTimeAsync(delay) })
    }
    act(() => Stream.instances.at(-1)!.send('error'))
    expect(Stream.instances).toHaveLength(4)
    expect(Stream.instances.slice(1).every((stream) => stream.url.endsWith('after=7'))).toBe(true)
    expect(result.current.connection).toBe('error')
    await act(async () => { await vi.advanceTimersByTimeAsync(60000) })
    expect(Stream.instances).toHaveLength(4)
  })
  it('refreshes final saved status on end and retains recorded activity', () => {
    const callbacks = setup()
    const { result } = renderHook(() => useAssessmentActivity({ runId: RUN, enabled: true, ...callbacks }))
    const stream = Stream.instances[0]
    act(() => { stream.send('activity', { sequence: 1, kind: 'assessment_complete', summary: 'Assessment saved.' }); stream.send('end') })
    expect(stream.close).toHaveBeenCalled()
    expect(result.current.connection).toBe('closed')
    expect(result.current.events[0].summary).toBe('Assessment saved.')
    expect(callbacks.onStatus).toHaveBeenCalledTimes(1)
  })
  it('clears private activity on browser expiry and rejects conflicting event records', () => {
    const callbacks = setup()
    const { result, rerender } = renderHook(({ runId }) => useAssessmentActivity({ runId, enabled: true, ...callbacks }), { initialProps: { runId: RUN } })
    act(() => { Stream.instances[0].send('activity', { sequence: 1, kind: 'checkpoint', summary: 'Private progress.' }); Stream.instances[0].send('session_expired') })
    expect(result.current.events).toEqual([])
    expect(callbacks.onSessionExpired).toHaveBeenCalledTimes(1)
    rerender({ runId: '22222222-2222-4222-8222-222222222222' })
    const next = Stream.instances.at(-1)!
    act(() => { next.send('activity', { sequence: 1, kind: 'checkpoint', summary: 'Original event.' }); next.send('activity', { sequence: 1, kind: 'checkpoint', summary: 'Conflicting event.' }) })
    expect(result.current.connection).toBe('error')
    expect(next.close).toHaveBeenCalled()
    expect(result.current.events[0].summary).toBe('Original event.')
  })
  it('ignores late events from a prior selection and closes on unmount', () => {
    const callbacks = setup()
    const { result, rerender, unmount } = renderHook(({ enabled }) => useAssessmentActivity({ runId: RUN, enabled, ...callbacks }), { initialProps: { enabled: true } })
    const stream = Stream.instances[0]
    rerender({ enabled: false })
    act(() => stream.send('activity', { sequence: 1, kind: 'assessment_complete', summary: 'Stale private event.' }))
    expect(result.current.events).toEqual([])
    expect(stream.close).toHaveBeenCalled()
    unmount()
  })
})
