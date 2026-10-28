import type { AskOverviewBinding, AskOverviewIntent, AskReassessmentOverviewIntent } from '../scientificContext/types'
import type { DiscoveryIntent, DiscoveryStage, DiscoveryStageMetadata } from '../discovery/types'
import type { AnswerFinding, BridgeFinding, FieldSelection, MemoryReadReference, ResearchActivityEvent, ResearchResult, VerifiedQuote } from '../research/types'
import type { SourceBibliography } from '../research/bibliography'
import type { SourceCoverage } from '../research/sourceCoverage'
import type { NativeRunControl } from '../research/nativeAdmission'

export type AssessmentDisposition = 'unchanged' | 'revised' | 'unsupported' | 'inconclusive'
export type AssessmentJobStatus = 'queued' | 'running' | 'waiting_retry' | 'cancel_requested' | 'cancelled' | 'succeeded' | 'failed' | 'interrupted'

export interface AssessmentCapabilities {
  execution_available: boolean
  reason: 'research_unavailable' | null
  automatic_reassessment: false
}

export interface FindingHead {
  discovery_stage?: DiscoveryStageMetadata
  requested_result_id: string
  root_result_id: string
  current_result_id: string
  current_run_id: string
  state: 'supported' | 'unsupported'
  revision: number
  last_assessment_id: string | null
  is_current: boolean
  is_supported: boolean
  is_historical: boolean
  can_generate: boolean
}

export interface CreateAssessmentJob {
  source_result_id: string
  watch_id: string
  new_source_version_ids: string[]
  expected_head_revision: number
}

export interface AssessmentJob extends CreateAssessmentJob {
  assessment_overview_intent?: AskReassessmentOverviewIntent
  native_research?: NativeRunControl
  id: string
  root_result_id: string
  source_sha256: string
  mode: 'explore' | 'ask' | 'surprise'
  task_kind: 'reassess_finding'
  goal: string
  status: AssessmentJobStatus
  created_at: string
  updated_at: string
  result_id: null
  artifact_id: null
  assessment_id: string | null
  error_code: string | null
  external_outcome_unknown: boolean
}

export interface AssessmentCancellation {
  id: string
  task_kind: 'reassess_finding'
  status: AssessmentJobStatus
  assessment_id: string | null
}

export interface AssessmentJobHistory { jobs: AssessmentJob[]; next_cursor: string | null }
export interface AssessmentHistory { assessments: AssessmentRecord[]; next_cursor: string | null }
export interface FindingVersion {
  id: string
  run_id: string
  task_kind: 'research' | 'reassess_finding'
  run: {
    id: string
    task_kind: 'research' | 'reassess_finding'
    mode: 'explore' | 'ask' | 'surprise'
    goal: string
    status: AssessmentJobStatus
    created_at: string
    updated_at: string
    result_id: string | null
    artifact_id: null
    assessment_id: string | null
    error_code: string | null
    external_outcome_unknown: boolean
    native_research?: NativeRunControl
    discovery_intent?: DiscoveryIntent
    ask_overview_intent?: AskOverviewIntent
    assessment_overview_intent?: AskReassessmentOverviewIntent
  }
  payload: ResearchResult
  assessment: FindingHead
  discovery_stage?: DiscoveryStage
}

// A revision is an immutable assessment; only an applied revision changes the head.
export interface AssessmentRecord {
  id: string
  run_id: string
  root_result_id: string
  source_result_id: string
  source_run_id: string
  source_sha256: string
  watch_id: string
  new_source_version_ids: string[]
  expected_head_revision: number
  disposition: AssessmentDisposition
  applied: boolean
  head_revision: number
  candidate_result_id: string | null
  payload: AssessmentPayload
  payload_sha256: string
  basis_sha256: string
  created_at: string
}

export interface ObservedAssessmentSource {
  source_id: string
  title: string
  url: string
  bibliography?: SourceBibliography
  coverage?: SourceCoverage
  retrieval_id: string
  version_id: string
  content_sha256: string
  content_kind: 'abstract' | 'full_text'
}
export interface AssessmentSourceReference extends ObservedAssessmentSource { role: 'historical' | 'new' }
export interface AssessmentBasis {
  prior_overview_binding?: AskOverviewBinding
  prior_reference: MemoryReadReference
  mode: 'explore' | 'ask' | 'surprise'
  prior_state: 'supported' | 'unsupported'
  prior_finding: BridgeFinding | AnswerFinding
  prior_quotes: VerifiedQuote[]
  field_selections: FieldSelection[]
  sources: AssessmentSourceReference[]
}
export interface AssessmentCitation { retrieval_id: string; quote: string; supports: string }
export interface AssessmentStatement { text: string; citations: AssessmentCitation[] }
export interface AssessmentQuote extends AssessmentCitation {
  source_id: string
  source_sha256: string
  content_kind: 'abstract' | 'full_text'
  coverage?: SourceCoverage
  start: number
  end: number
}
export interface AssessmentPayload { assessment_overview_intent?: AskReassessmentOverviewIntent; basis: AssessmentBasis; result: AssessmentResult; verified_passages: AssessmentQuote[]; observed_sources: ObservedAssessmentSource[] }
export interface AssessmentResult {
  status: 'completed' | 'failed' | 'limit_reached'
  disposition: AssessmentDisposition
  rationale: AssessmentStatement[]
  limitations: string[]
  observed_retrieval_ids: string[]
  selected_retrieval_ids: string[]
  quotes: AssessmentQuote[]
  revised_result: ResearchResult | null
  search_purposes: ('discovery' | 'counterevidence' | 'prior_work')[]
  events: ResearchActivityEvent[]
  usage: { model_calls: number; input_tokens_reported: number; output_tokens_reported: number; calls_without_usage: number }
  error_code: string | null
  reason: string | null
  validation_scope: 'version_bound_quote_integrity'
}
