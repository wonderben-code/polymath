import { render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { parsePublicFinding, parsePublicProjection, parseShareRecord } from './client'
import { PublicSnapshot } from './PublicSnapshot'
import { canonicalJson, projection, publicFinding, shareRecord } from './test-support'
import type { PublicProjection } from './types'

afterEach(() => vi.unstubAllGlobals())

function boundProjection(): PublicProjection {
  const value = projection('answer')
  if (value.finding.kind !== 'answer') throw new Error('Fixture must be Ask')
  value.overview_binding = { protocol: 'polymath-ask-claim-overview-v1', claim_ids: ['claim-3', 'claim-1'] }
  value.finding.summary = [value.finding.claims[2].text, value.finding.claims[0].text].join('\n\n')
  return value
}

describe('public claim-bound overview projection', () => {
  it('preserves the exact ordered local selection outside the unchanged answer DTO', () => {
    const value = boundProjection(), parsed = parsePublicProjection(value)
    expect(parsed).toEqual(value)
    expect(parsed.overview_binding?.claim_ids).toEqual(['claim-3', 'claim-1'])
    expect(parsed.finding).not.toHaveProperty('overview_binding')
    if (parsed.finding.kind !== 'answer') throw new Error('Expected Ask')
    expect(parsed.finding.claims).toHaveLength(3)
    expect(parsed.finding.claims[1].derivation).toBeTruthy()
  })

  it('preserves legacy absent field bytes without inventing a new overview guarantee', () => {
    for (const kind of ['answer', 'bridge'] as const) {
      const value = projection(kind)
      expect(canonicalJson(parsePublicProjection(value))).toBe(canonicalJson(value))
      expect(parsePublicProjection(value)).not.toHaveProperty('overview_binding')
    }
  })

  it.each([
    null,
    { protocol: 'polymath-ask-claim-overview-v1', claim_ids: ['PRIVATE_CLAIM'] },
    { protocol: 'polymath-ask-claim-overview-v1', claim_ids: ['claim-3', 'claim-3'] },
    { protocol: 'polymath-ask-claim-overview-v1', claim_ids: ['claim-8'] },
    { protocol: 'polymath-ask-claim-overview-v1', claim_ids: [] },
    { protocol: 'polymath-ask-claim-overview-v1', claim_ids: ['claim-0'] },
    { protocol: 'polymath-ask-claim-overview-v1', claim_ids: ['claim-3', 'claim-1'], run_id: 'private-run' },
    { protocol: 'polymath-ask-claim-overview-v1', claim_ids: ['claim-3', 'claim-1'], scientific_context: {} },
    { protocol: 'unknown', claim_ids: ['claim-3', 'claim-1'] },
    { claim_ids: ['claim-3', 'claim-1'] },
  ])('rejects null, private, malformed or injected overview metadata %#', (binding) => {
    expect(() => parsePublicProjection({ ...boundProjection(), overview_binding: binding })).toThrow('invalid_response')
  })

  it('checks exact text order and never accepts paraphrase or normalization', () => {
    const value = boundProjection()
    expect(() => parsePublicProjection({ ...value, overview_binding: { ...value.overview_binding, claim_ids: ['claim-1', 'claim-3'] } })).toThrow('invalid_response')
    if (value.finding.kind !== 'answer') throw new Error('Expected Ask')
    value.finding.summary += ' An extra unclassified conclusion.'
    expect(() => parsePublicProjection(value)).toThrow('invalid_response')
    const unicode = boundProjection()
    if (unicode.finding.kind !== 'answer') throw new Error('Expected Ask')
    unicode.finding.claims[2].text = 'A model-selected exact Unicode claim: λ 😀 e\u0301.'
    unicode.finding.summary = `${unicode.finding.claims[2].text}\n\n${unicode.finding.claims[0].text}`
    expect(parsePublicProjection(unicode).finding).toEqual(unicode.finding)
    unicode.finding.summary = unicode.finding.summary.normalize('NFC')
    expect(() => parsePublicProjection(unicode)).toThrow('invalid_response')
  })

  it('rejects a bound overview on a bridge or at the wrong inner location', () => {
    expect(() => parsePublicProjection({ ...projection(), overview_binding: boundProjection().overview_binding })).toThrow('invalid_response')
    const value = boundProjection()
    expect(() => parsePublicProjection({ ...value, finding: { ...value.finding, overview_binding: value.overview_binding } })).toThrow('invalid_response')
  })

  it('retains the same validated public selection in anonymous and owner snapshot parsers', () => {
    const value = boundProjection()
    expect(parsePublicFinding({ ...publicFinding(), projection: value }).projection.overview_binding).toEqual(value.overview_binding)
    expect(parseShareRecord({ ...shareRecord(), projection: value }).projection.overview_binding).toEqual(value.overview_binding)
  })
})

describe('classified public overview presentation', () => {
  it('shows exact selected claims, labels, caveats and citations with scoped full-claim navigation', () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
    const value = parsePublicProjection(boundProjection())
    render(<PublicSnapshot projection={value} prefix="preview-one" />)
    const overview = screen.getByRole('region', { name: 'Claim-bound answer overview' })
    expect(within(overview).getByRole('heading', { name: 'Answer overview' })).toBeVisible()
    const selected = within(overview).getByRole('list', { name: 'Selected overview claims' }).children
    expect(selected).toHaveLength(2)
    expect(within(selected[0] as HTMLElement).getByText('speculative · model label')).toBeVisible()
    expect(within(selected[0] as HTMLElement).getByText('This has not been scientifically established.')).toBeVisible()
    expect(within(selected[1] as HTMLElement).getByText('known · model label')).toBeVisible()
    expect(within(overview).getByRole('link', { name: 'Open full claim 3' })).toHaveAttribute('href', '#preview-one-claim-3')
    expect(within(overview).getByRole('link', { name: 'Citation 4' })).toHaveAttribute('href', '#preview-one-citation-4')
    expect(document.getElementById('preview-one-claim-3')).toHaveAttribute('tabindex', '-1')
    expect(document.getElementById('preview-one-citation-4')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Shared postulated claim' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Answer summary' })).toBeNull()
    expect(fetch).not.toHaveBeenCalled()
  })

  it('keeps anchors separate when preview and saved snapshots share a page', () => {
    const value = boundProjection()
    render(<><PublicSnapshot projection={value} prefix="preview-one" /><PublicSnapshot projection={value} prefix="saved-two" /></>)
    const overviews = screen.getAllByRole('region', { name: 'Claim-bound answer overview' })
    expect(within(overviews[0]).getByRole('link', { name: 'Open full claim 1' })).toHaveAttribute('href', '#preview-one-claim-1')
    expect(within(overviews[1]).getByRole('link', { name: 'Open full claim 1' })).toHaveAttribute('href', '#saved-two-claim-1')
    expect(document.getElementById('preview-one-claim-1')).not.toBe(document.getElementById('saved-two-claim-1'))
  })

  it('keeps historical unbound summaries while refusing malformed present metadata fallback', () => {
    const first = render(<PublicSnapshot projection={projection('answer')} />)
    expect(screen.getByRole('heading', { name: 'Answer summary' })).toBeVisible()
    first.unmount()
    const invalid = boundProjection()
    if (invalid.finding.kind !== 'answer') throw new Error('Expected Ask')
    invalid.finding.summary = 'A forged unclassified overview.'
    render(<PublicSnapshot projection={invalid} />)
    expect(screen.getByRole('alert')).toHaveTextContent('could not be matched')
    expect(screen.queryByText('A forged unclassified overview.')).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Answer summary' })).toBeNull()
  })
})
