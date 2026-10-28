import { afterEach, describe, expect, it, vi } from 'vitest'
import { getExploreBridge, getExploreCollectionSummary, getExploreProtocols, listExploreBridges, listExploreRankings } from './client'
import { pythonFixtures } from './fixtures'
import { parseExploreBridgeHistory, parseExploreCollectionSummary } from './parsers'
const summary = parseExploreCollectionSummary(pythonFixtures.summary), saved = parseExploreBridgeHistory(pythonFixtures.bridges), RUN = summary.run_id
const access = { protocol: 'polymath-explore-collection-access-v1', activation_sha256: 'a'.repeat(64), explore_collection_intent: summary.explore_collection_intent }
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })
describe('owned Explore reads', () => {
  it('binds actual routes and exact IDs using read-only private transport without session creation or operator headers', async () => {
    const values = [access, summary, saved, saved.stages[0], { run_id: RUN, rankings: summary.rankings }], fetch = vi.fn(async (_path: RequestInfo | URL, _options?: RequestInit) => Response.json(values.shift())); vi.stubGlobal('fetch', fetch)
    await getExploreProtocols(); await getExploreCollectionSummary(RUN); await listExploreBridges(RUN); await getExploreBridge(RUN, saved.stages[0].stage.stage_id); await listExploreRankings(RUN)
    expect(fetch.mock.calls.map(([path]) => path)).toEqual(['/api/v1/research/explore-protocols', `/api/v1/runs/${RUN}/explore-collection`, `/api/v1/runs/${RUN}/bridges`, `/api/v1/runs/${RUN}/bridges/${saved.stages[0].stage.stage_id}`, `/api/v1/runs/${RUN}/rankings`])
    for (const [, options] of fetch.mock.calls) expect(options).toMatchObject({ method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error', referrerPolicy: 'no-referrer', headers: { Accept: 'application/json' } })
  })
  it.each([401, 403, 404, 429, 503])('keeps status%s bounded with ownership expiry distinct from a resource cooldown', async (status) => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ detail: 'Synthetic ignored raw error' }, { status, headers: { 'Retry-After': '17' } })))
    await expect(getExploreCollectionSummary(RUN)).rejects.toMatchObject({ code: status === 401 || status === 403 ? 'session_expired' : status === 404 ? 'explore_collection_not_found' : status === 429 ? 'resource_limited' : 'unavailable', ...(status === 429 ? { retryAfterSeconds: 17 } : {}) })
  })
  it('rejects malformed/foreign envelopes and unsafe input before using their text or making invalid requests', async () => {
    const fetch = vi.fn(async () => Response.json({ ...summary, run_id: saved.stages[0].stage.stage_id })); vi.stubGlobal('fetch', fetch)
    await expect(getExploreCollectionSummary(RUN)).rejects.toMatchObject({ code: 'invalid_response' }); expect(() => getExploreCollectionSummary('../private')).toThrow('invalid_request'); expect(fetch).toHaveBeenCalledTimes(1)
  })
  it('bounds UTF8 response bytes and rejects invalid UTF8 without exposing a partial record', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { headers: { 'content-length': String(32769) } }))); await expect(getExploreProtocols()).rejects.toMatchObject({ code: 'invalid_response' })
    vi.stubGlobal('fetch', vi.fn(async () => new Response(new Uint8Array([0xff])))); await expect(getExploreProtocols()).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('fences late responses even when a synthetic transport ignores abort and never retries a read automatically', async () => {
    const controller = new AbortController(); let resolve!: (value: Response) => void
    const fetch = vi.fn(async () => new Promise<Response>((done) => { resolve = done })); vi.stubGlobal('fetch', fetch)
    const request = getExploreProtocols(controller.signal); controller.abort(); resolve(Response.json(access)); await expect(request).rejects.toMatchObject({ code: 'aborted' }); expect(fetch).toHaveBeenCalledTimes(1)
  })
})
