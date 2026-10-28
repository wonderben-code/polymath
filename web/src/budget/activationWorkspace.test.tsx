import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ActivationWorkspace } from './activationWorkspace'
import { ACCOUNT_ID, DATE, account, record } from './test-support'
import { completedJob, attestation } from './verificationFixtures'
import { ACTIVATION_ID, BROWSER_CSRF, OPERATOR_CSRF, READINESS_ID, RECEIPT_ID, activationCapabilities, activationPreview, activationProposal, researchActivation, researchReview } from './activationFixtures'
import type { MetaResearchActivation, MetaResearchCapabilities } from './activationTypes'

type Handler = (path: string, options?: RequestInit) => Response | undefined | Promise<Response | undefined>
function server(handler?: Handler) {
  const state: { caps: MetaResearchCapabilities; history: MetaResearchActivation[] } = { caps: activationCapabilities(), history: [] }
  const writes: { path: string; body: unknown; headers: RequestInit['headers'] }[] = []
  const fetch = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = input.toString()
    if (options?.method === 'POST') writes.push({ path, body: JSON.parse(options.body!.toString()), headers: options.headers })
    const custom = await handler?.(path, options); if (custom) return custom
    if (path === '/api/v1/budget/research/capabilities') return Response.json(state.caps)
    if (path.includes('/readiness?')) return Response.json({ proposals: [record(activationProposal())], next_cursor: null })
    if (path.includes('/verification/jobs?')) return Response.json({ jobs: [completedJob()], next_cursor: null })
    if (path.includes('/research/activations?')) return Response.json({ activations: state.history, next_cursor: null })
    if (path === '/api/v1/budget/research/preview') return Response.json(activationPreview())
    if (path === `/api/v1/budget/accounts/${ACCOUNT_ID}`) return Response.json(account())
    if (path === '/api/v1/budget/research/activations' && options?.method === 'POST') { const saved = researchActivation(); state.history = [saved]; return Response.json(saved, { status: 201 }) }
    if (path === `/api/v1/budget/research/activations/${ACTIVATION_ID}/revoke`) { const original = state.history[0]; const saved = { ...original, eligible_for_current_browser: false, research_execution_available: false, approval: { ...original.approval, revoked_at: DATE, valid: false } }; state.history = [saved]; return Response.json(saved) }
    if (path === `/api/v1/budget/research/activations/${ACTIVATION_ID}`) return Response.json(state.history[0])
    throw new Error(`Unexpected synthetic activation route: ${path}`)
  }); vi.stubGlobal('fetch', fetch)
  return { state, fetch, writes }
}
function settings() { return { csrfToken: BROWSER_CSRF, operatorCsrf: OPERATOR_CSRF as string | null, authenticated: true, onBrowserExpired: vi.fn(), onOperatorExpired: vi.fn() } }
async function ready() { await screen.findByText('No research activation has been saved in this authorized scope.') }
async function prepare() {
  fireEvent.change(screen.getByLabelText('Exact saved readiness attestation'), { target: { value: READINESS_ID } })
  fireEvent.change(screen.getByLabelText('Exact passed native protocol receipt'), { target: { value: RECEIPT_ID } })
  for (const checkbox of within(screen.getByRole('group', { name: 'Fresh human review required' })).getAllByRole('checkbox')) await userEvent.click(checkbox)
  const { model_wire_usage_confirmed: _wire, pricing_bounds_confirmed: _price, funding_cap_confirmed: _fund, ...facts } = researchReview()
  fireEvent.change(screen.getByLabelText('Fresh review dates and evidence JSON'), { target: { value: JSON.stringify(facts) } })
  fireEvent.change(screen.getByLabelText('Reviewed research provider-account attestation JSON'), { target: { value: JSON.stringify(attestation()) } })
  await userEvent.click(screen.getByRole('button', { name: 'Preview exact research activation' }))
  return screen.findByRole('region', { name: 'Research activation shared ledger observations' })
}
async function consent() { await userEvent.click(screen.getByRole('checkbox', { name: /I explicitly approve this exact native research activation/ })) }
const activations = <T extends { path: string },>(writes: T[]) => writes.filter((write) => write.path === '/api/v1/budget/research/activations')
beforeEach(() => vi.spyOn(Date, 'now').mockReturnValue(Date.parse(DATE)))
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('exact operator/browser scientific activation', () => {
  it('is blank and default disabled, reading no private endpoint before operator access', async () => {
    const requests = server(); requests.state.caps = activationCapabilities('off')
    render(<ActivationWorkspace {...settings()} authenticated={false} operatorCsrf={null} />)
    expect(await screen.findByText(/Native research activation is disabled or not configured. Authorized saved activations/)).toBeVisible()
    expect(screen.getByLabelText('Fresh review dates and evidence JSON')).toHaveValue('')
    expect(screen.getByLabelText('Reviewed research provider-account attestation JSON')).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Preview exact research activation' })).toBeDisabled()
    expect(screen.getByRole('group', { name: 'Fresh human review required' })).toBeDisabled()
    expect(requests.fetch.mock.calls).toHaveLength(1); expect(requests.writes).toHaveLength(0)
    expect(requests.fetch.mock.calls[0][1]).toMatchObject({ method: 'GET', credentials: 'omit' })
  })
  it('previews exact model, all five full profiles, ledger and evidence before a separate single explicit activation', async () => {
    const requests = server(); render(<ActivationWorkspace {...settings()} />); await ready(); await prepare()
    const preview = screen.getByRole('region', { name: 'Research activation approval review' }), activate = screen.getByRole('button', { name: 'Activate research for THIS operator and browser' })
    expect(preview).toHaveTextContent('synthetic-fixture-model'); expect(preview).toHaveTextContent('4,096 tokens / 90,000 characters'); expect(preview).toHaveTextContent('45.5s / 75.5s'); expect(preview).toHaveTextContent('20 / 16 / 2'); expect(preview).toHaveTextContent('12 / 12 / 2')
    expect(preview).toHaveTextContent('This approving operator and browser only.'); expect(preview).toHaveTextContent('Unknown outcomes keep their conservative hold')
    expect(activate).toBeDisabled(); expect(activations(requests.writes)).toHaveLength(0)
    await userEvent.click(within(preview).getByText('Exact activation hashes and complete manifest'))
    expect(within(preview).getByText(activationPreview().manifest_sha256)).toBeVisible()
    await consent(); fireEvent.click(activate); fireEvent.click(activate)
    await screen.findByText('Eligible for this operator and browser at the last server check')
    const writes = activations(requests.writes); expect(writes).toHaveLength(1)
    expect(writes[0].body).toEqual({ manifest: activationPreview().manifest, expected_manifest_sha256: activationPreview().manifest_sha256 })
    expect(writes[0].headers).toMatchObject({ 'X-CSRF-Token': BROWSER_CSRF, 'X-Operator-CSRF-Token': OPERATOR_CSRF })
    expect(requests.writes.every((write) => write.path.startsWith('/api/v1/budget/research/'))).toBe(true)
    expect(screen.queryByRole('region', { name: 'Research activation approval review' })).toBeNull()
  })
  it('clears preview and final consent when source, funding or fresh review changes', async () => {
    const requests = server(); render(<ActivationWorkspace {...settings()} />); await ready(); await prepare(); await consent()
    fireEvent.change(screen.getByLabelText('Reviewed research provider-account attestation JSON'), { target: { value: JSON.stringify(attestation()) + ' ' } })
    expect(screen.queryByRole('region', { name: 'Research activation approval review' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Preview exact research activation' })); await screen.findByRole('region', { name: 'Research activation shared ledger observations' })
    expect(screen.getByRole('checkbox', { name: /I explicitly approve this exact native research activation/ })).not.toBeChecked()
    expect(activations(requests.writes)).toHaveLength(0)
  })
  it('recovers a committed lost activation receipt from exact immutable history without repeating the mutation', async () => {
    const requests = server((path, options) => { if (path === '/api/v1/budget/research/activations' && options?.method === 'POST') { requests.state.history = [researchActivation()]; throw new TypeError('Synthetic committed response loss') }; return undefined })
    render(<ActivationWorkspace {...settings()} />); await ready(); await prepare(); await consent(); await userEvent.click(screen.getByRole('button', { name: 'Activate research for THIS operator and browser' }))
    expect(await screen.findByText('The original activation was recovered from saved history. No duplicate approval or scientific task was created.')).toBeVisible()
    expect(activations(requests.writes)).toHaveLength(1)
    expect(screen.queryByRole('button', { name: 'Check original activation action' })).toBeNull()
  })
  it('pins an unresolved activation and permits only an explicit identical manifest/hash check', async () => {
    let count = 0
    const requests = server((path, options) => { if (path === '/api/v1/budget/research/activations' && options?.method === 'POST' && ++count === 1) throw new TypeError('Synthetic missing receipt'); return undefined })
    render(<ActivationWorkspace {...settings()} />); await ready(); await prepare(); await consent(); await userEvent.click(screen.getByRole('button', { name: 'Activate research for THIS operator and browser' }))
    const check = await screen.findByRole('button', { name: 'Check original activation action' })
    expect(screen.getByRole('button', { name: 'Reload research activation desk' })).toBeDisabled()
    expect(screen.getByLabelText('Fresh review dates and evidence JSON')).toBeDisabled()
    await userEvent.click(check); await screen.findByText('Eligible for this operator and browser at the last server check')
    expect(activations(requests.writes)).toHaveLength(2); expect(activations(requests.writes)[1].body).toEqual(activations(requests.writes)[0].body)
  })
  it('reads historical or foreign-browser approvals honestly and revokes without queuing scientific work', async () => {
    const requests = server(); requests.state.caps = activationCapabilities('inspect'); requests.state.history = [researchActivation({ eligible_for_current_browser: false, research_execution_available: false })]
    render(<ActivationWorkspace {...settings()} />)
    expect(await screen.findByText('Recorded approval · not currently eligible for this browser')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Preview exact research activation' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Revoke this research activation' }))
    expect(await screen.findByText('Research approval revoked')).toBeVisible()
    expect(requests.writes.map((write) => write.path)).toEqual([`/api/v1/budget/research/activations/${ACTIVATION_ID}/revoke`])
    expect(requests.writes[0].body).toEqual({})
  })
  it.each([['research_session_required', 401, 'browser'], ['operator_session_required', 401, 'operator'], ['operator_unavailable', 503, 'operator'], ['readiness_role_unavailable', 503, 'role']])('clears private state on %s while preserving the correct parent scope', async (detail, status, scope) => {
    let denied = false
    const requests = server((path) => denied && path === `/api/v1/budget/research/activations/${ACTIVATION_ID}` ? Response.json({ detail }, { status }) : undefined); requests.state.history = [researchActivation()]
    const props = settings(); render(<ActivationWorkspace {...props} />); await screen.findByText('Eligible for this operator and browser at the last server check')
    denied = true; await userEvent.click(screen.getByRole('button', { name: 'Read saved activation status' }))
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Saved research activation status' })).toBeNull())
    expect(screen.getByLabelText('Reviewed research provider-account attestation JSON')).toHaveValue('')
    expect(props.onBrowserExpired).toHaveBeenCalledTimes(scope === 'browser' ? 1 : 0)
    expect(props.onOperatorExpired).toHaveBeenCalledTimes(scope === 'operator' ? 1 : 0)
    if (scope === 'role') expect(screen.getByRole('alert')).toHaveTextContent('Current budget authority is required')
  })
})
