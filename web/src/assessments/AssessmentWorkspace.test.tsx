import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AssessmentWorkspace } from './AssessmentWorkspace'
import { AssessmentPreview } from './AssessmentPreview'
import { ASSESSMENT, capabilities, CANDIDATE, DATE, head, job, NEW_QUOTE, record as assessmentRecord, ROOT, RUN, VERSION, WATCH } from './fixtures.test-support'
import type { AssessmentJob, AssessmentRecord, FindingHead } from './types'
import type { WatchSource } from '../ingestion/client'

const CSRF = 'e'.repeat(64)
class Stream extends EventTarget {
  static instances: Stream[] = []
  close = vi.fn()
  constructor(readonly url: string) { super(); Stream.instances.push(this) }
  send(kind: string, value?: unknown) { this.dispatchEvent(value === undefined ? new Event(kind) : new MessageEvent(kind, { data: JSON.stringify(value) })) }
}
function papers(count = 1): WatchSource[] { return Array.from({ length: count }, (_, index) => ({ id: index === 0 ? VERSION : `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`, source_id: `fixture:new-${index}`, title: `Synthetic new source ${index + 1}`, url: 'https://example.org/synthetic-new', content_kind: 'abstract', content_sha256: 'b'.repeat(64), first_seen_at: DATE, run_id: RUN })) }
function server(options: { available?: boolean; initialJob?: AssessmentJob; saved?: AssessmentRecord; currentHead?: FindingHead } = {}) {
  const state = { available: options.available ?? true, jobs: options.initialJob ? [options.initialJob] : [] as AssessmentJob[], saved: options.saved ?? null as AssessmentRecord | null, currentHead: options.currentHead ?? head(), lostWrite: false, authStatus: 200 }
  const requests = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const path = String(input)
    if (state.authStatus !== 200) return Response.json({ detail: 'research_session_required' }, { status: state.authStatus })
    if (path.endsWith('/capabilities')) return Response.json(capabilities(state.available))
    if (path === `/api/v1/assessments/heads/${ROOT}`) return Response.json(state.currentHead)
    if (path.includes(`/heads/${ROOT}/history?`)) return Response.json({ assessments: state.saved ? [state.saved] : [], next_cursor: null })
    if (path.startsWith('/api/v1/assessments/jobs?')) return Response.json({ jobs: state.jobs, next_cursor: null })
    if (path === '/api/v1/assessments/jobs' && init?.method === 'POST') {
      state.jobs = [job(JSON.parse(String(init.body)))]; if (state.lostWrite) throw new TypeError('Synthetic lost queue receipt')
      return Response.json(state.jobs[0], { status: 201 })
    }
    if (path === `/api/v1/assessments/jobs/${RUN}`) return Response.json(state.jobs[0])
    if (path === `/api/v1/assessments/${ASSESSMENT}`) return Response.json(state.saved)
    if (path === `/api/v1/runs/${RUN}/cancel`) { state.jobs = [{ ...state.jobs[0], status: 'cancel_requested', updated_at: '2099-10-28T00:00:01Z' }]; return Response.json(state.jobs[0]) }
    throw new Error(`Unexpected synthetic assessment path: ${path}`)
  })
  Stream.instances = []; vi.stubGlobal('fetch', requests); vi.stubGlobal('EventSource', Stream)
  return { state, requests }
}
const props = () => ({ watchId: WATCH, sourceResultId: ROOT, sources: papers(), csrfToken: CSRF, enabled: true, onSessionExpired: vi.fn() })
async function chooseAndConsent() {
  const choice = await screen.findByRole('checkbox', { name: 'Reassess with source: Synthetic new source 1' })
  await waitFor(() => expect(choice).toBeEnabled())
  await userEvent.click(choice)
  await userEvent.click(screen.getByRole('checkbox', { name: /I want Meta to reassess this exact finding/ }))
}
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })
describe('explicit version-bound Meta reassessments', () => {
  it('keeps the default model gate closed and never submits from collection or loading', async () => {
    const { requests } = server({ available: false })
    render(<AssessmentWorkspace {...props()} />)
    await screen.findByText(/Meta reassessment is disabled or unavailable/)
    expect(screen.getByRole('checkbox', { name: /Reassess with source:/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Reassess selected evidence' })).toBeDisabled()
    expect(requests.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
    expect(requests.mock.calls.some(([path]) => String(path).includes('/session'))).toBe(false)
  })
  it('requires fresh explicit consent, binds exact watch/source/head IDs, and synchronously prevents a double POST', async () => {
    const { requests } = server()
    render(<AssessmentWorkspace {...props()} />)
    await chooseAndConsent()
    const button = screen.getByRole('button', { name: 'Reassess selected evidence' })
    fireEvent.click(button); fireEvent.click(button)
    await screen.findByText('Meta reassessment · queued')
    const writes = requests.mock.calls.filter(([, init]) => init?.method === 'POST')
    expect(writes).toHaveLength(1)
    expect(JSON.parse(String(writes[0][1]?.body))).toEqual({ source_result_id: ROOT, watch_id: WATCH, new_source_version_ids: [VERSION], expected_head_revision: 0 })
    expect(writes[0][1]?.headers).toMatchObject({ 'X-CSRF-Token': CSRF })
    expect(screen.getByRole('checkbox', { name: /I want Meta to reassess/ })).not.toBeChecked()
    expect(Stream.instances[0].url).toBe(`/api/v1/runs/${RUN}/events?after=0`)
  })
  it('caps source selection at five immutable versions and resets consent after selection changes', async () => {
    server()
    render(<AssessmentWorkspace {...props()} sources={papers(6)} />)
    const choices = await screen.findAllByRole('checkbox', { name: /Reassess with source:/ })
    await waitFor(() => expect(choices[0]).toBeEnabled())
    for (const choice of choices.slice(0, 5)) await userEvent.click(choice)
    expect(choices[5]).toBeDisabled()
    const consent = screen.getByRole('checkbox', { name: /I want Meta to reassess/ })
    await userEvent.click(consent); expect(consent).toBeChecked()
    await userEvent.click(choices[0]); expect(consent).not.toBeChecked(); expect(choices[5]).toBeEnabled()
  })
  it('retains an exact chosen selection and consent across unchanged provider-list refreshes', async () => {
    server(); const view = render(<AssessmentWorkspace {...props()} />)
    await chooseAndConsent()
    view.rerender(<AssessmentWorkspace {...props()} sources={papers()} />)
    expect(screen.getByRole('checkbox', { name: /I want Meta to reassess/ })).toBeChecked()
    view.rerender(<AssessmentWorkspace {...props()} sources={[]} />)
    expect(screen.getByRole('checkbox', { name: /I want Meta to reassess/ })).not.toBeChecked()
    expect(screen.getByRole('button', { name: 'Reassess selected evidence' })).toBeDisabled()
  })
  it('locks uncertain writes until owned history is explicitly reloaded, without retrying Meta', async () => {
    const { state, requests } = server(); state.lostWrite = true
    render(<AssessmentWorkspace {...props()} />)
    await chooseAndConsent(); await userEvent.click(screen.getByRole('button', { name: 'Reassess selected evidence' }))
    await screen.findByText(/last request’s outcome is unknown/)
    expect(screen.getByRole('button', { name: 'Reassess selected evidence' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Reload assessments' }))
    await screen.findByText('Meta reassessment · queued')
    expect(screen.queryByText(/last request’s outcome is unknown/)).not.toBeInTheDocument()
    expect(requests.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
  })
  it('refreshes the actual saved result at SSE completion and displays its cited passages', async () => {
    const { state } = server(), changed = vi.fn()
    render(<AssessmentWorkspace {...props()} onAssessmentChanged={changed} />)
    await chooseAndConsent(); await userEvent.click(screen.getByRole('button', { name: 'Reassess selected evidence' }))
    await screen.findByText('Meta reassessment · queued')
    state.saved = assessmentRecord(); state.jobs = [job({ status: 'succeeded', assessment_id: ASSESSMENT, updated_at: '2099-10-28T00:00:01Z' })]; state.currentHead = head({ revision: 1, last_assessment_id: ASSESSMENT })
    act(() => Stream.instances.at(-1)!.send('end'))
    const preview = await screen.findByRole('article', { name: 'Saved evidence reassessment' })
    expect(preview).toHaveTextContent('Finding unchanged')
    expect(preview).toHaveTextContent('Applied at head revision 1')
    expect(within(preview).getByRole('region', { name: 'Verified assessment passages' })).toHaveTextContent(NEW_QUOTE)
    expect(within(preview).getByRole('link', { name: 'Read assessment source passage 2' })).toHaveAttribute('href', `#assessment-passage-${ASSESSMENT}-1`)
    expect(changed).toHaveBeenCalledTimes(1)
  })
  it('keeps failed saved audits readable without applying a revised conclusion', async () => {
    const failed = assessmentRecord({ disposition: 'inconclusive', applied: false, payload: { ...assessmentRecord({ disposition: 'inconclusive' }).payload, result: { ...assessmentRecord({ disposition: 'inconclusive' }).payload.result, status: 'failed', reason: 'Synthetic model failure.', error_code: 'model_unavailable' } } })
    server({ initialJob: job({ status: 'failed', assessment_id: ASSESSMENT }), saved: failed })
    render(<AssessmentWorkspace {...props()} />)
    const preview = await screen.findByRole('article', { name: 'Saved evidence reassessment' })
    expect(preview).toHaveTextContent('Synthetic model failure.')
    expect(preview).toHaveTextContent('did not change the finding head')
    expect(screen.queryByRole('button', { name: 'Open saved revised finding' })).not.toBeInTheDocument()
  })
  it('allows restoring review of an unsupported current baseline but blocks historical source revisions', async () => {
    const { state } = server({ currentHead: head({ state: 'unsupported', revision: 1, last_assessment_id: ASSESSMENT, is_supported: false, is_historical: true, can_generate: false }) })
    render(<AssessmentWorkspace {...props()} />)
    await chooseAndConsent()
    expect(screen.getByRole('button', { name: 'Reassess selected evidence' })).toBeEnabled()
    state.currentHead = head({ current_result_id: CANDIDATE, current_run_id: RUN, revision: 2, last_assessment_id: ASSESSMENT, is_current: false, is_supported: false, is_historical: true, can_generate: false })
    await userEvent.click(screen.getByRole('button', { name: 'Refresh finding status' }))
    await screen.findByText(/This watch is linked to an earlier finding version/)
    expect(screen.getByRole('button', { name: 'Reassess selected evidence' })).toBeDisabled()
  })
  it('cancels through the existing owned run endpoint and keeps its actual saved status', async () => {
    const { requests } = server({ initialJob: job({ status: 'running' }) })
    render(<AssessmentWorkspace {...props()} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Cancel reassessment' }))
    await screen.findByText('Meta reassessment · cancel requested')
    expect(requests.mock.calls.filter(([path, init]) => path === `/api/v1/runs/${RUN}/cancel` && init?.method === 'POST')).toHaveLength(1)
    expect(screen.queryByRole('article', { name: 'Saved evidence reassessment' })).not.toBeInTheDocument()
  })
  it('clears saved private evidence and selection on browser expiry, aborting old activity', async () => {
    const { state } = server({ initialJob: job({ status: 'succeeded', assessment_id: ASSESSMENT }), saved: assessmentRecord(), currentHead: head({ revision: 1, last_assessment_id: ASSESSMENT }) }), expired = vi.fn()
    render(<AssessmentWorkspace {...props()} onSessionExpired={expired} />)
    await screen.findByRole('article', { name: 'Saved evidence reassessment' })
    state.authStatus = 401
    await userEvent.click(screen.getByRole('button', { name: 'Refresh finding status' }))
    await screen.findByText(/Private reassessments and source selections have been cleared/)
    expect(screen.queryByRole('article', { name: 'Saved evidence reassessment' })).not.toBeInTheDocument()
    expect(expired).toHaveBeenCalledTimes(1)
    expect(Stream.instances.every((stream) => stream.close.mock.calls.length > 0)).toBe(true)
  })
  it('labels an unapplied revised branch and provides no current-finding navigation for its candidate', () => {
    render(<AssessmentPreview record={assessmentRecord({ disposition: 'revised', applied: false, head_revision: 2 })} head={head({ revision: 2, last_assessment_id: ASSESSMENT })} onOpenFinding={vi.fn()} />)
    expect(screen.getByRole('article', { name: 'Saved evidence reassessment' })).toHaveTextContent('unapplied assessment branch')
    expect(screen.getByText('Proposed revision · unapplied')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Open saved revised finding' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open earlier finding version' })).toBeEnabled()
  })
})
