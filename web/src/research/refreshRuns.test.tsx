import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { publicAccess } from './accessFixtures.test-support'
import { useResearchWorkspace } from './useResearchWorkspace'
import { DATE } from '../budget/test-support'

const RUN = '11111111-1111-4111-8111-111111111111'
class FixtureEventSource extends EventTarget { close = vi.fn(); constructor(readonly url: string) { super() } }
function run(status: string) { return { id: RUN, mode: 'explore', goal: 'Synthetic visitor investigation', task_kind: 'research', status, created_at: DATE, updated_at: status === 'interrupted' ? DATE : '2026-11-01T21:14:47Z', result_id: null, error_code: null, external_outcome_unknown: false } }

beforeEach(() => vi.spyOn(Date, 'now').mockReturnValue(Date.parse(DATE)))
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('re-reading runs the page still counts as active', () => {
  it('drops an interrupted run from the visitor’s active research once the saved state says it ended', async () => {
    let status = 'interrupted'
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      const path = input.toString()
      if (path === '/api/v1/health') return Response.json({ status: 'ok', service: 'polymath', version: '0.1.0' })
      if (path === '/api/v1/readiness') return Response.json({ status: 'ready', checks: { database: 'ok', migrations: 'ok' } })
      if (path === '/api/v1/capabilities') return Response.json({ research_available: true, meta_configured: true, reason: null })
      if (path === '/api/v1/session') return Response.json({ csrf_token: 'a'.repeat(64), expires_at: '2100-01-01T00:00:00Z' })
      if (path === '/api/v1/runs?own=true') return Response.json({ runs: [run('interrupted')], next_cursor: null })
      if (path === '/api/v1/research/access') return Response.json(publicAccess())
      if (path.endsWith('/protocols')) return Response.json({ protocol: 'polymath-research-protocols-v1', activation_sha256: publicAccess().expected_activation_sha256, capabilities: [] })
      if (path.endsWith('/explore-protocols')) return Response.json({ protocol: 'polymath-explore-collection-access-v1', activation_sha256: publicAccess().expected_activation_sha256, explore_collection_intent: null })
      if (path === `/api/v1/runs/${RUN}`) return Response.json(run(status))
      throw new Error(`Unexpected synthetic request: ${path}`)
    })
    vi.stubGlobal('fetch', fetch); vi.stubGlobal('EventSource', FixtureEventSource)
    const { result } = renderHook(() => useResearchWorkspace(true, true, true))
    await waitFor(() => expect(result.current.phase).toBe('ready'))
    await waitFor(() => expect(result.current.ownActiveResearch).toBe(true))
    expect(result.current.canSubmit).toBe(false)
    status = 'cancelled'
    await act(async () => { await result.current.refreshRuns() })
    expect(result.current.ownActiveResearch).toBe(false)
    expect(result.current.runs[0]?.status).toBe('cancelled')
    expect(fetch.mock.calls.every(([, options]) => !options || (options as RequestInit).method === 'GET' || (options as RequestInit).method === undefined)).toBe(true)
  })
})
