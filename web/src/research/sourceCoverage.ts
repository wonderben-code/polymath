import type { SourceBibliography } from './bibliography'

/** An exact window of one immutable extracted paper; hashes cover different bytes. */
export interface SourceCoverage {
  snapshot_id: string
  snapshot_sha256: string
  arxiv_id: string
  arxiv_version: number
  start: number
  end: number
  total: number
}

const UUID = /^[a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12}$/
const HASH = /^[a-f\d]{64}$/
const ARXIV = /^(?:\d{4}\.\d{4,5}|[a-z]+(?:-[a-z]+)?(?:\.[A-Z]{2})?\/\d{7})$/
const FIELDS = ['snapshot_id', 'snapshot_sha256', 'arxiv_id', 'arxiv_version', 'start', 'end', 'total']

export function parseSourceCoverage(value: unknown, invalid: () => never): SourceCoverage | undefined {
  if (value === undefined || value === null) return undefined
  if (typeof value !== 'object' || Array.isArray(value)) return invalid()
  const item = value as Record<string, unknown>
  if (Object.keys(item).some((key) => !FIELDS.includes(key))) return invalid()
  if (typeof item.snapshot_id !== 'string' || !UUID.test(item.snapshot_id) || typeof item.snapshot_sha256 !== 'string' || !HASH.test(item.snapshot_sha256) || typeof item.arxiv_id !== 'string' || item.arxiv_id.length > 100 || !ARXIV.test(item.arxiv_id)) return invalid()
  const serial = item.arxiv_id.split('/').at(-1)!.split('.')[0], month = Number(serial.slice(2, 4))
  if (month < 1 || month > 12) return invalid()
  function integer(raw: unknown, minimum: number, maximum: number): number { return typeof raw === 'number' && Number.isSafeInteger(raw) && raw >= minimum && raw <= maximum ? raw : invalid() }
  const start = integer(item.start, 0, 249999), end = integer(item.end, 1, 250000), total = integer(item.total, 1, 250000)
  if (!(start < end && end <= total) || end - start > 8000) return invalid()
  return { snapshot_id: item.snapshot_id, snapshot_sha256: item.snapshot_sha256, arxiv_id: item.arxiv_id, arxiv_version: integer(item.arxiv_version, 1, 9999), start, end, total }
}

export function validateSourceCoverageContext(source: {
  source_id: string
  url: string
  bibliography?: SourceBibliography
  coverage?: SourceCoverage
  content_kind?: 'abstract' | 'full_text'
  text?: string
}, invalid: () => never): void {
  const coverage = source.coverage
  if (!coverage) return
  if (source.source_id !== `arxiv-html:${coverage.snapshot_id}:${coverage.start}:${coverage.end}` || source.url !== `https://arxiv.org/html/${coverage.arxiv_id}v${coverage.arxiv_version}` || source.bibliography?.arxiv_id !== coverage.arxiv_id || source.bibliography?.arxiv_version !== coverage.arxiv_version || source.content_kind !== undefined && source.content_kind !== 'full_text') return invalid()
  if (source.text !== undefined && (Array.from(source.text).length !== coverage.end - coverage.start || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\uD800-\uDFFF]/u.test(source.text))) return invalid()
}
