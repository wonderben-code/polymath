export type SharingMode = 'explore' | 'ask' | 'surprise'
export type PublicFindingStatus = 'current' | 'superseded' | 'unsupported'
export type ShareStatus = PublicFindingStatus | 'revoked' | 'expired'
export interface PublicCoverage {
  snapshot_sha256: string
  arxiv_id: string
  arxiv_version: number
  start: number
  end: number
  total: number
}
export interface PublicScholarlyLink { kind: 'doi' | 'arxiv' | 'openalex'; url: string }
export interface PublicIdentifiers { doi?: string; arxiv_id?: string; arxiv_version?: number; openalex_id?: string }
export interface PublicBibliography {
  authors: { name: string; orcid: string | null }[]
  authors_truncated: boolean
  publication_year: number | null
  publication_date: string | null
  venue: string | null
}
export interface PublicPaper {
  id: string
  title: string
  identifiers: PublicIdentifiers
  links: PublicScholarlyLink[]
  bibliography?: PublicBibliography
}
export interface PublicQuote {
  excerpt: string | null
  excerpted: boolean
  excerpt_start: number | null
  excerpt_end: number | null
  full_quote_start: number
  full_quote_end: number
  full_quote_chars: number
  full_quote_sha256: string
  excerpt_omission_reason: 'paper_limit' | null
}
export interface PublicCitation {
  id: string
  paper_id: string
  side: 'left' | 'right' | 'answer'
  claim_id: string | null
  supports: string
  source_kind: 'abstract' | 'full_text'
  source_sha256: string
  quote: PublicQuote
  coverage?: PublicCoverage
}
export interface PublicBridge {
  kind: 'bridge'
  title: string
  field_left: string
  field_right: string
  explanation: string
  mappings: { left: string; right: string; assumption: string }[]
  open_question: string
  strongest_objection: string
  objection_verdict: string
  novelty_limitations: string
  citation_ids: string[]
}
export interface PublicAnswerClaim {
  id: string
  text: string
  label: 'known' | 'postulated' | 'speculative'
  derivation: string | null
  caveats: string[]
  citation_ids: string[]
}
export interface PublicAnswer {
  kind: 'answer'
  question: string
  summary: string
  claims: PublicAnswerClaim[]
  limitations: string[]
  unanswered_questions: string[]
}
export interface PublicProjection {
  protocol: 'polymath-public-finding-v1'
  mode: SharingMode
  validation_scope: 'model_assessment_and_saved_quote_integrity'
  finding: PublicBridge | PublicAnswer
  papers: PublicPaper[]
  citations: PublicCitation[]
  field_selection?: { fields: string[]; rationale: string }
}
export interface SharingCapabilities { sharing_available: boolean; reason: 'sharing_unavailable' | null }
export interface SharePreview { source_result_id: string; projection_sha256: string; projection: PublicProjection; expires_at: string }
export interface ShareRecord extends SharePreview {
  public_id: string
  created_at: string
  revoked_at: string | null
  status: ShareStatus
  public_path: string
  idempotency_key: string
}
export interface ShareHistory { shares: ShareRecord[]; next_cursor: string | null }
export interface CreateShare { source_result_id: string; expected_projection_sha256: string; idempotency_key: string }
export interface PublicFinding {
  public_id: string
  projection_sha256: string
  projection: PublicProjection
  created_at: string
  expires_at: string
  status: PublicFindingStatus
}
export interface PublicLibraryItem {
  public_id: string
  kind: 'bridge' | 'answer'
  mode: SharingMode
  title: string
  summary: string
  fields: string[]
  projection_sha256: string
  created_at: string
  expires_at: string
  status: 'current'
}
export interface PublicLibrary { items: PublicLibraryItem[]; next_cursor: string | null }
export interface PublicMapNode {
  id: string
  kind: 'finding' | 'field' | 'paper'
  label: string
  public_id: string | null
  public_path: string | null
  paper_id: string | null
  links: PublicScholarlyLink[]
}
export interface PublicMapEdge { id: string; source: string; target: string; kind: 'field_left' | 'field_right' | 'field_selected' | 'cites' }
export interface PublicMap { scope: 'explicitly_shared_current_findings'; nodes: PublicMapNode[]; edges: PublicMapEdge[]; truncated: boolean; coverage: { max_findings: 60; max_nodes: 200; max_edges: 500 } }
