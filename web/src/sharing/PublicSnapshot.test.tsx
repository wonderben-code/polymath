import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PublicSnapshot, ScholarlyLinks } from './PublicSnapshot'
import { PublicFindingPage } from './PublicFindingPage'
import { PublicLibraryPage } from './PublicLibraryPage'
import { EXTRACTED_PAPER, library, map, projection, PUBLIC_ID, publicFinding, QUOTE_A, sha256, SOURCE_WINDOW } from './test-support'

afterEach(() => vi.unstubAllGlobals())

describe('complete allowlisted public finding presentation', () => {
  it('labels an unversioned arXiv record without inventing a paper version', () => {
    render(<ScholarlyLinks links={[{ kind: 'arxiv', url: 'https://arxiv.org/abs/2610.00001' }]} />)
    expect(screen.getByRole('link', { name: 'arXiv paper' })).toBeVisible()
    expect(screen.queryByRole('link', { name: 'Versioned arXiv paper' })).toBeNull()
  })
  it('preserves Bridge text, exact displayed Unicode quote bytes and separate quote/window/snapshot ranges and hashes', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
    render(<PublicSnapshot projection={projection()} />)
    expect(screen.getByRole('heading', { name: 'Public synthetic bridge' })).toBeVisible()
    expect(screen.getByText('The synthetic assumptions are comparable.')).toBeVisible()
    expect(screen.getByText('Scientific validity remains unverified.')).toBeVisible()
    expect(screen.getByText('No scientific novelty is established.')).toBeVisible()
    const citations = screen.getByRole('list', { name: 'Public saved citation excerpts' })
    expect(citations.children).toHaveLength(2)
    expect(within(citations).getByText(QUOTE_A, { selector: 'blockquote' }).textContent).toBe(QUOTE_A)
    expect(within(citations).getByText(/saved window \[100, 500\) of 4000/)).toBeVisible()
    expect(within(citations).getByText(/full quote covers extracted characters \[107,/)).toBeVisible()
    expect(within(citations).getByText(/Mathematical alt text and TeX/)).toBeVisible()
    await userEvent.click(within(citations).getByText('Extracted snapshot integrity'))
    expect(within(citations).getByText(sha256(EXTRACTED_PAPER))).toBeVisible()
    await userEvent.click(within(citations).getAllByText('Quotation ranges and integrity')[0])
    expect(within(citations).getByText(sha256(SOURCE_WINDOW))).toBeVisible()
    expect(within(citations).getByText(sha256(QUOTE_A))).toBeVisible()
    expect(screen.getByText(/Synthetic Author \(ORCID/)).toBeVisible()
    expect(screen.getByText('Synthetic Journal')).toBeVisible()
    expect(screen.getByRole('link', { name: 'Versioned arXiv paper' })).toHaveAttribute('href', 'https://arxiv.org/html/2610.00001v2')
    expect(screen.queryByText('Snapshot identity')).toBeNull()
    expect(fetch).not.toHaveBeenCalled()
  })
  it('preserves the Ask question, each model label, derivation, caveat and every citation association', () => {
    render(<PublicSnapshot projection={projection('answer')} />)
    expect(screen.getByRole('heading', { name: 'What does the public synthetic evidence establish?' })).toBeVisible()
    for (const label of ['known', 'postulated', 'speculative']) expect(screen.getByRole('region', { name: `Shared ${label} claim` })).toBeVisible()
    expect(screen.getByText('A synthetic inference combines two saved source premises.')).toBeVisible()
    expect(screen.getByText('This has not been scientifically established.')).toBeVisible()
    expect(screen.getByText('Does real literature support the mechanism?')).toBeVisible()
    expect(screen.getByRole('list', { name: 'Public saved citation excerpts' }).children).toHaveLength(4)
    const claims = screen.getByRole('region', { name: 'Shared postulated claim' })
    expect(within(claims).getByRole('link', { name: 'Citation 2' })).toHaveAttribute('href', '#shared-citation-2')
    expect(within(claims).getByRole('link', { name: 'Citation 3' })).toHaveAttribute('href', '#shared-citation-3')
  })
  it('renders model markup literally and retains omitted-citation metadata without emitting quote text', () => {
    const saved = projection(); if (saved.finding.kind === 'bridge') saved.finding.explanation = '<img src="https://example.test/tracker" onerror="alert(1)">'
    saved.citations[0].quote = { ...saved.citations[0].quote, excerpt: null, excerpted: true, excerpt_start: null, excerpt_end: null, excerpt_omission_reason: 'paper_limit' }
    const { container } = render(<PublicSnapshot projection={saved} />)
    expect(screen.getByText('<img src="https://example.test/tracker" onerror="alert(1)">')).toBeVisible()
    expect(container.querySelector('img, iframe, script')).toBeNull()
    expect(screen.getByText(/Excerpt omitted: the quotation budget/)).toBeVisible()
    expect(screen.queryByText(QUOTE_A, { selector: 'blockquote' })).toBeNull()
    expect(screen.getByRole('list', { name: 'Public saved citation excerpts' }).children).toHaveLength(2)
  })
  it.each(['superseded', 'unsupported'] as const)('keeps %s shared content exact with an honest warning and no private successor navigation', async (status) => {
    const fetch = vi.fn(async () => Response.json(publicFinding({ status }))); vi.stubGlobal('fetch', fetch)
    render(<PublicFindingPage publicId={PUBLIC_ID} />)
    expect(await screen.findByRole('heading', { name: 'Public synthetic bridge' })).toBeVisible()
    expect(screen.getByText(status === 'superseded' ? /A different finding is now current/ : /latest model assessment no longer supports this exact finding/)).toBeVisible()
    expect(screen.queryByRole('button', { name: /Write|Formalise|Investigate|Reassess/ })).toBeNull()
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(screen.queryByText(/private-result-/)).toBeNull()
  })
  it('renders the bounded public map with public-only navigation and accessible relationship inspection', async () => {
    const graph = map({ truncated: true })
    const fetch = vi.fn(async (input: RequestInfo | URL) => Response.json(input.toString().includes('/map?') ? graph : library())); vi.stubGlobal('fetch', fetch)
    render(<PublicLibraryPage />)
    await screen.findByRole('list', { name: 'Public finding snapshots' })
    expect(screen.getByText(/limited to 60 findings, 200 nodes, and 500 edges/)).toBeVisible()
    const records = screen.getByRole('list', { name: 'Public knowledge map records' })
    await userEvent.click(within(records).getByRole('button', { name: 'paper · Synthetic versioned paper' }))
    const detail = screen.getByRole('region', { name: 'Selected public knowledge record' })
    expect(within(detail).getByRole('link', { name: 'Read this shared finding' })).toHaveAttribute('href', `/shared/${PUBLIC_ID}`)
    expect(within(detail).getByRole('link', { name: 'Versioned arXiv paper' })).toHaveAttribute('rel', 'noopener noreferrer')
    expect(within(detail).getByText('Saved citation association')).toBeVisible()
    expect(fetch).toHaveBeenCalledTimes(2)
  })
})
