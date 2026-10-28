import type { SavedArtifact } from '../artifacts/types'
import type { OperatorSession, PublicationCapabilities, PublicationManifest, PublicationMetadata } from './client'
import type { PublicationJob } from './deliveryClient'

export const ARTIFACT = '11111111-1111-4111-8111-111111111111'
export const RUN = '22222222-2222-4222-8222-222222222222'
export const MANIFEST = '33333333-3333-4333-8333-333333333333'
export const APPROVAL = '44444444-4444-4444-8444-444444444444'
export const SECOND_MANIFEST = '55555555-5555-4555-8555-555555555555'
export const PUBLICATION_JOB = '88888888-8888-4888-8888-888888888888'
export const BROWSER_CSRF = 'a'.repeat(64)
export const OPERATOR_CSRF = 'b'.repeat(64)
export const REVIEW_HASH = 'c'.repeat(64)
export const DATE = '2026-10-28T00:00:00Z'
export function artifactFixture(): SavedArtifact {
  const paragraph = { text: 'A synthetic model inference remains unverified.', label: 'postulated' as const, citation_ordinals: [0] }
  return { id: ARTIFACT, run_id: RUN, kind: 'write_summary', status: 'ready', created_at: DATE, source_result_id: '66666666-6666-4666-8666-666666666666', source_run_id: '77777777-7777-4777-8777-777777777777', source_sha256: BROWSER_CSRF, formalisation_id: null, formalisation_checks: [], passages: [{ ordinal: 0, source_id: 'fixture:original', title: 'Synthetic original source', url: 'https://example.org/synthetic', content_sha256: BROWSER_CSRF, content_kind: 'abstract', quote: 'The synthetic source provides an explicitly synthetic premise.', supports: 'A synthetic premise.', claim_id: null }], payload: { kind: 'write_summary', status: 'ready', draft: { kind: 'summary', title: 'Synthetic publication report', abstract: [paragraph], sections: [{ heading: 'Evidence', paragraphs: [paragraph] }, { heading: 'Open work', paragraphs: [paragraph] }], limitations: ['Synthetic sources are not scientific evidence.'] }, checks: [], events: [], usage: { model_calls: 1, input_tokens_reported: 10, output_tokens_reported: 15, calls_without_usage: 0 }, error_code: null, reason: null, validation_scope: 'citation_integrity_and_bounded_checks' }, markdown: '# Synthetic publication report\n\nA saved synthetic document.\n', markdown_sha256: BROWSER_CSRF, payload_sha256: BROWSER_CSRF, basis_sha256: BROWSER_CSRF }
}
export function metadataFixture(): PublicationMetadata { return { title: 'Synthetic publication report', description: 'A synthetic source-bound report for testing exact review.', creators: [{ name: 'Synthetic Creator', affiliation: 'Synthetic Institute', orcid: null }], keywords: ['synthetic'], license: 'cc-by-4.0', upload_type: 'publication', publication_type: 'preprint', access_right: 'open' } }
export function capabilityFixture(configured = true): PublicationCapabilities { return { configured, publisher: configured ? { identifier: 'synthetic.publisher', display_name: 'Synthetic Publisher' } : null, destinations: configured ? ['sandbox', 'production'] : [], can_prepare: configured } }
export function operatorFixture(authenticated = false, configured = true): OperatorSession { return { configured, authenticated, actor_id: authenticated ? 'synthetic.owner' : null, display_name: authenticated ? 'Synthetic Owner' : null, expires_at: authenticated ? '2100-01-01T00:00:00Z' : null, csrf_token: authenticated ? OPERATOR_CSRF : null } }
export function manifestFixture(approved = false): PublicationManifest {
  const source = artifactFixture()
  return { id: MANIFEST, manifest_sha256: REVIEW_HASH, manifest: { protocol: 'polymath-publication-v1', artifact_id: ARTIFACT, artifact_run_id: RUN, markdown_sha256: source.markdown_sha256, markdown_bytes: new TextEncoder().encode(source.markdown).length, payload_sha256: source.payload_sha256, basis_sha256: source.basis_sha256, file_name: `polymath-${ARTIFACT}.md`, media_type: 'text/markdown; charset=utf-8', remote_marker: `polymath-${'d'.repeat(64)}`, metadata: metadataFixture(), publisher: { identifier: 'synthetic.publisher', display_name: 'Synthetic Publisher' }, destination: 'sandbox' }, created_at: DATE, approval: approved ? { id: APPROVAL, manifest_id: MANIFEST, manifest_sha256: REVIEW_HASH, actor_id: 'synthetic.owner', approved_at: DATE, expires_at: '2100-01-01T00:00:00Z', revoked_at: null, valid: true } : null, publication_status: 'not_published', artifact_export_url: `/api/v1/artifacts/${ARTIFACT}/export` }
}

export function publicationJobFixture(changes: Partial<PublicationJob> = {}): PublicationJob {
  return { id: PUBLICATION_JOB, manifest_id: MANIFEST, approval_id: APPROVAL, manifest_sha256: REVIEW_HASH, status: 'queued', work_kind: 'publish', stage: 'queued', created_at: DATE, updated_at: DATE, reason: null, event_sequence: 1, external_outcome_unknown: false, frozen: false, publication_reported: false, destination: 'sandbox', sandbox: true, verified: false, doi: null, record_url: null, can_cancel: true, can_reconcile: false, can_resume: false, ...changes }
}
