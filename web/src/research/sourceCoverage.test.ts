import { describe, expect, it } from 'vitest'
import { parseSourceCoverage, validateSourceCoverageContext } from './sourceCoverage'
import type { SourceCoverage } from './sourceCoverage'
import { BIBLIOGRAPHY } from './bibliography.test-support'

const SNAPSHOT = '11111111-1111-4111-8111-111111111111'
const HASH = 'a'.repeat(64)
const invalid = (): never => { throw new Error('invalid_coverage') }
function coverage(overrides: Partial<SourceCoverage> = {}): SourceCoverage { return { snapshot_id: SNAPSHOT, snapshot_sha256: HASH, arxiv_id: '2610.00001', arxiv_version: 2, start: 100, end: 120, total: 10000, ...overrides } }
function context(overrides: Partial<Parameters<typeof validateSourceCoverageContext>[0]> = {}) { return { source_id: `arxiv-html:${SNAPSHOT}:100:120`, url: 'https://arxiv.org/html/2610.00001v2', bibliography: BIBLIOGRAPHY, content_kind: 'full_text' as const, coverage: coverage(), ...overrides } }

describe('exact extracted-paper coverage metadata', () => {
  it('leaves historical missing metadata absent without inventing a whole-paper claim', () => {
    expect(parseSourceCoverage(undefined, invalid)).toBeUndefined()
    expect(parseSourceCoverage(null, invalid)).toBeUndefined()
    expect(() => validateSourceCoverageContext({ source_id: 'old:source', url: 'https://example.org/source', content_kind: 'full_text', text: 'Historical full-text record without ranges.' }, invalid)).not.toThrow()
  })
  it.each(['2610.0001', '2610.00001', 'hep-th/9910001', 'math.GT/0301001'])('accepts canonical modern and archive arXiv base %s', (arxiv_id) => {
    expect(parseSourceCoverage(coverage({ arxiv_id }), invalid)?.arxiv_id).toBe(arxiv_id)
  })
  it.each([
    { snapshot_id: SNAPSHOT.toUpperCase().replace('11111111', 'AAAAAAAA') },
    { snapshot_id: '../snapshot' }, { snapshot_sha256: 'A'.repeat(64) }, { snapshot_sha256: 'hash' },
    { arxiv_id: '2613.00001' }, { arxiv_id: '2600.00001' }, { arxiv_id: 'hep-th/9913001' }, { arxiv_id: '2610.00001v2' }, { arxiv_id: 'https://arxiv.org/abs/2610.00001' }, { arxiv_id: ' 2610.00001' }, { arxiv_id: 'math.gt/0301001' }, { arxiv_id: `${'x'.repeat(100)}/9910001` },
    { arxiv_version: 0 }, { arxiv_version: 10000 }, { arxiv_version: 1.5 }, { arxiv_version: true },
    { start: -1 }, { start: 250000 }, { start: 120 }, { end: 100 }, { end: 10001 }, { total: 250001 }, { total: 0 }, { end: 8101, total: 10000 }, { start: 1.5 }, { total: '10000' }, { complete: true },
  ])('rejects a noncanonical, oversized or contradictory coverage descriptor %#', (changes) => {
    expect(() => parseSourceCoverage({ ...coverage(), ...changes }, invalid)).toThrow('invalid_coverage')
  })
  it('allows only an eight-thousand-codepoint window inside at most 250000 extracted codepoints', () => {
    expect(parseSourceCoverage(coverage({ start: 242000, end: 250000, total: 250000 }), invalid)).toMatchObject({ start: 242000, end: 250000 })
    expect(() => parseSourceCoverage(coverage({ start: 241999, end: 250000, total: 250000 }), invalid)).toThrow()
  })
  it('requires the exact snapshot locator, fixed paper URL, full-text kind and matching bibliography', () => {
    expect(() => validateSourceCoverageContext(context(), invalid)).not.toThrow()
    for (const changes of [{ source_id: `arxiv-html:${SNAPSHOT}:0:20` }, { url: 'https://arxiv.org/html/2610.00001v3' }, { url: 'https://arxiv.org/abs/2610.00001v2' }, { url: 'https://arxiv.org/html/2610.00001v2?redirect=elsewhere' }, { content_kind: 'abstract' as const }, { bibliography: undefined }, { bibliography: { ...BIBLIOGRAPHY, arxiv_id: '2610.00002' } }, { bibliography: { ...BIBLIOGRAPHY, arxiv_version: null } }]) expect(() => validateSourceCoverageContext(context(changes), invalid)).toThrow('invalid_coverage')
  })
  it('measures document window text in Unicode codepoints rather than UTF-16 units', () => {
    const text = '🌌'.repeat(20)
    expect(text.length).toBe(40)
    expect(() => validateSourceCoverageContext(context({ text }), invalid)).not.toThrow()
    expect(() => validateSourceCoverageContext(context({ text: text + 'x' }), invalid)).toThrow('invalid_coverage')
    expect(() => validateSourceCoverageContext(context({ text: text.slice(0, -2) }), invalid)).toThrow('invalid_coverage')
    expect(() => validateSourceCoverageContext(context({ text: 'x'.repeat(19) + '\ud800' }), invalid)).toThrow('invalid_coverage')
  })
})
