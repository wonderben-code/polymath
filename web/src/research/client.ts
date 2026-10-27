import type {
  BridgeFinding,
  EvidenceQuote,
  ResearchActivityEvent,
  ResearchResult,
  ResearchRunSummary,
  SourceDocument,
  VerifiedQuote,
} from './types'

export type ResearchApiErrorCode = 'session_expired' | 'session_changed' | 'run_unavailable' |
  'unavailable' | 'queue_limit' | 'invalid_request' | 'invalid_response' | 'connection_failed' | 'aborted'

export class ResearchApiError extends Error {
  constructor(public readonly code: ResearchApiErrorCode) {
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
  if (!['explore', 'ask', 'surprise'].includes(text(run.mode, 20))) return invalid()
  if (!/^[a-z_]{1,40}$/.test(text(run.status, 40))) return invalid()
  return {
    id: id(run.id), mode: run.mode as OwnedRun['mode'], goal: text(run.goal), status: run.status as string,
    created_at: date(run.created_at), updated_at: date(run.updated_at), result_id: nullableId(run.result_id),
    error_code: run.error_code === null ? null : text(run.error_code, 100),
    external_outcome_unknown: boolean(run.external_outcome_unknown),
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
  return {
    source_id: text(record.source_id, 400), title: text(record.title, 2000), url: text(record.url, 4000),
    text: text(record.text, 40000), content_kind: kind(record.content_kind),
    retrieved_at: date(record.retrieved_at), extraction_method: text(record.extraction_method, 200),
  }
}
function evidence(value: unknown): EvidenceQuote {
  const record = object(value)
  if (record.side !== 'left' && record.side !== 'right') return invalid()
  return { source_id: text(record.source_id, 400), side: record.side, quote: text(record.quote), supports: text(record.supports, 3000) }
}
function quote(value: unknown): VerifiedQuote {
  const record = object(value)
  return {
    ...evidence(record), start: integer(record.start), end: integer(record.end),
    source_sha256: text(record.source_sha256, 64), content_kind: kind(record.content_kind),
  }
}
function finding(value: unknown): ResearchResult['finding'] {
  if (value === null) return null
  const record = object(value)
  if (record.outcome === 'insufficient_evidence') {
    return { outcome: 'insufficient_evidence', reason: text(record.reason, 6000) }
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

export function parseResult(value: unknown): ResearchResult {
  const result = object(value)
  if (!['bridge', 'insufficient_evidence', 'limit_reached', 'failed'].includes(text(result.status, 40))) return invalid()
  if (result.validation_scope !== 'source_identity_and_quote_integrity') return invalid()
  const usage = object(result.usage)
  return {
    status: result.status as ResearchResult['status'], finding: finding(result.finding),
    sources: list(result.sources, source), quotes: list(result.quotes, quote, 12), events: list(result.events, parseActivity, 2000),
    usage: { model_calls: integer(usage.model_calls), input_tokens_reported: integer(usage.input_tokens_reported), output_tokens_reported: integer(usage.output_tokens_reported), calls_without_usage: integer(usage.calls_without_usage) },
    error_code: result.error_code === null ? null : text(result.error_code, 100), validation_scope: result.validation_scope,
  }
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

async function request(path: string, signal?: AbortSignal, write?: { csrf: string; body?: unknown }, missingResult = false): Promise<unknown> {
  const controller = new AbortController()
  const abort = () => controller.abort()
  signal?.addEventListener('abort', abort, { once: true })
  if (signal?.aborted) controller.abort()
  const timeout = window.setTimeout(abort, 10000)
  try {
    if (write && !/^[a-f\d]{64}$/.test(write.csrf)) throw new ResearchApiError('session_changed')
    const response = await fetch(path, {
      signal: controller.signal, credentials: 'same-origin', cache: 'no-store', redirect: 'error',
      method: write ? 'POST' : 'GET',
      headers: { Accept: 'application/json', ...(write ? { 'X-CSRF-Token': write.csrf, 'Content-Type': 'application/json' } : {}) },
      ...(write?.body === undefined ? {} : { body: JSON.stringify(write.body) }),
    })
    if (response.status === 401) throw new ResearchApiError('session_expired')
    if (response.status === 403) throw new ResearchApiError('session_changed')
    if (response.status === 429) throw new ResearchApiError('queue_limit')
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

export async function getResearchResult(runId: string, signal?: AbortSignal): Promise<ResearchResult | null> {
  const payload = await request(`/api/v1/runs/${id(runId)}/result`, signal, undefined, true)
  return payload === null ? null : parseResult(payload)
}

export async function cancelResearchRun(runId: string, csrf: string, signal?: AbortSignal): Promise<OwnedRun> {
  const run = parseRun(await request(`/api/v1/runs/${id(runId)}/cancel`, signal, { csrf }))
  return run.id === runId ? run : invalid()
}

export async function createResearchRun(mode: OwnedRun['mode'], goal: string, csrf: string, signal?: AbortSignal): Promise<OwnedRun> {
  if (!['explore', 'ask', 'surprise'].includes(mode) || typeof goal !== 'string' || goal.length > 4000 || (!goal.trim() && mode !== 'surprise')) throw new ResearchApiError('invalid_request')
  return parseRun(await request('/api/v1/runs', signal, { csrf, body: { mode, goal } }))
}
