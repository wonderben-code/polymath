import { afterEach, describe, expect, it, vi } from 'vitest'
import { createShare, getPublicFinding, getSharePreview, listShares, loadPublicLibrary, loadPublicMap, loadSharingCapabilities, parsePublicCoverage, parsePublicFinding, parsePublicLibrary, parsePublicMap, parsePublicProjection, parseShareRecord, parseSharingCapabilities, revokeShare, safePublicScholarlyLink } from './client'
import { canonicalJson, capabilities, CSRF, HASH, IDEMPOTENCY_KEY, library, map as publicMap, preview, projection, PUBLIC_ID, publicFinding, QUOTE_A, RESULT_ID, SECOND_PUBLIC_ID, sha256, shareRecord } from './test-support'
import type { CreateShare, PublicCitation, PublicProjection } from './types'

const body = (): CreateShare => ({ source_result_id: RESULT_ID, expected_projection_sha256: HASH, idempotency_key: IDEMPOTENCY_KEY })
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

function budgetProjection(): PublicProjection {
  const result = projection(), original = result.citations[0], quote = '🌱'.repeat(400), whole = 'h'.repeat(100) + quote + 't'.repeat(3500)
  result.citations = Array.from({ length: 5 }, (_, index): PublicCitation => {
    const length = index < 3 ? 280 : index === 3 ? 160 : 0, excerpt = length ? Array.from(quote).slice(0, length).join('') : null
    return { ...original, id: `citation-${index + 1}`, source_sha256: sha256(quote), coverage: { ...original.coverage!, snapshot_sha256: sha256(whole) }, quote: { excerpt, excerpted: true, excerpt_start: excerpt === null ? null : 0, excerpt_end: excerpt === null ? null : length, full_quote_start: 0, full_quote_end: 400, full_quote_chars: 400, full_quote_sha256: sha256(quote), excerpt_omission_reason: excerpt === null ? 'paper_limit' : null } }
  })
  result.citations.push({ ...projection().citations[1], id: 'citation-6' })
  if (result.finding.kind !== 'bridge') throw new Error('Synthetic fixture kind changed')
  result.finding.citation_ids = result.citations.map((citation) => citation.id)
  return result
}

describe('strict public-only finding projection', () => {
  it('preserves complete Bridge and Ask text, classifications and every local citation association', () => {
    expect(parsePublicProjection(projection())).toEqual(projection())
    const answer = parsePublicProjection(projection('answer'))
    if (answer.finding.kind !== 'answer') throw new Error('Expected synthetic Ask answer')
    expect(answer.finding.claims.map((claim) => claim.label)).toEqual(['known', 'postulated', 'speculative'])
    expect(answer.finding.claims.flatMap((claim) => claim.citation_ids)).toEqual(answer.citations.map((citation) => citation.id))
    expect(answer.finding.question).toBe('What does the public synthetic evidence establish?')
    expect(sha256(canonicalJson(projection()))).toBe(HASH)
    expect(answer.citations.every((citation) => sha256(citation.quote.excerpt!) === citation.quote.full_quote_sha256)).toBe(true)
  })
  it('retains only final Surprise fields and requires their exact mapped direction', () => {
    const selected = { ...projection(), mode: 'surprise', field_selection: { fields: ['Synthetic field A', 'Synthetic field B'], rationale: 'A final synthetic model-selected direction.' } }
    expect(parsePublicProjection(selected).field_selection).toEqual(selected.field_selection)
    expect(() => parsePublicProjection({ ...selected, field_selection: undefined })).toThrow('invalid_response')
    expect(() => parsePublicProjection({ ...selected, mode: 'explore' })).toThrow('invalid_response')
    expect(() => parsePublicProjection({ ...selected, field_selection: { ...selected.field_selection, fields: ['Other A', 'Other B'] } })).toThrow('invalid_response')
  })
  it('keeps snapshot coverage UUID-free and distinguishes quote/excerpt offsets using Unicode codepoints', () => {
    const saved = parsePublicProjection(projection()), quote = saved.citations[0].quote
    expect(QUOTE_A.length).toBe(Array.from(QUOTE_A).length + 1)
    expect(quote.excerpt_end! - quote.excerpt_start!).toBe(Array.from(QUOTE_A).length)
    expect(saved.citations[0].coverage).not.toHaveProperty('snapshot_id')
    expect(() => parsePublicCoverage({ ...saved.citations[0].coverage, snapshot_id: RESULT_ID })).toThrow('invalid_response')
    expect(() => parsePublicCoverage({ ...saved.citations[0].coverage, arxiv_id: '2613.00001' })).toThrow('invalid_response')
    expect(() => parsePublicCoverage({ ...saved.citations[0].coverage, start: 500 })).toThrow('invalid_response')
    expect(() => parsePublicCoverage({ ...saved.citations[0].coverage, end: 8101, total: 10000 })).toThrow('invalid_response')
    expect(() => parsePublicCoverage({ ...saved.citations[0].coverage, snapshot_sha256: 'A'.repeat(64) })).toThrow('invalid_response')
  })
  it('preserves exact whitespace-only quoted prefixes without trimming or changing their bytes', () => {
    const value = projection(), excerpt = ' '.repeat(10)
    value.citations[0].quote = { excerpt, excerpted: false, excerpt_start: 7, excerpt_end: 17, full_quote_start: 7, full_quote_end: 17, full_quote_chars: 10, full_quote_sha256: sha256(excerpt), excerpt_omission_reason: null }
    expect(parsePublicProjection(value).citations[0].quote.excerpt).toBe(excerpt)
  })
  it('enforces per-paper repeated/overlapping excerpt budgets and explicit omissions without dropping citation links', () => {
    const saved = budgetProjection(), parsed = parsePublicProjection(saved)
    expect(parsed.citations.filter((citation) => citation.paper_id === 'paper-1').reduce((sum, citation) => sum + Array.from(citation.quote.excerpt ?? '').length, 0)).toBe(1000)
    expect(parsed.citations[4].quote).toMatchObject({ excerpt: null, excerpted: true, excerpt_start: null, excerpt_end: null, excerpt_omission_reason: 'paper_limit' })
    expect(parsed.finding.kind === 'bridge' && parsed.finding.citation_ids).toHaveLength(6)
    const over = budgetProjection(); over.citations[3].quote.excerpt = '🌱'.repeat(161); over.citations[3].quote.excerpt_end = 161
    expect(() => parsePublicProjection(over)).toThrow('invalid_response')
    const premature = projection(); premature.citations[0].quote = { ...premature.citations[0].quote, excerpt: null, excerpted: true, excerpt_start: null, excerpt_end: null, excerpt_omission_reason: 'paper_limit' }
    expect(() => parsePublicProjection(premature)).toThrow('invalid_response')
  })
  it('rejects forged excerpt/full offsets, misplaced coverage and missing original claim associations', () => {
    const mutations: ((value: PublicProjection) => void)[] = [
      (value) => { value.citations[0].quote.excerpt_end!++ },
      (value) => { value.citations[0].quote.full_quote_chars++ },
      (value) => { value.citations[0].quote.excerpted = true },
      (value) => { value.citations[0].quote.excerpt_omission_reason = 'paper_limit' },
      (value) => { value.citations[0].quote.excerpt = 'x'.repeat(281) },
      (value) => { value.citations[0].quote.full_quote_sha256 = 'not-a-hash' },
      (value) => { value.citations[0].coverage!.end = 110 },
      (value) => { value.citations[0].source_kind = 'abstract' },
      (value) => { value.citations[0].claim_id = 'claim-1' },
      (value) => { value.citations[0].paper_id = 'paper-3' },
      (value) => { value.citations[0].id = 'citation-3' },
      (value) => { if (value.finding.kind === 'bridge') value.finding.citation_ids.reverse() },
    ]
    for (const mutate of mutations) { const value = projection(); mutate(value); expect(() => parsePublicProjection(value)).toThrow('invalid_response') }
    const answer = projection('answer'); answer.citations[1].claim_id = 'claim-1'
    expect(() => parsePublicProjection(answer)).toThrow('invalid_response')
    const omittedClaim = projection('answer'); if (omittedClaim.finding.kind === 'answer') omittedClaim.finding.claims[1].citation_ids.pop()
    expect(() => parsePublicProjection(omittedClaim)).toThrow('invalid_response')
  })
  it('retains all uncertainty requirements and bounded, explicitly truncated public bibliography', () => {
    const original = projection(), paper = original.papers[0]
    expect(parsePublicProjection(original).papers[0].bibliography?.authors[0].orcid).toBe('0000-0002-1825-0097')
    for (const change of [{ authors: Array.from({ length: 21 }, () => ({ name: 'Synthetic Author', orcid: null })) }, { authors_truncated: true }, { publication_date: '2026-02-30' }, { publication_year: 999 }, { license: 'private-field' }]) expect(() => parsePublicProjection({ ...original, papers: [{ ...paper, bibliography: { ...paper.bibliography, ...change } }, original.papers[1]] })).toThrow('invalid_response')
    const truncated = projection(); truncated.papers[0].bibliography!.authors = Array.from({ length: 20 }, () => ({ name: 'Synthetic Author', orcid: null })); truncated.papers[0].bibliography!.authors_truncated = true
    expect(parsePublicProjection(truncated).papers[0].bibliography?.authors).toHaveLength(20)
    for (const index of [1, 2]) { const value = projection('answer'); if (value.finding.kind !== 'answer') throw new Error('Expected Ask'); if (index === 1) value.finding.claims[1].derivation = null; else value.finding.claims[2].caveats = []; expect(() => parsePublicProjection(value)).toThrow('invalid_response') }
  })
  it('rejects structural private fields at every public boundary instead of stripping a private research result', () => {
    const original = projection()
    for (const changed of [
      { ...original, run_id: RESULT_ID }, { ...original, events: [] }, { ...original, sources: [] },
      { ...original, finding: { ...original.finding, evidence: [] } },
      { ...original, papers: [{ ...original.papers[0], source_id: 'private:source' }, original.papers[1]] },
      { ...original, citations: [{ ...original.citations[0], retrieval_id: RESULT_ID }, original.citations[1]] },
      { ...original, citations: [{ ...original.citations[0], quote: { ...original.citations[0].quote, text: 'A forbidden complete quote body.' } }, original.citations[1]] },
    ]) expect(() => parsePublicProjection(changed)).toThrow('invalid_response')
    expect(() => parsePublicFinding({ ...publicFinding(), source_result_id: RESULT_ID })).toThrow('invalid_response')
    expect(() => parsePublicLibrary({ ...library(), owner_session_hash: HASH })).toThrow('invalid_response')
  })
  it.each([
    { kind: 'arxiv', url: 'https://arxiv.org/html/2610.00001v2' },
    { kind: 'arxiv', url: 'https://arxiv.org/abs/hep-th/9910001v2' },
    { kind: 'doi', url: 'https://doi.org/10.1234/synthetic.fixture' },
    { kind: 'openalex', url: 'https://openalex.org/W123456' },
  ])('allows only canonical reconstructed scholarly links %#', (link) => expect(safePublicScholarlyLink(link)).toBe(true))
  it.each(['javascript:alert(1)', 'https://arxiv.org.evil.example/html/2610.00001v2', 'https://secret@arxiv.org/html/2610.00001v2', 'https://arxiv.org/html/2610.00001v2?token=secret', 'https://arxiv.org/html/2610.00001v2#private', 'http://arxiv.org/html/2610.00001v2', 'https://arxiv.org:443/html/2610.00001v2', 'https://arxiv.org/html/2610.00001', 'https://example.org/paper'])('rejects arbitrary, credential-bearing, token-bearing or noncanonical link %s', (url) => expect(safePublicScholarlyLink({ kind: 'arxiv', url })).toBe(false))
  it('binds every scholarly link to declared aliases and prevents alias splitting to evade paper budgets', () => {
    for (const mutate of [
      (value: PublicProjection) => { value.papers[0].links = [] },
      (value: PublicProjection) => { value.papers[0].links[0].url = 'https://arxiv.org/html/2610.00002v2' },
      (value: PublicProjection) => { value.papers[0].links.push(value.papers[0].links[0]) },
      (value: PublicProjection) => { value.papers[1].identifiers = { ...value.papers[0].identifiers }; value.papers[1].links = [...value.papers[0].links] },
    ]) { const value = projection(); mutate(value); expect(() => parsePublicProjection(value)).toThrow('invalid_response') }
    const noLinks = projection(); noLinks.papers[0].identifiers = {}; noLinks.papers[0].links = []
    expect(parsePublicProjection(noLinks).papers[0].links).toEqual([])
  })
  it('bounds the complete UTF-8 projection even when every individual field is within its limit', () => {
    const value = projection('answer')
    if (value.finding.kind !== 'answer') throw new Error('Expected Ask')
    value.papers = Array.from({ length: 64 }, (_, index) => ({ id: `paper-${index + 1}`, title: '🌱'.repeat(1000), identifiers: {}, links: [] }))
    value.citations = Array.from({ length: 64 }, (_, index): PublicCitation => ({ ...projection('answer').citations[0], id: `citation-${index + 1}`, paper_id: `paper-${index + 1}`, claim_id: `claim-${Math.floor(index / 8) + 1}`, supports: 'x'.repeat(1500) }))
    value.finding.claims = Array.from({ length: 8 }, (_, index) => ({ id: `claim-${index + 1}`, text: 'A synthetic classified premise.', label: 'known', derivation: null, caveats: [], citation_ids: value.citations.slice(index * 8, index * 8 + 8).map((citation) => citation.id) }))
    expect(new TextEncoder().encode(canonicalJson(value)).byteLength).toBeGreaterThan(262144)
    expect(() => parsePublicProjection(value)).toThrow('invalid_response')
  })
})

describe('owner grants and session-free public reads', () => {
  it('keeps sharing independently disabled and validates capabilities consistently', async () => {
    expect(parseSharingCapabilities(capabilities(false))).toEqual(capabilities(false))
    expect(() => parseSharingCapabilities({ sharing_available: true, reason: 'sharing_unavailable' })).toThrow('invalid_response')
    const fetch = vi.fn(async () => Response.json(capabilities(false))); vi.stubGlobal('fetch', fetch)
    expect((await loadSharingCapabilities()).sharing_available).toBe(false)
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/v1/shares/capabilities', expect.objectContaining({ method: 'GET', cache: 'no-store', credentials: 'same-origin' }))
  })
  it('loads the exact owned preview and bounded owner history without creating a session or sharing', async () => {
    const fetch = vi.fn(async (_path: string, _init?: RequestInit) => Response.json(preview())); vi.stubGlobal('fetch', fetch)
    expect((await getSharePreview(RESULT_ID)).projection_sha256).toBe(HASH)
    expect(fetch.mock.calls[0][0]).toBe(`/api/v1/shares/preview?source_result_id=${RESULT_ID}`)
    fetch.mockImplementation(async () => Response.json({ shares: [shareRecord()], next_cursor: PUBLIC_ID }))
    expect((await listShares(RESULT_ID, undefined, SECOND_PUBLIC_ID)).shares).toHaveLength(1)
    expect(fetch.mock.calls.at(-1)?.[0]).toBe(`/api/v1/shares?source_result_id=${RESULT_ID}&limit=5&before=${SECOND_PUBLIC_ID}`)
    expect(fetch.mock.calls.every(([, init]) => init?.method === 'GET')).toBe(true)
    expect(fetch.mock.calls.some(([path]) => path.includes('/session'))).toBe(false)
  })
  it('pins explicit grants to the displayed hash, source and unchanged caller-owned idempotency key', async () => {
    const fetch = vi.fn(async () => Response.json(shareRecord())); vi.stubGlobal('fetch', fetch)
    expect((await createShare(body(), CSRF)).public_id).toBe(PUBLIC_ID)
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/v1/shares', expect.objectContaining({ method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error', headers: { Accept: 'application/json', 'X-CSRF-Token': CSRF, 'Content-Type': 'application/json' }, body: JSON.stringify(body()) }))
  })
  it('never automatically retries a lost grant and permits an explicit identical retry to return its revoked history', async () => {
    const fetch = vi.fn(async (_path: string, _init?: RequestInit): Promise<Response> => { throw new TypeError('Synthetic lost receipt, private transport details.') }); vi.stubGlobal('fetch', fetch)
    const originalBody = body()
    await expect(createShare(originalBody, CSRF)).rejects.toMatchObject({ code: 'unavailable', uncertain: true })
    expect(fetch).toHaveBeenCalledTimes(1)
    fetch.mockImplementation(async () => Response.json(shareRecord({ status: 'revoked' })))
    expect((await createShare(originalBody, CSRF)).status).toBe('revoked')
    expect(fetch.mock.calls[0][1]?.body).toBe(fetch.mock.calls[1][1]?.body)
    expect(originalBody).toEqual(body())
  })
  it('requires an exact owned revocation receipt and treats mismatched accepted writes as uncertain', async () => {
    const fetch = vi.fn(async () => Response.json(shareRecord({ status: 'revoked' }))); vi.stubGlobal('fetch', fetch)
    expect((await revokeShare(PUBLIC_ID, CSRF)).revoked_at).not.toBeNull()
    expect(fetch).toHaveBeenCalledWith(`/api/v1/shares/${PUBLIC_ID}/revoke`, expect.objectContaining({ method: 'POST', headers: { Accept: 'application/json', 'X-CSRF-Token': CSRF } }))
    for (const changes of [{ source_result_id: IDEMPOTENCY_KEY }, { projection_sha256: CSRF }, { idempotency_key: RESULT_ID }]) {
      fetch.mockImplementation(async () => Response.json(shareRecord(changes)))
      await expect(createShare(body(), CSRF)).rejects.toMatchObject({ code: 'invalid_response', uncertain: true })
    }
    fetch.mockImplementation(async () => Response.json(shareRecord({ status: 'revoked', public_id: SECOND_PUBLIC_ID })))
    await expect(revokeShare(PUBLIC_ID, CSRF)).rejects.toMatchObject({ code: 'invalid_response', uncertain: true })
  })
  it.each([401, 403])('reports owner HTTP %s as session expiry without retaining raw error text', async (status) => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ detail: 'Untrusted private error text.' }, { status })))
    await expect(getSharePreview(RESULT_ID)).rejects.toMatchObject({ code: 'session_expired', uncertain: false })
  })
  it('separates definite admission errors from possibly committed storage failures', async () => {
    const fetch = vi.fn(async () => Response.json({ detail: 'sharing_unavailable' }, { status: 503 })); vi.stubGlobal('fetch', fetch)
    await expect(createShare(body(), CSRF)).rejects.toMatchObject({ code: 'sharing_unavailable', uncertain: false })
    fetch.mockImplementation(async () => Response.json({ detail: 'sharing_idempotency_conflict' }, { status: 409 }))
    await expect(createShare(body(), CSRF)).rejects.toMatchObject({ code: 'sharing_idempotency_conflict', uncertain: false })
    fetch.mockImplementation(async () => Response.json({ detail: 'private_storage_details' }, { status: 503 }))
    await expect(createShare(body(), CSRF)).rejects.toMatchObject({ code: 'unavailable', uncertain: true })
  })
  it('loads public detail/library/map using only credential-free no-store GETs', async () => {
    const fetch = vi.fn(async (path: string, _init?: RequestInit) => Response.json(path.includes('/findings/') ? publicFinding() : path.includes('/library?') ? library() : publicMap())); vi.stubGlobal('fetch', fetch)
    expect((await getPublicFinding(PUBLIC_ID)).public_id).toBe(PUBLIC_ID)
    expect((await loadPublicLibrary('A&B %_')).items).toHaveLength(1)
    expect((await loadPublicMap('A&B %_')).nodes).toHaveLength(5)
    expect(fetch.mock.calls.map(([path]) => path)).toEqual([`/api/v1/public/findings/${PUBLIC_ID}`, '/api/v1/public/library?q=A%26B+%25_&limit=20', '/api/v1/public/map?q=A%26B+%25_'])
    expect(fetch.mock.calls.every(([, init]) => init?.credentials === 'omit' && init.method === 'GET' && init.cache === 'no-store' && init.redirect === 'error' && init.referrerPolicy === 'no-referrer' && Object.keys(init.headers ?? {}).join() === 'Accept')).toBe(true)
    expect(fetch.mock.calls.some(([path]) => path.includes('/session'))).toBe(false)
  })
  it('never exposes owner metadata through public DTOs and preserves honest historical direct-page badges', () => {
    for (const status of ['current', 'superseded', 'unsupported', 'needs_review'] as const) expect(parsePublicFinding(publicFinding({ status })).status).toBe(status)
    expect(() => parsePublicFinding(shareRecord())).toThrow('invalid_response')
    expect(() => parsePublicFinding({ ...publicFinding(), status: 'revoked' })).toThrow('invalid_response')
    expect(() => parseShareRecord({ ...shareRecord(), public_path: `https://evil.example/shared/${PUBLIC_ID}` })).toThrow('invalid_response')
    expect(() => parsePublicLibrary(library({ items: [{ ...library().items[0], status: 'unsupported' as never }] }))).toThrow('invalid_response')
  })
  it('binds each fetched envelope and page cursor to its exact requested scope', async () => {
    const fetch = vi.fn(async () => Response.json(preview({ source_result_id: IDEMPOTENCY_KEY }))); vi.stubGlobal('fetch', fetch)
    await expect(getSharePreview(RESULT_ID)).rejects.toThrow('invalid_response')
    fetch.mockImplementation(async () => Response.json(publicFinding({ public_id: SECOND_PUBLIC_ID })))
    await expect(getPublicFinding(PUBLIC_ID)).rejects.toThrow('invalid_response')
    fetch.mockImplementation(async () => Response.json({ shares: [shareRecord({ source_result_id: IDEMPOTENCY_KEY })], next_cursor: null }))
    await expect(listShares(RESULT_ID)).rejects.toThrow('invalid_response')
    fetch.mockImplementation(async () => Response.json({ shares: [shareRecord()], next_cursor: SECOND_PUBLIC_ID }))
    await expect(listShares(RESULT_ID)).rejects.toThrow('invalid_response')
    fetch.mockImplementation(async () => Response.json(library({ next_cursor: SECOND_PUBLIC_ID })))
    await expect(loadPublicLibrary('')).rejects.toThrow('invalid_response')
    fetch.mockImplementation(async () => Response.json(library()))
    await expect(loadPublicLibrary('', undefined, PUBLIC_ID)).rejects.toThrow('invalid_response')
  })
  it('validates public map IDs/paths, edge topology, limits and source-link hosts without private pointers', () => {
    expect(parsePublicMap(publicMap())).toEqual(publicMap())
    for (const mutate of [
      (value: ReturnType<typeof publicMap>) => { value.nodes[0].public_path = 'https://evil.example' },
      (value: ReturnType<typeof publicMap>) => { value.nodes[1].id = `field:${RESULT_ID}` },
      (value: ReturnType<typeof publicMap>) => { value.nodes[3].links[0].url = 'https://arxiv.org/html/2610.00001v2?secret=value' },
      (value: ReturnType<typeof publicMap>) => { value.edges[0].source = value.nodes[1].id },
      (value: ReturnType<typeof publicMap>) => { value.edges.pop() },
      (value: ReturnType<typeof publicMap>) => { value.nodes.push(value.nodes[0]) },
      (value: ReturnType<typeof publicMap>) => { value.coverage.max_findings = 100 as never },
    ]) { const value = publicMap(); mutate(value); expect(() => parsePublicMap(value)).toThrow('invalid_response') }
    expect(() => parsePublicMap({ ...publicMap(), nodes: [{ ...publicMap().nodes[0], run_id: RESULT_ID }] })).toThrow('invalid_response')
  })
  it('rejects invalid inputs before any fetch or accidental private/public path conversion', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
    await expect(getPublicFinding(RESULT_ID)).rejects.toMatchObject({ code: 'sharing_not_found', uncertain: false })
    await expect(getSharePreview(PUBLIC_ID)).rejects.toMatchObject({ code: 'sharing_invalid_request', uncertain: false })
    for (const changed of [{ source_result_id: '../result' }, { expected_projection_sha256: 'bad' }, { idempotency_key: PUBLIC_ID }]) await expect(createShare({ ...body(), ...changed }, CSRF)).rejects.toMatchObject({ code: 'sharing_invalid_request', uncertain: false })
    await expect(createShare(body(), 'bad-csrf')).rejects.toMatchObject({ code: 'session_expired', uncertain: false })
    await expect(loadPublicLibrary('x'.repeat(201))).rejects.toThrow('sharing_invalid_request')
    await expect(loadPublicMap('bad\nquery')).rejects.toThrow('sharing_invalid_request')
    expect(fetch).not.toHaveBeenCalled()
  })
  it('bounds all response bytes and rejects malformed UTF-8/JSON as uncertain after a write', async () => {
    const fetch = vi.fn(async () => new Response('{invalid')); vi.stubGlobal('fetch', fetch)
    await expect(createShare(body(), CSRF)).rejects.toMatchObject({ code: 'invalid_response', uncertain: true })
    for (const value of [new Response(new Uint8Array(2 * 1024 * 1024 + 1)), new Response(new Uint8Array([255])), new Response('{}', { headers: { 'content-length': '2097153' } })]) {
      fetch.mockImplementation(async () => value)
      await expect(getPublicFinding(PUBLIC_ID)).rejects.toMatchObject({ code: 'invalid_response', uncertain: false })
    }
  })
  it('returns the same generic public 404 for absent, revoked, expired or disabled resources', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ detail: 'sharing_source_not_current' }, { status: 404 })))
    await expect(getPublicFinding(PUBLIC_ID)).rejects.toMatchObject({ code: 'sharing_not_found', uncertain: false })
    await expect(loadPublicLibrary('')).rejects.toMatchObject({ code: 'sharing_not_found', uncertain: false })
  })
  it('aborts and times out without retrying or replacing an unresolved idempotency key', async () => {
    vi.useFakeTimers()
    const fetch = vi.fn((_path: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true }))); vi.stubGlobal('fetch', fetch)
    const pending = createShare(body(), CSRF), assertion = expect(pending).rejects.toMatchObject({ code: 'unavailable', uncertain: true })
    await vi.advanceTimersByTimeAsync(10000); await assertion
    expect(fetch).toHaveBeenCalledTimes(1)
    const controller = new AbortController(); controller.abort()
    await expect(getPublicFinding(PUBLIC_ID, controller.signal)).rejects.toMatchObject({ code: 'aborted', uncertain: false })
    expect(fetch).toHaveBeenCalledTimes(1)
  })
})
