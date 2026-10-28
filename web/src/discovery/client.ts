import { parseResult } from '../research/client'
import type { MemoryReadReference } from '../research/types'
import type { DiscoveryIntent, DiscoveryStage, DiscoveryStageHistory, DiscoverySummary, StageAssessment, StageCommitReceipt, StageParentEdge } from './types'

export class DiscoveryError extends Error { constructor(readonly code: string) { super(code); this.name = 'DiscoveryError' } }
const HASH = /^[a-f\d]{64}$/, UUID = /^[a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12}$/
const DETAIL_BYTES = 4 * 1024 * 1024, LIST_BYTES = 16 * 1024 * 1024
function invalid(): never { throw new DiscoveryError('invalid_response') }
function object(value: unknown, required: string[]): Record<string, unknown> { if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid(); const item = value as Record<string, unknown>; if (required.some((key) => !Object.hasOwn(item, key)) || Object.keys(item).some((key) => !required.includes(key))) return invalid(); return item }
function text(value: unknown, maximum: number): string { return typeof value === 'string' && !!value.trim() && Array.from(value).length <= maximum && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\uD800-\uDFFF]/u.test(value) ? value : invalid() }
function integer(value: unknown, minimum = 0, maximum = Number.MAX_SAFE_INTEGER): number { return typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum && value <= maximum ? value : invalid() }
function member<T extends string>(value: unknown, choices: readonly T[]): T { return typeof value === 'string' && choices.includes(value as T) ? value as T : invalid() }
function hash(value: unknown): string { return typeof value === 'string' && HASH.test(value) ? value : invalid() }
function id(value: unknown): string { return typeof value === 'string' && UUID.test(value) ? value : invalid() }
function list<T>(value: unknown, parse: (value: unknown) => T, minimum = 0, maximum = 4): T[] { return Array.isArray(value) && value.length >= minimum && value.length <= maximum ? value.map(parse) : invalid() }
function nullable<T>(value: unknown, parse: (value: unknown) => T): T | null { return value === null ? null : parse(value) }
function decimal(value: unknown): string { return typeof value === 'string' && /^(?:0|[1-9]\d{0,999})$/.test(value) ? value : invalid() }
function reference(value: unknown): MemoryReadReference { const item = object(value, ['result_id', 'run_id', 'content_sha256']); return { result_id: id(item.result_id), run_id: id(item.run_id), content_sha256: hash(item.content_sha256) } }
function equalReference(left: MemoryReadReference, right: MemoryReadReference): boolean { return left.result_id === right.result_id && left.run_id === right.run_id && left.content_sha256 === right.content_sha256 }

export function parseDiscoveryIntent(value: unknown): DiscoveryIntent {
  const item = object(value, ['protocol', 'max_stages', 'max_depth'])
  if (item.protocol !== 'polymath-surprise-ladder-v1') return invalid()
  return { protocol: 'polymath-surprise-ladder-v1', max_stages: integer(item.max_stages, 1, 4), max_depth: integer(item.max_depth, 1, 3) }
}
export function parseStageAssessment(value: unknown): StageAssessment {
  const item = object(value, ['novelty', 'novelty_rationale', 'confidence', 'confidence_score', 'confidence_scope', 'confidence_rationale', 'limitations'])
  if (item.confidence_scope !== 'uncalibrated_model_judgement') return invalid()
  return { novelty: member(item.novelty, ['known', 'partly_known', 'not_found_in_bounded_search', 'uncertain']), novelty_rationale: text(item.novelty_rationale, 2000), confidence: member(item.confidence, ['tentative', 'moderate', 'strong']), confidence_score: integer(item.confidence_score, 0, 100), confidence_scope: 'uncalibrated_model_judgement', confidence_rationale: text(item.confidence_rationale, 2000), limitations: list(item.limitations, (value) => text(value, 1000), 1, 8) }
}
export function parseStageCommitReceipt(value: unknown): StageCommitReceipt {
  const item = object(value, ['stage_id', 'run_id', 'ordinal', 'result_id', 'result_sha256', 'depth', 'assessment', 'parent_refs', 'action_ordinal', 'model_request_id']), parents = list(item.parent_refs, reference), resultId = id(item.result_id)
  if (![0, 2, 3, 4].includes(parents.length) || new Set(parents.map((parent) => parent.result_id)).size !== parents.length || parents.some((parent) => parent.result_id === resultId) || typeof item.model_request_id !== 'string' || !/^model-[0-9]{4}$/.test(item.model_request_id)) return invalid()
  const depth = integer(item.depth, 1, 3)
  if (!parents.length && depth !== 1) return invalid()
  return { stage_id: id(item.stage_id), run_id: id(item.run_id), ordinal: integer(item.ordinal, 0, 3), result_id: resultId, result_sha256: hash(item.result_sha256), depth, assessment: parseStageAssessment(item.assessment), parent_refs: parents, action_ordinal: integer(item.action_ordinal, 1, 20), model_request_id: item.model_request_id }
}
function validateReceipts(stages: StageCommitReceipt[], runId: string, limits: DiscoveryIntent = { protocol: 'polymath-surprise-ladder-v1', max_stages: 4, max_depth: 3 }): void {
  if (stages.length > limits.max_stages || new Set(stages.map((stage) => stage.stage_id)).size !== stages.length || new Set(stages.map((stage) => stage.result_id)).size !== stages.length) return invalid()
  const observed = new Map<string, StageCommitReceipt>()
  for (const [ordinal, stage] of stages.entries()) {
    if (stage.run_id !== runId || stage.ordinal !== ordinal || stage.depth > limits.max_depth || ordinal > 0 && (stage.action_ordinal <= stages[ordinal - 1].action_ordinal || stage.model_request_id <= stages[ordinal - 1].model_request_id)) return invalid()
    let localDepth = 1
    for (const parent of stage.parent_refs) if (parent.run_id === runId) { const prior = observed.get(parent.result_id); if (!prior || prior.result_sha256 !== parent.content_sha256) return invalid(); localDepth = Math.max(localDepth, prior.depth + 1) }
    if (stage.depth !== localDepth) return invalid()
    observed.set(stage.result_id, stage)
  }
}
export function parseDiscoverySummary(value: unknown): DiscoverySummary {
  const item = object(value, ['run_id', 'run_status', 'discovery_intent', 'stages', 'selected_final_stage', 'stop_reason', 'total_usage', 'accounting']), runId = id(item.run_id), intent = parseDiscoveryIntent(item.discovery_intent), stages = list(item.stages, parseStageCommitReceipt), selected = nullable(item.selected_final_stage, parseStageCommitReceipt), usage = object(item.total_usage, ['model_calls', 'input_tokens_reported', 'output_tokens_reported', 'calls_without_usage']), accounting = object(item.accounting, ['counted_spend_microdollars', 'open_hold_microdollars', 'unknown_hold_microdollars'])
  validateReceipts(stages, runId, intent)
  if (selected && !stages.some((stage) => JSON.stringify(stage) === JSON.stringify(selected))) return invalid()
  const calls = integer(usage.model_calls, 0, 20), missing = integer(usage.calls_without_usage, 0, 20), open = decimal(accounting.open_hold_microdollars), unknown = decimal(accounting.unknown_hold_microdollars)
  if (calls < stages.length || missing > calls || BigInt(unknown) > BigInt(open)) return invalid()
  return { run_id: runId, run_status: member(item.run_status, ['queued', 'running', 'waiting_retry', 'cancel_requested', 'cancelled', 'succeeded', 'failed', 'interrupted']), discovery_intent: intent, stages, selected_final_stage: selected, stop_reason: nullable(item.stop_reason, (value) => text(value, 2000)), total_usage: { model_calls: calls, input_tokens_reported: decimal(usage.input_tokens_reported), output_tokens_reported: decimal(usage.output_tokens_reported), calls_without_usage: missing }, accounting: { counted_spend_microdollars: decimal(accounting.counted_spend_microdollars), open_hold_microdollars: open, unknown_hold_microdollars: unknown } }
}
export function parseDiscoveryStage(value: unknown): DiscoveryStage {
  const item = object(value, ['stage', 'result', 'parent_edges', 'primary_paper_groups', 'status']), stage = parseStageCommitReceipt(item.stage)
  let result
  try { result = parseResult(item.result) } catch { return invalid() }
  if (result.status !== 'bridge' || result.mode !== 'surprise' || result.finding?.outcome !== 'bridge' || !result.field_selections?.length || result.quotes.length < 2) return invalid()
  const sourceMap = new Map(result.sources.map((source) => [source.source_id, source])), remaining = [...result.quotes]
  if (sourceMap.size !== result.sources.length) return invalid()
  for (const citation of result.finding.evidence) {
    const source = sourceMap.get(citation.source_id), index = remaining.findIndex((quote) => quote.source_id === citation.source_id && quote.side === citation.side && quote.quote === citation.quote && quote.supports === citation.supports)
    if (!source || index < 0) return invalid()
    const [quote] = remaining.splice(index, 1), characters = Array.from(source.text)
    if (!HASH.test(quote.source_sha256) || quote.content_kind !== source.content_kind || quote.end > characters.length || quote.end - quote.start !== Array.from(quote.quote).length || characters.slice(quote.start, quote.end).join('') !== quote.quote) return invalid()
  }
  const cited = [...new Set(result.quotes.map((quote) => quote.source_id))]
  if (remaining.length || cited.length !== sourceMap.size || !result.quotes.some((quote) => quote.side === 'left') || !result.quotes.some((quote) => quote.side === 'right')) return invalid()
  const groups = list(item.primary_paper_groups, (raw) => { const group = object(raw, ['paper_id', 'source_ids']); return { paper_id: text(group.paper_id, 20), source_ids: list(group.source_ids, (value) => text(value, 200), 1, 12) } }, 1, 12), flattened = groups.flatMap((group) => group.source_ids)
  if (flattened.length !== cited.length || new Set(flattened).size !== flattened.length || flattened.some((sourceId) => !cited.includes(sourceId)) || groups.some((group, index) => group.paper_id !== `paper-${index + 1}` || group.source_ids.some((sourceId, position) => position > 0 && cited.indexOf(sourceId) <= cited.indexOf(group.source_ids[position - 1])) || index > 0 && cited.indexOf(group.source_ids[0]) <= cited.indexOf(groups[index - 1].source_ids[0]))) return invalid()
  const status = member(item.status, ['current_supported', 'unsupported', 'superseded', 'needs_review'] as const)
  if (status === 'current_supported' && groups.length < 2) return invalid()
  const edges = list(item.parent_edges, (value): StageParentEdge => { const edge = object(value, ['kind', 'ordinal', 'reference', 'stage_id']); return { kind: member(edge.kind, ['support', 'revision']), ordinal: integer(edge.ordinal, 0, 4), reference: reference(edge.reference), stage_id: nullable(edge.stage_id, id) } }, 0, 5)
  const support = edges.filter((edge) => edge.kind === 'support'), revision = edges.filter((edge) => edge.kind === 'revision')
  if (support.length !== stage.parent_refs.length || revision.length > 1 || edges.some((edge, ordinal) => edge.ordinal !== ordinal || edge.kind !== (ordinal < support.length ? 'support' : 'revision') || edge.reference.result_id === stage.result_id || edge.stage_id === stage.stage_id || (edge.reference.run_id === stage.run_id) !== (edge.stage_id !== null))) return invalid()
  for (const [ordinal, edge] of support.entries()) if (!equalReference(edge.reference, stage.parent_refs[ordinal]) || !result.memory_reads?.some((read) => equalReference(read, edge.reference))) return invalid()
  return { stage, result, parent_edges: edges, primary_paper_groups: groups, status }
}
export function parseDiscoveryStageHistory(value: unknown): DiscoveryStageHistory {
  const item = object(value, ['run_id', 'stages']), runId = id(item.run_id), stages = list(item.stages, parseDiscoveryStage)
  validateReceipts(stages.map((detail) => detail.stage), runId)
  const byResult = new Map(stages.map((detail) => [detail.stage.result_id, detail.stage]))
  for (const detail of stages) for (const edge of detail.parent_edges) if (edge.stage_id !== null) { const prior = byResult.get(edge.reference.result_id); if (!prior || prior.ordinal >= detail.stage.ordinal || prior.stage_id !== edge.stage_id || prior.result_sha256 !== edge.reference.content_sha256) return invalid() }
  return { run_id: runId, stages }
}
export function stageMatchesReceipt(detail: DiscoveryStage, receipt: StageCommitReceipt): boolean {
  const canonical = (value: unknown): string => JSON.stringify(value, (_key, item: unknown) => item && typeof item === 'object' && !Array.isArray(item) ? Object.fromEntries(Object.entries(item).sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)) : item)
  return canonical(detail.stage) === canonical(receipt)
}
async function json(response: Response, maximum: number): Promise<unknown> {
  const size = response.headers.get('content-length')
  if (size !== null && (!/^\d+$/.test(size) || Number(size) > maximum)) return invalid()
  const reader = response.body?.getReader(); if (!reader) return invalid()
  const chunks: Uint8Array[] = []; let length = 0
  try { while (true) { const chunk = await reader.read(); if (chunk.done) break; length += chunk.value.byteLength; if (length > maximum) { await reader.cancel(); return invalid() }; chunks.push(chunk.value) }; const bytes = new Uint8Array(length); let offset = 0; for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length }; return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)) as unknown } catch { return invalid() } finally { reader.releaseLock() }
}
async function request<T>(path: string, parse: (value: unknown) => T, signal?: AbortSignal, maximum = DETAIL_BYTES): Promise<T> {
  if (signal?.aborted) throw new DiscoveryError('aborted')
  const controller = new AbortController(), abort = () => controller.abort(); signal?.addEventListener('abort', abort, { once: true }); const timer = window.setTimeout(abort, 10000)
  try {
    const response = await fetch(path, { method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error', referrerPolicy: 'no-referrer', signal: controller.signal, headers: { Accept: 'application/json' } })
    if (response.status === 401 || response.status === 403) throw new DiscoveryError('session_expired')
    if (!response.ok) throw new DiscoveryError(response.status === 404 ? 'discovery_not_found' : 'unavailable')
    const value = await json(response, maximum)
    if (controller.signal.aborted) throw new DiscoveryError(signal?.aborted ? 'aborted' : 'unavailable')
    return parse(value)
  } catch (caught) { if (caught instanceof DiscoveryError) throw caught; throw new DiscoveryError(signal?.aborted ? 'aborted' : controller.signal.aborted ? 'unavailable' : 'connection_failed') }
  finally { signal?.removeEventListener('abort', abort); window.clearTimeout(timer) }
}
function inputId(value: string): string { if (typeof value !== 'string' || !UUID.test(value)) throw new DiscoveryError('invalid_request'); return value }
const base = (runId: string) => `/api/v1/runs/${inputId(runId)}/discovery`
export async function getDiscoverySummary(runId: string, signal?: AbortSignal): Promise<DiscoverySummary> { return request(base(runId), (value) => { const summary = parseDiscoverySummary(value); return summary.run_id === runId ? summary : invalid() }, signal) }
export async function listDiscoveryStages(runId: string, signal?: AbortSignal): Promise<DiscoveryStageHistory> { return request(`${base(runId)}/stages`, (value) => { const history = parseDiscoveryStageHistory(value); return history.run_id === runId ? history : invalid() }, signal, LIST_BYTES) }
export async function getDiscoveryStage(runId: string, stageId: string, signal?: AbortSignal): Promise<DiscoveryStage> { return request(`${base(runId)}/stages/${inputId(stageId)}`, (value) => { const detail = parseDiscoveryStage(value); return detail.stage.run_id === runId && detail.stage.stage_id === stageId ? detail : invalid() }, signal) }
