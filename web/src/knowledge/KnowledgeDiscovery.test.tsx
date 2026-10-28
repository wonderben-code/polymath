import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { KnowledgeBrowser } from './KnowledgeBrowser'
import { loadKnowledge } from './client'
import { RESULTS, RUN, STAGES } from '../discovery/test-support'
function records(review = false) {
  const nodes = RESULTS.map((id, ordinal) => ({ id: `finding:${id}`, kind: 'finding', task_kind: 'research', label: `Synthetic saved stage ${ordinal + 1}`, run_id: RUN, result_id: id, source_id: null, content_kind: null, finding_outcome: 'bridge', assessment: { root_result_id: id, current_result_id: id, state: 'supported', revision: 0, last_assessment_id: null, is_current: true, is_supported: !(review && ordinal === 0), is_historical: false }, discovery_stage: { stage_id: STAGES[ordinal], ordinal, depth: ordinal === 2 ? 2 : 1, status: review && ordinal === 0 ? 'needs_review' : 'current_supported' } }))
  return { search: { items: nodes.map((node) => ({ ...node, type: 'finding', title: node.label, summary: 'Synthetic immutable stage finding.' })), truncated: false, coverage: { scope: 'current_browser_session' } }, graph: { nodes, edges: [{ id: 'support', source: nodes[2].id, target: nodes[0].id, kind: 'discovered_from' }, { id: 'revision', source: nodes[1].id, target: nodes[0].id, kind: 'revised_from' }], truncated: false, coverage: { scope: 'current_browser_session', findings_considered: 3 } } }
}
function server(data = records()) { const fetch = vi.fn(async (path: RequestInfo | URL) => Response.json(path.toString().includes('/graph?') ? data.graph : data.search)); vi.stubGlobal('fetch', fetch); return fetch }
afterEach(() => vi.unstubAllGlobals())
describe('owned discovery stage map provenance', () => {
  it('preserves actual stage metadata and distinguishes parent premises from revision edges', async () => {
    server(); const data = await loadKnowledge('', new AbortController().signal)
    expect(data.nodes[2].discovery_stage).toEqual({ stage_id: STAGES[2], ordinal: 2, depth: 2, status: 'current_supported' }); expect(data.edges.map((edge) => edge.kind)).toEqual(['discovered_from', 'revised_from'])
    const onFinding = vi.fn(), onRun = vi.fn(); render(<KnowledgeBrowser enabled onOpenFinding={onFinding} onOpenRun={onRun} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Select map node: Finding Synthetic saved stage 3' })); const selected = screen.getByRole('region', { name: 'Selected knowledge record' }); expect(selected).toHaveTextContent('Saved finding stage 3 · local depth 2'); expect(selected).toHaveTextContent('Selected finding premise'); await userEvent.click(within(selected).getByRole('button', { name: 'Open exact saved stage' })); expect(onFinding).toHaveBeenCalledExactlyOnceWith(RESULTS[2]); expect(onRun).not.toHaveBeenCalled()
  })
  it('permits a narrowly proven needs-review stage for inspection while excluding it from new parent selection', async () => {
    server(records(true)); const data = await loadKnowledge('', new AbortController().signal); expect(data.nodes[0].assessment?.is_supported).toBe(false); expect(data.nodes[0].discovery_stage?.status).toBe('needs_review')
    render(<KnowledgeBrowser enabled canInvestigate onOpenFinding={vi.fn()} onOpenRun={vi.fn()} onInvestigateBridges={vi.fn()} />)
    expect(await screen.findByRole('checkbox', { name: 'Use bridge: Synthetic saved stage 2' })).toBeEnabled(); expect(screen.queryByRole('checkbox', { name: 'Use bridge: Synthetic saved stage 1' })).toBeNull(); await userEvent.click(screen.getByRole('button', { name: 'Select map node: Finding Synthetic saved stage 1' })); expect(screen.getByRole('region', { name: 'Selected knowledge record' })).toHaveTextContent('current evidence or ancestry needs review')
  })
  it('keeps the ordinary support invariant when discovery proof is absent or contradictory', async () => {
    for (const change of ['missing', 'contradictory'] as const) { const data = records(true); if (change === 'missing') delete (data.graph.nodes[0] as Partial<typeof data.graph.nodes[number]>).discovery_stage; else data.graph.nodes[0].discovery_stage.status = 'current_supported'; server(data); await expect(loadKnowledge('', new AbortController().signal)).rejects.toMatchObject({ code: 'invalid_response' }) }
  })
  it('rejects source/field ancestry edges and forged stage limits or task kinds', async () => {
    for (const change of ['self', 'field', 'depth', 'task'] as const) { const data = records(); if (change === 'self') data.graph.edges[0].target = data.graph.edges[0].source; else if (change === 'field') data.graph.nodes[0].kind = 'field'; else if (change === 'depth') data.graph.nodes[0].discovery_stage.depth = 4; else data.graph.nodes[0].task_kind = 'reassess_finding'; server(data); await expect(loadKnowledge('', new AbortController().signal)).rejects.toMatchObject({ code: 'invalid_response' }) }
  })
})
