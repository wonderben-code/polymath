import type { StageCommitReceipt } from '../discovery/types'
import type { ExploreCollectionAccess, ExploreCollectionIntent, ExploreCollectionSummary, ExploreRankingReceipt } from './types'
import { hash, integer, invalid, uuid } from './primitives'

export interface RankedCard { receipt: StageCommitReceipt; position: number; rationale: string }
export interface ExcludedCard { receipt: StageCommitReceipt; reason: string }
export interface CollectionPresentation { ranking: ExploreRankingReceipt | null; isExplicitFinal: boolean; ranked: RankedCard[]; excluded: ExcludedCard[]; outsideRanking: StageCommitReceipt[]; coverageCount: number }
/** Pure projection of the saved model decision. No scientific score or title is calculated. */
export function collectionPresentation(summary: ExploreCollectionSummary, rankingId?: string): CollectionPresentation {
  const ranking = rankingId === undefined ? summary.selected_final_ranking ?? summary.rankings.at(-1) ?? null : summary.rankings.find((item) => item.ranking_id === rankingId) ?? invalid()
  const byResult = new Map(summary.stages.map((receipt) => [receipt.result_id, receipt]))
  const count = ranking?.covered_through_ordinal === null || !ranking ? 0 : ranking.covered_through_ordinal + 1
  return {
    ranking, isExplicitFinal: ranking !== null && ranking.ranking_id === summary.selected_final_ranking?.ranking_id,
    ranked: ranking?.ranked.map((entry, index) => ({ receipt: byResult.get(entry.reference.result_id) ?? invalid(), position: index + 1, rationale: entry.rationale })) ?? [],
    excluded: ranking?.excluded.map((entry) => ({ receipt: byResult.get(entry.reference.result_id) ?? invalid(), reason: entry.reason })) ?? [],
    outsideRanking: summary.stages.filter((receipt) => receipt.ordinal >= count), coverageCount: count,
  }
}
/** Capture these inputs synchronously with the user's ordinary Explore click. */
export function chooseExploreIntent({ mode, initialParentIds, expectedActivationSha256, access, requestedMaximum }: { mode: 'explore' | 'ask' | 'surprise'; initialParentIds: readonly string[]; expectedActivationSha256: string; access: ExploreCollectionAccess; requestedMaximum?: number }): ExploreCollectionIntent | undefined {
  if (!['explore', 'ask', 'surprise'].includes(mode) || !Array.isArray(initialParentIds) || initialParentIds.some((id) => { try { uuid(id); return false } catch { return true } }) || new Set(initialParentIds).size !== initialParentIds.length) return invalid()
  if (mode !== 'explore' || initialParentIds.length) return undefined
  hash(expectedActivationSha256)
  if (access.activation_sha256 !== expectedActivationSha256) return invalid()
  const approved = access.explore_collection_intent
  if (approved === null) { if (requestedMaximum !== undefined) return invalid(); return undefined }
  const maximum = requestedMaximum === undefined ? Math.min(3, approved.max_bridges) : integer(requestedMaximum, 1, approved.max_bridges)
  return { protocol: 'polymath-explore-collection-v1', max_bridges: maximum }
}
