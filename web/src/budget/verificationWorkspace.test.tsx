import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { VerificationWorkspace } from './verificationWorkspace'
import { ACCOUNT_ID, DATE, account, proposal, record } from './test-support'
import { BROWSER_CSRF, JOB_ID, OPERATOR_CSRF, READINESS_ID, attestation, completedJob, verificationCapabilities, verificationJob, verificationPreview } from './verificationFixtures'
import type { MetaProbeCapabilities, MetaProbeJob } from './verificationTypes'

type Handler = (path: string, options?: RequestInit) => Response | undefined | Promise<Response | undefined>
function savedReadiness() { const content = proposal(); content.application_limits.request_timeout_seconds = 5.5; return record(content) }
function server(handler?: Handler) {
  const state: { caps: MetaProbeCapabilities; jobs: MetaProbeJob[] } = { caps: verificationCapabilities(), jobs: [] }
  const writes: { path: string; body: unknown; headers: RequestInit['headers'] }[] = []
  const fetch = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = input.toString()
    if (options?.method === 'POST') writes.push({ path, body: JSON.parse(options.body!.toString()), headers: options.headers })
    const result = await handler?.(path, options); if (result) return result
    if (path === '/api/v1/budget/verification/capabilities') return Response.json(state.caps)
    if (path.includes('/verification/jobs?')) return Response.json({ jobs: state.jobs, next_cursor: null })
    if (path.includes('/readiness?')) return Response.json({ proposals: [savedReadiness()], next_cursor: null })
    if (path === '/api/v1/budget/verification/preview') return Response.json(verificationPreview())
    if (path === `/api/v1/budget/accounts/${ACCOUNT_ID}`) return Response.json(account())
    if (path === '/api/v1/budget/verification/jobs' && options?.method === 'POST') { const value = verificationJob(); state.jobs = [value]; return Response.json(value, { status: 201 }) }
    if (path === `/api/v1/budget/verification/jobs/${JOB_ID}/cancel`) { const value = { ...state.jobs[0], status: 'cancelled' as const, can_cancel: false, approval: { ...state.jobs[0].approval, valid: false } }; state.jobs = [value]; return Response.json(value) }
    if (path === `/api/v1/budget/verification/jobs/${JOB_ID}/finalize`) { const value = { ...completedJob(), status: 'cancelled' as const }; state.jobs = [value]; return Response.json(value) }
    if (path === `/api/v1/budget/verification/jobs/${JOB_ID}`) return Response.json(state.jobs[0] ?? verificationJob())
    throw new Error(`Unexpected synthetic verification route: ${path}`)
  }); vi.stubGlobal('fetch', fetch)
  return { state, writes, fetch }
}
function settings() { return { csrfToken: BROWSER_CSRF, operatorCsrf: OPERATOR_CSRF as string | null, authenticated: true, onBrowserExpired: vi.fn(), onOperatorExpired: vi.fn() } }
async function ready() { await screen.findByText('No one-call verification job has been armed in this authorized scope.') }
async function prepare() {
  fireEvent.change(screen.getByLabelText('Exact saved readiness attestation'), { target: { value: READINESS_ID } })
  fireEvent.change(screen.getByLabelText('Reviewed provider-account attestation JSON'), { target: { value: JSON.stringify(attestation()) } })
  await userEvent.click(screen.getByRole('button', { name: 'Preview exact one-call manifest' }))
  return screen.findByRole('region', { name: 'Current shared ledger observations' })
}
async function consent() { await userEvent.click(screen.getByRole('checkbox', { name: /I explicitly approve ONE paid protocol call/ })) }
const arms = <T extends { path: string },>(writes: T[]) => writes.filter((write) => write.path === '/api/v1/budget/verification/jobs')

beforeEach(() => vi.spyOn(Date, 'now').mockReturnValue(Date.parse(DATE)))
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('explicit one-call verification workspace', () => {
  it('starts blank and default closed, reading only anonymous capabilities without automatic submission', async () => {
    const requests = server(); requests.state.caps = verificationCapabilities('off')
    render(<VerificationWorkspace {...settings()} authenticated={false} operatorCsrf={null} />)
    expect(await screen.findByText(/Paid verification is disabled or not configured. Authorized saved observations/)).toBeVisible()
    expect(screen.getByLabelText('Reviewed provider-account attestation JSON')).toHaveValue('')
    expect(screen.getByLabelText('Reviewed provider-account attestation JSON')).toBeDisabled()
    expect(screen.getByLabelText('Exact saved readiness attestation')).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Preview exact one-call manifest' })).toBeDisabled()
    expect(requests.fetch.mock.calls).toHaveLength(1); expect(requests.writes).toHaveLength(0)
    expect(requests.fetch.mock.calls[0][1]).toMatchObject({ credentials: 'omit', method: 'GET' })
    expect(screen.queryByRole('button', { name: /Activate research|Retry|Reconcile|Clear kill switch/ })).toBeNull()
  })
  it('displays the exact model, fixed request, reservation and hashes before a separate fresh consent and single arm', async () => {
    const requests = server(); render(<VerificationWorkspace {...settings()} />); await ready(); await prepare()
    const review = screen.getByRole('region', { name: 'One-call approval review' }), arm = screen.getByRole('button', { name: 'Arm and queue THIS one call' })
    expect(review).toHaveTextContent('synthetic-fixture-model'); expect(review).toHaveTextContent('5.5 seconds')
    expect(review).toHaveTextContent('$0.000002'); expect(review).toHaveTextContent('Human attestation of provider credits and limits')
    expect(review).toHaveTextContent('These are operator statements with evidence pointers.')
    expect(arm).toBeDisabled(); expect(requests.writes.map((item) => item.path)).toEqual(['/api/v1/budget/verification/preview'])
    await userEvent.click(within(review).getByText('Exact hashes and immutable manifest'))
    expect(within(review).getByText(verificationPreview().manifest_sha256)).toBeVisible()
    expect(within(review).getByText(verificationPreview().manifest.credential_binding_sha256)).toBeVisible()
    await userEvent.click(within(review).getByText('Exact fixed HTTP request'))
    expect(review).toHaveTextContent('Protocol check only.')
    await consent(); expect(arm).toBeEnabled(); fireEvent.click(arm); fireEvent.click(arm)
    await screen.findByRole('region', { name: 'Actual saved verification status' })
    const saved = arms(requests.writes); expect(saved).toHaveLength(1)
    expect(saved[0].body).toEqual({ manifest: verificationPreview().manifest, expected_manifest_sha256: verificationPreview().manifest_sha256 })
    expect(saved[0].headers).toMatchObject({ 'X-CSRF-Token': BROWSER_CSRF, 'X-Operator-CSRF-Token': OPERATOR_CSRF })
    expect(screen.queryByRole('region', { name: 'One-call approval review' })).toBeNull()
  })
  it('invalidates the whole preview and checkbox on attestation edits before requesting a new exact review', async () => {
    const requests = server(); render(<VerificationWorkspace {...settings()} />); await ready(); await prepare(); await consent()
    fireEvent.change(screen.getByLabelText('Reviewed provider-account attestation JSON'), { target: { value: `${JSON.stringify(attestation())} ` } })
    expect(screen.queryByRole('region', { name: 'One-call approval review' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Preview exact one-call manifest' }))
    await screen.findByRole('region', { name: 'Current shared ledger observations' })
    expect(screen.getByRole('checkbox', { name: /I explicitly approve ONE paid protocol call/ })).not.toBeChecked()
    expect(screen.getByRole('button', { name: 'Arm and queue THIS one call' })).toBeDisabled()
    expect(arms(requests.writes)).toHaveLength(0)
  })
  it('recovers a committed unknown arm from matching immutable history without another paid permission', async () => {
    const requests = server((path, options) => { if (path === '/api/v1/budget/verification/jobs' && options?.method === 'POST') { requests.state.jobs = [verificationJob()]; throw new TypeError('Synthetic lost committed arm receipt') }; return undefined })
    render(<VerificationWorkspace {...settings()} />); await ready(); await prepare(); await consent(); await userEvent.click(screen.getByRole('button', { name: 'Arm and queue THIS one call' }))
    expect(await screen.findByText('The original armed job was recovered. No second paid permission or request was created.')).toBeVisible()
    expect(screen.getByRole('region', { name: 'Actual saved verification status' })).toHaveTextContent('queued')
    expect(arms(requests.writes)).toHaveLength(1)
    expect(screen.queryByRole('button', { name: 'Check original arm action' })).toBeNull()
  })
  it('locks an unconfirmed arm and repeats only the identical reviewed manifest and hash on an explicit check', async () => {
    let count = 0
    const requests = server((path, options) => { if (path === '/api/v1/budget/verification/jobs' && options?.method === 'POST' && ++count === 1) throw new TypeError('Synthetic unknown arm response'); return undefined })
    render(<VerificationWorkspace {...settings()} />); await ready(); await prepare(); await consent(); await userEvent.click(screen.getByRole('button', { name: 'Arm and queue THIS one call' }))
    const check = await screen.findByRole('button', { name: 'Check original arm action' })
    expect(screen.getByLabelText('Reviewed provider-account attestation JSON')).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: /I explicitly approve ONE paid protocol call/ })).not.toBeChecked()
    expect(screen.getByRole('button', { name: 'Reload verification desk' })).toBeDisabled()
    expect(arms(requests.writes)).toHaveLength(1)
    await userEvent.click(check); await screen.findByRole('region', { name: 'Actual saved verification status' })
    const saved = arms(requests.writes); expect(saved).toHaveLength(2); expect(saved[1].body).toEqual(saved[0].body)
    expect(saved[1].body).not.toHaveProperty('idempotency_key')
  })
  it('stops unknown work locally while retaining its hold warning and offering no retry or paid replacement', async () => {
    const requests = server(); requests.state.jobs = [verificationJob({ status: 'interrupted', external_outcome_unknown: true, reason: 'native_response_unknown' })]
    render(<VerificationWorkspace {...settings()} />)
    await screen.findByRole('region', { name: 'Actual saved verification status' })
    const stop = await screen.findByRole('button', { name: 'Stop future dispatch' }); await waitFor(() => expect(stop).toBeEnabled()); await userEvent.click(stop)
    expect(await screen.findByText('Recorded job state: cancelled')).toBeVisible()
    expect(screen.getByText(/A request or billing outcome is unknown. Its conservative hold remains counted/)).toBeVisible()
    expect(requests.state.jobs[0].external_outcome_unknown).toBe(true)
    expect(screen.getByRole('button', { name: 'Preview exact one-call manifest' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: /Retry|Resume|Reconcile|Arm and queue THIS/ })).toBeNull()
    expect(requests.writes.map((item) => item.path)).toEqual([`/api/v1/budget/verification/jobs/${JOB_ID}/cancel`])
    expect(requests.writes[0].body).toEqual({})
  })
  it('keeps free finalization available when paid preparation is off, retaining a late passed receipt on a cancelled job', async () => {
    const requests = server(); requests.state.caps = verificationCapabilities('inspect'); requests.state.jobs = [verificationJob({ status: 'cancelled', can_cancel: false, can_finalize: true, approval: { ...verificationJob().approval, valid: false } })]
    render(<VerificationWorkspace {...settings()} />)
    const finalize = await screen.findByRole('button', { name: 'Finalize recorded response · no new request' }); await waitFor(() => expect(finalize).toBeEnabled())
    expect(screen.getByRole('button', { name: 'Preview exact one-call manifest' })).toBeDisabled(); await userEvent.click(finalize)
    const observed = await screen.findByRole('article', { name: 'Immutable one-call protocol receipt' })
    expect(observed).toHaveTextContent('One-call protocol check passed.'); expect(observed).toHaveTextContent('Research execution remains disabled.')
    expect(observed).toHaveTextContent('does not verify pricing authenticity, a final invoice, available credits, a provider spending cap')
    expect(screen.getByText('Recorded job state: cancelled')).toBeVisible()
    expect(requests.writes.map((item) => item.path)).toEqual([`/api/v1/budget/verification/jobs/${JOB_ID}/finalize`])
    expect(observed).not.toHaveTextContent('Protocol check only.')
    await userEvent.click(within(observed).getByText('Exact receipt and grant bindings'))
    expect(observed).toHaveTextContent(completedJob().receipt!.observation_sha256)
    expect(observed).toHaveTextContent(completedJob().receipt!.bindings.reservation_id)
  })
  it.each([[401, 'research_session_required', 'browser'], [401, 'operator_session_required', 'operator'], [503, 'operator_unavailable', 'operator']] as const)('clears reviewed private data on %s/%s and delegates only the correct scope', async (status, detail, expected) => {
    let expired = false
    const requests = server((path) => expired && path.includes('/verification/jobs?') ? Response.json({ detail }, { status }) : undefined)
    const props = settings(); render(<VerificationWorkspace {...props} />); await ready(); await prepare(); expired = true
    await userEvent.click(screen.getByRole('button', { name: 'Reload verification desk' }))
    await waitFor(() => expect(expected === 'browser' ? props.onBrowserExpired : props.onOperatorExpired).toHaveBeenCalledTimes(1))
    expect(expected === 'browser' ? props.onOperatorExpired : props.onBrowserExpired).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Reviewed provider-account attestation JSON')).toHaveValue('')
    expect(screen.queryByRole('region', { name: 'One-call approval review' })).toBeNull()
    expect(screen.queryByRole('region', { name: 'Saved one-call verification jobs' })).toBeNull()
    expect(arms(requests.writes)).toHaveLength(0)
  })
  it.each([[403, 'readiness_actor_forbidden'], [503, 'readiness_role_unavailable']] as const)('clears all private data on %s/%s while preserving browser/operator access distinction', async (status, detail) => {
    let forbidden = false
    server((path) => forbidden && path.includes('/verification/jobs?') ? Response.json({ detail }, { status }) : undefined)
    const props = settings(); render(<VerificationWorkspace {...props} />); await ready(); await prepare(); forbidden = true
    await userEvent.click(screen.getByRole('button', { name: 'Reload verification desk' }))
    expect(await screen.findByText('Current budget authority is required. Private verification inputs and receipts have been cleared.')).toBeVisible()
    expect(screen.getByLabelText('Reviewed provider-account attestation JSON')).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Reload verification desk' })).toBeDisabled()
    expect(screen.queryByRole('region', { name: 'One-call approval review' })).toBeNull()
    expect(props.onBrowserExpired).not.toHaveBeenCalled(); expect(props.onOperatorExpired).not.toHaveBeenCalled()
  })
  it('aborts a delayed status read and clears prior private inputs when the browser CSRF scope changes', async () => {
    let resolveOld!: (value: Response) => void, oldSignal: AbortSignal | null = null, delayed = true
    const requests = server((path, options) => { if (path === `/api/v1/budget/verification/jobs/${JOB_ID}` && delayed) { oldSignal = options?.signal as AbortSignal; return new Promise<Response>((resolve) => { resolveOld = resolve }) }; return undefined })
    requests.state.jobs = [verificationJob()]
    const props = settings(), view = render(<VerificationWorkspace {...props} />)
    await screen.findByRole('region', { name: 'Actual saved verification status' }); await waitFor(() => expect(oldSignal).not.toBeNull())
    delayed = false; requests.state.jobs = []
    view.rerender(<VerificationWorkspace {...props} csrfToken={'c'.repeat(64)} />)
    await ready(); expect(oldSignal!.aborted).toBe(true)
    await act(async () => { resolveOld(Response.json(completedJob())) })
    expect(screen.queryByRole('region', { name: 'Actual saved verification status' })).toBeNull()
    expect(screen.queryByRole('article', { name: 'Immutable one-call protocol receipt' })).toBeNull()
    expect(screen.getByLabelText('Reviewed provider-account attestation JSON')).toHaveValue('')
    expect(requests.writes).toHaveLength(0)
  })
  it('aborts in-flight polling on unmount and ignores the late private receipt without any mutation', async () => {
    let resolveOld!: (value: Response) => void, oldSignal: AbortSignal | null = null
    const requests = server((path, options) => path === `/api/v1/budget/verification/jobs/${JOB_ID}` ? new Promise<Response>((resolve) => { oldSignal = options?.signal as AbortSignal; resolveOld = resolve }) : undefined)
    requests.state.jobs = [verificationJob()]
    const view = render(<VerificationWorkspace {...settings()} />)
    await screen.findByRole('region', { name: 'Actual saved verification status' }); await waitFor(() => expect(oldSignal).not.toBeNull()); view.unmount()
    expect(oldSignal!.aborted).toBe(true)
    await act(async () => { resolveOld(Response.json(completedJob())) })
    expect(screen.queryByRole('region', { name: 'One-call Meta protocol verification' })).toBeNull()
    expect(requests.writes).toHaveLength(0)
  })
})
