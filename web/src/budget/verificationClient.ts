import { BudgetError, canonicalProposal } from './client'
import type { ReadinessEvidenceReference } from './types'
import type { ArmMetaProbeRequest, MetaProbeAssessment, MetaProbeCapabilities, MetaProbeHistory, MetaProbeJob, MetaProbeManifest, MetaProbePreview, MetaProbePreviewRequest, MetaProbeReceipt, MetaProbeRequest, ProviderAccountAttestation } from './verificationTypes'

export { BudgetError }
const BASE = '/api/v1/budget/verification'
const HASH = /^[a-f\d]{64}$/, UUID = /^[a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12}$/
const ACCOUNT = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$/, MODEL = /^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}$/
const ENDPOINT = 'https://api.meta.ai/v1/chat/completions', MAX_BYTES = 2 * 1024 * 1024, MAX_MONEY = 9000000000000000
const SYSTEM_MESSAGE = 'Perform one isolated JSON protocol check. Do not search, use tools, cite sources, or make scientific claims. Return only one JSON action.'
const USER_MESSAGE = 'Return exactly this JSON action: {"kind":"conclude","conclusion":{"outcome":"insufficient_evidence","reason":"Protocol check only."}}'
const JOB_STATUSES = ['queued', 'running', 'waiting_retry', 'cancel_requested', 'cancelled', 'succeeded', 'failed', 'interrupted'] as const
const REASONS = ['observed_model_missing', 'observed_model_mismatch', 'usage_missing_or_invalid', 'counted_cost_unrepresentable', 'billable_bound_exceeded', 'http_not_success', 'finish_not_stop', 'invalid_insufficient_action', 'expected_action_and_reported_usage'] as const
const ERRORS = new Set(['operator_unavailable', 'readiness_role_unavailable', 'readiness_actor_forbidden', 'meta_probe_unavailable', 'meta_probe_actor_forbidden', 'meta_probe_not_found', 'meta_probe_invalid_request', 'meta_probe_manifest_changed', 'meta_probe_account_conflict', 'meta_probe_prior_unresolved', 'meta_probe_authority_expired', 'meta_probe_outcome_unknown', 'meta_probe_storage_unavailable'])

function invalid(): never { throw new BudgetError('invalid_response') }
function object(value: unknown, required: string[]): Record<string, unknown> { if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid(); const item = value as Record<string, unknown>; if (required.some((key) => !Object.hasOwn(item, key)) || Object.keys(item).some((key) => !required.includes(key))) return invalid(); return item }
function text(value: unknown, maximum: number): string { return typeof value === 'string' && !!value.trim() && Array.from(value).length <= maximum && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\uD800-\uDFFF]/u.test(value) ? value : invalid() }
function integer(value: unknown, minimum = 0, maximum = Number.MAX_SAFE_INTEGER): number { return typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum && value <= maximum ? value : invalid() }
function bool(value: unknown): boolean { return typeof value === 'boolean' ? value : invalid() }
function member<T extends string>(value: unknown, choices: readonly T[]): T { return typeof value === 'string' && choices.includes(value as T) ? value as T : invalid() }
function hash(value: unknown): string { return typeof value === 'string' && HASH.test(value) ? value : invalid() }
function id(value: unknown): string { return typeof value === 'string' && UUID.test(value) ? value : invalid() }
function account(value: unknown): string { return typeof value === 'string' && ACCOUNT.test(value) ? value : invalid() }
function model(value: unknown): string { return typeof value === 'string' && MODEL.test(value) ? value : invalid() }
function code(value: unknown): string { return typeof value === 'string' && /^[a-z_]{1,100}$/.test(value) ? value : invalid() }
function nullable<T>(value: unknown, parse: (value: unknown) => T): T | null { return value === null ? null : parse(value) }
function list<T>(value: unknown, parse: (value: unknown) => T, minimum = 0, maximum = 20): T[] { return Array.isArray(value) && value.length >= minimum && value.length <= maximum ? value.map(parse) : invalid() }
function distinct<T>(items: T[]): T[] { return new Set(items).size === items.length ? items : invalid() }
function utc(value: unknown): string {
  const parsed = text(value, 40), match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?(?:Z|\+00:00)$/.exec(parsed)
  if (!match || !Number.isFinite(Date.parse(parsed)) || new Date(parsed).toISOString().slice(0, 19) !== match[1]) return invalid()
  return parsed
}
function semanticUtc(value: string): string { const parsed = utc(value), match = /^(.*T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(?:Z|\+00:00)$/.exec(parsed)!, fraction = (match[2] ?? '').replace(/0+$/, ''); return `${match[1]}${fraction ? `.${fraction}` : ''}Z` }
function timestamp(value: string): bigint { const match = /\.(\d+)(?:Z|\+00:00)$/.exec(value); return BigInt(Date.parse(value)) * 1000n + BigInt((match?.[1] ?? '').padEnd(6, '0').slice(3) || '0') }
function decimalCounter(value: unknown): string { if (typeof value !== 'string' || !/^(?:0|[1-9]\d{0,18})$/.test(value) || BigInt(value) > 9223372036854775807n) return invalid(); return value }
function noResearch(value: Record<string, unknown>): false { return value.research_execution_available === false ? false : invalid() }
function evidence(value: unknown): ReadinessEvidenceReference {
  const item = object(value, ['url', 'content_sha256']), url = text(item.url, 2000)
  try { const parsed = new URL(url); if (parsed.protocol !== 'https:' || !parsed.hostname || parsed.username || parsed.password || parsed.port || parsed.search || parsed.hash || /[\s\u0000-\u001f\u007f]/u.test(url)) return invalid() } catch { return invalid() }
  const path = /^https:\/\/[^/]+(\/[^?#]*)?$/.exec(url)?.[1] ?? '/'
  if (path.split('/').some((segment) => segment === '.' || segment === '..')) return invalid()
  return { url, content_sha256: hash(item.content_sha256) }
}
function evidenceBinding(value: ProviderAccountAttestation): string {
  return canonicalProposal({ ...value, observed_at: semanticUtc(value.observed_at), valid_until: semanticUtc(value.valid_until), evidence: value.evidence.map((entry) => ({ ...entry, url: new URL(entry.url).href })) })
}

export function parseProviderAccountAttestation(value: unknown): ProviderAccountAttestation {
  const item = object(value, ['provider_billing_account_id', 'model_id', 'currency', 'usage_contract', 'observed_at', 'valid_until', 'available_credit_microdollars', 'remaining_provider_allowance_microdollars', 'provider_hard_cap_microdollars', 'evidence'])
  if (item.currency !== 'USD' || item.usage_contract !== 'prompt_completion_inclusive_v1') return invalid()
  const observed = utc(item.observed_at), expires = utc(item.valid_until), available = integer(item.available_credit_microdollars, 0, MAX_MONEY), remaining = integer(item.remaining_provider_allowance_microdollars, 0, MAX_MONEY), cap = integer(item.provider_hard_cap_microdollars, 1, MAX_MONEY), references = list(item.evidence, evidence, 1, 8)
  if (timestamp(observed) >= timestamp(expires) || remaining > cap) return invalid()
  distinct(references.map((entry) => new URL(entry.url).href))
  return { provider_billing_account_id: account(item.provider_billing_account_id), model_id: model(item.model_id), currency: 'USD', usage_contract: 'prompt_completion_inclusive_v1', observed_at: observed, valid_until: expires, available_credit_microdollars: available, remaining_provider_allowance_microdollars: remaining, provider_hard_cap_microdollars: cap, evidence: references }
}

function probeRequest(value: unknown): MetaProbeRequest {
  const item = object(value, ['method', 'endpoint', 'payload']), payload = object(item.payload, ['model', 'messages', 'max_tokens', 'stream'])
  if (item.method !== 'POST' || item.endpoint !== ENDPOINT || payload.stream !== false) return invalid()
  const messages = list(payload.messages, (raw) => { const message = object(raw, ['role', 'content']); return { role: member(message.role, ['system', 'user']), content: text(message.content, 1000) } }, 2, 2)
  if (messages[0].role !== 'system' || messages[0].content !== SYSTEM_MESSAGE || messages[1].role !== 'user' || messages[1].content !== USER_MESSAGE) return invalid()
  return { method: 'POST', endpoint: ENDPOINT, payload: { model: model(payload.model), messages, max_tokens: integer(payload.max_tokens, 16, 4096), stream: false } }
}
export function parseMetaProbeManifest(value: unknown): MetaProbeManifest {
  const item = object(value, ['protocol', 'readiness_id', 'proposal_sha256', 'account_id', 'credential_binding_sha256', 'native_control_epoch', 'clear_kill_switch', 'request', 'request_sha256', 'request_timeout_seconds', 'input_reservation_microdollars', 'output_reservation_microdollars', 'reservation_microdollars', 'provider_account_attestation', 'valid_until'])
  if (item.protocol !== 'polymath-meta-probe-v1' || item.clear_kill_switch !== true || typeof item.request_timeout_seconds !== 'number' || !Number.isFinite(item.request_timeout_seconds) || item.request_timeout_seconds < .001 || item.request_timeout_seconds > 60) return invalid()
  const request = probeRequest(item.request), attestation = parseProviderAccountAttestation(item.provider_account_attestation), expiry = utc(item.valid_until), input = integer(item.input_reservation_microdollars, 1, 150000000), output = integer(item.output_reservation_microdollars, 1, 150000000), reservation = integer(item.reservation_microdollars, 1, 150000000)
  if (input + output !== reservation || request.payload.model !== attestation.model_id || timestamp(expiry) > timestamp(attestation.valid_until) || timestamp(expiry) <= timestamp(attestation.observed_at) || reservation > Math.min(attestation.available_credit_microdollars, attestation.remaining_provider_allowance_microdollars)) return invalid()
  return { protocol: 'polymath-meta-probe-v1', readiness_id: id(item.readiness_id), proposal_sha256: hash(item.proposal_sha256), account_id: account(item.account_id), credential_binding_sha256: hash(item.credential_binding_sha256), native_control_epoch: integer(item.native_control_epoch, 0, Number.MAX_SAFE_INTEGER - 1), clear_kill_switch: true, request, request_sha256: hash(item.request_sha256), request_timeout_seconds: item.request_timeout_seconds, input_reservation_microdollars: input, output_reservation_microdollars: output, reservation_microdollars: reservation, provider_account_attestation: attestation, valid_until: expiry }
}
export function parseMetaProbeCapabilities(value: unknown): MetaProbeCapabilities {
  const item = object(value, ['verification_available', 'inspection_available', 'cancel_available', 'finalization_available', 'research_execution_available', 'reason']), available = bool(item.verification_available), inspection = bool(item.inspection_available), cancel = bool(item.cancel_available), finalization = bool(item.finalization_available)
  if (inspection !== cancel || inspection !== finalization || available && !inspection || (available ? item.reason !== null : item.reason !== 'meta_probe_unavailable')) return invalid()
  return { verification_available: available, inspection_available: inspection, cancel_available: cancel, finalization_available: finalization, research_execution_available: noResearch(item), reason: available ? null : 'meta_probe_unavailable' }
}
export function parseMetaProbePreview(value: unknown): MetaProbePreview { const item = object(value, ['manifest', 'manifest_sha256', 'research_execution_available']); return { manifest: parseMetaProbeManifest(item.manifest), manifest_sha256: hash(item.manifest_sha256), research_execution_available: noResearch(item) } }

function assessment(value: unknown): MetaProbeAssessment {
  const item = object(value, ['status', 'reason_code', 'observation_sha256', 'input_tokens', 'output_tokens', 'actual_input_microdollars', 'actual_output_microdollars', 'actual_microdollars', 'usage_within_bounds', 'freeze_required', 'action_sha256', 'action', 'research_execution_available', 'validation_scope'])
  const status = member(item.status, ['passed', 'failed', 'unknown']), reason = member(item.reason_code, REASONS), input = nullable(item.input_tokens, decimalCounter), output = nullable(item.output_tokens, decimalCounter), actualInput = nullable(item.actual_input_microdollars, (value) => integer(value, 0, MAX_MONEY)), actualOutput = nullable(item.actual_output_microdollars, (value) => integer(value, 0, MAX_MONEY)), actual = nullable(item.actual_microdollars, (value) => integer(value, 0, MAX_MONEY)), within = nullable(item.usage_within_bounds, bool), freeze = bool(item.freeze_required), actionHash = nullable(item.action_sha256, hash)
  const action = nullable(item.action, (raw) => { const action = object(raw, ['kind', 'outcome', 'reason_sha256', 'reason_characters']); if (action.kind !== 'conclude' || action.outcome !== 'insufficient_evidence') return invalid(); return { kind: 'conclude' as const, outcome: 'insufficient_evidence' as const, reason_sha256: hash(action.reason_sha256), reason_characters: integer(action.reason_characters, 1, 3000) } })
  if ((input === null) !== (output === null) || (actualInput === null) !== (actualOutput === null) || (actualInput === null) !== (actual === null) || (actual === null) !== (within === null) || actual !== null && (input === null || BigInt(actualInput!) + BigInt(actualOutput!) !== BigInt(actual))) return invalid()
  if (status === 'passed' ? reason !== 'expected_action_and_reported_usage' || actual === null || within !== true || freeze || action === null || actionHash === null : action !== null || actionHash !== null || reason === 'expected_action_and_reported_usage') return invalid()
  if (status === 'unknown' ? actual !== null || !['observed_model_missing', 'observed_model_mismatch', 'usage_missing_or_invalid', 'counted_cost_unrepresentable'].includes(reason) : actual === null) return invalid()
  if (status === 'failed' && !['billable_bound_exceeded', 'http_not_success', 'finish_not_stop', 'invalid_insufficient_action'].includes(reason) || reason === 'usage_missing_or_invalid' && input !== null || reason === 'counted_cost_unrepresentable' && (input === null || !freeze) || reason === 'observed_model_mismatch' && !freeze) return invalid()
  if (reason === 'billable_bound_exceeded' && (within !== false || !freeze) || status === 'failed' && reason !== 'billable_bound_exceeded' && within !== true || item.validation_scope !== 'one_call_proposed_wire_action_and_reported_usage') return invalid()
  return { status, reason_code: reason, observation_sha256: hash(item.observation_sha256), input_tokens: input, output_tokens: output, actual_input_microdollars: actualInput, actual_output_microdollars: actualOutput, actual_microdollars: actual, usage_within_bounds: within, freeze_required: freeze, action_sha256: actionHash, action, research_execution_available: noResearch(item), validation_scope: 'one_call_proposed_wire_action_and_reported_usage' }
}
export function parseMetaProbeReceipt(value: unknown): MetaProbeReceipt {
  const item = object(value, ['id', 'run_id', 'manifest_sha256', 'observation_sha256', 'native_status', 'bindings', 'observation', 'assessment', 'created_at', 'research_execution_available']), parsed = assessment(item.assessment), bindings = object(item.bindings, ['proposal_sha256', 'request_sha256', 'credential_binding_sha256', 'reservation_id', 'approval_id', 'armed_epoch']), observation = object(item.observation, ['requested_model_id', 'observed_model_id', 'http_status', 'finish_reason', 'body_complete', 'started_at', 'finished_at'])
  const started = utc(observation.started_at), finished = utc(observation.finished_at), observedModel = nullable(observation.observed_model_id, (value) => text(value, 200)), requestedModel = model(observation.requested_model_id), complete = bool(observation.body_complete), http = nullable(observation.http_status, (value) => integer(value, 100, 599)), finish = nullable(observation.finish_reason, (value) => text(value, 80))
  if (item.native_status !== parsed.status || item.observation_sha256 !== parsed.observation_sha256 || timestamp(started) > timestamp(finished) || !complete && (observedModel !== null || parsed.input_tokens !== null) || parsed.actual_microdollars !== null && (!complete || requestedModel !== observedModel)) return invalid()
  if (parsed.status === 'passed' && (http !== 200 || finish !== 'stop') || parsed.reason_code === 'observed_model_missing' && observedModel !== null || parsed.reason_code === 'observed_model_mismatch' && (observedModel === null || observedModel === requestedModel)) return invalid()
  return { id: id(item.id), run_id: id(item.run_id), manifest_sha256: hash(item.manifest_sha256), observation_sha256: hash(item.observation_sha256), native_status: parsed.status, bindings: { proposal_sha256: hash(bindings.proposal_sha256), request_sha256: hash(bindings.request_sha256), credential_binding_sha256: hash(bindings.credential_binding_sha256), reservation_id: id(bindings.reservation_id), approval_id: id(bindings.approval_id), armed_epoch: integer(bindings.armed_epoch, 1) }, observation: { requested_model_id: requestedModel, observed_model_id: observedModel, http_status: http, finish_reason: finish, body_complete: complete, started_at: started, finished_at: finished }, assessment: parsed, created_at: utc(item.created_at), research_execution_available: noResearch(item) }
}
export function parseMetaProbeJob(value: unknown): MetaProbeJob {
  const item = object(value, ['id', 'task_kind', 'status', 'manifest_sha256', 'manifest', 'approval', 'receipt', 'can_cancel', 'can_finalize', 'external_outcome_unknown', 'reason', 'created_at', 'research_execution_available']), manifest = parseMetaProbeManifest(item.manifest), approval = object(item.approval, ['id', 'actor_id', 'approved_at', 'expires_at', 'revoked_at', 'valid']), runId = id(item.id), manifestHash = hash(item.manifest_sha256), approved = utc(approval.approved_at), expires = utc(approval.expires_at), revoked = nullable(approval.revoked_at, utc), valid = bool(approval.valid), receipt = nullable(item.receipt, parseMetaProbeReceipt), canFinalize = bool(item.can_finalize)
  if (item.task_kind !== 'verify_meta' || timestamp(expires) <= timestamp(approved) || timestamp(expires) > timestamp(manifest.valid_until) || valid && revoked !== null || revoked !== null && timestamp(revoked) < timestamp(approved) || canFinalize && receipt !== null) return invalid()
  const approvalId = id(approval.id)
  if (receipt && (receipt.run_id !== runId || receipt.manifest_sha256 !== manifestHash || receipt.bindings.proposal_sha256 !== manifest.proposal_sha256 || receipt.bindings.request_sha256 !== manifest.request_sha256 || receipt.bindings.credential_binding_sha256 !== manifest.credential_binding_sha256 || receipt.bindings.approval_id !== approvalId || receipt.bindings.armed_epoch !== manifest.native_control_epoch + 1 || receipt.observation.requested_model_id !== manifest.request.payload.model)) return invalid()
  const status = member(item.status, JOB_STATUSES), canCancel = bool(item.can_cancel)
  if (canCancel && ['succeeded', 'failed', 'cancelled'].includes(status)) return invalid()
  return { id: runId, task_kind: 'verify_meta', status, manifest_sha256: manifestHash, manifest, approval: { id: approvalId, actor_id: account(approval.actor_id), approved_at: approved, expires_at: expires, revoked_at: revoked, valid }, receipt, can_cancel: canCancel, can_finalize: canFinalize, external_outcome_unknown: bool(item.external_outcome_unknown), reason: nullable(item.reason, code), created_at: utc(item.created_at), research_execution_available: noResearch(item) }
}

async function json(response: Response): Promise<unknown> {
  const size = response.headers.get('content-length')
  if (size !== null && (!/^\d+$/.test(size) || Number(size) > MAX_BYTES)) return invalid()
  const reader = response.body?.getReader(); if (!reader) return invalid()
  const chunks: Uint8Array[] = []; let length = 0
  try { while (true) { const chunk = await reader.read(); if (chunk.done) break; length += chunk.value.byteLength; if (length > MAX_BYTES) { await reader.cancel(); return invalid() }; chunks.push(chunk.value) }; const bytes = new Uint8Array(length); let offset = 0; for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length }; return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)) as unknown } catch { return invalid() } finally { reader.releaseLock() }
}
async function request<T>(path: string, parse: (value: unknown) => T, signal?: AbortSignal, write?: { csrf: string; operatorCsrf: string; body: unknown }, anonymous = false): Promise<T> {
  if (write && !HASH.test(write.csrf)) throw new BudgetError('browser_expired')
  if (write && !HASH.test(write.operatorCsrf)) throw new BudgetError('operator_expired')
  if (signal?.aborted) throw new BudgetError('aborted')
  const controller = new AbortController(), abort = () => controller.abort(); signal?.addEventListener('abort', abort, { once: true }); const timer = window.setTimeout(abort, 10000); let sent = false
  try {
    sent = true
    const response = await fetch(path, { credentials: anonymous ? 'omit' : 'same-origin', cache: 'no-store', redirect: 'error', referrerPolicy: 'no-referrer', signal: controller.signal, method: write ? 'POST' : 'GET', headers: { Accept: 'application/json', ...(write ? { 'X-CSRF-Token': write.csrf, 'X-Operator-CSRF-Token': write.operatorCsrf, 'Content-Type': 'application/json' } : {}) }, ...(write ? { body: JSON.stringify(write.body) } : {}) })
    if (!response.ok) {
      let detail: string | null = null
      try { const value = object(await json(response), ['detail']); detail = typeof value.detail === 'string' ? value.detail : null } catch { /* Never expose untrusted response text. */ }
      if (!anonymous && response.status === 401) throw new BudgetError(detail === 'operator_session_required' ? 'operator_expired' : 'browser_expired')
      if (!anonymous && response.status === 403 && (detail === 'csrf_check_failed' || detail === 'origin_check_failed')) throw new BudgetError('browser_expired')
      if (!anonymous && response.status === 403 && detail === 'operator_csrf_check_failed') throw new BudgetError('operator_expired')
      const known = detail !== null && ERRORS.has(detail) ? detail : response.status === 404 ? 'meta_probe_not_found' : response.status === 422 ? 'meta_probe_invalid_request' : 'unavailable'
      throw new BudgetError(known, !!write && response.status >= 500 && !['operator_unavailable', 'readiness_role_unavailable', 'meta_probe_unavailable'].includes(known))
    }
    const value = await json(response)
    if (controller.signal.aborted) throw new BudgetError(signal?.aborted ? 'aborted' : 'unavailable', !!write && sent)
    return parse(value)
  } catch (caught) { if (caught instanceof BudgetError) { if (write && sent && caught.code === 'invalid_response') throw new BudgetError(caught.code, true); throw caught }; throw new BudgetError(signal?.aborted ? 'aborted' : 'unavailable', !!write && sent) }
  finally { signal?.removeEventListener('abort', abort); window.clearTimeout(timer) }
}
function inputId(value: string): string { if (typeof value !== 'string' || !UUID.test(value)) throw new BudgetError('meta_probe_invalid_request'); return value }
export const loadVerificationCapabilities = (signal?: AbortSignal) => request(`${BASE}/capabilities`, parseMetaProbeCapabilities, signal, undefined, true)
export async function previewMetaVerification(body: MetaProbePreviewRequest, csrf: string, operatorCsrf: string, signal?: AbortSignal): Promise<MetaProbePreview> {
  let exact: MetaProbePreviewRequest
  try { const item = object(body, ['readiness_id', 'provider_account_attestation']); exact = { readiness_id: id(item.readiness_id), provider_account_attestation: parseProviderAccountAttestation(item.provider_account_attestation) } } catch { throw new BudgetError('meta_probe_invalid_request') }
  return request(`${BASE}/preview`, (value) => { const result = parseMetaProbePreview(value); return result.manifest.readiness_id === exact.readiness_id && evidenceBinding(result.manifest.provider_account_attestation) === evidenceBinding(exact.provider_account_attestation) ? result : invalid() }, signal, { csrf, operatorCsrf, body: exact })
}
export async function armMetaVerification(body: ArmMetaProbeRequest, csrf: string, operatorCsrf: string, signal?: AbortSignal): Promise<MetaProbeJob> {
  let exact: ArmMetaProbeRequest
  try { const item = object(body, ['manifest', 'expected_manifest_sha256']); exact = { manifest: parseMetaProbeManifest(item.manifest), expected_manifest_sha256: hash(item.expected_manifest_sha256) } } catch { throw new BudgetError('meta_probe_invalid_request') }
  return request(`${BASE}/jobs`, (value) => { const result = parseMetaProbeJob(value); return result.manifest_sha256 === exact.expected_manifest_sha256 && canonicalProposal(result.manifest) === canonicalProposal(exact.manifest) ? result : invalid() }, signal, { csrf, operatorCsrf, body: exact })
}
export async function listMetaVerificationJobs(signal?: AbortSignal, before?: string): Promise<MetaProbeHistory> {
  const query = new URLSearchParams({ limit: '5' }); if (before !== undefined) query.set('before', inputId(before))
  return request(`${BASE}/jobs?${query}`, (value) => { const item = object(value, ['jobs', 'next_cursor']), jobs = list(item.jobs, parseMetaProbeJob, 0, 5), cursor = nullable(item.next_cursor, id); distinct(jobs.map((job) => job.id)); if (jobs.some((job) => job.id === before) || cursor !== null && (cursor !== jobs.at(-1)?.id || cursor === before)) return invalid(); return { jobs, next_cursor: cursor } }, signal)
}
export async function getMetaVerificationJob(runId: string, signal?: AbortSignal): Promise<MetaProbeJob> { return request(`${BASE}/jobs/${inputId(runId)}`, (value) => { const result = parseMetaProbeJob(value); return result.id === runId ? result : invalid() }, signal) }
export async function cancelMetaVerificationJob(runId: string, csrf: string, operatorCsrf: string, signal?: AbortSignal): Promise<MetaProbeJob> { return request(`${BASE}/jobs/${inputId(runId)}/cancel`, (value) => { const result = parseMetaProbeJob(value); return result.id === runId ? result : invalid() }, signal, { csrf, operatorCsrf, body: {} }) }
export async function finalizeMetaVerificationJob(runId: string, csrf: string, operatorCsrf: string, signal?: AbortSignal): Promise<MetaProbeJob> { return request(`${BASE}/jobs/${inputId(runId)}/finalize`, (value) => { const result = parseMetaProbeJob(value); return result.id === runId ? result : invalid() }, signal, { csrf, operatorCsrf, body: {} }) }
export async function getMetaVerificationReceipt(receiptId: string, signal?: AbortSignal): Promise<MetaProbeReceipt> { return request(`${BASE}/receipts/${inputId(receiptId)}`, (value) => { const result = parseMetaProbeReceipt(value); return result.id === receiptId ? result : invalid() }, signal) }
