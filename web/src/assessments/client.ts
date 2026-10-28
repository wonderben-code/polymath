import { parseBibliography } from '../research/bibliography'
import { parseResult } from '../research/client'
import type { AnswerFinding, BridgeFinding, ClaimQuote, FieldSelection, MemoryReadReference, ResearchActivityEvent, ResearchResult, VerifiedQuote } from '../research/types'
import type { AssessmentBasis, AssessmentCancellation, AssessmentCapabilities, AssessmentCitation, AssessmentDisposition, AssessmentHistory, AssessmentJob, AssessmentJobHistory, AssessmentJobStatus, AssessmentQuote, AssessmentRecord, AssessmentResult, AssessmentSourceReference, CreateAssessmentJob, FindingHead, FindingVersion, ObservedAssessmentSource } from './types'

export class AssessmentError extends Error {
  constructor(public readonly code: string, public readonly uncertain = false) { super(code); this.name = 'AssessmentError' }
}

const BASE = '/api/v1/assessments'
const UUID = /^[a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12}$/
const HASH = /^[a-f\d]{64}$/
const JOB_STATUSES: readonly AssessmentJobStatus[] = ['queued', 'running', 'waiting_retry', 'cancel_requested', 'cancelled', 'succeeded', 'failed', 'interrupted']
const DISPOSITIONS: readonly AssessmentDisposition[] = ['unchanged', 'revised', 'unsupported', 'inconclusive']
const MAX_BYTES = 2 * 1024 * 1024
const CODES = new Set(['research_unavailable', 'assessment_not_found', 'assessment_source_changed', 'assessment_head_changed', 'assessment_basis_changed', 'assessment_input_changed', 'assessment_source_not_current', 'invalid_assessment_request', 'assessment_source_already_assessed', 'assessment_source_already_cited', 'assessment_invalid_sources', 'assessment_input_too_large', 'active_run_limit', 'research_queue_full', 'assessment_queue_full'])
function invalid(): never { throw new AssessmentError('invalid_response') }
function object(value: unknown): Record<string, unknown> { return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : invalid() }
function text(value: unknown, maximum = 4000, required = true): string { return typeof value === 'string' && Array.from(value).length <= maximum && (!required || !!value.trim()) && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\uD800-\uDFFF]/u.test(value) ? value : invalid() }
function integer(value: unknown, minimum = 0, maximum = Number.MAX_SAFE_INTEGER): number { return typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum && value <= maximum ? value : invalid() }
function bool(value: unknown): boolean { return typeof value === 'boolean' ? value : invalid() }
function member<T extends string>(value: unknown, choices: readonly T[]): T { return typeof value === 'string' && choices.includes(value as T) ? value as T : invalid() }
function id(value: unknown): string { return typeof value === 'string' && UUID.test(value) ? value : invalid() }
function nullableId(value: unknown): string | null { return value === null ? null : id(value) }
function hash(value: unknown): string { return typeof value === 'string' && HASH.test(value) ? value : invalid() }
function date(value: unknown): string { const parsed = text(value, 100); return /(?:Z|[+-]\d\d:\d\d)$/.test(parsed) && Number.isFinite(Date.parse(parsed)) ? parsed : invalid() }
function code(value: unknown): string | null { return value === null ? null : typeof value === 'string' && /^[a-z_]{1,100}$/.test(value) ? value : invalid() }
function list<T>(value: unknown, parse: (item: unknown) => T, minimum = 0, maximum = 20): T[] { return Array.isArray(value) && value.length >= minimum && value.length <= maximum ? value.map(parse) : invalid() }
function distinct<T>(values: T[]): T[] { return new Set(values).size === values.length ? values : invalid() }
function versions(value: unknown): string[] { return distinct(list(value, id, 1, 5)) }
function inputId(value: string): string { if (typeof value !== 'string' || !UUID.test(value)) throw new AssessmentError('invalid_request'); return value }
function safeUrl(value: unknown): string { const parsed = text(value, 2000); try { const url = new URL(parsed); if (url.protocol !== 'https:' || url.username || url.password || /\s/u.test(parsed)) return invalid() } catch { return invalid() }; return parsed }
function normal(value: string): string { return value.trim().replace(/\s+/gu, ' ').toLowerCase().replaceAll('ß', 'ss').replaceAll('ς', 'σ') }
function equal(left: unknown, right: unknown): boolean { return JSON.stringify(left) === JSON.stringify(right) }
function quoteText(value: unknown): string { const parsed = text(value, 4000); return Array.from(parsed).length >= 10 ? parsed : invalid() }
function claimId(value: unknown): string { const parsed = text(value, 64); return /^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(parsed) ? parsed : invalid() }
function claimQuote(value: unknown): ClaimQuote { const item = object(value); return { source_id: text(item.source_id, 200), quote: quoteText(item.quote), supports: text(item.supports, 1500) } }

function finding(value: unknown): AnswerFinding | BridgeFinding {
  const item = object(value)
  if (item.outcome === 'answer') {
    const claims = list(item.claims, (raw) => {
      const claim = object(raw), label = member(claim.label, ['known', 'postulated', 'speculative'])
      const derivation = claim.derivation === null || claim.derivation === undefined ? null : text(claim.derivation, 2000)
      const caveats = claim.caveats === undefined ? [] : list(claim.caveats, (value) => text(value, 1000), 0, 6)
      if (label === 'postulated' && derivation === null || label === 'speculative' && !caveats.length) return invalid()
      return { id: claimId(claim.id), text: text(claim.text, 2000), label, evidence: list(claim.evidence, claimQuote, 1, 8), derivation, caveats }
    }, 1, 8)
    distinct(claims.map((claim) => claim.id))
    return { outcome: 'answer', question: text(item.question, 4000), summary: text(item.summary, 3000), claims, limitations: list(item.limitations, (value) => text(value, 1000), 1, 8), unanswered_questions: item.unanswered_questions === undefined ? [] : list(item.unanswered_questions, (value) => text(value, 1000), 0, 8) }
  }
  if (item.outcome !== 'bridge') return invalid()
  const left = text(item.field_left, 200), right = text(item.field_right, 200)
  if (normal(left) === normal(right)) return invalid()
  return { outcome: 'bridge', title: text(item.title, 300), field_left: left, field_right: right, explanation: text(item.explanation, 3000), mappings: list(item.mappings, (raw) => { const mapping = object(raw); return { left: text(mapping.left, 1000), right: text(mapping.right, 1000), assumption: text(mapping.assumption, 1500) } }, 1, 8), open_question: text(item.open_question, 1500), strongest_objection: text(item.strongest_objection, 2000), objection_verdict: text(item.objection_verdict, 2000), novelty_limitations: text(item.novelty_limitations, 2000), evidence: list(item.evidence, (raw) => { const item = object(raw); return { ...claimQuote(item), side: member(item.side, ['left', 'right']) } }, 2, 12) }
}
function reference(value: unknown): MemoryReadReference { const item = object(value); return { result_id: id(item.result_id), run_id: id(item.run_id), content_sha256: hash(item.content_sha256) } }
function fields(value: unknown): FieldSelection[] { return list(value, (raw) => { const item = object(raw), names = list(item.fields, (value) => text(value, 100), 2, 4); distinct(names.map(normal)); return { fields: names, rationale: text(item.rationale, 1000) } }, 0, 3) }
function activity(value: unknown): ResearchActivityEvent { const item = object(value); return { sequence: integer(item.sequence, 1), kind: text(item.kind, 64), summary: text(item.summary, 2000, false) } }
function activities(value: unknown, maximum = 100): ResearchActivityEvent[] { const parsed = list(value, activity, 0, maximum); if (parsed.some((event, index) => event.sequence !== index + 1)) return invalid(); return parsed }
function usage(value: unknown): AssessmentResult['usage'] { const item = object(value); return { model_calls: integer(item.model_calls), input_tokens_reported: integer(item.input_tokens_reported), output_tokens_reported: integer(item.output_tokens_reported), calls_without_usage: integer(item.calls_without_usage) } }
function researchQuote(value: unknown): VerifiedQuote {
  const item = object(value), quoted = quoteText(item.quote), start = integer(item.start, 0, 20000), end = integer(item.end, 1, 20000)
  const side = member(item.side, ['left', 'right', 'answer']), claim = item.claim_id === undefined || item.claim_id === null ? null : claimId(item.claim_id)
  if (end - start !== Array.from(quoted).length || (side === 'answer' ? claim === null : claim !== null)) return invalid()
  return { source_id: text(item.source_id, 200), side, claim_id: claim, quote: quoted, supports: text(item.supports, 1500), start, end, source_sha256: hash(item.source_sha256), content_kind: member(item.content_kind, ['abstract', 'full_text']) }
}
function findingQuotes(value: AnswerFinding | BridgeFinding, quotes: VerifiedQuote[]): void {
  const expected = value.outcome === 'bridge' ? value.evidence.map((item) => ({ ...item, claim_id: null })) : value.claims.flatMap((claim) => claim.evidence.map((item) => ({ ...item, side: 'answer', claim_id: claim.id })))
  if (expected.length !== quotes.length) return invalid()
  const remaining = [...quotes]
  for (const citation of expected) {
    const index = remaining.findIndex((quote) => quote.source_id === citation.source_id && quote.quote === citation.quote && quote.supports === citation.supports && quote.side === citation.side && quote.claim_id === citation.claim_id)
    if (index < 0) return invalid()
    remaining.splice(index, 1)
  }
}
function modeFinding(mode: AssessmentBasis['mode'], value: AnswerFinding | BridgeFinding, selections: FieldSelection[]): void {
  if ((mode === 'ask') !== (value.outcome === 'answer') || mode !== 'surprise' && selections.length) return invalid()
  if (mode === 'surprise' && value.outcome === 'bridge') { const selected = selections.at(-1)?.fields.map(normal) ?? []; if (!selected.includes(normal(value.field_left)) || !selected.includes(normal(value.field_right))) return invalid() }
}
function observedSource(value: unknown): ObservedAssessmentSource {
  const item = object(value)
  return { retrieval_id: id(item.retrieval_id), version_id: id(item.version_id), source_id: text(item.source_id, 200), title: text(item.title, 1000), url: safeUrl(item.url), content_sha256: hash(item.content_sha256), content_kind: member(item.content_kind, ['abstract', 'full_text']), ...(item.bibliography === undefined || item.bibliography === null ? {} : { bibliography: parseBibliography(item.bibliography, invalid) }) }
}
function sourceIdentity(value: ObservedAssessmentSource): string { return JSON.stringify([value.retrieval_id, value.version_id, value.source_id, value.title, value.url, value.content_sha256, value.content_kind, value.bibliography ?? null]) }
function premiseIdentity(value: { source_id: string; source_sha256: string; content_kind: string }): string { return JSON.stringify([value.source_id, value.source_sha256, value.content_kind]) }

export function parseAssessmentBasis(value: unknown): AssessmentBasis {
  const item = object(value), mode = member(item.mode, ['explore', 'ask', 'surprise']), prior = finding(item.prior_finding), selections = fields(item.field_selections)
  const quotes = list(item.prior_quotes, researchQuote, 1, 64)
  const sources: AssessmentSourceReference[] = list(item.sources, (raw) => ({ ...observedSource(raw), role: member(object(raw).role, ['historical', 'new']) }), 2, 69)
  distinct(sources.map((source) => source.retrieval_id))
  const historical = sources.filter((source) => source.role === 'historical'), added = sources.filter((source) => source.role === 'new')
  const premises = new Set(quotes.map(premiseIdentity)), historicalVersions = new Set(historical.map((source) => source.version_id))
  const historicalKeys = new Set(historical.map((source) => premiseIdentity({ ...source, source_sha256: source.content_sha256 })))
  if (!historical.length || historical.length > 64 || !added.length || added.length > 5 || added.some((source) => historicalVersions.has(source.version_id)) || [...premises].some((key) => !historicalKeys.has(key)) || [...historicalKeys].some((key) => !premises.has(key))) return invalid()
  distinct(added.map((source) => source.version_id)); findingQuotes(prior, quotes); modeFinding(mode, prior, selections)
  return { prior_reference: reference(item.prior_reference), mode, prior_state: member(item.prior_state, ['supported', 'unsupported']), prior_finding: prior, prior_quotes: quotes, field_selections: selections, sources }
}

function citation(value: unknown): AssessmentCitation { const item = object(value); return { retrieval_id: id(item.retrieval_id), quote: quoteText(item.quote), supports: text(item.supports, 1500) } }
function assessmentQuote(value: unknown): AssessmentQuote {
  const item = object(value), parsed = citation(item), start = integer(item.start, 0, 20000), end = integer(item.end, 1, 20000)
  if (end - start !== Array.from(parsed.quote).length) return invalid()
  return { ...parsed, source_id: text(item.source_id, 200), source_sha256: hash(item.source_sha256), content_kind: member(item.content_kind, ['abstract', 'full_text']), start, end }
}
function positiveResult(value: unknown, basis: AssessmentBasis, selected: ObservedAssessmentSource[]): ResearchResult {
  const item = object(value), proposed = finding(item.finding), mode = member(item.mode, ['explore', 'ask', 'surprise']), selections = fields(item.field_selections)
  const quotes = list(item.quotes, researchQuote, 1, 64), memory = list(item.memory_reads, reference, 1, 1)
  if (item.status !== proposed.outcome || item.validation_scope !== 'source_identity_and_quote_integrity' || mode !== basis.mode || !equal(selections, basis.field_selections) || !equal(memory, [basis.prior_reference]) || !Array.isArray(item.sources) || item.sources.length) return invalid()
  distinct(selected.map((source) => source.source_id)); modeFinding(mode, proposed, selections); findingQuotes(proposed, quotes)
  const available = new Map(selected.map((source) => [source.source_id, source]))
  for (const quote of quotes) { const source = available.get(quote.source_id); if (!source || source.content_sha256 !== quote.source_sha256 || source.content_kind !== quote.content_kind) return invalid() }
  if (proposed.outcome === 'answer' && basis.prior_finding.outcome === 'answer') {
    if (proposed.question !== basis.prior_finding.question) return invalid()
    const rank = { known: 0, postulated: 1, speculative: 2 }, previous = new Map(basis.prior_finding.claims.map((claim) => [claim.id, claim.label]))
    if (proposed.claims.some((claim) => previous.has(claim.id) && rank[claim.label] < rank[previous.get(claim.id)!])) return invalid()
  }
  return { status: proposed.outcome, mode, finding: proposed, field_selections: selections, memory_reads: memory, quotes, sources: [], events: activities(item.events, 2000), usage: usage(item.usage), error_code: code(item.error_code), validation_scope: 'source_identity_and_quote_integrity' }
}

export function parseAssessment(value: unknown): AssessmentRecord {
  const item = object(value), payload = object(item.payload), basis = parseAssessmentBasis(payload.basis), raw = object(payload.result)
  const observed = list(payload.observed_sources, observedSource, 0, 200); distinct(observed.map((source) => source.retrieval_id))
  const observedById = new Map(observed.map((source) => [source.retrieval_id, source]))
  for (const source of basis.sources) { const actual = observedById.get(source.retrieval_id); if (actual && sourceIdentity(actual) !== sourceIdentity(source)) return invalid() }
  const declared = distinct(list(raw.observed_retrieval_ids, id, 0, 200)), selected = distinct(list(raw.selected_retrieval_ids, id, 0, 200))
  if (declared.some((identifier) => !observedById.has(identifier)) || selected.some((identifier) => !declared.includes(identifier))) return invalid()
  const rationale = list(raw.rationale, (value) => { const item = object(value); return { text: text(item.text, 2000), citations: list(item.citations, citation, 0, 8) } }, 0, 8)
  const quotes = list(raw.quotes, assessmentQuote, 0, 64), verified = list(payload.verified_passages, assessmentQuote, 0, 64), citations = rationale.flatMap((statement) => statement.citations)
  if (!equal(quotes, verified) || citations.length !== quotes.length) return invalid()
  quotes.forEach((quote, index) => { const source = observedById.get(quote.retrieval_id), expected = citations[index]; if (!declared.includes(quote.retrieval_id) || !source || source.source_id !== quote.source_id || source.content_sha256 !== quote.source_sha256 || source.content_kind !== quote.content_kind || quote.retrieval_id !== expected.retrieval_id || quote.quote !== expected.quote || quote.supports !== expected.supports) return invalid() })
  const status = member(raw.status, ['completed', 'failed', 'limit_reached']), disposition = member(raw.disposition, DISPOSITIONS)
  if (status !== 'completed' && disposition !== 'inconclusive' || status === 'completed' && !rationale.length || raw.validation_scope !== 'version_bound_quote_integrity') return invalid()
  if (disposition === 'revised' ? raw.revised_result === null || !selected.length : raw.revised_result !== null || !!selected.length) return invalid()
  if (status === 'completed' && disposition !== 'inconclusive') {
    const cited = new Set(quotes.map((quote) => quote.retrieval_id))
    if (basis.sources.some((source) => source.role === 'new' && (!declared.includes(source.retrieval_id) || !cited.has(source.retrieval_id)))) return invalid()
    if (!quotes.some((quote) => basis.sources.some((source) => source.role === 'historical' && source.retrieval_id === quote.retrieval_id) && basis.prior_quotes.some((old) => premiseIdentity(old) === premiseIdentity(quote) && old.quote === quote.quote))) return invalid()
  }
  const result: AssessmentResult = { status, disposition, rationale, limitations: list(raw.limitations, (value) => text(value, 1000), 1, 8), observed_retrieval_ids: declared, selected_retrieval_ids: selected, quotes, revised_result: raw.revised_result === null ? null : positiveResult(raw.revised_result, basis, selected.map((identifier) => observedById.get(identifier)!)), search_purposes: distinct(list(raw.search_purposes, (value) => member(value, ['discovery', 'counterevidence', 'prior_work']), 0, 3)), events: activities(raw.events), usage: usage(raw.usage), error_code: code(raw.error_code), reason: raw.reason === null ? null : text(raw.reason, 3000), validation_scope: 'version_bound_quote_integrity' }
  const source = id(item.source_result_id), sourceRun = id(item.source_run_id), sourceHash = hash(item.source_sha256), expected = integer(item.expected_head_revision), revision = integer(item.head_revision), applied = bool(item.applied), candidate = nullableId(item.candidate_result_id), added = versions(item.new_source_version_ids)
  if (item.disposition !== disposition || basis.prior_reference.result_id !== source || basis.prior_reference.run_id !== sourceRun || basis.prior_reference.content_sha256 !== sourceHash || !equal(added, basis.sources.filter((source) => source.role === 'new').map((source) => source.version_id))) return invalid()
  if (revision < expected || applied && (status !== 'completed' || disposition === 'inconclusive' || revision !== expected + 1) || (disposition === 'revised') !== (candidate !== null)) return invalid()
  return { id: id(item.id), run_id: id(item.run_id), root_result_id: id(item.root_result_id), source_result_id: source, source_run_id: sourceRun, source_sha256: sourceHash, watch_id: id(item.watch_id), new_source_version_ids: added, expected_head_revision: expected, disposition, applied, head_revision: revision, candidate_result_id: candidate, payload: { basis, result, verified_passages: verified, observed_sources: observed }, payload_sha256: hash(item.payload_sha256), basis_sha256: hash(item.basis_sha256), created_at: date(item.created_at) }
}

export function parseCapabilities(value: unknown): AssessmentCapabilities {
  const item = object(value), available = bool(item.execution_available)
  if (item.automatic_reassessment !== false || (available ? item.reason !== null : item.reason !== 'research_unavailable')) return invalid()
  return { execution_available: available, reason: available ? null : 'research_unavailable', automatic_reassessment: false }
}

export function parseFindingHead(value: unknown): FindingHead {
  const item = object(value), requested = id(item.requested_result_id), root = id(item.root_result_id), current = id(item.current_result_id)
  const state = member(item.state, ['supported', 'unsupported']), revision = integer(item.revision), assessment = nullableId(item.last_assessment_id)
  const isCurrent = bool(item.is_current), supported = bool(item.is_supported), historical = bool(item.is_historical), canGenerate = bool(item.can_generate)
  if (isCurrent !== (requested === current) || supported !== (isCurrent && state === 'supported') || historical !== (!isCurrent || state === 'unsupported') || canGenerate !== (isCurrent && supported)) return invalid()
  if (revision === 0 ? assessment !== null || current !== root || state !== 'supported' : assessment === null) return invalid()
  return { requested_result_id: requested, root_result_id: root, current_result_id: current, current_run_id: id(item.current_run_id), state, revision, last_assessment_id: assessment, is_current: isCurrent, is_supported: supported, is_historical: historical, can_generate: canGenerate }
}

export function parseAssessmentJob(value: unknown): AssessmentJob {
  const item = object(value)
  if (item.task_kind !== 'reassess_finding' || item.result_id !== null || item.artifact_id !== null) return invalid()
  const status = member(item.status, JOB_STATUSES), assessment = nullableId(item.assessment_id)
  if (assessment !== null && status !== 'succeeded' && status !== 'failed') return invalid()
  return { id: id(item.id), root_result_id: id(item.root_result_id), source_result_id: id(item.source_result_id), source_sha256: hash(item.source_sha256), watch_id: id(item.watch_id), new_source_version_ids: versions(item.new_source_version_ids), expected_head_revision: integer(item.expected_head_revision),
    mode: member(item.mode, ['explore', 'ask', 'surprise']), task_kind: 'reassess_finding', goal: text(item.goal, 4000, false), status, created_at: date(item.created_at), updated_at: date(item.updated_at), result_id: null, artifact_id: null, assessment_id: assessment, error_code: code(item.error_code), external_outcome_unknown: bool(item.external_outcome_unknown) }
}

async function json(response: Response): Promise<unknown> {
  const size = response.headers.get('content-length')
  if (size !== null && (!/^\d+$/.test(size) || Number(size) > MAX_BYTES)) return invalid()
  const reader = response.body?.getReader()
  if (!reader) return invalid()
  const chunks: Uint8Array[] = []; let length = 0
  try {
    while (true) { const { done, value } = await reader.read(); if (done) break; length += value.byteLength; if (length > MAX_BYTES) { await reader.cancel(); return invalid() }; chunks.push(value) }
    const bytes = new Uint8Array(length); let offset = 0
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length }
    return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)) as unknown
  } catch { return invalid() } finally { reader.releaseLock() }
}

async function request<T>(path: string, parse: (value: unknown) => T, signal?: AbortSignal, write?: { csrf: string; body?: unknown }): Promise<T> {
  if (write && !HASH.test(write.csrf)) throw new AssessmentError('session_expired')
  if (signal?.aborted) throw new AssessmentError('aborted')
  const controller = new AbortController(), abort = () => controller.abort()
  signal?.addEventListener('abort', abort, { once: true })
  const timer = window.setTimeout(abort, 10000)
  let sent = false
  try {
    sent = true
    const response = await fetch(path, { method: write ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: controller.signal,
      headers: { Accept: 'application/json', ...(write ? { 'X-CSRF-Token': write.csrf, ...(write.body === undefined ? {} : { 'Content-Type': 'application/json' }) } : {}) }, ...(write?.body === undefined ? {} : { body: JSON.stringify(write.body) }) })
    if (response.status === 401 || response.status === 403) throw new AssessmentError('session_expired')
    if (!response.ok) {
      let detail: string | undefined
      try { const problem = object(await json(response)); if (typeof problem.detail === 'string' && CODES.has(problem.detail)) detail = problem.detail } catch { /* Untrusted error text is never shown. */ }
      const fallback = response.status === 404 ? 'not_found' : response.status === 409 ? 'assessment_changed' : response.status === 422 ? 'invalid_request' : response.status === 429 ? 'queue_limit' : 'unavailable'
      throw new AssessmentError(detail ?? fallback, !!write && response.status >= 500 && detail !== 'research_unavailable')
    }
    return parse(await json(response))
  } catch (error) {
    if (error instanceof AssessmentError) {
      if (write && sent && error.code === 'invalid_response') throw new AssessmentError(error.code, true)
      throw error
    }
    throw new AssessmentError(signal?.aborted ? 'aborted' : 'connection_failed', !!write && sent)
  } finally { signal?.removeEventListener('abort', abort); window.clearTimeout(timer) }
}

export const loadAssessmentCapabilities = (signal?: AbortSignal) => request(`${BASE}/capabilities`, parseCapabilities, signal)
export const getAssessmentHead = (resultId: string, signal?: AbortSignal) => request(`${BASE}/heads/${inputId(resultId)}`, (value) => { const head = parseFindingHead(value); return head.requested_result_id === resultId ? head : invalid() }, signal)
export const getAssessmentJob = (runId: string, signal?: AbortSignal) => request(`${BASE}/jobs/${inputId(runId)}`, (value) => { const job = parseAssessmentJob(value); return job.id === runId ? job : invalid() }, signal)

export function listAssessmentJobs(sourceResultId: string, signal?: AbortSignal, before?: string): Promise<AssessmentJobHistory> {
  const query = new URLSearchParams({ source_result_id: inputId(sourceResultId), limit: '20' })
  if (before !== undefined) query.set('before', inputId(before))
  return request(`${BASE}/jobs?${query}`, (value) => { const item = object(value), jobs = list(item.jobs, parseAssessmentJob), next = nullableId(item.next_cursor); distinct(jobs.map((job) => job.id)); if (jobs.some((job) => job.source_result_id !== sourceResultId || job.id === before) || next !== null && (next !== jobs.at(-1)?.id || next === before)) return invalid(); return { jobs, next_cursor: next } }, signal)
}

export async function createAssessmentJob(body: CreateAssessmentJob, csrf: string, signal?: AbortSignal): Promise<AssessmentJob> {
  let pinned: CreateAssessmentJob
  try { const item = object(body); pinned = { source_result_id: id(item.source_result_id), watch_id: id(item.watch_id), new_source_version_ids: versions(item.new_source_version_ids), expected_head_revision: integer(item.expected_head_revision) } } catch { throw new AssessmentError('invalid_request') }
  return request(`${BASE}/jobs`, (value) => { const job = parseAssessmentJob(value); return job.source_result_id === pinned.source_result_id && job.watch_id === pinned.watch_id && job.expected_head_revision === pinned.expected_head_revision && JSON.stringify(job.new_source_version_ids) === JSON.stringify(pinned.new_source_version_ids) ? job : invalid() }, signal, { csrf, body: pinned })
}

export function cancelAssessmentJob(runId: string, csrf: string, signal?: AbortSignal): Promise<AssessmentCancellation> {
  return request(`/api/v1/runs/${inputId(runId)}/cancel`, (value) => {
    const item = object(value)
    if (item.id !== runId || item.task_kind !== 'reassess_finding' || item.result_id !== null || item.artifact_id !== null) return invalid()
    return { id: runId, task_kind: 'reassess_finding', status: member(item.status, JOB_STATUSES), assessment_id: item.assessment_id === undefined ? null : nullableId(item.assessment_id) }
  }, signal, { csrf })
}

export function getAssessment(assessmentId: string, signal?: AbortSignal): Promise<AssessmentRecord> {
  return request(`${BASE}/${inputId(assessmentId)}`, (value) => { const record = parseAssessment(value); return record.id === assessmentId ? record : invalid() }, signal)
}

export function assessmentHistory(rootResultId: string, signal?: AbortSignal, before?: string): Promise<AssessmentHistory> {
  const query = new URLSearchParams({ limit: '5' })
  if (before !== undefined) query.set('before', inputId(before))
  return request(`${BASE}/heads/${inputId(rootResultId)}/history?${query}`, (value) => {
    const item = object(value), assessments = list(item.assessments, parseAssessment, 0, 5)
    distinct(assessments.map((assessment) => assessment.id))
    distinct(assessments.map((assessment) => assessment.run_id))
    const next = nullableId(item.next_cursor)
    if (assessments.some((assessment) => assessment.root_result_id !== rootResultId || assessment.id === before) || next !== null && (next !== assessments.at(-1)?.id || next === before)) return invalid()
    return { assessments, next_cursor: next }
  }, signal)
}

export function assessmentMatchesJob(record: AssessmentRecord, job: AssessmentJob): boolean {
  return record.id === job.assessment_id && record.run_id === job.id && record.root_result_id === job.root_result_id && record.source_result_id === job.source_result_id && record.source_sha256 === job.source_sha256 && record.watch_id === job.watch_id && record.expected_head_revision === job.expected_head_revision && equal(record.new_source_version_ids, job.new_source_version_ids)
}

export function parseFindingVersion(value: unknown): FindingVersion {
  const item = object(value), run = object(item.run), resultId = id(item.id), runId = id(item.run_id), kind = member(item.task_kind, ['research', 'reassess_finding']), head = parseFindingHead(item.assessment)
  if (run.id !== runId || run.task_kind !== kind || run.status !== 'succeeded' || run.artifact_id !== null || head.requested_result_id !== resultId || head.is_current && head.current_run_id !== runId || (kind === 'research' ? run.result_id !== resultId || head.root_result_id !== resultId : run.result_id !== null)) return invalid()
  let parsed: ResearchResult
  try { parsed = parseResult(item.payload) } catch { return invalid() }
  if (parsed.status !== 'bridge' && parsed.status !== 'answer' || !parsed.finding || parsed.finding.outcome === 'insufficient_evidence') return invalid()
  const proposed = finding(parsed.finding), selections = fields(parsed.field_selections), quotes = list(parsed.quotes, researchQuote, 1, 64), mode = member(run.mode, ['explore', 'ask', 'surprise'])
  if (parsed.mode !== mode) return invalid()
  modeFinding(mode, proposed, selections); findingQuotes(proposed, quotes)
  const sources = list(parsed.sources, (raw) => { const source = object(raw); return { source_id: text(source.source_id, 200), title: text(source.title, 1000), url: safeUrl(source.url), text: text(source.text, 20000), content_kind: member(source.content_kind, ['abstract', 'full_text']), retrieved_at: date(source.retrieved_at), extraction_method: text(source.extraction_method, 100), ...(source.bibliography === undefined ? {} : { bibliography: parseBibliography(source.bibliography, invalid) }) } }, 1, 200)
  distinct(sources.map((source) => source.source_id))
  for (const quote of quotes) { const source = sources.find((source) => source.source_id === quote.source_id); if (!source || source.content_kind !== quote.content_kind || Array.from(source.text).slice(quote.start, quote.end).join('') !== quote.quote) return invalid() }
  return { id: resultId, run_id: runId, task_kind: kind, run: { id: runId, task_kind: kind, mode, goal: text(run.goal, 4000, false), status: 'succeeded', created_at: date(run.created_at), updated_at: date(run.updated_at), result_id: nullableId(run.result_id), artifact_id: null, assessment_id: run.assessment_id === undefined ? null : nullableId(run.assessment_id), error_code: code(run.error_code), external_outcome_unknown: bool(run.external_outcome_unknown) }, payload: { ...parsed, finding: proposed, sources, quotes, field_selections: selections }, assessment: head }
}

export function getFindingVersion(resultId: string, signal?: AbortSignal): Promise<FindingVersion> {
  return request(`${BASE}/findings/${inputId(resultId)}`, (value) => { const version = parseFindingVersion(value); return version.id === resultId ? version : invalid() }, signal)
}
