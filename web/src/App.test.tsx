import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from './App'

interface ServerOptions {
  ready?: boolean
  metaConfigured?: boolean
  researchAvailable?: boolean
}

function mockServer({ ready = true, metaConfigured = false, researchAvailable = false }: ServerOptions = {}) {
  const mockFetch = vi.fn(async (input: RequestInfo | URL) => {
    const path = input.toString()
    if (path === '/api/v1/health') {
      return Response.json({ status: 'ok', service: 'polymath', version: '0.1.0' })
    }
    if (path === '/api/v1/readiness') {
      return Response.json({
        status: ready ? 'ready' : 'not_ready',
        checks: { database: ready ? 'ok' : 'unavailable', migrations: ready ? 'ok' : 'unavailable' },
      }, { status: ready ? 200 : 503 })
    }
    if (path === '/api/v1/capabilities') {
      return Response.json({
        research_available: researchAvailable,
        reason: 'research_not_implemented',
        meta_configured: metaConfigured,
      })
    }
    throw new Error(`Unexpected request: ${path}`)
  })
  vi.stubGlobal('fetch', mockFetch)
  return mockFetch
}

afterEach(() => vi.unstubAllGlobals())

describe('research workspace', () => {
  it('shows connected infrastructure without pretending research can run', async () => {
    const requests = mockServer()
    render(<App />)
    expect(await screen.findByText('Connected')).toBeInTheDocument()
    expect(screen.getByText('Ready')).toBeInTheDocument()
    expect(screen.getByText('Not configured')).toBeInTheDocument()
    expect(screen.getByText('No investigation started')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Explore connections' })).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent('Live investigations are not available yet')
    expect(requests).toHaveBeenCalledTimes(3)
  })

  it('switches research modes and preserves each draft without launching an investigation', async () => {
    const user = userEvent.setup()
    const requests = mockServer()
    render(<App />)
    await screen.findByText('Connected')
    await user.click(screen.getByRole('button', { name: /Error-correcting codes/ }))
    expect(screen.getByRole('textbox')).toHaveValue('Error-correcting codes')

    await user.click(screen.getByRole('button', { name: /02Ask/ }))
    expect(screen.getByRole('textbox', { name: 'What question is on your mind?' })).toHaveValue('')
    await user.type(screen.getByRole('textbox'), 'Why do systems sleep?')
    expect(screen.getByRole('button', { name: 'Investigate question' })).toBeDisabled()
    expect(screen.getByText(/known findings, postulated connections, and speculation/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /03Surprise me/ }))
    expect(screen.getByRole('textbox', { name: 'What should guide the search? (optional)' })).toBeInTheDocument()
    expect(screen.getByText(/The research model will choose the fields/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start a discovery' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: /01Explore/ }))
    expect(screen.getByRole('textbox')).toHaveValue('Error-correcting codes')
    await user.click(screen.getByRole('button', { name: /02Ask/ }))
    expect(screen.getByRole('textbox')).toHaveValue('Why do systems sleep?')
    expect(requests).toHaveBeenCalledTimes(3)
  })

  it('distinguishes a database readiness failure from an unreachable application', async () => {
    mockServer({ ready: false, metaConfigured: true })
    render(<App />)
    expect(await screen.findByText('Connected')).toBeInTheDocument()
    expect(screen.getByText('Not ready')).toBeInTheDocument()
    expect(screen.getByText('Key present · unverified')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Explore connections' })).toBeDisabled()
  })

  it('reports service failure and lets the user check the connection again', async () => {
    const user = userEvent.setup()
    const requests = vi.fn().mockRejectedValue(new Error('Connection refused'))
    vi.stubGlobal('fetch', requests)
    render(<App />)
    expect(await screen.findByText('Unreachable')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('could not be reached')
    mockServer()
    await user.click(screen.getByRole('button', { name: 'Check again' }))
    expect(await screen.findByText('Connected')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Explore connections' })).toBeDisabled()
  })

  it('does not turn a future capability flag into an unimplemented submission path', async () => {
    const user = userEvent.setup()
    const requests = mockServer({ researchAvailable: true, metaConfigured: true })
    render(<App />)
    await screen.findByText('Connected')
    const launch = screen.getByRole('button', { name: 'Explore connections' })
    await user.click(launch)
    expect(launch).toBeDisabled()
    expect(requests).toHaveBeenCalledTimes(3)
  })

  it('does not present malformed capability data as a working service', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ status: 'ok' })))
    render(<App />)
    await waitFor(() => expect(screen.getByText('Unreachable')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Explore connections' })).toBeDisabled()
  })
})
