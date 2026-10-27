import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import type { OwnedRun } from './client'

const RUN = '11111111-1111-4111-8111-111111111111'
const SECOND = '22222222-2222-4222-8222-222222222222'
const RESULT = '33333333-3333-4333-8333-333333333333'

class FixtureEventSource extends EventTarget {
  static instances: FixtureEventSource[] = []
  readonly url: string
  close = vi.fn()
  constructor(url: string) { super(); this.url = url; FixtureEventSource.instances.push(this) }
  emit(type: string, value?: unknown, sequence = '') {
    this.dispatchEvent(value === undefined ? new Event(type) : new MessageEvent(type, { data: JSON.stringify(value), lastEventId: sequence }))
  }
}

function run(id = RUN, goal = 'Synthetic saved investigation'): OwnedRun {
  return { id, goal, mode: 'explore', status: 'running', created_at: '2026-10-27T09:00:00Z', updated_at: '2026-10-27T09:01:00Z', result_id: null, error_code: null, external_outcome_unknown: false }
}

function server() {
  const state = {
    runs: [run()], earlier: [] as OwnedRun[], cursor: null as string | null,
    available: false, cancelStatus: 200, runStatus: 200, historyStatus: 200,
    sessionCalls: 0, createCalls: 0,
    getOverride: null as null | ((id: string) => Promise<Response> | null),
    cancelOverride: null as null | (() => Promise<Response>),
  }
  const requests = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const path = input.toString()
    if (path === '/api/v1/health') return Response.json({ status: 'ok', service: 'polymath', version: '0.1.0' })
    if (path === '/api/v1/readiness') return Response.json({ status: 'ready', checks: { database: 'ok', migrations: 'ok' } })
    if (path === '/api/v1/capabilities') return Response.json({ research_available: state.available, meta_configured: false, reason: 'research_not_implemented' })
    if (path === '/api/v1/session') { state.sessionCalls++; return Response.json({ csrf_token: 'a'.repeat(64), expires_at: '2100-01-01T00:00:00Z' }) }
    if (path === '/api/v1/runs' && init?.method === 'POST') {
      state.createCalls++
      const input = JSON.parse(init.body as string) as { mode: OwnedRun['mode']; goal: string }
      const created = { ...run(SECOND, input.goal), mode: input.mode, status: 'queued' }
      state.runs.unshift(created)
      return Response.json(created, { status: 201 })
    }
    if (path === '/api/v1/runs') return Response.json(state.historyStatus === 200 ? { runs: state.runs, next_cursor: state.cursor } : { detail: 'research_session_required' }, { status: state.historyStatus })
    if (path.startsWith('/api/v1/runs?before=')) return Response.json({ runs: state.earlier, next_cursor: null })
    const matched = /^\/api\/v1\/runs\/([^/]+)(\/cancel|\/result)?$/.exec(path)
    if (matched) {
      const selected = state.runs.find((item) => item.id === matched[1]) ?? state.earlier.find((item) => item.id === matched[1])
      if (!selected || state.runStatus !== 200) return Response.json({ detail: 'research_run_not_found' }, { status: state.runStatus === 200 ? 404 : state.runStatus })
      if (!matched[2] && state.getOverride) {
        const override = state.getOverride(selected.id)
        if (override) return override
      }
      if (matched[2] === '/cancel') {
        if (state.cancelOverride) return state.cancelOverride()
        if (state.cancelStatus !== 200) return Response.json({ detail: 'csrf_check_failed' }, { status: state.cancelStatus })
        selected.status = 'cancel_requested'
      }
      if (matched[2] === '/result') return Response.json({
        status: 'insufficient_evidence', finding: { outcome: 'insufficient_evidence', reason: 'The synthetic fixture did not establish a supported connection.' },
        quotes: [], sources: [], events: [], usage: { model_calls: 1, input_tokens_reported: 10, output_tokens_reported: 10, calls_without_usage: 0 }, error_code: null, validation_scope: 'source_identity_and_quote_integrity',
      })
      return Response.json(selected)
    }
    throw new Error('Unexpected synthetic fixture request')
  })
  vi.stubGlobal('fetch', requests)
  vi.stubGlobal('EventSource', FixtureEventSource)
  return { state, requests }
}

async function openSaved() {
  const user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: /Synthetic saved investigation/ }))
  await screen.findByRole('heading', { name: 'Synthetic saved investigation' })
  await waitFor(() => expect(FixtureEventSource.instances).toHaveLength(1))
  return { user, stream: FixtureEventSource.instances[0] }
}

afterEach(() => {
  FixtureEventSource.instances = []
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('session-owned research workspace', () => {
  it('loads real session/history paths without creating a job while execution is unavailable', async () => {
    const { state, requests } = server()
    render(<App />)
    expect(await screen.findByRole('list', { name: 'Your saved investigations' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Explore connections' })).toBeDisabled()
    expect(state.sessionCalls).toBe(1)
    expect(state.createCalls).toBe(0)
    expect(requests.mock.calls.every(([, init]) => init?.credentials === undefined || init.credentials === 'same-origin')).toBe(true)
  })

  it('displays persisted SSE activity, deduplicates replay, loads the final saved result, and closes on end', async () => {
    const { state, requests } = server()
    render(<App />)
    const { stream } = await openSaved()
    expect(stream.url).toBe(`/api/v1/runs/${RUN}/events?after=0`)
    act(() => {
      stream.emit('open')
      stream.emit('activity', { sequence: 1, kind: 'search_completed', summary: 'Searched synthetic fixture literature.' }, '1')
      stream.emit('activity', { sequence: 1, kind: 'search_completed', summary: 'Searched synthetic fixture literature.' }, '1')
    })
    expect(within(screen.getByRole('list', { name: 'Recorded research activity' })).getAllByRole('listitem')).toHaveLength(1)
    state.runs[0].status = 'succeeded'
    state.runs[0].result_id = RESULT
    act(() => stream.emit('end', {}))
    expect(await screen.findByRole('heading', { name: 'No supported connection established' })).toBeInTheDocument()
    expect(screen.getByText('The synthetic fixture did not establish a supported connection.')).toBeInTheDocument()
    expect(stream.close).toHaveBeenCalledOnce()
    expect(FixtureEventSource.instances).toHaveLength(1)
    expect(requests.mock.calls.some(([input]) => input.toString() === `/api/v1/runs/${RUN}/result`)).toBe(true)
  })

  it('sends cancellation with the in-memory CSRF header and retains the pending state', async () => {
    const { requests } = server()
    render(<App />)
    const { user } = await openSaved()
    await user.click(screen.getByRole('button', { name: 'Cancel investigation' }))
    expect(await screen.findByText('Cancellation is pending')).toBeInTheDocument()
    const cancel = requests.mock.calls.find(([input]) => input.toString().endsWith('/cancel'))
    expect(cancel?.[1]).toMatchObject({ method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error', headers: { 'X-CSRF-Token': 'a'.repeat(64) } })
    expect(cancel?.[0].toString()).not.toContain('csrf')
    expect(screen.queryByRole('button', { name: 'Cancel investigation' })).not.toBeInTheDocument()
  })

  it('clears owned data on a stream session expiry and does not automatically create another session', async () => {
    const { state } = server()
    render(<App />)
    const { stream, user } = await openSaved()
    act(() => stream.emit('session_expired', {}))
    expect(await screen.findByText('Your research session expired. Reload saved research to continue.')).toBeInTheDocument()
    expect(screen.queryByRole('list', { name: 'Your saved investigations' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Synthetic saved investigation' })).not.toBeInTheDocument()
    expect(state.sessionCalls).toBe(1)
    expect(stream.close).toHaveBeenCalled()
    state.runs = []
    await user.click(screen.getByRole('button', { name: 'Reload saved research' }))
    await screen.findByText('No saved investigations in this browser session.')
    expect(state.sessionCalls).toBe(2)
  })

  it('clears private data when cancellation is rejected because the cookie/session changed', async () => {
    const { state } = server()
    state.cancelStatus = 403
    render(<App />)
    const { user } = await openSaved()
    await user.click(screen.getByRole('button', { name: 'Cancel investigation' }))
    expect(await screen.findByText('Your research session changed. Reload saved research before trying again.')).toBeInTheDocument()
    expect(screen.queryByRole('list', { name: 'Your saved investigations' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Synthetic saved investigation' })).not.toBeInTheDocument()
    expect(state.sessionCalls).toBe(1)
  })

  it('does not disclose whether an unavailable run belongs to another session', async () => {
    const { state } = server()
    state.runStatus = 404
    render(<App />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /Synthetic saved investigation/ }))
    expect(await screen.findByText('This investigation is unavailable in your current session.')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Synthetic saved investigation' })).not.toBeInTheDocument()
    expect(FixtureEventSource.instances).toHaveLength(0)
  })

  it('loads only the next owned history cursor and does not put authentication in URLs', async () => {
    const { state, requests } = server()
    state.cursor = RUN
    state.earlier = [run(SECOND, 'Older synthetic investigation')]
    render(<App />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Load earlier investigations' }))
    expect(await screen.findByRole('button', { name: /Older synthetic investigation/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Load earlier investigations' })).not.toBeInTheDocument()
    expect(requests.mock.calls.some(([input]) => input.toString() === `/api/v1/runs?before=${RUN}`)).toBe(true)
    expect(requests.mock.calls.some(([input]) => input.toString().includes('a'.repeat(64)))).toBe(false)
  })

  it('queues a job only after the capability, actual session, and valid prompt are ready', async () => {
    const { state, requests } = server()
    state.available = true
    state.runs = []
    render(<App />)
    const user = userEvent.setup()
    await screen.findByText('No saved investigations in this browser session.')
    expect(screen.getByRole('button', { name: 'Explore connections' })).toBeDisabled()
    await user.type(screen.getByRole('textbox'), 'A synthetic creation test')
    await user.click(screen.getByRole('button', { name: 'Explore connections' }))
    expect(await screen.findByRole('heading', { name: 'A synthetic creation test' })).toBeInTheDocument()
    expect(state.createCalls).toBe(1)
    const create = requests.mock.calls.find(([input, init]) => input.toString() === '/api/v1/runs' && init?.method === 'POST')
    expect(create?.[1]?.headers).toMatchObject({ 'X-CSRF-Token': 'a'.repeat(64) })
    expect(create?.[1]?.body).toBe(JSON.stringify({ mode: 'explore', goal: 'A synthetic creation test' }))
  })

  it('never submits through Enter or form events when the server gate is false', async () => {
    const { state } = server()
    render(<App />)
    const user = userEvent.setup()
    await screen.findByRole('list', { name: 'Your saved investigations' })
    await user.type(screen.getByRole('textbox'), 'A synthetic guarded request{Enter}')
    fireEvent.submit(screen.getByRole('textbox').closest('form')!)
    expect(state.createCalls).toBe(0)
  })

  it('uses the last persisted cursor on bounded reconnect and never reconnects after end', async () => {
    const { state } = server()
    render(<App />)
    const { stream } = await openSaved()
    act(() => stream.emit('activity', { sequence: 9, kind: 'checkpoint', summary: 'Saved synthetic progress.' }, '9'))
    vi.useFakeTimers()
    act(() => stream.emit('error'))
    await act(async () => { await vi.advanceTimersByTimeAsync(500) })
    expect(FixtureEventSource.instances).toHaveLength(2)
    const replacement = FixtureEventSource.instances[1]
    expect(replacement.url).toBe(`/api/v1/runs/${RUN}/events?after=9`)
    state.runs[0].status = 'cancelled'
    act(() => replacement.emit('end', {}))
    await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
    expect(FixtureEventSource.instances).toHaveLength(2)
    expect(replacement.close).toHaveBeenCalledOnce()
  })

  it('bounds silent stream opens and automatic retries, while explicit heartbeats extend a live stream', async () => {
    server()
    render(<App />)
    const { stream } = await openSaved()
    vi.useFakeTimers()
    act(() => stream.emit('open'))
    await act(async () => { await vi.advanceTimersByTimeAsync(40000) })
    act(() => stream.emit('heartbeat', {}))
    await act(async () => { await vi.advanceTimersByTimeAsync(40000) })
    expect(stream.close).not.toHaveBeenCalled()
    await act(async () => { await vi.advanceTimersByTimeAsync(5500) })
    expect(FixtureEventSource.instances).toHaveLength(2)
    expect(stream.close).toHaveBeenCalledOnce()
    await act(async () => { await vi.advanceTimersByTimeAsync(11500) })
    expect(FixtureEventSource.instances).toHaveLength(3)
    await act(async () => { await vi.advanceTimersByTimeAsync(14000) })
    expect(FixtureEventSource.instances).toHaveLength(4)
    await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
    expect(FixtureEventSource.instances).toHaveLength(4)
    expect(screen.getByText(/The activity connection stopped after several attempts/)).toBeInTheDocument()
    await act(async () => { await vi.advanceTimersByTimeAsync(100000) })
    expect(FixtureEventSource.instances).toHaveLength(4)
  })

  it('does not let a stale GET overwrite a newer cancellation response', async () => {
    const { state } = server()
    render(<App />)
    const { user, stream } = await openSaved()
    let resolve: ((response: Response) => void) | undefined
    const stale = Response.json({ ...state.runs[0] })
    state.getOverride = () => new Promise<Response>((done) => { resolve = done })
    act(() => stream.emit('end', {}))
    await waitFor(() => expect(resolve).toBeDefined())
    await user.click(screen.getByRole('button', { name: 'Cancel investigation' }))
    expect(await screen.findByText('Cancellation is pending')).toBeInTheDocument()
    await act(async () => { resolve!(stale) })
    expect(screen.getByText('Cancellation is pending')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Cancel investigation' })).not.toBeInTheDocument()
  })

  it('does not show an old investigation’s cancellation failure on a newly selected run', async () => {
    const { state } = server()
    state.runs.push(run(SECOND, 'Second synthetic investigation'))
    render(<App />)
    const { user } = await openSaved()
    let resolve: ((response: Response) => void) | undefined
    state.cancelOverride = () => new Promise<Response>((done) => { resolve = done })
    await user.click(screen.getByRole('button', { name: 'Cancel investigation' }))
    await waitFor(() => expect(resolve).toBeDefined())
    await user.click(screen.getByRole('button', { name: /Second synthetic investigation/ }))
    await screen.findByRole('heading', { name: 'Second synthetic investigation' })
    await act(async () => { resolve!(Response.json({ detail: 'research_storage_unavailable' }, { status: 503 })) })
    expect(screen.queryByText('Saved research could not be loaded. Check the connection and try again.')).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Second synthetic investigation' })).toBeInTheDocument()
  })
})
