import type { PublicProjection } from './types'
import { safePublicScholarlyLink } from './client'
import './sharing.css'

export function sharingDate(value: string) { return new Date(value).toLocaleString() }

export function SnapshotStatus({ status }: { status: string }) {
  const label = status === 'current' ? 'Current model assessment' : status === 'superseded' ? 'Superseded snapshot' : status === 'unsupported' ? 'Unsupported by the latest model assessment' : status === 'needs_review' ? 'Needs review' : status === 'revoked' ? 'Revoked' : 'Expired'
  return <span className="sharing-status" data-state={status}>{label}</span>
}

export function publicTitle(projection: PublicProjection) { return projection.finding.kind === 'bridge' ? projection.finding.title : projection.finding.question }

export function ScholarlyLinks({ links }: { links: { kind: string; url: string }[] }) {
  return <div className="public-paper-links">{links.map((link) => safePublicScholarlyLink(link) && <a key={`${link.kind}:${link.url}`} href={link.url} target="_blank" rel="noopener noreferrer">{link.kind === 'arxiv' ? /v[1-9]\d{0,3}$/.test(link.url) ? 'Versioned arXiv paper' : 'arXiv paper' : link.kind === 'doi' ? 'DOI source' : 'OpenAlex record'}</a>)}</div>
}

function CitationLinks({ ids, prefix }: { ids: string[]; prefix: string }) {
  return <ul className="public-citation-links" aria-label="Associated source citations">{ids.map((id) => <li key={id}><a href={`#${prefix}-${id}`}>{id.replace('citation-', 'Citation ')}</a></li>)}</ul>
}

function PublicCoverage({ citation }: { citation: PublicProjection['citations'][number] }) {
  const coverage = citation.coverage
  if (!coverage) return citation.source_kind === 'full_text' ? <p>The original full-text snapshot’s reading range was not recorded.</p> : null
  const quote = citation.quote
  return <section className="public-coverage" aria-label="Public full-text excerpt coverage"><p><strong>Full text excerpt</strong> · saved window [{coverage.start}, {coverage.end}) of {coverage.total} extracted Unicode characters.</p>
    <p>arXiv {coverage.arxiv_id}v{coverage.arxiv_version}. The saved full quote covers extracted characters [{coverage.start + quote.full_quote_start}, {coverage.start + quote.full_quote_end}).{quote.excerpt_start !== null && quote.excerpt_end !== null && <> The displayed excerpt covers [{coverage.start + quote.excerpt_start}, {coverage.start + quote.excerpt_end}).</>}</p>
    <p>Only this bounded window was saved as source text. Figures, assets, and non-article navigation are omitted. Table layout may be lost. Mathematical alt text and TeX are plain text with unverified semantics; equations may be unavailable. This snapshot does not show that the whole paper was read.</p>
    <details><summary>Extracted snapshot integrity</summary><dl className="sharing-meta"><dt>Entire extracted snapshot SHA-256</dt><dd className="sharing-hash">{coverage.snapshot_sha256}</dd><dt>Original paper version</dt><dd>{coverage.arxiv_id}v{coverage.arxiv_version}</dd></dl><p>The snapshot hash identifies the full extracted text. The source-content hash below identifies the saved window.</p></details>
  </section>
}

export function PublicSnapshot({ projection, prefix = 'shared' }: { projection: PublicProjection; prefix?: string }) {
  const finding = projection.finding
  return <article className="public-snapshot" aria-label="Exact public finding snapshot">
    <span className="eyebrow accent">{finding.kind === 'answer' ? 'ASK · SHARED ANSWER' : projection.mode === 'surprise' ? 'SURPRISE · SHARED CONNECTION' : 'EXPLORE · SHARED CONNECTION'}</span>
    <h2>{publicTitle(projection)}</h2>
    <p className="public-notice">This is a model assessment with saved quotation integrity checks. Short excerpts do not establish scientific support, a proof, or whole-paper reading. No independent scientific review is implied.</p>
    {finding.kind === 'bridge' ? <>
      <p><strong>{finding.field_left}</strong> ↔ <strong>{finding.field_right}</strong></p><p>{finding.explanation}</p>
      <h3>Proposed structural mapping</h3><table className="public-mappings"><thead><tr><th scope="col">{finding.field_left}</th><th scope="col">{finding.field_right}</th><th scope="col">Assumption</th></tr></thead><tbody>{finding.mappings.map((mapping, index) => <tr key={index}><td>{mapping.left}</td><td>{mapping.right}</td><td>{mapping.assumption}</td></tr>)}</tbody></table>
      <h3>Open question</h3><p>{finding.open_question}</p><h3>Strongest objection</h3><p>{finding.strongest_objection}</p><h4>Model’s objection assessment</h4><p>{finding.objection_verdict}</p><h3>Novelty limitations</h3><p>{finding.novelty_limitations}</p><CitationLinks ids={finding.citation_ids} prefix={prefix} />
    </> : <>
      <p className="sharing-warning">The original question is part of this public snapshot.</p><h3>Answer summary</h3><p>{finding.summary}</p><h3>Claims and uncertainty</h3>
      {finding.claims.map((claim) => <section key={claim.id} className="public-claim" aria-label={`Shared ${claim.label} claim`}><span className="public-claim-label">{claim.label} · model label</span><p>{claim.text}</p>{claim.derivation && <><h4>Derivation</h4><p>{claim.derivation}</p></>}{claim.caveats.length > 0 && <><h4>Caveats</h4><ul>{claim.caveats.map((text, index) => <li key={index}>{text}</li>)}</ul></>}<CitationLinks ids={claim.citation_ids} prefix={prefix} /></section>)}
      <h3>Limitations</h3><ul>{finding.limitations.map((text, index) => <li key={index}>{text}</li>)}</ul><h3>Unanswered questions</h3><ul>{finding.unanswered_questions.map((text, index) => <li key={index}>{text}</li>)}</ul>
    </>}
    {projection.field_selection && <section aria-label="Final model-selected fields"><h3>Final field selection</h3><p>{projection.field_selection.fields.join(' · ')}</p><p>{projection.field_selection.rationale}</p></section>}
    <h3>Cited papers</h3><p>{projection.papers.length} paper {projection.papers.length === 1 ? 'identity' : 'identities'} · {projection.citations.length} retained citation {projection.citations.length === 1 ? 'association' : 'associations'}. Multiple excerpts from one paper do not provide independent corroboration.</p>
    <ol className="public-papers" aria-label="Public scholarly references">{projection.papers.map((paper) => <li key={paper.id} id={`${prefix}-${paper.id}`}><strong>{paper.title}</strong><ScholarlyLinks links={paper.links} /><dl className="sharing-meta">{paper.bibliography && <>{paper.bibliography.authors.length > 0 && <><dt>Authors</dt><dd>{paper.bibliography.authors.map((author) => `${author.name}${author.orcid ? ` (ORCID ${author.orcid})` : ''}`).join('; ')}{paper.bibliography.authors_truncated && ' · Author list truncated after 20 names.'}</dd></>}{(paper.bibliography.publication_date || paper.bibliography.publication_year) && <><dt>Publication date</dt><dd>{paper.bibliography.publication_date || paper.bibliography.publication_year}</dd></>}{paper.bibliography.venue && <><dt>Venue</dt><dd>{paper.bibliography.venue}</dd></>}</>}{paper.identifiers.doi && <><dt>DOI</dt><dd>{paper.identifiers.doi}</dd></>}{paper.identifiers.arxiv_id && <><dt>arXiv</dt><dd>{paper.identifiers.arxiv_id}{paper.identifiers.arxiv_version ? `v${paper.identifiers.arxiv_version}` : ''}</dd></>}{paper.identifiers.openalex_id && <><dt>OpenAlex</dt><dd>{paper.identifiers.openalex_id}</dd></>}</dl></li>)}</ol>
    <h3>Saved citation excerpts</h3><p>Each displayed quotation is an exact prefix of a saved verified quote, limited to 280 Unicode characters per citation and 1,000 emitted quotation characters per paper. Omitted excerpts retain their citation association and integrity metadata.</p>
    <ol className="public-citations" aria-label="Public saved citation excerpts">{projection.citations.map((citation) => {
      const paper = projection.papers.find((item) => item.id === citation.paper_id)!, quote = citation.quote
      return <li key={citation.id} id={`${prefix}-${citation.id}`}><h4>{citation.id.replace('citation-', 'Citation ')} · <a href={`#${prefix}-${paper.id}`}>{paper.title}</a></h4><p>{citation.side === 'answer' ? `Evidence association for ${citation.claim_id}` : `${citation.side === 'left' ? 'Left' : 'Right'} side of the proposed mapping`} · {citation.coverage ? 'Full text excerpt' : citation.source_kind === 'abstract' ? 'Abstract only' : 'Full-text snapshot'}</p>
        {quote.excerpt !== null ? <><p><strong>{quote.excerpted ? 'Short excerpt from the saved quote' : 'Saved quote excerpt'}</strong> · {Array.from(quote.excerpt).length} displayed Unicode characters{quote.excerpted ? ` of ${quote.full_quote_chars} saved quotation characters` : ''}.</p><blockquote>{quote.excerpt}</blockquote></> : <p className="sharing-warning">Excerpt omitted: the quotation budget for this paper has been reached. This citation’s association and hashes remain available.</p>}
        <p><strong>Recorded model support statement:</strong> {citation.supports}</p><PublicCoverage citation={citation} />
        <details><summary>Quotation ranges and integrity</summary><dl className="sharing-meta"><dt>Full saved quote range</dt><dd>[{quote.full_quote_start}, {quote.full_quote_end}) in the saved source/window · {quote.full_quote_chars} Unicode characters</dd><dt>Displayed excerpt range</dt><dd>{quote.excerpt_start === null ? 'No excerpt emitted' : `[${quote.excerpt_start}, ${quote.excerpt_end}) in the saved source/window`}</dd><dt>Full saved quote SHA-256</dt><dd className="sharing-hash">{quote.full_quote_sha256}</dd><dt>{citation.coverage ? 'Saved window content SHA-256' : 'Saved source content SHA-256'}</dt><dd className="sharing-hash">{citation.source_sha256}</dd></dl><p>All ranges are end excluded and count Unicode characters. These hashes establish saved-text integrity, not scientific correctness.</p></details>
      </li>
    })}</ol>
  </article>
}
