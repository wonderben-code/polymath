import type { SourceDocument } from './types'
import type { SourceCoverage } from './sourceCoverage'
import { BIBLIOGRAPHY } from './bibliography.test-support'

export const SNAPSHOT = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
export const SNAPSHOT_HASH = 'a'.repeat(64)
export const EXCERPT_HASH = 'b'.repeat(64)
export function coverage(overrides: Partial<SourceCoverage> = {}): SourceCoverage { return { snapshot_id: SNAPSHOT, snapshot_sha256: SNAPSHOT_HASH, arxiv_id: '2610.00001', arxiv_version: 2, start: 100, end: 140, total: 20000, ...overrides } }
export function excerpt(text = '🌱 Synthetic full-text excerpt, not proof.', start = 100): SourceDocument {
  const scope = coverage({ start, end: start + Array.from(text).length })
  return { source_id: `arxiv-html:${scope.snapshot_id}:${scope.start}:${scope.end}`, title: 'Synthetic versioned paper', url: `https://arxiv.org/html/${scope.arxiv_id}v${scope.arxiv_version}`, text, content_kind: 'full_text', retrieved_at: '2026-10-28T00:00:00Z', extraction_method: 'arxiv-html-latexml/v1', bibliography: BIBLIOGRAPHY, coverage: scope }
}
