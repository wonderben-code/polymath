import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ArtifactJob, SavedArtifact } from '../artifacts/types'
import type { FindingHead, FindingVersion } from './types'
import { FindingVersionWorkspace } from './FindingVersionWorkspace'
import { ASSESSMENT, bridge, CANDIDATE, DATE, findingVersion, HASH, head, LEFT_QUOTE, NEW_QUOTE, revisedResearchResult, RIGHT_QUOTE, ROOT, RUN, SOURCE_RUN } from './fixtures.test-support'

const ARTIFACT = 'ffffffff-ffff-4fff-8fff-ffffffffffff'
const DOCUMENT_RUN = '12121212-1212-4212-8212-121212121212'
class Activity extends EventTarget { close = vi.fn() }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done }); return { promise, resolve } }
function currentHead(): FindingHead { return findingVersion().assessment }
function historicalHead(): FindingHead { return head({ current_result_id: CANDIDATE, current_run_id: RUN, revision: 1, last_assessment_id: ASSESSMENT, is_current: false, is_supported: false, is_historical: true, can_generate: false }) }
function originalVersion(): FindingVersion {
  const version = findingVersion(), payload = revisedResearchResult(true)
  payload.finding = bridge(); payload.memory_reads = []
  payload.sources[1] = { ...payload.sources[1], title: 'Synthetic prior right source', text: RIGHT_QUOTE }
  payload.quotes[1] = { ...payload.quotes[1], quote: RIGHT_QUOTE, end: RIGHT_QUOTE.length, source_sha256: HASH }
  return { id: ROOT, run_id: SOURCE_RUN, task_kind: 'research', run: { ...version.run, id: SOURCE_RUN, task_kind: 'research', result_id: ROOT, assessment_id: null, goal: 'Original synthetic investigation.' }, payload, assessment: historicalHead() }
}
function document(source: FindingVersion): SavedArtifact {
  const paragraph = () => ({ text: 'A synthetic source-bound summary, not scientific evidence.', label: 'postulated' as const, citation_ordinals: [0, 1] })
  return { id: ARTIFACT, run_id: DOCUMENT_RUN, kind: 'write_summary', status: 'ready', created_at: DATE, source_result_id: source.id, source_run_id: source.run_id, source_sha256: HASH, formalisation_id: null, formalisation_checks: [], passages: source.payload.sources.map((source, ordinal) => ({ ordinal, source_id: source.source_id, title: source.title, url: source.url, content_sha256: HASH, content_kind: source.content_kind, quote: source.text, supports: 'Synthetic support.', claim_id: null })), payload: { kind: 'write_summary', status: 'ready', draft: { kind: 'summary', title: 'Saved synthetic summary', abstract: [paragraph()], sections: [{ heading: 'The finding', paragraphs: [paragraph()] }, { heading: 'What remains open', paragraphs: [paragraph()] }], limitations: ['Synthetic fixtures are not science.'] }, checks: [], events: [], usage: { model_calls: 1, input_tokens_reported: 10, output_tokens_reported: 20, calls_without_usage: 0 }, error_code: null, reason: null, validation_scope: 'citation_integrity_and_bounded_checks' }, markdown: '# Saved synthetic summary\n', markdown_sha256: HASH, payload_sha256: HASH, basis_sha256: HASH }
}
function documentJob(overrides: Partial<ArtifactJob> = {}): ArtifactJob { return { id: DOCUMENT_RUN, mode: 'explore', task_kind: 'write_summary', goal: 'Synthetic source-bound document.', status: 'succeeded', created_at: DATE, updated_at: DATE, result_id: null, artifact_id: ARTIFACT, error_code: null, external_outcome_unknown: false, ...overrides } }
function server(initial = findingVersion(), savedDocument = false) {
  const state = { version: initial, head: initial.assessment, headStatus: 200, versionStatus: 200, historyStatus: 200, headResponse: null as Promise<Response> | null, versionResponse: null as Promise<Response> | null, jobs: savedDocument ? [documentJob()] : [] }
  const artifact = document(initial)
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const path = input.toString()
    if (path.startsWith('/api/v1/assessments/heads/')) return state.headResponse ?? Response.json(state.headStatus === 200 ? state.head : { detail: 'private_head_error' }, { status: state.headStatus })
    if (path.startsWith('/api/v1/assessments/findings/')) return state.versionResponse ?? Response.json(state.versionStatus === 200 ? state.version : { detail: 'private_finding_error' }, { status: state.versionStatus })
    if (path.startsWith('/api/v1/artifacts/jobs?')) return Response.json(state.historyStatus === 200 ? { jobs: state.jobs, next_cursor: null } : { detail: 'private_document_session_error' }, { status: state.historyStatus })
    if (path === '/api/v1/artifacts/jobs' && init?.method === 'POST') { const body = JSON.parse(init.body as string) as { kind: ArtifactJob['task_kind'] }; const created = documentJob({ task_kind: body.kind, status: 'queued', artifact_id: null }); state.jobs = [created]; return Response.json(created, { status: 201 }) }
    if (path === `/api/v1/artifacts/jobs/${DOCUMENT_RUN}`) return Response.json(state.jobs[0])
    if (path === `/api/v1/artifacts/${ARTIFACT}`) return Response.json(artifact)
    if (path === '/api/v1/publications/capabilities') return Response.json({ configured: true, publisher: { identifier: 'fixture-publisher', display_name: 'Synthetic Publisher' }, destinations: ['sandbox'], can_prepare: true })
    if (path === '/api/v1/operator/session') return Response.json({ configured: false, authenticated: false, actor_id: null, display_name: null, expires_at: null, csrf_token: null })
    if (path.startsWith('/api/v1/publications?')) return Response.json({ manifests: [], next_cursor: null })
    throw new Error('Unexpected synthetic fixture request')
  })
  vi.stubGlobal('fetch', fetch); vi.stubGlobal('EventSource', Activity)
  return { state, fetch }
}
function workspace(overrides: Partial<Parameters<typeof FindingVersionWorkspace>[0]> = {}) {
  const props = { resultId: CANDIDATE, enabled: true, csrfToken: HASH, canGenerate: true, onSessionExpired: vi.fn(), onOpenFinding: vi.fn(), onOpenRun: vi.fn(), onClose: vi.fn(), ...overrides }
  return { ...render(<FindingVersionWorkspace {...props} />), props }
}
function generationButtons() { return within(screen.getByRole('group', { name: 'Develop this finding' })).getAllByRole('button') }
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

describe('exact applied finding version reader', () => {
  it('shows the actual reassessment task and hydrated original source passages without starting work', async () => {
    const { fetch } = server(), { props } = workspace()
    expect(await screen.findByRole('heading', { name: 'Synthetic revised bridge' })).toBeVisible()
    expect(screen.getByText('EVIDENCE REASSESSMENT / RESEARCH RECORD')).toBeVisible()
    expect(await screen.findByText('Current supported model assessment')).toBeVisible()
    const evidence = screen.getByRole('region', { name: 'Exact saved finding version' })
    const quoted = within(evidence).getByText(NEW_QUOTE, { selector: 'blockquote' })
    await userEvent.click(quoted.closest('details')!.querySelector('summary')!)
    expect(quoted).toBeVisible()
    expect(within(screen.getByRole('list', { name: 'Saved sources' })).getByRole('link', { name: /Synthetic new right source/ })).toHaveAttribute('href', 'https://example.org/synthetic-right')
    await userEvent.click(screen.getByRole('button', { name: 'Open earlier investigation 1' }))
    expect(props.onOpenRun).toHaveBeenCalledExactlyOnceWith(SOURCE_RUN)
    await userEvent.click(screen.getByRole('button', { name: 'Close finding version' }))
    expect(props.onClose).toHaveBeenCalledTimes(1)
    expect(fetch.mock.calls.every(([, init]) => init?.method === 'GET')).toBe(true)
  })

  it('waits for an actual head, then generates only from the exact positive result ID', async () => {
    const pending = deferred<Response>(), { state, fetch } = server(); state.headResponse = pending.promise
    workspace()
    await screen.findByRole('heading', { name: 'Synthetic revised bridge' })
    expect(screen.getByText('Checking finding version…')).toBeVisible()
    expect(generationButtons().every((button) => button.hasAttribute('disabled'))).toBe(true)
    await act(async () => { state.headResponse = null; pending.resolve(Response.json(currentHead())) })
    const summary = screen.getByRole('button', { name: /Write summary/ })
    await waitFor(() => expect(summary).toBeEnabled())
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
    await userEvent.click(summary)
    await screen.findByRole('button', { name: 'Cancel this task' })
    const posts = fetch.mock.calls.filter(([, init]) => init?.method === 'POST')
    expect(posts).toHaveLength(1)
    expect(posts[0]).toEqual(['/api/v1/artifacts/jobs', expect.objectContaining({ body: JSON.stringify({ kind: 'write_summary', source_result_id: CANDIDATE }), headers: expect.objectContaining({ 'X-CSRF-Token': HASH }) })])
  })

  it('keeps original historical research readable and navigates to the exact current finding', async () => {
    const { fetch } = server(originalVersion()), { props } = workspace({ resultId: ROOT })
    expect(await screen.findByRole('heading', { name: 'Synthetic prior bridge' })).toBeVisible()
    expect(await screen.findByText('Historical finding · a newer version is current')).toBeVisible()
    expect(screen.getByText('EXPLORE / RESEARCH RECORD')).toBeVisible()
    const quote = screen.getByText(RIGHT_QUOTE, { selector: 'blockquote' })
    await userEvent.click(quote.closest('details')!.querySelector('summary')!)
    expect(quote).toBeVisible()
    expect(generationButtons().every((button) => button.hasAttribute('disabled'))).toBe(true)
    await userEvent.click(screen.getByRole('button', { name: 'Open current finding version' }))
    expect(props.onOpenFinding).toHaveBeenCalledExactlyOnceWith(CANDIDATE)
    expect(props.onOpenRun).not.toHaveBeenCalled()
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })

  it.each(['historical', 'unsupported'] as const)('keeps %s source documents inspectable while new generation and publication stay disabled', async (stateName) => {
    const version = stateName === 'historical' ? originalVersion() : findingVersion({ assessment: { ...currentHead(), state: 'unsupported', revision: 2, is_supported: false, is_historical: true, can_generate: false } })
    const { fetch } = server(version, true); workspace({ resultId: version.id })
    expect(await screen.findByRole('heading', { name: 'Saved synthetic summary' })).toBeVisible()
    if (stateName === 'unsupported') expect(screen.getByText('Unsupported by the latest model assessment')).toBeVisible()
    expect(generationButtons().every((button) => button.hasAttribute('disabled'))).toBe(true)
    expect(screen.getByRole('link', { name: /Download saved Markdown/ })).toHaveAttribute('href', `/api/v1/artifacts/${ARTIFACT}/export`)
    expect(await screen.findByText(/This document’s finding is historical, unsupported/)).toBeVisible()
    await userEvent.type(screen.getByLabelText('Creator 1 name'), 'Synthetic Author')
    expect(screen.getByRole('button', { name: 'Prepare publication preview' })).toBeDisabled()
    expect(screen.getByRole('list', { name: 'Document source passages' })).toHaveTextContent(LEFT_QUOTE)
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })

  it('keeps generation disabled when app research is unavailable and after a head-read failure', async () => {
    const { state, fetch } = server(); state.headStatus = 503
    const rendered = workspace({ canGenerate: false })
    await screen.findByRole('heading', { name: 'Synthetic revised bridge' })
    expect(await screen.findByText('Current finding status unavailable')).toBeVisible()
    expect(generationButtons().every((button) => button.hasAttribute('disabled'))).toBe(true)
    state.headStatus = 200
    await userEvent.click(screen.getByRole('button', { name: 'Refresh finding status' }))
    await screen.findByText('Current supported model assessment')
    expect(generationButtons().every((button) => button.hasAttribute('disabled'))).toBe(true)
    rendered.rerender(<FindingVersionWorkspace {...rendered.props} canGenerate />)
    await waitFor(() => expect(screen.getByRole('button', { name: /Write summary/ })).toBeEnabled())
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })

  it('keeps unapplied or unavailable candidates closed and only retries their read on explicit reload', async () => {
    const { state, fetch } = server(); state.versionStatus = 404
    workspace()
    expect(await screen.findByRole('alert')).toHaveTextContent('Unapplied candidates do not become current findings.')
    expect(screen.queryByRole('heading', { name: 'Synthetic revised bridge' })).not.toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Develop this finding' })).not.toBeInTheDocument()
    expect(screen.queryByText('private_finding_error')).not.toBeInTheDocument()
    state.versionStatus = 200
    await userEvent.click(screen.getByRole('button', { name: 'Reload finding version' }))
    expect(await screen.findByRole('heading', { name: 'Synthetic revised bridge' })).toBeVisible()
    expect(fetch.mock.calls.filter(([path]) => path.toString().includes('/findings/'))).toHaveLength(2)
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })

  it('clears the reader when a nested document read detects browser expiry', async () => {
    const { state, fetch } = server(); state.historyStatus = 403
    const { props } = workspace()
    await waitFor(() => expect(props.onSessionExpired).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('heading', { name: 'Synthetic revised bridge' })).not.toBeInTheDocument()
    expect(screen.queryByText(NEW_QUOTE, { selector: 'blockquote' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reload finding version' })).toBeDisabled()
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })

  it('clears already displayed private findings on a head-poll expiry without relying on parent unmount', async () => {
    vi.useFakeTimers()
    const { state, fetch } = server(), { props } = workspace()
    await act(async () => {})
    expect(screen.getByRole('heading', { name: 'Synthetic revised bridge' })).toBeVisible()
    state.headStatus = 401
    await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
    expect(props.onSessionExpired).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('heading', { name: 'Synthetic revised bridge' })).not.toBeInTheDocument()
    expect(screen.queryByText(NEW_QUOTE, { selector: 'blockquote' })).not.toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Private finding text and source versions have been cleared.')
    expect(screen.getByRole('button', { name: 'Reload finding version' })).toBeDisabled()
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })

  it('aborts an in-flight private detail when head authentication expires and ignores its late response', async () => {
    vi.useFakeTimers()
    const pending = deferred<Response>(), { state, fetch } = server(); state.versionResponse = pending.promise
    const { props } = workspace()
    await act(async () => {})
    const detailSignal = fetch.mock.calls.find(([path]) => path.toString().includes('/findings/'))?.[1]?.signal
    state.headStatus = 403
    await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
    expect(props.onSessionExpired).toHaveBeenCalledTimes(1)
    expect(detailSignal?.aborted).toBe(true)
    await act(async () => pending.resolve(Response.json(findingVersion())))
    expect(screen.queryByRole('heading', { name: 'Synthetic revised bridge' })).not.toBeInTheDocument()
    expect(screen.queryByText(LEFT_QUOTE, { selector: 'blockquote' })).not.toBeInTheDocument()
  })

  it('ignores late responses for a previous exact selection and aborts on unmount', async () => {
    const pending = deferred<Response>(), { state, fetch } = server(); state.versionResponse = pending.promise
    const rendered = workspace()
    const firstSignal = fetch.mock.calls.find(([path]) => path.toString().includes('/findings/'))?.[1]?.signal
    state.versionResponse = null; state.version = originalVersion(); state.head = historicalHead()
    rendered.rerender(<FindingVersionWorkspace {...rendered.props} resultId={ROOT} />)
    expect(firstSignal?.aborted).toBe(true)
    expect(await screen.findByRole('heading', { name: 'Synthetic prior bridge' })).toBeVisible()
    await act(async () => pending.resolve(Response.json(findingVersion())))
    expect(screen.queryByRole('heading', { name: 'Synthetic revised bridge' })).not.toBeInTheDocument()
    const pendingHead = deferred<Response>(); state.headResponse = pendingHead.promise
    await userEvent.click(screen.getByRole('button', { name: 'Refresh finding status' }))
    const headSignal = fetch.mock.calls.findLast(([path]) => path.toString().includes('/heads/'))?.[1]?.signal
    rendered.unmount()
    expect(headSignal?.aborted).toBe(true)
    await act(async () => pendingHead.resolve(Response.json(historicalHead())))
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })
})
