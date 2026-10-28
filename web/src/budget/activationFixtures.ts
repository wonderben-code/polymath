import { canonicalProposal } from './client'
import { ACCOUNT_ID, DATE, proposal, sha256 } from './test-support'
import { READINESS_ID, RECEIPT_ID, attestation, manifest as probeManifest } from './verificationFixtures'
import type { MetaReadinessProposal } from './types'
import type { ActivateResearchRequest, CurrentResearchActivation, MetaResearchActivation, MetaResearchCapabilities, MetaResearchManifest, MetaResearchPreview, NativeResearchReview, NativeScientificTaskKind, NativeTaskProfile, ResearchActivationHistory, ResearchActivationPreviewRequest } from './activationTypes'

export { BROWSER_CSRF, OPERATOR_CSRF } from './test-support'
export { READINESS_ID, RECEIPT_ID } from './verificationFixtures'
export const ACTIVATION_ID = '66666666-6666-4666-8666-666666666666', ACTIVATION_APPROVAL_ID = '77777777-7777-4777-8777-777777777777'
export const ACTIVATION_EXPIRY = '2026-10-28T00:05:00Z'
const TASKS: NativeScientificTaskKind[] = ['research', 'formalise', 'write_summary', 'write_paper', 'reassess_finding']

/** Match the server's canonical integer/float distinction for these synthetic profiles. */
function canonicalWire(value: unknown, key = ''): string {
  if (Array.isArray(value)) return `[${value.map((item) => canonicalWire(item)).join(',')}]`
  if (value && typeof value === 'object') return `{${Object.entries(value).sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0).map(([name, item]) => `${JSON.stringify(name)}:${canonicalWire(item, name)}`).join(',')}}`
  return typeof value === 'number' && key.endsWith('_seconds') && Number.isInteger(value) ? value.toFixed(1) : JSON.stringify(value)
}
/** No provider, key, prices, funding, or authority in these fixtures is real. */
export function activationProposal(): MetaReadinessProposal {
  const value = proposal(); value.billable_bounds.output_tokens_absolute = 4096
  value.application_limits = { max_output_tokens: 4096, max_context_characters: 90000, max_model_calls: 20, request_timeout_seconds: 45.5 }
  return value
}
export function researchReview(overrides: Partial<NativeResearchReview> = {}): NativeResearchReview {
  return { model_wire_usage_confirmed: true, pricing_bounds_confirmed: true, funding_cap_confirmed: true, observed_at: DATE, valid_until: '2026-10-29T00:00:00Z', evidence: [{ url: 'https://fixtures.example.test/fresh-native-review', content_sha256: sha256('Synthetic fresh documentary review; not independently verified.') }], ...overrides }
}
export function activationPreviewRequest(overrides: Partial<ResearchActivationPreviewRequest> = {}): ResearchActivationPreviewRequest { return { readiness_id: READINESS_ID, canary_receipt_id: RECEIPT_ID, review: researchReview(), provider_account_attestation: attestation(), ...overrides } }
export function taskProfile(kind: NativeScientificTaskKind = 'research', content = activationProposal()): NativeTaskProfile {
  return { task_kind: kind, controller_protocol: kind === 'research' ? 'polymath-meta-research-controller-v1' : kind === 'reassess_finding' ? 'polymath-meta-assessment-controller-v1' : 'polymath-meta-artifact-controller-v1', controller_sha256: sha256(`Synthetic ${kind} controller semantics`), request_protocol: 'polymath-meta-research-request-v1', max_model_calls: Math.min(content.application_limits.max_model_calls, kind === 'research' ? 20 : 12), max_actions: kind === 'research' ? 16 : 12, max_repairs: 2, max_output_tokens: content.application_limits.max_output_tokens, max_context_characters: content.application_limits.max_context_characters, request_timeout_seconds: content.application_limits.request_timeout_seconds, model_timeout_seconds: content.application_limits.request_timeout_seconds + 30, tool_timeout_seconds: ['research', 'reassess_finding'].includes(kind) ? 20 : 5, max_sources: 200 }
}
export function researchManifest(overrides: Partial<MetaResearchManifest> = {}, content = activationProposal()): MetaResearchManifest {
  const credential = probeManifest().credential_binding_sha256
  return { protocol: 'polymath-meta-research-activation-v1', audience: 'operator_browser', readiness_id: READINESS_ID, proposal_sha256: sha256(canonicalProposal(content)), proposal: content, account_id: ACCOUNT_ID, model_id: content.model_id, endpoint: 'https://api.meta.ai/v1/chat/completions', credential_binding_sha256: credential, canary: { receipt_id: RECEIPT_ID, receipt_sha256: sha256('Synthetic saved passed canary payload'), account_id: ACCOUNT_ID, model_id: content.model_id, credential_binding_sha256: credential, endpoint: 'https://api.meta.ai/v1/chat/completions', probe_protocol: 'polymath-meta-probe-v1', status: 'passed' }, review: researchReview(), provider_account_attestation: attestation({ model_id: content.model_id }), native_control_epoch: 1, clear_kill_switch: true, task_profiles: TASKS.map((kind) => { const profile = taskProfile(kind, content); return { profile_sha256: sha256(canonicalWire(profile)), profile } }), valid_until: ACTIVATION_EXPIRY, ...overrides }
}
export function activationPreview(content = researchManifest()): MetaResearchPreview { return { manifest: content, manifest_sha256: sha256(canonicalWire(content)), research_execution_available: false } }
export function activationRequest(content = researchManifest()): ActivateResearchRequest { return { manifest: content, expected_manifest_sha256: activationPreview(content).manifest_sha256 } }
export function researchActivation(overrides: Partial<MetaResearchActivation> = {}, content = researchManifest()): MetaResearchActivation { return { id: ACTIVATION_ID, manifest_sha256: activationPreview(content).manifest_sha256, manifest: content, approval: { id: ACTIVATION_APPROVAL_ID, actor_id: 'synthetic.budget.admin', approved_at: DATE, expires_at: ACTIVATION_EXPIRY, revoked_at: null, valid: true }, eligible_for_current_browser: true, research_execution_available: true, created_at: DATE, audience: 'operator_browser', ...overrides } }
export function activationHistory(activations = [researchActivation()], next_cursor: string | null = null): ResearchActivationHistory { return { activations, next_cursor } }
export function currentActivation(record: MetaResearchActivation | null = researchActivation(), reason: CurrentResearchActivation['reason'] = 'meta_research_activation_required'): CurrentResearchActivation { return record ? { activation: { id: record.id, manifest_sha256: record.manifest_sha256, audience: 'operator_browser', expires_at: record.approval.expires_at, profiles: record.manifest.task_profiles.map((binding) => binding.profile), research_execution_available: true }, reason: null } : { activation: null, reason } }
export function activationCapabilities(mode: 'off' | 'inspect' | 'activate' = 'activate'): MetaResearchCapabilities { return { activation_available: mode === 'activate', inspection_available: mode !== 'off', revoke_available: mode !== 'off', audience: 'operator_browser', public_research_available: false, reason: mode === 'activate' ? null : 'meta_research_unavailable' } }
