import type { AnswerFinding, BridgeFinding, MemoryReadReference } from '../research/types'
import type { SourceBibliography } from '../research/bibliography'
import type { SourceCoverage } from '../research/sourceCoverage'
import type { StageCommitReceipt } from '../discovery/types'
export interface ArtifactScientificContextIntent { protocol: 'polymath-artifact-scientific-context-v1' }
export interface AskOverviewIntent { protocol: 'polymath-ask-claim-overview-v1' }
export interface AskReassessmentOverviewIntent { protocol: 'polymath-ask-reassessment-overview-v1' }
export interface AskOverviewBinding { protocol: 'polymath-ask-claim-overview-v1'; claim_ids: string[] }
export type ScientificFinding = BridgeFinding | AnswerFinding
export interface ScientificSourceReference { source_id: string; title: string; url: string; bibliography?: SourceBibliography; coverage?: SourceCoverage }
export interface ScientificAssessmentCitation { retrieval_id: string; quote: string; supports: string }
export interface ScientificAssessmentStatement { text: string; citations: ScientificAssessmentCitation[] }
export interface ScientificAssessmentQuote extends ScientificAssessmentCitation { version_id: string; source: ScientificSourceReference; source_sha256: string; content_kind: 'abstract' | 'full_text'; start: number; end: number }
export interface AppliedAssessmentContext { assessment_id: string; run_id: string; payload_sha256: string; applied: true; head_revision: number; prior_reference: MemoryReadReference; candidate_reference: MemoryReadReference | null; disposition: 'unchanged' | 'revised' | 'unsupported'; rationale: ScientificAssessmentStatement[]; quotes: ScientificAssessmentQuote[]; limitations: string[] }
export interface SourceRevisionContext { root_reference: MemoryReadReference; current_reference: MemoryReadReference; revision: number; state_at_admission: 'supported'; applied_assessments: AppliedAssessmentContext[] }
export interface StageOriginContext { receipt: StageCommitReceipt; receipt_sha256: string; relationship: 'exact_stage_result' | 'reassessed_descendant'; revision_of: MemoryReadReference | null }
export interface ScientificAncestor { reference: MemoryReadReference; finding: ScientificFinding; stage_origin: StageOriginContext | null; overview_binding: AskOverviewBinding | null }
export interface ScientificParentEdge { child: MemoryReadReference; parent: MemoryReadReference; ordinal: number; kind: 'premise' | 'revision' | 'reassessment'; assessment_id: string | null; assessment_sha256: string | null }
export interface PriorArtifactContextReference { artifact_id: string; source_reference: MemoryReadReference; payload_sha256: string; basis_sha256: string; context_sha256: string | null }
export interface ArtifactScientificContext { protocol: 'polymath-artifact-scientific-context-v1'; source_reference: MemoryReadReference; source_revision: SourceRevisionContext; stage_origin: StageOriginContext | null; ancestry_nodes: ScientificAncestor[]; ancestry_edges: ScientificParentEdge[]; prior_formalisation: PriorArtifactContextReference | null }
export interface ArtifactScientificContextBinding { context: ArtifactScientificContext; context_sha256: string }
export const SCIENTIFIC_CAPABILITIES = ['artifact_scientific_context_v1', 'ask_claim_overview_v1', 'ask_reassessment_overview_v1'] as const
export type ScientificCapability = typeof SCIENTIFIC_CAPABILITIES[number]
export interface ResearchProtocols { protocol: 'polymath-research-protocols-v1'; activation_sha256: string | null; capabilities: ScientificCapability[] }
