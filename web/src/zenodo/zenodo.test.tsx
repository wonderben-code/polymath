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

describe('Publishing levels on your own Zenodo', () => {
  const ITEM = '88888888-8888-4888-8888-888888888888'
  function prefs(changes: Record<string, unknown> = {}) { return { level: 1, draft_destination: 'sandbox', write_kind: 'write_paper', full_text_only: false, daily_cap: 3, creators: [], license: 'cc-by-4.0', enabled_at: null, production_confirmed: false, ...changes } }
  function item(changes: Record<string, unknown> = {}) { return { id: ITEM, result_id: '99999999-9999-4999-8999-999999999999', title: 'A synthetic finding', level: 2, destination: 'sandbox', write_kind: 'write_paper', status: 'ready', reason: null, artifact_id: ARTIFACT, publication_id: RECORD, manifest_sha256: 'd'.repeat(64), doi: null, record_url: null, created_at: '2026-11-01T00:00:00Z', updated_at: '2026-11-01T00:00:00Z', ...changes } }

  it('offers only the levels your connections allow, and asks once before publishing for real', async () => {
    let saved = prefs()
    const calls = server((call) => {
      if (call.url === '/api/v1/zenodo/status') return status(true, true)
      if (call.url === '/api/v1/zenodo/publications') return { publications: [] }
      if (call.url === '/api/v1/zenodo/autopilot/preferences' && call.method === 'POST') { const body = call.body as Record<string, unknown>; saved = prefs({ ...body, confirm_production: undefined, production_confirmed: body.confirm_production === true }); return saved }
      if (call.url === '/api/v1/zenodo/autopilot/preferences') return saved
      if (call.url === '/api/v1/zenodo/autopilot/items') return { items: [] }
      if (call.url === '/api/v1/zenodo/autopilot/next') return { action: 'none', reason: 'nothing_new' }
      return undefined
    })
    render(<YourZenodo csrfToken={BROWSER_CSRF} enabled />)
    const group = await screen.findByRole('group', { name: 'How much should Polymath do?' })
    expect(group).toHaveTextContent('Ask me every time')
    await userEvent.click(screen.getByRole('radio', { name: /Publish for real/ }))
    expect(screen.getByRole('button', { name: 'Save this choice' })).toBeDisabled()
    await userEvent.type(screen.getByLabelText('Your name, on every record'), 'Synthetic Visitor')
    await userEvent.click(screen.getByRole('button', { name: 'Save this choice' }))
    expect(screen.getByRole('alertdialog', { name: 'Publish permanently, on your behalf?' })).toBeInTheDocument()
    expect(calls.some((call) => call.method === 'POST' && call.url.endsWith('/preferences'))).toBe(false)
    await userEvent.click(screen.getByRole('button', { name: 'Yes, publish for real' }))
    await waitFor(() => expect(calls.find((call) => call.method === 'POST' && call.url.endsWith('/preferences'))?.body).toMatchObject({ level: 4, confirm_production: true, creators: [{ name: 'Synthetic Visitor', affiliation: null, orcid: null }] }))
    await waitFor(() => expect(calls.some((call) => call.url === '/api/v1/zenodo/autopilot/next')).toBe(true))
  })

  it('keeps the practice and real levels closed without those connections', async () => {
    server((call) => {
      if (call.url === '/api/v1/zenodo/status') return status(true, false)
      if (call.url === '/api/v1/zenodo/publications') return { publications: [] }
      if (call.url === '/api/v1/zenodo/autopilot/preferences') return prefs()
      if (call.url === '/api/v1/zenodo/autopilot/items') return { items: [] }
      return undefined
    })
    render(<YourZenodo csrfToken={BROWSER_CSRF} enabled />)
    expect(await screen.findByRole('radio', { name: /Publish for real/ })).toBeDisabled()
    expect(screen.getByRole('radio', { name: /Publish to practice/ })).toBeEnabled()
  })

  it('writes a new finding up through the ordinary route and links it, then publishes a ready one with one tap', async () => {
    let wrote = false
    const calls = server((call) => {
      if (call.url === '/api/v1/zenodo/status') return status(true)
      if (call.url === '/api/v1/zenodo/publications') return { publications: [] }
      if (call.url === '/api/v1/zenodo/autopilot/preferences') return prefs({ level: 2, creators: [{ name: 'Synthetic Visitor', affiliation: null, orcid: null }] })
      if (call.url === '/api/v1/zenodo/autopilot/items') return { items: [item()] }
      if (call.url === '/api/v1/zenodo/autopilot/next') { const first = !wrote; wrote = true; return first ? { action: 'write', item_id: ITEM, result_id: '99999999-9999-4999-8999-999999999999', kind: 'write_paper' } : { action: 'none', reason: 'nothing_new' } }
      if (call.url === '/api/v1/artifacts/jobs') return { id: '12121212-1212-4121-8121-121212121212', mode: 'explore', task_kind: 'write_paper', goal: 'Synthetic write-up', status: 'queued', created_at: '2026-11-01T00:00:00Z', updated_at: '2026-11-01T00:00:00Z', result_id: null, artifact_id: null, error_code: null, external_outcome_unknown: false }
      if (call.url === `/api/v1/zenodo/autopilot/items/${ITEM}/write`) return item({ status: 'writing' })
      if (call.url === `/api/v1/zenodo/publications/${RECORD}`) return record()
      if (call.url === `/api/v1/zenodo/publications/${RECORD}/publish`) return record({ status: 'queued' })
      return undefined
    })
    render(<YourZenodo csrfToken={BROWSER_CSRF} enabled />)
    await waitFor(() => expect(calls.find((call) => call.url.endsWith('/write'))?.body).toEqual({ run_id: '12121212-1212-4121-8121-121212121212' }))
    expect(calls.find((call) => call.url === '/api/v1/artifacts/jobs')?.body).toMatchObject({ kind: 'write_paper', source_result_id: '99999999-9999-4999-8999-999999999999' })
    await userEvent.click(await screen.findByRole('button', { name: 'Publish' }))
    await waitFor(() => expect(calls.find((call) => call.url.endsWith('/publish'))?.body).toEqual({ manifest_sha256: 'd'.repeat(64), confirm_permanent: false }))
  })
})
