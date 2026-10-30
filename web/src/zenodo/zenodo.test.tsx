import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ARTIFACT, BROWSER_CSRF, artifactFixture } from '../publications/test-fixtures'
import { parsePublication } from './client'
import type { ZenodoStatus } from './client'
import { PublishToZenodo } from './PublishToZenodo'
import { navigation, YourZenodo } from './YourZenodo'

const RECORD = '77777777-7777-4777-8777-777777777777'
const AUTHORIZE = 'https://sandbox.zenodo.org/oauth/authorize?response_type=code&client_id=synthetic&state=synthetic-state'

function status(sandbox = true, production = false): ZenodoStatus {
  return { available: true, daily_publications: 10, destinations: [
    { destination: 'sandbox', connected: sandbox, connected_at: sandbox ? '2026-10-30T00:00:00Z' : null },
    { destination: 'production', connected: production, connected_at: production ? '2026-10-30T00:00:00Z' : null },
  ] }
}

function record(changes: Record<string, unknown> = {}) {
  return { id: RECORD, artifact_id: ARTIFACT, destination: 'sandbox', status: 'preview', manifest_sha256: 'd'.repeat(64), title: 'A synthetic shared structure', creators: [{ name: 'Synthetic Visitor', affiliation: null, orcid: null }], description: 'Synthetic summary.', keywords: [], license: 'cc-by-4.0', file_name: `polymath-${ARTIFACT}.md`, markdown_sha256: 'e'.repeat(64), doi: null, doi_url: null, record_url: null, error: null, created_at: '2026-10-30T00:00:00Z', published_at: null, ...changes }
}

interface Call { method: string; url: string; body: unknown }
function server(routes: (call: Call) => unknown) {
  const calls: Call[] = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const call = { method: init?.method ?? 'GET', url: String(input), body: init?.body ? JSON.parse(String(init.body)) : null }
    calls.push(call)
    const value = routes(call)
    if (value === undefined) throw new Error(`Unexpected synthetic request ${call.method} ${call.url}`)
    return Response.json(value)
  }))
  return calls
}

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); window.history.replaceState(null, '', '/'); window.localStorage.clear() })

describe('Zenodo client', () => {
  it('keeps only real Zenodo and DOI links', () => {
    expect(parsePublication(record({ record_url: 'https://zenodo.org/records/1', doi_url: 'https://doi.org/10.5281/zenodo.1' })).record_url).toBe('https://zenodo.org/records/1')
    expect(parsePublication(record({ record_url: 'https://zenodo.org.evil.example/records/1' })).record_url).toBeNull()
    expect(parsePublication(record({ doi_url: 'javascript:alert(1)' })).doi_url).toBeNull()
    expect(() => parsePublication(record({ status: 'approved' }))).toThrow()
  })
})

describe('Your Zenodo', () => {
  it('shows nothing where the site has not switched it on', async () => {
    const calls = server(() => ({ available: false, destinations: [], daily_publications: 0 }))
    const { container } = render(<YourZenodo csrfToken={BROWSER_CSRF} enabled />)
    await waitFor(() => expect(calls.length).toBe(1))
    expect(container).toBeEmptyDOMElement()
  })

  it('connects by leaving for Zenodo’s own sign-in, and disconnects in one tap', async () => {
    const assign = vi.spyOn(navigation, 'assign').mockImplementation(() => undefined)
    const calls = server((call) => {
      if (call.url === '/api/v1/zenodo/status') return status(false)
      if (call.url === '/api/v1/zenodo/publications') return { publications: [] }
      if (call.url === '/api/v1/zenodo/connect') return { authorize_url: AUTHORIZE }
      if (call.url === '/api/v1/zenodo/connections/sandbox' && call.method === 'DELETE') return status(false)
      return undefined
    })
    render(<YourZenodo csrfToken={BROWSER_CSRF} enabled />)
    expect(await screen.findByRole('heading', { name: 'Your Zenodo' })).toBeInTheDocument()
    expect(screen.getByText(/never sees your password/)).toBeInTheDocument()
    await userEvent.click(screen.getAllByRole('button', { name: 'Connect' })[0])
    await waitFor(() => expect(assign).toHaveBeenCalledWith(new URL(AUTHORIZE).toString()))
    expect(calls.find((call) => call.url === '/api/v1/zenodo/connect')?.body).toEqual({ destination: 'sandbox' })
  })

  it('refuses an authorisation link that does not lead to Zenodo', async () => {
    const assign = vi.spyOn(navigation, 'assign').mockImplementation(() => undefined)
    server((call) => {
      if (call.url === '/api/v1/zenodo/status') return status(false)
      if (call.url === '/api/v1/zenodo/publications') return { publications: [] }
      if (call.url === '/api/v1/zenodo/connect') return { authorize_url: 'https://evil.example/oauth/authorize' }
      return undefined
    })
    render(<YourZenodo csrfToken={BROWSER_CSRF} enabled />)
    await userEvent.click((await screen.findAllByRole('button', { name: 'Connect' }))[0])
    expect(await screen.findByText(/could not be reached/)).toBeInTheDocument()
    expect(assign).not.toHaveBeenCalled()
  })

  it('finishes the connection when Zenodo sends the visitor back, and clears the code from the address bar', async () => {
    window.history.replaceState(null, '', '/zenodo/callback?code=synthetic-code&state=synthetic-state-0123456789')
    const calls = server((call) => {
      if (call.url === '/api/v1/zenodo/complete') return { destination: 'sandbox', connected: true }
      if (call.url === '/api/v1/zenodo/status') return status(true)
      if (call.url === '/api/v1/zenodo/publications') return { publications: [] }
      return undefined
    })
    render(<YourZenodo csrfToken={BROWSER_CSRF} enabled />)
    expect(window.location.pathname).toBe('/your-research')
    expect(window.location.search).toBe('')
    expect(await screen.findByText(/Connected to Zenodo sandbox/)).toBeInTheDocument()
    expect(calls.find((call) => call.url === '/api/v1/zenodo/complete')?.body).toEqual({ code: 'synthetic-code', state: 'synthetic-state-0123456789' })
  })

  it('says so when the visitor cancels on Zenodo, and connects nothing', async () => {
    window.history.replaceState(null, '', '/zenodo/callback?error=access_denied&state=synthetic-state-0123456789')
    const calls = server((call) => call.url === '/api/v1/zenodo/status' ? status(false) : call.url === '/api/v1/zenodo/publications' ? { publications: [] } : undefined)
    render(<YourZenodo csrfToken={BROWSER_CSRF} enabled />)
    await screen.findByRole('heading', { name: 'Your Zenodo' })
    expect(screen.getByText('Zenodo sign-in was cancelled, so nothing was connected.')).toBeInTheDocument()
    expect(calls.some((call) => call.url.endsWith('/complete'))).toBe(false)
  })

  it('lists published records with their DOI and offers Try again only when it helps', async () => {
    server((call) => {
      if (call.url === '/api/v1/zenodo/status') return status(true)
      if (call.url === '/api/v1/zenodo/publications') return { publications: [
        record({ status: 'published', doi: '10.5072/zenodo.100', doi_url: 'https://doi.org/10.5072/zenodo.100', record_url: 'https://sandbox.zenodo.org/records/100', published_at: '2026-10-30T00:00:00Z' }),
        record({ id: '66666666-6666-4666-8666-666666666666', status: 'missing', title: 'A second synthetic record' }),
        record({ id: '55555555-5555-4555-8555-555555555555', status: 'preview', title: 'An unreviewed preview' }),
      ] }
      return undefined
    })
    render(<YourZenodo csrfToken={BROWSER_CSRF} enabled />)
    expect(await screen.findByText('Zenodo sandbox · DOI 10.5072/zenodo.100')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open on Zenodo' })).toHaveAttribute('href', 'https://sandbox.zenodo.org/records/100')
    expect(screen.getByText(/Nothing reached Zenodo, so nothing was published/)).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Try again' })).toHaveLength(1)
    expect(screen.queryByText('An unreviewed preview')).not.toBeInTheDocument()
  })
})

describe('Publish to your Zenodo', () => {
  it('asks a visitor without a connection to connect first', async () => {
    server((call) => call.url.startsWith('/api/v1/zenodo/publications?') ? { publications: [] } : undefined)
    render(<PublishToZenodo artifact={artifactFixture()} csrfToken={BROWSER_CSRF} status={status(false)} sourceCurrent />)
    await userEvent.click(screen.getByRole('button', { name: 'Publish this…' }))
    expect(screen.getByRole('button', { name: 'Connect Zenodo sandbox' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Connect Zenodo' })).toBeInTheDocument()
  })

  it('reviews the exact record, publishes it, and shows the DOI when Zenodo confirms', async () => {
    let published = false
    const calls = server((call) => {
      if (call.url.startsWith('/api/v1/zenodo/publications?')) return { publications: [] }
      if (call.url === '/api/v1/zenodo/publications' && call.method === 'POST') return record()
      if (call.url === `/api/v1/zenodo/publications/${RECORD}/publish`) return record({ status: 'queued' })
      if (call.url === `/api/v1/zenodo/publications/${RECORD}`) { const done = published; published = true; return done ? record({ status: 'published', doi: '10.5072/zenodo.100', doi_url: 'https://doi.org/10.5072/zenodo.100', record_url: 'https://sandbox.zenodo.org/records/100', published_at: '2026-10-30T00:00:00Z' }) : record({ status: 'running' }) }
      return undefined
    })
    render(<PublishToZenodo artifact={artifactFixture()} csrfToken={BROWSER_CSRF} status={status(true)} sourceCurrent />)
    await userEvent.click(screen.getByRole('button', { name: 'Publish this…' }))
    expect(screen.getByRole('button', { name: 'Review the record' })).toBeDisabled()
    await userEvent.type(screen.getByLabelText('Your name'), 'Synthetic Visitor')
    await userEvent.click(screen.getByRole('button', { name: 'Review the record' }))
    expect(await screen.findByRole('heading', { name: 'A synthetic shared structure' })).toBeInTheDocument()
    const preview = calls.find((call) => call.method === 'POST' && call.url === '/api/v1/zenodo/publications')
    expect(preview?.body).toMatchObject({ artifact_id: ARTIFACT, destination: 'sandbox', metadata: { creators: [{ name: 'Synthetic Visitor', affiliation: null, orcid: null }], license: 'cc-by-4.0' } })
    await userEvent.click(screen.getByRole('button', { name: 'Publish' }))
    await waitFor(() => expect(calls.find((call) => call.url.endsWith('/publish'))?.body).toEqual({ manifest_sha256: 'd'.repeat(64), confirm_permanent: false }))
    expect(await screen.findByRole('link', { name: 'DOI 10.5072/zenodo.100' }, { timeout: 8000 })).toHaveAttribute('href', 'https://sandbox.zenodo.org/records/100')
    expect(JSON.parse(window.localStorage.getItem('polymath.zenodo.author') ?? '{}')).toEqual({ name: 'Synthetic Visitor', orcid: '' })
  }, 12000)

  it('asks once more before a permanent record, and sends nothing until the visitor agrees', async () => {
    const calls = server((call) => {
      if (call.url.startsWith('/api/v1/zenodo/publications?')) return { publications: [] }
      if (call.url === '/api/v1/zenodo/publications' && call.method === 'POST') return record({ destination: 'production' })
      if (call.url.endsWith('/publish')) return record({ destination: 'production', status: 'queued' })
      if (call.url === `/api/v1/zenodo/publications/${RECORD}`) return record({ destination: 'production', status: 'running' })
      return undefined
    })
    window.localStorage.setItem('polymath.zenodo.author', JSON.stringify({ name: 'Synthetic Visitor', orcid: '0000-0002-1825-0097' }))
    render(<PublishToZenodo artifact={artifactFixture()} csrfToken={BROWSER_CSRF} status={status(false, true)} sourceCurrent />)
    await userEvent.click(screen.getByRole('button', { name: 'Publish this…' }))
    expect(screen.getByLabelText('Your name')).toHaveValue('Synthetic Visitor')
    expect(screen.getByRole('radio', { name: /Practice/ })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Review the record' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Publish permanently' }))
    expect(screen.getByRole('alertdialog', { name: 'Publish permanently?' })).toBeInTheDocument()
    expect(calls.some((call) => call.url.endsWith('/publish'))).toBe(false)
    await userEvent.click(screen.getByRole('button', { name: 'Yes, publish' }))
    await waitFor(() => expect(calls.find((call) => call.url.endsWith('/publish'))?.body).toEqual({ manifest_sha256: 'd'.repeat(64), confirm_permanent: true }))
  })

  it('will not prepare a record from a finding that has changed, or with a malformed ORCID', async () => {
    server((call) => call.url.startsWith('/api/v1/zenodo/publications?') ? { publications: [] } : undefined)
    const { rerender } = render(<PublishToZenodo artifact={artifactFixture()} csrfToken={BROWSER_CSRF} status={status(true)} sourceCurrent={false} />)
    await userEvent.click(screen.getByRole('button', { name: 'Publish this…' }))
    await userEvent.type(screen.getByLabelText('Your name'), 'Synthetic Visitor')
    expect(screen.getByRole('alert')).toHaveTextContent('This finding has changed')
    expect(screen.getByRole('button', { name: 'Review the record' })).toBeDisabled()
    rerender(<PublishToZenodo artifact={artifactFixture()} csrfToken={BROWSER_CSRF} status={status(true)} sourceCurrent />)
    await userEvent.type(screen.getByLabelText('ORCID (optional)'), '0000-0002-1825-0098')
    expect(screen.getByText(/That ORCID does not look right/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Review the record' })).toBeDisabled()
  })
})
