import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApplicationRouter } from '../ApplicationRouter'

const ID = 'A'.repeat(43)
const emptyMap = { scope: 'explicitly_shared_current_findings', nodes: [], edges: [], truncated: false, coverage: { max_findings: 60, max_nodes: 200, max_edges: 500 } }
afterEach(() => vi.unstubAllGlobals())

describe('public routes remain independent of the private research desk', () => {
  it('reads a shared handle with only a credential-free public GET and hides unavailable details', async () => {
    const fetch = vi.fn(async () => Response.json({ detail: 'sharing_not_found' }, { status: 404 })); vi.stubGlobal('fetch', fetch)
    render(<ApplicationRouter pathname={`/shared/${ID}`} />)
    expect(await screen.findByRole('heading', { name: 'This shared finding is unavailable.' })).toBeVisible()
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(fetch.mock.calls[0]).toEqual([`/api/v1/public/findings/${ID}`, expect.objectContaining({ method: 'GET', credentials: 'omit', cache: 'no-store' })])
    expect(screen.queryByRole('textbox', { name: 'What would you like to explore?' })).toBeNull()
  })
  it('fails closed for malformed public handles without bootstrapping a private browser session', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
    render(<ApplicationRouter pathname="/shared/not-a-public-handle" />)
    expect(await screen.findByRole('heading', { name: 'This shared finding is unavailable.' })).toBeVisible()
    expect(fetch).not.toHaveBeenCalled()
  })
  it('searches and reloads the public library and map with GETs only', async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL) => Response.json(input.toString().startsWith('/api/v1/public/map?') ? emptyMap : { items: [], next_cursor: null })); vi.stubGlobal('fetch', fetch)
    render(<ApplicationRouter pathname="/library" />)
    expect(await screen.findByRole('heading', { name: 'No shared findings match this search.' })).toBeVisible()
    await userEvent.type(screen.getByRole('textbox', { name: 'Search public finding text' }), 'alpha & beta')
    await userEvent.click(screen.getByRole('button', { name: 'Search public knowledge' }))
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(4))
    expect(fetch.mock.calls.slice(2).every(([path]) => new URL(path.toString(), 'https://polymath.test').searchParams.get('q') === 'alpha & beta')).toBe(true)
    await userEvent.click(screen.getByRole('button', { name: 'Reload public library' }))
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(6))
    for (const [path, options] of fetch.mock.calls as unknown as [string, RequestInit][]) { expect(path.startsWith('/api/v1/public/')).toBe(true); expect(options.method).toBe('GET'); expect(options.credentials).toBe('omit') }
    expect(screen.queryByRole('region', { name: 'Literature watches' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Explore connections' })).toBeNull()
  })
  it('aborts a public detail request when navigation changes and ignores its late response', async () => {
    let resolve!: (value: Response) => void, signal: AbortSignal | undefined
    const late = new Promise<Response>((done) => { resolve = done })
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, options?: RequestInit) => {
      if (input.toString().includes('/findings/')) { signal = options?.signal as AbortSignal; return late }
      return Promise.resolve(Response.json(input.toString().includes('/map?') ? emptyMap : { items: [], next_cursor: null }))
    }))
    const view = render(<ApplicationRouter pathname={`/shared/${ID}`} />)
    expect(screen.getByText('Loading the shared finding…')).toBeVisible()
    view.rerender(<ApplicationRouter pathname="/library" />)
    expect(signal?.aborted).toBe(true)
    resolve(Response.json({ detail: 'sharing_not_found' }, { status: 404 }))
    expect(await screen.findByRole('heading', { name: 'A wider field of shared thought.' })).toBeVisible()
    expect(await screen.findByRole('heading', { name: 'No shared findings match this search.' })).toBeVisible()
    expect(screen.queryByText('This shared finding is unavailable.')).toBeNull()
  })
  it('does not submit a model task when a public search form is submitted directly', async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL) => Response.json(input.toString().includes('/map?') ? emptyMap : { items: [], next_cursor: null })); vi.stubGlobal('fetch', fetch)
    render(<ApplicationRouter pathname="/library" />)
    await screen.findByRole('heading', { name: 'No shared findings match this search.' })
    fireEvent.submit(screen.getByRole('textbox').closest('form')!)
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(4))
    expect(fetch.mock.calls.every(([path]) => path.toString().startsWith('/api/v1/public/'))).toBe(true)
  })
})
