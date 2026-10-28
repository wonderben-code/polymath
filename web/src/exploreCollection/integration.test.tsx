import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useResearchWorkspace } from '../research/useResearchWorkspace'
import { useNativeResearchContext } from '../research/useNativeResearchContext'
import { parseRun, createResearchRun } from '../research/client'
import { parseFindingVersion } from '../assessments/client'
import { publicAccess } from '../research/accessFixtures.test-support'
import { privateAccess } from '../research/accessFixtures.test-support'
import { operatorFixture } from '../publications/test-fixtures'
import { currentActivation } from '../budget/activationFixtures'
import { BROWSER_CSRF, DATE } from '../budget/test-support'
import { parseExploreBridgeHistory, parseExploreCollectionSummary } from './parsers'
import { pythonFixtures } from './fixtures'
const SHA = publicAccess().expected_activation_sha256!, RUN = '11111111-1111-4111-8111-111111111111'
const intent = (maximum = 4) => ({ protocol: 'polymath-explore-collection-v1' as const, max_bridges: maximum })
const cap = (hash: string | null = SHA, maximum: number | null = 4) => ({ protocol: 'polymath-explore-collection-access-v1', activation_sha256: hash, explore_collection_intent: maximum === null ? null : intent(maximum) })
const run = (extra = {}) => ({ id: RUN, task_kind: 'research', mode: 'explore', goal: 'Synthetic seeded question.', status: 'queued', result_id: null, error_code: null, external_outcome_unknown: false, created_at: DATE, updated_at: DATE, ...extra })
const settings = () => ({ enabled: true, csrfToken: BROWSER_CSRF, required: true, available: true, onBrowserExpired: vi.fn() })
function server(overrides?: (path: string, options?: RequestInit) => Response | undefined | Promise<Response | undefined>) {
  const fetch = vi.fn(async (path: RequestInfo | URL, options?: RequestInit) => {
    const custom = await overrides?.(String(path), options); if (custom) return custom
    if (String(path) === '/api/v1/session') return Response.json({ csrf_token: BROWSER_CSRF, expires_at: '2100-01-01T00:00:00Z' })
    if (String(path) === '/api/v1/research/access') return Response.json(publicAccess())
    if (String(path) === '/api/v1/research/protocols') return Response.json({ protocol: 'polymath-research-protocols-v1', activation_sha256: SHA, capabilities: [] })
    if (String(path) === '/api/v1/research/explore-protocols') return Response.json(cap())
    if (String(path) === '/api/v1/runs' && options?.method === 'POST') { const body = JSON.parse(String(options.body)); return Response.json(run({ ...body })) }
    if (String(path) === '/api/v1/runs') return Response.json({ runs: [], next_cursor: null })
    return Response.json(run())
  }); vi.stubGlobal('fetch', fetch); vi.stubGlobal('EventSource', class extends EventTarget { close() {} }); return fetch
}
beforeEach(() => { vi.spyOn(Date, 'now').mockReturnValue(Date.parse(DATE)) })
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })
describe('ordinary approved Explore variant', () => {
  it.each([1, 2, 4])('automatically selects min(3,approved %s) in one pinned public POST despite duplicate clicks', async (maximum) => {
    const fetch = server((path) => path.endsWith('/explore-protocols') ? Response.json(cap(SHA, maximum)) : undefined), view = renderHook(() => useResearchWorkspace(true, true, true))
    await waitFor(() => expect(view.result.current.canExplore && view.result.current.canSubmit).toBe(true))
    await act(async () => { await Promise.all([view.result.current.submit('explore', 'Synthetic seeded question.'), view.result.current.submit('explore', 'Synthetic seeded question.')]) })
    const posts = fetch.mock.calls.filter(([, options]) => options?.method === 'POST'); expect(posts).toHaveLength(1)
    expect(JSON.parse(String(posts[0][1]?.body))).toEqual({ mode: 'explore', goal: 'Synthetic seeded question.', expected_activation_sha256: SHA, explore_collection_intent: intent(Math.min(3, maximum)) }); expect(posts[0][1]?.headers).not.toHaveProperty('X-Operator-CSRF-Token')
  })
  it('preserves approved legacy seeded Explore and exact F3 parent requests without a collection opt-in', async () => {
    for (const parents of [[], ['21111111-1111-4111-8111-111111111111', '31111111-1111-4111-8111-111111111111']]) {
      const fetch = server((path) => path.endsWith('/explore-protocols') ? Response.json(cap(SHA, parents.length ? 4 : null)) : undefined), view = renderHook(() => useResearchWorkspace(true, true, true)); await waitFor(() => expect(view.result.current.canExplore).toBe(true))
      await act(async () => { await view.result.current.submit('explore', 'Synthetic seeded question.', parents) }); const posts = fetch.mock.calls.filter(([, options]) => options?.method === 'POST'); expect(posts).toHaveLength(1); const body = JSON.parse(String(posts[0][1]?.body)); expect(body).not.toHaveProperty('explore_collection_intent'); if (parents.length) expect(body.parent_result_ids).toEqual(parents); view.unmount()
    }
  })
  it('keeps Ask/Surprise and original continuation available while only Explore capability is missing or stale', async () => {
    server((path) => path.endsWith('/explore-protocols') ? Response.json(cap('d'.repeat(64))) : undefined)
    const view = renderHook(() => useResearchWorkspace(true, true, true)); await waitFor(() => expect(view.result.current.native.explorePhase).toBe('error')); expect(view.result.current.canExplore).toBe(false); expect(view.result.current.canAsk).toBe(true); expect(view.result.current.native.canResume).toBe(true)
  })
  it('invalidates only Explore on an explicit profile rejection and never falls back or automatically repeats', async () => {
    const fetch = server((_path, options) => options?.method === 'POST' ? Response.json({ detail: 'meta_research_explore_protocol_unavailable' }, { status: 409 }) : undefined), view = renderHook(() => useResearchWorkspace(true, true, true)); await waitFor(() => expect(view.result.current.canExplore).toBe(true))
    await act(async () => { await view.result.current.submit('explore', 'Synthetic seeded question.'); await view.result.current.submit('explore', 'Synthetic seeded question.') }); expect(fetch.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1); expect(view.result.current.canExplore).toBe(false); expect(view.result.current.native.canResume).toBe(true); expect(view.result.current.phase).toBe('ready')
  })
  it('latches an unreadable possibly committed receipt until an explicit owned history reload', async () => {
    const fetch = server((_path, options) => options?.method === 'POST' ? Response.json({ unknown: 'Synthetic lost receipt' }) : undefined), view = renderHook(() => useResearchWorkspace(true, true, true)); await waitFor(() => expect(view.result.current.canExplore).toBe(true))
    await act(async () => { await view.result.current.submit('explore', 'Synthetic seeded question.') }); expect(view.result.current.canSubmit).toBe(false); await act(async () => { await view.result.current.submit('explore', 'Synthetic seeded question.') }); expect(fetch.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1)
  })
  it('caches its separate public GET once per exact funding hash and never calls operator endpoints', async () => {
    vi.useFakeTimers(); vi.spyOn(Date, 'now').mockRestore(); vi.setSystemTime(new Date(DATE)); const fetch = server(), view = renderHook(() => useNativeResearchContext(settings())); await act(async () => { await vi.advanceTimersByTimeAsync(0) }); expect(view.result.current.explorePhase).toBe('ready'); await act(async () => { await vi.advanceTimersByTimeAsync(40000) }); expect(fetch.mock.calls.filter(([path]) => String(path).endsWith('/explore-protocols'))).toHaveLength(1); expect(fetch.mock.calls.some(([path]) => /operator|\/session/.test(String(path)))).toBe(false)
  })
  it('honors Explore read Retry-After, retains original resume, and clears owner only on true browser expiry', async () => {
    vi.useFakeTimers(); vi.spyOn(Date, 'now').mockRestore(); vi.setSystemTime(new Date(DATE)); const props = settings(), fetch = server((path) => path.endsWith('/explore-protocols') ? Response.json({ detail: 'public_request_rate_limited' }, { status: 429, headers: { 'Retry-After': '23' } }) : undefined), view = renderHook(() => useNativeResearchContext(props)); await act(async () => { await vi.advanceTimersByTimeAsync(0) }); expect(view.result.current.explorePhase).toBe('error'); expect(view.result.current.canResume).toBe(true); const before = fetch.mock.calls.length; act(() => view.result.current.refresh()); await act(async () => { await vi.advanceTimersByTimeAsync(22000) }); expect(fetch).toHaveBeenCalledTimes(before); expect(props.onBrowserExpired).not.toHaveBeenCalled(); view.unmount(); vi.useRealTimers()
    server((path) => path.endsWith('/explore-protocols') ? Response.json({ detail: 'research_session_required' }, { status: 401 }) : undefined); const expired = settings(); renderHook(() => useNativeResearchContext(expired)); await waitFor(() => expect(expired.onBrowserExpired).toHaveBeenCalledOnce())
  })
  it('derives private approval extension from the existing privileged current projection only', async () => {
    const current = currentActivation(), base = current.activation!.profiles[0]; current.activation!.explore_collection_profile = { profile: { ...base, task_kind: 'research', controller_protocol: 'polymath-meta-explore-collection-v1', collection_protocol: 'polymath-explore-collection-v1', max_bridges: 2 }, profile_sha256: 'd'.repeat(64) }
    const fetch = server((path) => path.endsWith('/access') ? Response.json(privateAccess()) : path.endsWith('/operator/session') ? Response.json(operatorFixture(true)) : path.endsWith('/research/current') ? Response.json(current) : undefined), view = renderHook(() => useNativeResearchContext(settings())); await waitFor(() => expect(view.result.current.explorePhase).toBe('ready')); expect(view.result.current.exploreAccess?.explore_collection_intent?.max_bridges).toBe(2); expect(fetch.mock.calls.some(([path]) => String(path).endsWith('/explore-protocols'))).toBe(false)
  })
})
describe('exact saved Explore reader and intent boundaries', () => {
  it('accepts genuine partial/unselected stage proofs and preserves exact evidence for downstream use', () => {
    const detail = parseExploreBridgeHistory(pythonFixtures.bridges).stages[0], summary = parseExploreCollectionSummary(pythonFixtures.summary), stage = detail.stage
    const value = { id: stage.result_id, run_id: stage.run_id, task_kind: 'research', run: run({ id: stage.run_id, status: 'cancelled', artifact_id: null, assessment_id: null, explore_collection_intent: summary.explore_collection_intent }), payload: detail.result, discovery_stage: detail, assessment: { requested_result_id: stage.result_id, root_result_id: stage.result_id, current_result_id: stage.result_id, current_run_id: stage.run_id, state: 'supported', revision: 0, last_assessment_id: null, is_current: true, is_supported: true, is_historical: false, can_generate: true, discovery_stage: { stage_id: stage.stage_id, ordinal: stage.ordinal, depth: 1, status: 'current_supported' } } }
    expect(parseFindingVersion(value).payload).toMatchObject(detail.result); expect(parseFindingVersion(value).run.explore_collection_intent).toEqual(summary.explore_collection_intent)
    for (const bad of [{ ...value, discovery_stage: undefined }, { ...value, run: { ...value.run, explore_collection_intent: null } }, { ...value, run: { ...value.run, mode: 'surprise' } }, { ...value, discovery_stage: { ...detail, stage: { ...stage, result_id: RUN } } }]) expect(() => parseFindingVersion(bad)).toThrow()
  })
  it('rejects explicit null, wrong-mode and parent-selected collection requests before any POST', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
    for (const mode of ['ask', 'surprise'] as const) await expect(createResearchRun(mode, 'Synthetic seed', BROWSER_CSRF, undefined, [], undefined, undefined, undefined, intent())).rejects.toMatchObject({ code: 'invalid_request' })
    await expect(createResearchRun('explore', 'Synthetic seed', BROWSER_CSRF, undefined, [], undefined, undefined, undefined, null as never)).rejects.toMatchObject({ code: 'invalid_request' }); expect(fetch).not.toHaveBeenCalled()
    expect(() => parseRun(run({ mode: 'ask', explore_collection_intent: intent() }))).toThrow()
  })
})
