export interface MetaResearchCapabilities {
  activation_available: boolean
  inspection_available: boolean
  revoke_available: boolean
  audience: 'operator_browser'
  public_research_available: false
  reason: 'meta_research_unavailable' | null
}
import type { MetaReadinessProposal, ReadinessEvidenceReference } from './types'
import type { ProviderAccountAttestation } from './verificationTypes'


export type NativeScientificTaskKind = 'research' | 'formalise' | 'write_summary' | 'write_paper' | 'reassess_finding'
export interface NativeResearchReview {
  model_wire_usage_confirmed: true
  pricing_bounds_confirmed: true
  funding_cap_confirmed: true
  observed_at: string
  valid_until: string
  evidence: ReadinessEvidenceReference[]
}
export interface PassedCanaryBinding {
  receipt_id: string
  receipt_sha256: string
  account_id: string
  model_id: string
  credential_binding_sha256: string
  endpoint: 'https://api.meta.ai/v1/chat/completions'
  probe_protocol: 'polymath-meta-probe-v1'
  status: 'passed'
}
export interface NativeTaskProfile {
  task_kind: NativeScientificTaskKind
  controller_protocol: string
  controller_sha256: string
  request_protocol: 'polymath-meta-research-request-v1'
  max_model_calls: number
  max_actions: number
  max_repairs: number
  max_output_tokens: number
  max_context_characters: number
  request_timeout_seconds: number
  model_timeout_seconds: number
  tool_timeout_seconds: number
  max_sources: 200
}
export interface NativeTaskProfileBinding { profile_sha256: string; profile: NativeTaskProfile }
export interface MetaResearchManifest {
  protocol: 'polymath-meta-research-activation-v1'
  audience: 'operator_browser'
  readiness_id: string
  proposal_sha256: string
  proposal: MetaReadinessProposal
  account_id: string
  model_id: string
  endpoint: 'https://api.meta.ai/v1/chat/completions'
  credential_binding_sha256: string
  canary: PassedCanaryBinding
  review: NativeResearchReview
  provider_account_attestation: ProviderAccountAttestation
  native_control_epoch: number
  clear_kill_switch: true
  task_profiles: NativeTaskProfileBinding[]
  valid_until: string
}
export interface MetaResearchPreview { manifest: MetaResearchManifest; manifest_sha256: string; research_execution_available: false }
export interface MetaResearchApproval { id: string; actor_id: string; approved_at: string; expires_at: string; revoked_at: string | null; valid: boolean }
export interface MetaResearchActivation {
  id: string
  manifest_sha256: string
  manifest: MetaResearchManifest
  approval: MetaResearchApproval
  eligible_for_current_browser: boolean
  research_execution_available: boolean
  created_at: string
  audience: 'operator_browser'
}
export interface EligibleActivation {
  id: string
  manifest_sha256: string
  audience: 'operator_browser'
  expires_at: string
  profiles: NativeTaskProfile[]
  research_execution_available: true
}
export interface CurrentResearchActivation { activation: EligibleActivation | null; reason: 'meta_research_activation_required' | 'meta_research_authority_expired' | 'meta_research_unavailable' | 'meta_research_prior_unresolved' | null }
export interface ResearchActivationHistory { activations: MetaResearchActivation[]; next_cursor: string | null }
export interface ResearchActivationPreviewRequest { readiness_id: string; canary_receipt_id: string; review: NativeResearchReview; provider_account_attestation: ProviderAccountAttestation }
export interface ActivateResearchRequest { manifest: MetaResearchManifest; expected_manifest_sha256: string }
