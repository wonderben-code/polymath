import type { SourceDocument } from '../research/types'
import { parseBibliography } from '../research/bibliography'
import { parseSourceCoverage, validateSourceCoverageContext } from '../research/sourceCoverage'

export type Provider = 'openalex' | 'arxiv'
export type TickStatus = 'queued' | 'running' | 'waiting_retry' | 'cancel_requested' | 'cancelled' | 'succeeded' | 'failed' | 'interrupted'
export interface WatchCapabilities {
  execution_available: boolean
  reason: 'ingestion_execution_disabled' | null
  model_calls: false
  min_interval_seconds: number
  max_interval_seconds: number
  watch_limit: number
  source_limit_per_watch: number
  coverage: 'bounded_provider_windows'
}
export interface ProviderCursor { since: string; until: string; cursor: string | number | null; exhausted: boolean; revision: number }
export interface LiteratureWatch {
  id: string; label: string; query: string; enabled: boolean; interval_seconds: number
  next_due_at: string; expires_at: string; created_at: string; updated_at: string
  source_result_id: string | null; active_run_id: string | null; active_status: TickStatus | null
  last_run_at: string | null; source_count: number
  cursors: Partial<Record<Provider, ProviderCursor>>
  coverage: 'current_browser_session'
}
export interface WatchSource {
  id: string; source_id: string; title: string; url: string; content_kind: 'abstract' | 'full_text'
  content_sha256: string; first_seen_at: string; run_id: string
  assessment_status?: 'unassessed' | 'already_cited' | 'assessed'
  assessment_root_result_id?: string | null
  assessment_history_url?: string | null
}
export interface ProviderDiagnostic { status: 'succeeded' | 'failed'; code?: string; sources_seen: number; added_sources: number; exhausted: boolean; since?: string; until?: string }
export interface WatchTick {
  id: string; run_id: string; watch_id: string; status: TickStatus; slot: string; created_at: string; updated_at: string
  added_sources: number; diagnostics: Partial<Record<Provider, ProviderDiagnostic>>; coverage: 'bounded_provider_window'
  ingestion_status?: 'complete' | 'partial' | 'failed' | null
  error_code?: 'source_limit_reached' | 'all_providers_unavailable' | null
}
export interface WatchSourceDetail {
  id: string; watch_id: string; run_id: string; version_id: string; retrieval_id: string
  content_sha256: string; metadata_sha256: string; document: SourceDocument; assessment_status: 'unassessed' | 'already_cited' | 'assessed'
  assessment_root_result_id?: string | null
  assessment_history_url?: string | null
}
export interface Page<T> { items: T[]; next_cursor: string | null }
export interface CreateWatch { label: string; query: string; interval_seconds: number; source_result_id: string | null }
export class IngestionError extends Error {
  code: string
  uncertain: boolean
  constructor(code: string, uncertain = false) { super(code); this.name = 'IngestionError'; this.code = code; this.uncertain = uncertain }
}

const BASE = '/api/v1/knowledge/watches'
const UUID = /^[a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12}$/
const HASH = /^[a-f\d]{64}$/
const STATUSES: readonly TickStatus[] = ['queued', 'running', 'waiting_retry', 'cancel_requested', 'cancelled', 'succeeded', 'failed', 'interrupted']
const PROVIDERS: Provider[] = ['openalex', 'arxiv']
const MAX_BYTES = 2 * 1024 * 1024
function invalid(): never { throw new IngestionError('invalid_response') }
function object(value: unknown): Record<string, unknown> { return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : invalid() }
function text(value: unknown, maximum = 500, required = true): string {
  return typeof value === 'string' && Array.from(value).length <= maximum && (!required || !!value.trim()) && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\uD800-\uDFFF]/u.test(value) ? value : invalid()
}
function integer(value: unknown, minimum = 0, maximum = Number.MAX_SAFE_INTEGER): number { return typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum && value <= maximum ? value : invalid() }
function bool(value: unknown): boolean { return typeof value === 'boolean' ? value : invalid() }
function member<T extends string>(value: unknown, choices: readonly T[]): T { return typeof value === 'string' && choices.includes(value as T) ? value as T : invalid() }
function id(value: unknown): string { return typeof value === 'string' && UUID.test(value) ? value : invalid() }
function nullableId(value: unknown): string | null { return value === null ? null : id(value) }
function hash(value: unknown): string { return typeof value === 'string' && HASH.test(value) ? value : invalid() }
function date(value: unknown): string { const parsed = text(value, 100); return Number.isFinite(Date.parse(parsed)) ? parsed : invalid() }
function safeUrl(value: unknown): string {
  const parsed = text(value, 2000)
  try { const url = new URL(parsed); if (url.protocol !== 'https:' || url.username || url.password) return invalid() } catch { return invalid() }
  return parsed
}
function providers<T>(value: unknown, parse: (item: unknown) => T): Partial<Record<Provider, T>> {
  const item = object(value)
  if (Object.keys(item).some((key) => !PROVIDERS.includes(key as Provider))) return invalid()
  return Object.fromEntries(Object.entries(item).map(([key, raw]) => [key, parse(raw)]))
}
export function parseCapabilities(value: unknown): WatchCapabilities {
  const item = object(value), available = bool(item.execution_available)
  if (item.model_calls !== false || item.coverage !== 'bounded_provider_windows' || item.min_interval_seconds !== 21600 || item.max_interval_seconds !== 604800 || (available ? item.reason !== null : item.reason !== 'ingestion_execution_disabled')) return invalid()
  return { execution_available: available, reason: available ? null : 'ingestion_execution_disabled', model_calls: false, min_interval_seconds: 21600, max_interval_seconds: 604800, watch_limit: integer(item.watch_limit, 1, 10), source_limit_per_watch: integer(item.source_limit_per_watch, 1, 2000), coverage: 'bounded_provider_windows' }
}
export function parseWatch(value: unknown): LiteratureWatch {
  const item = object(value)
  if (item.coverage !== 'current_browser_session') return invalid()
  const runId = nullableId(item.active_run_id), activeStatus = item.active_status === null ? null : member(item.active_status, STATUSES)
  if ((runId === null) !== (activeStatus === null)) return invalid()
  return {
    id: id(item.id), label: text(item.label, 120), query: text(item.query, 500), enabled: bool(item.enabled), interval_seconds: integer(item.interval_seconds, 21600, 604800),
    next_due_at: date(item.next_due_at), expires_at: date(item.expires_at), created_at: date(item.created_at), updated_at: date(item.updated_at),
    source_result_id: nullableId(item.source_result_id), active_run_id: runId, active_status: activeStatus, last_run_at: item.last_run_at === null ? null : date(item.last_run_at), source_count: integer(item.source_count),
    cursors: providers(item.cursors, (raw) => { const cursor = object(raw); return { since: date(cursor.since), until: date(cursor.until), cursor: cursor.cursor === null ? null : typeof cursor.cursor === 'number' ? integer(cursor.cursor) : text(cursor.cursor, 10000), exhausted: bool(cursor.exhausted), revision: integer(cursor.revision) } }),
    coverage: 'current_browser_session',
  }
}
export function parseSource(value: unknown): WatchSource {
  const item = object(value)
  return { id: id(item.id), source_id: text(item.source_id, 200), title: text(item.title, 1000), url: safeUrl(item.url), content_kind: member(item.content_kind, ['abstract', 'full_text']), content_sha256: hash(item.content_sha256), first_seen_at: date(item.first_seen_at), run_id: id(item.run_id), ...assessmentContext(item) }
}
function assessmentContext(item: Record<string, unknown>) {
  const status = item.assessment_status === undefined ? 'unassessed' : member(item.assessment_status, ['unassessed', 'already_cited', 'assessed'] as const)
  const root = item.assessment_root_result_id === undefined ? null : nullableId(item.assessment_root_result_id)
  const history = item.assessment_history_url === undefined || item.assessment_history_url === null ? null : text(item.assessment_history_url, 150)
  if (status !== 'unassessed' && !root || (root === null ? history !== null : history !== `/api/v1/assessments/heads/${root}/history`)) return invalid()
  return { assessment_status: status, assessment_root_result_id: root, assessment_history_url: history }
}
export function parseTick(value: unknown): WatchTick {
  const item = object(value), runId = id(item.run_id)
  if (item.coverage !== 'bounded_provider_window' || item.id !== runId) return invalid()
  return { id: runId, run_id: runId, watch_id: id(item.watch_id), status: member(item.status, STATUSES), slot: date(item.slot), created_at: date(item.created_at), updated_at: date(item.updated_at), added_sources: integer(item.added_sources), coverage: 'bounded_provider_window', ingestion_status: item.ingestion_status === undefined || item.ingestion_status === null ? null : member(item.ingestion_status, ['complete', 'partial', 'failed'] as const), error_code: item.error_code === undefined || item.error_code === null ? null : member(item.error_code, ['source_limit_reached', 'all_providers_unavailable'] as const), diagnostics: providers(item.diagnostics, (raw) => {
    const diagnostic = object(raw), code = diagnostic.code === undefined ? undefined : text(diagnostic.code, 100)
    if (code !== undefined && !/^[a-z_]{1,100}$/.test(code)) return invalid()
    return { status: member(diagnostic.status, ['succeeded', 'failed']), ...(code === undefined ? {} : { code }), sources_seen: integer(diagnostic.sources_seen), added_sources: integer(diagnostic.added_sources), exhausted: bool(diagnostic.exhausted), ...(diagnostic.since === undefined ? {} : { since: date(diagnostic.since) }), ...(diagnostic.until === undefined ? {} : { until: date(diagnostic.until) }) }
  }) }
}
export function parseSourceDetail(value: unknown): WatchSourceDetail {
  const item = object(value), doc = object(item.document)
  if (item.id !== item.version_id) return invalid()
  const bibliography = parseBibliography(doc.bibliography, invalid), coverage = parseSourceCoverage(doc.coverage, invalid)
  const document: SourceDocument = {
    source_id: text(doc.source_id, 200), title: text(doc.title, 1000), url: safeUrl(doc.url), text: text(doc.text, 20000), content_kind: member(doc.content_kind, ['abstract', 'full_text']), retrieved_at: date(doc.retrieved_at), extraction_method: text(doc.extraction_method, 100),
    ...(bibliography === undefined ? {} : { bibliography }), ...(coverage === undefined ? {} : { coverage }),
  }
  validateSourceCoverageContext(document, invalid)
  return { id: id(item.id), watch_id: id(item.watch_id), run_id: id(item.run_id), version_id: id(item.version_id), retrieval_id: id(item.retrieval_id), content_sha256: hash(item.content_sha256), metadata_sha256: hash(item.metadata_sha256), ...assessmentContext(item), document }
}
function page<T>(value: unknown, key: string, parse: (item: unknown) => T): Page<T> {
  const item = object(value), values = item[key]
  if (!Array.isArray(values) || values.length > 20) return invalid()
  const items = values.map(parse), ids = items.map((value) => (value as { id: string }).id)
  if (new Set(ids).size !== ids.length) return invalid()
  return { items, next_cursor: nullableId(item.next_cursor) }
}
function inputId(value: string): string { if (!UUID.test(value)) throw new IngestionError('invalid_request'); return value }
function path(watchId: string): string { return `${BASE}/${inputId(watchId)}` }
function listPath(base: string, before?: string | null): string { return `${base}?limit=20${before ? `&before=${inputId(before)}` : ''}` }

async function request<T>(url: string, parse: (value: unknown) => T, signal?: AbortSignal, write?: { csrf: string; body?: unknown }): Promise<T> {
  const controller = new AbortController(), abort = () => controller.abort()
  signal?.addEventListener('abort', abort, { once: true })
  if (signal?.aborted) controller.abort()
  const timer = window.setTimeout(abort, 10000)
  try {
    const response = await fetch(url, { method: write ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: controller.signal,
      headers: { Accept: 'application/json', ...(write ? { 'X-CSRF-Token': write.csrf, ...(write.body === undefined ? {} : { 'Content-Type': 'application/json' }) } : {}) }, ...(write?.body === undefined ? {} : { body: JSON.stringify(write.body) }) })
    if (!response.ok) {
      let code = 'unavailable'
      try { const problem = object(await response.json()); const value = problem.detail ?? problem.code; if (typeof value === 'string' && /^[a-z_]{1,100}$/.test(value)) code = value } catch { /* Never display untrusted server details. */ }
      if (response.status === 401 || response.status === 403) throw new IngestionError('session_expired')
      throw new IngestionError(code, !!write && response.status >= 500 && code !== 'ingestion_execution_disabled')
    }
    const size = response.headers?.get('content-length')
    if (size && (!/^\d+$/.test(size) || Number(size) > MAX_BYTES)) return invalid()
    let raw: unknown
    if (response.body?.getReader) {
      const reader = response.body.getReader(), decoder = new TextDecoder('utf-8', { fatal: true }); let bytes = 0, json = ''
      try { while (true) { const chunk = await reader.read(); if (chunk.done) break; bytes += chunk.value.byteLength; if (bytes > MAX_BYTES) { await reader.cancel(); return invalid() }; json += decoder.decode(chunk.value, { stream: true }) }; json += decoder.decode(); raw = JSON.parse(json) } finally { reader.releaseLock() }
    } else raw = await response.json()
    try { return parse(raw) } catch (error) { if (write) throw new IngestionError('invalid_response', true); throw error }
  } catch (error) {
    if (error instanceof IngestionError) throw error
    throw new IngestionError('unavailable', !!write)
  } finally { window.clearTimeout(timer); signal?.removeEventListener('abort', abort) }
}
export const loadWatchCapabilities = (signal?: AbortSignal) => request(`${BASE}/capabilities`, parseCapabilities, signal)
export const listWatches = (signal?: AbortSignal, before?: string | null) => request(listPath(BASE, before), (value) => page(value, 'watches', parseWatch), signal)
export const getWatch = (watchId: string, signal?: AbortSignal) => request(path(watchId), parseWatch, signal)
export const listWatchSources = (watchId: string, signal?: AbortSignal, before?: string | null) => request(listPath(`${path(watchId)}/sources`, before), (value) => page(value, 'sources', parseSource), signal)
export const listWatchTicks = (watchId: string, signal?: AbortSignal, before?: string | null) => request(listPath(`${path(watchId)}/ticks`, before), (value) => page(value, 'ticks', parseTick), signal)
export async function getWatchSource(watchId: string, sourceId: string, signal?: AbortSignal): Promise<WatchSourceDetail> {
  const result = await request(`${path(watchId)}/sources/${inputId(sourceId)}`, parseSourceDetail, signal)
  if (result.watch_id !== watchId || result.id !== sourceId) return invalid()
  return result
}
export const createWatch = (body: CreateWatch, csrf: string, signal?: AbortSignal) => request(BASE, parseWatch, signal, { csrf, body })
export const changeWatch = (watchId: string, action: 'pause' | 'resume', csrf: string, signal?: AbortSignal) => request(`${path(watchId)}/${action}`, parseWatch, signal, { csrf })
export async function checkWatch(watchId: string, csrf: string, signal?: AbortSignal): Promise<WatchTick> {
  const result = await request(`${path(watchId)}/check`, parseTick, signal, { csrf })
  if (result.watch_id !== watchId) throw new IngestionError('invalid_response', true)
  return result
}
