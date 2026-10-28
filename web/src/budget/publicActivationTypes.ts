import type { MetaResearchManifest, MetaResearchApproval, ResearchActivationPreviewRequest } from './activationTypes'
export const PUBLIC_RESOURCE_CEILINGS = {
  submit_session_per_minute: 6, submit_network_per_minute: 10, submit_service_per_minute: 30,
  mint_network_per_hour: 10, mint_network_per_day: 50, mint_service_per_hour: 200, mint_service_per_day: 1000,
  read_session_per_minute: 60, read_network_per_minute: 240, read_service_per_minute: 600,
  sse_session_connections: 2, sse_network_connections: 4, sse_service_connections: 20, sse_max_seconds: 300,
  free_write_session_per_minute: 3, free_write_network_per_minute: 6, free_write_service_per_minute: 20,
  control_session_per_minute: 30, control_network_per_minute: 60, control_service_per_minute: 120, body_max_bytes: 65536,
} as const
export type PublicResourceLimits = { [K in keyof typeof PUBLIC_RESOURCE_CEILINGS]: number }
export interface PublicLimits {
  public_total_cap_microdollars: number
  public_daily_cap_microdollars: number
  session_total_cap_microdollars: number
  session_daily_cap_microdollars: number
  max_jobs_per_session_day: number
  max_jobs_per_day: number
  max_active_jobs_per_session: 1
  max_active_jobs: number
  max_queued_jobs: number
  max_active_jobs_per_network: number
  resources: PublicResourceLimits
}
export interface PublicResearchManifest extends Omit<MetaResearchManifest, 'protocol' | 'audience'> {
  protocol: 'polymath-meta-public-research-v1'
  audience: 'public_sessions'
  authorization_mode: 'durable_owner_delegation'
  continues_after_operator_logout: true
  public_limits: PublicLimits
  public_policy_sha256: string
  sponsor_policy_sha256: string
}
export interface PublicResearchPreview { manifest: PublicResearchManifest; manifest_sha256: string; research_execution_available: false }
export interface PublicResearchActivation {
  id: string
  manifest_sha256: string
  manifest: PublicResearchManifest
  audience: 'public_sessions'
  authorization_mode: 'durable_owner_delegation'
  continues_after_operator_logout: true
  approval: MetaResearchApproval
  delegation_current: boolean
  created_at: string
}
export interface PublicActivationHistory { activations: PublicResearchActivation[]; next_cursor: string | null }
export interface PublicActivationPreviewRequest extends ResearchActivationPreviewRequest { public_limits: PublicLimits; valid_until: string }
export interface ActivatePublicResearchRequest { manifest: PublicResearchManifest; expected_manifest_sha256: string }
export interface PublicResearchCapabilities { activation_available: boolean; inspection_available: boolean; revoke_available: boolean; audience: 'public_sessions'; reason: 'public_research_unavailable' | null }
