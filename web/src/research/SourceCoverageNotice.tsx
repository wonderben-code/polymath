import type { SourceCoverage } from './sourceCoverage'
import './sourceCoverage.css'

export function SourceCoverageNotice({ coverage, contentKind, quoteStart, quoteEnd }: {
  coverage?: SourceCoverage
  contentKind?: 'abstract' | 'full_text'
  quoteStart?: number
  quoteEnd?: number
}) {
  if (!coverage) return contentKind === 'full_text' ? <p className="source-coverage-legacy">The saved record does not specify this full-text snapshot’s reading range.</p> : null
  const validQuote = Number.isSafeInteger(quoteStart) && Number.isSafeInteger(quoteEnd) && quoteStart! >= 0 && quoteEnd! > quoteStart! && quoteEnd! <= coverage.end - coverage.start
  const paper = `${coverage.arxiv_id}v${coverage.arxiv_version}`
  return <section className="source-coverage" aria-label="Saved full-text excerpt coverage">
    <p className="source-coverage-range"><strong>Full text excerpt</strong> · saved characters {coverage.start.toLocaleString()}–{coverage.end.toLocaleString()} of {coverage.total.toLocaleString()} extracted characters <span>(end excluded)</span></p>
    {validQuote && <p className="source-coverage-quote">Quoted characters {coverage.start + quoteStart!}–{coverage.start + quoteEnd!} in the extracted paper <span>(end excluded)</span>.</p>}
    <p className="source-coverage-version"><a href={`https://arxiv.org/html/${paper}`} target="_blank" rel="noopener noreferrer">arXiv {paper} · original versioned paper</a></p>
    <p className="source-coverage-limits">Only this excerpt was saved as source text. Figures, assets, and non-article navigation are omitted. Table layout may be lost. Mathematical alt text and TeX are extracted as plain text; their semantics are unverified, and unavailable equations may be marked explicitly.</p>
    <details className="source-coverage-integrity"><summary>Full-text extraction version</summary><dl><dt>Extracted snapshot SHA-256</dt><dd>{coverage.snapshot_sha256}</dd><dt>Snapshot identity</dt><dd>{coverage.snapshot_id}</dd><dt>Saved range</dt><dd>[{coverage.start}, {coverage.end}) of {coverage.total} Unicode characters</dd></dl><p>This snapshot hash identifies the complete extracted text. The source-content hash identifies this saved excerpt. Neither hash verifies a scientific claim or proof.</p></details>
  </section>
}
