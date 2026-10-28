import { parseOverviewIntent, parseResultOverviewMetadata } from '../scientificContext/overviewBinding'
import type { AskOverviewIntent } from '../scientificContext/types'
import { parseDiscoveryIntent } from './discoveryIntent'
import type { DiscoveryIntent } from '../discovery/types'
import { parseBibliography } from './bibliography'
import { parseSourceCoverage, validateSourceCoverageContext } from './sourceCoverage'
import { scientificAdmissionError, nativeSubmissionHeaders, nativeAdmissionFailure, parseNativeRunControl, validNativeSubmission } from './nativeAdmission'
import type { NativeSubmissionContext } from './nativeAdmission'
import type {
  AnswerClaim,
  AnswerFinding,
  BridgeFinding,
  ClaimQuote,
  EvidenceQuote,
  FieldSelection,
  MemoryReadReference,
  ResearchActivityEvent,
  ResearchResult,
  ResearchRunSummary,
  SourceDocument,
  VerifiedQuote,
} from './types'

export type ResearchApiErrorCode = 'session_expired' | 'session_changed' | 'run_unavailable' |
  'unavailable' | 'queue_limit' | 'invalid_request' | 'invalid_response' | 'connection_failed' | 'aborted' | 'native_authority_changed' | 'resource_limited' | 'scientific_variant_unavailable' | 'scientific_context_unavailable' | 'scientific_context_too_large'

export class ResearchApiError extends Error {
  constructor(public readonly code: ResearchApiErrorCode, public readonly retryAfterSeconds: number | null = null) {
    super(code)
    this.name = 'ResearchApiError'
  }
}

export interface OwnedRun extends ResearchRunSummary {
  created_at: string
  updated_at: string
  result_id: string | null
  error_code: string | null
  external_outcome_unknown: boolean
}

export interface ResearchSession {
  csrf_token: string
  expires_at: string
}

export interface RunHistory {
  runs: OwnedRun[]
  next_cursor: string | null
}

const UUID = /^[a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12}$/i
const MAX_RESPONSE_BYTES = 4 * 1024 * 1024

function invalid(): never { throw new ResearchApiError('invalid_response') }
function object(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return invalid()
  return value as Record<string, unknown>
}
function text(value: unknown, maximum = 8000): string {
  if (typeof value !== 'string' || value.length > maximum) return invalid()
  return value
}
function requiredText(value: unknown, maximum = 8000): string {
  const parsed = text(value, maximum)
  return parsed.trim() ? parsed : invalid()
}
function integer(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) return invalid()
  return value
}
function boolean(value: unknown): boolean {
  return typeof value === 'boolean' ? value : invalid()
}
function id(value: unknown): string {
  const parsed = text(value, 36)
  return UUID.test(parsed) ? parsed : invalid()
}
function nullableId(value: unknown): string | null { return value === null ? null : id(value) }
function date(value: unknown): string {
  const parsed = text(value, 100)
  return Number.isFinite(Date.parse(parsed)) ? parsed : invalid()
}
function list<T>(value: unknown, parse: (item: unknown) => T, maximum = 200): T[] {
  if (!Array.isArray(value) || value.length > maximum) return invalid()
  return value.map(parse)
}
function kind(value: unknown): SourceDocument['content_kind'] {
  return value === 'abstract' || value === 'full_text' ? value : invalid()
}

export function parseRun(value: unknown): OwnedRun {
  const run = object(value)
  const native = parseNativeRunControl(run.native_research, invalid)
  const discovery = parseDiscoveryIntent(run.discovery_intent, invalid)
  let overview: AskOverviewIntent | undefined; try { overview = parseOverviewIntent(run.ask_overview_intent, 'polymath-ask-claim-overview-v1') } catch { return invalid() }
  if (overview && (run.mode !== 'ask' || run.task_kind !== 'research')) return invalid()
  if (discovery && (run.mode !== 'surprise' || run.task_kind !== 'research')) return invalid()
  if (!['explore', 'ask', 'surprise'].includes(text(run.mode, 20))) return invalid()
  if (!/^[a-z_]{1,40}$/.test(text(run.status, 40))) return invalid()
  return {
    id: id(run.id), mode: run.mode as OwnedRun['mode'], goal: text(run.goal), status: run.status as string,
    created_at: date(run.created_at), updated_at: date(run.updated_at), result_id: nullableId(run.result_id),
    error_code: run.error_code === null ? null : text(run.error_code, 100),
    external_outcome_unknown: boolean(run.external_outcome_unknown),
    ...(run.task_kind === undefined ? {} : { task_kind: requiredText(run.task_kind, 80) }),
    ...(native ? { native_research: native } : {}),
    ...(discovery ? { discovery_intent: discovery } : {}),
    ...(overview ? { ask_overview_intent: overview } : {}),
  }
}

export function parseActivity(value: unknown): ResearchActivityEvent {
  const event = object(value)
  const sequence = integer(event.sequence)
  if (sequence < 1) return invalid()
  return { sequence, kind: text(event.kind, 128), summary: text(event.summary, 4000) }
}

function source(value: unknown): SourceDocument {
  const record = object(value)
  const bibliography = parseBibliography(record.bibliography, invalid), coverage = parseSourceCoverage(record.coverage, invalid)
  const parsed: SourceDocument = {
    source_id: text(record.source_id, 400), title: text(record.title, 2000), url: text(record.url, 4000),
    text: text(record.text, 40000), content_kind: kind(record.content_kind),
    retrieved_at: date(record.retrieved_at), extraction_method: text(record.extraction_method, 200),
    ...(bibliography === undefined ? {} : { bibliography }), ...(coverage === undefined ? {} : { coverage }),
  }
  validateSourceCoverageContext(parsed, invalid)
  return parsed
}
function evidence(value: unknown): EvidenceQuote {
  const record = object(value)
  if (record.side !== 'left' && record.side !== 'right') return invalid()
  return { source_id: text(record.source_id, 400), side: record.side, quote: text(record.quote), supports: text(record.supports, 3000) }
}
function quote(value: unknown): VerifiedQuote {
  const record = object(value)
  if (record.side !== 'left' && record.side !== 'right' && record.side !== 'answer') return invalid()
  const claimId = record.claim_id === undefined || record.claim_id === null ? null : claimIdentifier(record.claim_id)
  if (record.side === 'answer' ? !claimId : claimId !== null) return invalid()
  const start = integer(record.start), end = integer(record.end)
  if (end <= start || !/^[a-f\d]{64}$/i.test(text(record.source_sha256, 64))) return invalid()
  return {
    ...claimEvidence(record), side: record.side, claim_id: claimId, start, end,
    source_sha256: text(record.source_sha256, 64), content_kind: kind(record.content_kind),
  }
}
function claimIdentifier(value: unknown): string {
  const parsed = text(value, 64)
  return /^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(parsed) ? parsed : invalid()
}
function claimEvidence(value: unknown): ClaimQuote {
  const record = object(value)
  return { source_id: requiredText(record.source_id, 400), quote: requiredText(record.quote, 4000), supports: requiredText(record.supports, 3000) }
}
function answerClaim(value: unknown): AnswerClaim {
  const record = object(value)
  if (!['known', 'postulated', 'speculative'].includes(text(record.label, 20))) return invalid()
  const citations = list(record.evidence, claimEvidence, 8)
  if (!citations.length) return invalid()
  const derivation = record.derivation === null || record.derivation === undefined ? null : requiredText(record.derivation, 6000)
  const caveats = record.caveats === undefined ? [] : list(record.caveats, (item) => requiredText(item, 3000), 8)
  if (record.label === 'postulated' && !derivation || record.label === 'speculative' && !caveats.length) return invalid()
  return { id: claimIdentifier(record.id), text: requiredText(record.text, 6000), label: record.label as AnswerClaim['label'], evidence: citations, derivation, caveats }
}
function normalizeField(value: string): string {
  return value.trim().replace(/\s+/gu, ' ').toLowerCase().replaceAll('ß', 'ss').replaceAll('ς', 'σ')
}
function fieldSelection(value: unknown): FieldSelection {
  const record = object(value)
  const fields = list(record.fields, (item) => requiredText(item, 200), 4)
  if (fields.length < 2 || new Set(fields.map(normalizeField)).size !== fields.length) return invalid()
  return { fields, rationale: requiredText(record.rationale, 1000) }
}
function memoryRead(value: unknown): MemoryReadReference {
  const record = object(value)
  const hash = text(record.content_sha256, 64)
  if (!/^[a-f\d]{64}$/.test(hash)) return invalid()
  return { result_id: id(record.result_id), run_id: id(record.run_id), content_sha256: hash }
}
function finding(value: unknown): ResearchResult['finding'] {
  if (value === null) return null
  const record = object(value)
  if (record.outcome === 'insufficient_evidence') {
    return { outcome: 'insufficient_evidence', reason: text(record.reason, 6000) }
  }
  if (record.outcome === 'answer') {
    const claims = list(record.claims, answerClaim, 8)
    const limitations = list(record.limitations, (item) => requiredText(item, 3000), 8)
    if (!claims.length || !limitations.length || new Set(claims.map((item) => item.id)).size !== claims.length) return invalid()
    return {
      outcome: 'answer', question: requiredText(record.question, 4000), summary: requiredText(record.summary, 6000),
      claims, limitations, unanswered_questions: record.unanswered_questions === undefined ? [] : list(record.unanswered_questions, (item) => requiredText(item, 3000), 8),
    } satisfies AnswerFinding
  }
  if (record.outcome !== 'bridge') return invalid()
  return {
    outcome: 'bridge', title: text(record.title, 600), field_left: text(record.field_left, 400),
    field_right: text(record.field_right, 400), explanation: text(record.explanation, 6000),
    mappings: list(record.mappings, (value) => {
      const mapping = object(value)
      return { left: text(mapping.left, 2000), right: text(mapping.right, 2000), assumption: text(mapping.assumption, 3000) }
    }, 8),
    open_question: text(record.open_question, 3000), strongest_objection: text(record.strongest_objection, 4000),
    objection_verdict: text(record.objection_verdict, 4000), novelty_limitations: text(record.novelty_limitations, 4000),
    evidence: list(record.evidence, evidence, 12),
  } satisfies BridgeFinding
}

export function parseResult(value: unknown, expectedOverview?: boolean): ResearchResult {
  const result = object(value)
  let overview: ResearchResult['overview_binding']; try { overview = parseResultOverviewMetadata(value, expectedOverview) } catch { return invalid() }
  if (!['bridge', 'answer', 'insufficient_evidence', 'limit_reached', 'failed'].includes(text(result.status, 40))) return invalid()
  if (result.validation_scope !== 'source_identity_and_quote_integrity') return invalid()
  const mode = result.mode === undefined ? 'explore' : text(result.mode, 20)
  if (mode !== 'explore' && mode !== 'ask' && mode !== 'surprise') return invalid()
  const usage = object(result.usage)
  const parsed: ResearchResult = {
    status: result.status as ResearchResult['status'], mode, finding: finding(result.finding),
    ...(overview ? { overview_binding: overview } : {}),
    field_selections: result.field_selections === undefined ? [] : list(result.field_selections, fieldSelection, 3),
    memory_reads: result.memory_reads === undefined ? [] : list(result.memory_reads, memoryRead, 10),
    sources: list(result.sources, source), quotes: list(result.quotes, quote, 64), events: list(result.events, parseActivity, 2000),
    usage: { model_calls: integer(usage.model_calls), input_tokens_reported: integer(usage.input_tokens_reported), output_tokens_reported: integer(usage.output_tokens_reported), calls_without_usage: integer(usage.calls_without_usage) },
    error_code: result.error_code === null ? null : text(result.error_code, 100), validation_scope: result.validation_scope,
  }
  const covered = new Map(parsed.sources.filter((source) => source.coverage).map((source) => [source.source_id, source]))
  for (const quote of parsed.quotes) {
    const source = covered.get(quote.source_id)
    if (source) { const characters = Array.from(source.text); if (quote.content_kind !== source.content_kind || quote.end > characters.length || quote.end - quote.start !== Array.from(quote.quote).length || characters.slice(quote.start, quote.end).join('') !== quote.quote) return invalid() }
  }
  if (new Set(parsed.memory_reads?.map((item) => item.result_id)).size !== parsed.memory_reads?.length) return invalid()
  if (mode !== 'surprise' && parsed.field_selections?.length) return invalid()
  if (parsed.status === 'answer') {
    if (mode !== 'ask' || parsed.finding?.outcome !== 'answer') return invalid()
    validateAnswerEvidence(parsed, parsed.finding)
  } else if (parsed.finding?.outcome === 'answer') return invalid()
  if (parsed.status === 'bridge') {
    if (mode === 'ask' || parsed.finding?.outcome !== 'bridge') return invalid()
    if (mode === 'surprise') {
      const selected = parsed.field_selections?.at(-1)?.fields.map(normalizeField) ?? []
      if (!selected.includes(normalizeField(parsed.finding.field_left)) || !selected.includes(normalizeField(parsed.finding.field_right))) return invalid()
    }
  }
  return parsed
}

function validateAnswerEvidence(result: ResearchResult, answer: AnswerFinding): void {
  const sources = new Map(result.sources.map((item) => [item.source_id, item]))
  if (sources.size !== result.sources.length) return invalid()
  const remaining = [...result.quotes]
  for (const claim of answer.claims) {
    const sourceUrls = new Set<string>()
    for (const citation of claim.evidence) {
      const source = sources.get(citation.source_id)
      const index = remaining.findIndex((item) => item.side === 'answer' && item.claim_id === claim.id && item.source_id === citation.source_id && item.quote === citation.quote && item.supports === citation.supports)
      if (!source || index < 0) return invalid()
      const [checked] = remaining.splice(index, 1)
      if (source.content_kind !== checked.content_kind) return invalid()
      const characters = Array.from(source.text)
      if (checked.end > characters.length || checked.end - checked.start !== Array.from(checked.quote).length || characters.slice(checked.start, checked.end).join('') !== citation.quote) return invalid()
      sourceUrls.add(source.url)
    }
    if (claim.label === 'postulated' && sourceUrls.size < 2) return invalid()
  }
  if (remaining.length) return invalid()
}

async function json(response: Response): Promise<unknown> {
  const reader = response.body?.getReader()
  if (!reader) return invalid()
  const parts: Uint8Array[] = []
  let length = 0
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      length += value.byteLength
      if (length > MAX_RESPONSE_BYTES) { await reader.cancel(); return invalid() }
      parts.push(value)
    }
    const bytes = new Uint8Array(length)
    let offset = 0
    for (const part of parts) { bytes.set(part, offset); offset += part.length }
    return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)) as unknown
  } catch { return invalid() }
}

async function request(path: string, signal?: AbortSignal, write?: { csrf: string; body?: unknown; native?: NativeSubmissionContext }, missingResult = false): Promise<unknown> {
  const controller = new AbortController()
  const abort = () => controller.abort()
  signal?.addEventListener('abort', abort, { once: true })
  if (signal?.aborted) controller.abort()
  const timeout = window.setTimeout(abort, 10000)
  try {
    if (write && !/^[a-f\d]{64}$/.test(write.csrf)) throw new ResearchApiError('session_changed')
    if (write?.native && !validNativeSubmission(write.native)) throw new ResearchApiError('native_authority_changed')
    const response = await fetch(path, {
      signal: controller.signal, credentials: 'same-origin', cache: 'no-store', redirect: 'error',
      method: write ? 'POST' : 'GET',
      headers: { Accept: 'application/json', ...(write ? { 'X-CSRF-Token': write.csrf, 'Content-Type': 'application/json', ...(write.native ? nativeSubmissionHeaders(write.native) : {}) } : {}) },
      ...(write?.body === undefined ? {} : { body: JSON.stringify(write.body) }),
    })
    if (!response.ok && [401, 403, 409, 422, 503].includes(response.status)) {
      let detail: unknown
      try { detail = object(await json(response.clone())).detail } catch { /* Keep untrusted errors out of the UI. */ }
      const scientific = scientificAdmissionError(response.status, detail); if (scientific) throw new ResearchApiError(scientific)
      if (nativeAdmissionFailure(response.status, detail)) throw new ResearchApiError('native_authority_changed')
    }
    if (response.status === 401) throw new ResearchApiError('session_expired')
    if (response.status === 403) throw new ResearchApiError('session_changed')
    if (response.status === 429) { const retry = response.headers.get('retry-after'), seconds = retry && /^\d{1,5}$/.test(retry) && Number(retry) <= 86400 ? Number(retry) : null; throw new ResearchApiError(seconds === null ? 'queue_limit' : 'resource_limited', seconds) }
    if (response.status === 422) throw new ResearchApiError('invalid_request')
    if (response.status === 404) {
      if (missingResult) {
        const payload = object(await json(response))
        if (payload.detail === 'research_result_not_available') return null
      }
      throw new ResearchApiError('run_unavailable')
    }
    if (!response.ok) throw new ResearchApiError('unavailable')
    return await json(response)
  } catch (error) {
    if (error instanceof ResearchApiError) throw error
    throw new ResearchApiError(signal?.aborted ? 'aborted' : 'connection_failed')
  } finally {
    signal?.removeEventListener('abort', abort)
    window.clearTimeout(timeout)
  }
}

export async function openResearchSession(signal?: AbortSignal): Promise<ResearchSession> {
  const payload = object(await request('/api/v1/session', signal))
  const csrf = text(payload.csrf_token, 64)
  if (!/^[a-f\d]{64}$/.test(csrf)) return invalid()
  return { csrf_token: csrf, expires_at: date(payload.expires_at) }
}

export async function listResearchRuns(signal?: AbortSignal, before?: string): Promise<RunHistory> {
  if (before) id(before)
  const path = '/api/v1/runs' + (before ? `?before=${encodeURIComponent(before)}` : '')
  const payload = object(await request(path, signal))
  return { runs: list(payload.runs, parseRun, 50), next_cursor: nullableId(payload.next_cursor) }
}

export async function getResearchRun(runId: string, signal?: AbortSignal): Promise<OwnedRun> {
  const run = parseRun(await request(`/api/v1/runs/${id(runId)}`, signal))
  return run.id === runId ? run : invalid()
}

export async function getResearchResult(runId: string, signal?: AbortSignal, expectedOverview?: boolean): Promise<ResearchResult | null> {
  const payload = await request(`/api/v1/runs/${id(runId)}/result`, signal, undefined, true)
  return payload === null ? null : parseResult(payload, expectedOverview)
}

export async function cancelResearchRun(runId: string, csrf: string, signal?: AbortSignal): Promise<OwnedRun> {
  const run = parseRun(await request(`/api/v1/runs/${id(runId)}/cancel`, signal, { csrf }))
  return run.id === runId ? run : invalid()
}

export async function createResearchRun(mode: OwnedRun['mode'], goal: string, csrf: string, signal?: AbortSignal, parentResultIds: readonly string[] = [], native?: NativeSubmissionContext, discovery?: DiscoveryIntent, overview?: AskOverviewIntent): Promise<OwnedRun> {
  if (!['explore', 'ask', 'surprise'].includes(mode) || typeof goal !== 'string' || goal.length > 4000 || (!goal.trim() && mode !== 'surprise')) throw new ResearchApiError('invalid_request')
  if (parentResultIds.length && (mode !== 'explore' || parentResultIds.length < 2 || parentResultIds.length > 4 || parentResultIds.some((value) => typeof value !== 'string' || !UUID.test(value)) || new Set(parentResultIds.map((value) => value.toLowerCase())).size !== parentResultIds.length)) throw new ResearchApiError('invalid_request')
  const intent = parseDiscoveryIntent(discovery, () => { throw new ResearchApiError('invalid_request') })
  if (intent && (mode !== 'surprise' || parentResultIds.length)) throw new ResearchApiError('invalid_request')
  let overviewIntent: AskOverviewIntent | undefined; try { overviewIntent = parseOverviewIntent(overview, 'polymath-ask-claim-overview-v1') } catch { throw new ResearchApiError('invalid_request') }
  if (overviewIntent && mode !== 'ask') throw new ResearchApiError('invalid_request')
  const created = parseRun(await request('/api/v1/runs', signal, { csrf, native, body: { mode, goal, ...(parentResultIds.length ? { parent_result_ids: parentResultIds } : {}), ...(native ? { expected_activation_sha256: native.expectedActivationSha256 } : {}), ...(intent ? { discovery_intent: intent } : {}), ...(overviewIntent ? { ask_overview_intent: overviewIntent } : {}) } }))
  if (intent ? !created.discovery_intent || created.discovery_intent.max_stages !== intent.max_stages || created.discovery_intent.max_depth !== intent.max_depth : created.discovery_intent !== undefined) return invalid()
  if (!!created.ask_overview_intent !== !!overviewIntent) return invalid()
  return created
}

export async function resumeNativeResearchRun(runId: string, leaseToken: string, csrf: string, native: NativeSubmissionContext, signal?: AbortSignal): Promise<OwnedRun> {
  if (typeof leaseToken !== 'string' || !/^(?:0|[1-9]\d{0,18})$/.test(leaseToken) || BigInt(leaseToken) > 9223372036854775807n) throw new ResearchApiError('invalid_request')
  const result = parseRun(await request(`/api/v1/runs/${id(runId)}/resume`, signal, { csrf, native, body: { expected_lease_token: leaseToken, expected_activation_sha256: native.expectedActivationSha256 } }))
  return result.id === runId ? result : invalid()
}
