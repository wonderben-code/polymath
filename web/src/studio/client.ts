// Innovation Studio (docs/STUDIO.md). These routes never start research or
// spend; the visitor's browser queues each stage through the ordinary run API.

const BASE = '/api/v1/studio'
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const MAX_BYTES = 2_000_000

export const STAGES = ['brief', 'inspire', 'design', 'engineer', 'test'] as const
export const WILDNESS = ['near', 'far', 'strange'] as const
export const MAX_SPECS = 6
export type StudioStage = (typeof STAGES)[number]
export type Wildness = (typeof WILDNESS)[number]
export type StudioOutcome = 'designed' | 'inspired' | 'none'
export type StudioStatus = 'active' | 'paused' | 'completed' | 'stopped'
export interface Quote { source_id: string | null; quote: string; supports: string }
export interface Source { id: string; title: string; url: string; authors: string[]; more_authors: boolean; year: number | null; venue: string | null; doi: string | null }
export interface Claim { label: string; text: string; derivation: string | null; caveats: string[]; quotes: Quote[] }
export type StepFinding =
  | { kind: 'bridge'; title: string; field_left: string; field_right: string; explanation: string; mappings: { left: string; right: string; assumption: string }[]; objection: string; verdict: string; limitations: string; open_question: string; quotes: Quote[]; sources: Source[] }
  | { kind: 'answer'; summary: string; claims: Claim[]; limitations: string[]; open_questions: string[]; sources: Source[] }
  | { kind: 'insufficient_evidence' }
export interface StudioStep { id: string; stage: StudioStage; ordinal: number; target: string; status: 'pending' | 'running' | 'done' | 'failed'; outcome: string | null; run_id: string | null; result_id: string | null; premise_result_ids: string[]; finding: StepFinding | null }
export interface StudioSummary { id: string; brief: string; specs: string[]; wildness: Wildness; status: StudioStatus; outcome: StudioOutcome | null; created_at: string; updated_at: string }
export interface StudioProgramme extends StudioSummary { steps: StudioStep[] }
export type StudioNext =
  | { state: 'due'; step_id: string; stage: StudioStage; mode: 'ask' | 'explore'; goal: string; parent_result_ids: string[] }
  | { state: 'waiting'; run_id: string }
  | { state: 'paused' | 'stopped' | 'completed' | 'active'; outcome?: StudioOutcome | null }

export class StudioApiError extends Error {
  constructor(readonly code: string) { super(code) }
}

function invalid(): never { throw new StudioApiError('invalid_response') }
function record(value: unknown): Record<string, unknown> { return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : invalid() }
function text(value: unknown, max = 8000): string { return typeof value === 'string' && value.length <= max ? value : invalid() }
function id(value: unknown): string { return typeof value === 'string' && UUID.test(value) ? value : invalid() }
function nullableId(value: unknown): string | null { return value === null || value === undefined ? null : id(value) }
function oneOf<T extends string>(value: unknown, options: readonly T[]): T { return typeof value === 'string' && (options as readonly string[]).includes(value) ? value as T : invalid() }
function list<T>(value: unknown, max: number, parse: (item: unknown) => T): T[] { return Array.isArray(value) && value.length <= max ? value.map(parse) : invalid() }
const OUTCOMES = ['designed', 'inspired', 'none'] as const

function nullableText(value: unknown, max: number): string | null { return value === null || value === undefined ? null : text(value, max) }
function parseQuote(value: unknown): Quote { const item = record(value); return { source_id: nullableText(item.source_id, 600), quote: text(item.quote, 800), supports: text(item.supports, 600) } }
function parseSource(value: unknown): Source {
  const item = record(value)
  const url = text(item.url, 2000)
  if (!/^https:\/\/[^\s]+$/.test(url)) invalid()
  const year = item.year === null || item.year === undefined ? null : typeof item.year === 'number' && Number.isInteger(item.year) ? item.year : invalid()
  return { id: text(item.id, 600), title: text(item.title, 400), url, authors: list(item.authors, 3, (entry) => text(entry, 120)), more_authors: item.more_authors === true, year, venue: nullableText(item.venue, 200), doi: nullableText(item.doi, 200) }
}

function parseFinding(value: unknown): StepFinding | null {
  if (value === null || value === undefined) return null
  const item = record(value)
  if (item.kind === 'bridge') {
    return {
      kind: 'bridge', title: text(item.title, 400), field_left: text(item.field_left, 300), field_right: text(item.field_right, 300), explanation: text(item.explanation, 3200),
      mappings: list(item.mappings, 8, (entry) => { const pair = record(entry); return { left: text(pair.left, 1100), right: text(pair.right, 1100), assumption: text(pair.assumption, 1600) } }),
      objection: text(item.objection, 2200), verdict: text(item.verdict, 2200), limitations: text(item.limitations, 2200), open_question: text(item.open_question, 1600),
      quotes: list(item.quotes, 12, parseQuote), sources: list(item.sources, 20, parseSource),
    }
  }
  if (item.kind === 'answer') {
    return {
      kind: 'answer', summary: text(item.summary, 3200),
      claims: list(item.claims, 8, (entry) => { const claim = record(entry); return { label: oneOf(claim.label, ['known', 'postulated', 'speculative'] as const), text: text(claim.text, 2200), derivation: nullableText(claim.derivation, 2200), caveats: list(claim.caveats, 6, (caveat) => text(caveat, 1100)), quotes: list(claim.quotes, 8, parseQuote) } }),
      limitations: list(item.limitations, 8, (entry) => text(entry, 1100)), open_questions: list(item.open_questions, 8, (entry) => text(entry, 1100)), sources: list(item.sources, 20, parseSource),
    }
  }
  if (item.kind === 'insufficient_evidence') return { kind: 'insufficient_evidence' }
  return invalid()
}

function parseStep(value: unknown): StudioStep {
  const item = record(value)
  return {
    id: id(item.id), stage: oneOf(item.stage, STAGES), ordinal: typeof item.ordinal === 'number' ? item.ordinal : invalid(), target: text(item.target, 4000),
    status: oneOf(item.status, ['pending', 'running', 'done', 'failed'] as const), outcome: item.outcome === null ? null : text(item.outcome, 40),
    run_id: nullableId(item.run_id), result_id: nullableId(item.result_id),
    premise_result_ids: list(item.premise_result_ids, 4, id), finding: parseFinding(item.finding),
  }
}

function parseSummary(value: unknown): StudioSummary {
  const item = record(value)
  return {
    id: id(item.id), brief: text(item.brief, 1000), specs: list(item.specs, MAX_SPECS, (entry) => text(entry, 200)), wildness: oneOf(item.wildness, WILDNESS),
    status: oneOf(item.status, ['active', 'paused', 'completed', 'stopped'] as const), outcome: item.outcome === null ? null : oneOf(item.outcome, OUTCOMES),
    created_at: text(item.created_at, 64), updated_at: text(item.updated_at, 64),
  }
}

export function parseProgramme(value: unknown): StudioProgramme {
  const item = record(value)
  return { ...parseSummary(item), steps: list(item.steps, 10, parseStep) }
}

function parseNext(value: unknown): StudioNext {
  const item = record(value)
  if (item.state === 'due') return { state: 'due', step_id: id(item.step_id), stage: oneOf(item.stage, STAGES), mode: oneOf(item.mode, ['ask', 'explore'] as const), goal: text(item.goal, 4000), parent_result_ids: list(item.parent_result_ids, 4, id) }
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
  } catch { throw new StudioApiError(signal?.aborted ? 'aborted' : 'unavailable') }
  if (!response.ok) {
    let code = 'unavailable'
    try { const detail = record(await response.json()).detail; if (typeof detail === 'string' && /^[a-z_]{1,100}$/.test(detail)) code = detail } catch { /* Never display untrusted server details. */ }
    throw new StudioApiError(response.status === 401 || response.status === 403 ? 'session_expired' : code)
  }
  const size = response.headers.get('content-length')
  if (size && (!/^\d+$/.test(size) || Number(size) > MAX_BYTES)) invalid()
  let raw: unknown
  try { raw = await response.json() } catch { return invalid() }
  return parse(raw)
}

function path(programmeId: string): string { if (!UUID.test(programmeId)) throw new StudioApiError('invalid_request'); return `${BASE}/${programmeId}` }

export const listStudio = (signal?: AbortSignal) => request(BASE, (value) => list(record(value).programmes, 50, parseSummary), signal)
export const loadStudio = (programmeId: string, signal?: AbortSignal) => request(path(programmeId), parseProgramme, signal)
export const createStudio = (csrf: string, body: { brief: string; specs: string[]; wildness: Wildness }, signal?: AbortSignal) => request(BASE, parseProgramme, signal, { csrf, body })
export const nextStudioStep = (programmeId: string, csrf: string, signal?: AbortSignal) => request(`${path(programmeId)}/next`, (value) => { const item = record(value); return { next: parseNext(item.next), programme: parseProgramme(item.programme) } }, signal, { csrf })
export const attachStudioRun = (programmeId: string, csrf: string, body: { step_id: string; stage: StudioStage; run_id: string }, signal?: AbortSignal) => request(`${path(programmeId)}/attach`, parseProgramme, signal, { csrf, body })
export const changeStudio = (programmeId: string, csrf: string, change: 'pause' | 'resume' | 'stop', signal?: AbortSignal) => request(`${path(programmeId)}/${change}`, parseProgramme, signal, { csrf })

// Concept art (Meta's image model). The server builds the prompt from the saved
// design and reserves each image on the budget ledger before it is sent.
export const VIEWS = ['concept', 'in_use', 'schematic', 'detail', 'sequence', 'scale', 'origins'] as const
export type ArtView = (typeof VIEWS)[number]
export interface StudioImage { id: string; view: ArtView; status: 'rendering' | 'succeeded' | 'refused' | 'unknown'; error: string | null; mime: string | null; created_at: string }
function parseImage(value: unknown): StudioImage {
  const item = record(value)
  return { id: id(item.id), view: oneOf(item.view, VIEWS), status: oneOf(item.status, ['rendering', 'succeeded', 'refused', 'unknown'] as const), error: nullableText(item.error, 64), mime: nullableText(item.mime, 16), created_at: text(item.created_at, 64) }
}
export const studioImageUrl = (imageId: string) => { if (!UUID.test(imageId)) throw new StudioApiError('invalid_request'); return `${BASE}/images/${imageId}` }
export const listStudioImages = (programmeId: string, signal?: AbortSignal) => request(`${path(programmeId)}/images`, (value) => { const item = record(value); return { available: item.available === true, images: list(item.images, 40, parseImage), plan: list(item.plan, 7, (entry) => oneOf(entry, VIEWS)) } }, signal)
export const renderStudioImage = (programmeId: string, csrf: string, view?: ArtView, signal?: AbortSignal) => request(`${path(programmeId)}/images`, (value) => { const item = record(value); return { id: id(item.id), status: oneOf(item.status, ['rendering'] as const) } }, signal, { csrf, body: view ? { view } : undefined })
