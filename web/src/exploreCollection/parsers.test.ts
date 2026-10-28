import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { createElement } from 'react'
import { pythonFixtures } from './fixtures'
import type { ExploreBridgeHistory, ExploreCollectionSummary, ExploreRankingReceipt } from './types'
import type { ExploreCollectionProfileBinding } from './profile'
import type { NativeTaskProfile } from '../budget/activationTypes'
import { parseExploreBridgeDetail, parseExploreBridgeHistory, parseExploreCollectionAccess, parseExploreCollectionIntent, parseExploreCollectionSummary, parseExploreRankingHistory, parseExploreRankingReceipt, parseExploreReceipt, parseOptionalExploreIntent, referenceFor } from './parsers'
import { chooseExploreIntent, collectionPresentation } from './presentation'
import { parseExploreCollectionProfileBinding, parseOperatorExploreMarker, parseOptionalExploreProfile } from './profile'
import { ExploreCards } from './ExploreCards'
const SHA = 'a'.repeat(64)
const summary = () => structuredClone(pythonFixtures.summary) as ExploreCollectionSummary
const bridges = () => structuredClone(pythonFixtures.bridges) as ExploreBridgeHistory
const capability = (maximum = 4) => ({ protocol: 'polymath-explore-collection-access-v1', activation_sha256: SHA, explore_collection_intent: { protocol: 'polymath-explore-collection-v1', max_bridges: maximum } })
const parse = () => parseExploreCollectionSummary(summary())

describe('explicit separately versioned Explore capability and native review', () => {
  it('preserves absence and rejects explicit null/missing/malformed intent rather than supplying a default', () => {
    expect(parseOptionalExploreIntent(undefined)).toBeUndefined()
    expect(parseExploreCollectionIntent({ protocol: 'polymath-explore-collection-v1', max_bridges: 3 })).toEqual({ protocol: 'polymath-explore-collection-v1', max_bridges: 3 })
    for (const value of [null, {}, { protocol: 'polymath-explore-collection-v1' }, { protocol: 'wrong', max_bridges: 3 }, { protocol: 'polymath-explore-collection-v1', max_bridges: 3, mode: 'surprise' }]) expect(() => parseOptionalExploreIntent(value)).toThrow()
  })
  it.each([true, '3', 0, 5, 1.5, null])('rejects non-strict or out-of-range maximum %s', (maximum) => expect(() => parseExploreCollectionIntent({ protocol: 'polymath-explore-collection-v1', max_bridges: maximum })).toThrow())
  it('accepts exactly the new3key projection and never mutates D2/SC projections', () => {
    expect(parseExploreCollectionAccess(capability())).toEqual(capability())
    expect(parseExploreCollectionAccess({ ...capability(), explore_collection_intent: null }).activation_sha256).toBe(SHA)
    expect(parseExploreCollectionAccess({ ...capability(), activation_sha256: null, explore_collection_intent: null })).toEqual({ protocol: 'polymath-explore-collection-access-v1', activation_sha256: null, explore_collection_intent: null })
  })
  it.each([{ activation_sha256: null }, { activation_sha256: 'A'.repeat(64) }, { profiles: [] }, { model_id: 'secret' }, { account: 'private' }, { capabilities: [] }, { reason: 'invented' }, { protocol: 'polymath-research-access-v1' }])('rejects incoherence or forbidden projection data %s', (change) => expect(() => parseExploreCollectionAccess({ ...capability(), ...change })).toThrow())
  it('selects only the same-hash approved ordinary seeded variant; narrower requests never rewrite approved scope', () => {
    const approved = parseExploreCollectionAccess(capability()), initial = JSON.stringify(approved)
    expect(chooseExploreIntent({ mode: 'explore', initialParentIds: [], expectedActivationSha256: SHA, access: approved })).toEqual({ protocol: 'polymath-explore-collection-v1', max_bridges: 3 })
    expect(chooseExploreIntent({ mode: 'explore', initialParentIds: [], expectedActivationSha256: SHA, access: approved, requestedMaximum: 2 })?.max_bridges).toBe(2)
    expect(JSON.stringify(approved)).toBe(initial)
    expect(() => chooseExploreIntent({ mode: 'explore', initialParentIds: [], expectedActivationSha256: 'b'.repeat(64), access: approved })).toThrow()
    expect(() => chooseExploreIntent({ mode: 'explore', initialParentIds: [], expectedActivationSha256: SHA, access: parseExploreCollectionAccess(capability(1)), requestedMaximum: 2 })).toThrow()
  })
  it('preserves all original parent-selected F3, Ask, Surprise and baseline bodies', () => {
    const approved = parseExploreCollectionAccess(capability()), parents = summary().stages.slice(0, 2).map((stage) => stage.result_id)
    for (const mode of ['ask', 'surprise'] as const) expect(chooseExploreIntent({ mode, initialParentIds: [], expectedActivationSha256: SHA, access: approved })).toBeUndefined()
    expect(chooseExploreIntent({ mode: 'explore', initialParentIds: parents, expectedActivationSha256: SHA, access: approved })).toBeUndefined()
    expect(chooseExploreIntent({ mode: 'explore', initialParentIds: [], expectedActivationSha256: SHA, access: parseExploreCollectionAccess({ ...capability(), explore_collection_intent: null }) })).toBeUndefined()
  })
  it('accepts the genuine draft Python profile/digest and keeps original quantitative research limits at16actions', () => {
    const original = pythonFixtures.ordinary_profile as NativeTaskProfile, profile = parseExploreCollectionProfileBinding(pythonFixtures.profile, original)
    expect(profile).toEqual(pythonFixtures.profile); expect(profile.profile.max_actions).toBe(16); expect(profile.profile.max_bridges).toBe(4)
    expect(parseOptionalExploreProfile(undefined, original)).toBeUndefined(); expect(parseOperatorExploreMarker(undefined)).toBeUndefined()
    expect(parseOperatorExploreMarker({ protocol: 'polymath-explore-collection-v1', max_bridges: 2 })?.max_bridges).toBe(2)
    expect(() => parseOptionalExploreProfile(null)).toThrow(); expect(() => parseOperatorExploreMarker(null)).toThrow()
    for (const change of [{ max_actions: 20 }, { task_kind: 'formalise' }, { max_model_calls: original.max_model_calls - 1 }, { controller_protocol: 'polymath-meta-discovery-controller-v1' }, { max_bridges: 5 }, { request_timeout_seconds: 1, model_timeout_seconds: 31 }]) {
      const value = structuredClone(pythonFixtures.profile) as ExploreCollectionProfileBinding; Object.assign(value.profile, change); expect(() => parseExploreCollectionProfileBinding(value, original)).toThrow()
    }
  })
})

describe('exact model ranking receipts and immutable historical coverage', () => {
  it('accepts actual Python stage/ranking DTOs, whole-run strings, and cited source hydration', () => {
    expect(parse()).toEqual(summary()); expect(parseExploreBridgeHistory(bridges())).toEqual(bridges())
    expect(parseExploreRankingHistory(pythonFixtures.rankings).rankings).toHaveLength(2)
    expect(parse().accounting.counted_spend_microdollars).toBe('9007199254740993')
    expect(bridges().stages.map((detail) => detail.result.usage.model_calls)).toEqual([4, 8, 12]); expect(parse().total_usage.model_calls).toBe(20)
  })
  it('preserves model order when it differs from every descending confidence-score sort', () => {
    const view = collectionPresentation(parse())
    expect(view.ranked.map((card) => card.receipt.ordinal)).toEqual([2, 1]); expect(view.ranked.map((card) => card.receipt.assessment.confidence_score)).toEqual([15, 55]); expect(view.excluded[0].receipt.assessment.confidence_score).toBe(95)
    expect(view.ranked[0].rationale).toContain('lowest-scored'); expect(view.isExplicitFinal).toBe(true)
  })
  it('keeps interim prefix coverage and later unranked bridges separate without reranking', () => {
    const value = summary(); value.selected_final_ranking = null; value.rankings.pop(); value.run_status = 'running'; value.stop_reason = null
    const view = collectionPresentation(parseExploreCollectionSummary(value)); expect(view.ranked.map((entry) => entry.receipt.ordinal)).toEqual([1, 0]); expect(view.outsideRanking.map((receipt) => receipt.ordinal)).toEqual([2]); expect(view.coverageCount).toBe(2); expect(view.isExplicitFinal).toBe(false)
    expect(() => collectionPresentation(parse(), '99999999-9999-4999-8999-999999999999')).toThrow()
  })
  it('keeps committed cancelled/interrupted partials in discovery order when no ranking was saved', () => {
    const value = summary(); value.rankings = []; value.selected_final_ranking = null; value.run_status = 'interrupted'
    const view = collectionPresentation(parseExploreCollectionSummary(value)); expect(view.ranking).toBeNull(); expect(view.ranked).toEqual([]); expect(view.excluded).toEqual([]); expect(view.outsideRanking.map((receipt) => receipt.ordinal)).toEqual([0, 1, 2])
  })
  it('retains a genuinely saved final ranking through a late operational cancellation without claiming completion', () => {
    const value = summary(); value.run_status = 'cancelled'; const parsed = parseExploreCollectionSummary(value); expect(parsed.selected_final_ranking).not.toBeNull(); expect(parsed.run_status).toBe('cancelled')
  })
  it('handles all-excluded and empty final decisions without inventing a top finding', () => {
    const value = summary(), final = value.rankings[1]; final.excluded.push(...final.ranked.map((entry) => ({ reference: entry.reference, reason: entry.rationale }))); final.ranked = []; value.selected_final_ranking = structuredClone(final)
    const view = collectionPresentation(parseExploreCollectionSummary(value)); expect(view.ranked).toEqual([]); expect(view.excluded).toHaveLength(3); expect(view.outsideRanking).toEqual([])
    expect(parseExploreRankingReceipt(pythonFixtures.empty_ranking).ranked).toEqual([]); const empty = summary(); empty.stages = []; empty.rankings = [structuredClone(pythonFixtures.empty_ranking) as ExploreRankingReceipt]; empty.selected_final_ranking = empty.rankings[0]; expect(collectionPresentation(parseExploreCollectionSummary(empty)).ranked).toEqual([])
  })
  it.each(['duplicate', 'foreign', 'hash', 'missing', 'future', 'coverage', 'extra'])('rejects a corrupt ranked/excluded partition: %s', (kind) => {
    const value = summary(), ranking = value.rankings[1]
    if (kind === 'duplicate') ranking.excluded[0].reference = structuredClone(ranking.ranked[0].reference)
    if (kind === 'foreign') ranking.ranked[0].reference.run_id = value.stages[0].stage_id
    if (kind === 'hash') ranking.ranked[0].reference.content_sha256 = 'f'.repeat(64)
    if (kind === 'missing') ranking.ranked.pop()
    if (kind === 'future') { ranking.model_request_id = 'model-0012'; ranking.action_ordinal = 12 }
    if (kind === 'coverage') ranking.covered_through_ordinal = 1
    if (kind === 'extra') Object.assign(ranking.ranked[0], { computed_score: 100 })
    value.selected_final_ranking = structuredClone(ranking); expect(() => parseExploreCollectionSummary(value)).toThrow()
  })
  it.each(['position', 'stage-order', 'result-duplicate', 'ranking-order', 'final-coverage', 'counter', 'hold', 'unsafe-number'])('rejects changed chronology/counters/bindings: %s', (kind) => {
    const value = summary()
    if (kind === 'position') value.stages[0].action_ordinal = 17
    if (kind === 'stage-order') value.stages.reverse()
    if (kind === 'result-duplicate') value.stages[1].result_id = value.stages[0].result_id
    if (kind === 'ranking-order') value.rankings.reverse()
    if (kind === 'final-coverage') value.selected_final_ranking = value.rankings[0]
    if (kind === 'counter') value.total_usage.model_calls = 15
    if (kind === 'hold') value.accounting.unknown_hold_microdollars = '501'
    if (kind === 'unsafe-number') Object.assign(value.accounting, { counted_spend_microdollars: 9007199254740993 })
    expect(() => parseExploreCollectionSummary(value)).toThrow()
  })
  it('keeps base stages without inventing supporting parents or erasing ordinary memory leads', () => {
    const detail = bridges().stages[0]; const parent = referenceFor(detail.stage); parent.result_id = summary().stages[1].result_id; detail.result.memory_reads = [parent]
    expect(parseExploreBridgeDetail(detail).result.memory_reads).toEqual([parent]); expect(parseExploreBridgeDetail(detail).parent_edges).toEqual([])
    const changed = structuredClone(detail); changed.stage.parent_refs = [parent]; expect(() => parseExploreBridgeDetail(changed)).toThrow()
    for (const request of ['model-0000', 'model-0021', 'model-1']) expect(() => parseExploreReceipt({ ...detail.stage, model_request_id: request })).toThrow()
  })
  it('uses Unicode codepoint quote spans and authoritative paper groups with dynamic needs-review status', () => {
    const detail = bridges().stages[0]; expect(parseExploreBridgeDetail(detail)).toEqual(detail)
    detail.result.quotes[0].start++; expect(() => parseExploreBridgeDetail(detail)).toThrow()
    const merged = bridges().stages[0]; merged.primary_paper_groups = [{ paper_id: 'paper-1', source_ids: merged.result.quotes.map((quote) => quote.source_id) }]
    expect(() => parseExploreBridgeDetail(merged)).toThrow(); merged.status = 'needs_review'; expect(parseExploreBridgeDetail(merged).status).toBe('needs_review')
    const immutableRank = JSON.stringify(parse().rankings); expect(collectionPresentation(parse()).ranked.map((entry) => entry.position)).toEqual([1, 2]); expect(JSON.stringify(parse().rankings)).toBe(immutableRank)
  })
  it('rejects mixed receipt/detail snapshots and changed saved event/counter facts', () => {
    const value = parse(), details = bridges().stages; details[0].stage.result_sha256 = 'f'.repeat(64)
    expect(() => renderToStaticMarkup(createElement(ExploreCards, { summary: value, details, onInspect: () => {} }))).toThrow()
    const event = bridges().stages[0]; event.result.events = [{ sequence: 2, kind: 'saved', summary: 'Synthetic out-of-order event.' }]; expect(() => parseExploreBridgeDetail(event)).toThrow()
    const missing = bridges().stages[0]; missing.result.usage.calls_without_usage = missing.result.usage.model_calls + 1; expect(() => parseExploreBridgeDetail(missing)).toThrow()
  })
  it('renders literal model text, exact order, rationales, exclusions and uncalibrated limits without a new paid action', () => {
    const value = parse(); value.rankings[1].ranked[0].rationale = '<script>Literal model ranking rationale</script>'; value.selected_final_ranking = structuredClone(value.rankings[1])
    const html = renderToStaticMarkup(createElement(ExploreCards, { summary: value, details: bridges().stages, onInspect: () => {} }))
    expect(html.indexOf('Model rank 1 · Synthetic bridge 3')).toBeLessThan(html.indexOf('Model rank 2 · Synthetic bridge 2'))
    expect(html).toContain('&lt;script&gt;Literal model ranking rationale&lt;/script&gt;'); expect(html).not.toContain('<script>'); expect(html).toContain('uncalibrated judgement'); expect(html).toContain('Excluded from this ranking'); expect(html).not.toContain('Write paper'); expect(html).not.toContain('Publish')
  })
})
