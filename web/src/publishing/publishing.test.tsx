import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { APPROVAL, ARTIFACT, BROWSER_CSRF, MANIFEST, OPERATOR_CSRF, PUBLICATION_JOB, manifestFixture, metadataFixture, operatorFixture, publicationJobFixture } from '../publications/test-fixtures'
import { PublishingSection } from './PublishingSection'
import { performStep } from './usePublishingAutopilot'

const ITEM = '99999999-9999-4999-8999-999999999999'
const RESULT = '66666666-6666-4666-8666-666666666666'

function item(changes: Record<string, unknown> = {}) {
  return { id: ITEM, result_id: RESULT, title: 'A synthetic shared structure', level: 2, destination: 'sandbox', write_kind: 'write_paper', status: 'ready', reason: null, artifact_id: ARTIFACT, manifest_id: MANIFEST, approval_id: null, job_id: null, created_at: '2026-10-30T00:00:00Z', updated_at: '2026-10-30T00:00:00Z', ...changes }
}

function preferences(changes: Record<string, unknown> = {}) {
  return { level: 1, draft_destination: 'sandbox', write_kind: 'write_paper', full_text_only: false, daily_cap: 3, creators: [{ name: 'Synthetic Author', affiliation: null, orcid: null }], license: 'cc-by-4.0', keywords: [], enabled_at: null, production_confirmed: false, ...changes }
}

afterEach(() => vi.unstubAllGlobals())

describe('publishing autopilot steps', () => {
  it('prepares, approves and queues only through the ordinary publication routes, attaching each exact result', async () => {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input), method = init?.method ?? 'GET'
      calls.push(`${method} ${url}`)
      if (url === '/api/v1/publications' && method === 'POST') return Response.json(manifestFixture())
      if (url === `/api/v1/publications/${MANIFEST}/approve`) return Response.json(manifestFixture(true))
      if (url === `/api/v1/publications/${MANIFEST}`) return Response.json(manifestFixture(calls.some((call) => call.endsWith('/approve'))))
      if (url === '/api/v1/publication-jobs') return Response.json(publicationJobFixture())
      if (url.endsWith('/attach')) return Response.json(item())
      throw new Error(`Unexpected synthetic request ${url}`)
    }))
    await performStep({ action: 'prepare', item_id: ITEM, artifact_id: ARTIFACT, destination: 'sandbox', metadata: metadataFixture() }, BROWSER_CSRF, OPERATOR_CSRF)
    await performStep({ action: 'approve', item_id: ITEM, manifest_id: MANIFEST, manifest_sha256: manifestFixture().manifest_sha256 }, BROWSER_CSRF, OPERATOR_CSRF)
    await performStep({ action: 'queue', item_id: ITEM, manifest_id: MANIFEST, approval_id: APPROVAL, manifest_sha256: manifestFixture().manifest_sha256 }, BROWSER_CSRF, OPERATOR_CSRF)
    expect(calls).toEqual([
      'POST /api/v1/publications', `POST /api/v1/publishing/items/${ITEM}/attach`,
      `GET /api/v1/publications/${MANIFEST}`, `POST /api/v1/publications/${MANIFEST}/approve`, `POST /api/v1/publishing/items/${ITEM}/attach`,
      `GET /api/v1/publications/${MANIFEST}`, 'POST /api/v1/publication-jobs', `POST /api/v1/publishing/items/${ITEM}/attach`,
    ])
    expect(await performStep({ action: 'none', reason: 'manual' }, BROWSER_CSRF, OPERATOR_CSRF)).toBe(false)
  })

  it('refuses to approve a record that changed since the step was chosen', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(manifestFixture())))
    await expect(performStep({ action: 'approve', item_id: ITEM, manifest_id: MANIFEST, manifest_sha256: 'e'.repeat(64) }, BROWSER_CSRF, OPERATOR_CSRF)).rejects.toThrow()
  })
})

describe('publishing section', () => {
  it('says plainly when Zenodo is not connected and never asks about a publisher session', async () => {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => { calls.push(String(input)); return Response.json({ configured: false, publisher: null, destinations: [], publishing_runs: false }) }))
    render(<PublishingSection csrfToken={BROWSER_CSRF} enabled />)
    expect(await screen.findByText('Zenodo is not connected on this server yet.')).toBeInTheDocument()
    expect(calls.some((url) => url.includes('/operator/'))).toBe(false)
  })

  it('offers four levels, asks once before publishing for real, and publishes a ready draft with one tap', async () => {
    const posts: { url: string; body: unknown }[] = []
    let approved = false
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (init?.method === 'POST') posts.push({ url, body: init.body ? JSON.parse(String(init.body)) : null })
      if (url === '/api/v1/publishing/status') return Response.json({ configured: true, publisher: 'Synthetic Publisher', destinations: ['sandbox', 'production'], publishing_runs: true })
      if (url === '/api/v1/operator/session') return Response.json(operatorFixture(true))
      if (url === '/api/v1/publishing/preferences' && init?.method === 'POST') { const body = JSON.parse(String(init.body)); return Response.json(preferences({ ...body, confirm_production: undefined, production_confirmed: body.confirm_production === true })) }
      if (url === '/api/v1/publishing/preferences') return Response.json(preferences())
      if (url === '/api/v1/publishing/items') return Response.json({ items: [item()] })
      if (url === `/api/v1/publications/${MANIFEST}/approve`) { approved = true; return Response.json(manifestFixture(true)) }
      if (url === `/api/v1/publications/${MANIFEST}`) return Response.json(manifestFixture(approved))
      if (url === '/api/v1/publication-jobs') return Response.json(publicationJobFixture())
      if (url.endsWith('/attach')) return Response.json(item({ status: 'queued', approval_id: APPROVAL, job_id: PUBLICATION_JOB }))
      if (url === '/api/v1/publishing/next') return Response.json({ action: 'none', reason: 'nothing_new' })
      throw new Error(`Unexpected synthetic request ${url}`)
    }))
    render(<PublishingSection csrfToken={BROWSER_CSRF} enabled />)
    expect(await screen.findByText('Connected to Zenodo sandbox and production as Synthetic Publisher.')).toBeInTheDocument()
    const levels = await screen.findByRole('group', { name: 'How much should Polymath do?' })
    expect(levels).toHaveTextContent('Ask me every time')
    await userEvent.click(screen.getByRole('radio', { name: /Publish for real/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Save this choice' }))
    expect(screen.getByRole('alertdialog', { name: 'Publish permanently, on your behalf?' })).toBeInTheDocument()
    expect(posts.some((post) => post.url.endsWith('/preferences'))).toBe(false)
    await userEvent.click(screen.getByRole('button', { name: 'Yes, publish for real' }))
    await waitFor(() => expect(posts.find((post) => post.url.endsWith('/preferences'))?.body).toMatchObject({ level: 4, confirm_production: true }))

    await userEvent.click(screen.getByRole('button', { name: 'Publish' }))
    await waitFor(() => expect(posts.map((post) => post.url)).toContain('/api/v1/publication-jobs'))
    expect(posts.filter((post) => post.url.endsWith('/attach')).map((post) => (post.body as { stage: string }).stage)).toEqual(['approve', 'queue'])
  })
})
