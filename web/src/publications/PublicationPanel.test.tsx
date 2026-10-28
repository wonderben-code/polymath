import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PublicationPanel } from './PublicationPanel'
import type { PublicationManifest, PublicationMetadata } from './client'
import { APPROVAL, artifactFixture, BROWSER_CSRF, capabilityFixture, manifestFixture, OPERATOR_CSRF, operatorFixture, REVIEW_HASH, SECOND_MANIFEST } from './test-fixtures'

function server({ configured = true, authenticated = false, records = [] as PublicationManifest[] } = {}) {
  const state = { capabilities: capabilityFixture(configured), operator: operatorFixture(authenticated, configured), records, historyStatus: 200, loginStatus: 200, approveStatus: 200, loseApprovalResponse: false }
  const requests = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const path = input.toString()
    if (path === '/api/v1/publication-jobs/capabilities') return Response.json({ execution_available: false, reason: 'publication_execution_disabled' })
    if (path.startsWith('/api/v1/publication-jobs?')) return Response.json({ jobs: [], next_cursor: null })
    if (path === '/api/v1/publications/capabilities') return Response.json(state.capabilities)
    if (path === '/api/v1/operator/session' && init?.method === 'POST') {
      if (state.loginStatus !== 200) return Response.json({ detail: 'invalid_operator_credential' }, { status: state.loginStatus })
      state.operator = operatorFixture(true)
      return Response.json(state.operator)
    }
    if (path === '/api/v1/operator/session') return Response.json(state.operator)
    if (path === '/api/v1/operator/logout') { state.operator = operatorFixture(); return Response.json(state.operator) }
    if (path.startsWith('/api/v1/publications?')) return Response.json(state.historyStatus === 200 ? { manifests: state.records, next_cursor: null } : { detail: 'research_session_required' }, { status: state.historyStatus })
    if (path === '/api/v1/publications' && init?.method === 'POST') {
      const body = JSON.parse(init.body as string) as { metadata: PublicationMetadata; destination: 'sandbox' | 'production' }
      const record = manifestFixture()
      if (state.records.length) { record.id = SECOND_MANIFEST; record.manifest_sha256 = 'e'.repeat(64) }
      record.manifest.metadata = body.metadata
      record.manifest.destination = body.destination
      state.records = [record, ...state.records]
      return Response.json(record, { status: 201 })
    }
    const approve = /^\/api\/v1\/publications\/([^/]+)\/approve$/.exec(path)
    if (approve) {
      if (state.approveStatus !== 200) return Response.json({ detail: state.approveStatus === 401 ? 'operator_session_required' : 'operator_csrf_check_failed' }, { status: state.approveStatus })
      const record = state.records.find((item) => item.id === approve[1])!
      record.approval = { id: APPROVAL, manifest_id: record.id, manifest_sha256: record.manifest_sha256, actor_id: 'synthetic.owner', approved_at: new Date().toISOString(), expires_at: '2100-01-01T00:00:00Z', revoked_at: null, valid: true }
      if (state.loseApprovalResponse) throw new TypeError('synthetic response loss after recorded approval')
      return Response.json(record)
    }
    if (path === `/api/v1/publications/approvals/${APPROVAL}/revoke`) {
      const record = state.records.find((item) => item.approval?.id === APPROVAL)!
      record.approval = { ...record.approval!, revoked_at: new Date().toISOString(), valid: false }
      return Response.json(record)
    }
    throw new Error(`Unexpected publication fixture path: ${path}`)
  })
  vi.stubGlobal('fetch', requests)
  return { state, requests }
}

function mount() {
  const expired = vi.fn()
  const result = render(<PublicationPanel artifact={artifactFixture()} csrfToken={BROWSER_CSRF} enabled sourceCurrent onSessionExpired={expired} />)
  return { ...result, expired }
}
async function fillCreator() { await userEvent.type(await screen.findByRole('textbox', { name: 'Creator 1 name' }), 'Synthetic Creator') }
async function prepare() {
  await fillCreator()
  const button = screen.getByRole('button', { name: 'Prepare publication preview' })
  await waitFor(() => expect(button).toBeEnabled())
  await userEvent.click(button)
  return screen.findByRole('region', { name: 'Exact publication preview' })
}
async function unlock() {
  await userEvent.type(await screen.findByLabelText('Publisher access code'), 'synthetic-ephemeral-owner-code')
  await userEvent.click(screen.getByRole('button', { name: 'Unlock publisher approval' }))
  await screen.findByRole('button', { name: 'Lock publisher access' })
}
const consentName = /I approve this exact document, metadata, publisher, and destination/
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers() })

describe('human approval of exact saved publication versions', () => {
  it('clears fresh approval consent and blocks stale-basis preparation while preserving saved review and revoke access', async () => {
    const { requests } = server({ authenticated: true, records: [manifestFixture()] })
    const expired = vi.fn(), artifact = artifactFixture()
    const { rerender } = render(<PublicationPanel artifact={artifact} csrfToken={BROWSER_CSRF} enabled sourceCurrent onSessionExpired={expired} />)
    await userEvent.click(await screen.findByRole('checkbox', { name: consentName }))
    expect(screen.getByRole('button', { name: 'Approve this exact version' })).toBeEnabled()
    rerender(<PublicationPanel artifact={artifact} csrfToken={BROWSER_CSRF} enabled sourceCurrent={false} onSessionExpired={expired} />)
    expect(screen.getByRole('checkbox', { name: consentName })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: consentName })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Approve this exact version' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Prepare publication preview' })).toBeDisabled()
    expect(screen.getByRole('region', { name: 'Exact publication preview' })).toBeVisible()
    expect(screen.getByRole('alert')).toHaveTextContent('historical, unsupported, or awaiting a current-version check')
    expect(requests.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })

  it('keeps default preparation disabled while displaying a previously saved owned preview', async () => {
    const { requests } = server({ configured: false, records: [manifestFixture(true)] })
    mount()
    const preview = await screen.findByRole('region', { name: 'Exact publication preview' })
    expect(within(preview).getByText('Approval recorded', { exact: true })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Prepare publication preview' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Approve this exact version' })).toBeDisabled()
    expect(screen.getByText(/Publication preparation is not configured yet/)).toBeVisible()
    expect(requests.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
  })

  it('prepares an exact sandbox preview with ordinary browser authorization but cannot approve it', async () => {
    const { requests } = server()
    mount()
    const preview = await prepare()
    expect(within(preview).getByText('Zenodo sandbox · test record', { exact: true })).toBeVisible()
    expect(within(preview).getByText('Synthetic Publisher', { exact: true })).toBeVisible()
    expect(within(preview).getByRole('link', { name: /polymath-/ })).toHaveAttribute('href', `/api/v1/artifacts/${artifactFixture().id}/export`)
    expect(screen.getByRole('checkbox', { name: consentName })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Approve this exact version' })).toBeDisabled()
    const post = requests.mock.calls.find(([path, init]) => path === '/api/v1/publications' && init?.method === 'POST')!
    expect(post[1]?.headers).toMatchObject({ 'X-CSRF-Token': BROWSER_CSRF })
    expect(post[1]?.headers).not.toHaveProperty('X-Operator-CSRF-Token')
    expect(JSON.parse(post[1]?.body as string)).toMatchObject({ artifact_id: artifactFixture().id, destination: 'sandbox', metadata: { creators: [{ name: 'Synthetic Creator' }] } })
    expect(requests.mock.calls.some(([path]) => path.toString().includes('/approve'))).toBe(false)
  })

  it('erases the publisher credential and requires a separate explicit checked approval after login', async () => {
    const { requests } = server()
    const storage = vi.spyOn(Storage.prototype, 'setItem')
    mount()
    await prepare()
    await unlock()
    expect(screen.queryByLabelText('Publisher access code')).not.toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: consentName })).not.toBeChecked()
    expect(screen.getByRole('button', { name: 'Approve this exact version' })).toBeDisabled()
    expect(requests.mock.calls.filter(([path]) => path.toString().includes('/approve'))).toHaveLength(0)
    await userEvent.click(screen.getByRole('checkbox', { name: consentName }))
    await userEvent.dblClick(screen.getByRole('button', { name: 'Approve this exact version' }))
    const preview = await screen.findByRole('region', { name: 'Exact publication preview' })
    await waitFor(() => expect(within(preview).getByText('Approval recorded', { exact: true })).toBeVisible())
    const approvals = requests.mock.calls.filter(([path]) => path.toString().endsWith('/approve'))
    expect(approvals).toHaveLength(1)
    expect(approvals[0][1]?.headers).toMatchObject({ 'X-CSRF-Token': BROWSER_CSRF, 'X-Operator-CSRF-Token': OPERATOR_CSRF })
    expect(JSON.parse(approvals[0][1]?.body as string)).toEqual({ expected_manifest_sha256: REVIEW_HASH })
    expect(screen.getByText('Approval records a human decision. Publishing requires the separate action below.')).toBeVisible()
    expect(storage).not.toHaveBeenCalled()
    expect(requests.mock.calls.some(([path]) => path.toString().includes('/publish'))).toBe(false)
  })

  it('drops stale preview and consent when metadata changes, then requires review of the new exact version', async () => {
    const { requests } = server({ authenticated: true })
    mount()
    await prepare()
    await userEvent.click(screen.getByRole('checkbox', { name: consentName }))
    await userEvent.type(screen.getByRole('textbox', { name: 'Publication title' }), ' revised')
    expect(screen.queryByRole('region', { name: 'Exact publication preview' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve this exact version' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Prepare publication preview' }))
    await screen.findByRole('region', { name: 'Exact publication preview' })
    expect(screen.getByRole('checkbox', { name: consentName })).not.toBeChecked()
    expect(screen.getByRole('button', { name: 'Approve this exact version' })).toBeDisabled()
    await userEvent.click(screen.getByRole('checkbox', { name: consentName }))
    await userEvent.click(screen.getByRole('button', { name: 'Approve this exact version' }))
    await waitFor(() => expect(requests.mock.calls.some(([path]) => path === `/api/v1/publications/${SECOND_MANIFEST}/approve`)).toBe(true))
    const approval = requests.mock.calls.find(([path]) => path === `/api/v1/publications/${SECOND_MANIFEST}/approve`)!
    expect(JSON.parse(approval[1]?.body as string)).toEqual({ expected_manifest_sha256: 'e'.repeat(64) })
  })

  it('shows production distinctly and never selects it implicitly when sandbox is unavailable', async () => {
    const { state, requests } = server({ authenticated: true })
    state.capabilities.destinations = ['production']
    mount()
    await fillCreator()
    const destination = screen.getByRole('combobox', { name: 'Destination' })
    expect(destination).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Prepare publication preview' })).toBeDisabled()
    await userEvent.selectOptions(destination, 'production')
    await userEvent.click(screen.getByRole('button', { name: 'Prepare publication preview' }))
    const preview = await screen.findByRole('region', { name: 'Exact publication preview' })
    expect(within(preview).getByText('Zenodo production · public record', { exact: true })).toBeVisible()
    expect(screen.getByText('This approval is for production publication of this exact version.')).toBeVisible()
    expect(screen.getByRole('checkbox', { name: consentName })).not.toBeChecked()
    expect(requests.mock.calls.some(([path]) => path.toString().endsWith('/approve'))).toBe(false)
  })

  it('keeps login failures private and clears the submitted access code', async () => {
    const { state } = server()
    state.loginStatus = 401
    const { container, expired } = mount()
    const input = await screen.findByLabelText('Publisher access code')
    await userEvent.type(input, 'synthetic-private-invalid-code')
    await userEvent.click(screen.getByRole('button', { name: 'Unlock publisher approval' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Publisher access was not accepted')
    expect(input).toHaveValue('')
    expect(container).not.toHaveTextContent('synthetic-private-invalid-code')
    expect(expired).not.toHaveBeenCalled()
  })

  it('revokes the exact receipt without preparing or approving another document', async () => {
    const { requests } = server({ authenticated: true, records: [manifestFixture(true)] })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Revoke this approval' }))
    const preview = await screen.findByRole('region', { name: 'Exact publication preview' })
    await waitFor(() => expect(within(preview).getByText('Approval revoked', { exact: true })).toBeVisible())
    const posts = requests.mock.calls.filter(([, init]) => init?.method === 'POST')
    expect(posts).toHaveLength(1)
    expect(posts[0][0]).toBe(`/api/v1/publications/approvals/${APPROVAL}/revoke`)
    expect(posts[0][1]?.headers).toMatchObject({ 'X-CSRF-Token': BROWSER_CSRF, 'X-Operator-CSRF-Token': OPERATOR_CSRF })
    expect(screen.getByRole('checkbox', { name: consentName })).not.toBeChecked()
  })

  it('locks publisher access while retaining the browser-owned document preview', async () => {
    const { requests } = server({ authenticated: true, records: [manifestFixture()] })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Lock publisher access' }))
    await screen.findByLabelText('Publisher access code')
    expect(screen.getByRole('region', { name: 'Exact publication preview' })).toBeVisible()
    expect(screen.getByRole('checkbox', { name: consentName })).toBeDisabled()
    expect(requests.mock.calls.find(([path]) => path === '/api/v1/operator/logout')?.[1]?.headers).toMatchObject({ 'X-CSRF-Token': BROWSER_CSRF, 'X-Operator-CSRF-Token': OPERATOR_CSRF })
  })

  it('clears only publisher authorization when its CSRF/session expires', async () => {
    const { state } = server({ authenticated: true, records: [manifestFixture()] })
    state.approveStatus = 401
    const { expired } = mount()
    await userEvent.click(await screen.findByRole('checkbox', { name: consentName }))
    await userEvent.click(screen.getByRole('button', { name: 'Approve this exact version' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Publisher access expired or changed')
    expect(screen.getByRole('region', { name: 'Exact publication preview' })).toBeVisible()
    expect(screen.getByRole('checkbox', { name: consentName })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: consentName })).not.toBeChecked()
    expect(expired).not.toHaveBeenCalled()
  })

  it('clears all publication data when the browser session expires', async () => {
    const { state } = server({ authenticated: true, records: [manifestFixture(true)] })
    const { expired } = mount()
    await screen.findByRole('region', { name: 'Exact publication preview' })
    state.historyStatus = 401
    await userEvent.click(screen.getByRole('button', { name: 'Reload publication details' }))
    await waitFor(() => expect(expired).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('region', { name: 'Exact publication preview' })).not.toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Saved publication previews' })).not.toBeInTheDocument()
    expect(screen.queryByText('Synthetic Publisher')).not.toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Your research session expired or changed')
  })

  it('reconciles a lost approval response through read-only reload instead of resubmitting', async () => {
    const { state, requests } = server({ authenticated: true, records: [manifestFixture()] })
    state.loseApprovalResponse = true
    mount()
    await userEvent.click(await screen.findByRole('checkbox', { name: consentName }))
    await userEvent.click(screen.getByRole('button', { name: 'Approve this exact version' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('The change could not be confirmed')
    expect(screen.queryByRole('button', { name: 'Approve this exact version' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Prepare publication preview' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Reload publication details' }))
    const preview = await screen.findByRole('region', { name: 'Exact publication preview' })
    expect(within(preview).getByText('Approval recorded', { exact: true })).toBeVisible()
    expect(requests.mock.calls.filter(([path]) => path.toString().endsWith('/approve'))).toHaveLength(1)
  })

  it('expires an approval and publisher access locally without auto-renewing either', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-28T00:00:00Z'))
    const record = manifestFixture(true)
    record.approval!.expires_at = '2026-10-28T00:01:00Z'
    const { state, requests } = server({ authenticated: true, records: [record] })
    state.operator.expires_at = '2026-10-28T00:01:00Z'
    mount()
    await vi.waitFor(() => expect(screen.getByRole('region', { name: 'Exact publication preview' })).toBeVisible())
    await act(async () => { await vi.advanceTimersByTimeAsync(61000) })
    expect(within(screen.getByRole('region', { name: 'Exact publication preview' })).getByText('Approval expired', { exact: true })).toBeVisible()
    expect(screen.getByRole('checkbox', { name: consentName })).toBeDisabled()
    expect(screen.getByLabelText('Publisher access code')).toBeVisible()
    expect(requests.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
  })

  it('removes a reviewed sandbox preview as soon as its destination changes', async () => {
    server({ authenticated: true, records: [manifestFixture()] })
    mount()
    await userEvent.click(await screen.findByRole('checkbox', { name: consentName }))
    fireEvent.change(screen.getByRole('combobox', { name: 'Destination' }), { target: { value: 'production' } })
    expect(screen.queryByRole('region', { name: 'Exact publication preview' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve this exact version' })).not.toBeInTheDocument()
    expect(screen.getByText(/Changes need a new preview/)).toBeVisible()
  })

  it('rejects a saved manifest whose document hash no longer matches the displayed artifact', async () => {
    const record = manifestFixture()
    record.manifest.markdown_sha256 = REVIEW_HASH
    server({ authenticated: true, records: [record] })
    mount()
    expect(await screen.findByText('Publication settings could not be checked. Reload publication details to try again.')).toBeVisible()
    expect(screen.queryByRole('region', { name: 'Exact publication preview' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve this exact version' })).not.toBeInTheDocument()
  })

  it('keeps the approval label neutral when the runtime publication overlay reports a published record', async () => {
    const record = manifestFixture(true)
    record.publication_status = 'published'
    server({ authenticated: true, records: [record] })
    mount()
    const preview = await screen.findByRole('region', { name: 'Exact publication preview' })
    expect(within(preview).getByText('Approval recorded', { exact: true })).toBeVisible()
    expect(preview).not.toHaveTextContent('not published')
    expect(preview).not.toHaveTextContent('This document has not been published')
    expect(screen.queryByRole('link', { name: /Published DOI/ })).not.toBeInTheDocument()
  })
})
