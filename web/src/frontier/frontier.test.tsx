import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { parseProgramme } from './client'
import type { FrontierNode } from './client'
import { FrontierMap, FrontierOutline, layout } from './FrontierMap'
import { FrontierWorkspace } from './FrontierWorkspace'
import { frontierReport } from './report'

const PID = '11111111-1111-4111-8111-111111111111'
const ROOT = '22222222-2222-4222-8222-222222222222'
const GAP = '33333333-3333-4333-8333-333333333333'
const RUN = '44444444-4444-4444-8444-444444444444'
const CSRF = 'a'.repeat(64)

function node(overrides: Partial<FrontierNode> = {}): FrontierNode {
  return { id: ROOT, parent_id: null, kind: 'question', statement: 'An unsolved synthetic problem', depth: 0, ordinal: 0, status: 'surveyed', resolved_status: 'surveyed', pending_action: null, premise_result_ids: [], attempts: [], ...overrides }
}

function programme(nodes: FrontierNode[] = [node(), node({ id: GAP, parent_id: ROOT, kind: 'gap', statement: 'A named synthetic gap', depth: 1, status: 'pending', resolved_status: 'pending', pending_action: 'transfer' })], status = 'active') {
  return { id: PID, question: 'An unsolved synthetic problem', status, created_at: '2026-10-29T00:00:00Z', updated_at: '2026-10-29T00:00:00Z', node_counts: { surveyed: 1, pending: 1 }, success_criteria: null, limits: { max_attempts: 24, max_depth: 4, max_branches: 6 }, attempts_used: 1, nodes }
}

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

describe('frontier client and map', () => {
  it('parses a programme strictly and rejects unknown statuses or duplicate nodes', () => {
    expect(parseProgramme(programme()).nodes).toHaveLength(2)
    expect(() => parseProgramme({ ...programme(), nodes: [{ ...node(), status: 'proven' }] })).toThrow('invalid_response')
    expect(() => parseProgramme({ ...programme(), nodes: [node(), node()] })).toThrow('invalid_response')
  })

  it('lays the tree out left to right with parents beside their children', () => {
    const { placed } = layout(programme().nodes)
    const root = placed.find((item) => item.node.id === ROOT)!, gap = placed.find((item) => item.node.id === GAP)!
    expect(gap.x).toBeGreaterThan(root.x)
    expect(gap.y).toBe(root.y)
  })

  it('exposes every node as a labelled, keyboard-selectable control', async () => {
    const onSelect = vi.fn()
    render(<FrontierMap nodes={programme().nodes} selectedId={null} onSelect={onSelect} />)
    const gap = screen.getByRole('button', { name: /Gap: A named synthetic gap\. Waiting\./ })
    gap.focus()
    await userEvent.keyboard('{Enter}')
    expect(onSelect).toHaveBeenCalledWith(GAP)
  })
})

describe('frontier workspace', () => {
  it('queues the due step through the ordinary submit path and attaches its run', async () => {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push(`${init?.method ?? 'GET'} ${url}`)
      if (url === '/api/v1/frontier') return Response.json({ programmes: [programme()] })
      if (url.endsWith('/next')) return Response.json({ next: { state: 'due', node_id: GAP, action: 'transfer', mode: 'explore', goal: 'A named synthetic gap (an open gap within the problem)', parent_result_ids: [] }, programme: programme() })
      if (url.endsWith('/attach')) { expect(JSON.parse(String(init?.body))).toEqual({ node_id: GAP, action: 'transfer', run_id: RUN }); return Response.json(programme([node(), node({ id: GAP, parent_id: ROOT, kind: 'gap', statement: 'A named synthetic gap', depth: 1, status: 'running', resolved_status: 'running', pending_action: 'transfer' })])) }
      return Response.json(programme())
    }))
    const submit = vi.fn(async () => ({ id: RUN, mode: 'explore', goal: 'g', status: 'queued', created_at: '', updated_at: '', result_id: null, error_code: null, external_outcome_unknown: false } as never))
    render(<FrontierWorkspace enabled csrfToken={CSRF} canSubmit focusId={PID} submit={submit} onOpenRun={vi.fn()} onSessionExpired={vi.fn()} />)
    await waitFor(() => expect(submit).toHaveBeenCalledWith('explore', 'A named synthetic gap (an open gap within the problem)', []))
    await waitFor(() => expect(calls).toContain(`POST /api/v1/frontier/${PID}/attach`))
    expect(screen.getByText('Experimental')).toBeInTheDocument()
  })

  it('waits instead of queuing while live research is unavailable', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/v1/frontier') return Response.json({ programmes: [programme()] })
      if (url.endsWith('/next')) return Response.json({ next: { state: 'due', node_id: GAP, action: 'transfer', mode: 'explore', goal: 'goal', parent_result_ids: [] }, programme: programme() })
      return Response.json(programme())
    }))
    const submit = vi.fn()
    render(<FrontierWorkspace enabled csrfToken={CSRF} canSubmit={false} focusId={PID} submit={submit} onOpenRun={vi.fn()} onSessionExpired={vi.fn()} />)
    expect(await screen.findByText(/Waiting until live research is available for this browser./)).toBeInTheDocument()
    await act(async () => {})
    expect(submit).not.toHaveBeenCalled()
  })
})

describe('frontier report', () => {
  it('builds an escaped Markdown report naming the weakest branch and the open frontier', () => {
    const report = frontierReport(parseProgramme(programme([
      node({ statement: '# Injected *heading* <b>' }),
      node({ id: GAP, parent_id: ROOT, kind: 'gap', statement: 'An open synthetic gap', depth: 1, status: 'open', resolved_status: 'open' }),
    ])), new Date('2026-10-29T00:00:00Z'))
    expect(report).toContain('**Question** (Surveyed): \\# Injected \\*heading\\* \\<b\\>')
    expect(report).toContain('weakest top-level branch is **open frontier**')
    expect(report).toContain('## Open frontier\n\n- An open synthetic gap')
    expect(report).not.toContain('<b>')
  })
})

describe('frontier outline', () => {
  it('nests children under their parent and selects a node from the list', async () => {
    const onSelect = vi.fn()
    render(<FrontierOutline nodes={programme().nodes} selectedId={ROOT} onSelect={onSelect} />)
    const outline = screen.getByRole('navigation', { name: 'Solve path outline' })
    const question = screen.getByRole('button', { name: /Question · Surveyed/ })
    expect(question).toHaveAttribute('aria-pressed', 'true')
    expect(question.closest('li')?.querySelector('ol')).toHaveTextContent('A named synthetic gap')
    await userEvent.click(screen.getByRole('button', { name: /Gap · Waiting/ }))
    expect(onSelect).toHaveBeenCalledWith(GAP)
    expect(outline).toBeInTheDocument()
  })
})

