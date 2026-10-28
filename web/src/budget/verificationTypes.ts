import type { ReadinessEvidenceReference } from './types'

/** Human account-scope statements; these are never native billing verification. */
export interface ProviderAccountAttestation {
  provider_billing_account_id: string
  model_id: string
  currency: 'USD'
  usage_contract: 'prompt_completion_inclusive_v1'
  observed_at: string
  valid_until: string
  available_credit_microdollars: number
  remaining_provider_allowance_microdollars: number
  provider_hard_cap_microdollars: number
  evidence: ReadinessEvidenceReference[]
}

export interface MetaProbePreviewRequest {
  readiness_id: string
  provider_account_attestation: ProviderAccountAttestation
}

export interface MetaProbeCapabilities {
  verification_available: boolean
  inspection_available: boolean
  cancel_available: boolean
  finalization_available: boolean
  research_execution_available: false
  reason: 'meta_probe_unavailable' | null
}

export interface MetaProbeRequest {
  method: 'POST'
  endpoint: 'https://api.meta.ai/v1/chat/completions'
  payload: {
    model: string
    messages: { role: 'system' | 'user'; content: string }[]
    max_tokens: number
    stream: false
  }
}

export interface MetaProbeManifest {
  protocol: 'polymath-meta-probe-v1'
  readiness_id: string
  proposal_sha256: string
  account_id: string
  credential_binding_sha256: string
  native_control_epoch: number
  clear_kill_switch: true
  request: MetaProbeRequest
  request_sha256: string
  request_timeout_seconds: number
  input_reservation_microdollars: number
  output_reservation_microdollars: number
  reservation_microdollars: number
  provider_account_attestation: ProviderAccountAttestation
  valid_until: string
}

export interface MetaProbePreview { manifest: MetaProbeManifest; manifest_sha256: string; research_execution_available: false }
export interface ArmMetaProbeRequest { manifest: MetaProbeManifest; expected_manifest_sha256: string }

export interface MetaProbeAssessment {
  status: 'passed' | 'failed' | 'unknown'
  reason_code: string
  observation_sha256: string
  input_tokens: string | null
  output_tokens: string | null
  actual_input_microdollars: number | null
  actual_output_microdollars: number | null
  actual_microdollars: number | null
  usage_within_bounds: boolean | null
  freeze_required: boolean
  action_sha256: string | null
  action: { kind: 'conclude'; outcome: 'insufficient_evidence'; reason_sha256: string; reason_characters: number } | null
  research_execution_available: false
  validation_scope: 'one_call_proposed_wire_action_and_reported_usage'
}

export interface MetaProbeReceipt {
  id: string
  run_id: string
  manifest_sha256: string
  observation_sha256: string
  native_status: 'passed' | 'failed' | 'unknown'
  bindings: {
    proposal_sha256: string
    request_sha256: string
    credential_binding_sha256: string
    reservation_id: string
    approval_id: string
    armed_epoch: number
  }
  observation: {
    requested_model_id: string
    observed_model_id: string | null
    http_status: number | null
    finish_reason: string | null
    body_complete: boolean
    started_at: string
    finished_at: string
  }
  assessment: MetaProbeAssessment
  created_at: string
  research_execution_available: false
}

export interface MetaProbeApproval {
  id: string
  actor_id: string
  approved_at: string
  expires_at: string
  revoked_at: string | null
  valid: boolean
}

export interface MetaProbeJob {
  id: string
  task_kind: 'verify_meta'
  status: 'queued' | 'running' | 'waiting_retry' | 'cancel_requested' | 'cancelled' | 'succeeded' | 'failed' | 'interrupted'
  manifest_sha256: string
  manifest: MetaProbeManifest
  approval: MetaProbeApproval
  receipt: MetaProbeReceipt | null
  can_cancel: boolean
  can_finalize: boolean
  external_outcome_unknown: boolean
  reason: string | null
  created_at: string
  research_execution_available: false
}

export interface MetaProbeHistory { jobs: MetaProbeJob[]; next_cursor: string | null }
