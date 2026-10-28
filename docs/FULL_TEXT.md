# Versioned HTML reading

Polymath can read bounded excerpts from open arXiv HTML when Meta explicitly
chooses to do so. Abstract retrieval remains available separately. An opened
index is metadata, not evidence that the paper has been read.

## Model actions

1. Search literature. A successful result may include an unambiguous arXiv HTML
   candidate in its supplemental metadata, including when the paper's primary
   reference is a DOI or OpenAlex record.
2. `open_full_text(source_id)` resolves an exact arXiv version and saves an
   immutable extracted snapshot before returning its heading index. The index
   includes section title spans, text length, versioned URL, extraction
   limitations and snapshot identity; it contains no paper body.
3. `read_full_text_window(snapshot_id, start, end)` returns at most 8,000 Unicode
   codepoints from that snapshot. Offsets start at zero and the end is exclusive.
   The exact window becomes a durable source read before the model receives it.
4. Quotes can cite those saved windows. Opening a paper, seeing a heading, or
   reading another range does not authorize an unseen passage. The same actions
   are available during explicit reassessment.

Two windows of one snapshot count as one paper. A positive conclusion cannot
manufacture independent corroboration from different ranges, abstracts, arXiv
versions or known DOI/OpenAlex aliases of that paper. Multiple cited windows
must belong to the same exact snapshot/version; mixing an abstract with a
full-text primary version is rejected. Identity-only metadata may connect
aliases but never grants a source read or quotation.

## Retrieval and extraction

Requests use only the fixed HTTPS arXiv hosts and validated identifiers. A
versionless candidate resolves through `export.arxiv.org/api/query` first; the
worker records that exact locator before requesting
`https://arxiv.org/html/<id>v<version>`. Redirects, arbitrary URLs, asset fetches,
PDF fallback and retries are disabled. The HTML request has a 20-second total
deadline and a streamed 2 MiB ceiling. Responses require UTF-8 HTML, a matching
head identity, and a recognizable article body.

Extraction excludes navigation and executable/embedded elements. It preserves
available mathematical alt text or TeX and marks missing semantic math. Table
cells remain separated, while layout, figures and mathematical interpretation
have explicit limitations. At most 250,000 extracted codepoints are accepted;
larger documents fail instead of being silently labelled complete. The index
contains at most 80 validated heading spans.

The raw HTML hash and extracted snapshot hash describe different objects. Each
window also has its own saved text hash. Citation displays distinguish these
identities and show the exact range, total length and arXiv version. The shared
paper URL has no invented range fragment.

## Persistence and recovery

Migration `0017_arxiv_full_text` stores immutable snapshots and owned run index
links. Shared text carries no owner's bibliography or observation timestamp;
each run pins its own reference metadata. An index requires a live owned run.
Reopening or resuming returns the pinned index without refetching or
re-extracting its body. A second alias of an already indexed snapshot is rejected
rather than silently changing that reference.

Checkpoint receipts retain exact locators, snapshot IDs and retrieval IDs, not
full paper bodies. Source storage independently checks the window's snapshot
hash, identity, URL and exact substring, including through database constraints.
Lost commit responses interrupt execution; an explicit resume retries the
free, idempotent read before any further model dispatch. Replayed search
observations restore their frozen metadata; any bounded abstract refresh cannot
replace a frozen full-text locator.

Coverage survives original-source memory reads, saved findings, reassessment,
document passages and exports. Copying a historical owned window permits only
that exact earlier range, not a fresh index or broader paper access. Historical
records without coverage retain their original hashes.

## Verification limits

Local tests use synthetic HTML, mock HTTP, synthetic models and real disposable
PostgreSQL schemas. They exercise the actual parser, worker, accounting and
browser paths. Native arXiv availability and layout compatibility still require
an accessible provider response. Scientific support, novelty and mathematical
correctness require separate evaluation. Paid Meta calls remain disabled until
the owner's existing spending prerequisite is satisfied.
