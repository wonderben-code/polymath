import { parseScientificFinding } from './findings'
import type { AnswerFinding } from '../research/types'
import type { AskOverviewBinding, ResearchProtocols } from './types'
import { SCIENTIFIC_CAPABILITIES } from './types'
import { hash, invalid, list, member, nullable, object, text } from './primitives'
export function parseAskOverviewBinding(value: unknown): AskOverviewBinding {
  const item = object(value, ['protocol', 'claim_ids'])
  if (item.protocol !== 'polymath-ask-claim-overview-v1') return invalid('ask_overview_binding_invalid')
  const ids = list(item.claim_ids, (raw) => { const value = text(raw, 64); return /^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(value) ? value : invalid('ask_overview_binding_invalid') }, 1, 8)
  if (new Set(ids).size !== ids.length) return invalid('ask_overview_duplicate_claim')
  return { protocol: 'polymath-ask-claim-overview-v1', claim_ids: ids }
}
export function validateAnswerOverview(finding: AnswerFinding, binding: AskOverviewBinding): void {
  const parsedFinding = parseScientificFinding(finding)
  if (parsedFinding.outcome !== 'answer') return invalid('ask_overview_wrong_result')
  finding = parsedFinding
  const byId = new Map(finding.claims.map((claim) => [claim.id, claim]))
  if (finding.outcome !== 'answer' || byId.size !== finding.claims.length) return invalid('ask_overview_binding_invalid')
  const parsed = parseAskOverviewBinding(binding)
  if (parsed.claim_ids.some((id) => !byId.has(id))) return invalid('ask_overview_unknown_claim')
  const summary = parsed.claim_ids.map((id) => byId.get(id)!.text).join('\n\n')
  if (Array.from(summary).length > 3000) return invalid('ask_overview_too_long')
  if (finding.summary !== summary) return invalid('ask_overview_summary_mismatch')
}
/** The caller's expected flag comes from validated admission metadata, never user/model input. */
export function parseResultOverviewMetadata(rawResult: unknown, expected?: boolean): AskOverviewBinding | undefined {
  if (!rawResult || typeof rawResult !== 'object' || Array.isArray(rawResult) || expected !== undefined && typeof expected !== 'boolean') return invalid('ask_overview_binding_invalid')
  const result = rawResult as Record<string, unknown>
  if (!Object.hasOwn(result, 'overview_binding')) { if (expected === true && result.status === 'answer') return invalid('ask_overview_binding_required'); return undefined }
  if (expected === false) return invalid('ask_overview_not_admitted')
  if (result.overview_binding === null || result.overview_binding === undefined || result.mode !== 'ask' || result.status !== 'answer') return invalid('ask_overview_wrong_result')
  const finding = parseScientificFinding(result.finding)
  if (finding.outcome !== 'answer') return invalid('ask_overview_binding_invalid')
  const binding = parseAskOverviewBinding(result.overview_binding); validateAnswerOverview(finding, binding); return binding
}
/** No transport/authority here; the caller separately checks this public hash against D2 access. */
export function parseResearchProtocols(value: unknown): ResearchProtocols {
  const item = object(value, ['protocol', 'activation_sha256', 'capabilities'])
  if (item.protocol !== 'polymath-research-protocols-v1') return invalid()
  const sha = nullable(item.activation_sha256, hash), capabilities = list(item.capabilities, (raw) => member(raw, SCIENTIFIC_CAPABILITIES), 0, 3)
  if (sha === null && capabilities.length || new Set(capabilities).size !== capabilities.length || capabilities.some((capability, ordinal) => ordinal > 0 && SCIENTIFIC_CAPABILITIES.indexOf(capability) <= SCIENTIFIC_CAPABILITIES.indexOf(capabilities[ordinal - 1]))) return invalid()
  return { protocol: 'polymath-research-protocols-v1', activation_sha256: sha, capabilities }
}

export function parseOverviewIntent(value: unknown, protocol: 'polymath-ask-claim-overview-v1'): import('./types').AskOverviewIntent | undefined
export function parseOverviewIntent(value: unknown, protocol: 'polymath-ask-reassessment-overview-v1'): import('./types').AskReassessmentOverviewIntent | undefined
export function parseOverviewIntent(value: unknown, protocol: 'polymath-artifact-scientific-context-v1'): import('./types').ArtifactScientificContextIntent | undefined
export function parseOverviewIntent(value: unknown, protocol: string): { protocol: never } | undefined {
  if (value === undefined) return undefined
  const parsed = object(value, ['protocol']); if (parsed.protocol !== protocol) return invalid()
  return { protocol: protocol as never }
}
