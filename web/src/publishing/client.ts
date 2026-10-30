// Publishing levels and the publishing autopilot ledger. These routes never
// write, prepare, approve or publish; the publisher's browser performs each
// step through the ordinary routes and attaches the exact result.
import { PublicationError, publicationRequest } from '../publications/client'
import type { Creator, Destination } from '../publications/client'

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export type Level = 1 | 2 | 3 | 4
export type WriteKind = 'write_summary' | 'write_paper'
export type ItemStatus = 'pending_write' | 'writing' | 'drafted' | 'ready' | 'approved' | 'queued' | 'published' | 'failed' | 'skipped'
export interface PublishingStatus { configured: boolean; publisher: string | null; destinations: Destination[]; publishing_runs: boolean }
export interface PublishingPreferences {
  level: Level
  draft_destination: Destination
  write_kind: WriteKind
  full_text_only: boolean
  daily_cap: number
  creators: Creator[]
  license: 'cc-by-4.0' | 'cc0-1.0'
  keywords: string[]
  enabled_at: string | null
  production_confirmed: boolean
}
export interface PublishingItem { id: string; result_id: string; title: string; level: number; destination: Destination; write_kind: WriteKind; status: ItemStatus; reason: string | null; artifact_id: string | null; manifest_id: string | null; approval_id: string | null; job_id: string | null; created_at: string; updated_at: string }
export type PublishingStep =
  | { action: 'write'; item_id: string; result_id: string; kind: WriteKind }
  | { action: 'wait'; item_id: string; run_id: string }
  | { action: 'prepare'; item_id: string; artifact_id: string; destination: Destination; metadata: unknown }
  | { action: 'approve'; item_id: string; manifest_id: string; manifest_sha256: string }
  | { action: 'queue'; item_id: string; manifest_id: string; approval_id: string; manifest_sha256: string }
  | { action: 'none'; reason: string }

function invalid(): never { throw new PublicationError('invalid_response') }
function record(value: unknown): Record<string, unknown> { return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : invalid() }
function text(value: unknown, max = 400): string { return typeof value === 'string' && value.length <= max ? value : invalid() }
function id(value: unknown): string { return typeof value === 'string' && UUID.test(value) ? value : invalid() }
function nullableId(value: unknown): string | null { return value === null ? null : id(value) }
function oneOf<T extends string | number>(value: unknown, options: readonly T[]): T { return (options as readonly unknown[]).includes(value) ? value as T : invalid() }
const DESTINATIONS = ['sandbox', 'production'] as const
const STATUSES = ['pending_write', 'writing', 'drafted', 'ready', 'approved', 'queued', 'published', 'failed', 'skipped'] as const

function parseCreator(value: unknown): Creator {
  const item = record(value)
  return { name: text(item.name, 200), affiliation: item.affiliation === null || item.affiliation === undefined ? null : text(item.affiliation, 300), orcid: item.orcid === null || item.orcid === undefined ? null : text(item.orcid, 19) }
}

export function parsePreferences(value: unknown): PublishingPreferences {
  const item = record(value)
  const creators = Array.isArray(item.creators) && item.creators.length <= 10 ? item.creators.map(parseCreator) : invalid()
  const keywords = Array.isArray(item.keywords) && item.keywords.length <= 20 ? item.keywords.map((keyword) => text(keyword, 80)) : invalid()
  const cap = Number.isInteger(item.daily_cap) && (item.daily_cap as number) >= 1 && (item.daily_cap as number) <= 10 ? item.daily_cap as number : invalid()
  return {
    level: oneOf(item.level, [1, 2, 3, 4] as const), draft_destination: oneOf(item.draft_destination, DESTINATIONS), write_kind: oneOf(item.write_kind, ['write_summary', 'write_paper'] as const),
    full_text_only: item.full_text_only === true, daily_cap: cap, creators, license: oneOf(item.license, ['cc-by-4.0', 'cc0-1.0'] as const), keywords,
    enabled_at: item.enabled_at === null ? null : text(item.enabled_at, 64), production_confirmed: item.production_confirmed === true,
  }
}

export function parseItem(value: unknown): PublishingItem {
  const item = record(value)
  return {
    id: id(item.id), result_id: id(item.result_id), title: text(item.title, 300), level: oneOf(item.level, [2, 3, 4] as const), destination: oneOf(item.destination, DESTINATIONS),
    write_kind: oneOf(item.write_kind, ['write_summary', 'write_paper'] as const), status: oneOf(item.status, STATUSES), reason: item.reason === null ? null : text(item.reason, 64),
    artifact_id: nullableId(item.artifact_id), manifest_id: nullableId(item.manifest_id), approval_id: nullableId(item.approval_id), job_id: nullableId(item.job_id),
    created_at: text(item.created_at, 64), updated_at: text(item.updated_at, 64),
  }
}

export function parseStep(value: unknown): PublishingStep {
  const item = record(value)
  if (item.action === 'write') return { action: 'write', item_id: id(item.item_id), result_id: id(item.result_id), kind: oneOf(item.kind, ['write_summary', 'write_paper'] as const) }
  if (item.action === 'wait') return { action: 'wait', item_id: id(item.item_id), run_id: id(item.run_id) }
  if (item.action === 'prepare') return { action: 'prepare', item_id: id(item.item_id), artifact_id: id(item.artifact_id), destination: oneOf(item.destination, DESTINATIONS), metadata: record(item.metadata) }
  if (item.action === 'approve') return { action: 'approve', item_id: id(item.item_id), manifest_id: id(item.manifest_id), manifest_sha256: text(item.manifest_sha256, 64) }
  if (item.action === 'queue') return { action: 'queue', item_id: id(item.item_id), manifest_id: id(item.manifest_id), approval_id: id(item.approval_id), manifest_sha256: text(item.manifest_sha256, 64) }
  if (item.action === 'none') return { action: 'none', reason: text(item.reason, 64) }
  return invalid()
}

function itemPath(itemId: string): string { if (!UUID.test(itemId)) throw new PublicationError('invalid_request'); return `/api/v1/publishing/items/${itemId}` }

export async function publishingStatus(signal?: AbortSignal): Promise<PublishingStatus> {
  const item = record(await publicationRequest('/api/v1/publishing/status', signal))
  return { configured: item.configured === true, publisher: item.publisher === null ? null : text(item.publisher, 120), destinations: Array.isArray(item.destinations) ? item.destinations.map((value) => oneOf(value, DESTINATIONS)) : invalid(), publishing_runs: item.publishing_runs === true }
}
export async function publishingPreferences(signal?: AbortSignal): Promise<PublishingPreferences> { return parsePreferences(await publicationRequest('/api/v1/publishing/preferences', signal)) }
export async function savePublishingPreferences(body: Omit<PublishingPreferences, 'enabled_at' | 'production_confirmed'> & { confirm_production: boolean }, csrf: string, operatorCsrf: string, signal?: AbortSignal): Promise<PublishingPreferences> {
  return parsePreferences(await publicationRequest('/api/v1/publishing/preferences', signal, { csrf, operatorCsrf, body }))
}
export async function publishingItems(signal?: AbortSignal): Promise<PublishingItem[]> {
  const list = record(await publicationRequest('/api/v1/publishing/items', signal)).items
  return Array.isArray(list) && list.length <= 100 ? list.map(parseItem) : invalid()
}
export async function nextPublishingStep(csrf: string, operatorCsrf: string, signal?: AbortSignal): Promise<PublishingStep> { return parseStep(await publicationRequest('/api/v1/publishing/next', signal, { csrf, operatorCsrf })) }
export async function attachPublishingStep(itemId: string, stage: 'write' | 'prepare' | 'approve' | 'queue', ref: string, csrf: string, operatorCsrf: string, signal?: AbortSignal): Promise<PublishingItem> {
  return parseItem(await publicationRequest(`${itemPath(itemId)}/attach`, signal, { csrf, operatorCsrf, body: { stage, ref: id(ref) } }))
}
export async function skipPublishingItem(itemId: string, csrf: string, operatorCsrf: string, signal?: AbortSignal): Promise<PublishingItem> { return parseItem(await publicationRequest(`${itemPath(itemId)}/skip`, signal, { csrf, operatorCsrf })) }
