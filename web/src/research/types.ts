import type { SourceBibliography } from './bibliography'
import type { SourceCoverage } from './sourceCoverage'
import type { FindingVersionNoticeProps } from '../assessments/FindingVersionNotice'
import type { NativeRunControl } from './nativeAdmission'
import type { AskOverviewBinding, AskOverviewIntent } from '../scientificContext/types'
import type { ExploreCollectionIntent } from '../exploreCollection/types'
import type { DiscoveryIntent } from '../discovery/types'

export interface ResearchRunSummary {
  id: string
  mode: 'explore' | 'ask' | 'surprise'
  goal: string
  status: string
  task_kind?: string
  native_research?: NativeRunControl
  discovery_intent?: DiscoveryIntent
  explore_collection_intent?: ExploreCollectionIntent
  ask_overview_intent?: AskOverviewIntent
}

export interface ResearchActivityEvent {
  sequence: number
  kind: string
  summary: string
}

export interface SourceDocument {
  source_id: string
  title: string
  url: string
  text: string
  content_kind: 'abstract' | 'full_text'
  retrieved_at: string
  extraction_method: string
  bibliography?: SourceBibliography
  coverage?: SourceCoverage
}

export interface EvidenceQuote {
  source_id: string
  side: 'left' | 'right'
  quote: string
  supports: string
}

export interface ClaimQuote {
  source_id: string
  quote: string
  supports: string
}

export interface AnswerClaim {
  id: string
  text: string
  label: 'known' | 'postulated' | 'speculative'
  evidence: ClaimQuote[]
  derivation: string | null
  caveats: string[]
}

export interface AnswerFinding {
  outcome: 'answer'
  question: string
  summary: string
  claims: AnswerClaim[]
  limitations: string[]
  unanswered_questions: string[]
}

export interface FieldSelection {
  fields: string[]
  rationale: string
}

export interface VerifiedQuote extends ClaimQuote {
  side: 'left' | 'right' | 'answer'
  claim_id?: string | null
  start: number
  end: number
  source_sha256: string
  content_kind: 'abstract' | 'full_text'
}

export interface BridgeFinding {
  outcome: 'bridge'
  title: string
  field_left: string
  field_right: string
  explanation: string
  mappings: { left: string; right: string; assumption: string }[]
  open_question: string
  strongest_objection: string
  objection_verdict: string
  novelty_limitations: string
  evidence: EvidenceQuote[]
}

export interface InsufficientFinding {
  outcome: 'insufficient_evidence'
  reason: string
}

export interface MemoryReadReference {
  result_id: string
  run_id: string
  content_sha256: string
}

export interface ResearchResult {
  overview_binding?: AskOverviewBinding
  status: 'bridge' | 'answer' | 'insufficient_evidence' | 'limit_reached' | 'failed'
  // Older persisted Explore findings predate these fields.
  mode?: ResearchRunSummary['mode']
  field_selections?: FieldSelection[]
  memory_reads?: MemoryReadReference[]
  finding: BridgeFinding | AnswerFinding | InsufficientFinding | null
  quotes: VerifiedQuote[]
  sources: SourceDocument[]
  events: ResearchActivityEvent[]
  usage: {
    model_calls: number
    input_tokens_reported: number
    output_tokens_reported: number
    calls_without_usage: number
  }
  error_code: string | null
  validation_scope: 'source_identity_and_quote_integrity'
}

export interface ResearchRunViewProps {
  notifyControl?: import('react').ReactNode
  run: ResearchRunSummary
  events: ResearchActivityEvent[]
  result: ResearchResult | null
  connection: 'connecting' | 'live' | 'reconnecting' | 'closed' | 'error'
  onCancel?: () => void
  cancelling?: boolean
  onOpenRun?: (id: string) => void
  findingVersion?: FindingVersionNoticeProps
  hideOutcome?: boolean
  primaryPaperGroups?: { paper_id: string; source_ids: string[] }[]
}
