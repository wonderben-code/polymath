import { describe, expect, it } from 'vitest'
import { parseResult, ResearchApiError } from './client'
import { BIBLIOGRAPHY } from './bibliography.test-support'

// Every source and claim in this file is synthetic. These tests verify the
// browser's record boundary, not scientific findings or a live Meta model.
const quoteA = 'Synthetic source A reports a bounded redundancy result.'
const quoteB = 'Synthetic source B reports a conditional pathway result.'

function sources() {
  return [
    { source_id: 'a', title: 'Synthetic source A', url: 'https://example.org/synthetic-a', text: quoteA, content_kind: 'abstract', retrieved_at: '2026-10-27T12:00:00Z', extraction_method: 'synthetic_fixture' },
    { source_id: 'b', title: 'Synthetic source B', url: 'https://example.org/synthetic-b', text: quoteB, content_kind: 'full_text', retrieved_at: '2026-10-27T12:00:00Z', extraction_method: 'synthetic_fixture' },
  ]
}

function checkedQuote(sourceId: string, claimId: string | null, side = 'answer') {
  const quote = sourceId === 'a' ? quoteA : quoteB
  return { source_id: sourceId, claim_id: claimId, side, quote, supports: 'A synthetic fixture claim.', start: 0, end: Array.from(quote).length, source_sha256: (sourceId === 'a' ? 'a' : 'b').repeat(64), content_kind: sourceId === 'a' ? 'abstract' : 'full_text' }
}

function base() {
  return {
    sources: sources(), events: [], error_code: null,
    usage: { model_calls: 6, input_tokens_reported: 100, output_tokens_reported: 80, calls_without_usage: 0 },
    validation_scope: 'source_identity_and_quote_integrity',
  }
}

function answer() {
  const evidence = (sourceId: string) => ({ source_id: sourceId, quote: sourceId === 'a' ? quoteA : quoteB, supports: 'A synthetic fixture claim.' })
  return {
    ...base(), status: 'answer', mode: 'ask', field_selections: [],
    finding: {
      outcome: 'answer', question: 'What follows from these synthetic fixtures?',
      summary: 'The fixtures support a narrow comparison with unresolved assumptions.',
      claims: [
        { id: 'reported', text: 'Source A states a bounded fixture result.', label: 'known', evidence: [evidence('a')], derivation: null as string | null, caveats: ['This is a synthetic source-supported classification.'] },
        { id: 'inferred', text: 'The two fixture structures may be compared under assumptions.', label: 'postulated', evidence: [evidence('a'), evidence('b')], derivation: 'A conditional mapping combines the two saved fixture premises.', caveats: ['Equivalence is not established.'] },
        { id: 'possible', text: 'The comparison might suggest another fixture experiment.', label: 'speculative', evidence: [evidence('b')], derivation: null as string | null, caveats: ['The experiment has not been performed.'] },
      ],
      limitations: ['Synthetic records cannot establish scientific truth.'],
      unanswered_questions: ['Would a realistic noise model preserve the mapping?'],
    },
    quotes: [checkedQuote('a', 'reported'), checkedQuote('a', 'inferred'), checkedQuote('b', 'inferred'), checkedQuote('b', 'possible')],
  }
}

function legacyBridge() {
  const left = checkedQuote('a', null, 'left')
  const right = checkedQuote('b', null, 'right')
  // This is the persisted shape from before mode/field selection/claim links.
  const { claim_id: _leftClaim, ...leftQuote } = left
  const { claim_id: _rightClaim, ...rightQuote } = right
  return {
    ...base(), status: 'bridge',
    finding: {
      outcome: 'bridge', title: 'Synthetic cross-field analogy',
      field_left: 'Information theory', field_right: 'Biology',
      explanation: 'A conditional fixture comparison.',
      mappings: [{ left: 'Redundancy', right: 'Pathway redundancy', assumption: 'A specified noise model.' }],
      open_question: 'Does the fixture analogy survive a different perturbation?',
      strongest_objection: 'Mechanisms differ.', objection_verdict: 'Only a narrow analogy remains.',
      novelty_limitations: 'No scientific novelty is established.',
      evidence: [
        { source_id: 'a', side: 'left', quote: quoteA, supports: 'A synthetic fixture claim.' },
        { source_id: 'b', side: 'right', quote: quoteB, supports: 'A synthetic fixture claim.' },
      ],
    },
    quotes: [leftQuote, rightQuote],
  }
}

function surprise() {
  return {
    ...legacyBridge(), mode: 'surprise',
    field_selections: [{ fields: ['Information theory', 'Biology'], rationale: 'The model selected these fixture fields to investigate a conditional mapping.' }],
  }
}

function rejects(value: unknown) {
  let caught: unknown
  try {
    parseResult(value)
  } catch (error) {
    caught = error
  }
  expect(caught).toBeInstanceOf(ResearchApiError)
  expect(caught).toMatchObject({ code: 'invalid_response' })
}

describe('saved Ask and Surprise record boundary', () => {
  it('preserves exact full-text window coverage and rejects document or quotation range mismatches', () => {
    const fixture = legacyBridge(), snapshot = '99999999-9999-4999-8999-999999999999'
    const coverage = { snapshot_id: snapshot, snapshot_sha256: 'c'.repeat(64), arxiv_id: BIBLIOGRAPHY.arxiv_id, arxiv_version: 2, start: 100, end: 100 + Array.from(quoteA).length, total: 10000 }
    const sourceId = `arxiv-html:${snapshot}:${coverage.start}:${coverage.end}`
    const value = { ...fixture, sources: [{ ...fixture.sources[0], source_id: sourceId, content_kind: 'full_text', url: 'https://arxiv.org/html/2610.00001v2', bibliography: BIBLIOGRAPHY, coverage }, fixture.sources[1]], finding: { ...fixture.finding, evidence: [{ ...fixture.finding.evidence[0], source_id: sourceId }, fixture.finding.evidence[1]] }, quotes: [{ ...fixture.quotes[0], source_id: sourceId, content_kind: 'full_text' }, fixture.quotes[1]] }
    expect(parseResult(value).sources[0].coverage).toEqual(coverage)
    expect(parseResult(legacyBridge()).sources[0]).not.toHaveProperty('coverage')
    for (const change of [{ text: quoteA + 'x' }, { content_kind: 'abstract' }, { url: 'https://arxiv.org/abs/2610.00001v2' }, { bibliography: { ...BIBLIOGRAPHY, arxiv_version: 3 } }]) rejects({ ...value, sources: [{ ...value.sources[0], ...change }, value.sources[1]] })
    rejects({ ...value, quotes: [{ ...value.quotes[0], end: value.quotes[0].end + 1 }, value.quotes[1]] })
  })
  it('preserves only valid immutable memory references and defaults older records to no reads', () => {
    expect(parseResult(legacyBridge()).memory_reads).toEqual([])
    const memory = { result_id: '11111111-1111-4111-8111-111111111111', run_id: '22222222-2222-4222-8222-222222222222', content_sha256: 'a'.repeat(64) }
    expect(parseResult({ ...legacyBridge(), memory_reads: [memory] }).memory_reads).toEqual([memory])
    rejects({ ...legacyBridge(), memory_reads: [{ ...memory, content_sha256: 'not-a-hash' }] })
    rejects({ ...legacyBridge(), memory_reads: [{ ...memory, result_id: 'not-an-id' }] })
    rejects({ ...legacyBridge(), memory_reads: [memory, memory] })
  })

  it('keeps older saved bridge records readable without inventing selected fields', () => {
    const parsed = parseResult(legacyBridge())
    expect(parsed.status).toBe('bridge')
    expect(parsed.finding?.outcome).toBe('bridge')
    expect(parsed.field_selections ?? []).toEqual([])
  })

  it('preserves all three evidence classifications and their claim-specific quotations', () => {
    const parsed = parseResult(answer())
    expect(parsed.status).toBe('answer')
    if (parsed.finding?.outcome !== 'answer') throw new Error('Expected an Ask answer')
    expect(parsed.finding.claims.map((claim) => claim.label)).toEqual(['known', 'postulated', 'speculative'])
    expect(parsed.quotes.map((quote) => quote.claim_id)).toEqual(['reported', 'inferred', 'inferred', 'possible'])
    expect(parsed.finding.unanswered_questions).toEqual(['Would a realistic noise model preserve the mapping?'])
  })

  it('rejects an answer claim that has no cited evidence', () => {
    const result = answer()
    result.finding.claims[0].evidence = []
    rejects(result)
  })

  it('rejects a citation whose claim-specific checked quotation is missing', () => {
    const result = answer()
    result.quotes = result.quotes.filter((quote) => quote.claim_id !== 'reported')
    rejects(result)
  })

  it('consumes checked quotations one-to-one and validates duplicate citation records independently', () => {
    const extra = answer()
    extra.quotes.push({ ...extra.quotes[0] })
    rejects(extra)

    const repeatedCitation = answer()
    repeatedCitation.finding.claims[0].evidence.push({ ...repeatedCitation.finding.claims[0].evidence[0] })
    repeatedCitation.quotes.splice(1, 0, {
      ...repeatedCitation.quotes[0],
      start: repeatedCitation.quotes[0].start + 1,
      end: repeatedCitation.quotes[0].end + 1,
    })
    rejects(repeatedCitation)
  })

  it('rejects an answer citation to a source absent from the saved record', () => {
    const result = answer()
    result.finding.claims[0].evidence[0].source_id = 'never-retrieved'
    rejects(result)
  })

  it('rejects ambiguous claim identities and unsupported certainty labels', () => {
    const duplicate = answer()
    duplicate.finding.claims[1].id = 'reported'
    rejects(duplicate)
    const certainty = answer()
    certainty.finding.claims[0].label = 'proved'
    rejects(certainty)
  })

  it('requires an explicit derivation and distinct source identities for a postulated claim', () => {
    const missingDerivation = answer()
    missingDerivation.finding.claims[1].derivation = null
    rejects(missingDerivation)
    const samePaper = answer()
    samePaper.sources[1].url = samePaper.sources[0].url
    rejects(samePaper)
  })

  it('retains the fields actually selected for a Surprise investigation', () => {
    const parsed = parseResult(surprise())
    expect(parsed.mode).toBe('surprise')
    expect(parsed.field_selections?.[0].fields).toEqual(['Information theory', 'Biology'])
  })

  it('matches field selections using the backend whitespace and case normalization', () => {
    const result = surprise()
    result.field_selections[0].fields = ['  Information   theory ', 'BIOLOGY']
    const parsed = parseResult(result)
    expect(parsed.finding?.outcome).toBe('bridge')
    expect(parsed.field_selections?.[0].fields).toEqual(['  Information   theory ', 'BIOLOGY'])
  })

  it('rejects a positive Surprise outcome without distinct recorded fields', () => {
    const missing = surprise()
    missing.field_selections = []
    rejects(missing)
    const duplicate = surprise()
    duplicate.field_selections[0].fields = ['Biology', ' biology ']
    rejects(duplicate)
  })

  it('rejects a Surprise bridge that was not investigated within its selected fields', () => {
    const result = surprise()
    result.finding.field_right = 'Orbital mechanics'
    rejects(result)
  })

  it('accepts an honest incomplete Ask outcome without fabricating answer claims', () => {
    const result = { ...base(), mode: 'ask', field_selections: [], status: 'insufficient_evidence', finding: { outcome: 'insufficient_evidence', reason: 'The fixture sources did not support an answer.' }, quotes: [] }
    const parsed = parseResult(result)
    expect(parsed.status).toBe('insufficient_evidence')
    expect(parsed.finding).toEqual(result.finding)
  })
})
