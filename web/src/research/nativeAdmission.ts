/** Ephemeral admission from the current browser/operator activation only. */
export interface NativeSubmissionContext {
  operatorCsrfToken: string
  expectedActivationSha256: string
}
export interface NativeRunControl { activation_sha256: string; expected_lease_token: string; can_resume: boolean }

export function parseNativeRunControl(value: unknown, invalid: () => never): NativeRunControl | undefined {
  if (value === undefined) return undefined
  if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid()
  const item = value as Record<string, unknown>
  if (Object.keys(item).length !== 3 || typeof item.activation_sha256 !== 'string' || !/^[a-f\d]{64}$/.test(item.activation_sha256) || typeof item.expected_lease_token !== 'string' || !/^(?:0|[1-9]\d{0,18})$/.test(item.expected_lease_token) || BigInt(item.expected_lease_token) > 9223372036854775807n || typeof item.can_resume !== 'boolean') return invalid()
  return { activation_sha256: item.activation_sha256, expected_lease_token: item.expected_lease_token, can_resume: item.can_resume }
}

export function validNativeSubmission(value: NativeSubmissionContext): boolean {
  return /^[a-f\d]{64}$/.test(value.operatorCsrfToken) && /^[a-f\d]{64}$/.test(value.expectedActivationSha256)
}

export function nativeAdmissionFailure(status: number, detail: unknown): boolean {
  if (typeof detail !== 'string') return false
  if (['operator_session_required', 'operator_csrf_check_failed', 'operator_unavailable', 'readiness_role_unavailable', 'readiness_actor_forbidden'].includes(detail)) return true
  return detail.startsWith('meta_research_') && [401, 403, 409, 503].includes(status) && detail !== 'meta_research_storage_unavailable'
}
