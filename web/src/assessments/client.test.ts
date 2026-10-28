import { afterEach, describe, expect, it, vi } from 'vitest'
import { assessmentHistory, assessmentMatchesJob, cancelAssessmentJob, createAssessmentJob, getAssessment, getAssessmentHead, getAssessmentJob, getFindingVersion, listAssessmentJobs, loadAssessmentCapabilities, parseAssessment, parseAssessmentBasis, parseAssessmentJob, parseCapabilities, parseFindingHead, parseFindingVersion } from './client'
import { ASSESSMENT, basis, CANDIDATE, capabilities, findingVersion, HASH, head, job, payload, record, request, result, RETRIEVAL, ROOT, RUN, VERSION, WATCH } from './fixtures.test-support'
import { BIBLIOGRAPHY } from '../research/bibliography.test-support'
import type { AnswerFinding } from '../research/types'

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

describe('version-bound reassessment contracts', () => {
  it('requires explicit research availability and rejects automatic reassessment', () => {
    expect(parseCapabilities(capabilities(false)).execution_available).toBe(false)
    for (const changes of [{ automatic_reassessment: true }, { execution_available: true, reason: 'research_unavailable' }, { execution_available: false, reason: null }]) expect(() => parseCapabilities({ ...capabilities(), ...changes })).toThrow('invalid_response')
  })
  it('distinguishes current supported, unsupported baseline and historical versions', () => {
    expect(parseFindingHead(head()).can_generate).toBe(true)
    expect(parseFindingHead(head({ state: 'unsupported', revision: 1, last_assessment_id: ASSESSMENT, is_supported: false, is_historical: true, can_generate: false })).is_current).toBe(true)
    expect(parseFindingHead(head({ current_result_id: CANDIDATE, revision: 1, last_assessment_id: ASSESSMENT, is_current: false, is_supported: false, is_historical: true, can_generate: false })).state).toBe('supported')
  })
  it('rejects conflicting head identity, badges and revision history', () => {
    for (const changes of [{ is_current: false }, { is_supported: false }, { is_historical: true }, { can_generate: false }, { revision: -1 }, { revision: 1 }, { last_assessment_id: ASSESSMENT }, { current_result_id: CANDIDATE }, { requested_result_id: '../foreign' }, { state: 'proven' }]) expect(() => parseFindingHead(head(changes as never))).toThrow('invalid_response')
  })
  it('retains all enqueue pins and keeps assessment IDs separate from scientific results', () => {
    expect(parseAssessmentJob(job())).toMatchObject({ source_result_id: ROOT, watch_id: WATCH, expected_head_revision: 0, new_source_version_ids: [VERSION] })
    expect(parseAssessmentJob(job({ status: 'succeeded', assessment_id: ASSESSMENT })).assessment_id).toBe(ASSESSMENT)
    expect(parseAssessmentJob(job({ status: 'failed', assessment_id: ASSESSMENT, error_code: 'model_unavailable' })).assessment_id).toBe(ASSESSMENT)
    for (const changes of [{ task_kind: 'research' }, { result_id: CANDIDATE }, { artifact_id: ASSESSMENT }, { new_source_version_ids: [] }, { new_source_version_ids: [VERSION, VERSION] }, { new_source_version_ids: Array(6).fill(VERSION) }, { source_sha256: 'A'.repeat(64) }, { status: 'invented' }, { expected_head_revision: 0.5 }]) expect(() => parseAssessmentJob({ ...job(), ...changes })).toThrow('invalid_response')
  })
  it('preserves exact old and new versions of the same paper without making them independent', () => {
    const saved = record(), parsed = parseAssessment(saved)
    expect(parsed.payload.basis.sources.filter((source) => source.source_id === 'fixture:right')).toHaveLength(2)
    expect(parsed).toMatchObject({ source_result_id: ROOT, source_sha256: HASH, new_source_version_ids: [VERSION], applied: true, disposition: 'unchanged', head_revision: 1 })
    expect(parsed.payload.verified_passages).toEqual(parsed.payload.result.quotes)
    const bibliography = payload()
    bibliography.basis.sources[0].bibliography = BIBLIOGRAPHY
    bibliography.observed_sources[0].bibliography = BIBLIOGRAPHY
    expect(parseAssessment(record({ payload: bibliography })).payload.observed_sources[0].bibliography).toEqual(BIBLIOGRAPHY)
  })
  it('keeps unchanged, unsupported and inconclusive outcomes distinct from a new finding', () => {
    for (const disposition of ['unchanged', 'unsupported', 'inconclusive'] as const) expect(parseAssessment(record({ disposition }))).toMatchObject({ disposition, candidate_result_id: null, applied: disposition !== 'inconclusive' })
    for (const status of ['failed', 'limit_reached'] as const) {
      const conclusion = result({ status, disposition: 'inconclusive', reason: 'Synthetic bounded assessment did not conclude.' })
      expect(parseAssessment(record({ disposition: 'inconclusive', payload: payload({ result: conclusion }) })).payload.result.status).toBe(status)
    }
  })
  it('preserves revised CAS losers as auditable candidates without assigning a current badge', () => {
    const loser = parseAssessment(record({ disposition: 'revised', applied: false, head_revision: 2 }))
    expect(loser).toMatchObject({ disposition: 'revised', applied: false, candidate_result_id: CANDIDATE, expected_head_revision: 0, head_revision: 2 })
    expect(loser).not.toHaveProperty('is_current')
    expect(loser.payload.result.revised_result?.sources).toEqual([])
    expect(assessmentMatchesJob(loser, job({ status: 'succeeded', assessment_id: ASSESSMENT }))).toBe(true)
    for (const changed of [{ run_id: CANDIDATE }, { watch_id: CANDIDATE }, { source_result_id: CANDIDATE }, { source_sha256: 'b'.repeat(64) }, { expected_head_revision: 1 }, { new_source_version_ids: [CANDIDATE] }, { root_result_id: CANDIDATE }]) expect(assessmentMatchesJob({ ...loser, ...changed }, job({ status: 'succeeded', assessment_id: ASSESSMENT }))).toBe(false)
  })
  it('rejects forged CAS application, candidate binding and source pins', () => {
    for (const changes of [{ disposition: 'revised' }, { candidate_result_id: CANDIDATE }, { head_revision: 0 }, { head_revision: 2 }, { source_result_id: CANDIDATE }, { source_run_id: CANDIDATE }, { source_sha256: 'b'.repeat(64) }, { new_source_version_ids: [CANDIDATE] }, { expected_head_revision: 2 }, { basis_sha256: 'broken' }]) expect(() => parseAssessment({ ...record(), ...changes })).toThrow('invalid_response')
    expect(() => parseAssessment(record({ disposition: 'inconclusive', applied: true, head_revision: 1 }))).toThrow('invalid_response')
    expect(() => parseAssessment(record({ disposition: 'revised', candidate_result_id: null }))).toThrow('invalid_response')
  })
  it('rejects unread, changed, unselected and fabricated exact passage provenance', () => {
    const mutations: ((value: ReturnType<typeof payload>) => void)[] = [
      (value) => { value.observed_sources = [] },
      (value) => { value.observed_sources[0].content_sha256 = 'b'.repeat(64) },
      (value) => { value.observed_sources[0].title = 'Changed original metadata' },
      (value) => { value.result.observed_retrieval_ids = [] },
      (value) => { value.result.quotes[0].retrieval_id = CANDIDATE },
      (value) => { value.result.quotes[0].source_sha256 = 'b'.repeat(64) },
      (value) => { value.result.quotes[0].end++ },
      (value) => { value.result.rationale[0].citations[0].quote = 'Fabricated exact passage.' },
      (value) => { value.verified_passages = [] },
      (value) => { value.result.rationale[0].citations.pop(); value.result.quotes.pop(); value.verified_passages = value.result.quotes },
    ]
    for (const mutate of mutations) { const value = payload(); mutate(value); expect(() => parseAssessment(record({ payload: value }))).toThrow('invalid_response') }
  })
  it('rejects malformed historical bindings and unsafe source metadata', () => {
    for (const mutate of [
      (value: ReturnType<typeof basis>) => { value.sources[0].content_sha256 = 'b'.repeat(64) },
      (value: ReturnType<typeof basis>) => { value.sources[2].version_id = value.sources[0].version_id },
      (value: ReturnType<typeof basis>) => { value.sources[2].retrieval_id = value.sources[0].retrieval_id },
      (value: ReturnType<typeof basis>) => { value.sources[0].url = 'javascript:alert(1)' },
      (value: ReturnType<typeof basis>) => { value.sources[0].url = 'https://secret@example.org/source' },
      (value: ReturnType<typeof basis>) => { value.prior_quotes[0].quote = 'Changed source quote with offset mismatch.' },
    ]) { const value = basis(); mutate(value); expect(() => parseAssessmentBasis(value)).toThrow('invalid_response') }
  })
  it('requires selected exact versions, search purposes and immutable provenance in revised findings', () => {
    for (const mutate of [
      (value: ReturnType<typeof payload>) => { value.result.selected_retrieval_ids = [CANDIDATE] },
      (value: ReturnType<typeof payload>) => { value.result.selected_retrieval_ids = [RETRIEVAL] },
      (value: ReturnType<typeof payload>) => { value.result.revised_result!.memory_reads = [] },
      (value: ReturnType<typeof payload>) => { value.result.revised_result!.mode = 'ask' },
      (value: ReturnType<typeof payload>) => { value.result.revised_result!.quotes[0].source_sha256 = 'b'.repeat(64) },
      (value: ReturnType<typeof payload>) => { value.result.search_purposes = ['counterevidence', 'counterevidence'] },
    ]) { const value = payload({ result: result({ disposition: 'revised' }) }); mutate(value); expect(() => parseAssessment(record({ disposition: 'revised', payload: value }))).toThrow('invalid_response') }
  })
  it('preserves the original Ask question and refuses to upgrade a postulated claim to known', () => {
    const value = payload({ result: result({ disposition: 'revised' }) }), old = value.basis.prior_finding, revised = value.result.revised_result!
    if (old.outcome !== 'bridge' || revised.finding?.outcome !== 'bridge') throw new Error('Synthetic fixture shape changed')
    const answer: AnswerFinding = { outcome: 'answer', question: 'What does this synthetic question suggest?', summary: 'Synthetic classified answer.', claims: [{ id: 'claim1', text: 'Synthetic postulated inference.', label: 'postulated', evidence: old.evidence.map(({ source_id, quote, supports }) => ({ source_id, quote, supports })), derivation: 'Synthetic inference, not established science.', caveats: [] }], limitations: ['Synthetic fixtures are not scientific evidence.'], unanswered_questions: [] }
    value.basis.mode = 'ask'; value.basis.prior_finding = answer
    value.basis.prior_quotes = value.basis.prior_quotes.map((quote) => ({ ...quote, side: 'answer', claim_id: 'claim1' }))
    const replacement: AnswerFinding = { ...answer, claims: [{ ...answer.claims[0], evidence: revised.finding.evidence.map(({ source_id, quote, supports }) => ({ source_id, quote, supports })) }] }
    revised.mode = 'ask'; revised.status = 'answer'; revised.finding = replacement
    revised.quotes = revised.quotes.map((quote) => ({ ...quote, side: 'answer', claim_id: 'claim1' }))
    expect(parseAssessment(record({ disposition: 'revised', payload: value })).payload.result.revised_result?.finding?.outcome).toBe('answer')
    replacement.claims[0].label = 'known'
    expect(() => parseAssessment(record({ disposition: 'revised', payload: value }))).toThrow('invalid_response')
    replacement.claims[0].label = 'postulated'; replacement.question = 'A different question'
    expect(() => parseAssessment(record({ disposition: 'revised', payload: value }))).toThrow('invalid_response')
  })
  it('allows actual additional literature only when its exact read metadata and quote are persisted', () => {
    const value = payload(), extraId = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd', quote = 'Synthetic additional literature passage.'
    value.observed_sources.push({ retrieval_id: extraId, version_id: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee', source_id: 'fixture:extra', title: 'Synthetic additional source', url: 'https://example.org/synthetic-extra', content_sha256: 'c'.repeat(64), content_kind: 'abstract' })
    value.result.observed_retrieval_ids.push(extraId)
    value.result.rationale[0].citations.push({ retrieval_id: extraId, quote, supports: 'Additional synthetic comparison.' })
    value.result.quotes.push({ retrieval_id: extraId, quote, supports: 'Additional synthetic comparison.', source_id: 'fixture:extra', source_sha256: 'c'.repeat(64), content_kind: 'abstract', start: 0, end: quote.length })
    expect(parseAssessment(record({ payload: value })).payload.observed_sources).toHaveLength(4)
    value.observed_sources.pop()
    expect(() => parseAssessment(record({ payload: value }))).toThrow('invalid_response')
  })
  it('bounds rationale, events and usage and preserves hostile-looking text as literal data', () => {
    const value = payload(); value.result.rationale[0].text = '<script>plain source/model text</script>'
    expect(parseAssessment(record({ payload: value })).payload.result.rationale[0].text).toBe('<script>plain source/model text</script>')
    for (const mutate of [
      (value: ReturnType<typeof payload>) => { value.result.events[0].sequence = 2 },
      (value: ReturnType<typeof payload>) => { value.result.usage.model_calls = -1 },
      (value: ReturnType<typeof payload>) => { value.result.rationale = [] },
      (value: ReturnType<typeof payload>) => { value.result.limitations = [] },
      (value: ReturnType<typeof payload>) => { value.result.rationale[0].text = 'x'.repeat(2001) },
      (value: ReturnType<typeof payload>) => { value.result.validation_scope = 'scientific_truth' as never },
    ]) { const value = payload(); mutate(value); expect(() => parseAssessment(record({ payload: value }))).toThrow('invalid_response') }
  })
  it('reads applied finding versions with exact sources and separate head state', () => {
    const version = parseFindingVersion(findingVersion())
    expect(version).toMatchObject({ id: CANDIDATE, task_kind: 'reassess_finding', run: { id: RUN, result_id: null }, assessment: { can_generate: true } })
    expect(version.payload.sources).toHaveLength(2)
    for (const mutate of [
      (value: ReturnType<typeof findingVersion>) => { value.run.id = ROOT },
      (value: ReturnType<typeof findingVersion>) => { value.run.result_id = CANDIDATE },
      (value: ReturnType<typeof findingVersion>) => { value.payload.sources = [] },
      (value: ReturnType<typeof findingVersion>) => { value.payload.sources[0].text = 'A changed original source snapshot.' },
      (value: ReturnType<typeof findingVersion>) => { value.payload.sources[0].url = 'javascript:alert(1)' },
      (value: ReturnType<typeof findingVersion>) => { value.assessment.requested_result_id = ROOT },
      (value: ReturnType<typeof findingVersion>) => { value.assessment.current_run_id = ROOT },
    ]) { const value = findingVersion(); mutate(value); expect(() => parseFindingVersion(value)).toThrow('invalid_response') }
  })
})

describe('owned reassessment requests', () => {
  it('uses existing cookie and browser CSRF without opening sessions or invoking providers', async () => {
    const fetch = vi.fn(async () => Response.json(job()))
    vi.stubGlobal('fetch', fetch)
    await createAssessmentJob(request(), HASH)
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/v1/assessments/jobs', expect.objectContaining({ method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error', headers: { Accept: 'application/json', 'X-CSRF-Token': HASH, 'Content-Type': 'application/json' }, body: JSON.stringify(request()) }))
  })
  it('rejects noncanonical IDs, duplicate sources, revision and CSRF errors before fetch', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
    for (const changed of [{ source_result_id: '../source' }, { watch_id: 'AAAAAAAA-3333-4333-8333-333333333333' }, { new_source_version_ids: [] }, { new_source_version_ids: [VERSION, VERSION] }, { expected_head_revision: -1 }, { expected_head_revision: 1.5 }]) await expect(createAssessmentJob(request(changed), HASH)).rejects.toMatchObject({ code: 'invalid_request', uncertain: false })
    await expect(createAssessmentJob(request(), 'invalid-csrf')).rejects.toMatchObject({ code: 'session_expired', uncertain: false })
    expect(fetch).not.toHaveBeenCalled()
  })
  it('binds heads and fetched jobs to exact requested identifiers', async () => {
    const fetch = vi.fn(async () => Response.json(head({ requested_result_id: CANDIDATE, current_result_id: CANDIDATE, root_result_id: CANDIDATE }))); vi.stubGlobal('fetch', fetch)
    await expect(getAssessmentHead(ROOT)).rejects.toMatchObject({ code: 'invalid_response', uncertain: false })
    fetch.mockImplementation(async () => Response.json(job({ id: CANDIDATE })))
    await expect(getAssessmentJob(RUN)).rejects.toThrow('invalid_response')
  })
  it('pins every write response and treats mismatched accepted work as uncertain', async () => {
    const fetch = vi.fn(async () => Response.json(job())); vi.stubGlobal('fetch', fetch)
    for (const changes of [{ source_result_id: CANDIDATE }, { watch_id: CANDIDATE }, { new_source_version_ids: [CANDIDATE] }, { expected_head_revision: 1 }]) {
      fetch.mockImplementation(async () => Response.json(job(changes)))
      await expect(createAssessmentJob(request(), HASH)).rejects.toMatchObject({ code: 'invalid_response', uncertain: true })
    }
  })
  it('bounds and filters paginated job history and binds local cancellation receipts', async () => {
    const fetch = vi.fn(async (_url: string, _init?: RequestInit) => Response.json({ jobs: [job()], next_cursor: RUN })); vi.stubGlobal('fetch', fetch)
    expect((await listAssessmentJobs(ROOT, undefined, CANDIDATE)).jobs).toHaveLength(1)
    expect(fetch.mock.calls[0][0]).toBe(`/api/v1/assessments/jobs?source_result_id=${ROOT}&limit=20&before=${CANDIDATE}`)
    fetch.mockImplementation(async () => Response.json({ jobs: [job({ source_result_id: CANDIDATE })], next_cursor: null }))
    await expect(listAssessmentJobs(ROOT)).rejects.toThrow('invalid_response')
    fetch.mockImplementation(async () => Response.json(job({ status: 'cancelled' })))
    expect((await cancelAssessmentJob(RUN, HASH)).status).toBe('cancelled')
    expect(fetch.mock.calls.at(-1)?.[0]).toBe(`/api/v1/runs/${RUN}/cancel`)
    fetch.mockImplementation(async () => Response.json(job({ id: CANDIDATE })))
    await expect(cancelAssessmentJob(RUN, HASH)).rejects.toMatchObject({ code: 'invalid_response', uncertain: true })
  })
  it('loads immutable assessment detail/history and applied finding versions through read-only requests', async () => {
    const fetch = vi.fn(async (_url: string, _init?: RequestInit) => Response.json(record())); vi.stubGlobal('fetch', fetch)
    expect((await getAssessment(ASSESSMENT)).id).toBe(ASSESSMENT)
    expect(fetch.mock.calls[0][0]).toBe(`/api/v1/assessments/${ASSESSMENT}`)
    fetch.mockImplementation(async () => Response.json({ assessments: [record()], next_cursor: null }))
    expect((await assessmentHistory(ROOT, undefined, CANDIDATE)).assessments).toHaveLength(1)
    expect(fetch.mock.calls.at(-1)?.[0]).toBe(`/api/v1/assessments/heads/${ROOT}/history?limit=5&before=${CANDIDATE}`)
    fetch.mockImplementation(async () => Response.json(findingVersion()))
    expect((await getFindingVersion(CANDIDATE)).id).toBe(CANDIDATE)
    expect(fetch.mock.calls.at(-1)?.[0]).toBe(`/api/v1/assessments/findings/${CANDIDATE}`)
    expect(fetch.mock.calls.every(([, init]) => init?.method === 'GET' && init.credentials === 'same-origin' && init.cache === 'no-store')).toBe(true)
  })
  it('rejects foreign detail/version responses, mixed roots and duplicate or oversized pages', async () => {
    const fetch = vi.fn(async () => Response.json(record({ id: CANDIDATE }))); vi.stubGlobal('fetch', fetch)
    await expect(getAssessment(ASSESSMENT)).rejects.toThrow('invalid_response')
    for (const assessments of [[record({ root_result_id: CANDIDATE })], [record(), record()], Array.from({ length: 21 }, () => record())]) {
      fetch.mockImplementation(async () => Response.json({ assessments, next_cursor: null }))
      await expect(assessmentHistory(ROOT)).rejects.toThrow('invalid_response')
    }
    fetch.mockImplementation(async () => Response.json(findingVersion({ id: ROOT, assessment: head() })))
    await expect(getFindingVersion(CANDIDATE)).rejects.toThrow('invalid_response')
    fetch.mockImplementation(async () => Response.json({ jobs: [job(), job()], next_cursor: null }))
    await expect(listAssessmentJobs(ROOT)).rejects.toThrow('invalid_response')
  })
  it.each([401, 403])('clears the private browser context for HTTP %s without exposing details', async (status) => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ detail: 'Untrusted private details.' }, { status })))
    await expect(getAssessmentHead(ROOT)).rejects.toMatchObject({ code: 'session_expired', uncertain: false })
  })
  it('separates definite disabled/validation errors from potentially committed writes', async () => {
    const fetch = vi.fn(async () => Response.json({ detail: 'research_unavailable' }, { status: 503 })); vi.stubGlobal('fetch', fetch)
    await expect(createAssessmentJob(request(), HASH)).rejects.toMatchObject({ code: 'research_unavailable', uncertain: false })
    fetch.mockImplementation(async () => Response.json({ detail: 'secret_body' }, { status: 500 }))
    await expect(createAssessmentJob(request(), HASH)).rejects.toMatchObject({ code: 'unavailable', uncertain: true })
    fetch.mockImplementation(async () => Response.json({ detail: 'secret_body' }, { status: 422 }))
    await expect(createAssessmentJob(request(), HASH)).rejects.toMatchObject({ code: 'invalid_request', uncertain: false })
  })
  it('bounds response bytes and validates fatal UTF-8/JSON for reads and unknown writes', async () => {
    const fetch = vi.fn(async () => new Response('{invalid-json')); vi.stubGlobal('fetch', fetch)
    await expect(getAssessmentJob(RUN)).rejects.toMatchObject({ code: 'invalid_response', uncertain: false })
    await expect(createAssessmentJob(request(), HASH)).rejects.toMatchObject({ code: 'invalid_response', uncertain: true })
    for (const response of [new Response(new Uint8Array(2 * 1024 * 1024 + 1)), new Response(new Uint8Array([255])), new Response('{}', { headers: { 'content-length': '2097153' } })]) {
      fetch.mockImplementation(async () => response)
      await expect(getAssessmentJob(RUN)).rejects.toThrow('invalid_response')
    }
  })
  it('never retries a lost POST and preserves its unknown outcome', async () => {
    const fetch = vi.fn(async () => { throw new TypeError('private connection details') }); vi.stubGlobal('fetch', fetch)
    await expect(createAssessmentJob(request(), HASH)).rejects.toMatchObject({ code: 'connection_failed', uncertain: true })
    expect(fetch).toHaveBeenCalledTimes(1)
    await expect(loadAssessmentCapabilities()).rejects.toMatchObject({ code: 'connection_failed', uncertain: false })
  })
  it('times out and aborts requests without replaying model work', async () => {
    vi.useFakeTimers()
    const fetch = vi.fn((_url: string, init: RequestInit) => new Promise<Response>((_resolve, reject) => init.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true }))); vi.stubGlobal('fetch', fetch)
    const pending = createAssessmentJob(request(), HASH)
    const outcome = expect(pending).rejects.toMatchObject({ code: 'connection_failed', uncertain: true })
    await vi.advanceTimersByTimeAsync(10000); await outcome
    expect(fetch).toHaveBeenCalledTimes(1)
    const controller = new AbortController(); controller.abort()
    await expect(getAssessmentHead(ROOT, controller.signal)).rejects.toMatchObject({ code: 'aborted', uncertain: false })
    expect(fetch).toHaveBeenCalledTimes(1)
  })
})
