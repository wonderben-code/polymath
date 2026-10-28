import type { BridgeFinding, ResearchResult, SourceDocument, VerifiedQuote } from '../research/types'
import type { AssessmentBasis, AssessmentCapabilities, AssessmentJob, AssessmentPayload, AssessmentQuote, AssessmentRecord, AssessmentResult, CreateAssessmentJob, FindingHead, FindingVersion, ObservedAssessmentSource } from './types'

export const ROOT = '11111111-1111-4111-8111-111111111111'
export const RUN = '22222222-2222-4222-8222-222222222222'
export const WATCH = '33333333-3333-4333-8333-333333333333'
export const VERSION = '44444444-4444-4444-8444-444444444444'
export const ASSESSMENT = '55555555-5555-4555-8555-555555555555'
export const CANDIDATE = '66666666-6666-4666-8666-666666666666'
export const SOURCE_RUN = '77777777-7777-4777-8777-777777777777'
export const RETRIEVAL = '88888888-8888-4888-8888-888888888888'
export const LEFT_RETRIEVAL = '99999999-9999-4999-8999-999999999999'
export const RIGHT_RETRIEVAL = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
export const LEFT_VERSION = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
export const RIGHT_VERSION = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
export const HASH = 'a'.repeat(64)
export const DATE = '2099-10-28T00:00:00Z'
export const LEFT_QUOTE = 'Synthetic original left abstract passage.'
export const RIGHT_QUOTE = 'Synthetic original right abstract passage.'
export const NEW_QUOTE = 'Synthetic newly collected right abstract passage.'

export function capabilities(available = true): AssessmentCapabilities { return { execution_available: available, reason: available ? null : 'research_unavailable', automatic_reassessment: false } }
export function head(overrides: Partial<FindingHead> = {}): FindingHead {
  return { requested_result_id: ROOT, root_result_id: ROOT, current_result_id: ROOT, current_run_id: SOURCE_RUN, state: 'supported', revision: 0, last_assessment_id: null, is_current: true, is_supported: true, is_historical: false, can_generate: true, ...overrides }
}
export function request(overrides: Partial<CreateAssessmentJob> = {}): CreateAssessmentJob { return { source_result_id: ROOT, watch_id: WATCH, new_source_version_ids: [VERSION], expected_head_revision: 0, ...overrides } }
export function job(overrides: Partial<AssessmentJob> = {}): AssessmentJob {
  return { id: RUN, root_result_id: ROOT, source_result_id: ROOT, source_sha256: HASH, watch_id: WATCH, new_source_version_ids: [VERSION], expected_head_revision: 0, mode: 'explore', task_kind: 'reassess_finding', goal: 'Synthetic reassessment, not scientific evidence.', status: 'queued', created_at: DATE, updated_at: DATE, result_id: null, artifact_id: null, assessment_id: null, error_code: null, external_outcome_unknown: false, ...overrides }
}

export function bridge(revised = false): BridgeFinding {
  return { outcome: 'bridge', title: revised ? 'Synthetic revised bridge' : 'Synthetic prior bridge', field_left: 'Synthetic field A', field_right: 'Synthetic field B', explanation: 'This synthetic fixture is not a scientific finding.', mappings: [{ left: 'Synthetic left structure', right: 'Synthetic right structure', assumption: 'Synthetic model assumption.' }], open_question: 'Does this synthetic comparison hold?', strongest_objection: 'Synthetic fixtures do not establish science.', objection_verdict: 'Scientific support remains unverified.', novelty_limitations: 'No claim of real novelty.', evidence: [{ source_id: 'fixture:left', side: 'left', quote: LEFT_QUOTE, supports: 'Synthetic left support.' }, { source_id: 'fixture:right', side: 'right', quote: revised ? NEW_QUOTE : RIGHT_QUOTE, supports: 'Synthetic right support.' }] }
}
function priorQuotes(revised = false): VerifiedQuote[] {
  return bridge(revised).evidence.map((quote) => ({ ...quote, claim_id: null, start: 0, end: Array.from(quote.quote).length, source_sha256: quote.side === 'right' && revised ? 'b'.repeat(64) : HASH, content_kind: 'abstract' }))
}
export function observedSources(): ObservedAssessmentSource[] {
  return [{ retrieval_id: LEFT_RETRIEVAL, version_id: LEFT_VERSION, source_id: 'fixture:left', title: 'Synthetic left source', url: 'https://example.org/synthetic-left', content_sha256: HASH, content_kind: 'abstract' }, { retrieval_id: RIGHT_RETRIEVAL, version_id: RIGHT_VERSION, source_id: 'fixture:right', title: 'Synthetic prior right source', url: 'https://example.org/synthetic-right', content_sha256: HASH, content_kind: 'abstract' }, { retrieval_id: RETRIEVAL, version_id: VERSION, source_id: 'fixture:right', title: 'Synthetic new right source version', url: 'https://example.org/synthetic-right', content_sha256: 'b'.repeat(64), content_kind: 'abstract' }]
}
export function basis(overrides: Partial<AssessmentBasis> = {}): AssessmentBasis {
  return { prior_reference: { result_id: ROOT, run_id: SOURCE_RUN, content_sha256: HASH }, mode: 'explore', prior_state: 'supported', prior_finding: bridge(), prior_quotes: priorQuotes(), field_selections: [], sources: observedSources().map((source, index) => ({ ...source, role: index === 2 ? 'new' : 'historical' })), ...overrides }
}
export function revisedResearchResult(hydrated = false): ResearchResult {
  const sourceDocuments: SourceDocument[] = [observedSources()[0], observedSources()[2]].map((source, index) => ({ source_id: source.source_id, title: source.title, url: source.url, text: index ? NEW_QUOTE : LEFT_QUOTE, content_kind: 'abstract', retrieved_at: DATE, extraction_method: 'synthetic_fixture' }))
  return { status: 'bridge', mode: 'explore', finding: bridge(true), field_selections: [], memory_reads: [basis().prior_reference], sources: hydrated ? sourceDocuments : [], quotes: priorQuotes(true), events: [], usage: { model_calls: 1, input_tokens_reported: 40, output_tokens_reported: 80, calls_without_usage: 0 }, error_code: null, validation_scope: 'source_identity_and_quote_integrity' }
}
export function result(overrides: Partial<AssessmentResult> = {}): AssessmentResult {
  const quotes: AssessmentQuote[] = [{ retrieval_id: LEFT_RETRIEVAL, source_id: 'fixture:left', source_sha256: HASH, content_kind: 'abstract', quote: LEFT_QUOTE, supports: 'Synthetic old premise.', start: 0, end: Array.from(LEFT_QUOTE).length }, { retrieval_id: RETRIEVAL, source_id: 'fixture:right', source_sha256: 'b'.repeat(64), content_kind: 'abstract', quote: NEW_QUOTE, supports: 'Synthetic newly collected passage.', start: 0, end: Array.from(NEW_QUOTE).length }]
  const disposition = overrides.disposition ?? 'unchanged'
  return { status: 'completed', disposition, rationale: [{ text: 'Synthetic comparison of exact original and newly collected versions.', citations: quotes.map(({ retrieval_id, quote, supports }) => ({ retrieval_id, quote, supports })) }], limitations: ['Synthetic fixtures do not demonstrate scientific validity.'], observed_retrieval_ids: observedSources().map((source) => source.retrieval_id), selected_retrieval_ids: disposition === 'revised' ? [LEFT_RETRIEVAL, RETRIEVAL] : [], quotes, revised_result: disposition === 'revised' ? revisedResearchResult() : null, search_purposes: ['counterevidence', 'prior_work'], events: [{ sequence: 1, kind: 'assessment_complete', summary: 'Synthetic assessment saved.' }], usage: { model_calls: 1, input_tokens_reported: 60, output_tokens_reported: 100, calls_without_usage: 0 }, error_code: null, reason: null, validation_scope: 'version_bound_quote_integrity', ...overrides }
}
export function payload(overrides: Partial<AssessmentPayload> = {}): AssessmentPayload { const conclusion = overrides.result ?? result(); return { basis: basis(), result: conclusion, verified_passages: conclusion.quotes, observed_sources: observedSources(), ...overrides } }
export function record(overrides: Partial<AssessmentRecord> = {}): AssessmentRecord {
  const disposition = overrides.disposition ?? 'unchanged'
  return { id: ASSESSMENT, run_id: RUN, root_result_id: ROOT, source_result_id: ROOT, source_run_id: SOURCE_RUN, source_sha256: HASH, watch_id: WATCH, new_source_version_ids: [VERSION], expected_head_revision: 0, disposition, applied: disposition !== 'inconclusive', head_revision: disposition === 'inconclusive' ? 0 : 1, candidate_result_id: disposition === 'revised' ? CANDIDATE : null, payload: payload({ result: result({ disposition }) }), payload_sha256: HASH, basis_sha256: HASH, created_at: DATE, ...overrides }
}
export function findingVersion(overrides: Partial<FindingVersion> = {}): FindingVersion {
  return { id: CANDIDATE, run_id: RUN, task_kind: 'reassess_finding', run: { id: RUN, task_kind: 'reassess_finding', mode: 'explore', goal: 'Synthetic reassessment.', status: 'succeeded', created_at: DATE, updated_at: DATE, result_id: null, artifact_id: null, assessment_id: ASSESSMENT, error_code: null, external_outcome_unknown: false }, payload: revisedResearchResult(true), assessment: head({ requested_result_id: CANDIDATE, current_result_id: CANDIDATE, current_run_id: RUN, revision: 1, last_assessment_id: ASSESSMENT }), ...overrides }
}
