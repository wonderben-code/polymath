import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BudgetDesk } from './BudgetDesk'
import { operatorFixture, BROWSER_CSRF } from '../publications/test-fixtures'

vi.mock('./BudgetWorkspace', () => ({ BudgetWorkspace: ({ authenticated, onBrowserExpired, onOperatorExpired }: { authenticated: boolean; onBrowserExpired: () => void; onOperatorExpired: () => void }) => <section>{authenticated && <p>Private synthetic budget information</p>}<button type="button" onClick={onBrowserExpired}>Synthetic browser expiry</button><button type="button" onClick={onOperatorExpired}>Synthetic operator expiry</button></section> }))

function mockAccess({ configured = true, authenticated = false, delayedLogout = false } = {}) {
  let resolveLogout!: (response: Response) => void
  const pendingLogout = new Promise<Response>((resolve) => { resolveLogout = resolve })
  const fetch = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = input.toString()
    if (path === '/api/v1/session') return Response.json({ csrf_token: BROWSER_CSRF, expires_at: '2100-01-01T00:00:00Z' })
    if (path === '/api/v1/operator/session') return Response.json(operatorFixture(options?.method === 'POST' || authenticated, configured))
    if (path === '/api/v1/operator/logout') return delayedLogout ? pendingLogout : Response.json(operatorFixture())
    throw new Error('Unexpected synthetic operator route')
  }); vi.stubGlobal('fetch', fetch)
  return { fetch, resolveLogout }
}
afterEach(() => vi.unstubAllGlobals())

describe('isolated operator budget access', () => {
  it('opens only browser/operator access without hydrating jobs or investigations', async () => {
    const { fetch } = mockAccess({ configured: false })
    render(<BudgetDesk />)
    expect(await screen.findByText('Operator access has not been configured on this server.')).toBeVisible()
    expect(screen.queryByRole('textbox', { name: 'Operator access code' })).toBeNull()
    expect(fetch.mock.calls.map(([path]) => path)).toEqual(['/api/v1/session', '/api/v1/operator/session'])
    expect(screen.queryByRole('button', { name: 'Explore connections' })).toBeNull()
    expect(screen.getByText(/Publisher access alone grants no budget authority/)).toBeVisible()
  })
  it('erases an ephemeral login credential immediately and makes no budget or model action by logging in', async () => {
    const { fetch } = mockAccess(); render(<BudgetDesk />)
    const input = await screen.findByLabelText('Operator access code')
    expect(input).toHaveAttribute('type', 'password')
    expect(input).toHaveValue('')
    await userEvent.type(input, 'synthetic-budget-access-code')
    await userEvent.click(screen.getByRole('button', { name: 'Unlock operator access' }))
    expect(await screen.findByText('Private synthetic budget information')).toBeVisible()
    const writes = fetch.mock.calls.filter(([, options]) => options?.method === 'POST')
    expect(writes).toHaveLength(1)
    expect(writes[0]).toEqual(['/api/v1/operator/session', expect.objectContaining({ body: JSON.stringify({ token: 'synthetic-budget-access-code' }), headers: expect.objectContaining({ 'X-CSRF-Token': BROWSER_CSRF }) })])
    expect(screen.queryByDisplayValue('synthetic-budget-access-code')).toBeNull()
    expect(localStorage.getItem('synthetic-budget-access-code')).toBeNull()
    expect(sessionStorage.getItem('synthetic-budget-access-code')).toBeNull()
  })
  it('clears privileged budget content immediately when locking access, before a delayed logout receipt', async () => {
    const { fetch, resolveLogout } = mockAccess({ authenticated: true, delayedLogout: true }); render(<BudgetDesk />)
    await screen.findByText('Private synthetic budget information')
    await userEvent.click(screen.getByRole('button', { name: 'Lock operator access' }))
    expect(screen.queryByText('Private synthetic budget information')).toBeNull()
    expect(await screen.findByLabelText('Operator access code')).toHaveValue('')
    await waitFor(() => expect(fetch.mock.calls.some(([path]) => path === '/api/v1/operator/logout')).toBe(true))
    resolveLogout(Response.json(operatorFixture()))
  })
  it('clears operator-only private scope while retaining a browser session for deliberate sign-in', async () => {
    const { fetch } = mockAccess({ authenticated: true }); render(<BudgetDesk />); await screen.findByText('Private synthetic budget information')
    await userEvent.click(screen.getByRole('button', { name: 'Synthetic operator expiry' }))
    expect(screen.queryByText('Private synthetic budget information')).toBeNull()
    expect(screen.getByLabelText('Operator access code')).toHaveValue('')
    expect(screen.getByText(/Operator access expired or changed/)).toBeVisible()
    expect(fetch.mock.calls.filter(([path]) => path === '/api/v1/session')).toHaveLength(1)
  })
  it('clears all private scope on browser expiry and requires an explicit access reload', async () => {
    const { fetch } = mockAccess({ authenticated: true }); render(<BudgetDesk />); await screen.findByText('Private synthetic budget information')
    await userEvent.click(screen.getByRole('button', { name: 'Synthetic browser expiry' }))
    expect(screen.queryByText('Private synthetic budget information')).toBeNull()
    expect(screen.queryByLabelText('Operator access code')).toBeNull()
    expect(screen.getByText(/Private budget data and unsaved review inputs have been cleared/)).toBeVisible()
    expect(fetch).toHaveBeenCalledTimes(2)
  })
})
