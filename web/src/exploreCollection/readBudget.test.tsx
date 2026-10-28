import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import { BROWSER_CSRF, DATE } from '../budget/test-support'
import { publicAccess } from '../research/accessFixtures.test-support'
import { capabilities as watchCapabilities, watch, source, WATCH } from '../ingestion/fixtures.test-support'
import { capabilities as assessmentCapabilities } from '../assessments/fixtures.test-support'
import { capabilities as sharingCapabilities } from '../sharing/test-support'
import { parseExploreBridgeHistory, parseExploreCollectionSummary } from './parsers'
import { pythonFixtures } from './fixtures'
class Stream extends EventTarget {
  static instances: Stream[] = []; static opened: number[] = []
  close = vi.fn()
  constructor(readonly url: string) { super(); Stream.instances.push(this); Stream.opened.push(Date.now()) }
  emit(kind: string, value?: unknown) { this.dispatchEvent(value === undefined ? new Event(kind) : new MessageEvent(kind, { data: JSON.stringify(value) })) }
}
async function flush() { await act(async () => { await vi.advanceTimersByTimeAsync(0) }) }
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })
describe('actual public App collection read composition', () => {
  it('stays within60 rolling reads with a selected watch and live activity, retains closed funding history, and stops hidden collection reads for the exact reader', async () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date(DATE)); Stream.instances = []; Stream.opened = []
    const summary = parseExploreCollectionSummary(pythonFixtures.summary), saved = parseExploreBridgeHistory(pythonFixtures.bridges), RUN = summary.run_id, root = saved.stages[0].stage.result_id, SHA = publicAccess().expected_activation_sha256!, started = Date.now()
    const reads: { path: string; at: number }[] = [], methods: string[] = [], coverage = { scope: 'current_browser_session', findings_considered: 0 }
    let finished = false, fundingClosed = false
    const run = () => ({ id: RUN, mode: 'explore', task_kind: 'research', goal: 'Synthetic bounded multi-bridge question', status: finished ? 'cancelled' : 'running', created_at: DATE, updated_at: DATE, result_id: null, error_code: null, external_outcome_unknown: false, explore_collection_intent: summary.explore_collection_intent })
    function head(resultId: string) { const stage = saved.stages.find((detail) => detail.stage.result_id === resultId)!.stage; return { requested_result_id: resultId, root_result_id: resultId, current_result_id: resultId, current_run_id: RUN, state: 'supported', revision: 0, last_assessment_id: null, is_current: true, is_supported: true, is_historical: false, can_generate: true, discovery_stage: { stage_id: stage.stage_id, ordinal: stage.ordinal, depth: 1, status: 'current_supported' } } }
    const fetch = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
      const path = String(input), method = options?.method ?? 'GET'; methods.push(method)
      if (path.startsWith('/api/v1/') && !['/api/v1/health', '/api/v1/readiness', '/api/v1/session'].includes(path) && !path.endsWith('/capabilities')) reads.push({ path, at: Date.now() })
      if (path === '/api/v1/health') return Response.json({ status: 'ok', service: 'polymath' })
      if (path === '/api/v1/readiness') return Response.json({ checks: { database: 'ok', migrations: 'ok' } })
      if (path === '/api/v1/capabilities') return Response.json({ research_available: true, native_research_required: true, meta_configured: true })
      if (path === '/api/v1/session') return Response.json({ csrf_token: BROWSER_CSRF, expires_at: new Date(started + 3600000).toISOString() })
      if (path === '/api/v1/runs') return Response.json({ runs: [run()], next_cursor: null })
      if (path === `/api/v1/runs/${RUN}`) return Response.json(run())
      if (path === '/api/v1/research/access') return Response.json(publicAccess({ expires_at: new Date(started + 3600000).toISOString(), available: !fundingClosed, reason: fundingClosed ? 'public_research_stopped' : null }))
      if (path === '/api/v1/research/protocols') return Response.json({ protocol: 'polymath-research-protocols-v1', activation_sha256: SHA, capabilities: [] })
      if (path === '/api/v1/research/explore-protocols') return Response.json({ protocol: 'polymath-explore-collection-access-v1', activation_sha256: SHA, explore_collection_intent: summary.explore_collection_intent })
      if (path.endsWith('/explore-collection')) return Response.json({ ...summary, run_status: finished ? 'cancelled' : 'running', selected_final_ranking: finished ? summary.selected_final_ranking : null, stop_reason: finished ? summary.stop_reason : null })
      if (path.endsWith('/bridges')) return Response.json(saved)
      if (path.startsWith('/api/v1/assessments/findings/')) { const id = path.split('/').at(-1)!, detail = saved.stages.find((item) => item.stage.result_id === id)!; return Response.json({ id, run_id: RUN, task_kind: 'research', run: { ...run(), artifact_id: null, assessment_id: null }, payload: detail.result, discovery_stage: detail, assessment: head(id) }) }
      if (path.startsWith('/api/v1/assessments/heads/') && path.includes('/history?')) return Response.json({ assessments: [], next_cursor: null })
      if (path.startsWith('/api/v1/assessments/heads/')) return Response.json(head(path.split('/').at(-1)!))
      if (path === '/api/v1/assessments/capabilities') return Response.json(assessmentCapabilities())
      if (path.startsWith('/api/v1/assessments/jobs?')) return Response.json({ jobs: [], next_cursor: null })
      if (path.startsWith('/api/v1/artifacts/jobs?')) return Response.json({ jobs: [], next_cursor: null })
      if (path === '/api/v1/shares/capabilities') return Response.json(sharingCapabilities(false))
      if (path.startsWith('/api/v1/shares?')) return Response.json({ shares: [], next_cursor: null })
      if (path === '/api/v1/knowledge/watches/capabilities') return Response.json(watchCapabilities())
      if (path.startsWith('/api/v1/knowledge/watches?')) return Response.json({ watches: [watch({ source_result_id: root, source_count: 1, expires_at: new Date(started + 3600000).toISOString() })], next_cursor: null })
      if (path === `/api/v1/knowledge/watches/${WATCH}`) return Response.json(watch({ source_result_id: root, source_count: 1, expires_at: new Date(started + 3600000).toISOString() }))
      if (path.includes(`/watches/${WATCH}/sources?`)) return Response.json({ sources: [source()], next_cursor: null })
      if (path.includes(`/watches/${WATCH}/ticks?`)) return Response.json({ ticks: [], next_cursor: null })
      if (path.startsWith('/api/v1/knowledge/graph?')) return Response.json({ nodes: [], edges: [], truncated: false, coverage })
      if (path.startsWith('/api/v1/knowledge?')) return Response.json({ items: [], truncated: false, coverage })
      throw new Error(`Unexpected App collection fixture route: ${path}`)
    }); vi.stubGlobal('fetch', fetch); vi.stubGlobal('EventSource', Stream)
    render(<App />); await flush(); await flush()
    fireEvent.click(screen.getByRole('button', { name: /Synthetic bounded multi-bridge question/ })); await flush(); await flush()
    fireEvent.click(screen.getByRole('button', { name: /Synthetic watch/ })); await flush(); await flush()
    expect(screen.getByRole('region', { name: 'Explore investigation collection' })).toBeVisible(); const stream = Stream.instances[0]; act(() => stream.emit('open'))
    let sequence = 0
    for (let call = 0; call < 20; call++) {
      for (const kind of ['external_started', 'bridge_committed', 'checkpoint']) { act(() => stream.emit('activity', { sequence: ++sequence, kind, summary: `Synthetic recorded ${kind}` })); await act(async () => { await vi.advanceTimersByTimeAsync(kind === 'external_started' ? 3000 : 500) }) }
      if (call === 9) fundingClosed = true
    }
    const beforeFinal = reads.filter((item) => item.path.endsWith('/explore-collection')).length; finished = true; act(() => stream.emit('end')); await flush(); await flush()
    expect(reads.filter((item) => item.path.endsWith('/explore-collection')).length).toBeGreaterThan(beforeFinal)
    expect(screen.getByRole('region', { name: 'Saved Explore bridge collection' })).toHaveTextContent('cancelled')
    const ranked = screen.getByRole('region', { name: 'Model-ranked bridges' }); fireEvent.click(within(ranked).getAllByRole('button')[0]); await flush(); await flush()
    expect(screen.getByRole('region', { name: 'Exact saved finding version' })).toBeVisible(); expect(screen.queryByRole('region', { name: 'Explore investigation collection' })).toBeNull()
    const hiddenAt = reads.length; await act(async () => { await vi.advanceTimersByTimeAsync(65000) }); expect(reads.slice(hiddenAt).some((item) => /\/bridges$|\/explore-collection$/.test(item.path))).toBe(false)
    const metered = [...reads.map((item) => item.at), ...Stream.opened], peak = Math.max(...metered.map((at) => metered.filter((next) => next >= at && next < at + 60000).length))
    expect(peak).toBeLessThanOrEqual(60); expect(reads.filter((item) => item.path.endsWith('/explore-collection')).length).toBeLessThanOrEqual(7)
    expect(reads.filter((item) => item.path.endsWith('/explore-protocols'))).toHaveLength(1); expect(reads.some((item) => /operator/.test(item.path))).toBe(false); expect(methods.every((method) => method === 'GET')).toBe(true)
  })
})
