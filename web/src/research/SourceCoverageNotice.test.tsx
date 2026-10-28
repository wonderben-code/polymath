import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { SourceCoverageNotice } from './SourceCoverageNotice'
import { coverage, excerpt, SNAPSHOT, SNAPSHOT_HASH } from './coverage.test-support'
import { groupSourceVersions, sourceContentLabel, sourceIdentityCount } from './sourcePresentation'

describe('visible source reading scope', () => {
  it('shows exact range, versioned paper and extraction limits without a whole-paper claim', async () => {
    render(<SourceCoverageNotice coverage={coverage()} contentKind="full_text" />)
    const notice = screen.getByRole('region', { name: 'Saved full-text excerpt coverage' })
    expect(notice).toHaveTextContent('Full text excerpt · saved characters 100–140 of 20,000 extracted characters')
    expect(notice).toHaveTextContent('(end excluded)')
    expect(notice).toHaveTextContent('Figures, assets, and non-article navigation are omitted')
    expect(notice).toHaveTextContent('Mathematical alt text and TeX are extracted as plain text')
    expect(notice).toHaveTextContent('their semantics are unverified')
    expect(screen.getByRole('link', { name: /original versioned paper/ })).toHaveAttribute('href', 'https://arxiv.org/html/2610.00001v2')
    await userEvent.click(screen.getByText('Full-text extraction version'))
    expect(screen.getByText(SNAPSHOT_HASH)).toBeVisible()
    expect(screen.getByText(SNAPSHOT)).toBeVisible()
    expect(notice).toHaveTextContent('The source-content hash identifies this saved excerpt')
  })
  it('retains unknown legacy range rather than inventing an excerpt or whole-paper read', () => {
    render(<SourceCoverageNotice contentKind="full_text" />)
    expect(screen.getByText(/does not specify this full-text snapshot’s reading range/)).toBeVisible()
    expect(screen.queryByText('Full text excerpt')).not.toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    expect(sourceContentLabel('full_text')).toBe('Full-text snapshot')
  })
  it('translates verified Unicode-relative quote offsets into extracted-paper offsets', () => {
    const source = excerpt('🌱 a quoted synthetic statement', 100)
    render(<SourceCoverageNotice coverage={source.coverage} quoteStart={2} quoteEnd={10} />)
    expect(screen.getByText(/Quoted characters 102–110 in the extracted paper/)).toBeVisible()
  })
  it('omits a quoted range when offsets do not belong to the saved window', () => {
    render(<SourceCoverageNotice coverage={coverage()} quoteStart={-1} quoteEnd={9000} />)
    expect(screen.queryByText(/Quoted characters/)).not.toBeInTheDocument()
  })
  it('groups multiple windows and abstract versions under one known arXiv paper identity', () => {
    const first = excerpt(), second = excerpt('A second explicitly synthetic window.', 500)
    const abstract = { ...first, coverage: undefined, source_id: 'arxiv:2610.00001', content_kind: 'abstract' as const }
    const later = { ...second, coverage: { ...second.coverage!, arxiv_version: 3 }, bibliography: { ...second.bibliography!, arxiv_version: 3 } }
    expect(sourceIdentityCount([first, second, abstract, later])).toBe(1)
    expect(groupSourceVersions([first, second, abstract, later])[0].versions).toHaveLength(4)
    const other = { ...abstract, source_id: 'fixture:another-paper', bibliography: undefined }
    expect(sourceIdentityCount([first, second, other])).toBe(2)
  })
})
