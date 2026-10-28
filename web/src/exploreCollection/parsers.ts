import { parseStageCommitReceipt } from '../discovery/client'
import { parseResult } from '../research/client'
import type { MemoryReadReference } from '../research/types'
import type { StageCommitReceipt } from '../discovery/types'
import type { ExploreCollectionAccess, ExploreCollectionIntent, ExploreCollectionSummary, ExploreRankingReceipt, ExploreBridgeDetail, ExploreBridgeHistory, ExploreRankingHistory } from './types'
import { decimal, equal, hash, integer, invalid, list, member, modelStep, nullable, object, position, text, uuid } from './primitives'

const STATUSES = ['queued', 'running', 'waiting_retry', 'cancel_requested', 'cancelled', 'succeeded', 'failed', 'interrupted'] as const
export function parseExploreCollectionIntent(value: unknown): ExploreCollectionIntent {
  const item = object(value, ['protocol', 'max_bridges']); if (item.protocol !== 'polymath-explore-collection-v1') return invalid()
  return { protocol: 'polymath-explore-collection-v1', max_bridges: integer(item.max_bridges, 1, 4) }
}
/** Absence stays absent. Explicit null is never an implied opt-in/default. */
export function parseOptionalExploreIntent(value: unknown): ExploreCollectionIntent | undefined { return value === undefined ? undefined : parseExploreCollectionIntent(value) }
export function parseExploreCollectionAccess(value: unknown): ExploreCollectionAccess {
  const item = object(value, ['protocol', 'activation_sha256', 'explore_collection_intent']); if (item.protocol !== 'polymath-explore-collection-access-v1') return invalid()
  const sha = nullable(item.activation_sha256, hash), intent = nullable(item.explore_collection_intent, parseExploreCollectionIntent)
  if (sha === null && intent !== null) return invalid()
  return { protocol: 'polymath-explore-collection-access-v1', activation_sha256: sha, explore_collection_intent: intent }
}
export function parseReference(value: unknown): MemoryReadReference { const item = object(value, ['result_id', 'run_id', 'content_sha256']); return { result_id: uuid(item.result_id), run_id: uuid(item.run_id), content_sha256: hash(item.content_sha256) } }
export function referenceFor(stage: StageCommitReceipt): MemoryReadReference { return { result_id: stage.result_id, run_id: stage.run_id, content_sha256: stage.result_sha256 } }
export function parseExploreReceipt(value: unknown): StageCommitReceipt {
  let parsed: StageCommitReceipt; try { parsed = parseStageCommitReceipt(value) } catch { return invalid() }
  position(parsed.model_request_id, parsed.action_ordinal)
  if (parsed.depth !== 1 || parsed.parent_refs.length) return invalid()
  return parsed
}
export function validateExploreReceipts(stages: StageCommitReceipt[], runId: string, maximum = 4): void {
  if (stages.length > integer(maximum, 1, 4) || new Set(stages.map((stage) => stage.stage_id)).size !== stages.length || new Set(stages.map((stage) => stage.result_id)).size !== stages.length) return invalid()
  for (const [ordinal, stage] of stages.entries()) {
    if (stage.run_id !== runId || stage.ordinal !== ordinal || stage.depth !== 1 || stage.parent_refs.length || ordinal && (stage.action_ordinal <= stages[ordinal - 1].action_ordinal || modelStep(stage.model_request_id) <= modelStep(stages[ordinal - 1].model_request_id))) return invalid()
  }
}
export function parseExploreRankingReceipt(value: unknown): ExploreRankingReceipt {
  const item = object(value, ['ranking_id', 'run_id', 'packet_sha256', 'model_request_id', 'action_ordinal', 'covered_through_ordinal', 'ranked', 'excluded'])
  const runId = uuid(item.run_id), covered = nullable(item.covered_through_ordinal, (value) => integer(value, 0, 3))
  const ranked = list(item.ranked, (raw) => { const entry = object(raw, ['reference', 'rationale']); return { reference: parseReference(entry.reference), rationale: text(entry.rationale, 1500) } }, 4)
  const excluded = list(item.excluded, (raw) => { const entry = object(raw, ['reference', 'reason']); return { reference: parseReference(entry.reference), reason: text(entry.reason, 1500) } }, 4)
  const references = [...ranked, ...excluded].map((entry) => entry.reference)
  if (references.length !== (covered === null ? 0 : covered + 1) || references.some((reference) => reference.run_id !== runId) || new Set(references.map((reference) => reference.result_id)).size !== references.length) return invalid()
  return { ranking_id: uuid(item.ranking_id), run_id: runId, packet_sha256: hash(item.packet_sha256), ...position(item.model_request_id, item.action_ordinal), covered_through_ordinal: covered, ranked, excluded }
}
/** Only saved model order is used; confidence has no role in these checks. */
export function validateRankingCoverage(ranking: ExploreRankingReceipt, stages: StageCommitReceipt[], runId: string): void {
  if (ranking.run_id !== runId) return invalid()
  const count = ranking.covered_through_ordinal === null ? 0 : ranking.covered_through_ordinal + 1, visible = stages.slice(0, count)
  if (visible.length !== count || stages.slice(count).some((stage) => stage.action_ordinal < ranking.action_ordinal || modelStep(stage.model_request_id) < modelStep(ranking.model_request_id))) return invalid()
  const members = [...ranking.ranked, ...ranking.excluded]
  if (members.length !== visible.length || visible.some((stage) => stage.action_ordinal >= ranking.action_ordinal || modelStep(stage.model_request_id) >= modelStep(ranking.model_request_id) || !members.some((member) => equal(member.reference, referenceFor(stage))))) return invalid()
}
function history(value: unknown, runId: string): ExploreRankingReceipt[] {
  const rankings = list(value, parseExploreRankingReceipt, 16)
  if (new Set(rankings.map((ranking) => ranking.ranking_id)).size !== rankings.length || rankings.some((ranking, index) => ranking.run_id !== runId || index > 0 && (ranking.action_ordinal <= rankings[index - 1].action_ordinal || modelStep(ranking.model_request_id) <= modelStep(rankings[index - 1].model_request_id)))) return invalid()
  return rankings
}
export function parseExploreCollectionSummary(value: unknown): ExploreCollectionSummary {
  const item = object(value, ['run_id', 'run_status', 'explore_collection_intent', 'stages', 'rankings', 'selected_final_ranking', 'stop_reason', 'total_usage', 'accounting'])
  const runId = uuid(item.run_id), intent = parseExploreCollectionIntent(item.explore_collection_intent), stages = list(item.stages, parseExploreReceipt, 4), rankings = history(item.rankings, runId), final = nullable(item.selected_final_ranking, parseExploreRankingReceipt)
  validateExploreReceipts(stages, runId, intent.max_bridges)
  for (const ranking of rankings) validateRankingCoverage(ranking, stages, runId)
  const receipts = [...stages, ...rankings]; if (new Set(receipts.map((item) => item.action_ordinal)).size !== receipts.length || new Set(receipts.map((item) => item.model_request_id)).size !== receipts.length) return invalid()
  if (final && (!equal(final, rankings.at(-1)) || final.covered_through_ordinal !== (stages.length ? stages.length - 1 : null))) return invalid()
  const usage = object(item.total_usage, ['model_calls', 'input_tokens_reported', 'output_tokens_reported', 'calls_without_usage']), calls = integer(usage.model_calls, 0, 20), missing = integer(usage.calls_without_usage, 0, calls)
  if ([...stages, ...rankings].some((receipt) => modelStep(receipt.model_request_id) > calls)) return invalid()
  const accounting = object(item.accounting, ['counted_spend_microdollars', 'open_hold_microdollars', 'unknown_hold_microdollars']), open = decimal(accounting.open_hold_microdollars), unknown = decimal(accounting.unknown_hold_microdollars)
  if (BigInt(unknown) > BigInt(open)) return invalid()
  return { run_id: runId, run_status: member(item.run_status, STATUSES), explore_collection_intent: intent, stages, rankings, selected_final_ranking: final, stop_reason: nullable(item.stop_reason, (value) => text(value, 2000)), total_usage: { model_calls: calls, input_tokens_reported: decimal(usage.input_tokens_reported), output_tokens_reported: decimal(usage.output_tokens_reported), calls_without_usage: missing }, accounting: { counted_spend_microdollars: decimal(accounting.counted_spend_microdollars), open_hold_microdollars: open, unknown_hold_microdollars: unknown } }
}
export function parseExploreRankingHistory(value: unknown): ExploreRankingHistory { const item = object(value, ['run_id', 'rankings']), runId = uuid(item.run_id); return { run_id: runId, rankings: history(item.rankings, runId) } }
/** GET detail hydrates cited sources only; source/finding hash still identifies the original saved snapshot. */
export function parseExploreBridgeDetail(value: unknown): ExploreBridgeDetail {
  const item = object(value, ['stage', 'result', 'parent_edges', 'primary_paper_groups', 'status']), stage = parseExploreReceipt(item.stage)
  let result; try { result = parseResult(item.result) } catch { return invalid() }
  if (!item.result || typeof item.result !== 'object' || (item.result as Record<string, unknown>).mode !== 'explore' || result.status !== 'bridge' || result.mode !== 'explore' || result.finding?.outcome !== 'bridge' || result.field_selections?.length || result.overview_binding || !Array.isArray(item.parent_edges) || item.parent_edges.length || result.usage.model_calls !== modelStep(stage.model_request_id) || result.usage.calls_without_usage > result.usage.model_calls || result.events.length > 1000 || result.events.some((event, index) => event.sequence !== index + 1 || Array.from(event.summary).length > 2000 || event.kind.length > 64)) return invalid()
  const sources = new Map(result.sources.map((source) => [source.source_id, source])), remaining = [...result.quotes]
  if (sources.size !== result.sources.length || result.quotes.length < 2) return invalid()
  for (const citation of result.finding.evidence) {
    const source = sources.get(citation.source_id), index = remaining.findIndex((quote) => quote.source_id === citation.source_id && quote.side === citation.side && quote.quote === citation.quote && quote.supports === citation.supports)
    if (!source || index < 0) return invalid()
    const [quote] = remaining.splice(index, 1), characters = Array.from(source.text)
    if (quote.content_kind !== source.content_kind || characters.slice(quote.start, quote.end).join('') !== quote.quote || quote.end > characters.length || quote.end - quote.start !== Array.from(quote.quote).length) return invalid()
  }
  if (remaining.length || !result.quotes.some((quote) => quote.side === 'left') || !result.quotes.some((quote) => quote.side === 'right')) return invalid()
  const cited = [...new Set(result.quotes.map((quote) => quote.source_id))]
  if (cited.length !== sources.size) return invalid()
  const groups = list(item.primary_paper_groups, (raw) => { const group = object(raw, ['paper_id', 'source_ids']); return { paper_id: text(group.paper_id, 20), source_ids: list(group.source_ids, (value) => text(value, 200), 12, 1) } }, 12, 1), all = groups.flatMap((group) => group.source_ids)
  if (all.length !== cited.length || new Set(all).size !== all.length || all.some((source) => !cited.includes(source)) || groups.some((group, index) => group.paper_id !== `paper-${index + 1}` || group.source_ids.some((source, position) => position > 0 && cited.indexOf(source) <= cited.indexOf(group.source_ids[position - 1])) || index > 0 && cited.indexOf(group.source_ids[0]) <= cited.indexOf(groups[index - 1].source_ids[0]))) return invalid()
  const status = member(item.status, ['current_supported', 'unsupported', 'superseded', 'needs_review'] as const)
  if (status === 'current_supported' && groups.length < 2) return invalid()
  return { stage, result, parent_edges: [], primary_paper_groups: groups, status }
}
export function parseExploreBridgeHistory(value: unknown): ExploreBridgeHistory { const item = object(value, ['run_id', 'stages']), runId = uuid(item.run_id), stages = list(item.stages, parseExploreBridgeDetail, 4); validateExploreReceipts(stages.map((detail) => detail.stage), runId); return { run_id: runId, stages } }
export function bridgeMatchesReceipt(detail: ExploreBridgeDetail, receipt: StageCommitReceipt): boolean { return equal(detail.stage, receipt) }
