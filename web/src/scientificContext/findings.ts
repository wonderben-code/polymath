import type { AnswerClaim, AnswerFinding, BridgeFinding } from '../research/types'
import type { ScientificFinding } from './types'
import { invalid, list, member, nullable, object, text } from './primitives'
function quote(raw: unknown) { const item = object(raw, ['source_id', 'quote', 'supports']); return { source_id: text(item.source_id, 200), quote: text(item.quote, 4000, 10), supports: text(item.supports, 1500) } }
function claim(raw: unknown): AnswerClaim {
  const item = object(raw, ['id', 'text', 'label', 'evidence'], ['derivation', 'caveats']), identifier = text(item.id, 64), label = member(item.label, ['known', 'postulated', 'speculative']), derivation = item.derivation === undefined ? null : nullable(item.derivation, (value) => text(value, 2000)), caveats = item.caveats === undefined ? [] : list(item.caveats, (value) => text(value, 1000), 0, 6)
  if (!/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(identifier) || label === 'postulated' && !derivation || label === 'speculative' && !caveats.length) return invalid()
  return { id: identifier, text: text(item.text, 2000), label, evidence: list(item.evidence, quote, 1, 8), derivation, caveats }
}
export function parseScientificFinding(raw: unknown): ScientificFinding {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return invalid()
  if ((raw as Record<string, unknown>).outcome === 'answer') {
    const item = object(raw, ['outcome', 'question', 'summary', 'claims', 'limitations'], ['unanswered_questions']), claims = list(item.claims, claim, 1, 8)
    if (new Set(claims.map((claim) => claim.id)).size !== claims.length) return invalid()
    return { outcome: 'answer', question: text(item.question, 4000), summary: text(item.summary, 3000), claims, limitations: list(item.limitations, (value) => text(value, 1000), 1, 8), unanswered_questions: item.unanswered_questions === undefined ? [] : list(item.unanswered_questions, (value) => text(value, 1000), 0, 8) } satisfies AnswerFinding
  }
  const item = object(raw, ['outcome', 'title', 'field_left', 'field_right', 'explanation', 'mappings', 'open_question', 'strongest_objection', 'objection_verdict', 'novelty_limitations', 'evidence'])
  if (item.outcome !== 'bridge') return invalid()
  const left = text(item.field_left, 200), right = text(item.field_right, 200), fold = (value: string) => value.trim().replace(/\s+/gu, ' ').toLowerCase().replaceAll('ß', 'ss').replaceAll('ς', 'σ')
  if (fold(left) === fold(right)) return invalid()
  return { outcome: 'bridge', title: text(item.title, 300), field_left: left, field_right: right, explanation: text(item.explanation, 3000), mappings: list(item.mappings, (raw) => { const mapping = object(raw, ['left', 'right', 'assumption']); return { left: text(mapping.left, 1000), right: text(mapping.right, 1000), assumption: text(mapping.assumption, 1500) } }, 1, 8), open_question: text(item.open_question, 1500), strongest_objection: text(item.strongest_objection, 2000), objection_verdict: text(item.objection_verdict, 2000), novelty_limitations: text(item.novelty_limitations, 2000), evidence: list(item.evidence, (raw) => { const cited = object(raw, ['source_id', 'side', 'quote', 'supports']); return { source_id: text(cited.source_id, 200), side: member(cited.side, ['left', 'right']), quote: text(cited.quote, 4000, 10), supports: text(cited.supports, 1500) } }, 2, 12) } satisfies BridgeFinding
}
