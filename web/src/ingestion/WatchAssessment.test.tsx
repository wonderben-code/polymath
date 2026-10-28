import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { WatchWorkspace } from './WatchWorkspace'
import { capabilities as collectionCapabilities, watch } from './fixtures.test-support'
import type { WatchSource, WatchSourceDetail } from './client'
import { ASSESSMENT, capabilities as assessmentCapabilities, DATE, HASH, head, job, LEFT_QUOTE, LEFT_RETRIEVAL, LEFT_VERSION, NEW_QUOTE, observedSources, record, RETRIEVAL, ROOT, RUN, VERSION, WATCH } from '../assessments/fixtures.test-support'
import type { AssessmentJob } from '../assessments/types'

const COLLECTION_RUN = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd'
const HISTORY = `/api/v1/assessments/heads/${ROOT}/history`
class Stream extends EventTarget {
  static instances: Stream[] = []
  close = vi.fn()
  constructor(readonly url: string) { super(); Stream.instances.push(this) }
  send(kind: string) { this.dispatchEvent(new Event(kind)) }
}
function firstPage(): WatchSource[] {
  return Array.from({ length: 20 }, (_, index) => ({ id: index === 0 ? LEFT_VERSION : `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`, source_id: index === 0 ? 'fixture:left' : `fixture:assessed-${index}`, title: index === 0 ? 'Synthetic left source' : `Synthetic previously assessed source ${index}`, url: index === 0 ? 'https://example.org/synthetic-left' : `https://example.org/synthetic-assessed-${index}`, content_kind: 'abstract', content_sha256: HASH, first_seen_at: DATE, run_id: COLLECTION_RUN, assessment_status: index === 0 ? 'already_cited' : 'assessed', assessment_root_result_id: ROOT, assessment_history_url: HISTORY }))
}
function pendingSource(complete: boolean): WatchSource { const source = observedSources()[2]; return { id: VERSION, source_id: source.source_id, title: source.title, url: source.url, content_kind: source.content_kind, content_sha256: source.content_sha256, first_seen_at: DATE, run_id: COLLECTION_RUN, assessment_status: complete ? 'assessed' : 'unassessed', assessment_root_result_id: ROOT, assessment_history_url: HISTORY } }
function sourceDetail(id: string, complete: boolean): WatchSourceDetail {
  const source = id === VERSION ? pendingSource(complete) : firstPage()[0]
  return { id: source.id, version_id: source.id, retrieval_id: id === VERSION ? RETRIEVAL : LEFT_RETRIEVAL, watch_id: WATCH, run_id: COLLECTION_RUN, content_sha256: source.content_sha256, metadata_sha256: HASH, assessment_status: source.assessment_status!, assessment_root_result_id: ROOT, assessment_history_url: HISTORY, document: { source_id: source.source_id, title: source.title, url: source.url, text: id === VERSION ? NEW_QUOTE : LEFT_QUOTE, content_kind: source.content_kind, retrieved_at: DATE, extraction_method: 'synthetic_fixture' } }
}
function server() {
  const savedWatch = watch({ id: WATCH, source_result_id: ROOT, source_count: 21 })
  const state = { complete: false, jobs: [] as AssessmentJob[] }
  const cursor = firstPage().at(-1)!.id
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const path = input.toString(), parsed = new URL(path, 'https://example.org')
    if (path === '/api/v1/knowledge/watches/capabilities') return Response.json(collectionCapabilities(false))
    if (path.startsWith('/api/v1/knowledge/watches?')) return Response.json({ watches: [savedWatch], next_cursor: null })
    if (path === `/api/v1/knowledge/watches/${WATCH}`) return Response.json(savedWatch)
    if (parsed.pathname === `/api/v1/knowledge/watches/${WATCH}/sources`) return Response.json(parsed.searchParams.has('before') ? { sources: [pendingSource(state.complete)], next_cursor: null } : { sources: firstPage(), next_cursor: cursor })
    if (path.startsWith(`/api/v1/knowledge/watches/${WATCH}/ticks?`)) return Response.json({ ticks: [], next_cursor: null })
    if (path === `/api/v1/knowledge/watches/${WATCH}/sources/${VERSION}`) return Response.json(sourceDetail(VERSION, state.complete))
    if (path === `/api/v1/knowledge/watches/${WATCH}/sources/${LEFT_VERSION}`) return Response.json(sourceDetail(LEFT_VERSION, state.complete))
    if (path === '/api/v1/assessments/capabilities') return Response.json(assessmentCapabilities(true))
    if (path === `/api/v1/assessments/heads/${ROOT}`) return Response.json(head(state.complete ? { revision: 1, last_assessment_id: ASSESSMENT } : {}))
    if (path.startsWith(`${HISTORY}?`)) return Response.json({ assessments: state.complete ? [record()] : [], next_cursor: null })
    if (path.startsWith('/api/v1/assessments/jobs?')) return Response.json({ jobs: state.jobs, next_cursor: null })
    if (path === '/api/v1/assessments/jobs' && init?.method === 'POST') { state.jobs = [job(JSON.parse(String(init.body)))]; return Response.json(state.jobs[0], { status: 201 }) }
    if (path === `/api/v1/assessments/jobs/${RUN}`) return Response.json(state.jobs[0])
    if (path === `/api/v1/assessments/${ASSESSMENT}`) return Response.json(record())
    throw new Error('Unexpected synthetic watch/assessment fixture request')
  })
  Stream.instances = []; vi.stubGlobal('fetch', fetch); vi.stubGlobal('EventSource', Stream)
  return { state, fetch }
}
afterEach(() => { vi.unstubAllGlobals(); Stream.instances = [] })

describe('saved watch sources entering an explicit Meta reassessment', () => {
  it('refreshes a consumed older-page version and its open inspection immediately after an applied receipt', async () => {
    const { state, fetch } = server(), applied = vi.fn(), expired = vi.fn()
    render(<WatchWorkspace enabled csrfToken={HASH} canReassess onOpenFinding={vi.fn()} onSessionExpired={expired} onAssessmentApplied={applied} />)
    await userEvent.click(await screen.findByRole('button', { name: /Synthetic watch/ }))
    await screen.findByRole('region', { name: 'Selected literature watch' })
    expect(screen.getByRole('button', { name: 'Check providers now' })).toBeDisabled()
    expect(screen.getByText(/Free-provider reading is disabled/)).toBeVisible()
    expect(screen.queryByRole('checkbox', { name: 'Reassess with source: Synthetic left source' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Read saved source: Synthetic left source' }))
    const original = await screen.findByRole('region', { name: 'Saved source text' })
    expect(original).toHaveTextContent('Already cited in the finding')
    expect(original).toHaveTextContent(LEFT_QUOTE)
    await userEvent.click(screen.getByRole('button', { name: 'Load older sources' }))
    const choice = await screen.findByRole('checkbox', { name: 'Reassess with source: Synthetic new right source version' })
    await waitFor(() => expect(choice).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: 'Read saved source: Synthetic new right source version' }))
    expect(await screen.findByRole('region', { name: 'Saved source text' })).toHaveTextContent('Awaiting model assessment')
    expect(screen.getByRole('region', { name: 'Saved source text' })).toHaveTextContent(NEW_QUOTE)
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
    await userEvent.click(choice)
    await userEvent.click(screen.getByRole('checkbox', { name: /I want Meta to reassess this exact finding/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Reassess selected evidence' }))
    await screen.findByText('Meta reassessment · queued')
    const posts = fetch.mock.calls.filter(([, init]) => init?.method === 'POST')
    expect(posts).toHaveLength(1)
    expect(posts[0]).toEqual(['/api/v1/assessments/jobs', expect.objectContaining({ body: JSON.stringify({ source_result_id: ROOT, watch_id: WATCH, new_source_version_ids: [VERSION], expected_head_revision: 0 }), headers: expect.objectContaining({ 'X-CSRF-Token': HASH }) })])
    await waitFor(() => expect(Stream.instances.some((stream) => stream.url === `/api/v1/runs/${RUN}/events?after=0`)).toBe(true))
    const beforeCompletion = fetch.mock.calls.length
    state.complete = true; state.jobs = [job({ status: 'succeeded', assessment_id: ASSESSMENT, updated_at: '2099-10-28T00:00:01Z' })]
    act(() => Stream.instances.at(-1)!.send('end'))
    expect(await screen.findByRole('article', { name: 'Saved evidence reassessment' })).toHaveTextContent('Applied at head revision 1')
    await waitFor(() => expect(screen.getByRole('region', { name: 'Saved source text' })).toHaveTextContent('Included in an applied model assessment'))
    const sourceCards = screen.getByRole('region', { name: 'Watch source versions' })
    const newCard = within(sourceCards).getByRole('heading', { name: 'Synthetic new right source version' }).closest('li')!
    expect(newCard).toHaveTextContent('Included in an applied model assessment')
    expect(screen.queryByRole('checkbox', { name: 'Reassess with source: Synthetic new right source version' })).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: 'Reassess with source: Synthetic left source' })).not.toBeInTheDocument()
    expect(applied).toHaveBeenCalledTimes(1)
    const refreshes = fetch.mock.calls.slice(beforeCompletion).filter(([path]) => /\/sources\/[a-f\d-]{36}$/.test(path.toString()))
    expect(refreshes).toHaveLength(1)
    expect(refreshes[0][0]).toBe(`/api/v1/knowledge/watches/${WATCH}/sources/${VERSION}`)
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
    expect(expired).not.toHaveBeenCalled()
  })
})
