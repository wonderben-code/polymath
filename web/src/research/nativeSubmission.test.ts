import { act, renderHook, waitFor } from '@testing-library/react'
import { createElement } from 'react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createResearchRun, parseRun, resumeNativeResearchRun } from './client'
import type { OwnedRun } from './client'
import { createArtifactJob, parseArtifactJob } from '../artifacts/client'
import type { ArtifactJob, ArtifactKind } from '../artifacts/types'
import { createAssessmentJob, parseAssessmentJob } from '../assessments/client'
import { ROOT, WATCH, VERSION, job as assessmentJob, request as assessmentRequest } from '../assessments/fixtures.test-support'
import { nativeAdmissionFailure, parseNativeRunControl, validNativeSubmission } from './nativeAdmission'
import type { NativeSubmissionContext } from './nativeAdmission'
import { BROWSER_CSRF, DATE, OPERATOR_CSRF } from '../budget/test-support'
import { currentActivation } from '../budget/activationFixtures'
import { operatorFixture } from '../publications/test-fixtures'
import { useResearchWorkspace } from './useResearchWorkspace'
import { useArtifactWorkspace } from '../artifacts/useArtifactWorkspace'
import { NativeResearchProvider } from './nativeContext'
import type { NativeResearchContextValue } from './nativeContext'

const RUN = '11111111-1111-4111-8111-111111111111', FORMALISATION = '99999999-9999-4999-8999-999999999999'
const HASH = currentActivation().activation!.manifest_sha256
const native: NativeSubmissionContext = { operatorCsrfToken: OPERATOR_CSRF, expectedActivationSha256: HASH }
const control = { activation_sha256: HASH, expected_lease_token: '9223372036854775807', can_resume: true }
function run(mode: OwnedRun['mode'] = 'explore'): OwnedRun { return { id: RUN, mode, goal: 'Synthetic native scientific task', task_kind: 'research', status: 'queued', created_at: DATE, updated_at: DATE, result_id: null, error_code: null, external_outcome_unknown: false, native_research: { ...control, can_resume: false } } }
function artifactJob(kind: ArtifactKind = 'write_summary'): ArtifactJob { return { ...run(), task_kind: kind, status: 'queued', result_id: null, artifact_id: null } }
const submitters = [
  { name: 'research', path: '/api/v1/runs', response: () => run(), submit: (admission?: NativeSubmissionContext) => createResearchRun('explore', 'Synthetic native scientific task', BROWSER_CSRF, undefined, [], admission) },
  { name: 'artifact', path: '/api/v1/artifacts/jobs', response: () => artifactJob(), submit: (admission?: NativeSubmissionContext) => createArtifactJob('write_summary', ROOT, BROWSER_CSRF, undefined, undefined, admission) },
  { name: 'assessment', path: '/api/v1/assessments/jobs', response: () => assessmentJob(), submit: (admission?: NativeSubmissionContext) => createAssessmentJob(assessmentRequest(), BROWSER_CSRF, undefined, admission) },
]
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); vi.restoreAllMocks() })

describe('exact native admission on existing scientific routes', () => {
  it.each(['explore', 'ask', 'surprise'] as const)('sends %s once with the exact current activation and operator CSRF, without another approval route', async (mode) => {
    const fetch = vi.fn(async (_path: RequestInfo | URL, _options?: RequestInit) => Response.json(run(mode))); vi.stubGlobal('fetch', fetch)
    await createResearchRun(mode, 'Synthetic native scientific task', BROWSER_CSRF, undefined, [], native)
    expect(fetch.mock.calls).toEqual([['/api/v1/runs', expect.objectContaining({ method: 'POST', credentials: 'same-origin', cache: 'no-store', body: JSON.stringify({ mode, goal: 'Synthetic native scientific task', expected_activation_sha256: HASH }), headers: expect.objectContaining({ 'X-CSRF-Token': BROWSER_CSRF, 'X-Operator-CSRF-Token': OPERATOR_CSRF }) })]])
  })
  it.each(['formalise', 'write_summary', 'write_paper'] as const)('binds %s to its exact saved finding and optional formalisation without changing the activation', async (kind) => {
    const fetch = vi.fn(async (_path: RequestInfo | URL, _options?: RequestInit) => Response.json(artifactJob(kind))); vi.stubGlobal('fetch', fetch)
    const formalisation = kind === 'formalise' ? undefined : FORMALISATION
    await createArtifactJob(kind, ROOT, BROWSER_CSRF, undefined, formalisation, native)
    expect(fetch.mock.calls).toEqual([['/api/v1/artifacts/jobs', expect.objectContaining({ method: 'POST', body: JSON.stringify({ kind, source_result_id: ROOT, ...(formalisation ? { formalisation_id: formalisation } : {}), expected_activation_sha256: HASH }), headers: expect.objectContaining({ 'X-CSRF-Token': BROWSER_CSRF, 'X-Operator-CSRF-Token': OPERATOR_CSRF }) })]])
  })
  it('pins finding reassessment to the source, watch, exact new versions, head revision and activation in one POST', async () => {
    const fetch = vi.fn(async (_path: RequestInfo | URL, _options?: RequestInit) => Response.json(assessmentJob())); vi.stubGlobal('fetch', fetch)
    await createAssessmentJob(assessmentRequest(), BROWSER_CSRF, undefined, native)
    expect(fetch.mock.calls).toEqual([['/api/v1/assessments/jobs', expect.objectContaining({ method: 'POST', body: JSON.stringify({ source_result_id: ROOT, watch_id: WATCH, new_source_version_ids: [VERSION], expected_head_revision: 0, expected_activation_sha256: HASH }), headers: expect.objectContaining({ 'X-CSRF-Token': BROWSER_CSRF, 'X-Operator-CSRF-Token': OPERATOR_CSRF }) })]])
  })
  it.each(submitters)('retains explicit legacy/test admission only when no native context was requested: $name', async (surface) => {
    const fetch = vi.fn(async (_path: RequestInfo | URL, _options?: RequestInit) => Response.json(surface.response())); vi.stubGlobal('fetch', fetch)
    await surface.submit()
    const [path, options] = fetch.mock.calls[0]
    expect(path).toBe(surface.path); expect(JSON.parse(options!.body!.toString())).not.toHaveProperty('expected_activation_sha256')
    expect(options!.headers).not.toHaveProperty('X-Operator-CSRF-Token')
    expect(fetch).toHaveBeenCalledTimes(1)
  })
  it.each(submitters)('rejects malformed native context locally and never falls back to a legacy write: $name', async (surface) => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
    for (const admission of [{ ...native, operatorCsrfToken: '' }, { ...native, expectedActivationSha256: 'not-a-hash' }, { ...native, expectedActivationSha256: 'A'.repeat(64) }]) await expect(surface.submit(admission)).rejects.toMatchObject({ code: 'native_authority_changed' })
    expect(fetch).not.toHaveBeenCalled()
  })
  it.each(submitters)('invalidates native authority on operator/role/epoch errors without retrying another route: $name', async (surface) => {
    for (const [status, detail] of [[401, 'operator_session_required'], [403, 'operator_csrf_check_failed'], [503, 'readiness_role_unavailable'], [403, 'meta_research_actor_forbidden'], [409, 'meta_research_authority_expired'], [409, 'meta_research_prior_unresolved'], [409, 'meta_research_input_changed']] as const) {
      const fetch = vi.fn(async () => Response.json({ detail }, { status })); vi.stubGlobal('fetch', fetch)
      await expect(surface.submit(native)).rejects.toMatchObject({ code: 'native_authority_changed' })
      expect(fetch).toHaveBeenCalledTimes(1); expect(fetch.mock.calls[0]?.length).toBeGreaterThan(0)
    }
  })
  it.each(submitters)('keeps true browser expiry distinct from native authority and does not retry it: $name', async (surface) => {
    const fetch = vi.fn(async () => Response.json({ detail: 'research_session_required' }, { status: 401 })); vi.stubGlobal('fetch', fetch)
    await expect(surface.submit(native)).rejects.toMatchObject({ code: 'session_expired' })
    expect(fetch).toHaveBeenCalledTimes(1)
  })
  it.each(submitters)('never automatically resends a lost or malformed successful enqueue receipt: $name', async (surface) => {
    const fetch = vi.fn(async () => { throw new TypeError('Synthetic connection loss after possible commit') }); vi.stubGlobal('fetch', fetch)
    await expect(surface.submit(native)).rejects.toMatchObject({ code: 'connection_failed' }); expect(fetch).toHaveBeenCalledTimes(1)
    const malformed = vi.fn(async () => Response.json({ task_kind: 'invented', raw_response: 'Synthetic invalid result' })); vi.stubGlobal('fetch', malformed)
    await expect(surface.submit(native)).rejects.toMatchObject({ code: 'invalid_response' }); expect(malformed).toHaveBeenCalledTimes(1)
  })
  it('keeps private native errors bounded and treats storage failure as an unknown operation rather than expired browser authority', () => {
    expect(nativeAdmissionFailure(503, 'meta_research_storage_unavailable')).toBe(false)
    expect(nativeAdmissionFailure(409, 'meta_research_authority_expired')).toBe(true)
    expect(nativeAdmissionFailure(401, 'research_session_required')).toBe(false)
    expect(validNativeSubmission(native)).toBe(true)
  })
})

describe('native run control and explicit continuation', () => {
  it('preserves the full decimal fencing token on all three saved-job parsers without Number conversion', () => {
    expect(parseRun({ ...run(), native_research: control }).native_research).toEqual(control)
    expect(parseArtifactJob({ ...artifactJob(), native_research: control }).native_research).toEqual(control)
    expect(parseAssessmentJob({ ...assessmentJob(), native_research: control }).native_research).toEqual(control)
    expect(parseNativeRunControl(undefined, () => { throw new Error('Invalid native control') })).toBeUndefined()
  })
  it.each([1, '01', '-1', '+1', '1e3', '1.0', '9223372036854775808'])('rejects noncanonical or unsafe saved control lease %s', (token) => {
    for (const parse of [() => parseRun({ ...run(), native_research: { ...control, expected_lease_token: token } }), () => parseArtifactJob({ ...artifactJob(), native_research: { ...control, expected_lease_token: token } }), () => parseAssessmentJob({ ...assessmentJob(), native_research: { ...control, expected_lease_token: token } })]) expect(parse).toThrow('invalid_response')
  })
  it('rejects private fields or malformed hashes inside native controls instead of exposing a lease or owner identity', () => {
    for (const native_research of [{ ...control, activation_sha256: 'bad' }, { ...control, owner_hash: 'Synthetic private owner' }, { ...control, lease_owner: 'Synthetic worker' }, { ...control, can_resume: 'yes' }]) expect(() => parseRun({ ...run(), native_research })).toThrow('invalid_response')
  })
  it('continues only the exact existing run, original activation and decimal token with both CSRFs', async () => {
    const fetch = vi.fn(async (_path: RequestInfo | URL, _options?: RequestInit) => Response.json(run())); vi.stubGlobal('fetch', fetch)
    await resumeNativeResearchRun(RUN, control.expected_lease_token, BROWSER_CSRF, native)
    expect(fetch.mock.calls).toEqual([[`/api/v1/runs/${RUN}/resume`, expect.objectContaining({ method: 'POST', credentials: 'same-origin', cache: 'no-store', body: JSON.stringify({ expected_lease_token: '9223372036854775807', expected_activation_sha256: HASH }), headers: expect.objectContaining({ 'X-CSRF-Token': BROWSER_CSRF, 'X-Operator-CSRF-Token': OPERATOR_CSRF }) })]])
  })
  it.each([1, '01', '-1', '+1', '1e3', '1.0', '9223372036854775808'])('rejects malformed continuation lease %s before sending', async (token) => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
    await expect(resumeNativeResearchRun(RUN, token as string, BROWSER_CSRF, native)).rejects.toMatchObject({ code: 'invalid_request' })
    expect(fetch).not.toHaveBeenCalled()
  })
  it('rejects a continuation receipt for a different run and never issues a replacement', async () => {
    const fetch = vi.fn(async () => Response.json({ ...run(), id: FORMALISATION })); vi.stubGlobal('fetch', fetch)
    await expect(resumeNativeResearchRun(RUN, '1', BROWSER_CSRF, native)).rejects.toThrow('invalid_response')
    expect(fetch).toHaveBeenCalledTimes(1)
  })
})

describe('unknown scientific enqueue in the shared native workspace', () => {
  it.each(['lost', 'malformed', 'storage503'] as const)('locks an unknown research enqueue (%s) until an explicit owned-history GET refresh', async (failure) => {
    vi.spyOn(Date, 'now').mockReturnValue(Date.parse(DATE))
    const created = run(), fetch = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
      const path = input.toString()
      if (path === '/api/v1/session') return Response.json({ csrf_token: BROWSER_CSRF, expires_at: '2100-01-01T00:00:00Z' })
      if (path === '/api/v1/operator/session') return Response.json(operatorFixture(true))
      if (path === '/api/v1/budget/research/current') return Response.json(currentActivation())
      if (path === '/api/v1/runs' && options?.method === 'POST') {
        if (failure === 'lost') throw new TypeError('Synthetic committed enqueue response loss')
        return failure === 'malformed' ? Response.json({ malformed: 'Synthetic possible committed receipt' }) : Response.json({ detail: 'meta_research_storage_unavailable' }, { status: 503 })
      }
      if (path === '/api/v1/runs') return Response.json({ runs: [], next_cursor: null })
      throw new Error(`Unexpected synthetic scientific workspace path: ${path}`)
    }); vi.stubGlobal('fetch', fetch)
    const { result } = renderHook(() => useResearchWorkspace(true, true, true))
    await waitFor(() => expect(result.current.canSubmit).toBe(true))
    await act(async () => { await result.current.submit('ask', created.goal) })
    expect(result.current.canSubmit).toBe(false)
    expect(result.current.error).not.toBe('')
    await act(async () => { await result.current.submit('ask', created.goal) })
    expect(fetch.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1)
    act(() => result.current.refresh())
    await waitFor(() => expect(result.current.canSubmit).toBe(true))
    expect(fetch.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1)
    expect(fetch.mock.calls.filter(([path, options]) => path === '/api/v1/runs' && options?.method === 'GET')).toHaveLength(2)
    expect(result.current.runs).toEqual([])
  })
  it.each(['malformed', 'storage503'] as const)('locks an unknown artifact enqueue (%s) until its owned saved-work GET refresh', async (failure) => {
    const context: NativeResearchContextValue = { required: true, phase: 'ready', activation: currentActivation().activation, submission: native, canSubmit: true, pollPaused: false, reason: null, invalidate: vi.fn(), refresh: vi.fn() }
    const wrapper = ({ children }: { children: ReactNode }) => createElement(NativeResearchProvider, { value: context, children })
    const fetch = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
      const path = input.toString()
      if (path === '/api/v1/artifacts/jobs' && options?.method === 'POST') return failure === 'malformed' ? Response.json({ malformed: 'Synthetic possible committed artifact receipt' }) : Response.json({ detail: 'meta_research_storage_unavailable' }, { status: 503 })
      if (path.startsWith('/api/v1/artifacts/jobs?source_result_id=')) return Response.json({ jobs: [], next_cursor: null })
      throw new Error(`Unexpected synthetic artifact workspace path: ${path}`)
    }); vi.stubGlobal('fetch', fetch)
    const props = { sourceResultId: ROOT, csrfToken: BROWSER_CSRF, enabled: true, canGenerate: true, onSessionExpired: vi.fn() }
    const { result } = renderHook(() => useArtifactWorkspace(props), { wrapper })
    await waitFor(() => expect(result.current.canCreate).toBe(true))
    await act(async () => { await result.current.create('write_summary') })
    expect(result.current.canCreate).toBe(false)
    expect(result.current.error).toContain('Could not confirm whether this task was queued.')
    await act(async () => { await result.current.create('write_summary') })
    expect(fetch.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1)
    act(() => result.current.refresh())
    await waitFor(() => expect(result.current.canCreate).toBe(true))
    expect(fetch.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1)
    expect(fetch.mock.calls.filter(([, options]) => options?.method === 'GET')).toHaveLength(2)
    expect(props.onSessionExpired).not.toHaveBeenCalled(); expect(context.invalidate).not.toHaveBeenCalled()
  })
  it('keeps native 409 authority loss separate from browser-owned history and requires an explicit current refresh', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(Date.parse(DATE))
    const fetch = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
      const path = input.toString()
      if (path === '/api/v1/session') return Response.json({ csrf_token: BROWSER_CSRF, expires_at: '2100-01-01T00:00:00Z' })
      if (path === '/api/v1/operator/session') return Response.json(operatorFixture(true))
      if (path === '/api/v1/budget/research/current') return Response.json(currentActivation())
      if (path === '/api/v1/runs' && options?.method === 'POST') return Response.json({ detail: 'meta_research_authority_expired' }, { status: 409 })
      if (path === '/api/v1/runs') return Response.json({ runs: [run()], next_cursor: null })
      throw new Error(`Unexpected synthetic scientific workspace path: ${path}`)
    }); vi.stubGlobal('fetch', fetch)
    const { result } = renderHook(() => useResearchWorkspace(true, true, true))
    await waitFor(() => expect(result.current.canSubmit).toBe(true))
    await act(async () => { await result.current.submit('ask', 'Synthetic native authority race') })
    expect(result.current.phase).toBe('ready'); expect(result.current.runs).toHaveLength(1)
    expect(result.current.native.phase).toBe('expired'); expect(result.current.native.submission).toBeNull(); expect(result.current.canSubmit).toBe(false)
    expect(fetch.mock.calls.filter(([path]) => path === '/api/v1/session')).toHaveLength(1)
    expect(fetch.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1)
    act(() => result.current.native.refresh())
    await waitFor(() => expect(result.current.canSubmit).toBe(true))
    expect(fetch.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1)
  })
})
