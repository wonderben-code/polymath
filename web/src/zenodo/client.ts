// Connect Zenodo for every visitor (docs/CONNECT_ZENODO.md). Every route is the
// visitor's own; no route ever returns a token.
import type { Creator, Destination } from '../publications/client'

export type ZenodoStatusCode = 'preview' | 'queued' | 'running' | 'checking' | 'published' | 'failed' | 'missing' | 'reconnect'
export interface ZenodoDestination { destination: Destination; connected: boolean; connected_at: string | null }
export interface ZenodoStatus { available: boolean; destinations: ZenodoDestination[]; daily_publications: number }
export interface VisitorPublication {
  id: string; artifact_id: string; destination: Destination; status: ZenodoStatusCode; manifest_sha256: string
  title: string; creators: Creator[]; description: string; keywords: string[]; license: string
  file_name: string; markdown_sha256: string; doi: string | null; doi_url: string | null; record_url: string | null
  error: string | null; created_at: string; published_at: string | null
}
export interface ZenodoMetadata { title: string; description: string; creators: Creator[]; keywords: string[]; license: 'cc-by-4.0' | 'cc0-1.0' }

export class ZenodoError extends Error {
  constructor(public readonly code: string) { super(code); this.name = 'ZenodoError' }
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const HASH = /^[0-9a-f]{64}$/
const STATUSES: ZenodoStatusCode[] = ['preview', 'queued', 'running', 'checking', 'published', 'failed', 'missing', 'reconnect']
function invalid(): never { throw new ZenodoError('invalid_response') }
function object(value: unknown): Record<string, unknown> { return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : invalid() }
function text(value: unknown, max = 5000): string { return typeof value === 'string' && value.length <= max ? value : invalid() }
function nullableText(value: unknown, max = 500): string | null { return value === null || value === undefined ? null : text(value, max) }
function destination(value: unknown): Destination { return value === 'sandbox' || value === 'production' ? value : invalid() }

function https(value: string | null, hosts: string[]): string | null {
  if (value === null) return null
  try { const url = new URL(value); return url.protocol === 'https:' && hosts.includes(url.host) ? url.toString() : null } catch { return null }
}

export function parseStatus(value: unknown): ZenodoStatus {
  const item = object(value)
  const rows = Array.isArray(item.destinations) && item.destinations.length <= 2 ? item.destinations : invalid()
  return {
    available: item.available === true,
    destinations: rows.map((row) => { const entry = object(row); return { destination: destination(entry.destination), connected: entry.connected === true, connected_at: nullableText(entry.connected_at, 64) } }),
    daily_publications: typeof item.daily_publications === 'number' ? item.daily_publications : 0,
  }
}

export function parsePublication(value: unknown): VisitorPublication {
  const item = object(value)
  const id = text(item.id, 36), artifact = text(item.artifact_id, 36), sha = text(item.manifest_sha256, 64)
  if (!UUID.test(id) || !UUID.test(artifact) || !HASH.test(sha)) invalid()
  const status = STATUSES.includes(item.status as ZenodoStatusCode) ? item.status as ZenodoStatusCode : invalid()
  const creators = Array.isArray(item.creators) && item.creators.length >= 1 && item.creators.length <= 10 ? item.creators.map((entry) => {
    const creator = object(entry)
    return { name: text(creator.name, 200), affiliation: nullableText(creator.affiliation, 300), orcid: nullableText(creator.orcid, 19) }
  }) : invalid()
  return {
    id, artifact_id: artifact, destination: destination(item.destination), status, manifest_sha256: sha,
    title: text(item.title, 300), creators, description: text(item.description), keywords: Array.isArray(item.keywords) ? item.keywords.map((word) => text(word, 200)) : invalid(),
    license: text(item.license, 20), file_name: text(item.file_name, 60), markdown_sha256: text(item.markdown_sha256, 64),
    doi: nullableText(item.doi, 200), doi_url: https(nullableText(item.doi_url, 300), ['doi.org']),
    record_url: https(nullableText(item.record_url), ['zenodo.org', 'sandbox.zenodo.org']),
    error: nullableText(item.error, 80), created_at: text(item.created_at, 64), published_at: nullableText(item.published_at, 64),
  }
}

async function request(path: string, options: { method?: 'GET' | 'POST' | 'DELETE'; csrf?: string | null; body?: unknown; signal?: AbortSignal } = {}): Promise<unknown> {
  const method = options.method ?? 'GET'
  if (method !== 'GET' && (!options.csrf || !HASH.test(options.csrf))) throw new ZenodoError('session_changed')
  let response: Response
  try {
    response = await fetch(path, {
      method, credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: options.signal,
      headers: { Accept: 'application/json', ...(method !== 'GET' ? { 'X-CSRF-Token': options.csrf as string } : {}), ...(options.body === undefined ? {} : { 'Content-Type': 'application/json' }) },
      ...(options.body === undefined ? {} : { body: JSON.stringify(options.body) }),
    })
  } catch { throw new ZenodoError(options.signal?.aborted ? 'aborted' : 'connection_failed') }
  let payload: unknown = null
  try { payload = await response.json() } catch { if (response.ok) throw new ZenodoError('invalid_response') }
  if (!response.ok) {
    const detail = payload && typeof payload === 'object' && typeof (payload as { detail?: unknown }).detail === 'string' ? (payload as { detail: string }).detail : ''
    throw new ZenodoError(response.status === 401 ? 'session_expired' : response.status === 403 ? 'session_changed' : detail || 'unavailable')
  }
  return payload
}

export async function zenodoStatus(signal?: AbortSignal): Promise<ZenodoStatus> { return parseStatus(await request('/api/v1/zenodo/status', { signal })) }

export async function beginZenodoConnect(target: Destination, csrf: string | null): Promise<string> {
  const url = text(object(await request('/api/v1/zenodo/connect', { method: 'POST', csrf, body: { destination: target } })).authorize_url, 2000)
  const parsed = new URL(url)
  const expected = target === 'sandbox' ? 'sandbox.zenodo.org' : 'zenodo.org'
  if (parsed.protocol !== 'https:' || parsed.host !== expected || parsed.pathname !== '/oauth/authorize') invalid()
  return parsed.toString()
}

export async function completeZenodoConnect(code: string, state: string, csrf: string | null): Promise<Destination> {
  return destination(object(await request('/api/v1/zenodo/complete', { method: 'POST', csrf, body: { code, state } })).destination)
}

export async function disconnectZenodo(target: Destination, csrf: string | null): Promise<ZenodoStatus> {
  return parseStatus(await request(`/api/v1/zenodo/connections/${target}`, { method: 'DELETE', csrf }))
}

export async function previewZenodoRecord(artifactId: string, target: Destination, metadata: ZenodoMetadata, csrf: string | null): Promise<VisitorPublication> {
  return parsePublication(await request('/api/v1/zenodo/publications', { method: 'POST', csrf, body: { artifact_id: artifactId, destination: target, metadata } }))
}

export async function publishZenodoRecord(record: VisitorPublication, confirmPermanent: boolean, csrf: string | null): Promise<VisitorPublication> {
  return parsePublication(await request(`/api/v1/zenodo/publications/${record.id}/publish`, { method: 'POST', csrf, body: { manifest_sha256: record.manifest_sha256, confirm_permanent: confirmPermanent } }))
}

export async function retryZenodoRecord(id: string, csrf: string | null): Promise<VisitorPublication> {
  return parsePublication(await request(`/api/v1/zenodo/publications/${id}/retry`, { method: 'POST', csrf }))
}

export async function zenodoRecords(artifactId?: string, signal?: AbortSignal): Promise<VisitorPublication[]> {
  const query = artifactId ? `?artifact_id=${encodeURIComponent(artifactId)}` : ''
  const list = object(await request(`/api/v1/zenodo/publications${query}`, { signal })).publications
  return Array.isArray(list) ? list.map(parsePublication) : invalid()
}

export async function zenodoRecord(id: string, signal?: AbortSignal): Promise<VisitorPublication> {
  return parsePublication(await request(`/api/v1/zenodo/publications/${id}`, { signal }))
}

export const DESTINATION_WORDS: Record<Destination, { name: string; line: string }> = {
  sandbox: { name: 'Zenodo sandbox', line: 'A practice site. Records there are for trying things out.' },
  production: { name: 'Zenodo', line: 'Real, permanent public records with a DOI.' },
}

export const STATUS_WORDS: Record<ZenodoStatusCode, string> = {
  preview: 'Ready to review', queued: 'Publishing…', running: 'Publishing…', checking: 'Checking with Zenodo…',
  published: 'Published', failed: 'Did not complete', missing: 'Nothing reached Zenodo', reconnect: 'Needs Zenodo again',
}

export function errorWords(code: string): string {
  switch (code) {
    case 'zenodo_not_connected': return 'Connect Zenodo first.'
    case 'zenodo_reconnect_required': return 'Zenodo needs you to connect again.'
    case 'zenodo_daily_limit': return 'You have published the most records allowed today. Try again tomorrow.'
    case 'zenodo_finding_not_current': return 'This finding has changed since it was written, so it cannot be published as is.'
    case 'zenodo_publication_changed': return 'The record changed. Review it again before publishing.'
    case 'zenodo_publication_invalid': return 'Check the details: plain text only, a name, and a valid ORCID if you add one.'
    case 'zenodo_state_invalid': return 'That Zenodo sign-in link has expired. Please connect again.'
    case 'zenodo_connection_refused': case 'zenodo_permission_missing': return 'Zenodo did not grant permission to publish. Please connect again and approve Polymath.'
    case 'session_expired': case 'session_changed': return 'Your session changed. Reload the page and try again.'
    default: return 'Zenodo could not be reached just now. Please try again.'
  }
}
