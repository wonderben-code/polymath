import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import { createResearchRun } from './client'
import { KnowledgeBrowser } from '../knowledge/KnowledgeBrowser'

const PRIOR_RUN = '11111111-1111-4111-8111-111111111111'
const CREATED_RUN = '22222222-2222-4222-8222-222222222222'
const PARENTS = ['33333333-3333-4333-8333-333333333333', '44444444-4444-4444-8444-444444444444']
const GOAL = 'Investigate a shared structure across these saved bridges, test it against the original literature, and report limitations.'

class FixtureEventSource extends EventTarget { close = vi.fn() }

function run(id: string, goal: string, status = 'queued') {
  return { id, goal, mode: 'explore', status, created_at: '2026-10-27T09:00:00Z', updated_at: '2026-10-27T09:01:00Z', result_id: null, error_code: null, external_outcome_unknown: false }
}

function fixtureServer(available = true) {
  let created = run(CREATED_RUN, GOAL)
  const requests = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const path = input.toString()
    if (path === '/api/v1/health') return Response.json({ status: 'ok', service: 'polymath', version: '0.1.0' })
    if (path === '/api/v1/readiness') return Response.json({ status: 'ready', checks: { database: 'ok', migrations: 'ok' } })
    if (path === '/api/v1/capabilities') return Response.json({ research_available: available, meta_configured: false, reason: 'synthetic_fixture' })
    if (path === '/api/v1/session') return Response.json({ csrf_token: 'a'.repeat(64), expires_at: '2100-01-01T00:00:00Z' })
    if (path === '/api/v1/runs' && init?.method === 'POST') {
      const body = JSON.parse(init.body as string) as { goal: string }
      created = run(CREATED_RUN, body.goal)
      return Response.json(created, { status: 201 })
    }
    if (path === '/api/v1/runs') return Response.json({ runs: [], next_cursor: null })
    if (path === `/api/v1/runs/${CREATED_RUN}`) return Response.json(created)
    if (path === `/api/v1/runs/${PRIOR_RUN}`) return Response.json(run(PRIOR_RUN, 'Older synthetic investigation', 'succeeded'))
    if (path.startsWith('/api/v1/knowledge/graph')) return Response.json({
      nodes: PARENTS.map((resultId, index) => ({ id: `finding:${resultId}`, kind: 'finding', label: `Synthetic bridge ${index + 1}`, run_id: PRIOR_RUN, result_id: resultId, source_id: null, content_kind: null, finding_outcome: 'bridge', assessment: { root_result_id: resultId, current_result_id: resultId, state: 'supported', revision: 0, last_assessment_id: null, is_current: true, is_supported: true, is_historical: false } })),
      edges: [], truncated: false, coverage: { scope: 'current_browser_session', findings_considered: 2 },
    })
    if (path.startsWith('/api/v1/knowledge?')) return Response.json({ items: [], truncated: false, coverage: { scope: 'current_browser_session' } })
    throw new Error('Unexpected synthetic fixture request')
  })
  vi.stubGlobal('fetch', requests)
  vi.stubGlobal('EventSource', FixtureEventSource)
  return requests
}

afterEach(() => vi.unstubAllGlobals())

describe('higher-level research workspace submission', () => {
  it('arrives from a finding with that connection already chosen for going deeper', async () => {
    fixtureServer()
    const shown = vi.fn()
    render(<KnowledgeBrowser enabled canInvestigate onInvestigateBridges={vi.fn()} onOpenRun={vi.fn()} deeperFrom={PARENTS[0]} onDeeperShown={shown} />)
    await waitFor(() => expect(screen.getByRole('checkbox', { name: 'Use connection: Synthetic bridge 1' })).toBeChecked())
    expect(screen.getByRole('checkbox', { name: 'Use connection: Synthetic bridge 2' })).not.toBeChecked()
    expect(screen.getByRole('group', { name: 'Go deeper' })).toHaveTextContent('as many levels deep as you like')
    expect(shown).toHaveBeenCalled()
  })

  it('sends selected immutable bridge IDs through the existing owned job path', async () => {
    const requests = fixtureServer()
    render(<App />)
    await userEvent.click(screen.getByRole('link', { name: 'Your library' }))
    await userEvent.click(await screen.findByRole('checkbox', { name: 'Use connection: Synthetic bridge 1' }))
    await userEvent.click(screen.getByRole('checkbox', { name: 'Use connection: Synthetic bridge 2' }))
    await userEvent.dblClick(screen.getByRole('button', { name: 'Find the deeper connection' }))
    await screen.findByRole('heading', { name: GOAL })
    const posts = requests.mock.calls.filter(([, init]) => init?.method === 'POST')
    expect(posts).toHaveLength(1)
    expect(posts[0][0]).toBe('/api/v1/runs')
    expect(posts[0][1]).toMatchObject({ credentials: 'same-origin', cache: 'no-store', redirect: 'error', headers: { 'X-CSRF-Token': 'a'.repeat(64) } })
    expect(JSON.parse(posts[0][1]?.body as string)).toEqual({ mode: 'explore', goal: GOAL, parent_result_ids: PARENTS })
    expect(screen.getByRole('heading', { name: 'No completed outcome yet' })).toBeVisible()
  })

  it('keeps higher-level launch disabled under the default unavailable capability', async () => {
    const requests = fixtureServer(false)
    render(<App />)
    await userEvent.click(screen.getByRole('link', { name: 'Your library' }))
    expect(await screen.findByRole('checkbox', { name: 'Use connection: Synthetic bridge 1' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Find the deeper connection' })).toBeDisabled()
    expect(requests.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
  })

  it('opens an owned map record beyond the current history page using server authorization', async () => {
    const requests = fixtureServer(false)
    render(<App />)
    await userEvent.click(screen.getByRole('link', { name: 'Your library' }))
    const list = await screen.findByRole('list', { name: 'Saved knowledge records' })
    await userEvent.click(within(list).getByRole('button', { name: 'Finding Synthetic bridge 1' }))
    await userEvent.click(screen.getByRole('button', { name: 'Open research record' }))
    expect(await screen.findByRole('heading', { name: 'Older synthetic investigation' })).toBeVisible()
    await waitFor(() => expect(requests.mock.calls.some(([path]) => path.toString() === `/api/v1/runs/${PRIOR_RUN}`)).toBe(true))
  })

  it('rejects invalid parent requests locally before any request can be sent', async () => {
    const requests = fixtureServer()
    for (const ids of [[PARENTS[0]], [PARENTS[0], PARENTS[0]], [PARENTS[0], 'not-an-id'], [...PARENTS, ...PARENTS, PARENTS[0]]]) {
      await expect(createResearchRun('explore', GOAL, 'a'.repeat(64), undefined, ids)).rejects.toThrow('invalid_request')
    }
    await expect(createResearchRun('ask', GOAL, 'a'.repeat(64), undefined, PARENTS)).rejects.toThrow('invalid_request')
    expect(requests).not.toHaveBeenCalled()
  })
})
