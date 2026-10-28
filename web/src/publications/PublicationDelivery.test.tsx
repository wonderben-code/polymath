import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PublicationDelivery } from './PublicationDelivery'
import type { PublicationManifest } from './client'
import type { PublicationJob, PublicationJobEvent } from './deliveryClient'
import { APPROVAL, ARTIFACT, BROWSER_CSRF, DATE, MANIFEST, manifestFixture, OPERATOR_CSRF, PUBLICATION_JOB, publicationJobFixture, REVIEW_HASH, SECOND_MANIFEST } from './test-fixtures'

function server(initial: PublicationJob | null = null) {
  const state = { job: initial, execution: true, listStatus: 200, mutationStatus: 200, loseCreate: false, events: [] as PublicationJobEvent[], detailOverride: null as null | ((signal: AbortSignal | null | undefined) => Promise<Response>) }
  const requests = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const path = input.toString()
    if (path === '/api/v1/publication-jobs/capabilities') return Response.json({ execution_available: state.execution, reason: state.execution ? null : 'publication_execution_disabled' })
    if (path.startsWith('/api/v1/publication-jobs?')) {
      const selected = new URLSearchParams(path.split('?')[1]).get('manifest_id')
      return Response.json(state.listStatus === 200 ? { jobs: state.job?.manifest_id === selected ? [state.job] : [], next_cursor: null } : { detail: 'research_session_required' }, { status: state.listStatus })
    }
    if (path === '/api/v1/publication-jobs' && init?.method === 'POST') {
      const body = JSON.parse(init.body as string) as { manifest_id: string; approval_id: string; expected_manifest_sha256: string }
      state.job = publicationJobFixture({ manifest_id: body.manifest_id, approval_id: body.approval_id, manifest_sha256: body.expected_manifest_sha256 })
      if (state.loseCreate) throw new TypeError('synthetic lost queue response')
      return Response.json(state.job, { status: 201 })
    }
    const events = /^\/api\/v1\/publication-jobs\/[^/]+\/events\?after=(\d+)$/.exec(path)
    if (events) {
      const after = Number(events[1])
      return Response.json({ events: state.events.filter((item) => item.sequence > after), next_cursor: state.events.at(-1)?.sequence ?? after, terminal: !state.job || !['queued', 'running'].includes(state.job.status) })
    }
    const detail = /^\/api\/v1\/publication-jobs\/([^/]+)$/.exec(path)
    if (detail) {
      if (state.detailOverride) return state.detailOverride(init?.signal)
      return Response.json(state.job ?? { detail: 'publication_job_not_found' }, { status: state.job ? 200 : 404 })
    }
    const action = /^\/api\/v1\/publication-jobs\/[^/]+\/(cancel|reconcile|resume)$/.exec(path)
    if (action) {
      if (state.mutationStatus !== 200) return Response.json({ detail: 'operator_session_required' }, { status: state.mutationStatus })
      if (action[1] === 'cancel') state.job = { ...state.job!, status: 'cancelled', can_cancel: false, updated_at: '2026-10-28T00:05:00Z' }
      if (action[1] === 'reconcile') state.job = { ...state.job!, status: 'queued', work_kind: 'reconcile', stage: 'reconcile', can_cancel: true, can_resume: false, can_reconcile: false, updated_at: '2026-10-28T00:05:00Z' }
      if (action[1] === 'resume') { const body = JSON.parse(init?.body as string) as { approval_id: string }; state.job = { ...state.job!, status: 'queued', work_kind: 'publish', stage: 'verify', approval_id: body.approval_id, can_resume: false, can_reconcile: false, can_cancel: true, updated_at: '2026-10-28T00:06:00Z' } }
      return Response.json(state.job)
    }
    throw new Error(`Unexpected delivery fixture path: ${path}`)
  })
  vi.stubGlobal('fetch', requests)
  return { state, requests }
}
function mount(changes: Partial<Parameters<typeof PublicationDelivery>[0]> = {}) {
  const onAuthorizationError = vi.fn()
  const props = { record: manifestFixture(true), csrfToken: BROWSER_CSRF, operatorCsrf: OPERATOR_CSRF, operatorAuthenticated: true, enabled: true, blocked: false, sourceCurrent: true, onAuthorizationError, ...changes }
  return { ...render(<PublicationDelivery {...props} />), onAuthorizationError, props }
}
const sandboxConsent = /I want to publish this exact approved version to the Zenodo sandbox/
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

describe('explicit exact-version publishing', () => {
  it('blocks fresh publishing of a stale finding but retains explicit read-only verification and local cancellation', async () => {
    const { requests } = server(publicationJobFixture({ status: 'interrupted', external_outcome_unknown: true, can_reconcile: true, can_cancel: true }))
    mount({ sourceCurrent: false })
    expect(await screen.findByRole('button', { name: 'Verify remote status' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Cancel publication job' })).toBeEnabled()
    await userEvent.click(screen.getByRole('button', { name: 'Verify remote status' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Remote verification queued')
    expect(requests.mock.calls.filter(([, init]) => init?.method === 'POST').map(([path]) => path)).toEqual([`/api/v1/publication-jobs/${PUBLICATION_JOB}/reconcile`])
  })

  it('clears a checked publication decision when the exact finding basis becomes historical', async () => {
    const { requests } = server()
    const view = mount()
    const consent = await screen.findByRole('checkbox', { name: sandboxConsent })
    await userEvent.click(consent)
    expect(screen.getByRole('button', { name: 'Publish to sandbox' })).toBeEnabled()
    view.rerender(<PublicationDelivery {...view.props} sourceCurrent={false} />)
    expect(consent).not.toBeChecked()
    expect(consent).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Publish to sandbox' })).toBeDisabled()
    expect(requests.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })

  it('keeps publishing disabled by default and never enqueues on approval or load', async () => {
    const { state, requests } = server()
    state.execution = false
    mount()
    expect(await screen.findByText(/Publishing execution is disabled or unavailable/)).toBeVisible()
    expect(screen.getByRole('checkbox', { name: sandboxConsent })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Publish to sandbox' })).toBeDisabled()
    expect(requests.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
  })

  it('requires a fresh checked publishing decision and sends one exact privileged queue request', async () => {
    const { requests } = server()
    mount()
    const consent = await screen.findByRole('checkbox', { name: sandboxConsent })
    expect(consent).not.toBeChecked()
    expect(screen.getByRole('button', { name: 'Publish to sandbox' })).toBeDisabled()
    await userEvent.click(consent)
    await userEvent.dblClick(screen.getByRole('button', { name: 'Publish to sandbox' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Publication queued')
    const posts = requests.mock.calls.filter(([, init]) => init?.method === 'POST')
    expect(posts).toHaveLength(1)
    expect(posts[0][0]).toBe('/api/v1/publication-jobs')
    expect(JSON.parse(posts[0][1]?.body as string)).toEqual({ manifest_id: MANIFEST, approval_id: APPROVAL, expected_manifest_sha256: REVIEW_HASH })
    expect(posts[0][1]?.headers).toMatchObject({ 'X-CSRF-Token': BROWSER_CSRF, 'X-Operator-CSRF-Token': OPERATOR_CSRF })
    expect(screen.queryByRole('button', { name: 'Publish to sandbox' })).not.toBeInTheDocument()
    expect(requests.mock.calls.every(([path]) => path.toString().startsWith('/api/v1/'))).toBe(true)
  })

  it('makes production publication a distinct explicit public-record decision', async () => {
    server()
    const record = manifestFixture(true)
    record.manifest.destination = 'production'
    mount({ record })
    const consent = await screen.findByRole('checkbox', { name: /publicly on Zenodo production/ })
    expect(consent).not.toBeChecked()
    expect(screen.getByText('This creates a public production record with a DOI.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Publish public record' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Publish to sandbox' })).not.toBeInTheDocument()
  })

  it('offers read-only verification for an unknown outcome, then requires a new explicit continuation', async () => {
    vi.useFakeTimers()
    const uncertain = publicationJobFixture({ status: 'interrupted', stage: 'publish', external_outcome_unknown: true, can_reconcile: true, can_resume: true, can_cancel: false })
    const { state, requests } = server(uncertain)
    const record = manifestFixture(true)
    record.approval!.id = ARTIFACT
    mount({ record })
    await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Verify remote status' })).toBeEnabled())
    expect(screen.queryByRole('button', { name: 'Continue this publication' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Publish to sandbox' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Verify remote status' }))
    await vi.waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Remote verification queued'))
    expect(requests.mock.calls.filter(([, init]) => init?.method === 'POST').map(([path]) => path)).toEqual([`/api/v1/publication-jobs/${PUBLICATION_JOB}/reconcile`])
    state.job = { ...state.job!, status: 'interrupted', stage: 'verify', external_outcome_unknown: false, can_reconcile: true, can_resume: true, can_cancel: false, updated_at: '2026-10-28T00:05:30Z' }
    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Continue this publication' })).toBeDisabled())
    fireEvent.click(screen.getByRole('checkbox', { name: /I want to continue this existing publication job/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Continue this publication' }))
    await vi.waitFor(() => expect(requests.mock.calls.filter(([path]) => path.toString().endsWith('/resume'))).toHaveLength(1))
    const resumed = requests.mock.calls.find(([path]) => path.toString().endsWith('/resume'))!
    expect(JSON.parse(resumed[1]?.body as string)).toEqual({ approval_id: ARTIFACT, expected_manifest_sha256: REVIEW_HASH })
    expect(requests.mock.calls.some(([path, init]) => path === '/api/v1/publication-jobs' && init?.method === 'POST')).toBe(false)
  })

  it('does not display a reserved DOI or a raw publication report as a verified result', async () => {
    server(publicationJobFixture({ status: 'interrupted', stage: 'publish', publication_reported: true, can_reconcile: true, can_resume: true, can_cancel: false, doi: '10.5072/zenodo.123', record_url: 'https://sandbox.zenodo.org/records/123' }))
    mount()
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Remote publication reported · verification required'))
    expect(screen.queryByRole('link', { name: /DOI/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Open verified/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Continue this publication' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Verify remote status' })).toBeEnabled()
  })

  it('polls actual saved progress and exposes verified DOI only after exact remote verification', async () => {
    vi.useFakeTimers()
    const { state, requests } = server(publicationJobFixture())
    state.events = [{ sequence: 1, kind: 'queued', summary: 'Publication queued.', created_at: DATE }]
    mount()
    await vi.waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Publication queued'))
    state.job = { ...state.job!, status: 'running', stage: 'upload', updated_at: '2026-10-28T00:01:00Z' }
    state.events.push({ sequence: 2, kind: 'upload_verified', summary: 'Verified approved file bytes.', created_at: '2026-10-28T00:01:00Z' })
    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    await vi.waitFor(() => expect(screen.getByText('Uploading the exact saved document')).toBeVisible())
    expect(screen.queryByRole('link', { name: /DOI/ })).not.toBeInTheDocument()
    state.job = { ...state.job!, status: 'published', stage: 'complete', verified: true, doi: '10.5072/zenodo.987654', record_url: 'https://sandbox.zenodo.org/records/987654', can_cancel: false, updated_at: '2026-10-28T00:02:00Z' }
    state.events.push({ sequence: 3, kind: 'publication_verified', summary: 'Verified published record and exact file.', created_at: '2026-10-28T00:02:00Z' })
    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    await vi.waitFor(() => expect(screen.getByRole('link', { name: /Open verified sandbox record/ })).toBeVisible())
    expect(screen.getByRole('status')).toHaveTextContent('Verified sandbox publication · test record')
    expect(screen.getByRole('link', { name: /Sandbox DOI/ })).toHaveAttribute('href', 'https://doi.org/10.5072/zenodo.987654')
    const activity = screen.getByRole('list', { name: 'Recorded publication activity', hidden: true })
    expect(within(activity).getAllByRole('listitem', { hidden: true })).toHaveLength(3)
    const reads = requests.mock.calls.length
    await act(async () => { await vi.advanceTimersByTimeAsync(30000) })
    expect(requests.mock.calls).toHaveLength(reads)
  })

  it('gives verified remote publication priority over a late local cancelled status', async () => {
    server(publicationJobFixture({ status: 'cancelled', verified: true, doi: '10.5072/zenodo.900', record_url: 'https://sandbox.zenodo.org/records/900', can_cancel: false }))
    mount({ operatorAuthenticated: false, operatorCsrf: null })
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Verified sandbox publication · test record'))
    expect(screen.getByRole('link', { name: /Open verified sandbox record/ })).toBeVisible()
    expect(screen.queryByText('Publication job cancelled')).not.toBeInTheDocument()
    expect(screen.getByText(/Published records remain available/)).toBeVisible()
  })

  it('keeps ordinary owned status and verified records readable after publisher logout', async () => {
    const { state, requests } = server(publicationJobFixture({ status: 'published', verified: true, doi: '10.5072/zenodo.900', record_url: 'https://sandbox.zenodo.org/records/900', can_cancel: false }))
    state.execution = false
    mount({ operatorAuthenticated: false, operatorCsrf: null })
    expect(await screen.findByRole('link', { name: /Open verified sandbox record/ })).toBeVisible()
    expect(requests.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
  })

  it('refreshes server-owned action permissions after publisher login without automatically continuing', async () => {
    const { state, requests } = server(publicationJobFixture({ status: 'interrupted', can_reconcile: false, can_resume: false, can_cancel: false }))
    const view = mount({ operatorAuthenticated: false, operatorCsrf: null })
    await waitFor(() => expect(screen.getByRole('region', { name: 'Saved publication job' })).toBeVisible())
    expect(screen.queryByRole('button', { name: 'Verify remote status' })).not.toBeInTheDocument()
    state.job = { ...state.job!, can_reconcile: true }
    view.rerender(<PublicationDelivery {...view.props} operatorAuthenticated operatorCsrf={OPERATOR_CSRF} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Verify remote status' })).toBeEnabled())
    expect(requests.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
  })

  it('blocks duplicate enqueue after an unknown POST response and recovers through owned status reload', async () => {
    const { state, requests } = server()
    state.loseCreate = true
    mount()
    await userEvent.click(await screen.findByRole('checkbox', { name: sandboxConsent }))
    await userEvent.click(screen.getByRole('button', { name: 'Publish to sandbox' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('The publication action could not be confirmed')
    expect(screen.getByRole('button', { name: 'Publish to sandbox' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Reload publication status' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Publication queued')
    expect(screen.queryByRole('button', { name: 'Publish to sandbox' })).not.toBeInTheDocument()
    expect(requests.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
  })

  it('requires renewed exact approval before a server-allowed continuation', async () => {
    server(publicationJobFixture({ status: 'interrupted', can_resume: true, can_cancel: false }))
    const record = manifestFixture(true)
    record.approval!.expires_at = '2026-01-01T00:00:00Z'
    mount({ record })
    expect(await screen.findByRole('button', { name: 'Continue this publication' })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: /I want to continue/ })).toBeDisabled()
    expect(screen.getByText(/A current approval of this exact preview is required/)).toBeVisible()
  })

  it('permits local cancellation when execution is disabled without claiming to undo a remote publication', async () => {
    const { state, requests } = server(publicationJobFixture({ status: 'running', stage: 'publish' }))
    state.execution = false
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Cancel publication job' }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Publication job cancelled'))
    expect(screen.getByText(/A request already granted can still complete remotely/)).toBeVisible()
    expect(requests.mock.calls.find(([path]) => path.toString().endsWith('/cancel'))?.[1]?.headers).toMatchObject({ 'X-CSRF-Token': BROWSER_CSRF, 'X-Operator-CSRF-Token': OPERATOR_CSRF })
  })

  it('stops future steps for an interrupted unknown job while preserving verification and hiding duplicate Publish', async () => {
    const { requests } = server(publicationJobFixture({ status: 'interrupted', stage: 'publish', external_outcome_unknown: true, can_cancel: true, can_reconcile: true }))
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Cancel publication job' }))
    await waitFor(() => expect(screen.getByText(/A request already granted can still complete remotely/)).toBeVisible())
    expect(screen.getByRole('status')).toHaveTextContent('Remote outcome needs verification')
    expect(screen.getByRole('button', { name: 'Verify remote status' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Publish to sandbox' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /DOI/ })).not.toBeInTheDocument()
    expect(requests.mock.calls.filter(([, init]) => init?.method === 'POST').map(([path]) => path)).toEqual([`/api/v1/publication-jobs/${PUBLICATION_JOB}/cancel`])
  })

  it('clears publisher privileges on expiry while retaining ordinary saved job status', async () => {
    const { state } = server(publicationJobFixture({ status: 'interrupted', can_reconcile: true, can_cancel: false }))
    state.mutationStatus = 401
    const { onAuthorizationError } = mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Verify remote status' }))
    await waitFor(() => expect(onAuthorizationError).toHaveBeenCalledWith(expect.objectContaining({ code: 'operator_expired' })))
    expect(screen.getByRole('region', { name: 'Saved publication job' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Verify remote status' })).toBeDisabled()
  })

  it('clears all saved delivery data when the browser ownership session expires', async () => {
    const { state } = server(publicationJobFixture({ status: 'published', verified: true, doi: '10.5072/zenodo.900', record_url: 'https://sandbox.zenodo.org/records/900', can_cancel: false }))
    const { onAuthorizationError } = mount()
    await screen.findByRole('link', { name: /Open verified sandbox record/ })
    state.listStatus = 401
    await userEvent.click(screen.getByRole('button', { name: 'Reload publication status' }))
    await waitFor(() => expect(onAuthorizationError).toHaveBeenCalledWith(expect.objectContaining({ code: 'browser_expired' })))
    expect(screen.queryByRole('link', { name: /Open verified/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Saved publication job' })).not.toBeInTheDocument()
  })

  it('aborts old polling and ignores a late DOI response after the selected manifest changes', async () => {
    let completeOld!: (response: Response) => void
    let oldSignal: AbortSignal | null | undefined
    const { state } = server(publicationJobFixture())
    state.detailOverride = (signal) => { oldSignal = signal; return new Promise((resolve) => { completeOld = resolve }) }
    const { rerender, props } = mount()
    await waitFor(() => expect(completeOld).toBeTypeOf('function'))
    const replacement: PublicationManifest = { ...manifestFixture(true), id: SECOND_MANIFEST, manifest_sha256: 'e'.repeat(64) }
    rerender(<PublicationDelivery {...props} record={replacement} />)
    await screen.findByText('No publication job has been started for this exact preview.')
    expect(oldSignal?.aborted).toBe(true)
    await act(async () => completeOld(Response.json(publicationJobFixture({ status: 'published', verified: true, doi: '10.5072/zenodo.900', record_url: 'https://sandbox.zenodo.org/records/900' }))))
    expect(screen.queryByRole('link', { name: /DOI/ })).not.toBeInTheDocument()
  })
})
