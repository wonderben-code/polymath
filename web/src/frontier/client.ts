// Experimental Frontier programmes. These routes never start research or
// spend; the owner's browser queues each due attempt through the run API.

const BASE = '/api/v1/frontier'
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const MAX_BYTES = 2_000_000

export type FrontierNodeStatus = 'pending' | 'running' | 'surveyed' | 'supported' | 'transferred' | 'derived' | 'conjectured' | 'decomposed' | 'open' | 'skipped'
export type FrontierAction = 'survey' | 'transfer' | 'decompose' | 'attempt' | 'converge'
const STATUSES: readonly FrontierNodeStatus[] = ['pending', 'running', 'surveyed', 'supported', 'transferred', 'derived', 'conjectured', 'decomposed', 'open', 'skipped']
const ACTIONS: readonly FrontierAction[] = ['survey', 'transfer', 'decompose', 'attempt', 'converge']

export interface FrontierAttempt { action: FrontierAction; run_id: string; result_id: string | null; outcome: string | null }
export interface FrontierNode {
  id: string
  parent_id: string | null
  kind: 'question' | 'gap' | 'step' | 'convergence'
  statement: string
  depth: number
  ordinal: number
  status: FrontierNodeStatus
  resolved_status: FrontierNodeStatus
  pending_action: FrontierAction | null
  premise_result_ids: string[]
  attempts: FrontierAttempt[]
}
export interface FrontierSummary { id: string; question: string; status: 'active' | 'paused' | 'completed' | 'stopped'; created_at: string; updated_at: string; node_counts: Record<string, number> }
export interface FrontierProgramme extends FrontierSummary {
  success_criteria: string | null
  limits: { max_attempts: number; max_depth: number; max_branches: number }
  attempts_used: number
  nodes: FrontierNode[]
}
export type FrontierNext =
  | { state: 'due'; node_id: string; action: FrontierAction; mode: 'ask' | 'explore'; goal: string; parent_result_ids: string[] }
  | { state: 'waiting'; run_id: string }
  | { state: 'paused' | 'stopped' | 'completed' | 'active'; reason?: string }

export class FrontierApiError extends Error {
  constructor(readonly code: string) { super(code) }
}

function invalid(): never { throw new FrontierApiError('invalid_response') }
function record(value: unknown): Record<string, unknown> { return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : invalid() }
function text(value: unknown, max = 8000): string { return typeof value === 'string' && value.length <= max ? value : invalid() }
function id(value: unknown): string { return typeof value === 'string' && UUID.test(value) ? value : invalid() }
function nullableId(value: unknown): string | null { return value === null ? null : id(value) }
function integer(value: unknown): number { return Number.isInteger(value) && (value as number) >= 0 && (value as number) <= 1_000_000 ? value as number : invalid() }
function oneOf<T extends string>(value: unknown, options: readonly T[]): T { return typeof value === 'string' && (options as readonly string[]).includes(value) ? value as T : invalid() }

function parseNode(value: unknown): FrontierNode {
  const item = record(value)
  const attempts = Array.isArray(item.attempts) ? item.attempts : invalid()
  return {
    id: id(item.id), parent_id: nullableId(item.parent_id), kind: oneOf(item.kind, ['question', 'gap', 'step', 'convergence'] as const), statement: text(item.statement, 4000),
    depth: integer(item.depth), ordinal: integer(item.ordinal), status: oneOf(item.status, STATUSES), resolved_status: oneOf(item.resolved_status, STATUSES),
    pending_action: item.pending_action === null ? null : oneOf(item.pending_action, ACTIONS),
    premise_result_ids: Array.isArray(item.premise_result_ids) && item.premise_result_ids.length <= 4 ? item.premise_result_ids.map(id) : invalid(),
    attempts: attempts.map((attempt) => { const entry = record(attempt); return { action: oneOf(entry.action, ACTIONS), run_id: id(entry.run_id), result_id: nullableId(entry.result_id), outcome: entry.outcome === null ? null : text(entry.outcome, 32) } }),
  }
}

function parseSummary(value: unknown): FrontierSummary {
  const item = record(value), counts = record(item.node_counts)
  return { id: id(item.id), question: text(item.question, 2000), status: oneOf(item.status, ['active', 'paused', 'completed', 'stopped'] as const), created_at: text(item.created_at, 64), updated_at: text(item.updated_at, 64), node_counts: Object.fromEntries(Object.entries(counts).map(([key, count]) => [oneOf(key, STATUSES), integer(count)])) }
}

export function parseProgramme(value: unknown): FrontierProgramme {
  const item = record(value), limits = record(item.limits)
  const nodes = Array.isArray(item.nodes) ? item.nodes.map(parseNode) : invalid()
  if (nodes.length > 2000 || new Set(nodes.map((node) => node.id)).size !== nodes.length) invalid()
  return { ...parseSummary(item), success_criteria: item.success_criteria === null ? null : text(item.success_criteria, 1000), limits: { max_attempts: integer(limits.max_attempts), max_depth: integer(limits.max_depth), max_branches: integer(limits.max_branches) }, attempts_used: integer(item.attempts_used), nodes }
}

function parseNext(value: unknown): FrontierNext {
  const item = record(value)
  if (item.state === 'due') { const parents = Array.isArray(item.parent_result_ids) && item.parent_result_ids.length <= 4 ? item.parent_result_ids.map(id) : invalid(); return { state: 'due', node_id: id(item.node_id), action: oneOf(item.action, ACTIONS), mode: oneOf(item.mode, ['ask', 'explore'] as const), goal: text(item.goal, 4000), parent_result_ids: parents } }
  if (item.state === 'waiting') return { state: 'waiting', run_id: id(item.run_id) }
  return { state: oneOf(item.state, ['paused', 'stopped', 'completed', 'active'] as const), ...(typeof item.reason === 'string' ? { reason: text(item.reason, 64) } : {}) }
}

async function request<T>(url: string, parse: (value: unknown) => T, signal?: AbortSignal, write?: { csrf: string; body?: unknown }): Promise<T> {
  let response: Response
  try {
    response = await fetch(url, {
      method: write ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal,
      headers: { Accept: 'application/json', ...(write ? { 'X-CSRF-Token': write.csrf, ...(write.body === undefined ? {} : { 'Content-Type': 'application/json' }) } : {}) },
      ...(write?.body === undefined ? {} : { body: JSON.stringify(write.body) }),
    })
  } catch { throw new FrontierApiError(signal?.aborted ? 'aborted' : 'unavailable') }
  if (!response.ok) {
    let code = 'unavailable'
    try { const detail = record(await response.json()).detail; if (typeof detail === 'string' && /^[a-z_]{1,100}$/.test(detail)) code = detail } catch { /* Never display untrusted server details. */ }
    throw new FrontierApiError(response.status === 401 || response.status === 403 ? 'session_expired' : code)
  }
  const size = response.headers.get('content-length')
  if (size && (!/^\d+$/.test(size) || Number(size) > MAX_BYTES)) invalid()
  let raw: unknown
  try { raw = await response.json() } catch { return invalid() }
  return parse(raw)
}

function path(programmeId: string): string { if (!UUID.test(programmeId)) throw new FrontierApiError('invalid_request'); return `${BASE}/${programmeId}` }

export const loadFrontierCapabilities = (signal?: AbortSignal) => request(`${BASE}/capabilities`, (value) => { const item = record(value); return { available: item.frontier_available === true, experimental: item.experimental === true } }, signal)
export const listProgrammes = (signal?: AbortSignal) => request(BASE, (value) => { const list = record(value).programmes; return Array.isArray(list) ? list.slice(0, 50).map(parseSummary) : invalid() }, signal)
export const loadProgramme = (programmeId: string, signal?: AbortSignal) => request(path(programmeId), parseProgramme, signal)
export function createProgramme(csrf: string, body: { question: string; success_criteria?: string; max_attempts?: number; max_depth?: number; max_branches?: number }, signal?: AbortSignal) {
  return request(BASE, parseProgramme, signal, { csrf, body })
}
export const nextFrontierAction = (programmeId: string, csrf: string, signal?: AbortSignal) => request(`${path(programmeId)}/next`, (value) => { const item = record(value); return { next: parseNext(item.next), programme: parseProgramme(item.programme) } }, signal, { csrf })
export const attachFrontierRun = (programmeId: string, csrf: string, body: { node_id: string; action: FrontierAction; run_id: string }, signal?: AbortSignal) => request(`${path(programmeId)}/attach`, parseProgramme, signal, { csrf, body })
export const changeProgramme = (programmeId: string, csrf: string, change: 'pause' | 'resume' | 'stop', signal?: AbortSignal) => request(`${path(programmeId)}/${change}`, parseProgramme, signal, { csrf })
export const pruneFrontierNode = (programmeId: string, nodeId: string, csrf: string, signal?: AbortSignal) => {
  if (!UUID.test(nodeId)) throw new FrontierApiError('invalid_request')
  return request(`${path(programmeId)}/nodes/${nodeId}/prune`, parseProgramme, signal, { csrf })
}
