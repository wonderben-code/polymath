export interface ReadinessEvidenceReference { url: string; content_sha256: string }
export interface MetaReadinessProposal {
  protocol: 'polymath-meta-readiness-v1'
  model_id: string
  endpoint: 'https://api.meta.ai/v1/chat/completions'
  currency: 'USD'
  usage_contract: 'prompt_completion_inclusive_v1'
  observed_at: string
  valid_until: string
  billable_bounds: { input_tokens_absolute: number; output_tokens_absolute: number; evidence: ReadinessEvidenceReference[] }
  pricing: { input_microdollars_per_million: number; output_microdollars_per_million: number; evidence: ReadinessEvidenceReference[] }
  usage_evidence: ReadinessEvidenceReference[]
  application_limits: { max_output_tokens: number; max_context_characters: number; max_model_calls: number; request_timeout_seconds: number }
  account: { account_id: string; hard_cap_microdollars: number; daily_cap_microdollars: number; run_cap_microdollars: number; max_in_flight: number }
  native_status: 'not_run'
  execution_available: false
}
export interface ReadinessPreview {
  proposal_sha256: string; proposal: MetaReadinessProposal
  input_reservation_microdollars: number; output_reservation_microdollars: number
  per_call_reservation_microdollars: number; max_run_reservation_microdollars: number
  full_run_reservation_fits: boolean; budget_limited_stop_notice: string | null
  native_status: 'not_run'; execution_available: false
}
export interface ReadinessRecord { id: string; account_id: string; proposal_sha256: string; proposal: MetaReadinessProposal; quote: ReadinessPreview; actor_id: string; created_at: string; temporal_status: 'current' | 'expired' | 'not_yet_current'; native_status: 'not_run'; execution_available: false }
export interface SaveReadinessProposal { proposal: MetaReadinessProposal; expected_proposal_sha256: string }
export interface ReadinessHistory { proposals: ReadinessRecord[]; next_cursor: string | null }
export interface BudgetAccount {
  account_id: string; hard_cap_microdollars: number; daily_cap_microdollars: number; max_in_flight: number
  execution_enabled: boolean; pricing_verified: boolean; pricing_reference: string | null
  kill_switch: boolean; frozen: boolean; frozen_reason: string | null; created_at: string
  usage: { committed_microdollars: string; daily_committed_microdollars: string; counted_spend_microdollars: string; open_hold_microdollars: string; unknown_hold_microdollars: string; in_flight: number; reservations: number }
  native_status: 'not_run'; execution_available: false
}
export interface BudgetAccountHistory { accounts: BudgetAccount[]; next_cursor: string | null }
export interface BudgetCapabilities { preparation_available: boolean; inspection_available: boolean; disable_available: boolean; native_status: 'not_run'; execution_available: false; reason: 'readiness_unavailable' | 'preparation_disabled' | null }
