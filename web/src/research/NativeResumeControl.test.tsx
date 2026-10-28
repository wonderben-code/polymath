import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { NativeResumeControl } from './NativeResumeControl'
import { NativeResearchProvider } from './nativeContext'
import type { NativeResearchContextValue } from './nativeContext'
import { currentActivation } from '../budget/activationFixtures'
import { BROWSER_CSRF, OPERATOR_CSRF } from '../budget/test-support'
import type { OwnedRun } from './client'
const RUN = '11111111-1111-4111-8111-111111111111'
function run(overrides: Partial<OwnedRun> = {}): OwnedRun { return { id: RUN, mode: 'explore', goal: 'Synthetic interrupted task', status: 'interrupted', created_at: '2026-10-28T00:00:00Z', updated_at: '2026-10-28T00:00:01Z', result_id: null, error_code: null, external_outcome_unknown: false, native_research: { activation_sha256: currentActivation().activation!.manifest_sha256, expected_lease_token: '9223372036854775807', can_resume: true }, ...overrides } }
function context(overrides: Partial<NativeResearchContextValue> = {}): NativeResearchContextValue { const activation = currentActivation().activation!; return { required: true, phase: 'ready', activation, submission: { operatorCsrfToken: OPERATOR_CSRF, expectedActivationSha256: activation.manifest_sha256 }, canSubmit: true, pollPaused: false, reason: null, invalidate: vi.fn(), refresh: vi.fn(), ...overrides } }
afterEach(() => vi.unstubAllGlobals())
describe('continuing the original native task', () => {
  it('submits the exact decimal lease and original current activation once after an explicit click', async () => {
    const native = context(), onResumed = vi.fn(), fetch = vi.fn(async () => Response.json(run({ status: 'queued' }))); vi.stubGlobal('fetch', fetch)
    render(<NativeResearchProvider value={native}><NativeResumeControl run={run()} csrfToken={BROWSER_CSRF} onResumed={onResumed} onSessionExpired={vi.fn()} /></NativeResearchProvider>)
    expect(fetch).not.toHaveBeenCalled(); const button = screen.getByRole('button', { name: 'Continue this saved task' }); fireEvent.click(button); fireEvent.click(button)
    await waitFor(() => expect(onResumed).toHaveBeenCalledOnce()); expect(fetch).toHaveBeenCalledOnce()
    const [path, options] = fetch.mock.calls[0] as unknown as [string, RequestInit]
    expect(path).toBe(`/api/v1/runs/${RUN}/resume`); expect(JSON.parse(options.body!.toString())).toEqual({ expected_lease_token: '9223372036854775807', expected_activation_sha256: native.activation!.manifest_sha256 })
    expect(options.headers).toMatchObject({ 'X-CSRF-Token': BROWSER_CSRF, 'X-Operator-CSRF-Token': OPERATOR_CSRF })
  })
  it('does not rebind old work to a different activation or enable structurally unsafe continuation', () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
    const native = context({ submission: { operatorCsrfToken: OPERATOR_CSRF, expectedActivationSha256: 'b'.repeat(64) } })
    const { rerender } = render(<NativeResearchProvider value={native}><NativeResumeControl run={run()} csrfToken={BROWSER_CSRF} onResumed={vi.fn()} onSessionExpired={vi.fn()} /></NativeResearchProvider>)
    expect(screen.getByRole('button', { name: 'Continue this saved task' })).toBeDisabled()
    rerender(<NativeResearchProvider value={context()}><NativeResumeControl run={run({ native_research: { ...run().native_research!, can_resume: false } })} csrfToken={BROWSER_CSRF} onResumed={vi.fn()} onSessionExpired={vi.fn()} /></NativeResearchProvider>)
    expect(screen.getByRole('button', { name: 'Continue this saved task' })).toBeDisabled(); expect(screen.getByText(/Continuation is blocked while a prepared request/)).toBeVisible(); expect(fetch).not.toHaveBeenCalled()
  })
  it('requires a saved-status GET after a lost response instead of blindly repeating resume', async () => {
    const fetch = vi.fn(async (_path: string, options: RequestInit) => { if (options.method === 'POST') throw new TypeError('Synthetic lost continuation receipt'); return Response.json(run({ status: 'queued' })) }); vi.stubGlobal('fetch', fetch)
    const onResumed = vi.fn()
    render(<NativeResearchProvider value={context()}><NativeResumeControl run={run()} csrfToken={BROWSER_CSRF} onResumed={onResumed} onSessionExpired={vi.fn()} /></NativeResearchProvider>)
    await userEvent.click(screen.getByRole('button', { name: 'Continue this saved task' })); const read = await screen.findByRole('button', { name: 'Read saved task status' })
    expect(fetch).toHaveBeenCalledOnce(); expect(screen.queryByRole('button', { name: 'Continue this saved task' })).toBeNull()
    await userEvent.click(read); await waitFor(() => expect(onResumed).toHaveBeenCalledOnce()); expect(fetch).toHaveBeenCalledTimes(2)
    expect(fetch.mock.calls[1][1].method).toBe('GET')
  })
})
