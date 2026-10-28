import { createHash } from 'node:crypto'
import type { ResearchResult, SourceDocument } from '../research/types'
import type { DiscoveryIntent, DiscoveryStage, DiscoveryStageHistory, DiscoverySummary, StageAssessment, StageCommitReceipt } from './types'

export const RUN = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd'
export const STAGES = ['11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222', '33333333-3333-4333-8333-333333333333']
export const RESULTS = ['44444444-4444-4444-8444-444444444444', '55555555-5555-4555-8555-555555555555', '66666666-6666-4666-8666-666666666666']
export const DATE = '2026-10-28T00:00:00.123000Z'
export const HASH = 'a'.repeat(64)
const sha256 = (value: string) => createHash('sha256').update(value).digest('hex')
const PAPERS = ['Synthetic original premise from paper A.', 'Synthetic original premise from paper B.', 'Synthetic original premise from paper C.', 'Synthetic original premise from paper D.']
/** Deliberately synthetic: these helper values establish no discovery or score calibration. */
export function intent(overrides: Partial<DiscoveryIntent> = {}): DiscoveryIntent { return { protocol: 'polymath-surprise-ladder-v1', max_stages: 3, max_depth: 2, ...overrides } }
export function assessment(overrides: Partial<StageAssessment> = {}): StageAssessment { return { novelty: 'not_found_in_bounded_search', novelty_rationale: 'A synthetic bounded-search fixture found no observed matching prior mapping; it does not prove absence.', confidence: 'tentative', confidence_score: 37, confidence_scope: 'uncalibrated_model_judgement', confidence_rationale: 'This synthetic model judgement has not been calibrated or evaluated as scientific evidence.', limitations: ['Exact quotation integrity does not establish semantic support or independent experiments.', 'The bounded searches are not exhaustive.'], ...overrides } }
export function stageReceipt(ordinal = 0, overrides: Partial<StageCommitReceipt> = {}): StageCommitReceipt {
  const calls = ordinal === 0 ? 6 : ordinal === 1 ? 12 : 18
  return { stage_id: STAGES[ordinal], run_id: RUN, ordinal, result_id: RESULTS[ordinal], result_sha256: sha256(`Synthetic immutable stage result ${ordinal}`), depth: ordinal < 2 ? 1 : 2, assessment: assessment(), parent_refs: ordinal < 2 ? [] : [0, 1].map((parent) => ({ result_id: RESULTS[parent], run_id: RUN, content_sha256: stageReceipt(parent).result_sha256 })), action_ordinal: calls, model_request_id: `model-${calls.toString().padStart(4, '0')}`, ...overrides }
}
export function stageResult(ordinal = 0): ResearchResult {
  const indexes = ordinal === 0 ? [0, 1] : ordinal === 1 ? [2, 3] : [0, 3], fields = ordinal === 0 ? ['Synthetic field A', 'Synthetic field B'] : ordinal === 1 ? ['Synthetic field C', 'Synthetic field D'] : ['Synthetic field A', 'Synthetic field D']
  const sources: SourceDocument[] = indexes.map((index) => ({ source_id: `fixture:paper-${index}`, title: `Synthetic original paper ${String.fromCharCode(65 + index)}`, url: `https://example.org/synthetic-paper-${index}`, text: PAPERS[index], content_kind: 'abstract', retrieved_at: DATE, extraction_method: 'synthetic_fixture' }))
  const quotes = sources.map((source, index) => ({ source_id: source.source_id, quote: source.text, supports: 'A synthetic original premise; scientific support remains unverified.', side: index === 0 ? 'left' as const : 'right' as const, claim_id: null, start: 0, end: Array.from(source.text).length, source_sha256: sha256(source.text), content_kind: 'abstract' as const }))
  const calls = ordinal === 0 ? 6 : ordinal === 1 ? 12 : 18
  return { mode: 'surprise', status: 'bridge', finding: { outcome: 'bridge', title: `Synthetic committed bridge ${ordinal + 1}`, field_left: fields[0], field_right: fields[1], explanation: 'A bounded synthetic comparison, not a scientific discovery.', mappings: [{ left: 'Synthetic left structure', right: 'Synthetic right structure', assumption: 'This mapping is conditional on an unverified synthetic model.' }], open_question: 'Would a real empirical test support this synthetic comparison?', strongest_objection: 'Matching quoted structures does not demonstrate shared mechanisms.', objection_verdict: 'Only the explicit synthetic analogy remains; no scientific conclusion is established.', novelty_limitations: 'Bounded fixture searches establish no real novelty.', evidence: quotes.map(({ source_id, quote, supports, side }) => ({ source_id, quote, supports, side })) }, quotes, sources, field_selections: [{ fields, rationale: 'The synthetic model selected these fields for a bounded comparison.' }], memory_reads: stageReceipt(ordinal).parent_refs, events: [{ sequence: 1, kind: 'bridge_validated', summary: 'A synthetic stage passed quotation-integrity checks.' }], usage: { model_calls: calls, input_tokens_reported: calls * 30, output_tokens_reported: calls * 40, calls_without_usage: 0 }, error_code: null, validation_scope: 'source_identity_and_quote_integrity' }
}
export function stageDetail(ordinal = 0, overrides: Partial<DiscoveryStage> = {}): DiscoveryStage {
  const stage = stageReceipt(ordinal), result = stageResult(ordinal)
  return { stage, result, parent_edges: stage.parent_refs.map((reference, index) => ({ kind: 'support', ordinal: index, reference, stage_id: STAGES[index] })), primary_paper_groups: result.sources.map((source, index) => ({ paper_id: `paper-${index + 1}`, source_ids: [source.source_id] })), status: 'current_supported', ...overrides }
}
export function stageHistory(stages = [stageDetail(0), stageDetail(1), stageDetail(2)]): DiscoveryStageHistory { return { run_id: RUN, stages } }
export function summary(overrides: Partial<DiscoverySummary> = {}): DiscoverySummary { return { run_id: RUN, run_status: 'interrupted', discovery_intent: intent(), stages: [stageReceipt(0), stageReceipt(1), stageReceipt(2)], selected_final_stage: null, stop_reason: 'model_call_limit', total_usage: { model_calls: 20, input_tokens_reported: '600', output_tokens_reported: '800', calls_without_usage: 0 }, accounting: { counted_spend_microdollars: '123000', open_hold_microdollars: '7000', unknown_hold_microdollars: '3000' }, ...overrides } }
