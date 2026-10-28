import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { Bibliography, parseBibliography } from './bibliography'
import { BIBLIOGRAPHY } from './bibliography.test-support'
import { parseResult } from './client'
const invalid = (): never => { throw new Error('invalid bibliography') }
describe('saved source bibliography', () => {
  it('preserves missing historical metadata and exact provider-declared identifiers', () => {
    expect(parseBibliography(undefined, invalid)).toBeUndefined()
    expect(parseBibliography(null, invalid)).toBeUndefined()
    expect(parseBibliography(BIBLIOGRAPHY, invalid)).toEqual(BIBLIOGRAPHY)
  })
  it('preserves source bibliography when reading a research result, including historical absence', () => {
    const source = { source_id: 'fixture:source', title: 'Synthetic source', url: 'https://example.org/source', text: 'Synthetic source text for protocol testing only.', content_kind: 'abstract', retrieved_at: '2026-10-28T00:00:00Z', extraction_method: 'test_fixture' }
    const result = { status: 'failed', mode: 'explore', finding: null, sources: [source], quotes: [], events: [], usage: { model_calls: 0, input_tokens_reported: 0, output_tokens_reported: 0, calls_without_usage: 0 }, error_code: 'fixture_only', validation_scope: 'source_identity_and_quote_integrity' }
    expect(parseResult({ ...result, sources: [{ ...source, bibliography: BIBLIOGRAPHY }] }).sources[0].bibliography).toEqual(BIBLIOGRAPHY)
    expect(parseResult(result).sources[0]).not.toHaveProperty('bibliography')
  })
  it('rejects oversized author lists, malformed identifiers and inconsistent truncated lists', () => {
    for (const patch of [{ authors: Array.from({ length: 101 }, () => ({ name: 'Synthetic author', orcid: null })) }, { authors_truncated: true }, { doi: 'https://unsafe.invalid/credential' }, { authors: [{ name: 'Synthetic author', orcid: '0000-0002-1825-0098' }] }, { publication_date: '2026-02-30' }, { arxiv_id: null, arxiv_version: 1 }, { openalex_id: 'javascript:alert(1)' }]) expect(() => parseBibliography({ ...BIBLIOGRAPHY, ...patch }, invalid)).toThrow()
  })
  it('renders bibliographic context and safe citation links without claiming corroboration', async () => {
    render(<Bibliography value={parseBibliography(BIBLIOGRAPHY, invalid)} />)
    await userEvent.click(screen.getByText('Bibliographic details'))
    expect(screen.getByText(/Aliases do not establish independent corroboration/)).toBeVisible()
    expect(screen.getByText('Synthetic Journal')).toBeVisible()
    expect(screen.getByRole('link', { name: '10.1234/synthetic.fixture' })).toHaveAttribute('href', 'https://doi.org/10.1234/synthetic.fixture')
    expect(screen.getByRole('link', { name: '2610.00001v2' })).toHaveAttribute('href', 'https://arxiv.org/abs/2610.00001v2')
  })
})
