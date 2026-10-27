export type KnowledgeKind = 'field' | 'source' | 'finding'

export interface KnowledgeItem {
  id: string
  type: 'source' | 'finding'
  title: string
  run_id: string
  result_id: string | null
  source_id: string | null
  content_kind: 'abstract' | 'full_text' | null
  summary: string
}

export interface KnowledgeNode {
  id: string
  kind: KnowledgeKind
  label: string
  run_id: string | null
  result_id: string | null
  source_id: string | null
  content_kind: 'abstract' | 'full_text' | null
  finding_outcome?: 'bridge' | 'answer' | null
}

export interface KnowledgeEdge {
  id: string
  source: string
  target: string
  kind: 'maps_to' | 'supported_by' | 'selected_field' | 'investigated_from'
}

export interface KnowledgeData {
  items: KnowledgeItem[]
  nodes: KnowledgeNode[]
  edges: KnowledgeEdge[]
  truncated: boolean
  findingsConsidered: number
}

export class KnowledgeError extends Error {
  constructor(readonly code: 'session_expired' | 'unavailable' | 'invalid_response') {
    super(code)
  }
}

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new KnowledgeError('invalid_response')
  return value as Record<string, unknown>
}

function text(value: unknown, maximum: number, empty = false): string {
  if (typeof value !== 'string' || value.length > maximum || (!empty && !value.trim())) throw new KnowledgeError('invalid_response')
  return value
}

function nullableText(value: unknown, maximum: number): string | null {
  return value === null ? null : text(value, maximum)
}

function identity(value: unknown, nullable = false): string | null {
  if (nullable && value === null) return null
  const parsed = text(value, 36)
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(parsed)) throw new KnowledgeError('invalid_response')
  return parsed
}

function kind(value: unknown): 'abstract' | 'full_text' | null {
  if (value !== null && value !== 'abstract' && value !== 'full_text') throw new KnowledgeError('invalid_response')
  return value
}

function boolean(value: unknown): boolean {
  if (typeof value !== 'boolean') throw new KnowledgeError('invalid_response')
  return value
}

function collection(value: unknown, maximum: number): unknown[] {
  if (!Array.isArray(value) || value.length > maximum) throw new KnowledgeError('invalid_response')
  return value
}

function checkCoverage(value: unknown): Record<string, unknown> {
  const coverage = record(value)
  if (coverage.scope !== 'current_browser_session') throw new KnowledgeError('invalid_response')
  return coverage
}

function unique(items: { id: string }[]) {
  if (new Set(items.map((item) => item.id)).size !== items.length) throw new KnowledgeError('invalid_response')
}

async function response(path: string, signal: AbortSignal): Promise<unknown> {
  try {
    const result = await fetch(path, { signal, credentials: 'same-origin', headers: { Accept: 'application/json' }, cache: 'no-store' })
    if (result.status === 401 || result.status === 403) throw new KnowledgeError('session_expired')
    if (!result.ok) throw new KnowledgeError('unavailable')
    return await result.json()
  } catch (error) {
    if (error instanceof KnowledgeError || signal.aborted) throw error
    throw new KnowledgeError('unavailable')
  }
}

export async function loadKnowledge(query: string, signal: AbortSignal): Promise<KnowledgeData> {
  if (query.length > 200) throw new KnowledgeError('invalid_response')
  const search = new URLSearchParams({ q: query, limit: '20' })
  const graphQuery = new URLSearchParams({ q: query })
  const [rawSearch, rawGraph] = await Promise.all([
    response(`/api/v1/knowledge?${search}`, signal),
    response(`/api/v1/knowledge/graph?${graphQuery}`, signal),
  ])
  const found = record(rawSearch)
  const graph = record(rawGraph)
  checkCoverage(found.coverage)
  const coverage = checkCoverage(graph.coverage)
  if (!Number.isSafeInteger(coverage.findings_considered) || (coverage.findings_considered as number) < 0 || (coverage.findings_considered as number) > 60) throw new KnowledgeError('invalid_response')
  const items: KnowledgeItem[] = collection(found.items, 50).map((item) => {
    const entry = record(item)
    if (entry.type !== 'source' && entry.type !== 'finding') throw new KnowledgeError('invalid_response')
    return {
      id: text(entry.id, 300), type: entry.type, title: text(entry.title, 4000),
      run_id: identity(entry.run_id) as string, result_id: identity(entry.result_id, true),
      source_id: nullableText(entry.source_id, 200), content_kind: kind(entry.content_kind),
      summary: text(entry.summary, 3000, true),
    }
  })
  const nodes: KnowledgeNode[] = collection(graph.nodes, 200).map((item) => {
    const node = record(item)
    if (node.kind !== 'field' && node.kind !== 'source' && node.kind !== 'finding') throw new KnowledgeError('invalid_response')
    const outcome = node.finding_outcome === undefined ? null : node.finding_outcome
    if (outcome !== null && (node.kind !== 'finding' || outcome !== 'bridge' && outcome !== 'answer')) throw new KnowledgeError('invalid_response')
    return {
      id: text(node.id, 300), kind: node.kind, label: text(node.label, 4000),
      run_id: identity(node.run_id, true), result_id: identity(node.result_id, true),
      source_id: nullableText(node.source_id, 200), content_kind: kind(node.content_kind),
      finding_outcome: outcome,
    }
  })
  const nodeIds = new Set(nodes.map((node) => node.id))
  const edges: KnowledgeEdge[] = collection(graph.edges, 500).map((item) => {
    const edge = record(item)
    if (edge.kind !== 'maps_to' && edge.kind !== 'supported_by' && edge.kind !== 'selected_field' && edge.kind !== 'investigated_from') throw new KnowledgeError('invalid_response')
    const source = text(edge.source, 300)
    const target = text(edge.target, 300)
    if (!nodeIds.has(source) || !nodeIds.has(target)) throw new KnowledgeError('invalid_response')
    if (edge.kind === 'investigated_from' && (source === target || nodes.find((node) => node.id === source)?.kind !== 'finding' || nodes.find((node) => node.id === target)?.kind !== 'finding')) throw new KnowledgeError('invalid_response')
    return { id: text(edge.id, 650), source, target, kind: edge.kind }
  })
  unique(items)
  unique(nodes)
  unique(edges)
  const searchTruncated = boolean(found.truncated)
  const graphTruncated = boolean(graph.truncated)
  return {
    items, nodes, edges,
    truncated: searchTruncated || graphTruncated,
    findingsConsidered: coverage.findings_considered as number,
  }
}
