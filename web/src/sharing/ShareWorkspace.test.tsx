import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ShareWorkspace } from './ShareWorkspace'
import { capabilities, CSRF, HASH, preview, projection, PUBLIC_ID, RESULT_ID, shareRecord } from './test-support'
import type { CreateShare, ShareRecord } from './types'

type Server = (path: string, options?: RequestInit) => Promise<Response | undefined> | Response | undefined
function server(handle?: Server) {
  const writes: { path: string; body: CreateShare | null; options: RequestInit }[] = []
  const fetch = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = input.toString()
    if (options?.method === 'POST') writes.push({ path, body: options.body ? JSON.parse(options.body.toString()) as CreateShare : null, options })
    const custom = handle && await handle(path, options)
    if (custom) return custom
    if (path === '/api/v1/shares/capabilities') return Response.json(capabilities())
    if (path.startsWith('/api/v1/shares/preview?')) return Response.json(preview())
    if (path.startsWith('/api/v1/shares?')) return Response.json({ shares: [], next_cursor: null })
    throw new Error('Unexpected synthetic sharing route')
  }); vi.stubGlobal('fetch', fetch)
  return { fetch, writes }
}
function props() { return { sourceResultId: RESULT_ID, enabled: true, csrfToken: CSRF, sourceCurrent: true, onSessionExpired: vi.fn() } }
async function ready() { await screen.findByText('No public snapshot has been shared for this exact finding.') }
afterEach(() => vi.unstubAllGlobals())

describe('explicit owner public sharing', () => {
  it('defaults to disabled sharing while retaining owned inspection and revocation', async () => {
    const requests = server(async (path) => path.endsWith('/capabilities') ? Response.json(capabilities(false)) : path.startsWith('/api/v1/shares?') ? Response.json({ shares: [shareRecord()], next_cursor: null }) : undefined as never)
    render(<ShareWorkspace {...props()} />)
    expect(await screen.findByText(/New public sharing is disabled on this server/)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Preview public snapshot' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Revoke this public link' })).toBeEnabled()
    await userEvent.click(screen.getByRole('button', { name: 'Inspect shared snapshot' }))
    expect(screen.getByRole('region', { name: 'Saved public snapshot inspection' })).toBeVisible()
    expect(requests.writes).toHaveLength(0)
  })
  it('shows the exact Ask question, classifications and all citations before a single hash-bound explicit grant', async () => {
    const answer = projection('answer'), exact = preview({ projection: answer }); let resolve!: (response: Response) => void
    const response = new Promise<Response>((done) => { resolve = done })
    const requests = server((path, options) => path.startsWith('/api/v1/shares/preview?') ? Response.json(exact) : options?.method === 'POST' ? response : undefined as never)
    render(<ShareWorkspace {...props()} />); await ready()
    expect(requests.writes).toHaveLength(0)
    await userEvent.click(screen.getByRole('button', { name: 'Preview public snapshot' }))
    const review = await screen.findByRole('region', { name: 'Exact public sharing preview' })
    expect(within(review).getByRole('heading', { name: 'What does the public synthetic evidence establish?' })).toBeVisible()
    for (const label of ['known', 'postulated', 'speculative']) expect(within(review).getByText(`${label} · model label`)).toBeVisible()
    expect(within(review).getByRole('list', { name: 'Public saved citation excerpts' }).children).toHaveLength(4)
    expect(within(review).getByText(exact.projection_sha256)).toBeVisible()
    expect(within(review).getByText(/Copies already saved by viewers cannot be recalled/)).toBeVisible()
    expect(requests.writes).toHaveLength(0)
    const share = screen.getByRole('button', { name: 'Share this snapshot publicly' })
    fireEvent.click(share); fireEvent.click(share)
    await waitFor(() => expect(requests.writes).toHaveLength(1))
    const body = requests.writes[0].body!
    expect(body).toEqual({ source_result_id: RESULT_ID, expected_projection_sha256: exact.projection_sha256, idempotency_key: expect.stringMatching(/^[a-f\d-]{36}$/) })
    expect(requests.writes[0].options.headers).toMatchObject({ 'X-CSRF-Token': CSRF })
    resolve(Response.json(shareRecord({ projection: answer, projection_sha256: exact.projection_sha256, idempotency_key: body.idempotency_key })))
    await screen.findByRole('button', { name: 'Revoke this public link' })
    expect(requests.writes).toHaveLength(1)
    expect(screen.getByRole('link', { name: new RegExp(`/shared/${PUBLIC_ID}`) })).toHaveAttribute('href', `/shared/${PUBLIC_ID}`)
  })
  it('retains the original unknown-write request and key until an explicit same-action check resolves it', async () => {
    let posts = 0
    const requests = server((path, options) => {
      if (path === '/api/v1/shares' && options?.method === 'POST') { posts++; if (posts === 1) throw new TypeError('Synthetic response loss'); const body = JSON.parse(options.body!.toString()) as CreateShare; return Response.json(shareRecord({ idempotency_key: body.idempotency_key })) }
      return undefined as never
    })
    render(<ShareWorkspace {...props()} />); await ready(); await userEvent.click(screen.getByRole('button', { name: 'Preview public snapshot' })); await screen.findByRole('button', { name: 'Share this snapshot publicly' })
    await userEvent.click(screen.getByRole('button', { name: 'Share this snapshot publicly' }))
    const check = await screen.findByRole('button', { name: 'Check original share action' })
    expect(screen.getByRole('button', { name: 'Preview public snapshot' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Share this snapshot publicly' })).toBeDisabled()
    expect(requests.writes).toHaveLength(1)
    await userEvent.click(check); await screen.findByRole('button', { name: 'Revoke this public link' })
    expect(requests.writes).toHaveLength(2)
    expect(requests.writes[1].body).toEqual(requests.writes[0].body)
  })
  it('recovers a committed lost response by reading owner history without a second POST', async () => {
    let saved: ShareRecord | null = null
    const requests = server((path, options) => {
      if (path === '/api/v1/shares' && options?.method === 'POST') { const body = JSON.parse(options.body!.toString()) as CreateShare; saved = shareRecord({ idempotency_key: body.idempotency_key }); throw new TypeError('Synthetic committed response loss') }
      if (path.startsWith('/api/v1/shares?')) return Response.json({ shares: saved ? [saved] : [], next_cursor: null })
      return undefined as never
    })
    render(<ShareWorkspace {...props()} />); await ready(); await userEvent.click(screen.getByRole('button', { name: 'Preview public snapshot' })); await userEvent.click(await screen.findByRole('button', { name: 'Share this snapshot publicly' }))
    expect(await screen.findByText('The saved share was recovered. No second public link was created.')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Check original share action' })).toBeNull()
    expect(requests.writes).toHaveLength(1)
  })
  it('clears preview and owned records on browser expiry and does not display a delayed old-session preview', async () => {
    let expire = false
    const requests = server((path) => path.startsWith('/api/v1/shares?') ? Response.json(expire ? { detail: 'research_session_required' } : { shares: [], next_cursor: null }, { status: expire ? 401 : 200 }) : undefined as never)
    const settings = props(); render(<ShareWorkspace {...settings} />); await ready(); await userEvent.click(screen.getByRole('button', { name: 'Preview public snapshot' })); await screen.findByRole('region', { name: 'Exact public sharing preview' })
    expire = true; await userEvent.click(screen.getByRole('button', { name: 'Reload sharing records' }))
    expect(await screen.findByText(/Private sharing previews and records have been cleared/)).toBeVisible()
    expect(screen.queryByRole('article', { name: 'Exact public finding snapshot' })).toBeNull()
    expect(settings.onSessionExpired).toHaveBeenCalledTimes(1)
    expect(requests.writes).toHaveLength(0)
  })
  it('aborts old-scope reads and ignores their private preview after CSRF rotation', async () => {
    let resolve!: (response: Response) => void, signal: AbortSignal | undefined
    const late = new Promise<Response>((done) => { resolve = done })
    const requests = server((path, options) => { if (path.includes('/preview?')) { signal = options?.signal as AbortSignal; return late }; return undefined as never })
    const settings = props(), view = render(<ShareWorkspace {...settings} />); await ready(); await userEvent.click(screen.getByRole('button', { name: 'Preview public snapshot' }))
    view.rerender(<ShareWorkspace {...settings} csrfToken={'b'.repeat(64)} />)
    expect(signal?.aborted).toBe(true); resolve(Response.json(preview())); await ready()
    expect(screen.queryByRole('region', { name: 'Exact public sharing preview' })).toBeNull()
    expect(requests.writes).toHaveLength(0)
  })
  it('allows revoking an unsupported historical snapshot while blocking fresh sharing', async () => {
    const requests = server((path, options) => path.startsWith('/api/v1/shares?') ? Response.json({ shares: [shareRecord({ status: 'unsupported' })], next_cursor: null }) : options?.method === 'POST' ? Response.json(shareRecord({ status: 'revoked' })) : undefined as never)
    render(<ShareWorkspace {...props()} sourceCurrent={false} />)
    await screen.findByRole('button', { name: 'Revoke this public link' })
    expect(screen.getByRole('button', { name: 'Preview public snapshot' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Revoke this public link' }))
    expect(await screen.findByText('Revoked', { selector: '.sharing-status' })).toBeVisible()
    expect(requests.writes.map((write) => write.path)).toEqual([`/api/v1/shares/${PUBLIC_ID}/revoke`])
    expect(requests.writes[0].options.headers).not.toHaveProperty('X-Operator-CSRF-Token')
  })
  it('requires a fresh exact preview after the server reports a changed projection', async () => {
    const requests = server((_path, options) => options?.method === 'POST' ? Response.json({ detail: 'sharing_projection_changed' }, { status: 409 }) : undefined as never)
    render(<ShareWorkspace {...props()} />); await ready(); await userEvent.click(screen.getByRole('button', { name: 'Preview public snapshot' })); await userEvent.click(await screen.findByRole('button', { name: 'Share this snapshot publicly' }))
    expect(await screen.findByText(/Load and review a fresh preview before sharing/)).toBeVisible()
    expect(screen.queryByRole('region', { name: 'Exact public sharing preview' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Preview public snapshot' })).toBeEnabled()
    expect(requests.writes[0].body?.expected_projection_sha256).toBe(HASH)
  })
})
