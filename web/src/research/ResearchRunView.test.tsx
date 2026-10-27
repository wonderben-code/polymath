import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ResearchRunView } from './ResearchRunView'
import type { ResearchResult, ResearchRunViewProps } from './types'

const leftQuote = 'Synthetic redundancy can preserve information under a specified noise model.'
const rightQuote = 'Synthetic pathway redundancy can preserve a signal under limited perturbations.'

function fixtureResult(): ResearchResult {
  // All papers and findings here are synthetic UI fixtures, not scientific results.
  return {
    status: 'bridge',
    finding: {
      outcome: 'bridge',
      title: 'A synthetic analogy requiring further tests',
      field_left: 'Information theory',
      field_right: 'Biology',
      explanation: 'These synthetic fixtures compare redundant representations under explicit assumptions.',
      mappings: [{ left: 'Redundant message', right: 'Redundant pathway', assumption: 'A specified noise model is required.' }],
      open_question: 'Does this synthetic mapping survive a realistic noise model?',
      strongest_objection: 'The underlying mechanisms differ.',
      objection_verdict: 'A narrow analogy remains; equivalence is not established.',
      novelty_limitations: 'Synthetic fixtures cannot establish scientific novelty.',
      evidence: [
        { source_id: 'left', side: 'left', quote: leftQuote, supports: 'A synthetic information claim.' },
        { source_id: 'right', side: 'right', quote: rightQuote, supports: 'A synthetic biological claim.' },
      ],
    },
    quotes: [
      { source_id: 'left', side: 'left', quote: leftQuote, supports: 'A synthetic information claim.', start: 0, end: Array.from(leftQuote).length, source_sha256: 'a'.repeat(64), content_kind: 'abstract' },
      { source_id: 'right', side: 'right', quote: rightQuote, supports: 'A synthetic biological claim.', start: 0, end: Array.from(rightQuote).length, source_sha256: 'b'.repeat(64), content_kind: 'full_text' },
    ],
    sources: [
      { source_id: 'left', title: 'Synthetic information source', url: 'https://example.org/fixture-left', text: leftQuote, content_kind: 'abstract', retrieved_at: '2026-10-27T09:00:00Z', extraction_method: 'synthetic_fixture' },
      { source_id: 'right', title: 'Synthetic biology source', url: 'https://example.org/fixture-right', text: rightQuote, content_kind: 'full_text', retrieved_at: '2026-10-27T09:00:00Z', extraction_method: 'synthetic_fixture' },
    ],
    events: [],
    usage: { model_calls: 3, input_tokens_reported: 100, output_tokens_reported: 60, calls_without_usage: 0 },
    error_code: null,
    validation_scope: 'source_identity_and_quote_integrity',
  }
}

function props(overrides: Partial<ResearchRunViewProps> = {}): ResearchRunViewProps {
  return {
    run: { id: 'synthetic-run', mode: 'explore', goal: 'A synthetic research question', status: 'running' },
    events: [],
    result: null,
    connection: 'live',
    ...overrides,
  }
}

describe('recorded research view', () => {
  it('shows only actual recorded events in sequence and ignores replay duplicates', () => {
    render(<ResearchRunView {...props({ events: [
      { sequence: 3, kind: 'source_read', summary: 'Read a synthetic source.' },
      { sequence: 1, kind: 'search_completed', summary: 'Completed a synthetic search.' },
      { sequence: 3, kind: 'source_read', summary: 'Read a synthetic source.' },
    ] })} />)
    const timeline = screen.getByRole('list', { name: 'Recorded research activity' })
    const entries = within(timeline).getAllByRole('listitem')
    expect(entries).toHaveLength(2)
    expect(entries[0]).toHaveTextContent('Completed a synthetic search.')
    expect(entries[1]).toHaveTextContent('Read a synthetic source.')
    expect(screen.getByText('No completed outcome yet')).toBeInTheDocument()
    expect(screen.queryByText('Candidate connection', { exact: false })).not.toBeInTheDocument()
  })

  it('does not manufacture activity when connecting or reconnecting', () => {
    const { rerender } = render(<ResearchRunView {...props({ connection: 'connecting' })} />)
    expect(screen.getByText('No recorded activity yet.')).toBeInTheDocument()
    expect(screen.getByText('Connecting to saved activity')).toBeInTheDocument()
    rerender(<ResearchRunView {...props({ connection: 'reconnecting', events: [{ sequence: 4, kind: 'source_read', summary: 'Read a synthetic source.' }] })} />)
    expect(screen.getByText('Reconnecting · recorded activity is retained')).toBeInTheDocument()
    expect(screen.getByText('Read a synthetic source.')).toBeInTheDocument()
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
  })

  it('presents research actions and important outcomes without operational bookkeeping', () => {
    const events = [
      { sequence: 1, kind: 'checkpoint', summary: 'Internal checkpoint fixture.' },
      { sequence: 2, kind: 'external_started', summary: 'Internal dispatch fixture.' },
      { sequence: 3, kind: 'search_completed', summary: 'Searched synthetic literature.' },
      { sequence: 4, kind: 'external_observed', summary: 'Internal accounting fixture.' },
      { sequence: 5, kind: 'source_read', summary: 'Read a synthetic source.' },
      { sequence: 6, kind: 'external_reconciled', summary: 'Internal reconciliation fixture.' },
      { sequence: 7, kind: 'action_rejected', summary: 'Rejected an unsupported synthetic connection.' },
      { sequence: 8, kind: 'concluded', summary: 'Concluded with insufficient synthetic evidence.' },
      { sequence: 9, kind: 'accounting_conflict', summary: 'A recorded usage conflict needs review.' },
    ]
    render(<ResearchRunView {...props({ events })} />)
    const timeline = screen.getByRole('list', { name: 'Recorded research activity' })
    expect(within(timeline).getAllByRole('listitem')).toHaveLength(5)
    expect(screen.queryByText(/Internal .* fixture\./)).not.toBeInTheDocument()
    expect(within(timeline).getByText('Searched synthetic literature.')).toBeInTheDocument()
    expect(within(timeline).getByText('Read a synthetic source.')).toBeInTheDocument()
    expect(within(timeline).getByText('Rejected an unsupported synthetic connection.')).toBeInTheDocument()
    expect(within(timeline).getByText('Concluded with insufficient synthetic evidence.')).toBeInTheDocument()
    expect(within(timeline).getByText('A recorded usage conflict needs review.')).toBeInTheDocument()
    expect(events).toHaveLength(9)
  })

  it('lets an active investigation request cancellation without claiming it already stopped', async () => {
    const onCancel = vi.fn()
    const user = userEvent.setup()
    const { rerender } = render(<ResearchRunView {...props({ onCancel })} />)
    await user.click(screen.getByRole('button', { name: 'Cancel investigation' }))
    expect(onCancel).toHaveBeenCalledOnce()
    expect(screen.getByRole('status')).toHaveTextContent('Investigating')
    rerender(<ResearchRunView {...props({ onCancel, cancelling: true })} />)
    expect(screen.getByRole('button', { name: 'Requesting cancellation…' })).toBeDisabled()
    rerender(<ResearchRunView {...props({ run: { ...props().run, status: 'cancelled' }, onCancel })} />)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Cancelled')
    expect(screen.getByText(/an operation already sent may still incur usage/)).toBeInTheDocument()
    expect(screen.getByText('No completed finding saved')).toBeInTheDocument()
  })

  it('explains an interrupted investigation without promising an automatic retry', () => {
    render(<ResearchRunView {...props({ run: { ...props().run, status: 'interrupted' }, connection: 'closed' })} />)
    expect(screen.getByRole('status')).toHaveTextContent('Interrupted')
    expect(screen.getByText(/It will not automatically repeat an operation whose outcome is unknown/)).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('keeps a pending cancellation distinct from a completed stop', () => {
    render(<ResearchRunView {...props({ run: { ...props().run, status: 'cancel_requested' } })} />)
    expect(screen.getByRole('status')).toHaveTextContent('Cancellation requested')
    expect(screen.getByText('Cancellation is pending')).toBeInTheDocument()
    expect(screen.getByText(/An operation already in progress may still return an outcome/)).toBeInTheDocument()
    expect(screen.queryByText(/The investigation was stopped/)).not.toBeInTheDocument()
  })

  it('renders a candidate mapping and limitations with expandable source quotations', async () => {
    const user = userEvent.setup()
    render(<ResearchRunView {...props({ run: { ...props().run, status: 'succeeded' }, result: fixtureResult(), connection: 'closed' })} />)
    expect(screen.getByRole('heading', { name: 'A synthetic analogy requiring further tests' })).toBeInTheDocument()
    expect(screen.getByRole('table', { name: 'Proposed structural mapping' })).toBeInTheDocument()
    expect(screen.getByText('The underlying mechanisms differ.')).toBeInTheDocument()
    expect(screen.getByText('Synthetic fixtures cannot establish scientific novelty.')).toBeInTheDocument()
    expect(screen.getByText(/Model assessments are not independent scientific verification/)).toBeInTheDocument()
    const summaries = document.querySelectorAll('summary')
    expect(summaries).toHaveLength(2)
    expect(screen.getByText(leftQuote)).not.toBeVisible()
    await user.click(summaries[0])
    expect(screen.getByText(leftQuote)).toBeVisible()
    expect(screen.getAllByText('Abstract only')).toHaveLength(2)
    expect(screen.getAllByText('Full text retrieved')).toHaveLength(2)
    expect(screen.getAllByRole('link', { name: /Synthetic information source/ })[0]).toHaveAttribute('rel', 'noopener noreferrer')
    expect(screen.getByText(/Quotation matching checks the saved text/)).toBeInTheDocument()
  })

  it('matches Python code-point offsets correctly when source text contains non-BMP characters', () => {
    const result = fixtureResult()
    const prefix = '🧬 synthetic prefix. '
    result.sources[0].text = prefix + leftQuote
    result.quotes[0].start = Array.from(prefix).length
    result.quotes[0].end = Array.from(prefix + leftQuote).length
    render(<ResearchRunView {...props({ result })} />)
    expect(screen.queryByText('Quotation needs review')).not.toBeInTheDocument()
    expect(screen.getAllByText('Quotation matches the saved source text.')).toHaveLength(2)
  })

  it('flags mismatched quotations instead of presenting them as matched evidence', () => {
    const result = fixtureResult()
    result.quotes[0].quote = 'This quotation does not occur in the saved source.'
    render(<ResearchRunView {...props({ result })} />)
    expect(screen.getByText('Quotation needs review')).toBeInTheDocument()
    expect(screen.getByText(/must not be treated as verified evidence/)).toBeInTheDocument()
  })

  it('rejects an out-of-range quote end even when slicing would clamp it to a matching source', () => {
    const result = fixtureResult()
    result.quotes[0].end += 100
    render(<ResearchRunView {...props({ result })} />)
    expect(screen.getByText('Quotation needs review')).toBeInTheDocument()
  })

  it.each(['javascript:alert(1)', 'http://example.org/source', 'https://user:secret@example.org/source', 'https://127.0.0.1/source', 'https://localhost/source', 'https://localhost./source', 'https://foo.localhost./source', 'https://[::1]/source'])('does not make unsafe source URL %s clickable', (url) => {
    const result = fixtureResult()
    result.sources[0].url = url
    render(<ResearchRunView {...props({ result })} />)
    expect(screen.queryByRole('link', { name: /Synthetic information source/ })).not.toBeInTheDocument()
    expect(screen.getAllByText('Source link unavailable')).toHaveLength(2)
  })

  it('shows an honest empty outcome rather than creating a bridge from saved sources', () => {
    const result = fixtureResult()
    result.status = 'insufficient_evidence'
    result.finding = { outcome: 'insufficient_evidence', reason: 'The synthetic mapping did not survive its objection.' }
    result.quotes = []
    render(<ResearchRunView {...props({ result })} />)
    expect(screen.getByRole('heading', { name: 'No supported connection established' })).toBeInTheDocument()
    expect(screen.getByText('The synthetic mapping did not survive its objection.')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'Saved sources' })).toBeInTheDocument()
  })

  it.each(['failed', 'limit_reached'] as const)('does not surface a candidate finding when outcome status is %s', (status) => {
    const result = fixtureResult()
    result.status = status
    render(<ResearchRunView {...props({ result })} />)
    expect(screen.queryByRole('heading', { name: 'A synthetic analogy requiring further tests' })).not.toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'Saved sources' })).toBeInTheDocument()
  })

  it('renders untrusted titles and explanations as text rather than HTML', () => {
    const result = fixtureResult()
    result.sources[0].title = '<script>window.pwned=true</script>'
    if (result.finding?.outcome === 'bridge') result.finding.explanation = '<img src=x onerror=alert(1)>'
    render(<ResearchRunView {...props({ result })} />)
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument()
    expect(document.querySelector('script')).toBeNull()
    expect(document.querySelector('img')).toBeNull()
  })
})
