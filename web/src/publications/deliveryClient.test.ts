import { afterEach, describe, expect, it, vi } from 'vitest'
import { cancelPublication, parsePublicationExecution, parsePublicationJob, publicationDoiUrl, publicationEvents, publicationJob, publicationJobs, publicationRecordUrl, reconcilePublication, resumePublication, startPublication } from './deliveryClient'
import { APPROVAL, ARTIFACT, BROWSER_CSRF, MANIFEST, manifestFixture, OPERATOR_CSRF, PUBLICATION_JOB, publicationJobFixture, REVIEW_HASH } from './test-fixtures'

afterEach(() => vi.unstubAllGlobals())

describe('verified publication job contracts', () => {
  it('rejects credentialed, foreign-host and unsafe record/DOI URLs', () => {
    expect(publicationRecordUrl('https://sandbox.zenodo.org/records/987654', 'sandbox')).toBe('https://sandbox.zenodo.org/records/987654')
    expect(publicationRecordUrl('https://zenodo.org/record/987654', 'production')).toBe('https://zenodo.org/record/987654')
    for (const url of ['http://sandbox.zenodo.org/records/1', 'https://zenodo.org/records/1', 'https://sandbox.zenodo.org.evil.example/records/1', 'https://token@sandbox.zenodo.org/records/1', 'https://sandbox.zenodo.org/records/1?access_token=secret', 'https://sandbox.zenodo.org/api/deposit/depositions/1', 'https://sandbox.zenodo.org/records/0']) expect(publicationRecordUrl(url, 'sandbox')).toBeNull()
    expect(publicationDoiUrl('10.5072/zenodo.987654')).toBe('https://doi.org/10.5072/zenodo.987654')
    expect(publicationDoiUrl('https://evil.example/doi')).toBeNull()
    expect(publicationDoiUrl('10.5072/unsafe<script>')).toBeNull()
  })

  it('requires complete verified record evidence and preserves an actual record ID distinct from a deposit ID', () => {
    const record = publicationJobFixture({ verified: true, status: 'published', stage: 'complete', doi: '10.5072/zenodo.987654', record_url: 'https://sandbox.zenodo.org/records/987654', can_cancel: false })
    expect(parsePublicationJob({ ...record, remote_deposit_id: '123' }).record_url).toBe('https://sandbox.zenodo.org/records/987654')
    expect(() => parsePublicationJob({ ...record, doi: null })).toThrow('invalid_response')
    expect(() => parsePublicationJob({ ...record, record_url: 'https://zenodo.org/records/987654' })).toThrow('invalid_response')
    expect(() => parsePublicationJob({ ...record, sandbox: false })).toThrow('invalid_response')
    expect(parsePublicationJob({ ...record, status: 'cancelled' }).verified).toBe(true)
  })

  it('retains unverified reserved or reported values without granting verification', () => {
    const reserved = parsePublicationJob(publicationJobFixture({ verified: false, status: 'interrupted', doi: '10.5072/zenodo.123', record_url: 'https://sandbox.zenodo.org/records/123', publication_reported: true }))
    expect(reserved.verified).toBe(false)
    expect(reserved.publication_reported).toBe(true)
    expect(() => parsePublicationExecution({ execution_available: true, reason: 'publication_execution_disabled' })).toThrow('invalid_response')
    expect(parsePublicationExecution({ execution_available: false, reason: 'publication_execution_disabled' }).execution_available).toBe(false)
  })
})

describe('owned publication queue actions', () => {
  it('sends exact manifest/approval/hash and both CSRF headers without calling Zenodo from the browser', async () => {
    const fetch = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => Response.json(publicationJobFixture()))
    vi.stubGlobal('fetch', fetch)
    await startPublication(manifestFixture(true), APPROVAL, BROWSER_CSRF, OPERATOR_CSRF)
    expect(fetch.mock.calls[0]).toEqual(['/api/v1/publication-jobs', expect.objectContaining({ credentials: 'same-origin', cache: 'no-store', redirect: 'error', method: 'POST', headers: { Accept: 'application/json', 'X-CSRF-Token': BROWSER_CSRF, 'X-Operator-CSRF-Token': OPERATOR_CSRF, 'Content-Type': 'application/json' }, body: JSON.stringify({ manifest_id: MANIFEST, approval_id: APPROVAL, expected_manifest_sha256: REVIEW_HASH }) })])
  })

  it('binds returned jobs to the requested manifest, destination and job identifiers', async () => {
    const fetch = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => Response.json(publicationJobFixture({ manifest_id: ARTIFACT })))
    vi.stubGlobal('fetch', fetch)
    await expect(startPublication(manifestFixture(true), APPROVAL, BROWSER_CSRF, OPERATOR_CSRF)).rejects.toThrow('invalid_response')
    fetch.mockResolvedValueOnce(Response.json(publicationJobFixture({ id: ARTIFACT })))
    await expect(publicationJob(PUBLICATION_JOB, manifestFixture())).rejects.toThrow('invalid_response')
    fetch.mockResolvedValueOnce(Response.json({ jobs: [publicationJobFixture({ manifest_sha256: BROWSER_CSRF })], next_cursor: null }))
    await expect(publicationJobs(manifestFixture())).rejects.toThrow('invalid_response')
  })

  it('uses read-only reconciliation and exact-current-approval continuation routes rather than another enqueue', async () => {
    const fetch = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => Response.json(publicationJobFixture()))
    vi.stubGlobal('fetch', fetch)
    await reconcilePublication(PUBLICATION_JOB, manifestFixture(), BROWSER_CSRF, OPERATOR_CSRF)
    await resumePublication(PUBLICATION_JOB, manifestFixture(true), APPROVAL, BROWSER_CSRF, OPERATOR_CSRF)
    await cancelPublication(PUBLICATION_JOB, manifestFixture(), BROWSER_CSRF, OPERATOR_CSRF)
    expect(fetch.mock.calls.map(([path]) => path)).toEqual([`/api/v1/publication-jobs/${PUBLICATION_JOB}/reconcile`, `/api/v1/publication-jobs/${PUBLICATION_JOB}/resume`, `/api/v1/publication-jobs/${PUBLICATION_JOB}/cancel`])
    expect(fetch.mock.calls[0][1]?.body).toBeUndefined()
    expect(JSON.parse(fetch.mock.calls[1][1]?.body as string)).toEqual({ approval_id: APPROVAL, expected_manifest_sha256: REVIEW_HASH })
  })

  it('validates ordered persisted event cursors and rejects malformed replay data', async () => {
    const fetch = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => Response.json({ events: [{ sequence: 2, kind: 'uploaded', summary: 'Verified saved file upload.', created_at: '2026-10-28T00:00:00Z' }], next_cursor: 2, terminal: false }))
    vi.stubGlobal('fetch', fetch)
    expect((await publicationEvents(PUBLICATION_JOB, 1)).events[0].sequence).toBe(2)
    expect(fetch.mock.calls[0][0]).toBe(`/api/v1/publication-jobs/${PUBLICATION_JOB}/events?after=1`)
    fetch.mockResolvedValueOnce(Response.json({ events: [{ sequence: 1, kind: 'old', summary: 'Already seen.', created_at: '2026-10-28T00:00:00Z' }], next_cursor: 1, terminal: false }))
    await expect(publicationEvents(PUBLICATION_JOB, 1)).rejects.toThrow('invalid_response')
  })

  it('rejects invalid inputs before any credentialed request is sent', async () => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    await expect(startPublication(manifestFixture(true), '../approval', BROWSER_CSRF, OPERATOR_CSRF)).rejects.toThrow('invalid_request')
    await expect(cancelPublication(PUBLICATION_JOB, manifestFixture(), BROWSER_CSRF, '')).rejects.toThrow('operator_expired')
    await expect(publicationEvents(PUBLICATION_JOB, -1)).rejects.toThrow('invalid_request')
    expect(fetch).not.toHaveBeenCalled()
  })
})
