import type { MemoryReadReference, ResearchResult } from '../research/types'

export interface DiscoveryIntent {
  protocol: 'polymath-surprise-ladder-v1'
  max_stages: number
  max_depth: number
}

/** Model assessments remain uncalibrated judgements, never application scores. */
export interface StageAssessment {
  novelty: 'known' | 'partly_known' | 'not_found_in_bounded_search' | 'uncertain'
  novelty_rationale: string
  confidence: 'tentative' | 'moderate' | 'strong'
  confidence_score: number
  confidence_scope: 'uncalibrated_model_judgement'
  confidence_rationale: string
  limitations: string[]
}

export interface StageCommitReceipt {
  stage_id: string
  run_id: string
  ordinal: number
  result_id: string
  result_sha256: string
  depth: number
  assessment: StageAssessment
  parent_refs: MemoryReadReference[]
  action_ordinal: number
  model_request_id: string
}

export interface DiscoverySummary {
  run_id: string
  run_status: string
  discovery_intent: DiscoveryIntent
  stages: StageCommitReceipt[]
  selected_final_stage: StageCommitReceipt | null
  stop_reason: string | null
  total_usage: { model_calls: number; input_tokens_reported: string; output_tokens_reported: string; calls_without_usage: number }
  accounting: { counted_spend_microdollars: string; open_hold_microdollars: string; unknown_hold_microdollars: string }
}

export interface StageParentEdge {
  kind: 'support' | 'revision'
  ordinal: number
  reference: MemoryReadReference
  stage_id: string | null
}

export interface DiscoveryStage {
  stage: StageCommitReceipt
  result: ResearchResult
  parent_edges: StageParentEdge[]
  primary_paper_groups: { paper_id: string; source_ids: string[] }[]
  status: 'current_supported' | 'unsupported' | 'superseded' | 'needs_review'
}
export type StageDetail = DiscoveryStage
export interface DiscoveryStageHistory { run_id: string; stages: DiscoveryStage[] }

export interface DiscoveryStageMetadata { stage_id: string; ordinal: number; depth: number; status: DiscoveryStage['status'] }
