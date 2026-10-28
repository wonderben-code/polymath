import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PublicActivationWorkspace } from './publicActivationWorkspace'
import { publicCapabilities, publicPreview, publicPreviewRequest, publicRecord } from './publicActivationFixtures.test-support'
import { activationProposal, READINESS_ID, RECEIPT_ID } from './activationFixtures'
import { completedJob } from './verificationFixtures'
import { ACCOUNT_ID, BROWSER_CSRF, OPERATOR_CSRF, DATE, account, record } from './test-support'
import type { PublicResearchActivation } from './publicActivationTypes'
const BASE = '/api/v1/budget/research/public'
type Handler = (path: string, options?: RequestInit) => Promise<Response | undefined> | Response | undefined
function server(handler?: Handler) {
  const state = { caps: publicCapabilities(), history: [] as PublicResearchActivation[] }
  const fetch = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = input.toString(), custom = await handler?.(path, options); if (custom) return custom
    if (path === `${BASE}/capabilities`) return Response.json(state.caps)
    if (path.includes('/readiness?')) return Response.json({ proposals: [record(activationProposal())], next_cursor: null })
    if (path.includes('/verification/jobs?')) return Response.json({ jobs: [completedJob()], next_cursor: null })
    if (path.startsWith(`${BASE}/activations?`)) return Response.json({ activations: state.history, next_cursor: null })
    if (path === `${BASE}/preview`) return Response.json(publicPreview())
    if (path === `/api/v1/budget/accounts/${ACCOUNT_ID}`) return Response.json(account())
    if (path === `${BASE}/activate`) { state.history = [publicRecord()]; return Response.json(state.history[0], { status: 201 }) }
    if (path.endsWith('/revoke')) { const original = state.history[0]; state.history = [{ ...original, delegation_current: false, approval: { ...original.approval, valid: false, revoked_at: DATE } }]; return Response.json(state.history[0]) }
    if (path === `${BASE}/activations/${publicRecord().id}`) return Response.json(state.history[0])
    throw new Error(`Unexpected synthetic public funding route ${path}`)
  }); vi.stubGlobal('fetch', fetch)
  return { state, fetch, posts: () => fetch.mock.calls.filter(([, options]) => options?.method === 'POST') }
}
function settings() { return { csrfToken: BROWSER_CSRF, operatorCsrf: OPERATOR_CSRF as string | null, authenticated: true, onBrowserExpired: vi.fn(), onOperatorExpired: vi.fn() } }
async function ready() { await screen.findByText('No public funding delegation is saved in this authorized scope.') }
async function prepare() {
  fireEvent.change(screen.getByLabelText('Exact saved readiness attestation'), { target: { value: READINESS_ID } })
  fireEvent.change(screen.getByLabelText('Exact passed protocol receipt'), { target: { value: RECEIPT_ID } })
  for (const checkbox of within(screen.getByRole('group', { name: 'Fresh human review' })).getAllByRole('checkbox')) fireEvent.click(checkbox)
  const body = publicPreviewRequest()
  fireEvent.change(screen.getByLabelText('Exact public funding deadline in UTC'), { target: { value: body.valid_until } })
  fireEvent.change(screen.getByLabelText('Reviewed public funding JSON'), { target: { value: JSON.stringify({ review: body.review, provider_account_attestation: body.provider_account_attestation, public_limits: body.public_limits }) } })
  fireEvent.click(screen.getByRole('button', { name: 'Preview exact public funding' }))
  await screen.findByRole('region', { name: 'Exact public funding approval' })
  await waitFor(() => expect(screen.getByRole('checkbox', { name: /Allow public visitors/ })).toBeEnabled())
}
beforeEach(() => vi.spyOn(Date, 'now').mockReturnValue(Date.parse(DATE)))
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers() })
describe('explicit public funding workspace', () => {
  it('starts with blank money/review/deadline and sends no automatic activation', async () => {
    const requests = server(); render(<PublicActivationWorkspace {...settings()} />); await ready()
    expect(screen.getByLabelText('Exact public funding deadline in UTC')).toHaveValue(''); expect(screen.getByLabelText('Reviewed public funding JSON')).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Preview exact public funding' })).toBeDisabled(); expect(requests.posts()).toHaveLength(0)
  })
  it('leaves the default disabled setup honest and makes no protected reads or writes', async () => {
    const requests = server(); requests.state.caps = publicCapabilities('off'); render(<PublicActivationWorkspace {...settings()} />)
    await screen.findByText(/Public funding is disabled or not configured/)
    expect(screen.getByRole('button', { name: 'Preview exact public funding' })).toBeDisabled(); expect(requests.fetch).toHaveBeenCalledTimes(1); expect(requests.posts()).toHaveLength(0)
  })
  it('requires a fresh durable-after-signout confirmation and submits the exact hash only once', async () => {
    const requests = server(); render(<PublicActivationWorkspace {...settings()} />); await ready(); await prepare()
    expect(requests.posts()).toHaveLength(1); expect(screen.getByRole('button', { name: 'Activate THIS public funding delegation' })).toBeDisabled()
    expect(screen.getAllByText(/continues after operator sign-out/).length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('checkbox', { name: /Allow public visitors/ }))
    const button = screen.getByRole('button', { name: 'Activate THIS public funding delegation' }); fireEvent.click(button); fireEvent.click(button)
    await screen.findByRole('heading', { name: 'Public delegation current at the last server check' })
    expect(requests.posts()).toHaveLength(2)
    const [path, options] = requests.posts()[1]; expect(path).toBe(`${BASE}/activate`)
    expect(JSON.parse(options!.body!.toString())).toEqual({ manifest: publicPreview().manifest, expected_manifest_sha256: publicPreview().manifest_sha256 })
    expect(options!.headers).toMatchObject({ 'X-CSRF-Token': BROWSER_CSRF, 'X-Operator-CSRF-Token': OPERATOR_CSRF })
    expect(screen.getByRole('button', { name: 'Stop this public funding' })).toBeEnabled()
  })
  it('invalidates the exact preview and consent on any edited deadline', async () => {
    const requests = server(); render(<PublicActivationWorkspace {...settings()} />); await ready(); await prepare(); fireEvent.click(screen.getByRole('checkbox', { name: /Allow public visitors/ }))
    fireEvent.change(screen.getByLabelText('Exact public funding deadline in UTC'), { target: { value: '2026-10-28T13:00:00Z' } })
    expect(screen.queryByRole('region', { name: 'Exact public funding approval' })).not.toBeInTheDocument(); expect(requests.posts()).toHaveLength(1)
  })
  it('rejects a deadline beyond 24h before preview and makes no write', async () => {
    const requests = server(); render(<PublicActivationWorkspace {...settings()} />); await ready()
    fireEvent.change(screen.getByLabelText('Exact saved readiness attestation'), { target: { value: READINESS_ID } }); fireEvent.change(screen.getByLabelText('Exact passed protocol receipt'), { target: { value: RECEIPT_ID } })
    for (const checkbox of within(screen.getByRole('group', { name: 'Fresh human review' })).getAllByRole('checkbox')) fireEvent.click(checkbox)
    const body = publicPreviewRequest(); fireEvent.change(screen.getByLabelText('Reviewed public funding JSON'), { target: { value: JSON.stringify({ review: body.review, provider_account_attestation: body.provider_account_attestation, public_limits: body.public_limits }) } }); fireEvent.change(screen.getByLabelText('Exact public funding deadline in UTC'), { target: { value: '2026-10-30T00:00:00Z' } }); fireEvent.click(screen.getByRole('button', { name: 'Preview exact public funding' }))
    await screen.findByRole('alert'); expect(requests.posts()).toHaveLength(0)
  })
  it('recovers a committed lost response through owned history without a second activation', async () => {
    let requests: ReturnType<typeof server>
    requests = server((path) => { if (path === `${BASE}/activate`) { requests.state.history = [publicRecord()]; throw new TypeError('synthetic committed response loss') }; return undefined })
    render(<PublicActivationWorkspace {...settings()} />); await ready(); await prepare(); fireEvent.click(screen.getByRole('checkbox', { name: /Allow public visitors/ })); fireEvent.click(screen.getByRole('button', { name: 'Activate THIS public funding delegation' }))
    await screen.findByText(/original delegation was recovered from saved history/); expect(requests.posts().filter(([path]) => path === `${BASE}/activate`)).toHaveLength(1)
  })
  it('stops the precise delegation while preserving its immutable history and deadline', async () => {
    const requests = server(); requests.state.history = [publicRecord()]; render(<PublicActivationWorkspace {...settings()} />)
    await screen.findByRole('button', { name: 'Stop this public funding' }); fireEvent.click(screen.getByRole('button', { name: 'Stop this public funding' }))
    await screen.findByRole('heading', { name: 'Public funding stopped' }); expect(requests.posts()).toHaveLength(1); expect(requests.state.history[0].manifest).toEqual(publicPreview().manifest)
    expect(screen.getByRole('button', { name: 'Stop this public funding' })).toBeDisabled(); expect(screen.getAllByText(publicPreview().manifest.valid_until).length).toBeGreaterThan(0)
  })
  it.each([[401, 'research_session_required', 'browser'], [401, 'operator_session_required', 'operator'], [503, 'readiness_role_unavailable', 'operator']] as const)('clears private review on %s/%s without treating funding as session expiry', async (status, detail, kind) => {
    let reject = false; const requests = server((path) => reject && path.endsWith('/preview') ? Response.json({ detail }, { status }) : undefined), props = settings()
    render(<PublicActivationWorkspace {...props} />); await ready(); await prepare(); reject = true
    fireEvent.change(screen.getByLabelText('Exact public funding deadline in UTC'), { target: { value: publicPreviewRequest().valid_until + ' ' } }); fireEvent.change(screen.getByLabelText('Exact public funding deadline in UTC'), { target: { value: publicPreviewRequest().valid_until } }); fireEvent.click(screen.getByRole('button', { name: 'Preview exact public funding' }))
    await waitFor(() => expect(kind === 'browser' ? props.onBrowserExpired : props.onOperatorExpired).toHaveBeenCalledTimes(1))
    expect(screen.getByLabelText('Reviewed public funding JSON')).toHaveValue(''); expect(screen.getByLabelText('Exact public funding deadline in UTC')).toHaveValue(''); expect(requests.posts()).toHaveLength(2)
  })
})
