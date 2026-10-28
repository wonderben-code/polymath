import { parseOptionalExploreIntent } from '../exploreCollection/parsers'
import { parseOptionalExploreProfile } from '../exploreCollection/profile'
import { BudgetError, canonicalProposal, parseReadinessProposal } from './client'
import { parseProviderAccountAttestation } from './verificationClient'
import { parseDiscoveryIntent } from '../research/discoveryIntent'
import type { ActivateResearchRequest, CurrentResearchActivation, EligibleActivation, MetaResearchActivation, MetaResearchCapabilities, MetaResearchManifest, MetaResearchPreview, NativeResearchReview, NativeScientificContextProfiles, ScientificContextReviewIntent, NativeDiscoveryProfileBinding, NativeDiscoveryProfile, NativeTaskProfile, NativeTaskProfileBinding, PassedCanaryBinding, ResearchActivationHistory, ResearchActivationPreviewRequest } from './activationTypes'
import type { ReadinessEvidenceReference } from './types'

export { BudgetError }
const BASE = '/api/v1/budget/research', HASH = /^[a-f\d]{64}$/, UUID = /^[a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12}$/, MAX_BYTES = 2 * 1024 * 1024
const ENDPOINT = 'https://api.meta.ai/v1/chat/completions', TASKS = ['research', 'formalise', 'write_summary', 'write_paper', 'reassess_finding'] as const
const ERRORS = new Set(['public_research_unavailable', 'public_research_disabled', 'public_research_actor_forbidden', 'public_research_invalid_request', 'public_research_manifest_changed', 'public_research_authority_expired', 'public_research_activation_required', 'public_research_account_conflict', 'public_research_prior_unresolved', 'public_research_stopped', 'public_research_not_found', 'public_research_storage_unavailable', 'operator_unavailable', 'readiness_role_unavailable', 'readiness_actor_forbidden', 'meta_research_unavailable', 'meta_research_actor_forbidden', 'meta_research_not_found', 'meta_research_invalid_request', 'meta_research_manifest_changed', 'meta_research_account_conflict', 'meta_research_prior_unresolved', 'meta_research_authority_expired', 'meta_research_activation_required', 'meta_research_input_changed', 'meta_research_outcome_unknown', 'meta_research_storage_unavailable'])
function invalid(): never { throw new BudgetError('invalid_response') }
function object(value: unknown, required: string[], optional: string[] = []): Record<string, unknown> { if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid(); const item = value as Record<string, unknown>; if (required.some((key) => !Object.hasOwn(item, key)) || Object.keys(item).some((key) => !required.includes(key) && !optional.includes(key))) return invalid(); return item }
function bool(value: unknown): boolean { return typeof value === 'boolean' ? value : invalid() }
function text(value: unknown, maximum: number): string { return typeof value === 'string' && !!value.trim() && Array.from(value).length <= maximum && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\uD800-\uDFFF]/u.test(value) ? value : invalid() }
function integer(value: unknown, minimum = 0, maximum = Number.MAX_SAFE_INTEGER): number { return typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum && value <= maximum ? value : invalid() }
function member<T extends string>(value: unknown, choices: readonly T[]): T { return typeof value === 'string' && choices.includes(value as T) ? value as T : invalid() }
function hash(value: unknown): string { return typeof value === 'string' && HASH.test(value) ? value : invalid() }
function id(value: unknown): string { return typeof value === 'string' && UUID.test(value) ? value : invalid() }
function account(value: unknown): string { return typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$/.test(value) ? value : invalid() }
function model(value: unknown): string { return typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}$/.test(value) ? value : invalid() }
function nullable<T>(value: unknown, parse: (value: unknown) => T): T | null { return value === null ? null : parse(value) }
function list<T>(value: unknown, parse: (value: unknown) => T, minimum = 0, maximum = 5): T[] { return Array.isArray(value) && value.length >= minimum && value.length <= maximum ? value.map(parse) : invalid() }
function distinct<T>(items: T[]): T[] { return new Set(items).size === items.length ? items : invalid() }
function utc(value: unknown): string {
  const parsed = text(value, 40), match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?(?:Z|\+00:00)$/.exec(parsed)
  if (!match || !Number.isFinite(Date.parse(parsed)) || new Date(parsed).toISOString().slice(0, 19) !== match[1]) return invalid()
  return parsed
}
function semanticUtc(value: string): string { const parsed = utc(value), match = /^(.*T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(?:Z|\+00:00)$/.exec(parsed)!, fraction = (match[2] ?? '').replace(/0+$/, ''); return `${match[1]}${fraction ? `.${fraction}` : ''}Z` }
function timestamp(value: string): bigint { const match = /\.(\d+)(?:Z|\+00:00)$/.exec(value); return BigInt(Date.parse(value)) * 1000n + BigInt((match?.[1] ?? '').padEnd(6, '0').slice(3) || '0') }
function evidence(value: unknown): ReadinessEvidenceReference {
  const item = object(value, ['url', 'content_sha256']), url = text(item.url, 2000)
  try { const parsed = new URL(url); if (parsed.protocol !== 'https:' || !parsed.hostname || parsed.username || parsed.password || parsed.port || parsed.search || parsed.hash || /[\s\u0000-\u001f\u007f]/u.test(url)) return invalid() } catch { return invalid() }
  const path = /^https:\/\/[^/]+(\/[^?#]*)?$/.exec(url)?.[1] ?? '/'
  if (path.split('/').some((segment) => segment === '.' || segment === '..')) return invalid()
  return { url, content_sha256: hash(item.content_sha256) }
}
export function parseNativeResearchReview(value: unknown): NativeResearchReview {
  const item = object(value, ['model_wire_usage_confirmed', 'pricing_bounds_confirmed', 'funding_cap_confirmed', 'observed_at', 'valid_until', 'evidence']), observed = utc(item.observed_at), expires = utc(item.valid_until), references = list(item.evidence, evidence, 1, 8)
  if (item.model_wire_usage_confirmed !== true || item.pricing_bounds_confirmed !== true || item.funding_cap_confirmed !== true || timestamp(observed) >= timestamp(expires)) return invalid()
  distinct(references.map((entry) => new URL(entry.url).href))
  return { model_wire_usage_confirmed: true, pricing_bounds_confirmed: true, funding_cap_confirmed: true, observed_at: observed, valid_until: expires, evidence: references }
}
function canary(value: unknown): PassedCanaryBinding {
  const item = object(value, ['receipt_id', 'receipt_sha256', 'account_id', 'model_id', 'credential_binding_sha256', 'endpoint', 'probe_protocol', 'status'])
  if (item.endpoint !== ENDPOINT || item.probe_protocol !== 'polymath-meta-probe-v1' || item.status !== 'passed') return invalid()
  return { receipt_id: id(item.receipt_id), receipt_sha256: hash(item.receipt_sha256), account_id: account(item.account_id), model_id: model(item.model_id), credential_binding_sha256: hash(item.credential_binding_sha256), endpoint: ENDPOINT, probe_protocol: 'polymath-meta-probe-v1', status: 'passed' }
}
export function parseNativeTaskProfile(value: unknown): NativeTaskProfile {
  const item = object(value, ['task_kind', 'controller_protocol', 'controller_sha256', 'request_protocol', 'max_model_calls', 'max_actions', 'max_repairs', 'max_output_tokens', 'max_context_characters', 'request_timeout_seconds', 'model_timeout_seconds', 'tool_timeout_seconds', 'max_sources']), kind = member(item.task_kind, TASKS), timeout = item.request_timeout_seconds
  if (typeof item.controller_protocol !== 'string' || !/^[a-z0-9-]{1,80}$/.test(item.controller_protocol) || item.request_protocol !== 'polymath-meta-research-request-v1' || item.max_actions !== (kind === 'research' ? 16 : 12) || item.max_repairs !== 2 || item.max_sources !== 200 || item.tool_timeout_seconds !== (['research', 'reassess_finding'].includes(kind) ? 20 : 5) || typeof timeout !== 'number' || !Number.isFinite(timeout) || timeout < .001 || timeout > 60 || item.model_timeout_seconds !== timeout + 30) return invalid()
  return { task_kind: kind, controller_protocol: item.controller_protocol, controller_sha256: hash(item.controller_sha256), request_protocol: 'polymath-meta-research-request-v1', max_model_calls: integer(item.max_model_calls, 1, kind === 'research' ? 20 : 12), max_actions: kind === 'research' ? 16 : 12, max_repairs: 2, max_output_tokens: integer(item.max_output_tokens, 16, 4096), max_context_characters: integer(item.max_context_characters, 1000, 90000), request_timeout_seconds: timeout, model_timeout_seconds: timeout + 30, tool_timeout_seconds: ['research', 'reassess_finding'].includes(kind) ? 20 : 5, max_sources: 200 }
}
export function parseNativeDiscoveryProfileBinding(value: unknown): NativeDiscoveryProfileBinding {
  const binding = object(value, ['profile', 'profile_sha256'])
  const item = object(binding.profile, ['task_kind', 'discovery_protocol', 'controller_protocol', 'controller_sha256', 'request_protocol', 'max_model_calls', 'max_actions', 'max_repairs', 'max_output_tokens', 'max_context_characters', 'request_timeout_seconds', 'model_timeout_seconds', 'tool_timeout_seconds', 'max_sources', 'max_stages', 'max_depth'])
  if (item.task_kind !== 'research' || item.discovery_protocol !== 'polymath-surprise-ladder-v1' || item.max_actions !== 20 || item.tool_timeout_seconds !== 20) return invalid()
  const { discovery_protocol: _protocol, max_stages: _stages, max_depth: _depth, ...ordinary } = item
  const base = parseNativeTaskProfile({ ...ordinary, max_actions: 16 })
  const profile: NativeDiscoveryProfile = { ...base, task_kind: 'research', discovery_protocol: 'polymath-surprise-ladder-v1', max_actions: 20, tool_timeout_seconds: 20, max_stages: integer(item.max_stages, 1, 4), max_depth: integer(item.max_depth, 1, 3) }
  return { profile, profile_sha256: hash(binding.profile_sha256) }
}
function profiles<T>(value: unknown, parse: (value: unknown) => T, extract: (item: T) => NativeTaskProfile): T[] {
  const result = list(value, parse, 5, 5), actual = result.map(extract)
  if (actual.some((item, index) => item.task_kind !== TASKS[index])) return invalid()
  const base = actual[0]
  for (const item of actual) if (item.max_output_tokens !== base.max_output_tokens || item.max_context_characters !== base.max_context_characters || item.request_timeout_seconds !== base.request_timeout_seconds) return invalid()
  return result
}
export function parseScientificContextReviewIntent(value: unknown): ScientificContextReviewIntent | undefined {
  if (value === undefined) return undefined
  const item = object(value, ['protocol'])
  if (item.protocol !== 'polymath-scientific-context-profiles-v1') return invalid()
  return { protocol: 'polymath-scientific-context-profiles-v1' }
}
export function parseScientificContextProfiles(value: unknown): NativeScientificContextProfiles {
  const item = object(value, ['protocol', 'bindings'])
  if (item.protocol !== 'polymath-scientific-context-profiles-v1' || !Array.isArray(item.bindings) || item.bindings.length !== 5) return invalid()
  const expected = [
    ['artifact_scientific_context_v1', 'formalise', null, 'polymath-meta-artifact-scientific-context-v1'],
    ['artifact_scientific_context_v1', 'write_summary', null, 'polymath-meta-artifact-scientific-context-v1'],
    ['artifact_scientific_context_v1', 'write_paper', null, 'polymath-meta-artifact-scientific-context-v1'],
    ['ask_claim_overview_v1', 'research', 'ask', 'polymath-meta-ask-claim-overview-v1'],
    ['ask_reassessment_overview_v1', 'reassess_finding', 'ask', 'polymath-meta-ask-reassessment-overview-v1'],
  ] as const
  const bindings = item.bindings.map((raw, ordinal) => {
    const binding = object(raw, ['capability', 'mode', 'profile', 'profile_sha256']), profile = parseNativeTaskProfile(binding.profile), scope = expected[ordinal]
    if (binding.capability !== scope[0] || profile.task_kind !== scope[1] || binding.mode !== scope[2] || profile.controller_protocol !== scope[3]) return invalid()
    return { capability: scope[0], mode: scope[2], profile, profile_sha256: hash(binding.profile_sha256) }
  })
  return { protocol: 'polymath-scientific-context-profiles-v1', bindings }
}
function exploreProfile(value: unknown, ordinary: NativeTaskProfile) { try { return parseOptionalExploreProfile(value, ordinary) } catch { return invalid() } }
export function parseMetaResearchManifest(value: unknown): MetaResearchManifest {
  const item = object(value, ['protocol', 'audience', 'readiness_id', 'proposal_sha256', 'proposal', 'account_id', 'model_id', 'endpoint', 'credential_binding_sha256', 'canary', 'review', 'provider_account_attestation', 'native_control_epoch', 'clear_kill_switch', 'task_profiles', 'valid_until'], ['discovery_ladder_profile', 'scientific_context_profiles', 'explore_collection_profile'])
  if (item.protocol !== 'polymath-meta-research-activation-v1' || item.audience !== 'operator_browser' || item.endpoint !== ENDPOINT || item.clear_kill_switch !== true) return invalid()
  const proposal = parseReadinessProposal(item.proposal), passed = canary(item.canary), review = parseNativeResearchReview(item.review), attestation = parseProviderAccountAttestation(item.provider_account_attestation), expires = utc(item.valid_until), accountId = account(item.account_id), modelId = model(item.model_id), credential = hash(item.credential_binding_sha256)
  const bindings = profiles(item.task_profiles, (raw): NativeTaskProfileBinding => { const binding = object(raw, ['profile_sha256', 'profile']); return { profile_sha256: hash(binding.profile_sha256), profile: parseNativeTaskProfile(binding.profile) } }, (binding) => binding.profile)
  const collection = exploreProfile(item.explore_collection_profile, bindings[0].profile)
  const ladder = item.discovery_ladder_profile === undefined ? undefined : parseNativeDiscoveryProfileBinding(item.discovery_ladder_profile), scientific = item.scientific_context_profiles === undefined ? undefined : parseScientificContextProfiles(item.scientific_context_profiles)
  if (accountId !== proposal.account.account_id || modelId !== proposal.model_id || passed.account_id !== accountId || passed.model_id !== modelId || passed.credential_binding_sha256 !== credential || attestation.model_id !== modelId || attestation.provider_hard_cap_microdollars < proposal.account.hard_cap_microdollars) return invalid()
  for (const deadline of [proposal.valid_until, review.valid_until, attestation.valid_until]) if (timestamp(expires) > timestamp(utc(deadline))) return invalid()
  for (const observed of [proposal.observed_at, review.observed_at, attestation.observed_at]) if (timestamp(expires) <= timestamp(utc(observed))) return invalid()
  const app = proposal.application_limits
  for (const { profile } of [...bindings, ...(ladder ? [ladder] : []), ...(scientific?.bindings ?? []), ...(collection ? [collection] : [])]) if (profile.max_model_calls > app.max_model_calls || profile.max_output_tokens !== app.max_output_tokens || profile.max_context_characters !== app.max_context_characters || profile.request_timeout_seconds !== app.request_timeout_seconds) return invalid()
  const quoted = (BigInt(proposal.billable_bounds.input_tokens_absolute) * BigInt(proposal.pricing.input_microdollars_per_million) + 999999n) / 1000000n + (BigInt(proposal.billable_bounds.output_tokens_absolute) * BigInt(proposal.pricing.output_microdollars_per_million) + 999999n) / 1000000n
  if (BigInt(attestation.available_credit_microdollars) < quoted || BigInt(attestation.remaining_provider_allowance_microdollars) < quoted) return invalid()
  return { protocol: 'polymath-meta-research-activation-v1', audience: 'operator_browser', readiness_id: id(item.readiness_id), proposal_sha256: hash(item.proposal_sha256), proposal, account_id: accountId, model_id: modelId, endpoint: ENDPOINT, credential_binding_sha256: credential, canary: passed, review, provider_account_attestation: attestation, native_control_epoch: integer(item.native_control_epoch, 0, Number.MAX_SAFE_INTEGER - 1), clear_kill_switch: true, task_profiles: bindings, ...(ladder ? { discovery_ladder_profile: ladder } : {}), ...(scientific ? { scientific_context_profiles: scientific } : {}), ...(collection ? { explore_collection_profile: collection } : {}), valid_until: expires }
}
export function parseMetaResearchPreview(value: unknown): MetaResearchPreview {
  const item = object(value, ['manifest', 'manifest_sha256', 'research_execution_available'])
  if (item.research_execution_available !== false) return invalid()
  return { manifest: parseMetaResearchManifest(item.manifest), manifest_sha256: hash(item.manifest_sha256), research_execution_available: false }
}
export function parseMetaResearchActivation(value: unknown): MetaResearchActivation {
  const item = object(value, ['id', 'manifest_sha256', 'manifest', 'approval', 'eligible_for_current_browser', 'research_execution_available', 'created_at', 'audience']), manifest = parseMetaResearchManifest(item.manifest), approval = object(item.approval, ['id', 'actor_id', 'approved_at', 'expires_at', 'revoked_at', 'valid']), approved = utc(approval.approved_at), expires = utc(approval.expires_at), revoked = nullable(approval.revoked_at, utc), valid = bool(approval.valid), eligible = bool(item.eligible_for_current_browser), available = bool(item.research_execution_available)
  if (item.audience !== 'operator_browser' || timestamp(approved) >= timestamp(expires) || timestamp(expires) > timestamp(manifest.valid_until) || valid && revoked !== null || revoked !== null && timestamp(revoked) < timestamp(approved) || available !== eligible || available && !valid) return invalid()
  return { id: id(item.id), manifest_sha256: hash(item.manifest_sha256), manifest, approval: { id: id(approval.id), actor_id: account(approval.actor_id), approved_at: approved, expires_at: expires, revoked_at: revoked, valid }, eligible_for_current_browser: eligible, research_execution_available: available, created_at: utc(item.created_at), audience: 'operator_browser' }
}
export function parseCurrentResearchActivation(value: unknown): CurrentResearchActivation {
  const item = object(value, ['activation', 'reason']), activation = nullable(item.activation, (raw): EligibleActivation => { const current = object(raw, ['id', 'manifest_sha256', 'audience', 'expires_at', 'profiles', 'research_execution_available'], ['discovery_ladder_profile', 'scientific_context_profiles', 'explore_collection_profile']); if (current.audience !== 'operator_browser' || current.research_execution_available !== true) return invalid(); const ordinary = profiles(current.profiles, parseNativeTaskProfile, (profile) => profile), collection = exploreProfile(current.explore_collection_profile, ordinary[0]), ladder = current.discovery_ladder_profile === undefined ? undefined : parseNativeDiscoveryProfileBinding(current.discovery_ladder_profile), scientific = current.scientific_context_profiles === undefined ? undefined : parseScientificContextProfiles(current.scientific_context_profiles); if (scientific?.bindings.some(({ profile }) => profile.max_model_calls > ordinary[0].max_model_calls || profile.max_output_tokens !== ordinary[0].max_output_tokens || profile.max_context_characters !== ordinary[0].max_context_characters || profile.request_timeout_seconds !== ordinary[0].request_timeout_seconds)) return invalid(); if (ladder && (ladder.profile.max_model_calls > ordinary[0].max_model_calls || ladder.profile.max_output_tokens !== ordinary[0].max_output_tokens || ladder.profile.max_context_characters !== ordinary[0].max_context_characters || ladder.profile.request_timeout_seconds !== ordinary[0].request_timeout_seconds)) return invalid(); return { id: id(current.id), manifest_sha256: hash(current.manifest_sha256), audience: 'operator_browser', expires_at: utc(current.expires_at), profiles: ordinary, ...(ladder ? { discovery_ladder_profile: ladder } : {}), ...(scientific ? { scientific_context_profiles: scientific } : {}), ...(collection ? { explore_collection_profile: collection } : {}), research_execution_available: true } })
  const reason = nullable(item.reason, (value) => member(value, ['meta_research_activation_required', 'meta_research_authority_expired', 'meta_research_unavailable', 'meta_research_prior_unresolved'] as const))
  if ((activation === null) === (reason === null)) return invalid()
  return { activation, reason }
}

export function parseMetaResearchCapabilities(value: unknown): MetaResearchCapabilities {
  const item = object(value, ['activation_available', 'inspection_available', 'revoke_available', 'audience', 'public_research_available', 'reason']), available = bool(item.activation_available), inspection = bool(item.inspection_available), revoke = bool(item.revoke_available)
  if (item.audience !== 'operator_browser' || item.public_research_available !== false || inspection !== revoke || available && !inspection || (available ? item.reason !== null : item.reason !== 'meta_research_unavailable')) return invalid()
  return { activation_available: available, inspection_available: inspection, revoke_available: revoke, audience: 'operator_browser', public_research_available: false, reason: available ? null : 'meta_research_unavailable' }
}
async function json(response: Response): Promise<unknown> {
  const size = response.headers.get('content-length')
  if (size !== null && (!/^\d+$/.test(size) || Number(size) > MAX_BYTES)) return invalid()
  const reader = response.body?.getReader(); if (!reader) return invalid()
  const chunks: Uint8Array[] = []; let length = 0
  try { while (true) { const chunk = await reader.read(); if (chunk.done) break; length += chunk.value.byteLength; if (length > MAX_BYTES) { await reader.cancel(); return invalid() }; chunks.push(chunk.value) }; const bytes = new Uint8Array(length); let offset = 0; for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length }; return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)) as unknown } catch { return invalid() } finally { reader.releaseLock() }
}
export async function researchAdminRequest<T>(path: string, parse: (value: unknown) => T, signal?: AbortSignal, write?: { csrf: string; operatorCsrf: string; body: unknown }, anonymous = false): Promise<T> {
  if (write && !HASH.test(write.csrf)) throw new BudgetError('browser_expired')
  if (write && !HASH.test(write.operatorCsrf)) throw new BudgetError('operator_expired')
  if (signal?.aborted) throw new BudgetError('aborted')
  const controller = new AbortController(), abort = () => controller.abort(); signal?.addEventListener('abort', abort, { once: true }); const timer = window.setTimeout(abort, 10000); let sent = false
  try {
    sent = true
    const response = await fetch(path, { credentials: anonymous ? 'omit' : 'same-origin', cache: 'no-store', redirect: 'error', referrerPolicy: 'no-referrer', signal: controller.signal, method: write ? 'POST' : 'GET', headers: { Accept: 'application/json', ...(write ? { 'X-CSRF-Token': write.csrf, 'X-Operator-CSRF-Token': write.operatorCsrf, 'Content-Type': 'application/json' } : {}) }, ...(write ? { body: JSON.stringify(write.body) } : {}) })
    if (!response.ok) {
      let detail: string | null = null
      try { const value = object(await json(response), ['detail']); detail = typeof value.detail === 'string' ? value.detail : null } catch { /* Never expose private or untrusted response text. */ }
      if (!anonymous && response.status === 401) throw new BudgetError(detail === 'operator_session_required' ? 'operator_expired' : 'browser_expired')
      if (!anonymous && response.status === 403 && (detail === 'csrf_check_failed' || detail === 'origin_check_failed')) throw new BudgetError('browser_expired')
      if (!anonymous && response.status === 403 && detail === 'operator_csrf_check_failed') throw new BudgetError('operator_expired')
      const known = detail !== null && ERRORS.has(detail) ? detail : response.status === 404 ? 'meta_research_not_found' : response.status === 422 ? 'meta_research_invalid_request' : 'unavailable'
      throw new BudgetError(known, !!write && response.status >= 500 && !['operator_unavailable', 'readiness_role_unavailable', 'meta_research_unavailable', 'public_research_unavailable', 'public_research_disabled'].includes(known))
    }
    const value = await json(response)
    if (controller.signal.aborted) throw new BudgetError(signal?.aborted ? 'aborted' : 'unavailable', !!write && sent)
    return parse(value)
  } catch (caught) { if (caught instanceof BudgetError) { if (write && sent && caught.code === 'invalid_response') throw new BudgetError(caught.code, true); throw caught }; throw new BudgetError(signal?.aborted ? 'aborted' : 'unavailable', !!write && sent) }
  finally { signal?.removeEventListener('abort', abort); window.clearTimeout(timer) }
}
export const loadResearchCapabilities = (signal?: AbortSignal) => researchAdminRequest(`${BASE}/capabilities`, parseMetaResearchCapabilities, signal, undefined, true)
export const getCurrentResearchActivation = (signal?: AbortSignal) => researchAdminRequest(`${BASE}/current`, parseCurrentResearchActivation, signal)
function inputId(value: string): string { if (typeof value !== 'string' || !UUID.test(value)) throw new BudgetError('meta_research_invalid_request'); return value }
function evidenceBinding<T extends { observed_at: string; valid_until: string; evidence: ReadinessEvidenceReference[] }>(value: T): string { return canonicalProposal({ ...value, observed_at: semanticUtc(value.observed_at), valid_until: semanticUtc(value.valid_until), evidence: value.evidence.map((entry) => ({ ...entry, url: new URL(entry.url).href })) }) }
export async function previewResearchActivation(body: ResearchActivationPreviewRequest, csrf: string, operatorCsrf: string, signal?: AbortSignal): Promise<MetaResearchPreview> {
  let exact: ResearchActivationPreviewRequest
  try { const item = object(body, ['readiness_id', 'canary_receipt_id', 'review', 'provider_account_attestation'], ['discovery', 'scientific_context', 'explore_collection']), collection = parseOptionalExploreIntent(item.explore_collection), discovery = parseDiscoveryIntent(item.discovery, invalid), scientific = parseScientificContextReviewIntent(item.scientific_context); exact = { readiness_id: id(item.readiness_id), canary_receipt_id: id(item.canary_receipt_id), review: parseNativeResearchReview(item.review), provider_account_attestation: parseProviderAccountAttestation(item.provider_account_attestation), ...(discovery ? { discovery } : {}), ...(scientific ? { scientific_context: scientific } : {}), ...(collection ? { explore_collection: collection } : {}) } } catch { throw new BudgetError('meta_research_invalid_request') }
  return researchAdminRequest(`${BASE}/preview`, (value) => { const result = parseMetaResearchPreview(value); return result.manifest.readiness_id === exact.readiness_id && result.manifest.canary.receipt_id === exact.canary_receipt_id && evidenceBinding(result.manifest.review) === evidenceBinding(exact.review) && evidenceBinding(result.manifest.provider_account_attestation) === evidenceBinding(exact.provider_account_attestation) && (exact.discovery ? result.manifest.discovery_ladder_profile?.profile.max_stages === exact.discovery.max_stages && result.manifest.discovery_ladder_profile.profile.max_depth === exact.discovery.max_depth : result.manifest.discovery_ladder_profile === undefined) && (exact.explore_collection ? result.manifest.explore_collection_profile?.profile.max_bridges === exact.explore_collection.max_bridges : result.manifest.explore_collection_profile === undefined) && (exact.scientific_context ? result.manifest.scientific_context_profiles !== undefined : result.manifest.scientific_context_profiles === undefined) ? result : invalid() }, signal, { csrf, operatorCsrf, body: exact })
}
export async function activateResearch(body: ActivateResearchRequest, csrf: string, operatorCsrf: string, signal?: AbortSignal): Promise<MetaResearchActivation> {
  let exact: ActivateResearchRequest
  try { const item = object(body, ['manifest', 'expected_manifest_sha256']); exact = { manifest: parseMetaResearchManifest(item.manifest), expected_manifest_sha256: hash(item.expected_manifest_sha256) } } catch { throw new BudgetError('meta_research_invalid_request') }
  return researchAdminRequest(`${BASE}/activations`, (value) => { const result = parseMetaResearchActivation(value); return result.manifest_sha256 === exact.expected_manifest_sha256 && canonicalProposal(result.manifest) === canonicalProposal(exact.manifest) ? result : invalid() }, signal, { csrf, operatorCsrf, body: exact })
}
export async function listResearchActivations(signal?: AbortSignal, before?: string): Promise<ResearchActivationHistory> {
  const query = new URLSearchParams({ limit: '5' }); if (before !== undefined) query.set('before', inputId(before))
  return researchAdminRequest(`${BASE}/activations?${query}`, (value) => { const item = object(value, ['activations', 'next_cursor']), activations = list(item.activations, parseMetaResearchActivation), cursor = nullable(item.next_cursor, id); distinct(activations.map((activation) => activation.id)); if (activations.some((activation) => activation.id === before) || cursor !== null && (cursor !== activations.at(-1)?.id || cursor === before)) return invalid(); return { activations, next_cursor: cursor } }, signal)
}
export async function getResearchActivation(activationId: string, signal?: AbortSignal): Promise<MetaResearchActivation> { return researchAdminRequest(`${BASE}/activations/${inputId(activationId)}`, (value) => { const result = parseMetaResearchActivation(value); return result.id === activationId ? result : invalid() }, signal) }
export async function revokeResearchActivation(activationId: string, csrf: string, operatorCsrf: string, signal?: AbortSignal): Promise<MetaResearchActivation> { return researchAdminRequest(`${BASE}/activations/${inputId(activationId)}/revoke`, (value) => { const result = parseMetaResearchActivation(value); return result.id === activationId && result.approval.revoked_at !== null && !result.approval.valid && !result.research_execution_available ? result : invalid() }, signal, { csrf, operatorCsrf, body: {} }) }
