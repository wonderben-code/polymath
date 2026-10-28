import { afterEach, describe, expect, it, vi } from 'vitest'
import { DiscoveryError, getDiscoveryStage, getDiscoverySummary, listDiscoveryStages, parseDiscoveryIntent, parseDiscoveryStage, parseDiscoveryStageHistory, parseDiscoverySummary, parseStageAssessment, parseStageCommitReceipt, stageMatchesReceipt } from './client'
import { HASH, RESULTS, RUN, STAGES, assessment, intent, stageDetail, stageHistory, stageReceipt, summary } from './test-support'
import { BIBLIOGRAPHY } from '../research/bibliography.test-support'
import type { DiscoveryStage } from './types'

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

describe('immutable discovery stage contracts', () => {
  it('requires explicit bounded ladder intent and accepts no call/budget or public-audience override', () => {
    expect(parseDiscoveryIntent(intent())).toEqual(intent())
    expect(parseDiscoveryIntent(intent({ max_stages: 4, max_depth: 3 }))).toEqual(intent({ max_stages: 4, max_depth: 3 }))
    for (const changed of [{ max_stages: 0 }, { max_stages: 5 }, { max_depth: 0 }, { max_depth: 4 }, { max_stages: true }, { protocol: 'ordinary-surprise' }, { max_model_calls: 100 }, { account_cap: 999 }, { audience: 'public_session' }]) expect(() => parseDiscoveryIntent({ ...intent(), ...changed })).toThrow(DiscoveryError)
    const missing = { ...intent() } as Record<string, unknown>; delete missing.max_stages
    expect(() => parseDiscoveryIntent(missing)).toThrow('invalid_response')
  })
  it('preserves model judgement, rationales and material limits without inventing a calibrated ranking', () => {
    expect(parseStageAssessment(assessment())).toEqual(assessment())
    expect(parseStageAssessment(assessment({ confidence_score: 0 })).confidence_score).toBe(0)
    expect(parseStageAssessment(assessment({ confidence_score: 100 })).confidence_score).toBe(100)
    expect(parseStageAssessment(assessment()).confidence_scope).toBe('uncalibrated_model_judgement')
    expect(parseStageAssessment(assessment()).limitations).toHaveLength(2)
  })
  it.each(['novelty', 'novelty_rationale', 'confidence', 'confidence_score', 'confidence_scope', 'confidence_rationale', 'limitations'])('rejects a missing %s instead of assigning an application judgement', (field) => {
    const value = { ...assessment() } as Record<string, unknown>; delete value[field]
    expect(() => parseStageAssessment(value)).toThrow('invalid_response')
  })
  it('rejects unsupported probability/novelty claims, noninteger scores and absent limitations', () => {
    for (const changed of [{ confidence_scope: 'calibrated_probability' }, { novelty: 'novel_discovery' }, { confidence: 'verified' }, { confidence_score: 0.37 }, { confidence_score: -1 }, { confidence_score: 101 }, { confidence_score: '37' }, { limitations: [] }, { limitations: Array.from({ length: 9 }, () => 'Synthetic limitation') }, { novelty_rationale: '' }, { independent_probability: 99 }]) expect(() => parseStageAssessment({ ...assessment(), ...changed })).toThrow()
  })
  it('keeps literal model markup as plain text while rejecting controls and unpaired surrogates', () => {
    const literal = '<script>synthetic unavailable mathematics</script> [link](javascript:invalid)'
    expect(parseStageAssessment(assessment({ confidence_rationale: literal })).confidence_rationale).toBe(literal)
    expect(() => parseStageAssessment(assessment({ confidence_rationale: 'Synthetic\u0000control' }))).toThrow()
    expect(() => parseStageAssessment(assessment({ confidence_rationale: 'Synthetic\ud800surrogate' }))).toThrow()
    expect(parseStageAssessment(assessment({ confidence_rationale: 'Synthetic Unicode premise 🧠 remains unverified.' })).confidence_rationale).toContain('🧠')
  })
  it('preserves exact stage/result hashes, local depth and ordered original parent references', () => {
    expect(parseStageCommitReceipt(stageReceipt())).toEqual(stageReceipt())
    const higher = stageReceipt(2)
    expect(parseStageCommitReceipt(higher)).toEqual(higher)
    expect(higher.parent_refs.map((parent) => parent.result_id)).toEqual([stageReceipt(0).result_id, stageReceipt(1).result_id])
  })
  it('rejects self-parent, duplicate logical parent, single-parent and contradictory base depth receipts', () => {
    const value = stageReceipt(2), parent = value.parent_refs[0]
    for (const parent_refs of [[parent], [parent, parent], [{ ...parent, result_id: value.result_id }, value.parent_refs[1]], Array.from({ length: 5 }, () => parent)]) expect(() => parseStageCommitReceipt({ ...value, parent_refs })).toThrow()
    expect(() => parseStageCommitReceipt({ ...stageReceipt(), depth: 2 })).toThrow()
    expect(() => parseStageCommitReceipt({ ...value, parent_refs: [{ ...parent, content_sha256: 'not-a-hash' }, value.parent_refs[1]] })).toThrow()
  })
  it('rejects impossible stage ordinals, action identities, unsafe hashes and private worker fields', () => {
    for (const changed of [{ ordinal: 4 }, { ordinal: -1 }, { depth: 4 }, { action_ordinal: 21 }, { action_ordinal: 0 }, { model_request_id: 'model-1' }, { model_request_id: 'https://example.org/model-0001' }, { result_sha256: 'A'.repeat(64) }, { stage_id: '../private' }, { lease_owner: 'Synthetic private worker' }]) expect(() => parseStageCommitReceipt({ ...stageReceipt(), ...changed })).toThrow()
  })
})

describe('saved discovery projections and exact evidence', () => {
  it('preserves all completed stages and actual whole-run usage/cost separately from immutable commit prefixes', () => {
    const total = parseDiscoverySummary(summary()), earlier = parseDiscoveryStage(stageDetail(0)), later = parseDiscoveryStage(stageDetail(2))
    expect(total.stages).toHaveLength(3); expect(total.run_status).toBe('interrupted'); expect(total.selected_final_stage).toBeNull()
    expect(total.total_usage.model_calls).toBe(20); expect(earlier.result.usage.model_calls).toBe(6); expect(later.result.usage.model_calls).toBe(18)
    expect(total.accounting).toEqual({ counted_spend_microdollars: '123000', open_hold_microdollars: '7000', unknown_hold_microdollars: '3000' })
    expect(total.stop_reason).toBe('model_call_limit')
  })
  it('keeps an explicitly chosen earlier stage without hiding later calls or ranking by confidence', () => {
    const original = summary(), stages = original.stages.map((stage, index) => ({ ...stage, assessment: assessment({ confidence_score: index === 2 ? 99 : 37 }) })), chosen = parseDiscoverySummary({ ...original, stages, selected_final_stage: stages[0], run_status: 'succeeded', stop_reason: 'The model explicitly preferred the earlier bounded bridge.' })
    expect(chosen.selected_final_stage?.stage_id).toBe(STAGES[0]); expect(chosen.stages.at(-1)?.assessment.confidence_score).toBe(99)
    expect(chosen.total_usage.model_calls).toBe(20)
    const lateStop = parseDiscoverySummary({ ...original, selected_final_stage: original.stages[0], run_status: 'cancelled', stop_reason: 'cancel_requested' })
    expect(lateStop.selected_final_stage?.stage_id).toBe(STAGES[0]); expect(lateStop.run_status).toBe('cancelled')
    expect(parseDiscoverySummary({ ...original, run_status: 'succeeded' }).selected_final_stage).toBeNull()
  })
  it('requires an exact selected receipt and cannot infer a preference from the last stage or its score', () => {
    const value = summary()
    expect(() => parseDiscoverySummary({ ...value, selected_final_stage: { ...value.stages[0], result_sha256: 'f'.repeat(64) } })).toThrow()
    expect(() => parseDiscoverySummary({ ...value, selected_final_stage: { ...value.stages[0], assessment: assessment({ confidence_score: 99 }) } })).toThrow()
    expect(parseDiscoverySummary(value).selected_final_stage).toBeNull()
  })
  it('rejects future/same-stage parents, changed original hashes, duplicate results and noncontiguous commit histories', () => {
    const value = summary(), higher = value.stages[2]
    for (const stages of [[value.stages[1]], [value.stages[0], value.stages[0]], [value.stages[0], value.stages[1], { ...higher, result_id: value.stages[0].result_id }], [value.stages[0], value.stages[1], { ...higher, parent_refs: [{ ...higher.parent_refs[0], content_sha256: 'f'.repeat(64) }, higher.parent_refs[1]] }], [value.stages[0], { ...value.stages[1], parent_refs: [{ result_id: higher.result_id, run_id: RUN, content_sha256: higher.result_sha256 }, { result_id: value.stages[0].result_id, run_id: RUN, content_sha256: value.stages[0].result_sha256 }], depth: 3 }, higher]]) expect(() => parseDiscoverySummary({ ...value, stages })).toThrow()
    expect(() => parseDiscoverySummary({ ...value, discovery_intent: intent({ max_stages: 2 }) })).toThrow()
    expect(() => parseDiscoverySummary({ ...value, discovery_intent: intent({ max_depth: 1 }) })).toThrow()
    expect(() => parseDiscoverySummary({ ...value, stages: value.stages.map((stage, index) => index === 2 ? { ...stage, action_ordinal: 12 } : stage) })).toThrow()
  })
  it('measures local depth from this run only, leaving earlier-run anchors as explicit premises', () => {
    const external = ['77777777-7777-4777-8777-777777777777', '88888888-8888-4888-8888-888888888888'].map((result_id) => ({ result_id, run_id: '99999999-9999-4999-8999-999999999999', content_sha256: HASH }))
    const stage = stageReceipt(0, { parent_refs: external, depth: 1 })
    expect(parseDiscoverySummary(summary({ stages: [stage], discovery_intent: intent({ max_stages: 1, max_depth: 1 }) })).stages[0].depth).toBe(1)
    expect(() => parseDiscoverySummary(summary({ stages: [{ ...stage, depth: 2 }] }))).toThrow()
  })
  it('preserves arbitrarily large canonical decimal totals without Number rounding or a false zero-cost inference', () => {
    const amount = '900719925474099312345', value = summary({ total_usage: { model_calls: 20, input_tokens_reported: amount, output_tokens_reported: amount, calls_without_usage: 1 }, accounting: { counted_spend_microdollars: amount, open_hold_microdollars: amount, unknown_hold_microdollars: amount } })
    expect(parseDiscoverySummary(value).accounting.counted_spend_microdollars).toBe(amount)
    expect(parseDiscoverySummary(value).total_usage.input_tokens_reported).toBe(amount)
    for (const total of [100, '001', '-1', '1.0']) expect(() => parseDiscoverySummary({ ...value, accounting: { ...value.accounting, open_hold_microdollars: total } })).toThrow()
    expect(() => parseDiscoverySummary(summary({ accounting: { counted_spend_microdollars: '0', open_hold_microdollars: '1', unknown_hold_microdollars: '2' } }))).toThrow()
    expect(() => parseDiscoverySummary(summary({ total_usage: { ...value.total_usage, calls_without_usage: 21 } }))).toThrow()
  })
  it('binds support edges to every ordered original parent and keeps revision edges separate', () => {
    const value = stageDetail(2)
    expect(parseDiscoveryStage(value)).toEqual(value)
    const revision = { kind: 'revision' as const, ordinal: 2, reference: { result_id: RESULTS[1], run_id: RUN, content_sha256: stageReceipt(1).result_sha256 }, stage_id: STAGES[1] }
    expect(parseDiscoveryStage({ ...value, parent_edges: [...value.parent_edges, revision] }).parent_edges.at(-1)?.kind).toBe('revision')
    for (const parent_edges of [value.parent_edges.slice(0, 1), [...value.parent_edges].reverse(), [{ ...value.parent_edges[0], ordinal: 1 }, value.parent_edges[1]], [{ ...value.parent_edges[0], reference: { ...value.parent_edges[0].reference, content_sha256: 'f'.repeat(64) } }, value.parent_edges[1]], [...value.parent_edges, { ...revision, ordinal: 0 }]]) expect(() => parseDiscoveryStage({ ...value, parent_edges })).toThrow()
    expect(() => parseDiscoveryStage({ ...value, result: { ...value.result, memory_reads: [] } })).toThrow()
  })
  it('checks same-run stage edge IDs against earlier persisted receipts in the complete bounded list', () => {
    expect(parseDiscoveryStageHistory(stageHistory()).stages).toHaveLength(3)
    const value = stageHistory(), child = value.stages[2]
    const parent_edges = [{ ...child.parent_edges[0], stage_id: STAGES[1] }, child.parent_edges[1]]
    expect(() => parseDiscoveryStageHistory({ ...value, stages: [value.stages[0], value.stages[1], { ...child, parent_edges }] })).toThrow()
    expect(() => parseDiscoveryStage({ ...child, parent_edges: [{ ...child.parent_edges[0], stage_id: null }, child.parent_edges[1]] })).toThrow()
    expect(() => parseDiscoveryStage({ ...child, parent_edges: [{ ...child.parent_edges[0], stage_id: child.stage.stage_id }, child.parent_edges[1]] })).toThrow()
  })
  it('preserves server alias closure groups without counting distinct handles as independent papers', () => {
    const value = stageDetail(), merged = { paper_id: 'paper-1', source_ids: value.result.sources.map((source) => source.source_id) }
    const currentProjection = parseDiscoveryStage({ ...value, primary_paper_groups: [merged], status: 'needs_review' })
    expect(currentProjection.primary_paper_groups).toEqual([merged]); expect(currentProjection.status).toBe('needs_review')
    expect(currentProjection.stage).toEqual(value.stage)
    expect(() => parseDiscoveryStage({ ...value, primary_paper_groups: [merged] })).toThrow()
    expect(parseDiscoveryStage({ ...value, primary_paper_groups: [merged], status: 'unsupported' }).status).toBe('unsupported')
  })
  it('requires primary groups to be an exact first-citation-ordered partition and exposes no uncited connector', () => {
    const value = stageDetail(), groups = value.primary_paper_groups
    for (const primary_paper_groups of [[], [groups[0]], [groups[0], groups[0]], [{ ...groups[0], paper_id: 'paper-3' }, groups[1]], [{ ...groups[0], source_ids: ['fixture:uncited-alias-connector'] }, groups[1]], [...groups].reverse(), [{ ...groups[0], source_ids: [...groups[0].source_ids, groups[1].source_ids[0]] }, groups[1]]]) expect(() => parseDiscoveryStage({ ...value, primary_paper_groups })).toThrow()
  })
  it('requires complete exact Bridge quotations and rejects missing, forged, orphaned or misclassified source passages', () => {
    const value = stageDetail(), result = value.result, first = result.quotes[0]
    for (const changed of [{ quotes: [first] }, { quotes: [{ ...first, quote: 'Synthetic invented quotation.' }, result.quotes[1]] }, { quotes: [{ ...first, content_kind: 'full_text' }, result.quotes[1]] }, { quotes: [{ ...first, start: 1 }, result.quotes[1]] }, { sources: result.sources.slice(0, 1) }, { sources: [...result.sources, result.sources[0]] }, { sources: [...result.sources, { ...result.sources[0], source_id: 'fixture:uncited-body' }] }, { mode: 'explore' }, { status: 'answer' }]) expect(() => parseDiscoveryStage({ ...value, result: { ...result, ...changed } })).toThrow()
    expect(parseDiscoveryStage(value).result.sources[0]).not.toHaveProperty('coverage')
  })
  it('preserves window provenance, complete-source hashes and Unicode-relative offsets without a whole-paper claim', () => {
    const value = stageDetail(), result = value.result, original = result.sources[0], snapshot = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', body = `🧠 ${original.text}`, coverage = { snapshot_id: snapshot, snapshot_sha256: 'c'.repeat(64), arxiv_id: BIBLIOGRAPHY.arxiv_id, arxiv_version: 2, start: 100, end: 100 + Array.from(body).length, total: 10000 }, sourceId = `arxiv-html:${snapshot}:100:${coverage.end}`
    const source = { ...original, source_id: sourceId, text: body, content_kind: 'full_text' as const, coverage, bibliography: BIBLIOGRAPHY, url: 'https://arxiv.org/html/2610.00001v2' }, quote = { ...result.quotes[0], source_id: sourceId, content_kind: 'full_text' as const, start: 2, end: 2 + Array.from(original.text).length }
    const bridge = result.finding
    if (bridge?.outcome !== 'bridge') throw new Error('Invalid synthetic bridge fixture')
    const changed: DiscoveryStage = { ...value, result: { ...result, sources: [source, result.sources[1]], quotes: [quote, result.quotes[1]], finding: { ...bridge, evidence: [{ ...bridge.evidence[0], source_id: sourceId }, bridge.evidence[1]] } }, primary_paper_groups: [{ paper_id: 'paper-1', source_ids: [sourceId] }, value.primary_paper_groups[1]] }
    const parsed = parseDiscoveryStage(changed)
    expect(parsed.result.sources[0].coverage).toEqual(coverage); expect(parsed.result.quotes[0].start + coverage.start).toBe(102)
    expect(parsed.result.sources[0].coverage?.snapshot_sha256).not.toBe(parsed.result.quotes[0].source_sha256)
    expect(() => parseDiscoveryStage({ ...changed, result: { ...changed.result, quotes: [{ ...quote, start: 3, end: quote.end + 1 }, result.quotes[1]] } })).toThrow()
  })
  it('matches all immutable receipt facts across reads while permitting current group/status changes', () => {
    const detail = stageDetail(), receipt = stageReceipt()
    expect(stageMatchesReceipt(detail, receipt)).toBe(true)
    expect(stageMatchesReceipt({ ...detail, status: 'unsupported' }, receipt)).toBe(true)
    expect(stageMatchesReceipt(detail, { ...receipt, result_sha256: 'f'.repeat(64) })).toBe(false)
    expect(stageMatchesReceipt(detail, { ...receipt, assessment: assessment({ confidence_score: 99 }) })).toBe(false)
    expect(stageMatchesReceipt(detail, { ...receipt, action_ordinal: 7 })).toBe(false)
  })
})

describe('owned read-only discovery transport', () => {
  it('reads summary, all bounded stages and exact detail with cookie authentication only and no bootstrap or model request', async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL, _options?: RequestInit) => { const path = input.toString(); return Response.json(path.endsWith(`/stages/${STAGES[0]}`) ? stageDetail() : path.endsWith('/stages') ? stageHistory() : summary()) }); vi.stubGlobal('fetch', fetch)
    await getDiscoverySummary(RUN); await listDiscoveryStages(RUN); await getDiscoveryStage(RUN, STAGES[0])
    expect(fetch.mock.calls.map(([path]) => path)).toEqual([`/api/v1/runs/${RUN}/discovery`, `/api/v1/runs/${RUN}/discovery/stages`, `/api/v1/runs/${RUN}/discovery/stages/${STAGES[0]}`])
    for (const [, options] of fetch.mock.calls) { expect(options).toMatchObject({ method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error', referrerPolicy: 'no-referrer' }); expect(options?.body).toBeUndefined(); expect(options?.headers).toEqual({ Accept: 'application/json' }) }
    expect(fetch).toHaveBeenCalledTimes(3)
  })
  it('binds every response to the exact requested run/stage and rejects a foreign result envelope', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(summary())))
    await expect(getDiscoverySummary(STAGES[0])).rejects.toMatchObject({ code: 'invalid_response' })
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(stageHistory())))
    await expect(listDiscoveryStages(STAGES[0])).rejects.toMatchObject({ code: 'invalid_response' })
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(stageDetail())))
    await expect(getDiscoveryStage(RUN, STAGES[1])).rejects.toMatchObject({ code: 'invalid_response' })
    await expect(getDiscoveryStage(STAGES[1], STAGES[0])).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it.each([401, 403])('clears browser-private state through session_expired for HTTP%s without interpreting untrusted error text', async (status) => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('Synthetic private access error body', { status })))
    await expect(getDiscoverySummary(RUN)).rejects.toMatchObject({ code: 'session_expired' })
  })
  it('uses one indistinguishable missing-discovery error and redacts raw storage/provider messages', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ detail: 'discovery_not_found' }, { status: 404 })))
    await expect(getDiscoveryStage(RUN, STAGES[0])).rejects.toMatchObject({ code: 'discovery_not_found' })
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ detail: 'Synthetic secret database stack or source body' }, { status: 503 })))
    await expect(getDiscoverySummary(RUN)).rejects.toMatchObject({ code: 'unavailable' })
  })
  it('rejects noncanonical UUID locators before making any request', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
    for (const runId of ['../foreign', RUN.toUpperCase(), 'https://example.org/result']) await expect(getDiscoverySummary(runId)).rejects.toMatchObject({ code: 'invalid_request' })
    await expect(getDiscoveryStage(RUN, '../stage')).rejects.toMatchObject({ code: 'invalid_request' })
    expect(fetch).not.toHaveBeenCalled()
  })
  it('enforces endpoint byte bounds and fatal UTF-8 while permitting the larger bounded full-stage list', async () => {
    for (const response of [new Response('{}', { headers: { 'content-length': '4194305' } }), new Response(' '.repeat(4194305)), new Response(new Uint8Array([0xc3, 0x28]))]) { vi.stubGlobal('fetch', vi.fn(async () => response)); await expect(getDiscoverySummary(RUN)).rejects.toMatchObject({ code: 'invalid_response' }) }
    vi.stubGlobal('fetch', vi.fn(async () => new Response(`${' '.repeat(4 * 1024 * 1024)}${JSON.stringify(stageHistory())}`)))
    expect((await listDiscoveryStages(RUN)).stages).toHaveLength(3)
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { headers: { 'content-length': '16777217' } })))
    await expect(listDiscoveryStages(RUN)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('fences a delayed private read after caller abort and sends no pre-aborted request', async () => {
    const controller = new AbortController(); let resolve!: (response: Response) => void
    const fetch = vi.fn(() => new Promise<Response>((done) => { resolve = done })); vi.stubGlobal('fetch', fetch)
    const pending = getDiscoverySummary(RUN, controller.signal); controller.abort(); resolve(Response.json(summary()))
    await expect(pending).rejects.toMatchObject({ code: 'aborted' })
    await expect(listDiscoveryStages(RUN, controller.signal)).rejects.toMatchObject({ code: 'aborted' })
    expect(fetch).toHaveBeenCalledTimes(1)
  })
  it('bounds unavailable reads by timeout without retrying or creating work', async () => {
    vi.useFakeTimers()
    const fetch = vi.fn(async (_input: RequestInfo | URL, options?: RequestInit) => new Promise<Response>((_resolve, reject) => options?.signal?.addEventListener('abort', () => reject(new DOMException('Synthetic timeout', 'AbortError')), { once: true }))); vi.stubGlobal('fetch', fetch)
    const pending = getDiscoverySummary(RUN), assertion = expect(pending).rejects.toMatchObject({ code: 'unavailable' })
    await vi.advanceTimersByTimeAsync(10000); await assertion
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(fetch.mock.calls[0][1]?.method).toBe('GET')
  })
})
