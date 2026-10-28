/** Saved collection projections carry no new scientific or financial authority. */
import type { MemoryReadReference, ResearchResult } from '../research/types'
import type { DiscoveryStage, StageCommitReceipt } from '../discovery/types'

export interface ExploreCollectionIntent { protocol: 'polymath-explore-collection-v1'; max_bridges: number }
export interface ExploreCollectionAccess { protocol: 'polymath-explore-collection-access-v1'; activation_sha256: string | null; explore_collection_intent: ExploreCollectionIntent | null }
export interface ExploreRankedBridge { reference: MemoryReadReference; rationale: string }
export interface ExploreExcludedBridge { reference: MemoryReadReference; reason: string }
export interface ExploreRankingReceipt {
  ranking_id: string; run_id: string; packet_sha256: string; model_request_id: string; action_ordinal: number
  covered_through_ordinal: number | null
  ranked: ExploreRankedBridge[]; excluded: ExploreExcludedBridge[]
}
/** Owned, read-only saved collection projections. */
export interface ExploreCollectionSummary {
  run_id: string; run_status: string; explore_collection_intent: ExploreCollectionIntent
  stages: StageCommitReceipt[]; rankings: ExploreRankingReceipt[]; selected_final_ranking: ExploreRankingReceipt | null
  stop_reason: string | null
  total_usage: { model_calls: number; input_tokens_reported: string; output_tokens_reported: string; calls_without_usage: number }
  accounting: { counted_spend_microdollars: string; open_hold_microdollars: string; unknown_hold_microdollars: string }
}
export interface ExploreBridgeDetail extends Omit<DiscoveryStage, 'result'> { result: ResearchResult }
export interface ExploreBridgeHistory { run_id: string; stages: ExploreBridgeDetail[] }
export interface ExploreRankingHistory { run_id: string; rankings: ExploreRankingReceipt[] }
