import { parseDiscoveryIntent } from './discoveryIntent'
import type { DiscoveryIntent } from '../discovery/types'
import type { NativeScientificTaskKind } from '../budget/activationTypes'

const REASONS = ['public_research_disabled', 'public_research_activation_required', 'public_research_authority_expired', 'public_research_stopped', 'public_research_frozen', 'public_research_budget_exhausted', 'public_research_daily_budget_exhausted', 'public_research_session_budget_exhausted', 'public_research_session_daily_budget_exhausted', 'public_research_prior_unresolved', 'public_research_resource_unavailable', 'operator_required', 'research_unavailable'] as const
export type ResearchAccessReason = typeof REASONS[number]
export interface ResearchAccessLimits {
  session_total_cap_microdollars: string
  session_daily_cap_microdollars: string
  max_jobs_per_session_day: number
  max_active_jobs_per_session: 1
  task_kinds: NativeScientificTaskKind[]
  discovery_intent: DiscoveryIntent | null
}
export interface ResearchAccess {
  protocol: 'polymath-research-access-v1'
  audience: 'public_sessions' | 'operator_browser' | null
  available: boolean
  reason: ResearchAccessReason | null
  expected_activation_sha256: string | null
  expires_at: string | null
  limits: ResearchAccessLimits | null
  remaining_session_allowance_microdollars: string | null
  remaining_session_jobs_today: number | null
  retry_after_seconds: number | null
}
export class ResearchAccessError extends Error {
  constructor(public code: 'invalid_response' | 'connection_failed' | 'browser_expired' | 'browser_changed' | 'resource_limited' | 'unavailable', public retryAfterSeconds: number | null = null) { super(code) }
}
function invalid(): never { throw new ResearchAccessError('invalid_response') }
function object(value: unknown, keys: string[]): Record<string, unknown> { if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid(); const item = value as Record<string, unknown>; if (Object.keys(item).length !== keys.length || keys.some((key) => !Object.hasOwn(item, key))) return invalid(); return item }
function integer(value: unknown): number { return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : invalid() }
function decimal(value: unknown): string { return typeof value === 'string' && /^(?:0|[1-9]\d{0,99})$/.test(value) ? value : invalid() }
function nullable<T>(value: unknown, parse: (value: unknown) => T): T | null { return value === null ? null : parse(value) }
function limits(value: unknown): ResearchAccessLimits {
  const item = object(value, ['session_total_cap_microdollars', 'session_daily_cap_microdollars', 'max_jobs_per_session_day', 'max_active_jobs_per_session', 'task_kinds', 'discovery_intent']), tasks = ['research', 'formalise', 'write_summary', 'write_paper', 'reassess_finding'] as const
  if (item.max_active_jobs_per_session !== 1 || !Array.isArray(item.task_kinds) || item.task_kinds.length !== tasks.length || new Set(item.task_kinds).size !== tasks.length || item.task_kinds.some((task) => !tasks.includes(task))) return invalid()
  const total = decimal(item.session_total_cap_microdollars), daily = decimal(item.session_daily_cap_microdollars), jobs = integer(item.max_jobs_per_session_day)
  if (BigInt(total) <= 0n || BigInt(daily) <= 0n || BigInt(daily) > BigInt(total) || jobs < 1 || jobs > 12 || BigInt(total) > 150000000n) return invalid()
  let discovery: DiscoveryIntent | null = null
  try { discovery = item.discovery_intent === null ? null : parseDiscoveryIntent(item.discovery_intent, invalid) ?? invalid() } catch { return invalid() }
  return { session_total_cap_microdollars: total, session_daily_cap_microdollars: daily, max_jobs_per_session_day: jobs, max_active_jobs_per_session: 1, task_kinds: item.task_kinds as NativeScientificTaskKind[], discovery_intent: discovery }
}
export function parseResearchAccess(value: unknown): ResearchAccess {
  const item = object(value, ['protocol', 'audience', 'available', 'reason', 'expected_activation_sha256', 'expires_at', 'limits', 'remaining_session_allowance_microdollars', 'remaining_session_jobs_today', 'retry_after_seconds'])
  if (item.protocol !== 'polymath-research-access-v1' || ![null, 'public_sessions', 'operator_browser'].includes(item.audience as string | null) || typeof item.available !== 'boolean' || item.reason !== null && !REASONS.includes(item.reason as ResearchAccessReason) || item.available !== (item.reason === null)) return invalid()
  const sha = nullable(item.expected_activation_sha256, (value) => typeof value === 'string' && /^[a-f\d]{64}$/.test(value) ? value : invalid()), expiry = nullable(item.expires_at, (value) => {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(value) || !Number.isFinite(Date.parse(value)) || new Date(value).toISOString().slice(0, 19) !== value.slice(0, 19)) return invalid()
    return value
  }), caps = nullable(item.limits, limits), allowance = nullable(item.remaining_session_allowance_microdollars, decimal), jobs = nullable(item.remaining_session_jobs_today, integer), retry = nullable(item.retry_after_seconds, (value) => { const seconds = integer(value); return seconds <= 86400 ? seconds : invalid() })
  if (item.audience !== 'public_sessions') {
    if (item.available || [sha, expiry, caps, allowance, jobs].some((value) => value !== null) || item.audience === 'operator_browser' && item.reason !== 'operator_required') return invalid()
  } else {
    if (sha === null && (allowance !== null || jobs !== null)) return invalid()
    if ((sha === null || expiry === null || caps === null) && [sha, expiry, caps].some((value) => value !== null) || item.available && (!sha || !expiry || !caps || allowance === null || jobs === null || BigInt(allowance) <= 0n) || caps && (jobs !== null && jobs > caps.max_jobs_per_session_day || allowance !== null && BigInt(allowance) > BigInt(caps.session_total_cap_microdollars))) return invalid()
  }
  return { protocol: 'polymath-research-access-v1', audience: item.audience as ResearchAccess['audience'], available: item.available, reason: item.reason as ResearchAccessReason | null, expected_activation_sha256: sha, expires_at: expiry, limits: caps, remaining_session_allowance_microdollars: allowance, remaining_session_jobs_today: jobs, retry_after_seconds: retry }
}
/** Existing browser ownership only. This read never creates or unlocks a session. */
export async function getResearchAccess(signal?: AbortSignal): Promise<ResearchAccess> {
  const controller = new AbortController(), abort = () => controller.abort(); signal?.addEventListener('abort', abort, { once: true }); if (signal?.aborted) controller.abort()
  const timer = window.setTimeout(abort, 10000)
  try {
    const response = await fetch('/api/v1/research/access', { method: 'GET', credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' }, signal: controller.signal })
    if (controller.signal.aborted) throw new ResearchAccessError('connection_failed')
    if (response.status === 401) throw new ResearchAccessError('browser_expired')
    if (response.status === 403) throw new ResearchAccessError('browser_changed')
    if (response.status === 429) { const retry = response.headers.get('retry-after'); throw new ResearchAccessError('resource_limited', retry && /^\d{1,6}$/.test(retry) ? Number(retry) : null) }
    if (!response.ok) throw new ResearchAccessError('unavailable')
    const reader = response.body?.getReader(); if (!reader) return invalid()
    let bytes = 0, result = ''; const decoder = new TextDecoder('utf-8', { fatal: true })
    try { while (true) { const chunk = await reader.read(); if (controller.signal.aborted) throw new ResearchAccessError('connection_failed'); if (chunk.done) break; bytes += chunk.value.byteLength; if (bytes > 32768) { void reader.cancel(); return invalid() }; result += decoder.decode(chunk.value, { stream: true }) }; result += decoder.decode() } finally { reader.releaseLock() }
    if (controller.signal.aborted) throw new ResearchAccessError('connection_failed')
    return parseResearchAccess(JSON.parse(result))
  } catch (caught) { if (caught instanceof ResearchAccessError) throw caught; throw new ResearchAccessError(controller.signal.aborted || caught instanceof TypeError ? 'connection_failed' : 'invalid_response') }
  finally { window.clearTimeout(timer); signal?.removeEventListener('abort', abort) }
}
