import type { SavedArtifact } from '../artifacts/types'

export type Destination = 'sandbox' | 'production'
export interface Creator { name: string; affiliation: string | null; orcid: string | null }
export interface PublicationMetadata {
  title: string
  description: string
  creators: Creator[]
  keywords: string[]
  license: 'cc-by-4.0' | 'cc0-1.0'
  upload_type: 'publication'
  publication_type: 'preprint'
  access_right: 'open'
}
export interface Publisher { identifier: string; display_name: string }
export interface PublicationCapabilities { configured: boolean; publisher: Publisher | null; destinations: Destination[]; can_prepare: boolean }
export interface OperatorSession {
  configured: boolean
  authenticated: boolean
  actor_id: string | null
  display_name: string | null
  expires_at: string | null
  csrf_token: string | null
}
export interface PublicationApproval {
  id: string
  manifest_id: string
  manifest_sha256: string
  actor_id: string
  approved_at: string
  expires_at: string
  revoked_at: string | null
  valid: boolean
}
export interface PublicationManifest {
  id: string
  manifest_sha256: string
  manifest: {
    protocol: 'polymath-publication-v1'
    artifact_id: string
    artifact_run_id: string
    markdown_sha256: string
    markdown_bytes: number
    payload_sha256: string
    basis_sha256: string
    file_name: string
    media_type: 'text/markdown; charset=utf-8'
    remote_marker: string
    metadata: PublicationMetadata
    publisher: Publisher
    destination: Destination
  }
  created_at: string
  approval: PublicationApproval | null
  publication_status: 'not_published'
  artifact_export_url: string
}
export interface PublicationHistory { manifests: PublicationManifest[]; next_cursor: string | null }
export type PublicationErrorCode = 'browser_expired' | 'browser_changed' | 'operator_expired' | 'invalid_credential' | 'unavailable' | 'not_found' | 'manifest_changed' | 'invalid_request' | 'invalid_response' | 'connection_failed' | 'aborted'
export class PublicationError extends Error {
  constructor(public readonly code: PublicationErrorCode) { super(code); this.name = 'PublicationError' }
}

const UUID = /^[a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12}$/
const HASH = /^[a-f\d]{64}$/
const MAX_BYTES = 2 * 1024 * 1024
function invalid(): never { throw new PublicationError('invalid_response') }
function object(value: unknown): Record<string, unknown> { return typeof value === 'object' && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : invalid() }
function text(value: unknown, maximum: number): string {
  if (typeof value !== 'string' || !value.trim() || value.length > maximum * 2 || Array.from(value).length > maximum || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\uD800-\uDFFF]/u.test(value)) return invalid()
  return value
}
function id(value: unknown): string { const parsed = text(value, 36); return UUID.test(parsed) ? parsed : invalid() }
function hash(value: unknown): string { const parsed = text(value, 64); return HASH.test(parsed) ? parsed : invalid() }
function actor(value: unknown): string { const parsed = text(value, 100); return /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$/.test(parsed) ? parsed : invalid() }
function date(value: unknown): string { const parsed = text(value, 100); return Number.isFinite(Date.parse(parsed)) ? parsed : invalid() }
function boolean(value: unknown): boolean { return typeof value === 'boolean' ? value : invalid() }
function nullable<T>(value: unknown, parse: (value: unknown) => T): T | null { return value === null ? null : parse(value) }
function list<T>(value: unknown, parse: (value: unknown) => T, minimum: number, maximum: number): T[] { return Array.isArray(value) && value.length >= minimum && value.length <= maximum ? value.map(parse) : invalid() }
function destination(value: unknown): Destination { return value === 'sandbox' || value === 'production' ? value : invalid() }
function publisher(value: unknown): Publisher { const item = object(value), identifier = text(item.identifier, 80); return /^[A-Za-z][A-Za-z0-9_.-]{0,79}$/.test(identifier) ? { identifier, display_name: plain(item.display_name, 200) } : invalid() }
function plain(value: unknown, maximum: number): string { const parsed = text(value, maximum); return /<\s*\/?\s*[A-Za-z][^>]*>|https?:\/\//iu.test(parsed) ? invalid() : parsed }
function inputId(value: string): string { if (!UUID.test(value)) throw new PublicationError('invalid_request'); return value }

export function validOrcid(value: string): boolean {
  if (!/^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$/.test(value)) return false
  const digits = value.replaceAll('-', '')
  let total = 0
  for (const character of digits.slice(0, 15)) total = (total + Number(character)) * 2
  const result = (12 - total % 11) % 11
  return digits[15] === (result === 10 ? 'X' : String(result))
}

export function parseMetadata(value: unknown): PublicationMetadata {
  const item = object(value)
  if (item.upload_type !== 'publication' || item.publication_type !== 'preprint' || item.access_right !== 'open' || item.license !== 'cc-by-4.0' && item.license !== 'cc0-1.0') return invalid()
  const creators = list(item.creators, (value) => {
    const creator = object(value)
    const orcid = creator.orcid === undefined || creator.orcid === null ? null : text(creator.orcid, 19)
    if (orcid !== null && !validOrcid(orcid)) return invalid()
    return { name: plain(creator.name, 200), affiliation: creator.affiliation === undefined || creator.affiliation === null ? null : plain(creator.affiliation, 300), orcid }
  }, 1, 10)
  const keywords = list(item.keywords, (value) => plain(value, 80), 0, 20)
  if (new Set(keywords.map((keyword) => keyword.trim().replace(/\s+/gu, ' ').toLowerCase().replaceAll('ß', 'ss').replaceAll('ς', 'σ'))).size !== keywords.length) return invalid()
  return { title: plain(item.title, 300), description: plain(item.description, 5000), creators, keywords, license: item.license, upload_type: 'publication', publication_type: 'preprint', access_right: 'open' }
}

export function parseCapabilities(value: unknown): PublicationCapabilities {
  const item = object(value), configured = boolean(item.configured), canPrepare = boolean(item.can_prepare)
  const identity = nullable(item.publisher, publisher), destinations = list(item.destinations, destination, 0, 2)
  if (new Set(destinations).size !== destinations.length || canPrepare && (!configured || !identity || !destinations.length)) return invalid()
  return { configured, publisher: identity, destinations, can_prepare: canPrepare }
}

export function parseOperatorSession(value: unknown): OperatorSession {
  const item = object(value), configured = boolean(item.configured), authenticated = boolean(item.authenticated)
  const session = { configured, authenticated, actor_id: nullable(item.actor_id, actor), display_name: nullable(item.display_name, (value) => text(value, 120)), expires_at: nullable(item.expires_at, date), csrf_token: nullable(item.csrf_token, hash) }
  if (authenticated ? !configured || !session.actor_id || !session.display_name || !session.expires_at || !session.csrf_token : session.actor_id !== null || session.display_name !== null || session.expires_at !== null || session.csrf_token !== null) return invalid()
  return session
}

export function parseManifest(value: unknown): PublicationManifest {
  const item = object(value), manifest = object(item.manifest)
  const artifactId = id(manifest.artifact_id), manifestId = id(item.id), manifestHash = hash(item.manifest_sha256)
  if (manifest.protocol !== 'polymath-publication-v1' || manifest.media_type !== 'text/markdown; charset=utf-8' || manifest.file_name !== `polymath-${artifactId}.md` || item.publication_status !== 'not_published' || item.artifact_export_url !== `/api/v1/artifacts/${artifactId}/export` || typeof manifest.remote_marker !== 'string' || !/^polymath-[a-f\d]{64}$/.test(manifest.remote_marker)) return invalid()
  if (typeof manifest.markdown_bytes !== 'number' || !Number.isSafeInteger(manifest.markdown_bytes) || manifest.markdown_bytes < 1 || manifest.markdown_bytes > 262144) return invalid()
  const approval = nullable(item.approval, (value) => {
    const receipt = object(value)
    const parsed = { id: id(receipt.id), manifest_id: id(receipt.manifest_id), manifest_sha256: hash(receipt.manifest_sha256), actor_id: actor(receipt.actor_id), approved_at: date(receipt.approved_at), expires_at: date(receipt.expires_at), revoked_at: nullable(receipt.revoked_at, date), valid: boolean(receipt.valid) }
    if (parsed.manifest_id !== manifestId || parsed.manifest_sha256 !== manifestHash || Date.parse(parsed.expires_at) <= Date.parse(parsed.approved_at) || parsed.valid && parsed.revoked_at !== null) return invalid()
    return parsed
  })
  return { id: manifestId, manifest_sha256: manifestHash, manifest: { protocol: 'polymath-publication-v1', artifact_id: artifactId, artifact_run_id: id(manifest.artifact_run_id), markdown_sha256: hash(manifest.markdown_sha256), markdown_bytes: manifest.markdown_bytes, payload_sha256: hash(manifest.payload_sha256), basis_sha256: hash(manifest.basis_sha256), file_name: manifest.file_name, media_type: 'text/markdown; charset=utf-8', remote_marker: manifest.remote_marker, metadata: parseMetadata(manifest.metadata), publisher: publisher(manifest.publisher), destination: destination(manifest.destination) }, created_at: date(item.created_at), approval, publication_status: 'not_published', artifact_export_url: item.artifact_export_url }
}

export function manifestMatchesArtifact(record: PublicationManifest, artifact: SavedArtifact): boolean {
  const manifest = record.manifest
  return artifact.status === 'ready' && artifact.kind !== 'formalise' && manifest.artifact_id === artifact.id && manifest.artifact_run_id === artifact.run_id && manifest.markdown_sha256 === artifact.markdown_sha256 && manifest.payload_sha256 === artifact.payload_sha256 && manifest.basis_sha256 === artifact.basis_sha256 && manifest.markdown_bytes === new TextEncoder().encode(artifact.markdown).length
}

async function json(response: Response): Promise<unknown> {
  const reader = response.body?.getReader()
  if (!reader) return invalid()
  const chunks: Uint8Array[] = []
  let length = 0
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      length += value.byteLength
      if (length > MAX_BYTES) { await reader.cancel(); return invalid() }
      chunks.push(value)
    }
    const bytes = new Uint8Array(length)
    let offset = 0
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length }
    return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)) as unknown
  } catch { return invalid() }
}

async function request(path: string, signal?: AbortSignal, write?: { csrf: string; operatorCsrf?: string; body?: unknown }): Promise<unknown> {
  const controller = new AbortController(), abort = () => controller.abort()
  signal?.addEventListener('abort', abort, { once: true })
  if (signal?.aborted) controller.abort()
  const timeout = window.setTimeout(abort, 10000)
  try {
    if (write && !HASH.test(write.csrf)) throw new PublicationError('browser_changed')
    if (write?.operatorCsrf !== undefined && !HASH.test(write.operatorCsrf)) throw new PublicationError('operator_expired')
    const response = await fetch(path, { credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: controller.signal, method: write ? 'POST' : 'GET', headers: { Accept: 'application/json', ...(write ? { 'X-CSRF-Token': write.csrf, ...(write.body === undefined ? {} : { 'Content-Type': 'application/json' }), ...(write.operatorCsrf === undefined ? {} : { 'X-Operator-CSRF-Token': write.operatorCsrf }) } : {}) }, ...(write?.body === undefined ? {} : { body: JSON.stringify(write.body) }) })
    const payload = await json(response)
    if (!response.ok) {
      const error = object(payload).detail
      if (response.status === 401) throw new PublicationError(error === 'operator_session_required' ? 'operator_expired' : error === 'invalid_operator_credential' ? 'invalid_credential' : 'browser_expired')
      if (response.status === 403) throw new PublicationError(error === 'operator_csrf_check_failed' ? 'operator_expired' : 'browser_changed')
      if (response.status === 404) throw new PublicationError('not_found')
      if (response.status === 409) throw new PublicationError('manifest_changed')
      if (response.status === 422) throw new PublicationError('invalid_request')
      throw new PublicationError('unavailable')
    }
    return payload
  } catch (error) {
    if (error instanceof PublicationError) throw error
    throw new PublicationError(signal?.aborted ? 'aborted' : 'connection_failed')
  } finally { signal?.removeEventListener('abort', abort); window.clearTimeout(timeout) }
}

export async function publicationCapabilities(signal?: AbortSignal): Promise<PublicationCapabilities> { return parseCapabilities(await request('/api/v1/publications/capabilities', signal)) }
export async function operatorSession(signal?: AbortSignal): Promise<OperatorSession> { return parseOperatorSession(await request('/api/v1/operator/session', signal)) }
export async function loginOperator(token: string, csrf: string, signal?: AbortSignal): Promise<OperatorSession> {
  if (typeof token !== 'string' || !token.length || token.length > 256) throw new PublicationError('invalid_request')
  const session = parseOperatorSession(await request('/api/v1/operator/session', signal, { csrf, body: { token } }))
  return session.authenticated ? session : invalid()
}
export async function logoutOperator(csrf: string, operatorCsrf: string, signal?: AbortSignal): Promise<OperatorSession> {
  const session = parseOperatorSession(await request('/api/v1/operator/logout', signal, { csrf, operatorCsrf }))
  return !session.authenticated ? session : invalid()
}
export async function publicationHistory(artifactId: string, signal?: AbortSignal, before?: string): Promise<PublicationHistory> {
  const query = new URLSearchParams({ artifact_id: inputId(artifactId) })
  if (before !== undefined) query.set('before', inputId(before))
  const payload = object(await request(`/api/v1/publications?${query}`, signal))
  const manifests = list(payload.manifests, parseManifest, 0, 50)
  if (manifests.some((item) => item.manifest.artifact_id !== artifactId) || new Set(manifests.map((item) => item.id)).size !== manifests.length) return invalid()
  return { manifests, next_cursor: nullable(payload.next_cursor, id) }
}
export async function getPublication(manifestId: string, signal?: AbortSignal): Promise<PublicationManifest> {
  const record = parseManifest(await request(`/api/v1/publications/${inputId(manifestId)}`, signal))
  return record.id === manifestId ? record : invalid()
}
export async function preparePublication(artifactId: string, metadata: PublicationMetadata, target: Destination, csrf: string, signal?: AbortSignal): Promise<PublicationManifest> {
  let validated: PublicationMetadata
  try { validated = parseMetadata(metadata) } catch { throw new PublicationError('invalid_request') }
  if (target !== 'sandbox' && target !== 'production') throw new PublicationError('invalid_request')
  const record = parseManifest(await request('/api/v1/publications', signal, { csrf, body: { artifact_id: inputId(artifactId), metadata: validated, destination: target } }))
  if (record.manifest.artifact_id !== artifactId || record.manifest.destination !== target || JSON.stringify(record.manifest.metadata) !== JSON.stringify(validated)) return invalid()
  return record
}
export async function approvePublication(record: PublicationManifest, csrf: string, operatorCsrf: string, signal?: AbortSignal): Promise<PublicationManifest> {
  const expected = hash(record.manifest_sha256)
  const updated = parseManifest(await request(`/api/v1/publications/${inputId(record.id)}/approve`, signal, { csrf, operatorCsrf, body: { expected_manifest_sha256: expected } }))
  if (updated.id !== record.id || updated.manifest_sha256 !== expected || JSON.stringify(updated.manifest) !== JSON.stringify(record.manifest)) return invalid()
  return updated
}
export async function revokePublication(record: PublicationManifest, csrf: string, operatorCsrf: string, signal?: AbortSignal): Promise<PublicationManifest> {
  if (!record.approval) throw new PublicationError('invalid_request')
  const updated = parseManifest(await request(`/api/v1/publications/approvals/${inputId(record.approval.id)}/revoke`, signal, { csrf, operatorCsrf }))
  if (updated.id !== record.id || updated.manifest_sha256 !== record.manifest_sha256 || JSON.stringify(updated.manifest) !== JSON.stringify(record.manifest)) return invalid()
  return updated
}
