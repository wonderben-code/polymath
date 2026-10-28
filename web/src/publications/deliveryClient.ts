import { PublicationError, publicationRequest } from './client'
import type { Destination, PublicationManifest } from './client'

export type PublicationJobStatus = 'queued' | 'running' | 'interrupted' | 'published' | 'failed' | 'cancelled'
export type PublicationStage = 'queued' | 'create' | 'upload' | 'verify' | 'publish' | 'reconcile' | 'complete'
export interface PublicationJob {
  id: string
  manifest_id: string
  approval_id: string
  manifest_sha256: string
  status: PublicationJobStatus
  work_kind: 'publish' | 'reconcile'
  stage: PublicationStage
  created_at: string
  updated_at: string
  reason: string | null
  event_sequence: number
  external_outcome_unknown: boolean
  frozen: boolean
  publication_reported: boolean
  destination: Destination
  sandbox: boolean
  verified: boolean
  doi: string | null
  record_url: string | null
  can_cancel: boolean
  can_reconcile: boolean
  can_resume: boolean
}
export interface PublicationJobEvent { sequence: number; kind: string; summary: string; created_at: string }
export interface PublicationJobEvents { events: PublicationJobEvent[]; next_cursor: number; terminal: boolean }
export interface PublicationExecution { execution_available: boolean; reason: 'publication_execution_disabled' | 'publication_unavailable' | 'operator_unavailable' | null }
export interface PublicationJobHistory { jobs: PublicationJob[]; next_cursor: string | null }

const UUID = /^[a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12}$/
const HASH = /^[a-f\d]{64}$/
const DOI = /^10\.[0-9]{4,9}\/[A-Za-z0-9][A-Za-z0-9._;()/:+\-]{0,180}$/
function invalid(): never { throw new PublicationError('invalid_response') }
function object(value: unknown): Record<string, unknown> { return typeof value === 'object' && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : invalid() }
function text(value: unknown, maximum: number): string { return typeof value === 'string' && value.length <= maximum && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\uD800-\uDFFF]/u.test(value) ? value : invalid() }
function id(value: unknown): string { const parsed = text(value, 36); return UUID.test(parsed) ? parsed : invalid() }
function inputId(value: string): string { if (!UUID.test(value)) throw new PublicationError('invalid_request'); return value }
function hash(value: unknown): string { const parsed = text(value, 64); return HASH.test(parsed) ? parsed : invalid() }
function date(value: unknown): string { const parsed = text(value, 100); return Number.isFinite(Date.parse(parsed)) ? parsed : invalid() }
function integer(value: unknown): number { return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : invalid() }
function boolean(value: unknown): boolean { return typeof value === 'boolean' ? value : invalid() }
function member<T extends string>(value: unknown, allowed: readonly T[]): T { return typeof value === 'string' && allowed.includes(value as T) ? value as T : invalid() }
function list<T>(value: unknown, parse: (value: unknown) => T, maximum: number): T[] { return Array.isArray(value) && value.length <= maximum ? value.map(parse) : invalid() }

export function publicationRecordUrl(value: string, target: Destination): string | null {
  try {
    const url = new URL(value)
    if (url.protocol !== 'https:' || url.hostname !== (target === 'sandbox' ? 'sandbox.zenodo.org' : 'zenodo.org') || url.username || url.password || url.port || url.search || url.hash || !/^\/records?\/[1-9]\d{0,19}$/.test(url.pathname)) return null
    return url.href
  } catch { return null }
}
export function publicationDoiUrl(value: string): string | null { return DOI.test(value) ? `https://doi.org/${value.split('/').map(encodeURIComponent).join('/')}` : null }
export function parsePublicationExecution(value: unknown): PublicationExecution {
  const item = object(value), available = boolean(item.execution_available)
  const reason = item.reason === null ? null : member(item.reason, ['publication_execution_disabled', 'publication_unavailable', 'operator_unavailable'])
  if (available && reason !== null) return invalid()
  return { execution_available: available, reason }
}
export function parsePublicationJob(value: unknown): PublicationJob {
  const item = object(value)
  const destination = member(item.destination, ['sandbox', 'production'])
  const sandbox = boolean(item.sandbox), verified = boolean(item.verified)
  if (sandbox !== (destination === 'sandbox')) return invalid()
  const doi = item.doi === null ? null : text(item.doi, 200)
  const recordUrl = item.record_url === null ? null : text(item.record_url, 2000)
  if (doi !== null && publicationDoiUrl(doi) === null || recordUrl !== null && publicationRecordUrl(recordUrl, destination) === null || verified && (!doi || !recordUrl)) return invalid()
  const reason = item.reason === null ? null : text(item.reason, 100)
  if (reason !== null && !/^[a-z_]{1,100}$/.test(reason)) return invalid()
  return { id: id(item.id), manifest_id: id(item.manifest_id), approval_id: id(item.approval_id), manifest_sha256: hash(item.manifest_sha256), status: member(item.status, ['queued', 'running', 'interrupted', 'published', 'failed', 'cancelled']), work_kind: member(item.work_kind, ['publish', 'reconcile']), stage: member(item.stage, ['queued', 'create', 'upload', 'verify', 'publish', 'reconcile', 'complete']), created_at: date(item.created_at), updated_at: date(item.updated_at), reason, event_sequence: integer(item.event_sequence), frozen: item.frozen === undefined ? false : boolean(item.frozen), publication_reported: item.publication_reported === undefined ? false : boolean(item.publication_reported), external_outcome_unknown: boolean(item.external_outcome_unknown), destination, sandbox, verified, doi, record_url: recordUrl, can_cancel: boolean(item.can_cancel), can_reconcile: boolean(item.can_reconcile), can_resume: boolean(item.can_resume) }
}
function bind(job: PublicationJob, record: PublicationManifest): PublicationJob {
  if (job.manifest_id !== record.id || job.manifest_sha256 !== record.manifest_sha256 || job.destination !== record.manifest.destination) return invalid()
  return job
}
export async function publicationExecution(signal?: AbortSignal): Promise<PublicationExecution> { return parsePublicationExecution(await publicationRequest('/api/v1/publication-jobs/capabilities', signal)) }
export async function publicationJobs(record: PublicationManifest, signal?: AbortSignal, before?: string): Promise<PublicationJobHistory> {
  const query = new URLSearchParams({ manifest_id: inputId(record.id) })
  if (before !== undefined) query.set('before', inputId(before))
  const payload = object(await publicationRequest(`/api/v1/publication-jobs?${query}`, signal))
  const jobs = list(payload.jobs, (value) => bind(parsePublicationJob(value), record), 50)
  if (new Set(jobs.map((job) => job.id)).size !== jobs.length) return invalid()
  return { jobs, next_cursor: payload.next_cursor === null ? null : id(payload.next_cursor) }
}
export async function publicationJob(jobId: string, record: PublicationManifest, signal?: AbortSignal): Promise<PublicationJob> {
  const job = bind(parsePublicationJob(await publicationRequest(`/api/v1/publication-jobs/${inputId(jobId)}`, signal)), record)
  return job.id === jobId ? job : invalid()
}
export async function publicationEvents(jobId: string, after: number, signal?: AbortSignal): Promise<PublicationJobEvents> {
  if (!Number.isSafeInteger(after) || after < 0) throw new PublicationError('invalid_request')
  const payload = object(await publicationRequest(`/api/v1/publication-jobs/${inputId(jobId)}/events?after=${after}`, signal))
  const events = list(payload.events, (value) => {
    const event = object(value), sequence = integer(event.sequence)
    if (!sequence) return invalid()
    return { sequence, kind: text(event.kind, 128), summary: text(event.summary, 4000), created_at: date(event.created_at) }
  }, 200)
  const cursor = integer(payload.next_cursor)
  if (events.some((event, index) => event.sequence <= after || index > 0 && event.sequence <= events[index - 1].sequence) || cursor < after || events.length > 0 && cursor < events[events.length - 1].sequence) return invalid()
  return { events, next_cursor: cursor, terminal: boolean(payload.terminal) }
}
export async function startPublication(record: PublicationManifest, approvalId: string, csrf: string, operatorCsrf: string, signal?: AbortSignal): Promise<PublicationJob> {
  const expected = hash(record.manifest_sha256)
  return bind(parsePublicationJob(await publicationRequest('/api/v1/publication-jobs', signal, { csrf, operatorCsrf, body: { manifest_id: inputId(record.id), approval_id: inputId(approvalId), expected_manifest_sha256: expected } })), record)
}
async function mutatePublication(jobId: string, action: 'cancel' | 'reconcile' | 'resume', record: PublicationManifest, csrf: string, operatorCsrf: string, signal?: AbortSignal, approvalId?: string): Promise<PublicationJob> {
  const body = action === 'resume' ? { approval_id: inputId(approvalId!), expected_manifest_sha256: hash(record.manifest_sha256) } : undefined
  const job = bind(parsePublicationJob(await publicationRequest(`/api/v1/publication-jobs/${inputId(jobId)}/${action}`, signal, { csrf, operatorCsrf, ...(body === undefined ? {} : { body }) })), record)
  return job.id === jobId && (approvalId === undefined || job.approval_id === approvalId) ? job : invalid()
}
export function cancelPublication(jobId: string, record: PublicationManifest, csrf: string, operatorCsrf: string, signal?: AbortSignal) { return mutatePublication(jobId, 'cancel', record, csrf, operatorCsrf, signal) }
export function reconcilePublication(jobId: string, record: PublicationManifest, csrf: string, operatorCsrf: string, signal?: AbortSignal) { return mutatePublication(jobId, 'reconcile', record, csrf, operatorCsrf, signal) }
export function resumePublication(jobId: string, record: PublicationManifest, approvalId: string, csrf: string, operatorCsrf: string, signal?: AbortSignal) { return mutatePublication(jobId, 'resume', record, csrf, operatorCsrf, signal, approvalId) }
