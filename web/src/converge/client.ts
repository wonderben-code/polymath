// Experimental Converge programmes. These routes never start research or
// spend; the owner's browser queues each due step through the run API.

const BASE = '/api/v1/converge'
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const MAX_BYTES = 4_000_000
const MONEY = 1_000_000_000_000

export type ConvergeStatus = 'active' | 'paused' | 'completed' | 'stopped'
export type StepStatus = 'running' | 'held' | 'not_held' | 'stopped'
export interface ConvergeField { name: string; origin: 'chosen' | 'spread' | 'found'; touched: boolean }
export interface ConvergeStep {
  id: string
  kind: 'thread' | 'meta'
  level: number
  status: StepStatus
  title: string | null
  field_left: string | null
  field_right: string | null
  field_indices: number[]
  premise_step_ids: string[]
  run_id: string
  result_id: string | null
}
export interface ConvergeSummary { id: string; theme: string | null; status: ConvergeStatus; field_count: number; connections_held: number; created_at: string; updated_at: string }
export interface ConvergeProgramme extends ConvergeSummary {
  finished_reason: string | null
  fields: ConvergeField[]
  ceiling_microdollars: number
  spent_microdollars: number
  step_estimate_microdollars: number
  max_steps: number
  steps_used: number
  deepest_step_ids: string[]
  steps: ConvergeStep[]
}
export interface ConvergeCapabilities { available: boolean; minFields: number; maxFields: number; minCeiling: number; maxCeiling: number; stepEstimate: number }
export type ConvergeNext =
  | { state: 'due'; kind: 'thread' | 'meta'; level: number; mode: 'explore'; goal: string; parent_result_ids: string[] }
  | { state: 'waiting'; run_id: string }
  | { state: 'paused' | 'stopped' | 'completed' | 'active'; reason?: string }

export class ConvergeApiError extends Error {
  constructor(readonly code: string) { super(code) }
}

function invalid(): never { throw new ConvergeApiError('invalid_response') }
function record(value: unknown): Record<string, unknown> { return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : invalid() }
function text(value: unknown, max = 8000): string { return typeof value === 'string' && value.length <= max ? value : invalid() }
function nullableText(value: unknown, max: number): string | null { return value === null ? null : text(value, max) }
function id(value: unknown): string { return typeof value === 'string' && UUID.test(value) ? value : invalid() }
function integer(value: unknown, max = 1_000_000): number { return Number.isInteger(value) && (value as number) >= 0 && (value as number) <= max ? value as number : invalid() }
function oneOf<T extends string>(value: unknown, options: readonly T[]): T { return typeof value === 'string' && (options as readonly string[]).includes(value) ? value as T : invalid() }
function list<T>(value: unknown, parse: (item: unknown) => T, max: number): T[] { return Array.isArray(value) && value.length <= max ? value.map(parse) : invalid() }

const STATUSES = ['active', 'paused', 'completed', 'stopped'] as const

function parseSummary(value: unknown): ConvergeSummary {
  const item = record(value)
  return { id: id(item.id), theme: nullableText(item.theme, 2000), status: oneOf(item.status, STATUSES), field_count: integer(item.field_count, 240), connections_held: integer(item.connections_held), created_at: text(item.created_at, 64), updated_at: text(item.updated_at, 64) }
}

function parseStep(value: unknown, fieldCount: number): ConvergeStep {
  const item = record(value)
  return {
    id: id(item.id), kind: oneOf(item.kind, ['thread', 'meta'] as const), level: integer(item.level, 40), status: oneOf(item.status, ['running', 'held', 'not_held', 'stopped'] as const),
    title: nullableText(item.title, 300), field_left: nullableText(item.field_left, 200), field_right: nullableText(item.field_right, 200),
    field_indices: list(item.field_indices, (index) => { const value = integer(index, 239); return value < fieldCount ? value : invalid() }, 240),
    premise_step_ids: list(item.premise_step_ids, id, 4), run_id: id(item.run_id), result_id: item.result_id === null ? null : id(item.result_id),
  }
}

export function parseProgramme(value: unknown): ConvergeProgramme {
  const item = record(value)
  const fields = list(item.fields, (field) => { const entry = record(field); return { name: text(entry.name, 100), origin: oneOf(entry.origin, ['chosen', 'spread', 'found'] as const), touched: entry.touched === true } }, 240)
  const steps = list(item.steps, (step) => parseStep(step, fields.length), 400)
  const ids = new Set(steps.map((step) => step.id))
  if (ids.size !== steps.length || steps.some((step) => step.premise_step_ids.some((premise) => !ids.has(premise)))) invalid()
  const deepest = list(item.deepest_step_ids, id, 400)
  if (deepest.some((value) => !ids.has(value))) invalid()
  return {
    ...parseSummary(item), finished_reason: nullableText(item.finished_reason, 32), fields,
    ceiling_microdollars: integer(item.ceiling_microdollars, MONEY), spent_microdollars: integer(item.spent_microdollars, MONEY), step_estimate_microdollars: integer(item.step_estimate_microdollars, MONEY),
    max_steps: integer(item.max_steps, 400), steps_used: integer(item.steps_used, 400), deepest_step_ids: deepest, steps,
  }
}

function parseNext(value: unknown): ConvergeNext {
  const item = record(value)
  if (item.state === 'due') return { state: 'due', kind: oneOf(item.kind, ['thread', 'meta'] as const), level: integer(item.level, 40), mode: oneOf(item.mode, ['explore'] as const), goal: text(item.goal, 4000), parent_result_ids: list(item.parent_result_ids, id, 4) }
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
  } catch { throw new ConvergeApiError(signal?.aborted ? 'aborted' : 'unavailable') }
  if (!response.ok) {
    let code = 'unavailable'
    try { const detail = record(await response.json()).detail; if (typeof detail === 'string' && /^[a-z_]{1,100}$/.test(detail)) code = detail } catch { /* Never display untrusted server details. */ }
    throw new ConvergeApiError(response.status === 401 || response.status === 403 ? 'session_expired' : code)
  }
  const size = response.headers.get('content-length')
  if (size && (!/^\d+$/.test(size) || Number(size) > MAX_BYTES)) invalid()
  let raw: unknown
  try { raw = await response.json() } catch { return invalid() }
  return parse(raw)
}

function path(programmeId: string): string { if (!UUID.test(programmeId)) throw new ConvergeApiError('invalid_request'); return `${BASE}/${programmeId}` }

export const loadConvergeCapabilities = (signal?: AbortSignal) => request(`${BASE}/capabilities`, (value): ConvergeCapabilities => {
  const item = record(value)
  return { available: item.converge_available === true, minFields: integer(item.min_fields, 240), maxFields: integer(item.max_fields, 240), minCeiling: integer(item.min_ceiling_microdollars, MONEY), maxCeiling: integer(item.max_ceiling_microdollars, MONEY), stepEstimate: integer(item.step_estimate_microdollars, MONEY) }
}, signal)
export const suggestFields = (query: string, signal?: AbortSignal) => request(`${BASE}/fields?q=${encodeURIComponent(query.slice(0, 100))}`, (value) => list(record(value).fields, (name) => text(name, 100), 20), signal)
export const listConvergences = (signal?: AbortSignal) => request(BASE, (value) => list(record(value).programmes, parseSummary, 50), signal)
export const loadConvergence = (programmeId: string, signal?: AbortSignal) => request(path(programmeId), parseProgramme, signal)
export const createConvergence = (csrf: string, body: { theme: string | null; field_count: number; fields: string[]; ceiling_microdollars: number }, signal?: AbortSignal) => request(BASE, parseProgramme, signal, { csrf, body })
export const nextConvergeStep = (programmeId: string, csrf: string, signal?: AbortSignal) => request(`${path(programmeId)}/next`, (value) => { const item = record(value); return { next: parseNext(item.next), programme: parseProgramme(item.programme) } }, signal, { csrf })
export const attachConvergeRun = (programmeId: string, csrf: string, body: { kind: 'thread' | 'meta'; parent_result_ids: string[]; run_id: string }, signal?: AbortSignal) => request(`${path(programmeId)}/attach`, parseProgramme, signal, { csrf, body })
export const extendConvergence = (programmeId: string, csrf: string, body: { fields?: string[]; add_count?: number; ceiling_microdollars?: number }, signal?: AbortSignal) => request(`${path(programmeId)}/extend`, parseProgramme, signal, { csrf, body })
export const changeConvergence = (programmeId: string, csrf: string, change: 'pause' | 'resume' | 'stop', signal?: AbortSignal) => request(`${path(programmeId)}/${change}`, parseProgramme, signal, { csrf })

/** Whole dollars from microdollars, e.g. "$12" or "$0.50". */
export function dollars(microdollars: number): string {
  const value = microdollars / 1_000_000
  return value >= 10 ? `$${Math.round(value)}` : `$${value.toFixed(2)}`
}
