import type { CreateShare, PublicAnswer, PublicAnswerClaim, PublicBibliography, PublicBridge, PublicCitation, PublicCoverage, PublicFinding, PublicIdentifiers, PublicLibrary, PublicLibraryItem, PublicMap, PublicMapEdge, PublicMapNode, PublicPaper, PublicProjection, PublicQuote, PublicScholarlyLink, ShareHistory, SharePreview, ShareRecord, SharingCapabilities, SharingMode } from './types'

export class SharingError extends Error {
  constructor(public readonly code: string, public readonly uncertain = false) { super(code); this.name = 'SharingError' }
}

const OWNER = '/api/v1/shares', PUBLIC = '/api/v1/public'
const UUID = /^[a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12}$/
const HANDLE = /^[A-Za-z\d_-]{43}$/
const HASH = /^[a-f\d]{64}$/
const ARXIV = /^(?:\d{4}\.\d{4,5}|[a-z]+(?:-[a-z]+)?(?:\.[A-Z]{2})?\/\d{7})$/
const DOI = /^10\.\d{4,9}\/[a-z\d][a-z\d._;()/:+\-]{0,170}$/
const OPENALEX = /^W[1-9]\d{0,18}$/
const MAX_BYTES = 2 * 1024 * 1024
const ERROR_CODES = new Set(['sharing_unavailable', 'sharing_not_found', 'sharing_source_not_current', 'sharing_projection_changed', 'sharing_idempotency_conflict', 'sharing_active_limit', 'sharing_global_limit', 'sharing_invalid_request'])
function invalid(): never { throw new SharingError('invalid_response') }
function object(value: unknown, required: string[], optional: string[] = []): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return invalid()
  const item = value as Record<string, unknown>
  if (required.some((key) => !Object.hasOwn(item, key)) || Object.keys(item).some((key) => !required.includes(key) && !optional.includes(key))) return invalid()
  return item
}
function text(value: unknown, maximum: number, required = true): string { return typeof value === 'string' && Array.from(value).length <= maximum && (!required || !!value.trim()) && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\uD800-\uDFFF]/u.test(value) ? value : invalid() }
function integer(value: unknown, minimum = 0, maximum = Number.MAX_SAFE_INTEGER): number { return typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum && value <= maximum ? value : invalid() }
function bool(value: unknown): boolean { return typeof value === 'boolean' ? value : invalid() }
function member<T extends string>(value: unknown, choices: readonly T[]): T { return typeof value === 'string' && choices.includes(value as T) ? value as T : invalid() }
function list<T>(value: unknown, parse: (value: unknown) => T, minimum = 0, maximum = 20): T[] { return Array.isArray(value) && value.length >= minimum && value.length <= maximum ? value.map(parse) : invalid() }
function distinct<T>(items: T[]): T[] { return new Set(items).size === items.length ? items : invalid() }
function nullable<T>(value: unknown, parse: (value: unknown) => T): T | null { return value === null ? null : parse(value) }
function uuid(value: unknown): string { return typeof value === 'string' && UUID.test(value) ? value : invalid() }
function handle(value: unknown): string { return typeof value === 'string' && HANDLE.test(value) ? value : invalid() }
function hash(value: unknown): string { return typeof value === 'string' && HASH.test(value) ? value : invalid() }
function date(value: unknown): string { const parsed = text(value, 100); return /(?:Z|[+-]\d\d:\d\d)$/.test(parsed) && Number.isFinite(Date.parse(parsed)) ? parsed : invalid() }
function mode(value: unknown): SharingMode { return member(value, ['explore', 'ask', 'surprise']) }
function localId(value: unknown, prefix: 'paper' | 'citation' | 'claim', maximum: number): string { const parsed = text(value, 20), match = new RegExp(`^${prefix}-([1-9]\\d*)$`).exec(parsed); return match && Number(match[1]) <= maximum ? parsed : invalid() }
function arxiv(value: unknown): string { const parsed = text(value, 100); if (!ARXIV.test(parsed)) return invalid(); const serial = parsed.split('/').at(-1)!.split('.')[0], month = Number(serial.slice(2, 4)); return month >= 1 && month <= 12 ? parsed : invalid() }
function doi(value: unknown): string { const parsed = text(value, 200); return DOI.test(parsed) ? parsed : invalid() }
function openalex(value: unknown): string { const parsed = text(value, 40); return OPENALEX.test(parsed) ? parsed : invalid() }
function normal(value: string): string { return value.trim().replace(/\s+/gu, ' ').toLowerCase().replaceAll('ß', 'ss').replaceAll('ς', 'σ') }

export function safePublicScholarlyLink(value: unknown): value is PublicScholarlyLink {
  try {
    const item = object(value, ['kind', 'url']), url = new URL(text(item.url, 2000))
    if (url.protocol !== 'https:' || url.username || url.password || url.port || url.search || url.hash || url.href !== item.url) return false
    if (item.kind === 'doi') return url.hostname === 'doi.org' && DOI.test(url.pathname.slice(1))
    if (item.kind === 'openalex') return url.hostname === 'openalex.org' && OPENALEX.test(url.pathname.slice(1))
    if (item.kind === 'arxiv' && url.hostname === 'arxiv.org') {
      const match = /^\/(abs|html)\/(.+?)(?:v([1-9]\d{0,3}))?$/.exec(url.pathname)
      if (!match || match[1] === 'html' && !match[3]) return false
      arxiv(match[2]); return true
    }
    return false
  } catch { return false }
}
function scholarlyLink(value: unknown): PublicScholarlyLink { if (!safePublicScholarlyLink(value)) return invalid(); return { kind: value.kind, url: value.url } }

export function parsePublicCoverage(value: unknown): PublicCoverage {
  const item = object(value, ['snapshot_sha256', 'arxiv_id', 'arxiv_version', 'start', 'end', 'total'])
  const start = integer(item.start, 0, 249999), end = integer(item.end, 1, 250000), total = integer(item.total, 1, 250000)
  if (!(start < end && end <= total) || end - start > 8000) return invalid()
  return { snapshot_sha256: hash(item.snapshot_sha256), arxiv_id: arxiv(item.arxiv_id), arxiv_version: integer(item.arxiv_version, 1, 9999), start, end, total }
}
function identifiers(value: unknown): PublicIdentifiers {
  const item = object(value, [], ['doi', 'arxiv_id', 'arxiv_version', 'openalex_id'])
  if (item.arxiv_version !== undefined && item.arxiv_id === undefined) return invalid()
  return { ...(item.doi === undefined ? {} : { doi: doi(item.doi) }), ...(item.arxiv_id === undefined ? {} : { arxiv_id: arxiv(item.arxiv_id) }), ...(item.arxiv_version === undefined ? {} : { arxiv_version: integer(item.arxiv_version, 1, 9999) }), ...(item.openalex_id === undefined ? {} : { openalex_id: openalex(item.openalex_id) }) }
}
function bibliography(value: unknown): PublicBibliography {
  const item = object(value, ['authors', 'authors_truncated', 'publication_year', 'publication_date', 'venue'])
  const authors = list(item.authors, (raw) => {
    const author = object(raw, ['name', 'orcid']), orcid = nullable(author.orcid, (value) => text(value, 19))
    if (orcid !== null) {
      if (!/^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$/.test(orcid)) return invalid()
      const digits = orcid.replaceAll('-', ''); let total = 0
      for (const digit of digits.slice(0, 15)) total = (total + Number(digit)) * 2
      const checksum = (12 - total % 11) % 11
      if (digits[15] !== (checksum === 10 ? 'X' : String(checksum))) return invalid()
    }
    return { name: text(author.name, 300), orcid }
  }, 0, 20), truncated = bool(item.authors_truncated)
  if (truncated && authors.length !== 20) return invalid()
  const publicationDate = nullable(item.publication_date, (value) => text(value, 10))
  if (publicationDate !== null && (!/^\d{4}-\d{2}-\d{2}$/.test(publicationDate) || !Number.isFinite(Date.parse(publicationDate)) || new Date(publicationDate).toISOString().slice(0, 10) !== publicationDate || Number(publicationDate.slice(0, 4)) < 1000 || Number(publicationDate.slice(0, 4)) > 2100)) return invalid()
  return { authors, authors_truncated: truncated, publication_year: nullable(item.publication_year, (value) => integer(value, 1000, 2100)), publication_date: publicationDate, venue: nullable(item.venue, (value) => text(value, 500)) }
}
function paper(value: unknown): PublicPaper {
  const item = object(value, ['id', 'title', 'identifiers', 'links'], ['bibliography']), aliases = identifiers(item.identifiers), links = list(item.links, scholarlyLink, 0, 3)
  distinct(links.map((link) => link.kind))
  if (links.length !== [aliases.doi, aliases.arxiv_id, aliases.openalex_id].filter((value) => value !== undefined).length) return invalid()
  for (const link of links) {
    if (link.kind === 'doi' ? link.url !== `https://doi.org/${aliases.doi}` : link.kind === 'openalex' ? link.url !== `https://openalex.org/${aliases.openalex_id}` : !aliases.arxiv_id || ![`https://arxiv.org/abs/${aliases.arxiv_id}${aliases.arxiv_version ? `v${aliases.arxiv_version}` : ''}`, ...(aliases.arxiv_version ? [`https://arxiv.org/html/${aliases.arxiv_id}v${aliases.arxiv_version}`] : [])].includes(link.url)) return invalid()
  }
  return { id: localId(item.id, 'paper', 64), title: text(item.title, 1000), identifiers: aliases, links, ...(item.bibliography === undefined ? {} : { bibliography: bibliography(item.bibliography) }) }
}
function publicQuote(value: unknown): PublicQuote {
  const item = object(value, ['excerpt', 'excerpted', 'excerpt_start', 'excerpt_end', 'full_quote_start', 'full_quote_end', 'full_quote_chars', 'full_quote_sha256', 'excerpt_omission_reason'])
  const start = integer(item.full_quote_start, 0, 20000), end = integer(item.full_quote_end, 1, 20000), length = integer(item.full_quote_chars, 10, 4000), excerpted = bool(item.excerpted)
  if (end - start !== length) return invalid()
  const excerpt = nullable(item.excerpt, (value) => text(value, 280, false)), excerptStart = nullable(item.excerpt_start, (value) => integer(value, 0, 20000)), excerptEnd = nullable(item.excerpt_end, (value) => integer(value, 1, 20000))
  const omission = item.excerpt_omission_reason === null ? null : member(item.excerpt_omission_reason, ['paper_limit'])
  if (excerpt === null ? omission !== 'paper_limit' || excerptStart !== null || excerptEnd !== null || !excerpted : omission !== null || excerptStart !== start || excerptEnd !== start + Array.from(excerpt).length || Array.from(excerpt).length < 10 || Array.from(excerpt).length > length || excerpted !== (Array.from(excerpt).length < length)) return invalid()
  return { excerpt, excerpted, excerpt_start: excerptStart, excerpt_end: excerptEnd, full_quote_start: start, full_quote_end: end, full_quote_chars: length, full_quote_sha256: hash(item.full_quote_sha256), excerpt_omission_reason: omission }
}
function citation(value: unknown): PublicCitation {
  const item = object(value, ['id', 'paper_id', 'side', 'claim_id', 'supports', 'source_kind', 'source_sha256', 'quote'], ['coverage'])
  const coverage = item.coverage === undefined ? undefined : parsePublicCoverage(item.coverage), sourceKind = member(item.source_kind, ['abstract', 'full_text']), quote = publicQuote(item.quote)
  if (coverage && (sourceKind !== 'full_text' || quote.full_quote_end > coverage.end - coverage.start)) return invalid()
  return { id: localId(item.id, 'citation', 64), paper_id: localId(item.paper_id, 'paper', 64), side: member(item.side, ['left', 'right', 'answer']), claim_id: nullable(item.claim_id, (value) => localId(value, 'claim', 8)), supports: text(item.supports, 1500), source_kind: sourceKind, source_sha256: hash(item.source_sha256), quote, ...(coverage ? { coverage } : {}) }
}
function bridge(value: Record<string, unknown>): PublicBridge {
  const item = object(value, ['kind', 'title', 'field_left', 'field_right', 'explanation', 'mappings', 'open_question', 'strongest_objection', 'objection_verdict', 'novelty_limitations', 'citation_ids'])
  const left = text(item.field_left, 200), right = text(item.field_right, 200)
  if (normal(left) === normal(right)) return invalid()
  return { kind: 'bridge', title: text(item.title, 300), field_left: left, field_right: right, explanation: text(item.explanation, 3000), mappings: list(item.mappings, (raw) => { const mapping = object(raw, ['left', 'right', 'assumption']); return { left: text(mapping.left, 1000), right: text(mapping.right, 1000), assumption: text(mapping.assumption, 1500) } }, 1, 8), open_question: text(item.open_question, 1500), strongest_objection: text(item.strongest_objection, 2000), objection_verdict: text(item.objection_verdict, 2000), novelty_limitations: text(item.novelty_limitations, 2000), citation_ids: distinct(list(item.citation_ids, (value) => localId(value, 'citation', 64), 2, 12)) }
}
function answerClaim(value: unknown): PublicAnswerClaim {
  const item = object(value, ['id', 'text', 'label', 'derivation', 'caveats', 'citation_ids']), label = member(item.label, ['known', 'postulated', 'speculative']), derivation = nullable(item.derivation, (value) => text(value, 2000)), caveats = list(item.caveats, (value) => text(value, 1000), 0, 6)
  if (label === 'postulated' && derivation === null || label === 'speculative' && !caveats.length) return invalid()
  return { id: localId(item.id, 'claim', 8), text: text(item.text, 2000), label, derivation, caveats, citation_ids: distinct(list(item.citation_ids, (value) => localId(value, 'citation', 64), 1, 8)) }
}
function answer(value: Record<string, unknown>): PublicAnswer {
  const item = object(value, ['kind', 'question', 'summary', 'claims', 'limitations', 'unanswered_questions']), claims = list(item.claims, answerClaim, 1, 8)
  distinct(claims.map((claim) => claim.id))
  return { kind: 'answer', question: text(item.question, 4000), summary: text(item.summary, 3000), claims, limitations: list(item.limitations, (value) => text(value, 1000), 1, 8), unanswered_questions: list(item.unanswered_questions, (value) => text(value, 1000), 0, 8) }
}
export function parsePublicProjection(value: unknown): PublicProjection {
  const item = object(value, ['protocol', 'mode', 'validation_scope', 'finding', 'papers', 'citations'], ['field_selection'])
  if (item.protocol !== 'polymath-public-finding-v1' || item.validation_scope !== 'model_assessment_and_saved_quote_integrity') return invalid()
  const researchMode = mode(item.mode), rawFinding = object(item.finding, ['kind'], ['title', 'field_left', 'field_right', 'explanation', 'mappings', 'open_question', 'strongest_objection', 'objection_verdict', 'novelty_limitations', 'citation_ids', 'question', 'summary', 'claims', 'limitations', 'unanswered_questions'])
  const finding = rawFinding.kind === 'bridge' ? bridge(rawFinding) : rawFinding.kind === 'answer' ? answer(rawFinding) : invalid(), papers = list(item.papers, paper, 1, 64), citations = list(item.citations, citation, 1, 64)
  if ((researchMode === 'ask') !== (finding.kind === 'answer')) return invalid()
  distinct(papers.map((paper) => paper.id)); distinct(citations.map((citation) => citation.id))
  if (papers.some((paper, index) => paper.id !== `paper-${index + 1}`) || citations.some((citation, index) => citation.id !== `citation-${index + 1}`) || finding.kind === 'answer' && finding.claims.some((claim, index) => claim.id !== `claim-${index + 1}`)) return invalid()
  const aliasOwners = new Map<string, string>()
  for (const paper of papers) for (const [kind, value] of Object.entries(paper.identifiers)) {
    if (kind === 'arxiv_version') continue
    const alias = `${kind}:${value}`, owner = aliasOwners.get(alias)
    if (owner && owner !== paper.id) return invalid()
    aliasOwners.set(alias, paper.id)
  }
  const availablePapers = new Set(papers.map((paper) => paper.id)), byCitation = new Map(citations.map((citation) => [citation.id, citation])), usedPapers = new Set<string>(), emitted = new Map<string, number>()
  for (const cited of citations) { if (!availablePapers.has(cited.paper_id)) return invalid(); usedPapers.add(cited.paper_id); const previous = emitted.get(cited.paper_id) ?? 0, count = previous + Array.from(cited.quote.excerpt ?? '').length; if (count > 1000 || cited.quote.excerpt === null && previous <= 990) return invalid(); emitted.set(cited.paper_id, count) }
  if (usedPapers.size !== papers.length) return invalid()
  const attached = finding.kind === 'bridge' ? finding.citation_ids : finding.claims.flatMap((claim) => claim.citation_ids)
  if (distinct(attached).length !== citations.length || attached.some((id, index) => id !== citations[index].id || !byCitation.has(id))) return invalid()
  if (finding.kind === 'bridge') { if (citations.some((citation) => citation.side === 'answer' || citation.claim_id !== null)) return invalid() }
  else { for (const claim of finding.claims) for (const id of claim.citation_ids) { const citation = byCitation.get(id)!; if (citation.side !== 'answer' || citation.claim_id !== claim.id) return invalid() } }
  let selection: PublicProjection['field_selection']
  if (item.field_selection !== undefined) { const selected = object(item.field_selection, ['fields', 'rationale']), names = list(selected.fields, (value) => text(value, 100), 2, 4); distinct(names.map(normal)); selection = { fields: names, rationale: text(selected.rationale, 1000) } }
  if (researchMode === 'surprise' ? !selection || finding.kind !== 'bridge' || !selection.fields.map(normal).includes(normal(finding.field_left)) || !selection.fields.map(normal).includes(normal(finding.field_right)) : selection !== undefined) return invalid()
  const projection: PublicProjection = { protocol: 'polymath-public-finding-v1', mode: researchMode, validation_scope: 'model_assessment_and_saved_quote_integrity', finding, papers, citations, ...(selection ? { field_selection: selection } : {}) }
  if (new TextEncoder().encode(JSON.stringify(projection)).byteLength > 262144) return invalid()
  return projection
}

export function parseSharingCapabilities(value: unknown): SharingCapabilities {
  const item = object(value, ['sharing_available', 'reason']), available = bool(item.sharing_available)
  if (available ? item.reason !== null : item.reason !== 'sharing_unavailable') return invalid()
  return { sharing_available: available, reason: available ? null : 'sharing_unavailable' }
}
function preview(value: unknown): SharePreview {
  const item = object(value, ['source_result_id', 'projection_sha256', 'projection', 'expires_at'])
  return { source_result_id: uuid(item.source_result_id), projection_sha256: hash(item.projection_sha256), projection: parsePublicProjection(item.projection), expires_at: date(item.expires_at) }
}
export function parseShareRecord(value: unknown): ShareRecord {
  const item = object(value, ['public_id', 'source_result_id', 'projection_sha256', 'projection', 'created_at', 'expires_at', 'revoked_at', 'status', 'public_path', 'idempotency_key'])
  const id = handle(item.public_id), created = date(item.created_at), expires = date(item.expires_at), revoked = nullable(item.revoked_at, date), status = member(item.status, ['current', 'superseded', 'unsupported', 'revoked', 'expired'])
  if (item.public_path !== `/shared/${id}` || Date.parse(expires) <= Date.parse(created) || (status === 'revoked') !== (revoked !== null) || revoked !== null && Date.parse(revoked) < Date.parse(created)) return invalid()
  return { public_id: id, source_result_id: uuid(item.source_result_id), projection_sha256: hash(item.projection_sha256), projection: parsePublicProjection(item.projection), created_at: created, expires_at: expires, revoked_at: revoked, status, public_path: `/shared/${id}`, idempotency_key: uuid(item.idempotency_key) }
}
export function parsePublicFinding(value: unknown): PublicFinding {
  const item = object(value, ['public_id', 'projection_sha256', 'projection', 'created_at', 'expires_at', 'status']), created = date(item.created_at), expires = date(item.expires_at)
  if (Date.parse(expires) <= Date.parse(created)) return invalid()
  return { public_id: handle(item.public_id), projection_sha256: hash(item.projection_sha256), projection: parsePublicProjection(item.projection), created_at: created, expires_at: expires, status: member(item.status, ['current', 'superseded', 'unsupported']) }
}
function libraryItem(value: unknown): PublicLibraryItem {
  const item = object(value, ['public_id', 'kind', 'mode', 'title', 'summary', 'fields', 'projection_sha256', 'created_at', 'expires_at', 'status']), kind = member(item.kind, ['bridge', 'answer']), researchMode = mode(item.mode), fields = distinct(list(item.fields, (value) => text(value, 200), 0, 6)), created = date(item.created_at), expires = date(item.expires_at)
  if ((researchMode === 'ask') !== (kind === 'answer') || kind === 'answer' && fields.length || kind === 'bridge' && fields.length < 2 || Date.parse(expires) <= Date.parse(created) || item.status !== 'current') return invalid()
  return { public_id: handle(item.public_id), kind, mode: researchMode, title: text(item.title, kind === 'answer' ? 4000 : 300), summary: text(item.summary, 240), fields, projection_sha256: hash(item.projection_sha256), created_at: created, expires_at: expires, status: 'current' }
}
export function parsePublicLibrary(value: unknown): PublicLibrary {
  const item = object(value, ['items', 'next_cursor']), items = list(item.items, libraryItem, 0, 20), next = nullable(item.next_cursor, handle)
  distinct(items.map((item) => item.public_id))
  if (next !== null && next !== items.at(-1)?.public_id) return invalid()
  return { items, next_cursor: next }
}
function mapNode(value: unknown): PublicMapNode {
  const item = object(value, ['id', 'kind', 'label', 'public_id', 'public_path', 'paper_id', 'links']), kind = member(item.kind, ['finding', 'field', 'paper']), id = text(item.id, 150), publicId = nullable(item.public_id, handle), paperId = nullable(item.paper_id, (value) => localId(value, 'paper', 64)), links = list(item.links, scholarlyLink, 0, 3)
  distinct(links.map((link) => link.kind))
  if (kind === 'field' ? publicId !== null || item.public_path !== null || paperId !== null || !!links.length || !/^field:[a-f\d]{64}$/.test(id) : publicId === null || item.public_path !== `/shared/${publicId}` || (kind === 'finding' ? id !== `share:${publicId}` || paperId !== null || !!links.length : paperId === null || id !== `paper:${publicId}:${paperId}`)) return invalid()
  return { id, kind, label: text(item.label, kind === 'finding' ? 4000 : kind === 'paper' ? 1000 : 200), public_id: publicId, public_path: publicId === null ? null : `/shared/${publicId}`, paper_id: paperId, links }
}
export function parsePublicMap(value: unknown): PublicMap {
  const item = object(value, ['scope', 'nodes', 'edges', 'truncated', 'coverage']), coverage = object(item.coverage, ['max_findings', 'max_nodes', 'max_edges'])
  if (item.scope !== 'explicitly_shared_current_findings' || coverage.max_findings !== 60 || coverage.max_nodes !== 200 || coverage.max_edges !== 500) return invalid()
  const nodes = list(item.nodes, mapNode, 0, 200); distinct(nodes.map((node) => node.id))
  if (nodes.filter((node) => node.kind === 'finding').length > 60) return invalid()
  const byId = new Map(nodes.map((node) => [node.id, node]))
  const edges: PublicMapEdge[] = list(item.edges, (raw) => {
    const edge = object(raw, ['id', 'source', 'target', 'kind']), source = text(edge.source, 150), target = text(edge.target, 150), kind = member(edge.kind, ['field_left', 'field_right', 'field_selected', 'cites']), from = byId.get(source), to = byId.get(target)
    if (!from || !to || from.kind !== 'finding' || (kind === 'cites' ? to.kind !== 'paper' || to.public_id !== from.public_id : to.kind !== 'field') || edge.id !== `${kind}:${source}:${target}`) return invalid()
    return { id: text(edge.id, 400), source, target, kind }
  }, 0, 500)
  distinct(edges.map((edge) => edge.id))
  if (nodes.some((node) => node.kind === 'finding' ? !edges.some((edge) => edge.source === node.id && edge.kind === 'cites') : !edges.some((edge) => edge.target === node.id))) return invalid()
  return { scope: 'explicitly_shared_current_findings', nodes, edges, truncated: bool(item.truncated), coverage: { max_findings: 60, max_nodes: 200, max_edges: 500 } }
}

async function json(response: Response): Promise<unknown> {
  const size = response.headers.get('content-length')
  if (size !== null && (!/^\d+$/.test(size) || Number(size) > MAX_BYTES)) return invalid()
  const reader = response.body?.getReader()
  if (!reader) return invalid()
  const chunks: Uint8Array[] = []; let length = 0
  try {
    while (true) { const chunk = await reader.read(); if (chunk.done) break; length += chunk.value.byteLength; if (length > MAX_BYTES) { await reader.cancel(); return invalid() }; chunks.push(chunk.value) }
    const bytes = new Uint8Array(length); let offset = 0
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length }
    return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)) as unknown
  } catch { return invalid() } finally { reader.releaseLock() }
}
async function request<T>(path: string, parse: (value: unknown) => T, signal?: AbortSignal, write?: { csrf: string; body?: unknown }, isPublic = false): Promise<T> {
  if (write && (!HASH.test(write.csrf) || isPublic)) throw new SharingError('session_expired')
  if (signal?.aborted) throw new SharingError('aborted')
  const controller = new AbortController(), abort = () => controller.abort()
  signal?.addEventListener('abort', abort, { once: true })
  const timer = window.setTimeout(abort, 10000); let sent = false
  try {
    sent = true
    const response = await fetch(path, { credentials: isPublic ? 'omit' : 'same-origin', method: write ? 'POST' : 'GET', cache: 'no-store', redirect: 'error', ...(isPublic ? { referrerPolicy: 'no-referrer' as const } : {}), signal: controller.signal, headers: { Accept: 'application/json', ...(write ? { 'X-CSRF-Token': write.csrf, ...(write.body === undefined ? {} : { 'Content-Type': 'application/json' }) } : {}) }, ...(write?.body === undefined ? {} : { body: JSON.stringify(write.body) }) })
    if (!isPublic && (response.status === 401 || response.status === 403)) throw new SharingError('session_expired')
    if (response.status === 404) throw new SharingError('sharing_not_found')
    if (!response.ok) {
      let detail: string | undefined
      try { const item = object(await json(response), ['detail']); if (typeof item.detail === 'string' && ERROR_CODES.has(item.detail)) detail = item.detail } catch { /* Do not display private or untrusted server details. */ }
      const fallback = response.status === 404 ? 'sharing_not_found' : response.status === 409 ? 'sharing_projection_changed' : response.status === 422 ? 'sharing_invalid_request' : response.status === 429 ? 'sharing_active_limit' : 'unavailable'
      throw new SharingError(detail ?? fallback, !!write && response.status >= 500 && detail !== 'sharing_unavailable')
    }
    return parse(await json(response))
  } catch (error) {
    if (error instanceof SharingError) { if (write && sent && error.code === 'invalid_response') throw new SharingError(error.code, true); throw error }
    throw new SharingError(signal?.aborted ? 'aborted' : 'unavailable', !!write && sent)
  } finally { signal?.removeEventListener('abort', abort); window.clearTimeout(timer) }
}
function inputId(value: string): string { if (typeof value !== 'string' || !UUID.test(value)) throw new SharingError('sharing_invalid_request'); return value }
function inputHandle(value: string): string { if (typeof value !== 'string' || !HANDLE.test(value)) throw new SharingError('sharing_not_found'); return value }
function inputQuery(value: string): string { if (typeof value !== 'string' || Array.from(value).length > 200 || /[\u0000-\u001f\u007f\uD800-\uDFFF]/u.test(value)) throw new SharingError('sharing_invalid_request'); return value }

export const loadSharingCapabilities = (signal?: AbortSignal) => request(`${OWNER}/capabilities`, parseSharingCapabilities, signal)
export async function getSharePreview(sourceResultId: string, signal?: AbortSignal): Promise<SharePreview> {
  return request(`${OWNER}/preview?${new URLSearchParams({ source_result_id: inputId(sourceResultId) })}`, (value) => { const result = preview(value); return result.source_result_id === sourceResultId ? result : invalid() }, signal)
}
export async function listShares(sourceResultId: string, signal?: AbortSignal, before?: string): Promise<ShareHistory> {
  const query = new URLSearchParams({ source_result_id: inputId(sourceResultId), limit: '5' })
  if (before !== undefined) query.set('before', inputHandle(before))
  return request(`${OWNER}?${query}`, (value) => { const item = object(value, ['shares', 'next_cursor']), shares = list(item.shares, parseShareRecord, 0, 5), next = nullable(item.next_cursor, handle); distinct(shares.map((share) => share.public_id)); if (shares.some((share) => share.source_result_id !== sourceResultId || share.public_id === before) || next !== null && (next !== shares.at(-1)?.public_id || next === before)) return invalid(); return { shares, next_cursor: next } }, signal)
}
export async function createShare(body: CreateShare, csrf: string, signal?: AbortSignal): Promise<ShareRecord> {
  let pinned: CreateShare
  try { const item = object(body, ['source_result_id', 'expected_projection_sha256', 'idempotency_key']); pinned = { source_result_id: uuid(item.source_result_id), expected_projection_sha256: hash(item.expected_projection_sha256), idempotency_key: uuid(item.idempotency_key) } } catch { throw new SharingError('sharing_invalid_request') }
  return request(OWNER, (value) => { const record = parseShareRecord(value); return record.source_result_id === pinned.source_result_id && record.projection_sha256 === pinned.expected_projection_sha256 && record.idempotency_key === pinned.idempotency_key ? record : invalid() }, signal, { csrf, body: pinned })
}
export async function revokeShare(publicId: string, csrf: string, signal?: AbortSignal): Promise<ShareRecord> {
  return request(`${OWNER}/${inputHandle(publicId)}/revoke`, (value) => { const record = parseShareRecord(value); return record.public_id === publicId && record.status === 'revoked' && record.revoked_at !== null ? record : invalid() }, signal, { csrf })
}
export async function getPublicFinding(publicId: string, signal?: AbortSignal): Promise<PublicFinding> {
  return request(`${PUBLIC}/findings/${inputHandle(publicId)}`, (value) => { const record = parsePublicFinding(value); return record.public_id === publicId ? record : invalid() }, signal, undefined, true)
}
export async function loadPublicLibrary(query: string, signal?: AbortSignal, before?: string): Promise<PublicLibrary> {
  const search = new URLSearchParams({ q: inputQuery(query), limit: '20' })
  if (before !== undefined) search.set('before', inputHandle(before))
  return request(`${PUBLIC}/library?${search}`, (value) => { const page = parsePublicLibrary(value); if (before !== undefined && (page.next_cursor === before || page.items.some((item) => item.public_id === before))) return invalid(); return page }, signal, undefined, true)
}
export async function loadPublicMap(query: string, signal?: AbortSignal): Promise<PublicMap> { return request(`${PUBLIC}/map?${new URLSearchParams({ q: inputQuery(query) })}`, parsePublicMap, signal, undefined, true) }
