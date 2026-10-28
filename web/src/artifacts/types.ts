import type { ResearchActivityEvent } from '../research/types'
import type { SourceBibliography } from '../research/bibliography'
import type { SourceCoverage } from '../research/sourceCoverage'
import type { NativeRunControl } from '../research/nativeAdmission'

export type ArtifactKind = 'formalise' | 'write_summary' | 'write_paper'
export type ArtifactStatus = 'ready' | 'insufficient_evidence' | 'failed' | 'limit_reached'
export type ClaimLabel = 'known' | 'postulated' | 'speculative'
export type JobStatus = 'queued' | 'running' | 'waiting_retry' | 'cancel_requested' | 'cancelled' | 'succeeded' | 'failed' | 'interrupted'

export interface ArtifactJob {
  native_research?: NativeRunControl
  id: string
  mode: 'explore' | 'ask' | 'surprise'
  task_kind: ArtifactKind
  goal: string
  status: JobStatus
  created_at: string
  updated_at: string
  result_id: null
  artifact_id: string | null
  error_code: string | null
  external_outcome_unknown: boolean
}

export interface ArtifactHistory {
  jobs: ArtifactJob[]
  next_cursor: string | null
}

export interface CitedText {
  text: string
  label: ClaimLabel
  citation_ordinals: number[]
}

export interface ArtifactPassage {
  ordinal: number
  source_id: string
  title: string
  url: string
  content_sha256: string
  content_kind: 'abstract' | 'full_text'
  quote: string
  supports: string
  claim_id: string | null
  bibliography?: SourceBibliography
  coverage?: SourceCoverage
}

export interface DocumentSection {
  heading: string
  paragraphs: CitedText[]
}

export interface DocumentDraft {
  kind: 'summary' | 'paper'
  title: string
  abstract: CitedText[]
  sections: DocumentSection[]
  limitations: string[]
}

export type Expression = { op: 'constant'; value: number } | { op: 'variable'; name: string } |
  { op: 'add' | 'subtract' | 'multiply'; left: Expression; right: Expression } |
  { op: 'power'; base: Expression; exponent: number }

export interface Relation {
  left: Expression
  operator: 'eq' | 'lt' | 'le'
  right: Expression
}

export interface FiniteConjecture {
  variables: { name: string; lower: number; upper: number }[]
  assumptions: Relation[]
  conclusion: Relation
}

export interface FiniteCheckResult {
  conjecture: FiniteConjecture
  conjecture_sha256: string
  status: 'counterexample_found' | 'no_counterexample_in_domain' | 'no_applicable_cases' | 'limit_reached'
  assignments_tested: number
  applicable_cases: number
  total_assignments: number
  witness: Record<string, number> | null
  // Exact integer values arrive as decimal strings, never rounded JS numbers.
  lhs: string | null
  rhs: string | null
  error_code: 'integer_size_limit' | 'evaluation_limit' | null
  scope: 'finite_integer_domain'
  proof_status: 'not_a_general_proof'
}

export interface FormalisationDraft {
  kind: 'formalisation'
  title: string
  definitions: { symbol: string; meaning: CitedText }[]
  assumptions: CitedText[]
  conjecture: CitedText
  proof_attempt: CitedText[]
  limitations: string[]
  finite_conjecture: FiniteConjecture | null
}

export interface ArtifactResult {
  kind: ArtifactKind
  status: ArtifactStatus
  draft: DocumentDraft | FormalisationDraft | null
  checks: FiniteCheckResult[]
  events: ResearchActivityEvent[]
  usage: { model_calls: number; input_tokens_reported: number; output_tokens_reported: number; calls_without_usage: number }
  error_code: string | null
  reason: string | null
  validation_scope: 'citation_integrity_and_bounded_checks'
}

export interface SavedArtifact {
  id: string
  run_id: string
  kind: ArtifactKind
  status: ArtifactStatus
  created_at: string
  source_result_id: string
  source_run_id: string
  source_sha256: string
  formalisation_id: string | null
  formalisation_checks: FiniteCheckResult[]
  passages: ArtifactPassage[]
  payload: ArtifactResult
  markdown: string
  markdown_sha256: string
  payload_sha256: string
  basis_sha256: string
}
