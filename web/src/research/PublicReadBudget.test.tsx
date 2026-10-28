import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { FindingVersionWorkspace } from '../assessments/FindingVersionWorkspace'
import { ASSESSMENT, DATE, ROOT, RUN, SOURCE_RUN, VERSION, WATCH, capabilities as assessmentCapabilities, findingVersion, head, job, record, revisedResearchResult } from '../assessments/fixtures.test-support'
import { capabilities as watchCapabilities, detail, source, watch } from '../ingestion/fixtures.test-support'
import { KnowledgeBrowser } from '../knowledge/KnowledgeBrowser'
import { capabilities as sharingCapabilities } from '../sharing/test-support'
import { BROWSER_CSRF } from '../budget/test-support'
import { publicAccess } from './accessFixtures.test-support'
import { NativeResearchProvider } from './nativeContext'
import { useNativeResearchContext } from './useNativeResearchContext'

class Stream extends EventTarget {
  static instances: Stream[] = []
  static opened: number[] = []
  close = vi.fn()
  constructor(readonly url: string) { super(); Stream.instances.push(this); Stream.opened.push(Date.now()) }
  emit(kind: string, value?: unknown) { this.dispatchEvent(value === undefined ? new Event(kind) : new MessageEvent(kind, { data: JSON.stringify(value) })) }
}
const noop = () => {}
function Workspace() {
  const native = useNativeResearchContext({ enabled: true, csrfToken: BROWSER_CSRF, required: true, available: true, onBrowserExpired: noop })
  return <NativeResearchProvider value={native}><FindingVersionWorkspace resultId={ROOT} enabled csrfToken={BROWSER_CSRF} canGenerate={false} onSessionExpired={noop} onOpenFinding={noop} onOpenRun={noop} onClose={noop} /><KnowledgeBrowser enabled csrfToken={BROWSER_CSRF} onOpenRun={noop} onOpenFinding={noop} onSessionExpired={noop} /></NativeResearchProvider>
}
async function flush() { await act(async () => { await vi.advanceTimersByTimeAsync(0) }) }
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

describe('combined public automatic read budget', () => {
  it('fits the 60-read/minute gate while a slow twenty-call reassessment and its finding/map remain open', async () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date(DATE)); Stream.instances = []; Stream.opened = []
    const started = Date.now(), reads: { path: string; at: number }[] = [], methods: string[] = []
    let complete = false
    const savedWatch = watch({ id: WATCH, source_result_id: ROOT, source_count: 1 }), savedSource = source({ id: VERSION, content_sha256: 'b'.repeat(64) })
    const coverage = { scope: 'current_browser_session', findings_considered: 0 }
    const fetch = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
      const path = String(input), method = options?.method ?? 'GET'; methods.push(method)
      // Match PublicResourceMiddleware: capabilities are excluded; an SSE
      // connection itself consumes one read, each delivered event does not.
      if (!path.endsWith('/capabilities')) reads.push({ path, at: Date.now() })
      if (path === '/api/v1/research/protocols') return Response.json({ protocol: 'polymath-research-protocols-v1', activation_sha256: publicAccess().expected_activation_sha256, capabilities: [] })
      if (path === '/api/v1/research/access') return Response.json(publicAccess({ expires_at: new Date(started + 3600000).toISOString() }))
      if (path === `/api/v1/assessments/findings/${ROOT}`) return Response.json(findingVersion({ id: ROOT, run_id: SOURCE_RUN, task_kind: 'research', run: { id: SOURCE_RUN, task_kind: 'research', mode: 'explore', goal: 'Synthetic exact prior finding.', status: 'succeeded', created_at: DATE, updated_at: DATE, result_id: ROOT, artifact_id: null, assessment_id: null, error_code: null, external_outcome_unknown: false }, payload: revisedResearchResult(true), assessment: head() }))
      if (path === `/api/v1/assessments/heads/${ROOT}`) return Response.json(head(complete ? { revision: 1, last_assessment_id: ASSESSMENT } : {}))
      if (path.includes(`/heads/${ROOT}/history?`)) return Response.json({ assessments: complete ? [record()] : [], next_cursor: null })
      if (path === '/api/v1/assessments/capabilities') return Response.json(assessmentCapabilities())
      if (path.startsWith('/api/v1/assessments/jobs?')) return Response.json({ jobs: [job({ status: 'running' })], next_cursor: null })
      if (path === `/api/v1/assessments/jobs/${RUN}`) return Response.json(job({ status: complete ? 'succeeded' : 'running', assessment_id: complete ? ASSESSMENT : null }))
      if (path === `/api/v1/assessments/${ASSESSMENT}`) return Response.json(record())
      if (path.startsWith('/api/v1/artifacts/jobs?')) return Response.json({ jobs: [], next_cursor: null })
      if (path === '/api/v1/shares/capabilities') return Response.json(sharingCapabilities(false))
      if (path.startsWith('/api/v1/shares?')) return Response.json({ shares: [], next_cursor: null })
      if (path === '/api/v1/knowledge/watches/capabilities') return Response.json(watchCapabilities())
      if (path.startsWith('/api/v1/knowledge/watches?')) return Response.json({ watches: [savedWatch], next_cursor: null })
      if (path === `/api/v1/knowledge/watches/${WATCH}`) return Response.json(savedWatch)
      if (path.endsWith(`/watches/${WATCH}/sources/${VERSION}`)) return Response.json(detail({ id: VERSION, version_id: VERSION, watch_id: WATCH, content_sha256: 'b'.repeat(64) }))
      if (path.includes(`/watches/${WATCH}/sources?`)) return Response.json({ sources: [savedSource], next_cursor: null })
      if (path.includes(`/watches/${WATCH}/ticks?`)) return Response.json({ ticks: [], next_cursor: null })
      if (path.startsWith('/api/v1/knowledge/graph?')) return Response.json({ nodes: [], edges: [], truncated: false, coverage })
      if (path.startsWith('/api/v1/knowledge?')) return Response.json({ items: [], truncated: false, coverage })
      throw new Error(`Unexpected read-budget fixture route: ${path}`)
    })
    vi.stubGlobal('fetch', fetch); vi.stubGlobal('EventSource', Stream)
    render(<Workspace />); await flush(); await flush()
    fireEvent.click(screen.getByRole('button', { name: /Synthetic watch/ })); await flush(); await flush()
    expect(screen.getByText('Meta reassessment · running')).toBeVisible()
    expect(screen.getByRole('region', { name: 'Exact saved finding version' })).toBeVisible()
    const stream = Stream.instances[0]; expect(stream.url).toBe(`/api/v1/runs/${RUN}/events?after=0`)
    act(() => stream.emit('open'))
    let sequence = 0
    function event(kind: string) { act(() => stream.emit('activity', { sequence: ++sequence, kind, summary: `Synthetic retained ${kind}` })) }
    for (let call = 0; call < 20; call++) {
      event('external_started')
      await act(async () => { await vi.advanceTimersByTimeAsync(2800) })
      event('external_observed'); event('checkpoint')
      await act(async () => { await vi.advanceTimersByTimeAsync(500) })
      event('assessment_source_read'); event('checkpoint')
      await act(async () => { await vi.advanceTimersByTimeAsync(700) })
    }
    const priorFinalReads = reads.filter((item) => item.path === `/api/v1/assessments/jobs/${RUN}`).length
    complete = true; act(() => stream.emit('end')); await flush(); await flush()
    expect(reads.filter((item) => item.path === `/api/v1/assessments/jobs/${RUN}`).length).toBeGreaterThan(priorFinalReads)
    expect(screen.getByRole('article', { name: 'Saved evidence reassessment' })).toBeVisible()
    expect(stream.close).toHaveBeenCalled()
    const metered = [...reads.map((item) => item.at), ...Stream.opened]
    const counts = [0, 1].map((minute) => metered.filter((at) => at >= started + minute * 60000 && at < started + (minute + 1) * 60000).length)
    expect(Math.max(...counts)).toBeLessThanOrEqual(60)
    const rollingPeak = Math.max(...metered.map((start) => metered.filter((at) => at >= start && at < start + 60000).length))
    expect(rollingPeak).toBeLessThanOrEqual(60)
    expect(reads.filter((item) => item.path === `/api/v1/assessments/jobs/${RUN}`).length).toBeLessThanOrEqual(10)
    expect(reads.filter((item) => item.path === '/api/v1/research/access').length).toBeGreaterThanOrEqual(8)
    expect(reads.some((item) => item.path.startsWith('/api/v1/knowledge/graph?'))).toBe(true)
    expect(methods.every((method) => method === 'GET')).toBe(true)
    expect(reads.some((item) => /operator|\/session/.test(item.path))).toBe(false)
  })
})
