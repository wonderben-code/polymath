import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ResearchRunView } from './ResearchRunView'
import type { ResearchResult } from './types'
import { excerpt, EXCERPT_HASH } from './coverage.test-support'

function result(): ResearchResult {
  const sources = [excerpt('🌱 The first synthetic excerpt includes an unavailable equation marker.', 100), excerpt('The second synthetic excerpt includes <img src=x onerror=alert(1)> as literal text.', 800)]
  return { status: 'insufficient_evidence', mode: 'explore', finding: { outcome: 'insufficient_evidence', reason: 'Synthetic excerpts do not establish a finding.' }, sources, quotes: sources.map((source, index) => ({ source_id: source.source_id, side: index ? 'right' : 'left', quote: source.text, supports: 'A synthetic UI fixture only.', start: 0, end: Array.from(source.text).length, source_sha256: EXCERPT_HASH, content_kind: 'full_text' })), events: [], usage: { model_calls: 0, input_tokens_reported: 0, output_tokens_reported: 0, calls_without_usage: 0 }, error_code: null, validation_scope: 'source_identity_and_quote_integrity' }
}
describe('full-text evidence presentation', () => {
  it('groups saved windows as one paper while retaining each exact excerpt for inspection', async () => {
    const saved = result()
    render(<ResearchRunView run={{ id: 'synthetic-run', mode: 'explore', goal: 'Synthetic coverage check', status: 'succeeded' }} result={saved} events={[]} connection="closed" />)
    expect(screen.getByText('2 saved source versions · 1 paper identity')).toBeVisible()
    expect(screen.getByText(/not independent papers/)).toBeVisible()
    const list = screen.getByRole('list', { name: 'Saved sources' })
    expect(list.children).toHaveLength(1)
    expect(within(list).getByText('2 saved excerpts')).toBeVisible()
    const windows = within(list).getByRole('list', { name: 'Saved versions of Synthetic versioned paper' })
    expect(windows.children).toHaveLength(2)
    const reads = within(windows).getAllByText('Read this saved excerpt')
    await userEvent.click(reads[0]); expect(within(windows).getByText(saved.sources[0].text)).toBeVisible()
    expect(within(windows).getAllByRole('link', { name: /original versioned paper/ })).toHaveLength(2)
    expect(within(windows).getAllByText(/Mathematical alt text and TeX/).every((node) => node.textContent?.includes('unverified'))).toBe(true)
  })
  it('shows the quote’s exact global character range and keeps literal extraction text escaped', async () => {
    const saved = result(), { container } = render(<ResearchRunView run={{ id: 'synthetic-run', mode: 'explore', goal: 'Synthetic coverage check', status: 'succeeded' }} result={saved} events={[]} connection="closed" />)
    const quotation = container.querySelectorAll('.research-quote')[1] as HTMLElement
    await userEvent.click(quotation.querySelector('summary')!)
    expect(within(quotation).getByText(/Quoted characters 800–/)).toBeVisible()
    expect(within(quotation).getByText(saved.sources[1].text)).toBeVisible()
    await userEvent.click(within(quotation).getByText('Saved excerpt content hash'))
    expect(within(quotation).getByText(EXCERPT_HASH)).toBeVisible()
    expect(within(quotation).getByText(/separately from the complete extracted snapshot/)).toBeVisible()
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('script')).toBeNull()
  })
  it('renders only actual recorded opening/reading activity without launching a fetch', () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
    const events = [{ sequence: 1, kind: 'full_text_opened', summary: 'Opened the saved synthetic arXiv HTML index.' }, { sequence: 2, kind: 'full_text_window_read', summary: 'Read saved synthetic extracted characters 100–140.' }]
    render(<ResearchRunView run={{ id: 'synthetic-run', mode: 'explore', goal: 'Synthetic coverage check', status: 'succeeded' }} result={result()} events={events} connection="closed" />)
    expect(screen.getByRole('list', { name: 'Recorded research activity' })).toHaveTextContent(events[0].summary)
    expect(screen.getByRole('list', { name: 'Recorded research activity' })).toHaveTextContent(events[1].summary)
    expect(fetch).not.toHaveBeenCalled(); vi.unstubAllGlobals()
  })
})
