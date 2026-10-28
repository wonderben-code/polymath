import { BIBLIOGRAPHY } from '../research/bibliography.test-support'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DiscoveryWorkspace } from './DiscoveryWorkspace'
import { DATE, RESULTS, RUN, STAGES, assessment, intent, stageDetail, stageHistory, summary } from './test-support'
import type { DiscoveryStage, DiscoverySummary } from './types'
import type { OwnedRun } from '../research/client'
import { head } from '../assessments/fixtures.test-support'
const CSRF = 'a'.repeat(64), JOB = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
type Handler = (path: string, options?: RequestInit) => Response | undefined | Promise<Response | undefined>
function server(handler?: Handler) {
  const state: { details: DiscoveryStage[]; total: DiscoverySummary } = { details: stageHistory().stages, total: summary() }, writes: { path: string; body: any }[] = []
  const fetch = vi.fn(async (input: RequestInfo | URL, options?: RequestInit): Promise<Response> => {
    const path = input.toString(); if (options?.method === 'POST') writes.push({ path, body: JSON.parse(options.body!.toString()) })
    const custom = await handler?.(path, options); if (custom) return custom
    if (path === `/api/v1/runs/${RUN}/discovery/stages`) return Response.json(stageHistory(state.details))
    if (path === `/api/v1/runs/${RUN}/discovery`) return Response.json({ ...state.total, stages: state.details.map((detail) => detail.stage), selected_final_stage: state.total.selected_final_stage })
    const stage = /^\/api\/v1\/runs\/[^/]+\/discovery\/stages\/([^/]+)$/.exec(path)
    if (stage) return Response.json(state.details.find((detail) => detail.stage.stage_id === stage[1]))
    const requestedHead = /^\/api\/v1\/assessments\/heads\/([^/]+)$/.exec(path)
    if (requestedHead) { const id = requestedHead[1], detail = state.details.find((item) => item.stage.result_id === id), review = detail?.status === 'needs_review'; return Response.json(head({ requested_result_id: id, root_result_id: id, current_result_id: id, current_run_id: RUN, ...(detail ? { discovery_stage: { stage_id: detail.stage.stage_id, ordinal: detail.stage.ordinal, depth: detail.stage.depth, status: detail.status } } : {}), ...(review ? { is_supported: false, can_generate: false } : {}) })) }
    if (path.startsWith('/api/v1/artifacts/jobs?')) return Response.json({ jobs: [], next_cursor: null })
    if (path === '/api/v1/artifacts/jobs' && options?.method === 'POST') { const body = JSON.parse(options.body!.toString()); return Response.json({ id: JOB, mode: 'surprise', task_kind: body.kind, goal: 'Synthetic stage generation', status: 'cancelled', created_at: DATE, updated_at: DATE, result_id: null, artifact_id: null, error_code: null, external_outcome_unknown: false }) }
    if (path === `/api/v1/artifacts/jobs/${JOB}`) return Response.json({ id: JOB, mode: 'surprise', task_kind: writes.at(-1)?.body.kind, goal: 'Synthetic stage generation', status: 'cancelled', created_at: DATE, updated_at: DATE, result_id: null, artifact_id: null, error_code: null, external_outcome_unknown: false })
    if (path === '/api/v1/shares/capabilities') return Response.json({ sharing_available: false, reason: 'sharing_unavailable' })
    if (path.startsWith('/api/v1/shares?')) return Response.json({ shares: [], next_cursor: null })
    throw new Error(`Unexpected synthetic discovery route: ${path}`)
  }); vi.stubGlobal('fetch', fetch)
  return { state, fetch, writes }
}
function settings(status = 'interrupted') { const run: OwnedRun = { id: RUN, mode: 'surprise', task_kind: 'research', goal: 'Synthetic bounded discovery', status, discovery_intent: intent(), created_at: DATE, updated_at: DATE, result_id: null, error_code: null, external_outcome_unknown: false }; return { run, events: [], finalResult: null, enabled: true, csrfToken: CSRF, canGenerate: true, onSessionExpired: vi.fn(), onOpenFinding: vi.fn(), onOpenRun: vi.fn(), onBusyChange: vi.fn() } }
async function ready() { await screen.findByRole('region', { name: 'Inspected saved discovery finding' }); await waitFor(() => expect(screen.getByRole('region', { name: 'Finding version status' })).toHaveTextContent(/Current supported model assessment|Saved discovery evidence needs review/)) }
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers() })
describe('retained Surprise discovery workspace', () => {
  it('separates whole-run totals and stop state from immutable stage usage and uncalibrated scores', async () => {
    const requests = server(); render(<DiscoveryWorkspace {...settings()} />); await ready()
    const totals = screen.getByRole('region', { name: 'Whole discovery run totals' }); expect(totals).toHaveTextContent('Run: interrupted'); expect(totals).toHaveTextContent('20'); expect(totals).toHaveTextContent('123,000 microdollars'); expect(totals).toHaveTextContent('7,000 microdollars'); expect(totals).toHaveTextContent('3,000 microdollars'); expect(totals).toHaveTextContent('model_call_limit')
    const first = screen.getByRole('article', { name: 'Saved discovery stage 1' }); expect(first).toHaveTextContent('Commit-time usage snapshot: 6 model calls'); expect(first).toHaveTextContent('37/100'); expect(first).toHaveTextContent('Uncalibrated model judgement'); expect(first).toHaveTextContent('not a probability'); expect(first).toHaveTextContent('Not found in the bounded search'); expect(first).toHaveTextContent('Strongest recorded objection'); expect(screen.getByText(/No final stage has been explicitly selected/)).toBeVisible()
    expect(screen.queryByText('Model’s explicit final selection')).toBeNull(); expect(requests.writes).toHaveLength(0)
    expect(screen.getByRole('button', { name: /Write summary/ })).toBeDisabled(); expect(screen.getByText(/Finish or stop that investigation before starting another model task/)).toBeVisible()
  })
  it('preserves an explicit earlier final selection even after late cancellation without sorting by confidence', async () => {
    const requests = server(); requests.state.details[2].stage.assessment = assessment({ confidence_score: 99, confidence: 'strong' }); requests.state.total = summary({ run_status: 'cancelled', selected_final_stage: requests.state.details[0].stage, stop_reason: 'cancelled_after_finish' })
    render(<DiscoveryWorkspace {...settings('cancelled')} />); await ready()
    expect(screen.getByRole('region', { name: 'Whole discovery run totals' })).toHaveTextContent('explicitly selected saved stage 1'); expect(screen.getByRole('region', { name: 'Whole discovery run totals' })).toHaveTextContent('actual state remains cancelled')
    expect(within(screen.getByRole('article', { name: 'Saved discovery stage 1' })).getByText('Model’s explicit final selection')).toBeVisible()
    expect(screen.getAllByRole('article', { name: /Saved discovery stage/ }).map((card) => within(card).getByRole('heading', { level: 3 }).textContent)).toEqual(['Synthetic committed bridge 1', 'Synthetic committed bridge 2', 'Synthetic committed bridge 3'])
    expect(screen.getByRole('article', { name: 'Saved discovery stage 3' })).toHaveTextContent('99/100'); expect(requests.writes).toHaveLength(0)
  })
  it('opens parent premises separately from exact original quoted evidence', async () => {
    const requests = server(), props = settings('cancelled'); requests.state.total.run_status = 'cancelled'; render(<DiscoveryWorkspace {...props} />); await ready()
    const third = screen.getByRole('article', { name: 'Saved discovery stage 3' }); expect(third).toHaveTextContent('Selected parent premise'); expect(third).toHaveTextContent('conceptual premises')
    await userEvent.click(within(third).getByRole('button', { name: 'Saved stage 2' })); await waitFor(() => expect(screen.getByRole('region', { name: 'Inspected saved discovery finding' })).toHaveTextContent('INSPECTING SAVED STAGE 2'))
    expect(requests.fetch.mock.calls.some(([path]) => path.toString().endsWith(`/discovery/stages/${STAGES[1]}`))).toBe(true)
    await userEvent.click(within(third).getByText(/Primary quoted evidence/)); expect(third).toHaveTextContent('Synthetic original premise from paper A.'); expect(third).toHaveTextContent('Saved source quote characters 0–'); expect(third).toHaveTextContent('Paper identity group 1'); expect(props.onOpenRun).not.toHaveBeenCalled(); expect(requests.writes).toHaveLength(0)
  })
  it('targets the exact inspected stage result when writing from a retained cancelled run', async () => {
    const requests = server(); requests.state.total.run_status = 'cancelled'; render(<DiscoveryWorkspace {...settings('cancelled')} />); await ready()
    await userEvent.click(screen.getByRole('button', { name: 'Inspect stage 2 and develop its exact finding' })); const reader = screen.getByRole('region', { name: 'Inspected saved discovery finding' }); await waitFor(() => expect(reader).toHaveTextContent('INSPECTING SAVED STAGE 2')); const write = within(reader).getByRole('button', { name: /Write summary/ }); await waitFor(() => expect(write).toBeEnabled()); await userEvent.click(write)
    await waitFor(() => expect(requests.writes).toHaveLength(1)); expect(requests.writes[0]).toEqual({ path: '/api/v1/artifacts/jobs', body: { kind: 'write_summary', source_result_id: RESULTS[1] } })
  })
  it('uses authoritative merged paper groups and disables fresh generation when evidence needs review', async () => {
    const requests = server(); requests.state.total.run_status = 'cancelled'; const original = requests.state.details[0]; requests.state.details[0] = { ...original, status: 'needs_review', primary_paper_groups: [{ paper_id: 'paper-1', source_ids: original.result.sources.map((source) => source.source_id) }] }
    render(<DiscoveryWorkspace {...settings('cancelled')} />); await ready(); const first = screen.getByRole('article', { name: 'Saved discovery stage 1' }); expect(first).toHaveTextContent('Current evidence or ancestry needs review'); expect(first).toHaveTextContent('1 paper identity group')
    const reader = screen.getByRole('region', { name: 'Inspected saved discovery finding' }); expect(reader).toHaveTextContent('2 saved source versions · 1 paper identity'); expect(within(reader).getByRole('button', { name: /Formalise/ })).toBeDisabled(); expect(within(reader).getByRole('button', { name: /Write paper/ })).toBeDisabled(); expect(requests.writes).toHaveLength(0)
  })
  it('renders full-text window and global quote ranges with literal model text and separate source hashes', async () => {
    const requests = server(), original = requests.state.details[0], result = original.result, source = result.sources[0], snapshot = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', body = `🧠 ${source.text}`, coverage = { snapshot_id: snapshot, snapshot_sha256: 'c'.repeat(64), arxiv_id: BIBLIOGRAPHY.arxiv_id, arxiv_version: 2, start: 100, end: 100 + Array.from(body).length, total: 10000 }, sourceId = `arxiv-html:${snapshot}:100:${coverage.end}`
    if (result.finding?.outcome !== 'bridge') throw new Error('Invalid synthetic bridge fixture')
    requests.state.details[0] = { ...original, result: { ...result, sources: [{ ...source, source_id: sourceId, text: body, content_kind: 'full_text', coverage, bibliography: BIBLIOGRAPHY, url: 'https://arxiv.org/html/2610.00001v2' }, result.sources[1]], quotes: [{ ...result.quotes[0], source_id: sourceId, content_kind: 'full_text', start: 2, end: 2 + Array.from(source.text).length }, result.quotes[1]], finding: { ...result.finding, evidence: [{ ...result.finding.evidence[0], source_id: sourceId }, result.finding.evidence[1]] } }, primary_paper_groups: [{ paper_id: 'paper-1', source_ids: [sourceId] }, original.primary_paper_groups[1]] }
    requests.state.details[0].stage.assessment.confidence_rationale = '<script>synthetic mathematical markup remains literal</script>'
    render(<DiscoveryWorkspace {...settings()} />); await ready(); const first = screen.getByRole('article', { name: 'Saved discovery stage 1' })
    await userEvent.click(within(first).getByText(/Primary quoted evidence/)); expect(first).toHaveTextContent('Within this saved excerpt, quote characters 2–'); expect(first).toHaveTextContent(`Quoted characters 102–${102 + Array.from(source.text).length} in the extracted paper`); expect(first).toHaveTextContent('Mathematical alt text and TeX'); expect(first).toHaveTextContent('semantics are unverified'); expect(first).toHaveTextContent('<script>synthetic mathematical markup remains literal</script>'); expect(first.querySelector('script')).toBeNull()
    expect(within(first).getByRole('link', { name: /original versioned paper/ })).toHaveAttribute('href', 'https://arxiv.org/html/2610.00001v2'); await userEvent.click(within(first).getByText('Full-text extraction version')); expect(first).toHaveTextContent(coverage.snapshot_sha256); await userEvent.click(within(first).getAllByText('Exact quoted source content hash')[0]); expect(first).toHaveTextContent(result.quotes[0].source_sha256); expect(requests.writes).toHaveLength(0)
  })
  it('clears every private stage and aborts stale readbacks when the browser expires', async () => {
    let expired = false
    const requests = server((path) => expired && path.endsWith('/discovery/stages') ? Response.json({ detail: 'research_session_required' }, { status: 401 }) : undefined), props = settings('cancelled'); requests.state.total.run_status = 'cancelled'
    render(<DiscoveryWorkspace {...props} />); await ready(); expired = true; await userEvent.click(screen.getByRole('button', { name: 'Refresh saved discoveries' }))
    expect(await screen.findByText('The browser session expired or changed. Private discovery stages, sources and totals have been cleared.')).toBeVisible(); expect(screen.queryByRole('article', { name: /Saved discovery stage/ })).toBeNull(); expect(screen.queryByText('Synthetic committed bridge 1')).toBeNull(); expect(props.onSessionExpired).toHaveBeenCalledOnce(); expect(requests.writes).toHaveLength(0)
  })
  it('keeps a confirmed stage while a later partial run stops, and reloads through GETs only', async () => {
    const requests = server(); requests.state.details = [stageDetail(0)]; requests.state.total = summary({ run_status: 'cancelled', stages: [requests.state.details[0].stage], selected_final_stage: null, stop_reason: 'insufficient_evidence_after_saved_stage', total_usage: { model_calls: 9, input_tokens_reported: '270', output_tokens_reported: '360', calls_without_usage: 0 } })
    render(<DiscoveryWorkspace {...settings('cancelled')} />); await ready(); expect(screen.getByRole('region', { name: 'Whole discovery run totals' })).toHaveTextContent('insufficient_evidence_after_saved_stage'); expect(screen.getAllByRole('article', { name: /Saved discovery stage/ })).toHaveLength(1)
    await userEvent.click(screen.getByRole('button', { name: 'Refresh saved discoveries' })); await waitFor(() => expect(requests.fetch.mock.calls.filter(([path]) => path.toString().endsWith('/discovery')).length).toBe(2)); expect(requests.writes).toHaveLength(0)
  })
  it('clears selection on browser scope rotation and ignores an aborted old detail response', async () => {
    let resolve!: (response: Response) => void
    const requests = server((path) => path.endsWith(`/discovery/stages/${STAGES[1]}`) ? new Promise<Response>((done) => { resolve = done }) : undefined), props = settings('cancelled'); requests.state.total.run_status = 'cancelled'
    const view = render(<DiscoveryWorkspace {...props} />); await ready(); fireEvent.click(screen.getByRole('button', { name: 'Inspect stage 2 and develop its exact finding' })); await waitFor(() => expect(resolve).toBeDefined()); view.rerender(<DiscoveryWorkspace {...props} enabled={false} csrfToken={null} />)
    await act(async () => resolve(Response.json(stageDetail(1)))); expect(screen.queryByRole('article', { name: /Saved discovery stage/ })).toBeNull(); expect(screen.queryByText('Synthetic original premise from paper C.')).toBeNull(); expect(requests.writes).toHaveLength(0)
  })
})
