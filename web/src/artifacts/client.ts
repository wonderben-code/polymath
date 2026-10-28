import { parseActivity, ResearchApiError } from '../research/client'
import { parseBibliography } from '../research/bibliography'
import { nativeSubmissionHeaders, nativeAdmissionFailure, parseNativeRunControl, validNativeSubmission } from '../research/nativeAdmission'
import type { NativeSubmissionContext } from '../research/nativeAdmission'
import { parseSourceCoverage, validateSourceCoverageContext } from '../research/sourceCoverage'
import type { ArtifactHistory, ArtifactJob, ArtifactKind, ArtifactPassage, ArtifactResult, CitedText, DocumentDraft, Expression, FiniteCheckResult, FiniteConjecture, FormalisationDraft, Relation, SavedArtifact } from './types'

const UUID = /^[a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12}$/
const HASH = /^[a-f\d]{64}$/
const VARIABLE = /^[a-z][a-z0-9_]{0,15}$/
const KINDS = ['formalise', 'write_summary', 'write_paper'] as const
const STATUSES = ['ready', 'insufficient_evidence', 'failed', 'limit_reached'] as const
const JOB_STATUSES = ['queued', 'running', 'waiting_retry', 'cancel_requested', 'cancelled', 'succeeded', 'failed', 'interrupted'] as const
const MAX_BYTES = 2 * 1024 * 1024

function invalid(): never { throw new ResearchApiError('invalid_response') }
function record(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return invalid()
  return value as Record<string, unknown>
}
function text(value: unknown, maximum = 4000, required = true): string {
  if (typeof value !== 'string' || Array.from(value).length > maximum || required && !value.trim() || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\uD800-\uDFFF]/u.test(value)) return invalid()
  return value
}
function integer(value: unknown, minimum = 0, maximum = Number.MAX_SAFE_INTEGER): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < minimum || value > maximum) return invalid()
  return value
}
function list<T>(value: unknown, parse: (item: unknown) => T, minimum: number, maximum: number): T[] {
  if (!Array.isArray(value) || value.length < minimum || value.length > maximum) return invalid()
  return value.map(parse)
}
function member<T extends string>(value: unknown, values: readonly T[]): T { return typeof value === 'string' && values.includes(value as T) ? value as T : invalid() }
function id(value: unknown): string { const parsed = text(value, 36); return UUID.test(parsed) ? parsed : invalid() }
function nullableId(value: unknown): string | null { return value === null ? null : id(value) }
function hash(value: unknown): string { const parsed = text(value, 64); return HASH.test(parsed) ? parsed : invalid() }
function date(value: unknown): string { const parsed = text(value, 100); return Number.isFinite(Date.parse(parsed)) ? parsed : invalid() }
function nullableText(value: unknown, maximum: number): string | null { return value === null ? null : text(value, maximum) }
function code(value: unknown): string | null { const parsed = nullableText(value, 100); return parsed === null || /^[a-z_]{1,100}$/.test(parsed) ? parsed : invalid() }
function inputId(value: unknown): string { if (typeof value !== 'string' || !UUID.test(value)) throw new ResearchApiError('invalid_request'); return value }
function distinct<T>(values: T[]): T[] { return new Set(values).size === values.length ? values : invalid() }

export function parseArtifactJob(value: unknown): ArtifactJob {
  const job = record(value)
  const native = parseNativeRunControl(job.native_research, invalid)
  if (job.result_id !== null || typeof job.external_outcome_unknown !== 'boolean') return invalid()
  return {
    id: id(job.id), mode: member(job.mode, ['explore', 'ask', 'surprise']), task_kind: member(job.task_kind, KINDS),
    goal: text(job.goal, 4000, false), status: member(job.status, JOB_STATUSES), created_at: date(job.created_at), updated_at: date(job.updated_at),
    result_id: null, artifact_id: nullableId(job.artifact_id), error_code: code(job.error_code), external_outcome_unknown: job.external_outcome_unknown,
    ...(native ? { native_research: native } : {}),
  }
}

function cited(value: unknown): CitedText {
  const item = record(value)
  return { text: text(item.text, 2000), label: member(item.label, ['known', 'postulated', 'speculative']), citation_ordinals: distinct(list(item.citation_ordinals, (ordinal) => integer(ordinal, 0, 63), 1, 8)) }
}
function document(value: Record<string, unknown>): DocumentDraft {
  const sections = list(value.sections, (item) => {
    const section = record(item)
    return { heading: text(section.heading, 100), paragraphs: list(section.paragraphs, cited, 1, 4) }
  }, 2, 8)
  const headings = distinct(sections.map((item) => item.heading.trim().replace(/\s+/gu, ' ').toLowerCase()))
  const kind = member(value.kind, ['summary', 'paper'])
  if (kind === 'paper' && ['background', 'findings', 'evidence', 'limitations'].some((heading) => !headings.includes(heading))) return invalid()
  return { kind, title: text(value.title, 300), abstract: list(value.abstract, cited, 1, 3), sections, limitations: list(value.limitations, (item) => text(item, 1000), 1, 8) }
}
function variableName(value: unknown): string { const parsed = text(value, 16); return VARIABLE.test(parsed) ? parsed : invalid() }
function conjecture(value: unknown): FiniteConjecture {
  const item = record(value)
  const variables = list(item.variables, (value) => {
    const variable = record(value), lower = integer(variable.lower, -20, 20), upper = integer(variable.upper, -20, 20)
    if (lower > upper) return invalid()
    return { name: variableName(variable.name), lower, upper }
  }, 1, 3)
  const names = new Set(distinct(variables.map((variable) => variable.name)))
  if (variables.reduce((count, variable) => count * (variable.upper - variable.lower + 1), 1) > 4096) return invalid()
  let nodes = 0
  function expression(value: unknown, depth = 1): Expression {
    if (++nodes > 64 || depth > 8) return invalid()
    const node = record(value)
    switch (node.op) {
      case 'constant': return { op: 'constant', value: integer(node.value, -1000, 1000) }
      case 'variable': {
        const name = variableName(node.name)
        return names.has(name) ? { op: 'variable', name } : invalid()
      }
      case 'add': case 'subtract': case 'multiply': return { op: node.op, left: expression(node.left, depth + 1), right: expression(node.right, depth + 1) }
      case 'power': return { op: 'power', base: expression(node.base, depth + 1), exponent: integer(node.exponent, 0, 6) }
      default: return invalid()
    }
  }
  function relation(value: unknown): Relation {
    const relation = record(value)
    return { left: expression(relation.left), operator: member(relation.operator, ['eq', 'lt', 'le']), right: expression(relation.right) }
  }
  return { variables, assumptions: list(item.assumptions, relation, 0, 8), conclusion: relation(item.conclusion) }
}
function formalisation(value: Record<string, unknown>): FormalisationDraft {
  const definitions = list(value.definitions, (value) => {
    const definition = record(value)
    return { symbol: text(definition.symbol, 80), meaning: cited(definition.meaning) }
  }, 1, 8)
  distinct(definitions.map((item) => item.symbol.trim().replace(/\s+/gu, ' ').toLowerCase()))
  const proposed = cited(value.conjecture), attempts = list(value.proof_attempt, cited, 1, 8)
  if (proposed.label === 'known' || attempts.some((attempt) => attempt.label === 'known')) return invalid()
  return {
    kind: 'formalisation', title: text(value.title, 300), definitions, assumptions: list(value.assumptions, cited, 1, 8),
    conjecture: proposed, proof_attempt: attempts, limitations: list(value.limitations, (item) => text(item, 1000), 1, 8),
    finite_conjecture: value.finite_conjecture === null ? null : conjecture(value.finite_conjecture),
  }
}
function decimal(value: unknown): string | null {
  if (value === null) return null
  const parsed = text(value, 79)
  if (!/^(?:0|-?[1-9]\d*)$/.test(parsed) || (BigInt(parsed) < 0n ? -BigInt(parsed) : BigInt(parsed)) >= 2n ** 256n) return invalid()
  return parsed
}
function check(value: unknown): FiniteCheckResult {
  const item = record(value), encoded = conjecture(item.conjecture)
  const status = member(item.status, ['counterexample_found', 'no_counterexample_in_domain', 'no_applicable_cases', 'limit_reached'])
  const tested = integer(item.assignments_tested, 0, 4096), applicable = integer(item.applicable_cases, 0, 4096)
  const total = integer(item.total_assignments, 1, 4096)
  const expectedTotal = encoded.variables.reduce((count, variable) => count * (variable.upper - variable.lower + 1), 1)
  const lhs = decimal(item.lhs), rhs = decimal(item.rhs)
  if (applicable > tested || tested > total || total !== expectedTotal || item.scope !== 'finite_integer_domain' || item.proof_status !== 'not_a_general_proof') return invalid()
  const error = item.error_code === null ? null : member(item.error_code, ['integer_size_limit', 'evaluation_limit'])
  let witness: Record<string, number> | null = null
  if (status === 'counterexample_found') {
    const raw = record(item.witness)
    if (!applicable || lhs === null || rhs === null || error !== null || Object.keys(raw).length !== encoded.variables.length) return invalid()
    witness = Object.fromEntries(encoded.variables.map((variable) => [variable.name, integer(raw[variable.name], variable.lower, variable.upper)]))
  } else if (item.witness !== null || lhs !== null || rhs !== null) return invalid()
  if (status === 'limit_reached' ? error === null : error !== null) return invalid()
  if ((status === 'no_counterexample_in_domain' || status === 'no_applicable_cases') && (tested !== total || (status === 'no_applicable_cases') !== (applicable === 0))) return invalid()
  return { conjecture: encoded, conjecture_sha256: hash(item.conjecture_sha256), status, assignments_tested: tested, applicable_cases: applicable, total_assignments: total, witness, lhs, rhs, error_code: error, scope: 'finite_integer_domain', proof_status: 'not_a_general_proof' }
}
function result(value: unknown): ArtifactResult {
  const item = record(value), kind = member(item.kind, KINDS), status = member(item.status, STATUSES)
  const rawDraft = item.draft === null ? null : record(item.draft)
  const draft = rawDraft === null ? null : rawDraft.kind === 'formalisation' ? formalisation(rawDraft) : document(rawDraft)
  if (status === 'ready' ? !draft || (kind === 'formalise' ? draft.kind !== 'formalisation' : draft.kind !== (kind === 'write_summary' ? 'summary' : 'paper')) : draft !== null) return invalid()
  if (item.validation_scope !== 'citation_integrity_and_bounded_checks') return invalid()
  const reason = nullableText(item.reason, 3000)
  if (status === 'insufficient_evidence' && reason === null) return invalid()
  const usage = record(item.usage)
  const events = list(item.events, parseActivity, 0, 100)
  if (events.some((event, index) => event.sequence !== index + 1)) return invalid()
  return {
    kind, status, draft, checks: list(item.checks, check, 0, 3), events,
    usage: { model_calls: integer(usage.model_calls), input_tokens_reported: integer(usage.input_tokens_reported), output_tokens_reported: integer(usage.output_tokens_reported), calls_without_usage: integer(usage.calls_without_usage) },
    error_code: code(item.error_code), reason, validation_scope: 'citation_integrity_and_bounded_checks',
  }
}
function passage(value: unknown): ArtifactPassage {
  const item = record(value), url = text(item.url, 2000)
  try { const parsed = new URL(url); if (parsed.protocol !== 'https:' || parsed.username || parsed.password) return invalid() } catch { return invalid() }
  const claimId = item.claim_id === null ? null : text(item.claim_id, 64)
  if (claimId !== null && !/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(claimId)) return invalid()
  const quoted = text(item.quote, 4000)
  if (Array.from(quoted).length < 10) return invalid()
  const bibliography = parseBibliography(item.bibliography, invalid), coverage = parseSourceCoverage(item.coverage, invalid)
  const parsed: ArtifactPassage = { ordinal: integer(item.ordinal, 0, 63), source_id: text(item.source_id, 200), title: text(item.title, 1000), url, content_sha256: hash(item.content_sha256), content_kind: member(item.content_kind, ['abstract', 'full_text']), quote: quoted, supports: text(item.supports, 1500), claim_id: claimId, ...(bibliography === undefined ? {} : { bibliography }), ...(coverage === undefined ? {} : { coverage }) }
  validateSourceCoverageContext(parsed, invalid)
  if (coverage && Array.from(quoted).length > coverage.end - coverage.start) return invalid()
  return parsed
}
function paragraphs(draft: NonNullable<ArtifactResult['draft']>): CitedText[] {
  return draft.kind === 'formalisation' ? [...draft.definitions.map((item) => item.meaning), ...draft.assumptions, draft.conjecture, ...draft.proof_attempt] : [...draft.abstract, ...draft.sections.flatMap((section) => section.paragraphs)]
}
export function parseArtifact(value: unknown): SavedArtifact {
  const item = record(value), kind = member(item.kind, KINDS), status = member(item.status, STATUSES)
  const payload = result(item.payload), passages = list(item.passages, passage, 1, 64)
  const formalisationId = nullableId(item.formalisation_id)
  const formalisationChecks = item.formalisation_checks === undefined ? [] : list(item.formalisation_checks, check, 0, 3)
  if (formalisationId === null && formalisationChecks.length) return invalid()
  const ordinals = new Set(distinct(passages.map((item) => item.ordinal)))
  if (payload.kind !== kind || payload.status !== status) return invalid()
  if (payload.draft && paragraphs(payload.draft).some((paragraph) => paragraph.citation_ordinals.some((ordinal) => !ordinals.has(ordinal)))) return invalid()
  const finalEncoding = payload.draft?.kind === 'formalisation' ? payload.draft.finite_conjecture : null
  if (finalEncoding !== null && !payload.checks.some((check) => JSON.stringify(check.conjecture) === JSON.stringify(finalEncoding))) return invalid()
  return {
    id: id(item.id), run_id: id(item.run_id), kind, status, created_at: date(item.created_at), source_result_id: id(item.source_result_id), source_run_id: id(item.source_run_id), source_sha256: hash(item.source_sha256), formalisation_id: formalisationId, formalisation_checks: formalisationChecks,
    passages, payload, markdown: text(item.markdown, 262144, false), markdown_sha256: hash(item.markdown_sha256), payload_sha256: hash(item.payload_sha256), basis_sha256: hash(item.basis_sha256),
  }
}

async function json(response: Response): Promise<unknown> {
  const reader = response.body?.getReader()
  if (!reader) return invalid()
  const chunks: Uint8Array[] = []
  let length = 0
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      length += value.byteLength
      if (length > MAX_BYTES) { await reader.cancel(); return invalid() }
      chunks.push(value)
    }
    const bytes = new Uint8Array(length)
    let offset = 0
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length }
    return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)) as unknown
  } catch { return invalid() }
}
async function request(path: string, signal?: AbortSignal, write?: { csrf: string; body?: unknown; native?: NativeSubmissionContext }): Promise<unknown> {
  const controller = new AbortController(), abort = () => controller.abort()
  signal?.addEventListener('abort', abort, { once: true })
  if (signal?.aborted) controller.abort()
  const timeout = window.setTimeout(abort, 10000)
  try {
    if (write && !HASH.test(write.csrf)) throw new ResearchApiError('session_changed')
    if (write?.native && !validNativeSubmission(write.native)) throw new ResearchApiError('native_authority_changed')
    const response = await fetch(path, {
      signal: controller.signal, credentials: 'same-origin', cache: 'no-store', redirect: 'error', method: write ? 'POST' : 'GET',
      headers: { Accept: 'application/json', ...(write ? { 'X-CSRF-Token': write.csrf, 'Content-Type': 'application/json', ...(write.native ? nativeSubmissionHeaders(write.native) : {}) } : {}) },
      ...(write?.body === undefined ? {} : { body: JSON.stringify(write.body) }),
    })
    if (!response.ok && [401, 403, 409, 503].includes(response.status)) {
      let detail: unknown
      try { detail = record(await json(response.clone())).detail } catch { /* Do not display untrusted server details. */ }
      if (nativeAdmissionFailure(response.status, detail)) throw new ResearchApiError('native_authority_changed')
    }
    if (response.status === 401) throw new ResearchApiError('session_expired')
    if (response.status === 403) throw new ResearchApiError('session_changed')
    if (response.status === 404) throw new ResearchApiError('run_unavailable')
    if (response.status === 422) throw new ResearchApiError('invalid_request')
    if (response.status === 429) { const retry = response.headers.get('retry-after'), seconds = retry && /^\d{1,5}$/.test(retry) && Number(retry) <= 86400 ? Number(retry) : null; throw new ResearchApiError(seconds === null ? 'queue_limit' : 'resource_limited', seconds) }
    if (!response.ok) throw new ResearchApiError('unavailable')
    return await json(response)
  } catch (error) {
    if (error instanceof ResearchApiError) throw error
    throw new ResearchApiError(signal?.aborted ? 'aborted' : 'connection_failed')
  } finally { signal?.removeEventListener('abort', abort); window.clearTimeout(timeout) }
}

export async function listArtifactJobs(sourceResultId: string, signal?: AbortSignal, before?: string): Promise<ArtifactHistory> {
  const query = new URLSearchParams({ source_result_id: inputId(sourceResultId) })
  if (before) query.set('before', inputId(before))
  const payload = record(await request(`/api/v1/artifacts/jobs?${query}`, signal))
  const jobs = list(payload.jobs, parseArtifactJob, 0, 50)
  distinct(jobs.map((job) => job.id))
  return { jobs, next_cursor: nullableId(payload.next_cursor) }
}
export async function getArtifactJob(runId: string, signal?: AbortSignal): Promise<ArtifactJob> {
  const job = parseArtifactJob(await request(`/api/v1/artifacts/jobs/${inputId(runId)}`, signal))
  return job.id === runId ? job : invalid()
}
export async function createArtifactJob(kind: ArtifactKind, sourceResultId: string, csrf: string, signal?: AbortSignal, formalisationId?: string, native?: NativeSubmissionContext): Promise<ArtifactJob> {
  if (!KINDS.includes(kind) || kind === 'formalise' && formalisationId !== undefined) throw new ResearchApiError('invalid_request')
  const body = { kind, source_result_id: inputId(sourceResultId), ...(formalisationId === undefined ? {} : { formalisation_id: inputId(formalisationId) }), ...(native ? { expected_activation_sha256: native.expectedActivationSha256 } : {}) }
  const job = parseArtifactJob(await request('/api/v1/artifacts/jobs', signal, { csrf, native, body }))
  return job.task_kind === kind ? job : invalid()
}
export async function cancelArtifactJob(runId: string, csrf: string, signal?: AbortSignal): Promise<ArtifactJob> {
  const job = parseArtifactJob(await request(`/api/v1/runs/${inputId(runId)}/cancel`, signal, { csrf }))
  return job.id === runId ? job : invalid()
}
export async function getArtifact(artifactId: string, signal?: AbortSignal): Promise<SavedArtifact> {
  const artifact = parseArtifact(await request(`/api/v1/artifacts/${inputId(artifactId)}`, signal))
  return artifact.id === artifactId ? artifact : invalid()
}
export function exportUrl(artifactId: string): string { return `/api/v1/artifacts/${inputId(artifactId)}/export` }
