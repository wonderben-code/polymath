import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BudgetWorkspace } from './BudgetWorkspace'
import { ACCOUNT_ID, account, BROWSER_CSRF, capabilities, OPERATOR_CSRF, preview, proposal, record, RECORD_ID } from './test-support'
import type { BudgetAccount, ReadinessRecord, SaveReadinessProposal } from './types'

type Handler = (path: string, options?: RequestInit) => Response | undefined | Promise<Response | undefined>
function server(handler?: Handler) {
  const state: { saved: ReadinessRecord | null; account: BudgetAccount | null } = { saved: null, account: null }
  const writes: { path: string; body: unknown }[] = []
  const fetch = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = input.toString(); if (options?.method === 'POST') writes.push({ path, body: JSON.parse(options.body!.toString()) })
    const result = await handler?.(path, options); if (result) return result
    if (path.endsWith('/capabilities')) return Response.json(capabilities())
    if (path.includes('/readiness?')) return Response.json({ proposals: state.saved ? [state.saved] : [], next_cursor: null })
    if (path.includes('/accounts?')) return Response.json({ accounts: state.account ? [state.account] : [], next_cursor: null })
    if (path.endsWith('/readiness/preview')) return Response.json(preview(JSON.parse(options!.body!.toString()).proposal))
    if (path === '/api/v1/budget/readiness') { const body = JSON.parse(options!.body!.toString()) as SaveReadinessProposal; state.saved = record(body.proposal); state.account = account(); return Response.json(state.saved, { status: 201 }) }
    if (path.endsWith(`/readiness/${RECORD_ID}`)) return Response.json(state.saved ?? record())
    if (path.endsWith('/disable')) { state.account = { ...(state.account ?? account()), execution_enabled: false, kill_switch: true }; return Response.json(state.account) }
    if (path.endsWith(`/accounts/${ACCOUNT_ID}`)) return Response.json(state.account ?? account())
    throw new Error('Unexpected synthetic budget route')
  }); vi.stubGlobal('fetch', fetch); return { state, writes, fetch }
}
function settings() { return { csrfToken: BROWSER_CSRF, operatorCsrf: OPERATOR_CSRF as string | null, authenticated: true, onBrowserExpired: vi.fn(), onOperatorExpired: vi.fn() } }
async function ready() { await screen.findByText('No disabled readiness proposal has been saved in this authorized scope.') }
async function prepare() { fireEvent.change(screen.getByLabelText('Reviewed readiness proposal JSON'), { target: { value: JSON.stringify(proposal()) } }); await userEvent.click(screen.getByRole('button', { name: 'Preview disabled readiness proposal' })); return screen.findByRole('region', { name: 'Exact disabled readiness preview' }) }
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

describe('disabled financial readiness workspace', () => {
  it('keeps provider facts blank and disables preparation by default without reading protected records', async () => {
    const requests = server((path) => path.endsWith('/capabilities') ? Response.json(capabilities('off')) : undefined)
    render(<BudgetWorkspace {...settings()} authenticated={false} operatorCsrf={null} />)
    expect(await screen.findByText(/Budget administration is not configured/)).toBeVisible()
    expect(screen.getByLabelText('Reviewed readiness proposal JSON')).toHaveValue('')
    expect(screen.getByLabelText('Reviewed readiness proposal JSON')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Preview disabled readiness proposal' })).toBeDisabled()
    expect(requests.fetch).toHaveBeenCalledTimes(1); expect(requests.writes).toHaveLength(0)
    expect(screen.queryByRole('button', { name: /Activate|Probe|Reconcile|Clear kill/ })).toBeNull()
  })
  it('shows exact rates, bounds, evidence, component rounding and run-cap stop before a separate pinned save', async () => {
    const requests = server(); render(<BudgetWorkspace {...settings()} />); await ready(); const review = await prepare()
    expect(within(review).getByText('synthetic-fixture-model')).toBeVisible()
    expect(within(review).getByText('https://fixtures.example.test/pricing')).toBeVisible()
    expect(within(review).getByText(preview().proposal_sha256)).toBeVisible()
    expect(within(review).getByRole('region', { name: 'Conservative reservation calculation' })).toHaveTextContent('$0.000002')
    expect(within(review).getByText('The ledger may stop the run before its maximum call count.')).toBeVisible()
    expect(requests.writes.map((item) => item.path)).toEqual(['/api/v1/budget/readiness/preview'])
    const save = screen.getByRole('button', { name: 'Save this disabled proposal' }); fireEvent.click(save); fireEvent.click(save)
    expect(await screen.findByRole('region', { name: 'Exact saved readiness attestation' })).toBeVisible()
    expect(await screen.findByRole('article', { name: 'Current budget account inspection' })).toHaveTextContent('Disabled')
    const saves = requests.writes.filter((item) => item.path === '/api/v1/budget/readiness'); expect(saves).toHaveLength(1)
    expect(saves[0].body).toEqual({ proposal: preview().proposal, expected_proposal_sha256: preview().proposal_sha256 })
    expect(requests.state.account?.execution_enabled).toBe(false); expect(requests.state.account?.pricing_verified).toBe(false); expect(requests.state.account?.kill_switch).toBe(true)
  })
  it('invalidates a stale exact preview on any reviewed JSON edit', async () => {
    const requests = server(); render(<BudgetWorkspace {...settings()} />); await ready(); await prepare()
    fireEvent.change(screen.getByLabelText('Reviewed readiness proposal JSON'), { target: { value: JSON.stringify({ ...proposal(), model_id: 'another-synthetic-model' }) } })
    expect(screen.queryByRole('region', { name: 'Exact disabled readiness preview' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Save this disabled proposal' })).toBeNull()
    expect(requests.writes).toHaveLength(1)
  })
  it('retains a valid proposal beyond 25 days and expires it only at its actual documentary boundary', async () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-28T00:00:00Z'))
    server(); render(<BudgetWorkspace {...settings()} />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    const content = proposal(); content.valid_until = '2026-12-02T00:00:00Z'
    fireEvent.change(screen.getByLabelText('Reviewed readiness proposal JSON'), { target: { value: JSON.stringify(content) } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Preview disabled readiness proposal' })); await vi.advanceTimersByTimeAsync(0) })
    expect(screen.getByRole('region', { name: 'Exact disabled readiness preview' })).toBeVisible()
    await act(async () => { await vi.advanceTimersByTimeAsync(25 * 86400000) })
    expect(screen.getByRole('region', { name: 'Exact disabled readiness preview' })).toBeVisible()
    await act(async () => { await vi.advanceTimersByTimeAsync(10 * 86400000) })
    expect(screen.queryByRole('region', { name: 'Exact disabled readiness preview' })).toBeNull()
    expect(screen.getByText(/proposal validity window ended/)).toBeVisible()
  })
  it('recovers a committed unknown save from immutable history without repeating its POST', async () => {
    const requests = server((path, options) => { if (path === '/api/v1/budget/readiness' && options?.method === 'POST') { requests.state.saved = record(); requests.state.account = account(); throw new TypeError('Synthetic committed response loss') }; return undefined })
    render(<BudgetWorkspace {...settings()} />); await ready(); await prepare(); await userEvent.click(screen.getByRole('button', { name: 'Save this disabled proposal' }))
    expect(await screen.findByText('The saved disabled attestation was recovered. No second proposal or account was created.')).toBeVisible()
    expect(await screen.findByRole('article', { name: 'Current budget account inspection' })).toBeVisible()
    expect(requests.writes.filter((item) => item.path === '/api/v1/budget/readiness')).toHaveLength(1)
  })
  it('retains an unresolved save and explicitly retries only its identical account/proposal/hash', async () => {
    let count = 0
    const requests = server((path, options) => { if (path === '/api/v1/budget/readiness' && options?.method === 'POST' && ++count === 1) throw new TypeError('Synthetic response loss'); return undefined })
    render(<BudgetWorkspace {...settings()} />); await ready(); await prepare(); await userEvent.click(screen.getByRole('button', { name: 'Save this disabled proposal' }))
    const check = await screen.findByRole('button', { name: 'Check original save action' })
    expect(screen.getByLabelText('Reviewed readiness proposal JSON')).toBeDisabled()
    await userEvent.click(check); await screen.findByRole('region', { name: 'Exact saved readiness attestation' })
    const saves = requests.writes.filter((item) => item.path === '/api/v1/budget/readiness'); expect(saves).toHaveLength(2); expect(saves[1].body).toEqual(saves[0].body)
  })
  it('keeps inspection and a fresh explicit disable available with preparation off while preserving all holds and frozen state', async () => {
    const original = account({ execution_enabled: true, pricing_verified: true, kill_switch: false, frozen: true, frozen_reason: 'synthetic_unknown_hold' })
    const requests = server((path) => path.endsWith('/capabilities') ? Response.json(capabilities('inspect')) : undefined); requests.state.account = original
    render(<BudgetWorkspace {...settings()} />); await ready(); expect(screen.getByLabelText('Reviewed readiness proposal JSON')).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: `Inspect account: ${ACCOUNT_ID}` }))
    const inspection = await screen.findByRole('article', { name: 'Current budget account inspection' }); expect(within(inspection).getByText('Enabled in the ledger')).toBeVisible()
    const disable = screen.getByRole('button', { name: 'Disable future execution' }); expect(disable).toBeDisabled(); expect(requests.writes).toHaveLength(0)
    await userEvent.click(screen.getByRole('checkbox', { name: /Disable future execution for exactly/ })); await userEvent.click(disable)
    await waitFor(() => expect(requests.state.account?.execution_enabled).toBe(false)); expect(requests.state.account?.kill_switch).toBe(true)
    expect(requests.state.account?.usage).toEqual(original.usage); expect(requests.state.account?.frozen).toBe(true); expect(requests.state.account?.pricing_verified).toBe(true)
    expect(screen.getByRole('checkbox', { name: /Disable future execution for exactly/ })).not.toBeChecked()
    expect(requests.writes.map((item) => item.path)).toEqual([`/api/v1/budget/accounts/${ACCOUNT_ID}/disable`])
  })
  it('clears protected budget data on operator expiry while distinguishing a publisher without budget role', async () => {
    let forbidden = false
    const requests = server((path) => path.includes('/accounts?') && forbidden ? Response.json({ detail: 'readiness_actor_forbidden' }, { status: 403 }) : undefined)
    const props = settings(); render(<BudgetWorkspace {...props} />); await ready(); await prepare(); forbidden = true
    await userEvent.click(screen.getByRole('button', { name: 'Reload budget inspection' }))
    expect(await screen.findByText(/This operator does not have current budget authority/)).toBeVisible()
    expect(screen.getByLabelText('Reviewed readiness proposal JSON')).toHaveValue('')
    expect(screen.queryByRole('region', { name: 'Exact disabled readiness preview' })).toBeNull()
    expect(props.onBrowserExpired).not.toHaveBeenCalled(); expect(props.onOperatorExpired).not.toHaveBeenCalled(); expect(requests.writes).toHaveLength(1)
  })
  it.each([[401, 'operator_session_required', 'operator'], [503, 'operator_unavailable', 'operator'], [403, 'csrf_check_failed', 'browser']] as const)('clears and delegates %s/%s expiry to the correct private scope', async (status, detail, expected) => {
    let expired = false; server((path) => expired && path.includes('/readiness?') ? Response.json({ detail }, { status }) : undefined)
    const props = settings(); render(<BudgetWorkspace {...props} />); await ready(); await prepare(); expired = true; await userEvent.click(screen.getByRole('button', { name: 'Reload budget inspection' }))
    await waitFor(() => expect(expected === 'operator' ? props.onOperatorExpired : props.onBrowserExpired).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('region', { name: 'Exact disabled readiness preview' })).toBeNull(); expect(screen.getByLabelText('Reviewed readiness proposal JSON')).toHaveValue('')
  })
})
