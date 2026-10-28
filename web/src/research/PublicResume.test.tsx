import { useState } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BROWSER_CSRF } from '../budget/test-support'
import { NativeResumeControl } from './NativeResumeControl'
import { publicAccess } from './accessFixtures.test-support'
import type { OwnedRun } from './client'
import { NativeResearchProvider } from './nativeContext'
import type { NativeResearchContextValue } from './nativeContext'

const RUN = '21111111-1111-4111-8111-111111111111', HASH = 'c'.repeat(64), LEASE = '9223372036854775807'
function run(status: OwnedRun['status'] = 'interrupted'): OwnedRun {
  return { id: RUN, mode: 'explore', goal: 'Synthetic saved public task', task_kind: 'research', status, created_at: '2026-10-28T00:00:00Z', updated_at: '2026-10-28T00:00:01Z', result_id: null, error_code: null, external_outcome_unknown: false, native_research: { activation_sha256: HASH, expected_lease_token: LEASE, can_resume: status === 'interrupted' } }
}
function context(): NativeResearchContextValue {
  return { required: true, phase: 'ready', activation: null, access: publicAccess({ expected_activation_sha256: HASH, remaining_session_jobs_today: 0, expires_at: new Date(Date.now() + 3600000).toISOString() }), submission: { audience: 'public_sessions', expectedActivationSha256: HASH }, canSubmit: false, canResume: true, pollPaused: false, reason: null, invalidate: vi.fn(), refresh: vi.fn() }
}
function retained(native: NativeResearchContextValue, expired = vi.fn(), resumed = vi.fn()) {
  function OwnedResearch() {
    const [owned, setOwned] = useState(true)
    if (!owned) return <p>Private research has been cleared.</p>
    return <NativeResearchProvider value={native}><p>Retained private research.</p><NativeResumeControl run={run()} csrfToken={BROWSER_CSRF} onResumed={resumed} onSessionExpired={() => { setOwned(false); expired() }} /></NativeResearchProvider>
  }
  return { ...render(<OwnedResearch />), expired, resumed }
}
afterEach(() => vi.unstubAllGlobals())

describe('explicit public continuation under the original funding version', () => {
  it('continues with zero new jobs using one exact decimal lease POST and browser CSRF only', async () => {
    const native = context(), fetch = vi.fn(async () => Response.json(run('queued'))), resumed = vi.fn()
    vi.stubGlobal('fetch', fetch)
    retained(native, vi.fn(), resumed)
    expect(native.canSubmit).toBe(false); expect(native.canResume).toBe(true); expect(fetch).not.toHaveBeenCalled()
    const button = screen.getByRole('button', { name: 'Continue this saved task' })
    expect(button).toBeEnabled(); fireEvent.click(button); fireEvent.click(button)
    await waitFor(() => expect(resumed).toHaveBeenCalledOnce())
    expect(fetch).toHaveBeenCalledOnce()
    const [path, options] = fetch.mock.calls[0] as unknown as [string, RequestInit]
    expect(path).toBe(`/api/v1/runs/${RUN}/resume`)
    expect(options).toMatchObject({ method: 'POST', credentials: 'same-origin', redirect: 'error' })
    expect(JSON.parse(options.body!.toString())).toEqual({ expected_lease_token: LEASE, expected_activation_sha256: HASH })
    expect(options.headers).toMatchObject({ 'X-CSRF-Token': BROWSER_CSRF })
    expect(Object.keys(options.headers!).some((key) => /operator|authorization/i.test(key))).toBe(false)
  })

  it('cannot rebind saved work to another current public approval', () => {
    const native = context(), fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
    native.submission = { audience: 'public_sessions', expectedActivationSha256: 'd'.repeat(64) }
    retained(native)
    expect(screen.getByRole('button', { name: 'Continue this saved task' })).toBeDisabled()
    expect(screen.getByText(/A different or expired activation cannot authorize this earlier task/)).toBeVisible()
    expect(fetch).not.toHaveBeenCalled()
  })

  it('keeps a definitive rate rejection separate from an unknown write or lost browser session', async () => {
    const fetch = vi.fn(async () => Response.json({ detail: 'public_submit_rate_limited' }, { status: 429, headers: { 'Retry-After': '17' } }))
    vi.stubGlobal('fetch', fetch)
    const native = context(), { expired, resumed } = retained(native)
    await userEvent.click(screen.getByRole('button', { name: 'Continue this saved task' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/Wait at least 17 seconds/)
    expect(screen.getByRole('button', { name: 'Continue this saved task' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Read saved task status' })).toBeNull()
    expect(screen.getByText('Retained private research.')).toBeVisible()
    expect(fetch).toHaveBeenCalledOnce(); expect(expired).not.toHaveBeenCalled(); expect(resumed).not.toHaveBeenCalled(); expect(native.invalidate).not.toHaveBeenCalled()
  })

  it.each(['research_session_required', 'public_research_requester_expired'])('clears owned data on actual browser expiry: %s', async (detail) => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ detail }, { status: 401 })))
    const native = context(), { expired } = retained(native)
    await userEvent.click(screen.getByRole('button', { name: 'Continue this saved task' }))
    expect(await screen.findByText('Private research has been cleared.')).toBeVisible()
    expect(screen.queryByText('Retained private research.')).toBeNull()
    expect(expired).toHaveBeenCalledOnce(); expect(native.invalidate).not.toHaveBeenCalled()
  })

  it('keeps saved ownership when the funding deadline closes', async () => {
    const fetch = vi.fn(async () => Response.json({ detail: 'public_research_delegation_expired' }, { status: 503 }))
    vi.stubGlobal('fetch', fetch)
    const native = context(), { expired } = retained(native)
    await userEvent.click(screen.getByRole('button', { name: 'Continue this saved task' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/original approval is no longer current/)
    expect(screen.getByText('Retained private research.')).toBeVisible()
    expect(expired).not.toHaveBeenCalled(); expect(native.invalidate).toHaveBeenCalledOnce(); expect(fetch).toHaveBeenCalledOnce()
    expect(screen.queryByRole('button', { name: 'Read saved task status' })).toBeNull()
  })

  it.each(['lost', 'malformed'])('resolves an unknown public continuation through an explicit saved-status GET: %s', async (failure) => {
    const fetch = vi.fn(async (_path: string, options: RequestInit) => {
      if (options.method === 'POST') { if (failure === 'lost') throw new TypeError('Synthetic lost receipt'); return Response.json({}) }
      return Response.json(run('queued'))
    })
    vi.stubGlobal('fetch', fetch)
    const { resumed, expired } = retained(context())
    await userEvent.click(screen.getByRole('button', { name: 'Continue this saved task' }))
    const read = await screen.findByRole('button', { name: 'Read saved task status' })
    expect(fetch).toHaveBeenCalledOnce(); expect(resumed).not.toHaveBeenCalled(); expect(expired).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'Continue this saved task' })).toBeNull()
    await userEvent.click(read)
    await waitFor(() => expect(resumed).toHaveBeenCalledOnce())
    expect(fetch).toHaveBeenCalledTimes(2); expect(fetch.mock.calls[1][1].method).toBe('GET')
    expect(screen.getByText('Retained private research.')).toBeVisible()
  })
})
