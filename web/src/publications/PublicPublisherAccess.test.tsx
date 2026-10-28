import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PublicationPanel } from './PublicationPanel'
import { artifactFixture, BROWSER_CSRF, capabilityFixture, manifestFixture, operatorFixture } from './test-fixtures'
import { NativeResearchProvider } from '../research/nativeContext'
import type { NativeResearchContextValue } from '../research/nativeContext'
import { publicAccess } from '../research/accessFixtures.test-support'
function authority(access: ReturnType<typeof publicAccess> | undefined): NativeResearchContextValue { return { required: true, phase: access ? 'ready' : 'loading', activation: null, access, submission: null, canSubmit: false, canResume: false, pollPaused: false, reason: null, invalidate: vi.fn(), refresh: vi.fn() } }
afterEach(() => vi.unstubAllGlobals())
describe('publisher authority is separate from visitor funding', () => {
  it.each(['public', 'pending'] as const)('does not hydrate an operator automatically in %s native admission', async (scope) => {
    const fetch = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
      const path = input.toString()
      if (path === '/api/v1/publications/capabilities') return Response.json(capabilityFixture())
      if (path.startsWith('/api/v1/publications?')) return Response.json({ manifests: [manifestFixture()], next_cursor: null })
      if (path === '/api/v1/publication-jobs/capabilities') return Response.json({ execution_available: false, reason: 'publication_execution_disabled' })
      if (path.startsWith('/api/v1/publication-jobs?')) return Response.json({ jobs: [], next_cursor: null })
      if (path === '/api/v1/operator/session' && options?.method === 'POST') return Response.json(operatorFixture(true))
      throw new Error(`Forbidden automatic visitor route ${path}`)
    }); vi.stubGlobal('fetch', fetch)
    render(<NativeResearchProvider value={authority(scope === 'public' ? publicAccess() : undefined)}><PublicationPanel artifact={artifactFixture()} csrfToken={BROWSER_CSRF} enabled sourceCurrent onSessionExpired={vi.fn()} /></NativeResearchProvider>)
    await screen.findByLabelText('Publisher access code')
    expect(fetch.mock.calls.every(([path]) => path.toString() !== '/api/v1/operator/session')).toBe(true)
    expect(fetch.mock.calls.every(([, options]) => options?.method === 'GET')).toBe(true)
    fireEvent.change(screen.getByLabelText('Publisher access code'), { target: { value: 'synthetic-separate-publisher-code' } })
    fireEvent.click(screen.getByRole('button', { name: 'Unlock publisher approval' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Lock publisher access' })).toBeInTheDocument())
    const posts = fetch.mock.calls.filter(([, options]) => options?.method === 'POST')
    expect(posts).toHaveLength(1); expect(posts[0][0]).toBe('/api/v1/operator/session'); expect(posts[0][1]?.headers).not.toHaveProperty('X-Operator-CSRF-Token')
    expect(screen.queryByDisplayValue('synthetic-separate-publisher-code')).not.toBeInTheDocument()
  })
})
