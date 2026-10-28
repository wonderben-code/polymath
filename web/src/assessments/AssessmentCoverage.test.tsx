import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { AssessmentPreview } from './AssessmentPreview'
import { parseAssessment } from './client'
import { ASSESSMENT, head, payload, record } from './fixtures.test-support'
import { BIBLIOGRAPHY } from '../research/bibliography.test-support'

const SNAPSHOT = '99999999-9999-4999-8999-999999999999'
const SNAPSHOT_HASH = 'd'.repeat(64), WINDOW_HASH = 'a'.repeat(64)
const QUOTE = '[equation unavailable] <math><mi>x</mi></math> \\alpha <img src="untrusted" onerror="alert(1)">'
function coveredRecord() {
  const value = payload(), coverage = { snapshot_id: SNAPSHOT, snapshot_sha256: SNAPSHOT_HASH, arxiv_id: BIBLIOGRAPHY.arxiv_id, arxiv_version: 2, start: 100, end: 300, total: 3000 }, sourceId = `arxiv-html:${SNAPSHOT}:100:300`
  Object.assign(value.basis.sources[0], { source_id: sourceId, url: 'https://arxiv.org/html/2610.00001v2', content_kind: 'full_text', bibliography: BIBLIOGRAPHY, coverage })
  Object.assign(value.observed_sources[0], value.basis.sources[0])
  Object.assign(value.basis.prior_quotes[0], { source_id: sourceId, content_kind: 'full_text', quote: QUOTE, start: 5, end: 5 + Array.from(QUOTE).length })
  if (value.basis.prior_finding.outcome !== 'bridge') throw new Error('Synthetic fixture shape changed')
  Object.assign(value.basis.prior_finding.evidence[0], { source_id: sourceId, quote: QUOTE })
  Object.assign(value.result.rationale[0].citations[0], { quote: QUOTE })
  Object.assign(value.result.quotes[0], { source_id: sourceId, content_kind: 'full_text', coverage, quote: QUOTE, start: 5, end: 5 + Array.from(QUOTE).length })
  // The watch abstract and saved window share a declared paper identity.
  for (const source of [value.basis.sources[2], value.observed_sources[2]]) Object.assign(source, { source_id: 'arxiv:2610.00001v2', url: 'https://arxiv.org/abs/2610.00001v2', bibliography: BIBLIOGRAPHY })
  value.result.quotes[1].source_id = 'arxiv:2610.00001v2'
  return parseAssessment(record({ payload: value }))
}

describe('assessment excerpt provenance', () => {
  it('shows window and global quote offsets, exact paper version and separate hashes without rendering extracted math markup', async () => {
    const saved = coveredRecord(), rendered = render(<AssessmentPreview record={saved} head={head({ revision: 1, last_assessment_id: ASSESSMENT })} onOpenFinding={vi.fn()} />)
    const passages = screen.getByRole('region', { name: 'Verified assessment passages' }), first = within(passages).getAllByRole('listitem')[0]
    expect(passages).toHaveTextContent('2 saved passages · 1 paper identity')
    expect(passages).toHaveTextContent('Multiple windows or versions of one paper are not independent evidence')
    const coverage = within(first).getByLabelText('Saved full-text excerpt coverage')
    expect(coverage).toHaveTextContent(`saved characters 100–300 of ${(3000).toLocaleString()} extracted characters`)
    expect(coverage).toHaveTextContent(`Quoted characters 105–${105 + Array.from(QUOTE).length} in the extracted paper`)
    expect(within(coverage).getByRole('link', { name: 'arXiv 2610.00001v2 · original versioned paper' })).toHaveAttribute('href', 'https://arxiv.org/html/2610.00001v2')
    expect(coverage).toHaveTextContent('Figures, assets, and non-article navigation are omitted')
    expect(coverage).toHaveTextContent('Table layout may be lost')
    expect(coverage).toHaveTextContent('their semantics are unverified')
    expect(within(first).getByText(QUOTE, { selector: 'blockquote' })).toBeVisible()
    expect(rendered.container.querySelector('math, img, script')).toBeNull()
    await userEvent.click(within(first).getByText('Full-text extraction version'))
    expect(within(first).getByText(SNAPSHOT_HASH)).toBeVisible()
    expect(within(first).getByText('Extracted snapshot SHA-256')).toBeVisible()
    await userEvent.click(within(first).getByText('Exact passage version'))
    expect(within(first).getByText(WINDOW_HASH)).toBeVisible()
    expect(within(first).getByText('Excerpt content SHA-256')).toBeVisible()
    expect(screen.getByRole('article', { name: 'Saved evidence reassessment' })).toHaveTextContent('Applied at head revision 1')
  })

  it('preserves full-text legacy passage inspection without inventing a window or whole-paper verification', () => {
    const value = payload()
    value.basis.sources[0].content_kind = 'full_text'; value.observed_sources[0].content_kind = 'full_text'; value.basis.prior_quotes[0].content_kind = 'full_text'; value.result.quotes[0].content_kind = 'full_text'
    const saved = parseAssessment(record({ payload: value }))
    render(<AssessmentPreview record={saved} />)
    expect(screen.getByText('The saved record does not specify this full-text snapshot’s reading range.')).toBeVisible()
    expect(screen.queryByLabelText('Saved full-text excerpt coverage')).not.toBeInTheDocument()
    expect(screen.queryByText(/Quoted characters/)).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Verified assessment passages' })).toHaveTextContent(value.result.quotes[0].quote)
  })
})
