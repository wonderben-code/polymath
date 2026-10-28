import { afterEach, describe, expect, it, vi } from 'vitest'
import { changeWatch, checkWatch, createWatch, getWatchSource, listWatches, listWatchSources, loadWatchCapabilities, parseCapabilities, parseSourceDetail, parseTick, parseWatch } from './client'
import { capabilities, detail, RESULT, RUN, SOURCE, tick, WATCH, watch } from './fixtures.test-support'
import { BIBLIOGRAPHY } from '../research/bibliography.test-support'

afterEach(() => vi.unstubAllGlobals())
describe('owned literature watch protocol', () => {
  it('preserves exact source-document coverage without interpreting a window as an entire paper', () => {
    const fixture = detail(), snapshot = '99999999-9999-4999-8999-999999999999'
    const coverage = { snapshot_id: snapshot, snapshot_sha256: 'c'.repeat(64), arxiv_id: BIBLIOGRAPHY.arxiv_id, arxiv_version: 2, start: 100, end: 100 + Array.from(fixture.document.text).length, total: 10000 }
    const document = { ...fixture.document, source_id: `arxiv-html:${snapshot}:${coverage.start}:${coverage.end}`, content_kind: 'full_text', url: 'https://arxiv.org/html/2610.00001v2', bibliography: BIBLIOGRAPHY, coverage }
    expect(parseSourceDetail({ ...fixture, document }).document.coverage).toEqual(coverage)
    expect(parseSourceDetail(fixture).document).not.toHaveProperty('coverage')
    for (const change of [{ text: document.text + 'x' }, { content_kind: 'abstract' }, { bibliography: undefined }, { url: 'https://arxiv.org/html/2610.00001v3' }]) expect(() => parseSourceDetail({ ...fixture, document: { ...document, ...change } })).toThrow('invalid_response')
  })
  it('requires explicit collection availability and never accepts model execution', () => {
    expect(parseCapabilities(capabilities(false)).execution_available).toBe(false)
    expect(() => parseCapabilities({ ...capabilities(), model_calls: true })).toThrow()
    expect(() => parseCapabilities({ ...capabilities(), min_interval_seconds: 900 })).toThrow()
    expect(() => parseCapabilities({ ...capabilities(), coverage: 'all_literature' })).toThrow()
  })
  it('bounds query, cadence, ownership coverage, provider cursors and active identity', () => {
    expect(parseWatch(watch()).cursors.openalex?.cursor).toBe('private-provider-cursor')
    for (const change of [{ query: 'q'.repeat(501) }, { label: 'x'.repeat(121) }, { interval_seconds: 21599 }, { interval_seconds: 604801 }, { coverage: 'everyone' }, { active_run_id: RUN }, { cursors: { unknown: {} } }]) expect(() => parseWatch({ ...watch(), ...change })).toThrow()
  })
  it('preserves partial diagnostics and source versions without claiming reviewed findings', () => {
    expect(parseTick(tick()).diagnostics.arxiv?.status).toBe('failed')
    expect(parseSourceDetail(detail()).assessment_status).toBe('unassessed')
    expect(() => parseSourceDetail({ ...detail(), assessment_status: 'confirmed' })).toThrow()
    expect(() => parseSourceDetail({ ...detail(), document: { ...detail().document, url: 'https://secret@arxiv.org/abs/2099.00001' } })).toThrow()
    expect(() => parseTick({ ...tick(), watch_id: 'other' })).toThrow()
    expect(parseTick(tick()).ingestion_status).toBeNull()
    expect(parseTick({ ...tick(), ingestion_status: 'partial', error_code: 'source_limit_reached' })).toMatchObject({ ingestion_status: 'partial', error_code: 'source_limit_reached' })
    expect(() => parseTick({ ...tick(), ingestion_status: 'entire_literature_complete' })).toThrow()
  })
  it('preserves bibliographic metadata on the exact owned source snapshot', () => {
    expect(parseSourceDetail({ ...detail(), document: { ...detail().document, bibliography: BIBLIOGRAPHY } }).document.bibliography).toEqual(BIBLIOGRAPHY)
  })
  it('preserves contextual consumed-source status without calling it a confirmed finding', () => {
    const root = RESULT, context = { assessment_status: 'assessed', assessment_root_result_id: root, assessment_history_url: `/api/v1/assessments/heads/${root}/history` }
    expect(parseSourceDetail({ ...detail(), ...context }).assessment_status).toBe('assessed')
    expect(() => parseSourceDetail({ ...detail(), ...context, assessment_history_url: 'https://foreign.invalid/private' })).toThrow()
    expect(() => parseSourceDetail({ ...detail(), ...context, assessment_root_result_id: null })).toThrow()
    expect(() => parseSourceDetail({ ...detail(), ...context, assessment_status: 'confirmed' })).toThrow()
  })
  it('uses browser-owned cookies and CSRF only; never creates a browser or operator session', async () => {
    const fetch = vi.fn(async (_path: RequestInfo | URL, _init?: RequestInit) => Response.json(watch()))
    vi.stubGlobal('fetch', fetch)
    await createWatch({ label: 'A watch', query: 'A&B %_ query', interval_seconds: 21600, source_result_id: RESULT }, 'ephemeral-browser-csrf')
    const [path, init] = fetch.mock.calls[0]
    expect(path).toBe('/api/v1/knowledge/watches')
    expect(init).toMatchObject({ method: 'POST', credentials: 'same-origin', cache: 'no-store', headers: { 'X-CSRF-Token': 'ephemeral-browser-csrf' } })
    expect(JSON.parse(String(init?.body))).toEqual({ label: 'A watch', query: 'A&B %_ query', interval_seconds: 21600, source_result_id: RESULT })
    expect(init?.headers).not.toHaveProperty('X-Operator-CSRF-Token')
    expect(fetch).toHaveBeenCalledTimes(1)
  })
  it('uses exact owned version IDs for source text and checks its response binding', async () => {
    const fetch = vi.fn(async (_path: RequestInfo | URL) => Response.json(detail()))
    vi.stubGlobal('fetch', fetch)
    await expect(getWatchSource(WATCH, SOURCE)).resolves.toMatchObject({ id: SOURCE, watch_id: WATCH })
    expect(fetch.mock.calls[0][0]).toBe(`/api/v1/knowledge/watches/${WATCH}/sources/${SOURCE}`)
    fetch.mockImplementation(async () => Response.json(detail({ watch_id: RESULT })))
    await expect(getWatchSource(WATCH, SOURCE)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('encodes only validated pagination IDs and rejects wrong scope, duplicate IDs or oversized pages', async () => {
    const fetch = vi.fn(async (_path: RequestInfo | URL) => Response.json({ watches: [watch()], next_cursor: null }))
    vi.stubGlobal('fetch', fetch)
    await listWatches(undefined, WATCH)
    expect(fetch.mock.calls[0][0]).toBe(`/api/v1/knowledge/watches?limit=20&before=${WATCH}`)
    expect(() => listWatchSources('foreign/../id')).toThrow()
    fetch.mockImplementation(async () => Response.json({ watches: [watch(), watch()], next_cursor: null }))
    await expect(listWatches()).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('distinguishes browser expiry, disabled execution and throttling from an unknown write outcome', async () => {
    const fetch = vi.fn(async () => Response.json({ detail: 'watch_check_throttled' }, { status: 429 }))
    vi.stubGlobal('fetch', fetch)
    await expect(checkWatch(WATCH, 'csrf')).rejects.toMatchObject({ code: 'watch_check_throttled', uncertain: false })
    fetch.mockImplementation(async () => Response.json({ detail: 'ingestion_execution_disabled' }, { status: 503 }))
    await expect(changeWatch(WATCH, 'resume', 'csrf')).rejects.toMatchObject({ code: 'ingestion_execution_disabled', uncertain: false })
    fetch.mockImplementation(async () => Response.json({}, { status: 401 }))
    await expect(listWatches()).rejects.toMatchObject({ code: 'session_expired' })
    fetch.mockImplementation(async () => { throw new TypeError('lost response') })
    await expect(changeWatch(WATCH, 'pause', 'csrf')).rejects.toMatchObject({ uncertain: true })
  })
  it('aborts stalled requests and bounds streamed response bytes', async () => {
    const controller = new AbortController()
    const fetch = vi.fn((_path: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((_resolve, reject) => init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))))
    vi.stubGlobal('fetch', fetch)
    const pending = loadWatchCapabilities(controller.signal); controller.abort()
    await expect(pending).rejects.toMatchObject({ code: 'unavailable' })
    fetch.mockImplementation(async () => new Response('a'.repeat(2 * 1024 * 1024 + 1)))
    await expect(listWatches()).rejects.toMatchObject({ code: 'invalid_response' })
  })
})
