// Solve: key finder and key maker (docs/SOLVE.md). These routes never start
// research or spend; the visitor's browser queues each stage through the run API.

const BASE = '/api/v1/solve'
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const MAX_BYTES = 2_000_000

export type SolveStage = 'find' | 'fit' | 'forge'
export type SolveOutcome = 'found' | 'forged' | 'close' | 'none'
export type SolveStatus = 'active' | 'paused' | 'completed' | 'stopped'
export type StepFinding =
  | { kind: 'bridge'; title: string; field_left: string; field_right: string; explanation: string; falls_short: string; verdict: string; open_question: string }
  | { kind: 'answer'; summary: string; known: string[]; other: { label: string; text: string }[]; unsolved: string[] }
  | { kind: 'insufficient_evidence' }
export interface SolveStep { id: string; stage: SolveStage; ordinal: number; target: string; status: 'pending' | 'running' | 'done' | 'failed'; outcome: string | null; run_id: string | null; result_id: string | null; premise_result_ids: string[]; finding: StepFinding | null }
export interface SolveSummary { id: string; problem: string; status: SolveStatus; outcome: SolveOutcome | null; frontier_programme_id: string | null; frontier_node_id: string | null; created_at: string; updated_at: string }
export interface SolveProgramme extends SolveSummary { steps: SolveStep[] }
export type SolveNext =
  | { state: 'due'; step_id: string; stage: SolveStage; mode: 'ask' | 'explore'; goal: string; parent_result_ids: string[] }
  | { state: 'waiting'; run_id: string }
  | { state: 'paused' | 'stopped' | 'completed' | 'active'; outcome?: SolveOutcome | null }

export class SolveApiError extends Error {
  constructor(readonly code: string) { super(code) }
}

function invalid(): never { throw new SolveApiError('invalid_response') }
function record(value: unknown): Record<string, unknown> { return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : invalid() }
function text(value: unknown, max = 8000): string { return typeof value === 'string' && value.length <= max ? value : invalid() }
function id(value: unknown): string { return typeof value === 'string' && UUID.test(value) ? value : invalid() }
function nullableId(value: unknown): string | null { return value === null || value === undefined ? null : id(value) }
function oneOf<T extends string>(value: unknown, options: readonly T[]): T { return typeof value === 'string' && (options as readonly string[]).includes(value) ? value as T : invalid() }
function texts(value: unknown, max: number): string[] { return Array.isArray(value) && value.length <= 8 ? value.map((item) => text(item, max)) : invalid() }
const OUTCOMES = ['found', 'forged', 'close', 'none'] as const
const STAGES = ['find', 'fit', 'forge'] as const

function parseFinding(value: unknown): StepFinding | null {
  if (value === null || value === undefined) return null
  const item = record(value)
  if (item.kind === 'bridge') return { kind: 'bridge', title: text(item.title, 400), field_left: text(item.field_left, 300), field_right: text(item.field_right, 300), explanation: text(item.explanation, 1200), falls_short: text(item.falls_short, 800), verdict: text(item.verdict, 800), open_question: text(item.open_question, 600) }
  if (item.kind === 'answer') {
    const other = Array.isArray(item.other) && item.other.length <= 8 ? item.other.map((entry) => { const claim = record(entry); return { label: text(claim.label, 40), text: text(claim.text, 2200) } }) : invalid()
    return { kind: 'answer', summary: text(item.summary, 1200), known: texts(item.known, 2200), other, unsolved: texts(item.unsolved, 2200) }
  }
  if (item.kind === 'insufficient_evidence') return { kind: 'insufficient_evidence' }
  return invalid()
}

function parseStep(value: unknown): SolveStep {
  const item = record(value)
  return {
    id: id(item.id), stage: oneOf(item.stage, STAGES), ordinal: typeof item.ordinal === 'number' ? item.ordinal : invalid(), target: text(item.target, 4000),
    status: oneOf(item.status, ['pending', 'running', 'done', 'failed'] as const), outcome: item.outcome === null ? null : text(item.outcome, 40),
    run_id: nullableId(item.run_id), result_id: nullableId(item.result_id),
    premise_result_ids: Array.isArray(item.premise_result_ids) && item.premise_result_ids.length <= 4 ? item.premise_result_ids.map(id) : invalid(),
    finding: parseFinding(item.finding),
  }
}

function parseSummary(value: unknown): SolveSummary {
  const item = record(value)
  return {
    id: id(item.id), problem: text(item.problem, 2000), status: oneOf(item.status, ['active', 'paused', 'completed', 'stopped'] as const),
    outcome: item.outcome === null ? null : oneOf(item.outcome, OUTCOMES), frontier_programme_id: nullableId(item.frontier_programme_id), frontier_node_id: nullableId(item.frontier_node_id),
    created_at: text(item.created_at, 64), updated_at: text(item.updated_at, 64),
  }
}

export function parseProgramme(value: unknown): SolveProgramme {
  const item = record(value)
  const steps = Array.isArray(item.steps) && item.steps.length <= 8 ? item.steps.map(parseStep) : invalid()
  return { ...parseSummary(item), steps }
}

function parseNext(value: unknown): SolveNext {
  const item = record(value)
  if (item.state === 'due') {
    const parents = Array.isArray(item.parent_result_ids) && item.parent_result_ids.length <= 4 ? item.parent_result_ids.map(id) : invalid()
    return { state: 'due', step_id: id(item.step_id), stage: oneOf(item.stage, STAGES), mode: oneOf(item.mode, ['ask', 'explore'] as const), goal: text(item.goal, 4000), parent_result_ids: parents }
  }
  if (item.state === 'waiting') return { state: 'waiting', run_id: id(item.run_id) }
  return { state: oneOf(item.state, ['paused', 'stopped', 'completed', 'active'] as const), outcome: item.outcome == null ? null : oneOf(item.outcome, OUTCOMES) }
}

async function request<T>(url: string, parse: (value: unknown) => T, signal?: AbortSignal, write?: { csrf: string; body?: unknown }): Promise<T> {
  let response: Response
  try {
    response = await fetch(url, {
      method: write ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal,
      headers: { Accept: 'application/json', ...(write ? { 'X-CSRF-Token': write.csrf, ...(write.body === undefined ? {} : { 'Content-Type': 'application/json' }) } : {}) },
      ...(write?.body === undefined ? {} : { body: JSON.stringify(write.body) }),
    })
  } catch { throw new SolveApiError(signal?.aborted ? 'aborted' : 'unavailable') }
  if (!response.ok) {
    let code = 'unavailable'
    try { const detail = record(await response.json()).detail; if (typeof detail === 'string' && /^[a-z_]{1,100}$/.test(detail)) code = detail } catch { /* Never display untrusted server details. */ }
    throw new SolveApiError(response.status === 401 || response.status === 403 ? 'session_expired' : code)
  }
  const size = response.headers.get('content-length')
  if (size && (!/^\d+$/.test(size) || Number(size) > MAX_BYTES)) invalid()
  let raw: unknown
  try { raw = await response.json() } catch { return invalid() }
  return parse(raw)
}

function path(programmeId: string): string { if (!UUID.test(programmeId)) throw new SolveApiError('invalid_request'); return `${BASE}/${programmeId}` }

export const listSolves = (signal?: AbortSignal) => request(BASE, (value) => { const list = record(value).programmes; return Array.isArray(list) ? list.slice(0, 50).map(parseSummary) : invalid() }, signal)
export const loadSolve = (programmeId: string, signal?: AbortSignal) => request(path(programmeId), parseProgramme, signal)
export const createSolve = (csrf: string, body: { problem?: string; frontier_programme_id?: string; frontier_node_id?: string }, signal?: AbortSignal) => request(BASE, parseProgramme, signal, { csrf, body })
export const nextSolveStep = (programmeId: string, csrf: string, signal?: AbortSignal) => request(`${path(programmeId)}/next`, (value) => { const item = record(value); return { next: parseNext(item.next), programme: parseProgramme(item.programme) } }, signal, { csrf })
export const attachSolveRun = (programmeId: string, csrf: string, body: { step_id: string; stage: SolveStage; run_id: string }, signal?: AbortSignal) => request(`${path(programmeId)}/attach`, parseProgramme, signal, { csrf, body })
export const changeSolve = (programmeId: string, csrf: string, change: 'pause' | 'resume' | 'stop', signal?: AbortSignal) => request(`${path(programmeId)}/${change}`, parseProgramme, signal, { csrf })
