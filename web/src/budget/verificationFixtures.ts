import { canonicalProposal } from './client'
import { ACCOUNT_ID, DATE, RECORD_ID, proposal, sha256 } from './test-support'
import type { ArmMetaProbeRequest, MetaProbeCapabilities, MetaProbeHistory, MetaProbeJob, MetaProbeManifest, MetaProbePreview, MetaProbePreviewRequest, MetaProbeReceipt, ProviderAccountAttestation } from './verificationTypes'

export { BROWSER_CSRF, OPERATOR_CSRF } from './test-support'
export const READINESS_ID = RECORD_ID
export const JOB_ID = '22222222-2222-4222-8222-222222222222'
export const APPROVAL_ID = '33333333-3333-4333-8333-333333333333'
export const RECEIPT_ID = '44444444-4444-4444-8444-444444444444'
export const RESERVATION_ID = '55555555-5555-4555-8555-555555555555'
export const PROBE_EXPIRY = '2026-10-28T00:05:00Z'
export const OBSERVED_AT = '2026-10-28T00:00:01Z'
export const FINISHED_AT = '2026-10-28T00:00:02Z'

/** Synthetic protocol fixtures only. They certify no provider or billing facts. */
export function attestation(overrides: Partial<ProviderAccountAttestation> = {}): ProviderAccountAttestation {
  return { provider_billing_account_id: 'synthetic-provider-account', model_id: proposal().model_id, currency: 'USD', usage_contract: 'prompt_completion_inclusive_v1', observed_at: DATE, valid_until: '2026-10-29T00:00:00Z', available_credit_microdollars: 150000000, remaining_provider_allowance_microdollars: 150000000, provider_hard_cap_microdollars: 150000000, evidence: [{ url: 'https://fixtures.example.test/provider-account', content_sha256: sha256('Synthetic account statement; not verified provider facts.') }], ...overrides }
}
export function previewRequest(overrides: Partial<MetaProbePreviewRequest> = {}): MetaProbePreviewRequest { return { readiness_id: READINESS_ID, provider_account_attestation: attestation(), ...overrides } }
export function manifest(overrides: Partial<MetaProbeManifest> = {}): MetaProbeManifest {
  const ready = proposal(); ready.application_limits.request_timeout_seconds = 5.5
  const request: MetaProbeManifest['request'] = { method: 'POST', endpoint: 'https://api.meta.ai/v1/chat/completions', payload: { model: ready.model_id, messages: [{ role: 'system', content: 'Perform one isolated JSON protocol check. Do not search, use tools, cite sources, or make scientific claims. Return only one JSON action.' }, { role: 'user', content: 'Return exactly this JSON action: {"kind":"conclude","conclusion":{"outcome":"insufficient_evidence","reason":"Protocol check only."}}' }], max_tokens: ready.application_limits.max_output_tokens, stream: false } }
  return { protocol: 'polymath-meta-probe-v1', readiness_id: READINESS_ID, proposal_sha256: sha256(canonicalProposal(ready)), account_id: ACCOUNT_ID, credential_binding_sha256: sha256('Synthetic one-way credential binding; no real credential.'), native_control_epoch: 0, clear_kill_switch: true, request, request_sha256: sha256(canonicalProposal(request)), request_timeout_seconds: ready.application_limits.request_timeout_seconds, input_reservation_microdollars: 1, output_reservation_microdollars: 1, reservation_microdollars: 2, provider_account_attestation: attestation(), valid_until: PROBE_EXPIRY, ...overrides }
}
export function verificationPreview(content = manifest()): MetaProbePreview { return { manifest: content, manifest_sha256: sha256(canonicalProposal(content)), research_execution_available: false } }
export function armRequest(content = manifest()): ArmMetaProbeRequest { return { manifest: content, expected_manifest_sha256: verificationPreview(content).manifest_sha256 } }
export function verificationCapabilities(mode: 'off' | 'inspect' | 'verify' = 'verify'): MetaProbeCapabilities { return { verification_available: mode === 'verify', inspection_available: mode !== 'off', cancel_available: mode !== 'off', finalization_available: mode !== 'off', reason: mode === 'verify' ? null : 'meta_probe_unavailable', research_execution_available: false } }
export function receipt(content = manifest(), overrides: Partial<MetaProbeReceipt> = {}): MetaProbeReceipt {
  const reason = 'Protocol check only.', action = { kind: 'conclude', conclusion: { outcome: 'insufficient_evidence', reason } }, observationHash = sha256('Synthetic complete protocol observation')
  return { id: RECEIPT_ID, run_id: JOB_ID, manifest_sha256: verificationPreview(content).manifest_sha256, observation_sha256: observationHash, native_status: 'passed', bindings: { proposal_sha256: content.proposal_sha256, request_sha256: content.request_sha256, credential_binding_sha256: content.credential_binding_sha256, reservation_id: RESERVATION_ID, approval_id: APPROVAL_ID, armed_epoch: content.native_control_epoch + 1 }, observation: { requested_model_id: content.request.payload.model, observed_model_id: content.request.payload.model, http_status: 200, finish_reason: 'stop', body_complete: true, started_at: OBSERVED_AT, finished_at: FINISHED_AT }, assessment: { status: 'passed', reason_code: 'expected_action_and_reported_usage', observation_sha256: observationHash, input_tokens: '2', output_tokens: '3', actual_input_microdollars: 1, actual_output_microdollars: 1, actual_microdollars: 2, usage_within_bounds: true, freeze_required: false, action_sha256: sha256(canonicalProposal(action)), action: { kind: 'conclude', outcome: 'insufficient_evidence', reason_sha256: sha256(reason), reason_characters: Array.from(reason).length }, research_execution_available: false, validation_scope: 'one_call_proposed_wire_action_and_reported_usage' }, created_at: FINISHED_AT, research_execution_available: false, ...overrides }
}
export function unknownReceipt(content = manifest()): MetaProbeReceipt {
  const value = receipt(content)
  return { ...value, native_status: 'unknown', observation: { ...value.observation, observed_model_id: null, http_status: null, finish_reason: null, body_complete: false }, assessment: { ...value.assessment, status: 'unknown', reason_code: 'observed_model_missing', input_tokens: null, output_tokens: null, actual_input_microdollars: null, actual_output_microdollars: null, actual_microdollars: null, usage_within_bounds: null, action_sha256: null, action: null } }
}
export function verificationJob(overrides: Partial<MetaProbeJob> = {}, content = manifest()): MetaProbeJob { return { id: JOB_ID, task_kind: 'verify_meta', status: 'queued', manifest_sha256: verificationPreview(content).manifest_sha256, manifest: content, approval: { id: APPROVAL_ID, actor_id: 'synthetic.budget.admin', approved_at: DATE, expires_at: PROBE_EXPIRY, revoked_at: null, valid: true }, receipt: null, can_cancel: true, can_finalize: false, external_outcome_unknown: false, reason: null, created_at: DATE, research_execution_available: false, ...overrides } }
export function completedJob(): MetaProbeJob { return verificationJob({ status: 'succeeded', receipt: receipt(), can_cancel: false, approval: { ...verificationJob().approval, valid: false } }) }
export function verificationHistory(jobs = [verificationJob()], next_cursor: string | null = null): MetaProbeHistory { return { jobs, next_cursor } }
