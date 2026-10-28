import './bibliography.css'

export interface SourceBibliography {
  authors: { name: string; orcid: string | null }[]
  authors_truncated: boolean
  publication_year: number | null
  publication_date: string | null
  venue: string | null
  doi: string | null
  arxiv_id: string | null
  arxiv_version: number | null
  openalex_id: string | null
  license: string | null
}

export function parseBibliography(value: unknown, invalid: () => never): SourceBibliography | undefined {
  if (value === undefined || value === null) return undefined
  function record(raw: unknown): Record<string, unknown> { return raw !== null && typeof raw === 'object' && !Array.isArray(raw) ? raw as Record<string, unknown> : invalid() }
  function text(raw: unknown, maximum: number): string { return typeof raw === 'string' && !!raw.trim() && Array.from(raw).length <= maximum && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\uD800-\uDFFF]/u.test(raw) ? raw : invalid() }
  function optionalText(raw: unknown, maximum: number): string | null { return raw === null || raw === undefined ? null : text(raw, maximum) }
  function integer(raw: unknown, minimum: number, maximum: number): number | null { return raw === null || raw === undefined ? null : typeof raw === 'number' && Number.isSafeInteger(raw) && raw >= minimum && raw <= maximum ? raw : invalid() }
  const item = record(value), authors = item.authors ?? []
  if (!Array.isArray(authors) || authors.length > 100) return invalid()
  const parsedAuthors = authors.map((raw) => {
    const author = record(raw), orcid = optionalText(author.orcid, 19)
    if (orcid !== null) {
      if (!/^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$/.test(orcid)) return invalid()
      const digits = orcid.replaceAll('-', ''); let sum = 0
      for (const digit of digits.slice(0, 15)) sum = (sum + Number(digit)) * 2
      const checksum = (12 - sum % 11) % 11
      if (digits[15] !== (checksum === 10 ? 'X' : String(checksum))) return invalid()
    }
    return { name: text(author.name, 300), orcid }
  })
  const truncated = item.authors_truncated ?? false
  if (typeof truncated !== 'boolean' || truncated && parsedAuthors.length !== 100) return invalid()
  const publicationDate = optionalText(item.publication_date, 10)
  if (publicationDate !== null && (!/^\d{4}-\d{2}-\d{2}$/.test(publicationDate) || !Number.isFinite(Date.parse(publicationDate)) || new Date(publicationDate).toISOString().slice(0, 10) !== publicationDate || Number(publicationDate.slice(0, 4)) < 1000 || Number(publicationDate.slice(0, 4)) > 2100)) return invalid()
  const doi = optionalText(item.doi, 200), arxivId = optionalText(item.arxiv_id, 100), arxivVersion = integer(item.arxiv_version, 1, 9999), openalexId = optionalText(item.openalex_id, 40)
  if (doi !== null && !/^10\.\d{4,9}\/[A-Za-z\d][A-Za-z\d._;()/:+\-]{0,170}$/.test(doi)) return invalid()
  if (arxivId !== null && !/^(?:\d{4}\.\d{4,5}|[a-z]+(?:-[a-z]+)?(?:\.[A-Z]{2})?\/\d{7})$/.test(arxivId) || arxivVersion !== null && arxivId === null) return invalid()
  if (openalexId !== null && !/^W[1-9]\d{0,18}$/.test(openalexId)) return invalid()
  return { authors: parsedAuthors, authors_truncated: truncated, publication_year: integer(item.publication_year, 1000, 2100), publication_date: publicationDate, venue: optionalText(item.venue, 500), doi, arxiv_id: arxivId, arxiv_version: arxivVersion, openalex_id: openalexId, license: optionalText(item.license, 100) }
}

export function Bibliography({ value }: { value?: SourceBibliography }) {
  if (!value) return null
  return <details className="source-bibliography"><summary>Bibliographic details</summary><p>Provider-declared metadata from this saved retrieval. Aliases do not establish independent corroboration.</p><dl>
    {value.authors.length > 0 && <><dt>Authors</dt><dd>{value.authors.map((author, index) => <span key={index}>{index > 0 && '; '}{author.name}{author.orcid && <> (<a href={`https://orcid.org/${author.orcid}`} target="_blank" rel="noopener noreferrer">ORCID {author.orcid}</a>)</>}</span>)}{value.authors_truncated && ' · Author list truncated after 100 names.'}</dd></>}
    {(value.publication_date || value.publication_year) && <><dt>Publication date</dt><dd>{value.publication_date || value.publication_year}</dd></>}
    {value.venue && <><dt>Venue</dt><dd>{value.venue}</dd></>}
    {value.doi && <><dt>DOI</dt><dd><a href={`https://doi.org/${value.doi}`} target="_blank" rel="noopener noreferrer">{value.doi}</a></dd></>}
    {value.arxiv_id && <><dt>arXiv</dt><dd><a href={`https://arxiv.org/abs/${value.arxiv_id}${value.arxiv_version ? `v${value.arxiv_version}` : ''}`} target="_blank" rel="noopener noreferrer">{value.arxiv_id}{value.arxiv_version ? `v${value.arxiv_version}` : ''}</a></dd></>}
    {value.openalex_id && <><dt>OpenAlex</dt><dd><a href={`https://openalex.org/${value.openalex_id}`} target="_blank" rel="noopener noreferrer">{value.openalex_id}</a></dd></>}
    {value.license && <><dt>Reported license</dt><dd>{value.license}</dd></>}
  </dl></details>
}
