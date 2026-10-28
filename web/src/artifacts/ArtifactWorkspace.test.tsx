import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ArtifactWorkspace } from './ArtifactWorkspace'
import type { ArtifactJob, ArtifactKind, CitedText, FiniteConjecture, SavedArtifact } from './types'

const SOURCE = '11111111-1111-4111-8111-111111111111'
const SOURCE_RUN = '22222222-2222-4222-8222-222222222222'
const JOB = '33333333-3333-4333-8333-333333333333'
const ARTIFACT = '44444444-4444-4444-8444-444444444444'
const SECOND_JOB = '55555555-5555-4555-8555-555555555555'
const SECOND_ARTIFACT = '66666666-6666-4666-8666-666666666666'
const HASH = 'a'.repeat(64)
const DATE = '2026-10-28T00:00:00Z'

class FixtureEventSource extends EventTarget {
  static instances: FixtureEventSource[] = []
  close = vi.fn()
  readonly url: string
  constructor(url: string) { super(); this.url = url; FixtureEventSource.instances.push(this) }
  emit(type: string, value?: unknown, sequence = '') { this.dispatchEvent(value === undefined ? new Event(type) : new MessageEvent(type, { data: JSON.stringify(value), lastEventId: sequence })) }
}

function paragraph(text = 'A synthetic inference drawn from the original source passage.'): CitedText {
  return { text, label: 'postulated', citation_ordinals: [0] }
}
function encoding(): FiniteConjecture {
  return { variables: [{ name: 'n', lower: 0, upper: 1 }], assumptions: [], conclusion: { left: { op: 'variable', name: 'n' }, operator: 'eq', right: { op: 'constant', value: 0 } } }
}
function job(kind: ArtifactKind = 'formalise', id = JOB): ArtifactJob {
  return { id, mode: 'explore', task_kind: kind, goal: 'Synthetic source-backed generation', status: 'queued', created_at: DATE, updated_at: DATE, result_id: null, artifact_id: null, error_code: null, external_outcome_unknown: false }
}
function saved(kind: ArtifactKind = 'formalise', id = ARTIFACT, runId = JOB): SavedArtifact {
  const draft = kind === 'formalise' ? {
    kind: 'formalisation' as const, title: 'Synthetic conjecture from saved evidence', definitions: [{ symbol: 'n', meaning: paragraph('A synthetic finite integer variable.') }], assumptions: [paragraph('Assume the encoded finite domain is the intended model.')], conjecture: paragraph('Every value in the synthetic domain equals zero.'), proof_attempt: [paragraph('The synthetic argument has a gap that the check exposes.')], limitations: ['The encoded statement is synthetic and is not a scientific conclusion.'], finite_conjecture: encoding(),
  } : {
    kind: kind === 'write_paper' ? 'paper' as const : 'summary' as const, title: kind === 'write_paper' ? 'Synthetic source-backed research paper' : 'Synthetic plain-English summary', abstract: [paragraph('A concise synthetic overview, with its original passage cited.')], sections: (kind === 'write_paper' ? ['Background', 'Findings', 'Evidence', 'Limitations'] : ['The finding', 'What remains open']).map((heading) => ({ heading, paragraphs: [paragraph(`Synthetic ${heading.toLowerCase()} with explicit source binding.`)] })), limitations: ['These synthetic sources do not establish scientific truth.'],
  }
  return {
    id, run_id: runId, kind, status: 'ready', created_at: DATE, source_result_id: SOURCE, source_run_id: SOURCE_RUN, source_sha256: HASH, formalisation_id: kind === 'write_paper' ? ARTIFACT : null,
    passages: [{ ordinal: 0, source_id: 'fixture:source', title: 'An original synthetic source', url: 'https://example.org/synthetic-source', content_sha256: HASH, content_kind: 'abstract', quote: 'The original synthetic source describes a finite set of integer values.', supports: 'A synthetic premise, not independent scientific verification.', claim_id: null }],
    payload: { kind, status: 'ready', draft, checks: kind === 'formalise' ? [{ conjecture: encoding(), conjecture_sha256: HASH, status: 'counterexample_found', assignments_tested: 2, applicable_cases: 2, total_assignments: 2, witness: { n: 1 }, lhs: '1', rhs: '0', error_code: null, scope: 'finite_integer_domain', proof_status: 'not_a_general_proof' }] : [], events: [{ sequence: 1, kind: 'draft_completed', summary: 'Generated a source-bound synthetic draft.' }], usage: { model_calls: 1, input_tokens_reported: 10, output_tokens_reported: 40, calls_without_usage: 0 }, error_code: null, reason: null, validation_scope: 'citation_integrity_and_bounded_checks' },
    formalisation_checks: [], markdown: '# Exact saved synthetic export\n', markdown_sha256: HASH, payload_sha256: HASH, basis_sha256: HASH,
  }
}

function server(initial: ArtifactJob[] = [], initialArtifacts: SavedArtifact[] = []) {
  const state = { jobs: initial, artifacts: new Map(initialArtifacts.map((item) => [item.id, item])), historyStatus: 200, artifactStatus: 200, cancelStatus: 200 }
  const requests = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const path = input.toString()
    if (path === '/api/v1/publications/capabilities') return Response.json({ configured: false, publisher: null, destinations: [], can_prepare: false })
    if (path === '/api/v1/operator/session') return Response.json({ configured: false, authenticated: false, actor_id: null, display_name: null, expires_at: null, csrf_token: null })
    if (path.startsWith('/api/v1/publications?')) return Response.json({ manifests: [], next_cursor: null })
    if (path === '/api/v1/artifacts/jobs' && init?.method === 'POST') {
      const body = JSON.parse(init.body as string) as { kind: ArtifactKind }
      const created = job(body.kind, state.jobs.length ? SECOND_JOB : JOB)
      state.jobs = [created, ...state.jobs]
      return Response.json(created, { status: 201 })
    }
    if (path.startsWith('/api/v1/artifacts/jobs?')) return Response.json(state.historyStatus === 200 ? { jobs: state.jobs, next_cursor: null } : { detail: 'private-session-error' }, { status: state.historyStatus })
    const detail = /^\/api\/v1\/artifacts\/jobs\/([^/]+)$/.exec(path)
    if (detail) {
      const found = state.jobs.find((item) => item.id === detail[1])
      return Response.json(found ?? { detail: 'missing' }, { status: found ? 200 : 404 })
    }
    const artifact = /^\/api\/v1\/artifacts\/([^/]+)$/.exec(path)
    if (artifact) {
      const found = state.artifacts.get(artifact[1])
      return Response.json(state.artifactStatus === 200 ? found : { detail: 'private-artifact-error' }, { status: state.artifactStatus })
    }
    const cancel = /^\/api\/v1\/runs\/([^/]+)\/cancel$/.exec(path)
    if (cancel) {
      const found = state.jobs.find((item) => item.id === cancel[1])!
      if (state.cancelStatus !== 200) return Response.json({ detail: 'private-csrf-error' }, { status: state.cancelStatus })
      found.status = found.status === 'running' ? 'cancel_requested' : 'cancelled'
      found.updated_at = '2026-10-28T00:05:00Z'
      return Response.json(found)
    }
    throw new Error(`Unexpected fixture path: ${path}`)
  })
  vi.stubGlobal('fetch', requests)
  vi.stubGlobal('EventSource', FixtureEventSource)
  return { state, requests }
}

function renderWorkspace(changes: Partial<Parameters<typeof ArtifactWorkspace>[0]> = {}) {
  const expired = vi.fn()
  const busy = vi.fn()
  const props = { sourceResultId: SOURCE, sourceKind: 'bridge' as const, csrfToken: HASH, enabled: true, canGenerate: true, sourceCurrent: true, onSessionExpired: expired, onBusyChange: busy, ...changes }
  return { ...render(<ArtifactWorkspace {...props} />), expired, busy }
}

async function finish(state: ReturnType<typeof server>['state'], artifact: SavedArtifact) {
  state.artifacts.set(artifact.id, artifact)
  const current = state.jobs.find((item) => item.id === artifact.run_id)!
  current.status = 'succeeded'
  current.artifact_id = artifact.id
  current.updated_at = '2026-10-28T00:02:00Z'
  await waitFor(() => expect(FixtureEventSource.instances.some((stream) => stream.url.includes(artifact.run_id))).toBe(true))
  await act(async () => { FixtureEventSource.instances.findLast((stream) => stream.url.includes(artifact.run_id))!.emit('end') })
}

afterEach(() => { FixtureEventSource.instances = []; vi.unstubAllGlobals(); vi.useRealTimers() })

describe('source-bound Formalise and Write workspace', () => {
  it('blocks new generation from a stale finding even when model execution is available', async () => {
    const { requests } = server()
    renderWorkspace({ canGenerate: true, sourceCurrent: false })
    await screen.findByText('No conjecture or document has been generated from this finding yet.')
    expect(screen.getByRole('button', { name: /Formalise/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Write summary/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Write paper/ })).toBeDisabled()
    expect(screen.getByText(/New generation requires the current supported finding/)).toBeVisible()
    expect(requests.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })
  it('keeps generation disabled by default while allowing owned saved previews', async () => {
    const artifact = saved('write_summary')
    const complete = { ...job('write_summary'), status: 'succeeded' as const, artifact_id: artifact.id }
    const { requests } = server([complete], [artifact])
    renderWorkspace({ canGenerate: false })
    expect(await screen.findByRole('heading', { name: 'Synthetic plain-English summary' })).toBeVisible()
    for (const name of ['Formalise', 'Write summary', 'Write paper']) expect(screen.getByRole('button', { name: new RegExp(name) })).toBeDisabled()
    expect(screen.getByRole('link', { name: /Download saved Markdown/ })).toHaveAttribute('href', `/api/v1/artifacts/${ARTIFACT}/export`)
    expect(requests.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
  })

  it('queues one conjecture, inspects its exact sources and counterexample, then writes from its saved version', async () => {
    const { state, requests } = server()
    renderWorkspace()
    const button = await screen.findByRole('button', { name: /Formalise/ })
    await waitFor(() => expect(button).toBeEnabled())
    await userEvent.dblClick(button)
    await screen.findByRole('button', { name: 'Cancel this task' })
    const posts = () => requests.mock.calls.filter(([, init]) => init?.method === 'POST')
    expect(posts()).toHaveLength(1)
    expect(JSON.parse(posts()[0][1]?.body as string)).toEqual({ kind: 'formalise', source_result_id: SOURCE })
    expect(posts()[0][1]).toMatchObject({ credentials: 'same-origin', headers: { 'X-CSRF-Token': HASH } })
    await finish(state, saved())
    expect(await screen.findByRole('heading', { name: 'Synthetic conjecture from saved evidence' })).toBeVisible()
    expect(screen.getByText('Open conjecture · proof attempt unverified')).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Counterexample to the encoded statement' })).toBeVisible()
    const sources = screen.getByRole('list', { name: 'Document source passages' })
    expect(within(sources).getByText('The original synthetic source describes a finite set of integer values.')).toBeVisible()
    expect(within(sources).getByRole('link', { name: /An original synthetic source/ })).toHaveAttribute('href', 'https://example.org/synthetic-source')
    expect(screen.getAllByRole('link', { name: 'Read source passage 1' })[0]).toHaveAttribute('href', `#artifact-passage-${ARTIFACT}-0`)
    expect(screen.getByRole('checkbox', { name: /Include the saved conjecture in writing/ })).toBeChecked()
    await userEvent.click(screen.getByRole('button', { name: /Write paper/ }))
    await waitFor(() => expect(posts()).toHaveLength(2))
    expect(JSON.parse(posts()[1][1]?.body as string)).toEqual({ kind: 'write_paper', source_result_id: SOURCE, formalisation_id: ARTIFACT })
    await finish(state, saved('write_paper', SECOND_ARTIFACT, SECOND_JOB))
    expect(await screen.findByRole('heading', { name: 'Synthetic source-backed research paper' })).toBeVisible()
    for (const heading of ['Abstract', 'Background', 'Findings', 'Evidence', 'Limitations']) expect(screen.getByRole('heading', { name: heading })).toBeVisible()
    expect(screen.getByRole('link', { name: /Download saved Markdown/ })).toHaveAttribute('href', `/api/v1/artifacts/${SECOND_ARTIFACT}/export`)
    expect(screen.getByText(/This document incorporates a saved conjecture/)).toBeVisible()
    expect(requests.mock.calls.some(([path]) => path.toString() === '/api/v1/session')).toBe(false)
  })

  it('reloads an already saved artifact without generating another task', async () => {
    const artifact = saved('write_paper')
    const complete = { ...job('write_paper'), status: 'succeeded' as const, artifact_id: artifact.id }
    const { requests } = server([complete], [artifact])
    const first = renderWorkspace()
    await screen.findByRole('heading', { name: 'Synthetic source-backed research paper' })
    first.unmount()
    renderWorkspace()
    await screen.findByRole('heading', { name: 'Synthetic source-backed research paper' })
    expect(requests.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
  })

  it('allows summaries and papers for an answer, while Formalise requires a bridge', async () => {
    server()
    renderWorkspace({ sourceKind: 'answer' })
    await waitFor(() => expect(screen.getByRole('button', { name: /Write summary/ })).toBeEnabled())
    expect(screen.getByRole('button', { name: /Formalise/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Write paper/ })).toBeEnabled()
  })

  it('keeps an inherited counterexample visible in the paper instead of silently dropping it', async () => {
    const artifact = saved('write_paper')
    artifact.formalisation_checks = saved().payload.checks
    server([{ ...job('write_paper'), status: 'succeeded', artifact_id: artifact.id }], [artifact])
    renderWorkspace()
    expect(await screen.findByRole('heading', { name: 'Checks from the saved conjecture' })).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Counterexample to the encoded statement' })).toBeVisible()
    expect(screen.getByText(/recorded checks of the exact conjecture version/)).toBeVisible()
    expect(screen.getByText(/It is not a general proof/)).toBeVisible()
    expect(screen.getByRole('link', { name: /Download saved Markdown/ })).toBeVisible()
  })

  it('cancels an owned queued artifact using the shared CSRF boundary', async () => {
    const { requests } = server([job()])
    const { busy } = renderWorkspace()
    await userEvent.click(await screen.findByRole('button', { name: 'Cancel this task' }))
    expect(await screen.findByText('Conjecture · Cancelled')).toBeVisible()
    expect(requests.mock.calls.find(([path]) => path.toString().endsWith('/cancel'))?.[1]).toMatchObject({ credentials: 'same-origin', method: 'POST', headers: { 'X-CSRF-Token': HASH } })
    await waitFor(() => expect(busy).toHaveBeenLastCalledWith(false))
    expect(screen.getByRole('button', { name: /Write summary/ })).toBeEnabled()
  })

  it('does not turn a queued or insufficient outcome into a document preview', async () => {
    const artifact = saved()
    artifact.status = 'insufficient_evidence'
    artifact.payload = { ...artifact.payload, status: 'insufficient_evidence', draft: null, reason: 'The selected source does not support a precise mathematical statement.' }
    const complete = { ...job(), status: 'succeeded' as const, artifact_id: artifact.id }
    server([complete], [artifact])
    renderWorkspace()
    expect(await screen.findByRole('heading', { name: 'More evidence is needed' })).toBeVisible()
    expect(screen.queryByRole('article', { name: 'Saved document preview' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Download saved Markdown/ })).not.toBeInTheDocument()
  })

  it('clears saved preview and job history when authorization expires', async () => {
    const artifact = saved('write_summary')
    const complete = { ...job('write_summary'), status: 'succeeded' as const, artifact_id: artifact.id }
    const { state } = server([complete], [artifact])
    const { expired } = renderWorkspace()
    await screen.findByRole('heading', { name: 'Synthetic plain-English summary' })
    state.artifactStatus = 401
    await userEvent.click(screen.getByRole('button', { name: 'Reload saved work' }))
    await waitFor(() => expect(expired).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('heading', { name: 'Synthetic plain-English summary' })).not.toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Saved conjectures and documents' })).not.toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Your research session expired or changed')
    expect(screen.getByRole('alert')).not.toHaveTextContent('private-artifact-error')
  })

  it('refuses a saved artifact bound to another source finding', async () => {
    const artifact = saved('write_summary')
    artifact.source_result_id = SECOND_ARTIFACT
    const complete = { ...job('write_summary'), status: 'succeeded' as const, artifact_id: artifact.id }
    server([complete], [artifact])
    renderWorkspace()
    expect(await screen.findByRole('alert')).toHaveTextContent('The saved document could not be read')
    expect(screen.queryByRole('heading', { name: 'Synthetic plain-English summary' })).not.toBeInTheDocument()
  })

  it('renders model text as text rather than activating HTML or inventing source links', async () => {
    const artifact = saved('write_summary')
    if (artifact.payload.draft?.kind === 'summary') artifact.payload.draft.abstract[0].text = '<img src=x onerror="alert(1)"> This is literal model text.'
    server([{ ...job('write_summary'), status: 'succeeded', artifact_id: artifact.id }], [artifact])
    const { container } = renderWorkspace()
    expect(await screen.findByText('<img src=x onerror="alert(1)"> This is literal model text.')).toBeVisible()
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('script')).toBeNull()
  })

  it('retains cancellation when a stale working response arrives later', async () => {
    const active = { ...job(), status: 'running' as const }
    const { state } = server([active])
    renderWorkspace()
    await userEvent.click(await screen.findByRole('button', { name: 'Cancel this task' }))
    expect(await screen.findByText('Conjecture · Cancelling')).toBeVisible()
    state.jobs[0] = { ...state.jobs[0], status: 'running', updated_at: '2026-10-28T00:01:00Z' }
    await act(async () => FixtureEventSource.instances[0].emit('end'))
    await waitFor(() => expect(screen.getByText('Conjecture · Cancelling')).toBeVisible())
    expect(screen.queryByText('Conjecture · Working')).not.toBeInTheDocument()
  })

  it('reports a lost create response without automatically submitting again', async () => {
    const { requests } = server()
    const normalFetch = globalThis.fetch
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => init?.method === 'POST' ? Promise.reject(new TypeError('synthetic lost response')) : normalFetch(input, init)))
    renderWorkspace()
    await waitFor(() => expect(screen.getByRole('button', { name: /Write summary/ })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: /Write summary/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not confirm whether this task was queued')
    expect(screen.getByRole('button', { name: /Write summary/ })).toBeDisabled()
    expect(requests.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
    expect(vi.mocked(globalThis.fetch).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
    await userEvent.click(screen.getByRole('button', { name: 'Reload saved work' }))
    await waitFor(() => expect(screen.getByRole('button', { name: /Write summary/ })).toBeEnabled())
  })
})
