import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { KnowledgeBrowser } from './KnowledgeBrowser'
import { capabilities } from '../ingestion/fixtures.test-support'

const CSRF = 'a'.repeat(64), NEXT_CSRF = 'b'.repeat(64)
const RUN = '11111111-1111-4111-8111-111111111111'
const RESULT = '22222222-2222-4222-8222-222222222222'
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done }); return { promise, resolve } }
function records(label: string) {
  const common = { run_id: RUN, result_id: RESULT, source_id: null, content_kind: null, task_kind: 'research' }
  const coverage = { scope: 'current_browser_session', findings_considered: 1 }
  return { search: { items: [{ ...common, id: 'finding:synthetic', type: 'finding', title: label, summary: 'Synthetic privacy fixture, not scientific evidence.' }], truncated: false, coverage }, graph: { nodes: [{ ...common, id: 'finding:synthetic', kind: 'finding', label }], edges: [], truncated: false, coverage } }
}
afterEach(() => vi.unstubAllGlobals())

describe('knowledge browser session boundaries', () => {
  it('rejects delayed library success after a child browser expiry even while its parent stays enabled', async () => {
    const search = deferred<Response>(), graph = deferred<Response>(), expired = vi.fn(), open = vi.fn()
    const state = { blocked: true, pending: true }
    const stale = records('Delayed private source from expired session'), fresh = records('Fresh synthetic session library')
    const fetch = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit): Promise<Response> => {
      const path = input.toString()
      if (path.endsWith('/watches/capabilities')) return Response.json(capabilities(false))
      if (path.includes('/watches?')) return Response.json(state.blocked ? { detail: 'private_session_details' } : { watches: [], next_cursor: null }, { status: state.blocked ? 401 : 200 })
      if (path.startsWith('/api/v1/knowledge/graph?')) return state.pending ? graph.promise : Response.json(fresh.graph)
      if (path.startsWith('/api/v1/knowledge?')) return state.pending ? search.promise : Response.json(fresh.search)
      throw new Error('Unexpected synthetic fixture request')
    })
    vi.stubGlobal('fetch', fetch)
    const rendered = render(<KnowledgeBrowser enabled csrfToken={CSRF} onOpenRun={open} onSessionExpired={expired} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Your research session has expired or changed.')
    expect(expired).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('searchbox')).toBeDisabled()
    await act(async () => { search.resolve(Response.json(stale.search)); graph.resolve(Response.json(stale.graph)) })
    expect(screen.queryByRole('heading', { name: 'Delayed private source from expired session' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Select map node/ })).not.toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Your research session has expired or changed.')
    state.blocked = false; state.pending = false
    rendered.rerender(<KnowledgeBrowser enabled csrfToken={NEXT_CSRF} onOpenRun={open} onSessionExpired={expired} />)
    expect(await screen.findByRole('heading', { name: 'Fresh synthetic session library' })).toBeVisible()
    expect(screen.getByRole('searchbox')).toHaveValue('')
    expect(fetch.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true)
  })

  it('clears the old private query and aborts its delayed reads when the browser CSRF scope changes', async () => {
    const search = deferred<Response>(), graph = deferred<Response>(), expired = vi.fn(), open = vi.fn()
    const old = records('Initial synthetic private library'), fresh = records('New browser session library'), delayed = records('Delayed old-query private finding')
    let changedSession = false
    const fetch = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit): Promise<Response> => {
      const path = input.toString()
      if (path.endsWith('/watches/capabilities')) return Response.json(capabilities(false))
      if (path.includes('/watches?')) return Response.json({ watches: [], next_cursor: null })
      const parsed = new URL(path, 'https://example.org')
      if (parsed.searchParams.get('q')) return parsed.pathname.endsWith('/graph') ? graph.promise : search.promise
      return Response.json(parsed.pathname.endsWith('/graph') ? (changedSession ? fresh.graph : old.graph) : (changedSession ? fresh.search : old.search))
    })
    vi.stubGlobal('fetch', fetch)
    const rendered = render(<KnowledgeBrowser enabled csrfToken={CSRF} onOpenRun={open} onSessionExpired={expired} />)
    await screen.findByRole('heading', { name: 'Initial synthetic private library' })
    await userEvent.type(screen.getByRole('searchbox'), 'Old private query')
    await userEvent.click(screen.getByRole('button', { name: 'Search library' }))
    await waitFor(() => expect(fetch.mock.calls.filter(([path]) => path.toString().includes('Old+private+query'))).toHaveLength(2))
    const oldSignals = fetch.mock.calls.filter(([path]) => path.toString().includes('Old+private+query')).map(([, init]) => init?.signal)
    const beforeRotation = fetch.mock.calls.length
    changedSession = true
    rendered.rerender(<KnowledgeBrowser enabled csrfToken={NEXT_CSRF} onOpenRun={open} onSessionExpired={expired} />)
    expect(await screen.findByRole('heading', { name: 'New browser session library' })).toBeVisible()
    expect(screen.getByRole('searchbox')).toHaveValue('')
    expect(oldSignals.every((signal) => signal?.aborted)).toBe(true)
    await act(async () => { search.resolve(Response.json(delayed.search)); graph.resolve(Response.json(delayed.graph)) })
    expect(screen.queryByRole('heading', { name: 'Delayed old-query private finding' })).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'New browser session library' })).toBeVisible()
    const newLibraryReads = fetch.mock.calls.slice(beforeRotation).filter(([path]) => !path.toString().includes('/watches'))
    expect(newLibraryReads.length).toBeGreaterThanOrEqual(2)
    expect(newLibraryReads.every(([path]) => new URL(path.toString(), 'https://example.org').searchParams.get('q') === '')).toBe(true)
    expect(expired).not.toHaveBeenCalled()
    expect(fetch.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true)
  })
})
