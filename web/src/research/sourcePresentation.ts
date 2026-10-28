import type { SourceBibliography } from './bibliography'
import type { SourceCoverage } from './sourceCoverage'

export interface PresentedSource {
  source_id: string
  content_kind: 'abstract' | 'full_text'
  coverage?: SourceCoverage
  bibliography?: SourceBibliography
}

/** Display grouping only; shared paper identity is not independent corroboration. */
export function sourcePaperIdentity(source: PresentedSource): string {
  if (source.coverage) return `arxiv:${source.coverage.arxiv_id}`
  if (source.bibliography?.arxiv_id) return `arxiv:${source.bibliography.arxiv_id}`
  const arxiv = /^arxiv:((?:\d{4}\.\d{4,5}|[a-z]+(?:-[a-z]+)?(?:\.[A-Z]{2})?\/\d{7}))(?:v[1-9]\d{0,3})?$/.exec(source.source_id)
  return arxiv ? `arxiv:${arxiv[1]}` : source.source_id
}

export function groupSourceVersions<T extends PresentedSource>(sources: readonly T[]): { identity: string; versions: T[] }[] {
  const grouped = new Map<string, T[]>()
  for (const source of sources) { const identity = sourcePaperIdentity(source); const existing = grouped.get(identity); if (existing) existing.push(source); else grouped.set(identity, [source]) }
  return [...grouped].map(([identity, versions]) => ({ identity, versions }))
}

export function sourceContentLabel(kind: PresentedSource['content_kind'], coverage?: SourceCoverage): string {
  return coverage ? 'Full text excerpt' : kind === 'abstract' ? 'Abstract only' : 'Full-text snapshot'
}

export function sourceIdentityCount(sources: readonly PresentedSource[]): number { return new Set(sources.map(sourcePaperIdentity)).size }
