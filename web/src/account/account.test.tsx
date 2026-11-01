import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AccountControl, accountNavigation } from './AccountControl'
import { parseAccount } from './client'

const CSRF = 'a'.repeat(64)
const AUTHORIZE = 'https://github.com/login/oauth/authorize?client_id=synthetic&state=synthetic-state'
interface Call { method: string; url: string; body: unknown }
function server(routes: (call: Call) => unknown) {
  const calls: Call[] = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const call = { method: init?.method ?? 'GET', url: String(input), body: init?.body ? JSON.parse(String(init.body)) : null }
    calls.push(call)
    const value = routes(call)
    if (value === undefined) throw new Error(`Unexpected synthetic request ${call.url}`)
    return Response.json(value)
  }))
  return calls
}
const guest = { available: true, providers: ['orcid', 'github', 'google'], signed_in: false, account: null }
const member = { available: true, providers: ['orcid', 'github', 'google'], signed_in: true, account: { display_name: 'Josiah Carberry', provider: 'orcid', orcid: '0000-0002-1825-0097' } }

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); window.history.replaceState(null, '', '/'); window.localStorage.clear(); window.sessionStorage.clear() })

describe('accounts', () => {
  it('rejects a malformed account reply', () => {
    expect(() => parseAccount({ ...member, account: { ...member.account, orcid: 'not-an-orcid' } })).toThrow()
    expect(() => parseAccount({ ...guest, providers: ['myspace'] })).toThrow()
  })

  it('stays out of the way where accounts are off', async () => {
    const calls = server(() => ({ available: false, providers: [], signed_in: false, account: null }))
    const { container } = render(<AccountControl csrfToken={CSRF} enabled />)
    await waitFor(() => expect(calls.length).toBe(1))
    expect(container).toBeEmptyDOMElement()
  })

  it('offers each provider and makes plain that guests lose nothing', async () => {
    const assign = vi.spyOn(accountNavigation, 'assign').mockImplementation(() => undefined)
    window.history.replaceState(null, '', '/your-research')
    const calls = server((call) => call.url === '/api/v1/account' ? guest : call.url === '/api/v1/account/sign-in' ? { authorize_url: AUTHORIZE } : undefined)
    render(<AccountControl csrfToken={CSRF} enabled />)
    await userEvent.click(await screen.findByRole('button', { name: 'Sign in' }))
    const panel = screen.getByRole('dialog', { name: 'Keep your work, on any device' })
    expect(panel).toHaveTextContent('never your password')
    expect(panel).toHaveTextContent('carry on as a guest')
    expect(screen.getByRole('button', { name: /Continue with ORCID/ })).toHaveTextContent('Adds your ORCID')
    await userEvent.click(screen.getByRole('button', { name: 'Continue with GitHub' }))
    await waitFor(() => expect(assign).toHaveBeenCalledWith(AUTHORIZE))
    expect(calls.find((call) => call.url.endsWith('/sign-in'))?.body).toEqual({ provider: 'github' })
    expect(window.sessionStorage.getItem('polymath.account.return')).toBe('/your-research')
  })

  it('refuses a sign-in link that leads somewhere else', async () => {
    const assign = vi.spyOn(accountNavigation, 'assign').mockImplementation(() => undefined)
    server((call) => call.url === '/api/v1/account' ? guest : call.url === '/api/v1/account/sign-in' ? { authorize_url: 'https://evil.example/login' } : undefined)
    render(<AccountControl csrfToken={CSRF} enabled />)
    await userEvent.click(await screen.findByRole('button', { name: 'Sign in' }))
    await userEvent.click(screen.getByRole('button', { name: 'Continue with GitHub' }))
    expect(await screen.findByRole('status')).toHaveTextContent('carry on as a guest')
    expect(assign).not.toHaveBeenCalled()
  })

  it('finishes signing in when the provider sends the visitor back, then reopens where they were', async () => {
    const replace = vi.spyOn(accountNavigation, 'replace').mockImplementation(() => undefined)
    window.sessionStorage.setItem('polymath.account.return', '/your-research')
    window.history.replaceState(null, '', '/account/callback?code=synthetic-code&state=synthetic-state-0123456789')
    const calls = server((call) => call.url === '/api/v1/account/complete' ? { result: 'created', signed_in: true } : undefined)
    render(<AccountControl csrfToken={CSRF} enabled />)
    expect(window.location.search).toBe('')
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/your-research'))
    expect(calls[0].body).toEqual({ code: 'synthetic-code', state: 'synthetic-state-0123456789' })
  })

  it('says so when sign-in is cancelled, and changes nothing', async () => {
    window.history.replaceState(null, '', '/account/callback?error=access_denied')
    const calls = server((call) => call.url === '/api/v1/account' ? guest : undefined)
    render(<AccountControl csrfToken={CSRF} enabled />)
    expect(await screen.findByText(/Sign-in was cancelled, so nothing changed/)).toBeInTheDocument()
    expect(calls.some((call) => call.url.endsWith('/complete'))).toBe(false)
  })

  it('shows who is signed in, fills their ORCID for publishing, and signs out or deletes only when asked', async () => {
    const replace = vi.spyOn(accountNavigation, 'replace').mockImplementation(() => undefined)
    const calls = server((call) => call.url === '/api/v1/account' ? member : call.url.endsWith('/sign-out') || call.url.endsWith('/delete') ? { signed_out: true } : undefined)
    render(<AccountControl csrfToken={CSRF} enabled />)
    await userEvent.click(await screen.findByRole('button', { name: 'Josiah Carberry' }))
    expect(screen.getByRole('dialog')).toHaveTextContent('ORCID 0000-0002-1825-0097')
    expect(JSON.parse(window.localStorage.getItem('polymath.zenodo.author') ?? '{}')).toEqual({ name: 'Josiah Carberry', orcid: '0000-0002-1825-0097' })
    await userEvent.click(screen.getByRole('button', { name: 'Delete my account' }))
    expect(screen.getByText(/Your work stays in this browser as a guest for 7 days/)).toBeInTheDocument()
    expect(calls.some((call) => call.url.endsWith('/delete'))).toBe(false)
    await userEvent.click(screen.getByRole('button', { name: 'Keep it' }))
    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }))
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/'))
    expect(calls.some((call) => call.url.endsWith('/sign-out'))).toBe(true)
  })
})
