/** Ephemeral admission: public visitors never carry an operator credential. */
export type NativeSubmissionContext = { audience?: 'operator_browser'; operatorCsrfToken: string; expectedActivationSha256: string } | { audience: 'public_sessions'; expectedActivationSha256: string; operatorCsrfToken?: never }
export interface NativeRunControl { activation_sha256: string; expected_lease_token: string; can_resume: boolean }

export function parseNativeRunControl(value: unknown, invalid: () => never): NativeRunControl | undefined {
  if (value === undefined) return undefined
  if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid()
  const item = value as Record<string, unknown>
  if (Object.keys(item).length !== 3 || typeof item.activation_sha256 !== 'string' || !/^[a-f\d]{64}$/.test(item.activation_sha256) || typeof item.expected_lease_token !== 'string' || !/^(?:0|[1-9]\d{0,18})$/.test(item.expected_lease_token) || BigInt(item.expected_lease_token) > 9223372036854775807n || typeof item.can_resume !== 'boolean') return invalid()
  return { activation_sha256: item.activation_sha256, expected_lease_token: item.expected_lease_token, can_resume: item.can_resume }
}

export function validNativeSubmission(value: NativeSubmissionContext): boolean {
  if (!value || typeof value.expectedActivationSha256 !== 'string' || !/^[a-f\d]{64}$/.test(value.expectedActivationSha256)) return false
  return value.audience === 'public_sessions' ? !Object.hasOwn(value, 'operatorCsrfToken') : (value.audience === undefined || value.audience === 'operator_browser') && typeof value.operatorCsrfToken === 'string' && /^[a-f\d]{64}$/.test(value.operatorCsrfToken)
}
export function nativeSubmissionHeaders(value: NativeSubmissionContext): Record<string, string> { return value.audience === 'public_sessions' ? {} : { 'X-Operator-CSRF-Token': value.operatorCsrfToken } }

export function nativeAdmissionFailure(status: number, detail: unknown): boolean {
  if (typeof detail !== 'string' || ['public_research_requester_expired', 'meta_research_scientific_protocol_unavailable'].includes(detail)) return false
  if (['operator_session_required', 'operator_csrf_check_failed', 'operator_unavailable', 'readiness_role_unavailable', 'readiness_actor_forbidden'].includes(detail)) return true
  return (detail.startsWith('meta_research_') || detail.startsWith('public_research_')) && [401, 403, 409, 503].includes(status) && !['meta_research_storage_unavailable', 'public_research_storage_unavailable', 'public_research_resource_unavailable'].includes(detail)
}

export type ScientificAdmissionError = 'scientific_variant_unavailable' | 'scientific_context_unavailable' | 'scientific_context_too_large'
export function scientificAdmissionError(status: number, detail: unknown): ScientificAdmissionError | null {
  if (status === 409 && ['meta_research_scientific_protocol_unavailable', 'ask_reassessment_overview_required'].includes(detail as string)) return 'scientific_variant_unavailable'
  if (status === 409 && ['scientific_context_integrity_failed', 'scientific_context_not_available', 'scientific_context_admission_closed'].includes(detail as string)) return 'scientific_context_unavailable'
  return status === 422 && detail === 'artifact_context_too_large' ? 'scientific_context_too_large' : null
}
