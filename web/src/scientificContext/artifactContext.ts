import type { MemoryReadReference } from '../research/types'
import { parseBibliography } from '../research/bibliography'
import { parseSourceCoverage, validateSourceCoverageContext } from '../research/sourceCoverage'
import type { StageCommitReceipt } from '../discovery/types'
import type { AppliedAssessmentContext, ArtifactScientificContext, ArtifactScientificContextBinding, ScientificAncestor, ScientificAssessmentCitation, ScientificAssessmentQuote, ScientificParentEdge, ScientificSourceReference, SourceRevisionContext, StageOriginContext } from './types'
import { canonical, equal, hash, id, integer, invalid, list, member, nullable, object, sha256, text } from './primitives'
import { parseScientificFinding } from './findings'
import { parseAskOverviewBinding, validateAnswerOverview } from './overviewBinding'
export function parseMemoryReference(value: unknown): MemoryReadReference { const item = object(value, ['result_id', 'run_id', 'content_sha256']); return { result_id: id(item.result_id), run_id: id(item.run_id), content_sha256: hash(item.content_sha256) } }
function source(value: unknown): ScientificSourceReference {
  const item = object(value, ['source_id', 'title', 'url'], ['bibliography', 'coverage']), url = text(item.url, 2000), bibliography = parseBibliography(item.bibliography, invalid), coverage = parseSourceCoverage(item.coverage, invalid)
  if (!/^https:\/\/[^\s]+$/u.test(url)) return invalid()
  if (item.bibliography !== undefined && item.bibliography !== null) { const bibliographic = object(item.bibliography, [], ['authors', 'authors_truncated', 'publication_year', 'publication_date', 'venue', 'doi', 'arxiv_id', 'arxiv_version', 'openalex_id', 'license']); if (Array.isArray(bibliographic.authors)) for (const author of bibliographic.authors) object(author, ['name'], ['orcid']) }
  const result = { source_id: text(item.source_id, 200), title: text(item.title, 1000), url, ...(bibliography ? { bibliography } : {}), ...(coverage ? { coverage } : {}) }; validateSourceCoverageContext(result, invalid); return result
}
function citation(value: unknown): ScientificAssessmentCitation { const item = object(value, ['retrieval_id', 'quote', 'supports']); return { retrieval_id: id(item.retrieval_id), quote: text(item.quote, 4000, 10), supports: text(item.supports, 1500) } }
function assessmentQuote(value: unknown): ScientificAssessmentQuote {
  const item = object(value, ['retrieval_id', 'quote', 'supports', 'version_id', 'source', 'source_sha256', 'content_kind', 'start', 'end']), quoted = citation({ retrieval_id: item.retrieval_id, quote: item.quote, supports: item.supports }), saved = source(item.source), kind = member(item.content_kind, ['abstract', 'full_text']), start = integer(item.start, 0, 19999), end = integer(item.end, 1, 20000)
  if (end - start !== Array.from(quoted.quote).length || saved.coverage && (kind !== 'full_text' || end > saved.coverage.end - saved.coverage.start)) return invalid()
  return { ...quoted, version_id: id(item.version_id), source: saved, source_sha256: hash(item.source_sha256), content_kind: kind, start, end }
}
function applied(value: unknown): AppliedAssessmentContext {
  const item = object(value, ['assessment_id', 'run_id', 'payload_sha256', 'applied', 'head_revision', 'prior_reference', 'candidate_reference', 'disposition', 'rationale', 'quotes', 'limitations']), prior = parseMemoryReference(item.prior_reference), candidate = nullable(item.candidate_reference, parseMemoryReference), disposition = member(item.disposition, ['unchanged', 'revised', 'unsupported']), runId = id(item.run_id)
  if (item.applied !== true || (disposition === 'revised') !== (candidate !== null) || candidate && (candidate.result_id === prior.result_id || candidate.run_id !== runId)) return invalid()
  const rationale = list(item.rationale, (raw) => { const statement = object(raw, ['text', 'citations']); return { text: text(statement.text, 2000), citations: list(statement.citations, citation, 1, 8) } }, 1, 8), quotes = list(item.quotes, assessmentQuote, 0, 64), citations = rationale.flatMap((statement) => statement.citations)
  if (citations.length !== quotes.length || citations.some((cited, ordinal) => !equal(cited, { retrieval_id: quotes[ordinal].retrieval_id, quote: quotes[ordinal].quote, supports: quotes[ordinal].supports }))) return invalid()
  return { assessment_id: id(item.assessment_id), run_id: runId, payload_sha256: hash(item.payload_sha256), applied: true, head_revision: integer(item.head_revision, 1, 2147483647), prior_reference: prior, candidate_reference: candidate, disposition, rationale, quotes, limitations: list(item.limitations, (value) => text(value, 1000), 1, 8) }
}
function revision(value: unknown): SourceRevisionContext {
  const item = object(value, ['root_reference', 'current_reference', 'revision', 'state_at_admission', 'applied_assessments']), root = parseMemoryReference(item.root_reference), current = parseMemoryReference(item.current_reference), count = integer(item.revision, 0, 64), history = list(item.applied_assessments, applied, 0, 64)
  if (item.state_at_admission !== 'supported' || count !== history.length) return invalid()
  let cursor = root, supported = true; const assessments = new Set<string>(), runs = new Set<string>(), results = new Set([root.result_id])
  for (const [index, value] of history.entries()) {
    if (value.head_revision !== index + 1 || !equal(value.prior_reference, cursor) || assessments.has(value.assessment_id) || runs.has(value.run_id)) return invalid()
    assessments.add(value.assessment_id); runs.add(value.run_id)
    if (value.disposition === 'revised') { if (results.has(value.candidate_reference!.result_id)) return invalid(); cursor = value.candidate_reference!; results.add(cursor.result_id); supported = true }
    else if (value.disposition === 'unsupported') supported = false
  }
  if (!supported || !equal(cursor, current)) return invalid()
  return { root_reference: root, current_reference: current, revision: count, state_at_admission: 'supported', applied_assessments: history }
}
function receipt(value: unknown): StageCommitReceipt {
  const item = object(value, ['stage_id', 'run_id', 'ordinal', 'result_id', 'result_sha256', 'depth', 'assessment', 'parent_refs', 'action_ordinal', 'model_request_id']), assessment = object(item.assessment, ['novelty', 'novelty_rationale', 'confidence', 'confidence_score', 'confidence_scope', 'confidence_rationale', 'limitations']), resultId = id(item.result_id), parents = list(item.parent_refs, parseMemoryReference, 0, 4), depth = integer(item.depth, 1, 3)
  if (![0, 2, 3, 4].includes(parents.length) || new Set(parents.map((parent) => parent.result_id)).size !== parents.length || parents.some((parent) => parent.result_id === resultId) || !parents.length && depth !== 1 || assessment.confidence_scope !== 'uncalibrated_model_judgement' || typeof item.model_request_id !== 'string' || !/^model-\d{4}$/.test(item.model_request_id)) return invalid()
  return { stage_id: id(item.stage_id), run_id: id(item.run_id), ordinal: integer(item.ordinal, 0, 3), result_id: resultId, result_sha256: hash(item.result_sha256), depth, assessment: { novelty: member(assessment.novelty, ['known', 'partly_known', 'not_found_in_bounded_search', 'uncertain']), novelty_rationale: text(assessment.novelty_rationale, 2000), confidence: member(assessment.confidence, ['tentative', 'moderate', 'strong']), confidence_score: integer(assessment.confidence_score, 0, 100), confidence_scope: 'uncalibrated_model_judgement', confidence_rationale: text(assessment.confidence_rationale, 2000), limitations: list(assessment.limitations, (value) => text(value, 1000), 1, 8) }, parent_refs: parents, action_ordinal: integer(item.action_ordinal, 1, 20), model_request_id: item.model_request_id }
}
function stageReference(value: StageCommitReceipt): MemoryReadReference { return { result_id: value.result_id, run_id: value.run_id, content_sha256: value.result_sha256 } }
function origin(value: unknown): StageOriginContext { const item = object(value, ['receipt', 'receipt_sha256', 'relationship', 'revision_of']), stage = receipt(item.receipt), prior = nullable(item.revision_of, parseMemoryReference); if (prior && equal(prior, stageReference(stage))) return invalid(); return { receipt: stage, receipt_sha256: hash(item.receipt_sha256), relationship: member(item.relationship, ['exact_stage_result', 'reassessed_descendant']), revision_of: prior } }
function ancestor(value: unknown): ScientificAncestor {
  const item = object(value, ['reference', 'finding', 'stage_origin', 'overview_binding']), reference = parseMemoryReference(item.reference), finding = parseScientificFinding(item.finding), stage = nullable(item.stage_origin, origin), binding = nullable(item.overview_binding, parseAskOverviewBinding)
  if (stage && (finding.outcome !== 'bridge' || !equal(stageReference(stage.receipt), reference) || stage.relationship !== 'exact_stage_result')) return invalid()
  if (binding) { if (finding.outcome !== 'answer') return invalid(); validateAnswerOverview(finding, binding) }
  return { reference, finding, stage_origin: stage, overview_binding: binding }
}
function edge(value: unknown): ScientificParentEdge { const item = object(value, ['child', 'parent', 'ordinal', 'kind', 'assessment_id', 'assessment_sha256']), child = parseMemoryReference(item.child), parent = parseMemoryReference(item.parent), kind = member(item.kind, ['premise', 'revision', 'reassessment']), assessmentId = nullable(item.assessment_id, id), assessmentHash = nullable(item.assessment_sha256, hash); if (child.result_id === parent.result_id || (assessmentId !== null) !== (assessmentHash !== null) || (kind === 'reassessment') !== (assessmentId !== null)) return invalid(); return { child, parent, ordinal: integer(item.ordinal, 0, 4), kind, assessment_id: assessmentId, assessment_sha256: assessmentHash } }
function parseContext(value: unknown): ArtifactScientificContext {
  const item = object(value, ['protocol', 'source_reference', 'source_revision', 'stage_origin', 'ancestry_nodes', 'ancestry_edges', 'prior_formalisation'])
  if (item.protocol !== 'polymath-artifact-scientific-context-v1') return invalid()
  const source = parseMemoryReference(item.source_reference), history = revision(item.source_revision), stage = nullable(item.stage_origin, origin), nodes = list(item.ancestry_nodes, ancestor, 1, 128), edges = list(item.ancestry_edges, edge, 0, 512), prior = nullable(item.prior_formalisation, (raw) => { const prior = object(raw, ['artifact_id', 'source_reference', 'payload_sha256', 'basis_sha256', 'context_sha256']); return { artifact_id: id(prior.artifact_id), source_reference: parseMemoryReference(prior.source_reference), payload_sha256: hash(prior.payload_sha256), basis_sha256: hash(prior.basis_sha256), context_sha256: nullable(prior.context_sha256, hash) } })
  if (!equal(source, history.current_reference) || !equal(nodes[0].reference, source)) return invalid()
  const byId = new Map(nodes.map((node) => [node.reference.result_id, node])); if (byId.size !== nodes.length) return invalid()
  const known = (reference: MemoryReadReference) => { const node = byId.get(reference.result_id); if (!node || !equal(node.reference, reference)) return invalid() }
  known(history.root_reference)
  const children = new Map<string, ScientificParentEdge[]>(), positions = new Set<string>()
  for (const value of edges) { known(value.child); known(value.parent); const position = `${value.child.result_id}:${value.ordinal}`; if (positions.has(position)) return invalid(); positions.add(position); children.set(value.child.result_id, [...(children.get(value.child.result_id) ?? []), value]) }
  for (const values of children.values()) if (values.some((value, ordinal) => value.ordinal !== ordinal)) return invalid()
  const visited = new Set<string>(), active = new Set<string>(), order: string[] = []
  function visit(identifier: string) { if (active.has(identifier)) return invalid(); if (visited.has(identifier)) return; active.add(identifier); visited.add(identifier); order.push(identifier); for (const value of children.get(identifier) ?? []) visit(value.parent.result_id); active.delete(identifier) }
  visit(source.result_id)
  if (visited.size !== nodes.length || !equal(order, nodes.map((node) => node.reference.result_id)) || !equal(edges, order.flatMap((identifier) => children.get(identifier) ?? []))) return invalid()
  const rootOrigin = byId.get(history.root_reference.result_id)!.stage_origin
  if (!!rootOrigin !== !!stage) return invalid()
  if (stage) { if (!equal(stageReference(stage.receipt), history.root_reference) || (stage.relationship === 'exact_stage_result') !== equal(stageReference(stage.receipt), source) || !rootOrigin || !equal(stage.receipt, rootOrigin.receipt) || stage.receipt_sha256 !== rootOrigin.receipt_sha256 || !equal(stage.revision_of, rootOrigin.revision_of)) return invalid() }
  for (const node of nodes) if (node.stage_origin) { const origin = node.stage_origin, expected = [...origin.receipt.parent_refs.map((parent) => ({ parent, kind: 'premise' })), ...(origin.revision_of ? [{ parent: origin.revision_of, kind: 'revision' }] : [])], actual = (children.get(node.reference.result_id) ?? []).map((edge) => ({ parent: edge.parent, kind: edge.kind })); if (!equal(expected, actual)) return invalid() }
  for (const value of history.applied_assessments) { known(value.prior_reference); if (value.candidate_reference) { known(value.candidate_reference); const actual = (children.get(value.candidate_reference.result_id) ?? []).filter((edge) => edge.kind === 'reassessment').map((edge) => [edge.parent, edge.assessment_id, edge.assessment_sha256]); if (!equal(actual, [[value.prior_reference, value.assessment_id, value.payload_sha256]])) return invalid() } }
  if (prior && !equal(prior.source_reference, source)) return invalid()
  const result: ArtifactScientificContext = { protocol: 'polymath-artifact-scientific-context-v1', source_reference: source, source_revision: history, stage_origin: stage, ancestry_nodes: nodes, ancestry_edges: edges, prior_formalisation: prior }
  if (new TextEncoder().encode(canonical(result)).byteLength > 262144) return invalid('artifact_context_too_large')
  return result
}
/** Async verifies supplied canonical digests. It does not establish actual application/ownership. */
export async function parseArtifactScientificContextBinding(value: unknown): Promise<ArtifactScientificContextBinding> {
  const item = object(value, ['context', 'context_sha256']), context = parseContext(item.context), contextHash = hash(item.context_sha256)
  if (await sha256(context) !== contextHash) return invalid('scientific_context_hash_mismatch')
  const origins = [context.stage_origin, ...context.ancestry_nodes.map((node) => node.stage_origin)].filter((value): value is StageOriginContext => value !== null)
  for (const origin of origins) if (await sha256(origin.receipt) !== origin.receipt_sha256) return invalid('scientific_stage_receipt_hash_mismatch')
  return { context, context_sha256: contextHash }
}
export async function parseArtifactContextMetadata(data: Record<string, unknown>, expected?: boolean): Promise<ArtifactScientificContextBinding | undefined> {
  if (!data || typeof data !== 'object' || Array.isArray(data) || expected !== undefined && typeof expected !== 'boolean') return invalid()
  if (!Object.hasOwn(data, 'scientific_context')) { if (expected === true) return invalid('scientific_context_binding_required'); return undefined }
  if (expected === false) return invalid('scientific_context_not_admitted')
  if (data.scientific_context === null || data.scientific_context === undefined) return invalid()
  const value = await parseArtifactScientificContextBinding(data.scientific_context), reference = parseMemoryReference(data.reference), finding = parseScientificFinding(data.finding)
  if (!equal(reference, value.context.source_reference) || !equal(finding, value.context.ancestry_nodes[0].finding)) return invalid('scientific_context_basis_mismatch')
  return value
}
