import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { KnowledgeBrowser } from './KnowledgeBrowser'
import { loadKnowledge } from './client'
import { excerpt } from '../research/coverage.test-support'

const RUN = '11111111-1111-4111-8111-111111111111'
function records() {
  const sources = [excerpt(), excerpt('Another explicitly synthetic window from the same paper.', 500)]
  const items = sources.map((source, index) => ({ id: `source:synthetic-${index}`, type: 'source', title: source.title, run_id: RUN, result_id: null, source_id: source.source_id, content_kind: source.content_kind, summary: 'Saved FULL TEXT EXCERPT; exact bounded snapshot window', coverage: source.coverage, bibliography: source.bibliography, url: source.url, read_scope: 'full_text_excerpt' }))
  return { search: { items, truncated: false, coverage: { scope: 'current_browser_session' } }, graph: { nodes: items.map((item) => ({ ...item, kind: 'source', label: item.title })), edges: [], truncated: false, coverage: { scope: 'current_browser_session', findings_considered: 0 } } }
}
function server(data = records()) {
  const fetch = vi.fn(async (path: RequestInfo | URL) => Response.json(String(path).includes('/graph?') ? data.graph : data.search))
  vi.stubGlobal('fetch', fetch)
  return fetch
}
afterEach(() => vi.unstubAllGlobals())
describe('coverage in saved knowledge', () => {
  it('preserves paired coverage and paper-version metadata from the owned projection', async () => {
    server()
    const data = await loadKnowledge('', new AbortController().signal)
    expect(data.items[0].coverage?.start).toBe(100)
    expect(data.nodes[1].coverage?.start).toBe(500)
    expect(data.nodes[1].url).toBe('https://arxiv.org/html/2610.00001v2')
    expect(data.items[0].read_scope).toBe('full_text_excerpt')
  })
  it('shows windows as source versions of one paper and exposes exact reading scope on selection', async () => {
    const data = records(); data.graph.nodes[1].label = 'Second saved window'
    const fetch = server(data)
    render(<KnowledgeBrowser enabled onOpenRun={vi.fn()} />)
    expect(await screen.findByText(/2 saved source versions · 1 paper identity/)).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Select map node: Source Second saved window' }))
    expect(screen.getByRole('region', { name: 'Selected knowledge record' })).toHaveTextContent('Full text excerpt')
    expect(screen.getByRole('region', { name: 'Saved full-text excerpt coverage' })).toHaveTextContent('saved characters 500–')
    expect(screen.getByRole('link', { name: /original versioned paper/ })).toHaveAttribute('href', 'https://arxiv.org/html/2610.00001v2')
    expect(fetch).toHaveBeenCalledTimes(2)
    expect(fetch.mock.calls.every(([path]) => String(path).startsWith('/api/v1/knowledge'))).toBe(true)
  })
  it('rejects coverage without matching canonical URL, paper aliases or excerpt scope', async () => {
    const data = records(); server(data)
    data.graph.nodes[0].url = 'https://arxiv.org/html/2610.00001v3'
    await expect(loadKnowledge('', new AbortController().signal)).rejects.toThrow('invalid_response')
    data.graph.nodes[0].url = 'https://arxiv.org/html/2610.00001v2'; data.search.items[0].read_scope = 'whole_paper'
    await expect(loadKnowledge('', new AbortController().signal)).rejects.toThrow('invalid_response')
    data.search.items[0].read_scope = 'full_text_excerpt'; data.search.items[0].bibliography = undefined
    await expect(loadKnowledge('', new AbortController().signal)).rejects.toThrow('invalid_response')
  })
})
