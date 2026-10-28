import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { KnowledgeBrowser } from './KnowledgeBrowser'
import { loadKnowledge } from './client'

// Synthetic saved artifacts exercise provenance/navigation, not scientific claims.
const RUN = '11111111-1111-4111-8111-111111111111'
const resultId = (index: number) => `${String(index).padStart(8, '0')}-2222-4222-8222-222222222222`

function records(count = 2) {
  const nodes = Array.from({ length: count }, (_, index) => ({
    id: `finding:${index + 1}`, kind: 'finding', label: `Synthetic bridge ${index + 1}`,
    run_id: RUN, result_id: resultId(index + 1), source_id: null, content_kind: null, finding_outcome: 'bridge', assessment: { root_result_id: resultId(index + 1), current_result_id: resultId(index + 1), state: 'supported', revision: 0, last_assessment_id: null as string | null, is_current: true, is_supported: true, is_historical: false },
  }))
  return {
    search: { items: [], truncated: false, coverage: { scope: 'current_browser_session' } },
    graph: { nodes, edges: [] as { id: string; source: string; target: string; kind: string }[], truncated: false, coverage: { scope: 'current_browser_session', findings_considered: count } },
  }
}

function server(data = records()) {
  const fetch = vi.fn(async (path: string | URL | Request) => Response.json(String(path).startsWith('/api/v1/knowledge/graph') ? data.graph : data.search))
  vi.stubGlobal('fetch', fetch)
  return fetch
}

afterEach(() => vi.unstubAllGlobals())

describe('investigating connections between saved bridges', () => {
  it('keeps historical or unsupported bridge versions inspectable but excludes them from new investigations', async () => {
    const data = records(3)
    data.graph.nodes[0].assessment = { ...data.graph.nodes[0].assessment, current_result_id: resultId(3), revision: 1, last_assessment_id: resultId(9), is_current: false, is_supported: false, is_historical: true }
    data.graph.nodes[1].assessment = { ...data.graph.nodes[1].assessment, state: 'unsupported', revision: 1, last_assessment_id: resultId(9), is_supported: false, is_historical: true }
    server(data)
    render(<KnowledgeBrowser enabled canInvestigate onOpenRun={vi.fn()} onInvestigateBridges={vi.fn()} />)
    expect(await screen.findByRole('checkbox', { name: 'Use bridge: Synthetic bridge 1' })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: 'Use bridge: Synthetic bridge 2' })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: 'Use bridge: Synthetic bridge 3' })).toBeEnabled()
    await userEvent.click(screen.getByRole('button', { name: 'Select map node: Finding Synthetic bridge 1' }))
    expect(screen.getByRole('region', { name: 'Selected knowledge record' })).toHaveTextContent('Historical finding')
    expect(screen.getByRole('button', { name: 'Open research record' })).toBeEnabled()
  })
  it('requires two to four saved bridges and passes exact selected versions once', async () => {
    server()
    const investigate = vi.fn()
    render(<KnowledgeBrowser enabled canInvestigate onOpenRun={vi.fn()} onInvestigateBridges={investigate} />)
    const first = await screen.findByRole('checkbox', { name: 'Use bridge: Synthetic bridge 1' })
    const action = screen.getByRole('button', { name: 'Investigate these bridges' })
    expect(action).toBeDisabled()
    await userEvent.click(first)
    expect(action).toBeDisabled()
    await userEvent.click(screen.getByRole('checkbox', { name: 'Use bridge: Synthetic bridge 2' }))
    expect(action).toBeEnabled()
    await userEvent.click(action)
    expect(investigate).toHaveBeenCalledExactlyOnceWith([resultId(1), resultId(2)])
    expect(action).toBeDisabled()
    expect(first).not.toBeChecked()
  })

  it('caps the selection and excludes saved answers or untyped older findings', async () => {
    const data = records(5)
    data.graph.nodes.push({ ...data.graph.nodes[0], id: 'finding:answer', result_id: resultId(6), label: 'Synthetic answer', finding_outcome: 'answer', assessment: { ...data.graph.nodes[0].assessment, root_result_id: resultId(6), current_result_id: resultId(6) } })
    const { finding_outcome: _outcome, assessment: _assessment, ...legacy } = data.graph.nodes[0]
    data.graph.nodes.push({ ...legacy, id: 'finding:legacy', result_id: resultId(7), label: 'Legacy saved record' } as typeof data.graph.nodes[number])
    server(data)
    render(<KnowledgeBrowser enabled canInvestigate onOpenRun={vi.fn()} onInvestigateBridges={vi.fn()} />)
    const choices = await screen.findAllByRole('checkbox')
    expect(choices).toHaveLength(5)
    for (const checkbox of choices.slice(0, 4)) await userEvent.click(checkbox)
    expect(choices[4]).toBeDisabled()
    expect(choices[0]).toBeEnabled()
    await userEvent.click(choices[0])
    expect(choices[4]).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Investigate these bridges' })).toBeEnabled()
  })

  it('keeps new research disabled while allowing saved relationship inspection', async () => {
    const data = records()
    data.graph.edges = [{ id: 'lineage:1', source: 'finding:2', target: 'finding:1', kind: 'investigated_from' }]
    server(data)
    render(<KnowledgeBrowser enabled onOpenRun={vi.fn()} onInvestigateBridges={vi.fn()} />)
    expect(await screen.findByRole('checkbox', { name: 'Use bridge: Synthetic bridge 1' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Investigate these bridges' })).toBeDisabled()
    expect(screen.getByText(/New investigations are unavailable/)).toBeVisible()
    fireEvent.keyDown(screen.getByRole('button', { name: 'Select map node: Finding Synthetic bridge 2' }), { key: 'Enter' })
    const detail = screen.getByRole('region', { name: 'Selected knowledge record' })
    expect(within(detail).getByText('Earlier bridge investigated')).toBeVisible()
    await userEvent.click(within(detail).getByRole('button', { name: 'Synthetic bridge 1' }))
    expect(within(screen.getByRole('region', { name: 'Selected knowledge record' })).getByText('Follow-up investigation')).toBeVisible()
    expect(screen.getByText(/they do not provide independent support/)).toBeVisible()
  })

  it('clears selections after capability loss, query changes, and expired sessions', async () => {
    const fetch = server()
    const investigate = vi.fn()
    const { rerender } = render(<KnowledgeBrowser enabled canInvestigate onOpenRun={vi.fn()} onInvestigateBridges={investigate} />)
    await userEvent.click(await screen.findByRole('checkbox', { name: 'Use bridge: Synthetic bridge 1' }))
    rerender(<KnowledgeBrowser enabled canInvestigate={false} onOpenRun={vi.fn()} onInvestigateBridges={investigate} />)
    expect(screen.getByRole('checkbox', { name: 'Use bridge: Synthetic bridge 1' })).not.toBeChecked()
    rerender(<KnowledgeBrowser enabled canInvestigate onOpenRun={vi.fn()} onInvestigateBridges={investigate} />)
    await userEvent.click(screen.getByRole('checkbox', { name: 'Use bridge: Synthetic bridge 1' }))
    await userEvent.type(screen.getByRole('searchbox'), 'Synthetic')
    await userEvent.click(screen.getByRole('button', { name: 'Search library' }))
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(4))
    expect(await screen.findByRole('checkbox', { name: 'Use bridge: Synthetic bridge 1' })).not.toBeChecked()
    await userEvent.click(screen.getByRole('checkbox', { name: 'Use bridge: Synthetic bridge 1' }))
    fetch.mockImplementation(async () => Response.json({}, { status: 401 }))
    await userEvent.click(screen.getByRole('button', { name: 'Search library' }))
    await screen.findByText(/session has expired or changed/)
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Investigate these bridges' })).not.toBeInTheDocument()
    expect(investigate).not.toHaveBeenCalled()
  })

  it('rejects self-referential lineage edges and malformed outcome tags', async () => {
    const data = records()
    const fetch = server(data)
    data.graph.edges = [{ id: 'lineage:1', source: 'finding:1', target: 'finding:1', kind: 'investigated_from' }]
    await expect(loadKnowledge('', new AbortController().signal)).rejects.toThrow('invalid_response')
    data.graph.edges = []
    data.graph.nodes[0].finding_outcome = 'proved'
    await expect(loadKnowledge('', new AbortController().signal)).rejects.toThrow('invalid_response')
    expect(fetch).toHaveBeenCalledTimes(4)
  })
})
