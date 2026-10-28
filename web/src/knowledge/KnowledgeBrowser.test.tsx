import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { KnowledgeBrowser } from './KnowledgeBrowser'
import { loadKnowledge } from './client'
import { capabilities as watchCapabilities, detail as watchDetail, source as watchSource, watch as savedWatch, WATCH, SOURCE } from '../ingestion/fixtures.test-support'

const RUN = '11111111-1111-4111-8111-111111111111'
const RESULT = '22222222-2222-4222-8222-222222222222'
const coverage = { scope: 'current_browser_session', findings_considered: 1 }

function records(label = 'Synthetic fixture bridge') {
  return {
    search: {
      items: [{ id: 'finding:one', type: 'finding', title: label, run_id: RUN, result_id: RESULT, source_id: null, content_kind: null, summary: 'Synthetic provenance fixture, not a discovery.' }],
      truncated: false,
      coverage: { ...coverage },
    },
    graph: {
      nodes: [
        { id: 'field:a', kind: 'field', label: 'Fixture field A', run_id: null, result_id: null, source_id: null, content_kind: null },
        { id: 'finding:one', kind: 'finding', label, run_id: RUN, result_id: RESULT, source_id: null, content_kind: null },
        { id: 'source:one', kind: 'source', label: 'Synthetic source', run_id: RUN, result_id: null, source_id: 'fixture:source', content_kind: 'abstract' },
      ],
      edges: [
        { id: 'edge:a', source: 'finding:one', target: 'field:a', kind: 'maps_to' },
        { id: 'edge:b', source: 'finding:one', target: 'source:one', kind: 'supported_by' },
      ],
      truncated: false,
      coverage: { ...coverage },
    },
  }
}

function response(value: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => value } as Response
}

function mockRecords(data = records()) {
  const mock = vi.fn(async (path: string | URL | Request, _init?: RequestInit) => response(String(path).startsWith('/api/v1/knowledge/graph') ? data.graph : data.search))
  vi.stubGlobal('fetch', mock)
  return mock
}

afterEach(() => vi.unstubAllGlobals())

describe('session-owned knowledge browser', () => {
  it('routes collected map and search sources to owned unassessed text, never to a research run', async () => {
    const source = { id: `source:${SOURCE}`, type: 'source', title: watchSource().title, run_id: RUN, result_id: null, source_id: watchSource().source_id, content_kind: 'abstract', summary: 'Collected abstract; unassessed source version', task_kind: 'ingest_literature', watch_id: WATCH, source_version_id: SOURCE }
    const data = { items: [source], nodes: [{ ...source, kind: 'source', label: source.title }], edges: [], truncated: false, coverage: { scope: 'current_browser_session', findings_considered: 0 } }
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path.endsWith('/capabilities')) return Response.json(watchCapabilities(false))
      if (path.includes('/watches?')) return Response.json({ watches: [savedWatch({ source_count: 1 })], next_cursor: null })
      if (path.endsWith(`/sources/${SOURCE}`)) return Response.json(watchDetail())
      if (path.includes('/sources?')) return Response.json({ sources: [watchSource()], next_cursor: null })
      if (path.includes('/ticks?')) return Response.json({ ticks: [], next_cursor: null })
      if (path === `/api/v1/knowledge/watches/${WATCH}`) return Response.json(savedWatch({ source_count: 1 }))
      return Response.json(data)
    })
    vi.stubGlobal('fetch', fetch)
    const open = vi.fn()
    render(<KnowledgeBrowser enabled onOpenRun={open} csrfToken="csrf" />)
    await userEvent.click(await screen.findByRole('button', { name: `Read unassessed source: ${source.title}` }))
    expect(await screen.findByRole('region', { name: 'Saved source text' })).toHaveTextContent('Synthetic original abstract')
    expect(open).not.toHaveBeenCalled()
    expect(fetch.mock.calls.every(([path]) => !String(path).startsWith('/api/v1/runs'))).toBe(true)
    await userEvent.click(screen.getByRole('button', { name: 'Close saved source' }))
    await userEvent.click(screen.getByRole('button', { name: `Select map node: Source ${source.title}` }))
    const node = screen.getByRole('region', { name: 'Selected knowledge record' })
    expect(node).toHaveTextContent('An unassessed source version collected by a literature watch')
    await userEvent.click(within(node).getByRole('button', { name: 'Read unassessed source' }))
    expect(await screen.findByRole('region', { name: 'Saved source text' })).toBeInTheDocument()
    expect(open).not.toHaveBeenCalled()
  })

  it('never fetches or creates a session while disabled', () => {
    const fetch = mockRecords()
    render(<KnowledgeBrowser enabled={false} onOpenRun={vi.fn()} />)
    expect(fetch).not.toHaveBeenCalled()
    expect(screen.getByRole('searchbox')).toBeDisabled()
    expect(screen.getByText(/Connect to the research workspace/)).toBeVisible()
  })

  it('loads only cookie-owned API data and opens the selected record', async () => {
    const fetch = mockRecords()
    const open = vi.fn()
    render(<KnowledgeBrowser enabled onOpenRun={open} />)
    await screen.findByRole('heading', { name: 'Matching records' })
    expect(fetch).toHaveBeenCalledTimes(2)
    expect(fetch.mock.calls.map(([path]) => path)).toEqual(['/api/v1/knowledge?q=&limit=20', '/api/v1/knowledge/graph?q='])
    expect(fetch.mock.calls.every((call) => !String(call[0]).includes('/session'))).toBe(true)
    expect(fetch.mock.calls.every((call) => call[1]?.credentials === 'same-origin')).toBe(true)
    await userEvent.click(screen.getByRole('button', { name: /Open investigation: Synthetic fixture bridge/ }))
    expect(open).toHaveBeenCalledWith(RUN)
    expect(screen.getByText(/Current browser session only/)).toBeVisible()
    expect(screen.getByText(/do not establish scientific truth/)).toBeVisible()
  })

  it('supports the map by keyboard and equivalent record buttons', async () => {
    mockRecords()
    const open = vi.fn()
    render(<KnowledgeBrowser enabled onOpenRun={open} />)
    const node = await screen.findByRole('button', { name: 'Select map node: Source Synthetic source' })
    fireEvent.keyDown(node, { key: 'Enter' })
    const detail = screen.getByRole('region', { name: 'Selected knowledge record' })
    expect(within(detail).getByText('Abstract snapshot')).toBeVisible()
    expect(within(detail).getByRole('list', { name: 'Connected records' })).toHaveTextContent('Synthetic fixture bridge')
    await userEvent.click(within(detail).getByRole('button', { name: 'Open research record' }))
    expect(open).toHaveBeenCalledWith(RUN)
    const list = screen.getByRole('list', { name: 'Saved knowledge records' })
    await userEvent.click(within(list).getByRole('button', { name: 'Field Fixture field A' }))
    expect(screen.getByRole('region', { name: 'Selected knowledge record' })).toHaveTextContent('A field named in saved research')
    expect(screen.queryByRole('button', { name: 'Open research record' })).not.toBeInTheDocument()
  })

  it('encodes literal query characters and clearly reports bounded coverage', async () => {
    const data = records()
    data.graph.truncated = true
    const fetch = mockRecords(data)
    render(<KnowledgeBrowser enabled onOpenRun={vi.fn()} />)
    await screen.findByText(/bounded view of your library/)
    await userEvent.type(screen.getByRole('searchbox'), 'A&B %_')
    await userEvent.click(screen.getByRole('button', { name: 'Search library' }))
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(4))
    const path = fetch.mock.calls[2][0] as string
    expect(new URL(path, 'https://example.org').searchParams.get('q')).toBe('A&B %_')
  })

  it('clears all private records and selection when the session expires', async () => {
    const fetch = mockRecords()
    render(<KnowledgeBrowser enabled onOpenRun={vi.fn()} />)
    const node = await screen.findByRole('button', { name: 'Select map node: Source Synthetic source' })
    await userEvent.click(node)
    await userEvent.type(screen.getByRole('searchbox'), 'Private fixture query')
    fetch.mockImplementation(async () => response({}, 401))
    await userEvent.click(screen.getByRole('button', { name: 'Search library' }))
    await screen.findByText(/session has expired or changed/)
    expect(screen.queryByRole('list', { name: 'Saved knowledge records' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Selected knowledge record' })).not.toBeInTheDocument()
    expect(screen.getByRole('searchbox')).toHaveValue('')
    expect(screen.getByRole('searchbox')).toBeDisabled()
    expect(fetch).toHaveBeenCalledTimes(4)
  })

  it('rejects dangling graph edges instead of inventing a relationship', async () => {
    const data = records()
    data.graph.edges[0].target = 'private:unreturned'
    mockRecords(data)
    render(<KnowledgeBrowser enabled onOpenRun={vi.fn()} />)
    await screen.findByText(/Saved knowledge could not be loaded/)
    expect(screen.queryByRole('list', { name: 'Matching saved research' })).not.toBeInTheDocument()
  })

  it('escapes stored markup and provides no external source URL navigation', async () => {
    const label = '<img src=x onerror=alert(1)>'
    mockRecords(records(label))
    const { container } = render(<KnowledgeBrowser enabled onOpenRun={vi.fn()} />)
    await screen.findByRole('heading', { name: label })
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('a')).toBeNull()
  })

  it('accepts bounded long saved questions without inserting raw markup', async () => {
    const label = 'A'.repeat(4000)
    mockRecords(records(label))
    render(<KnowledgeBrowser enabled onOpenRun={vi.fn()} />)
    expect(await screen.findByRole('heading', { name: label })).toBeVisible()
    expect(screen.getByRole('button', { name: `Select map node: Finding ${label}` })).toBeInTheDocument()
  })

  it('shows honest empty and connection-failure states with retry', async () => {
    const empty = records()
    empty.search.items = []
    empty.graph.nodes = []
    empty.graph.edges = []
    empty.graph.coverage.findings_considered = 0
    const fetch = mockRecords(empty)
    render(<KnowledgeBrowser enabled onOpenRun={vi.fn()} />)
    await screen.findByText(/No saved knowledge matches/)
    fetch.mockImplementation(async () => response({}, 503))
    await userEvent.click(screen.getByRole('button', { name: 'Search library' }))
    await screen.findByRole('button', { name: 'Retry library connection' })
    fetch.mockImplementation(async () => response({}, 503))
    await userEvent.click(screen.getByRole('button', { name: 'Retry library connection' }))
    await waitFor(() => expect(fetch.mock.calls.length).toBe(6))
  })

  it('clears data without a request when disabled after loading', async () => {
    const fetch = mockRecords()
    const { rerender } = render(<KnowledgeBrowser enabled onOpenRun={vi.fn()} />)
    await screen.findByRole('list', { name: 'Saved knowledge records' })
    await userEvent.type(screen.getByRole('searchbox'), 'Private fixture draft')
    rerender(<KnowledgeBrowser enabled={false} onOpenRun={vi.fn()} />)
    expect(screen.queryByRole('list', { name: 'Saved knowledge records' })).not.toBeInTheDocument()
    expect(fetch).toHaveBeenCalledTimes(2)
    expect(screen.getByRole('searchbox')).toHaveValue('')
  })

  it('bounds stalled requests and rejects malformed coverage flags', async () => {
    const data = records()
    data.search.truncated = true
    const malformed = { ...data.graph, truncated: 'not-a-boolean' }
    vi.stubGlobal('fetch', vi.fn(async (path: string) => response(path.startsWith('/api/v1/knowledge/graph') ? malformed : data.search)))
    await expect(loadKnowledge('', new AbortController().signal)).rejects.toThrow('invalid_response')

    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})))
    render(<KnowledgeBrowser enabled onOpenRun={vi.fn()} />)
    await act(async () => { await vi.advanceTimersByTimeAsync(10_001) })
    expect(screen.getByText(/Saved knowledge could not be loaded/)).toBeVisible()
    vi.useRealTimers()
  })
})
