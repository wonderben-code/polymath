import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { WatchWorkspace } from './WatchWorkspace'
import { capabilities, detail, RESULT, RUN, source, SOURCE, tick, WATCH, watch } from './fixtures.test-support'
import type { LiteratureWatch, WatchTick } from './client'

function server({ available = true, initial = [watch()], initialTicks = [tick()] }: { available?: boolean; initial?: LiteratureWatch[]; initialTicks?: WatchTick[] } = {}) {
  let saved = initial, history = initialTicks
  const fetch = vi.fn(async (path: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(path), action = init?.method === 'POST'
    if (url.endsWith('/capabilities')) return Response.json(capabilities(available))
    if (url === '/api/v1/knowledge/watches' && action) { const body = JSON.parse(String(init?.body)); saved = [watch(body)]; return Response.json(saved[0], { status: 201 }) }
    if (url.startsWith('/api/v1/knowledge/watches?')) return Response.json({ watches: saved, next_cursor: null })
    if (url.endsWith(`/sources/${SOURCE}`)) return Response.json(detail())
    if (url.includes('/sources?')) return Response.json({ sources: saved[0]?.source_count ? [source()] : [], next_cursor: null })
    if (url.includes('/ticks?')) return Response.json({ ticks: history, next_cursor: null })
    if (url.endsWith('/pause') && action) { saved = [watch({ ...saved[0], enabled: false, active_run_id: null, active_status: null })]; return Response.json(saved[0]) }
    if (url.endsWith('/resume') && action) { saved = [watch({ ...saved[0], enabled: true })]; return Response.json(saved[0]) }
    if (url.endsWith('/check') && action) { saved = [watch({ ...saved[0], active_run_id: RUN, active_status: 'queued' })]; history = [tick({ status: 'queued', added_sources: 0, diagnostics: {} })]; return Response.json(history[0], { status: 201 }) }
    if (url === `/api/v1/knowledge/watches/${WATCH}`) return Response.json(saved[0])
    throw new Error(`Unexpected fixture path ${url}`)
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}
async function choose() { await userEvent.click(await screen.findByRole('button', { name: /Synthetic watch/ })); return screen.findByRole('region', { name: 'Selected literature watch' }) }
async function openCreate() { await userEvent.click(screen.getByRole('button', { name: 'Create a literature watch' })) }
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

describe('scheduled literature reading', () => {
  it('makes no calls without a ready browser session and never seeds fake watch data', () => {
    const fetch = server()
    render(<WatchWorkspace enabled={false} csrfToken={null} onSessionExpired={vi.fn()} />)
    expect(fetch).not.toHaveBeenCalled()
    expect(screen.queryByText('Synthetic watch')).not.toBeInTheDocument()
  })
  it('keeps saved reads and pause available while default collection is disabled', async () => {
    const fetch = server({ available: false })
    render(<WatchWorkspace enabled csrfToken="csrf" onSessionExpired={vi.fn()} />)
    await screen.findByText(/Free-provider reading is disabled/)
    await openCreate()
    expect(screen.getByRole('button', { name: 'Create literature watch' })).toBeDisabled()
    await choose()
    expect(screen.getByRole('button', { name: 'Check providers now' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Pause reading' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Resume reading' })).toBeDisabled())
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
    expect(String(fetch.mock.calls.find(([, init]) => init?.method === 'POST')?.[0]).endsWith('/pause')).toBe(true)
  })
  it('requires an explicit bounded query and binds optional exact finding without any model call', async () => {
    const fetch = server({ initial: [], initialTicks: [] })
    render(<WatchWorkspace enabled csrfToken="csrf" findings={[{ id: RESULT, title: 'Saved synthetic finding' }]} onSessionExpired={vi.fn()} />)
    await screen.findByText(/No literature watches have been saved/)
    await openCreate()
    expect(screen.getByRole('button', { name: 'Create literature watch' })).toBeDisabled()
    await userEvent.type(screen.getByRole('textbox', { name: 'Watch name' }), 'My literature question')
    await userEvent.type(screen.getByRole('textbox', { name: 'Literature search query' }), 'A&B %_ model-free reading')
    await userEvent.selectOptions(screen.getByLabelText('Reading interval'), '21600')
    await userEvent.selectOptions(screen.getByLabelText(/Link a saved finding/), RESULT)
    await userEvent.click(screen.getByRole('button', { name: 'Create literature watch' }))
    await screen.findByRole('region', { name: 'Selected literature watch' })
    const posts = fetch.mock.calls.filter(([, init]) => init?.method === 'POST')
    expect(posts).toHaveLength(1)
    expect(JSON.parse(String(posts[0][1]?.body))).toEqual({ label: 'My literature question', query: 'A&B %_ model-free reading', interval_seconds: 21600, source_result_id: RESULT })
    expect(fetch.mock.calls.every(([path]) => String(path).includes('/knowledge/watches'))).toBe(true)
    expect(screen.getByText(/at most seven days/)).toBeVisible()
    expect(screen.getByText(/does not call the research model/)).toBeVisible()
  })
  it('shows actual additions, incomplete provider coverage and owned original source text as unassessed', async () => {
    const fetch = server({ initial: [watch({ source_count: 1 })] })
    render(<WatchWorkspace enabled csrfToken="csrf" onSessionExpired={vi.fn()} />)
    await choose()
    expect(screen.getByText(/Provider failures make this check incomplete/)).toBeVisible()
    expect(screen.getByText(/More provider pages may remain/)).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: /Read saved source:/ }))
    const inspection = await screen.findByRole('region', { name: 'Saved unassessed source text' })
    expect(inspection).toHaveTextContent('Synthetic original abstract')
    expect(inspection).toHaveTextContent('Unassessed source text')
    await userEvent.click(within(inspection).getByText('Saved source integrity'))
    expect(inspection).toHaveTextContent('a'.repeat(64))
    expect(fetch.mock.calls.some(([path]) => String(path).endsWith(`/sources/${SOURCE}`))).toBe(true)
    expect(screen.queryByText('private-provider-cursor')).not.toBeInTheDocument()
  })
  it('never creates a check from a read or from a status poll, and prevents synchronous double submission', async () => {
    const fetch = server()
    render(<WatchWorkspace enabled csrfToken="csrf" onSessionExpired={vi.fn()} />)
    await choose()
    const button = screen.getByRole('button', { name: 'Check providers now' })
    fireEvent.click(button); fireEvent.click(button)
    await screen.findByText(/Current provider check: queued/)
    expect(fetch.mock.calls.filter(([path, init]) => String(path).endsWith('/check') && init?.method === 'POST')).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'Check providers now' })).toBeDisabled()
  })
  it('locks unknown writes until an explicit owned reload, with no blind retry', async () => {
    const fetch = server()
    render(<WatchWorkspace enabled csrfToken="csrf" onSessionExpired={vi.fn()} />)
    await choose()
    fetch.mockImplementationOnce(async () => { throw new TypeError('response lost') })
    await userEvent.click(screen.getByRole('button', { name: 'Pause reading' }))
    await screen.findByText(/last request’s outcome is unknown/)
    expect(screen.getByRole('button', { name: 'Pause reading' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Check providers now' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Refresh watches' }))
    await waitFor(() => expect(screen.queryByText(/last request’s outcome is unknown/)).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Pause reading' })).toBeEnabled()
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
  })
  it('shows throttle feedback and avoids claiming no literature from zero additions', async () => {
    const fetch = server({ initialTicks: [tick({ added_sources: 0, diagnostics: {} })] })
    render(<WatchWorkspace enabled csrfToken="csrf" onSessionExpired={vi.fn()} />)
    await choose()
    expect(screen.getByText(/does not show that no relevant papers exist/)).toBeVisible()
    fetch.mockImplementationOnce(async () => Response.json({ detail: 'watch_check_throttled' }, { status: 429 }))
    await userEvent.click(screen.getByRole('button', { name: 'Check providers now' }))
    await screen.findByText(/was checked recently/)
    expect(screen.getByRole('button', { name: 'Check providers now' })).toBeEnabled()
  })
  it('explains capped partial checks even when a skipped provider has no diagnostic', async () => {
    server({ initialTicks: [tick({ ingestion_status: 'partial', error_code: 'source_limit_reached', diagnostics: {} })] })
    render(<WatchWorkspace enabled csrfToken="csrf" onSessionExpired={vi.fn()} />)
    await choose()
    expect(screen.getByText('Source storage limit reached')).toBeVisible()
    expect(screen.getByText(/Provider positions were preserved and papers may remain unread/)).toBeVisible()
    expect(screen.getByText(/This check has partial coverage/)).toBeVisible()
  })
  it('clears all private state on a browser authorization error and blocks stale responses', async () => {
    const fetch = server({ initial: [watch({ source_count: 1 })] }), expired = vi.fn()
    render(<WatchWorkspace enabled csrfToken="csrf" onSessionExpired={expired} />)
    await choose()
    await userEvent.click(screen.getByRole('button', { name: /Read saved source:/ }))
    await screen.findByText('Synthetic original abstract. This fixture is not a research finding.')
    fetch.mockImplementation(async () => Response.json({}, { status: 401 }))
    await userEvent.click(screen.getByRole('button', { name: 'Refresh watches' }))
    await screen.findByText(/Private watches and sources have been cleared/)
    expect(expired).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('Synthetic original abstract. This fixture is not a research finding.')).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Selected literature watch' })).not.toBeInTheDocument()
  })
  it('clears on session rotation, aborts reads, and leaves no persisted credentials', async () => {
    const fetch = server(), expired = vi.fn()
    const { rerender, unmount } = render(<WatchWorkspace enabled csrfToken="csrf-one" onSessionExpired={expired} />)
    await choose()
    const completedReads = fetch.mock.calls.length
    fetch.mockImplementation((_path, init) => new Promise<Response>((_resolve, reject) => init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))))
    rerender(<WatchWorkspace enabled={false} csrfToken={null} onSessionExpired={expired} />)
    expect(screen.queryByText('Synthetic watch')).not.toBeInTheDocument()
    rerender(<WatchWorkspace enabled csrfToken="csrf-two" onSessionExpired={expired} />)
    await waitFor(() => expect(fetch.mock.calls.length).toBeGreaterThan(completedReads))
    unmount()
    expect(fetch.mock.calls.slice(completedReads).every(([, init]) => init?.signal?.aborted)).toBe(true)
    expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0)
  })
  it('polls saved state with GET only and expires private records at the exact session boundary', async () => {
    vi.useFakeTimers()
    const fetch = server({ initial: [watch({ expires_at: new Date(Date.now() + 6500).toISOString() })] }), expired = vi.fn()
    render(<WatchWorkspace enabled csrfToken="csrf" onSessionExpired={expired} />)
    await vi.waitFor(() => expect(screen.getByRole('button', { name: /Synthetic watch/ })).toBeInTheDocument())
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(fetch.mock.calls.filter(([path]) => String(path).includes('?limit=20'))).toHaveLength(2)
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    expect(expired).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('Synthetic watch')).not.toBeInTheDocument()
  })
  it('retains explicitly paged source versions while polling fresh check status', async () => {
    vi.useFakeTimers()
    const fetch = server({ initial: [watch({ source_count: 21 })] }), original = fetch.getMockImplementation()!
    let finished = false
    const papers = Array.from({ length: 21 }, (_, index) => source({ id: `00000000-0000-4000-8000-${String(index + 1).padStart(12, '0')}`, title: `Synthetic paged source ${index + 1}` }))
    fetch.mockImplementation(async (path, init) => {
      const url = String(path)
      if (url.includes('/sources?')) return Response.json({ sources: url.includes('&before=') ? papers.slice(20) : papers.slice(0, 20), next_cursor: url.includes('&before=') ? null : papers[19].id })
      if (url.includes('/ticks?')) return Response.json({ ticks: [tick({ status: finished ? 'succeeded' : 'queued', diagnostics: {} })], next_cursor: null })
      return original(path, init)
    })
    render(<WatchWorkspace enabled csrfToken="csrf" onSessionExpired={vi.fn()} />)
    await vi.waitFor(() => expect(screen.getByRole('button', { name: /Synthetic watch/ })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /Synthetic watch/ }))
    await vi.waitFor(() => expect(screen.getAllByRole('button', { name: /Read saved source:/ })).toHaveLength(20))
    fireEvent.click(screen.getByRole('button', { name: 'Load older sources' }))
    await vi.waitFor(() => expect(screen.getAllByRole('button', { name: /Read saved source:/ })).toHaveLength(21))
    finished = true
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(screen.getAllByRole('button', { name: /Read saved source:/ })).toHaveLength(21)
    expect(screen.getByRole('region', { name: 'Literature check history' })).toHaveTextContent('succeeded')
    expect(screen.queryByRole('button', { name: 'Load older sources' })).not.toBeInTheDocument()
  })
})
