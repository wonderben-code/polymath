import { afterEach, describe, expect, it, vi } from 'vitest'
import { approvePublication, loginOperator, logoutOperator, manifestMatchesArtifact, parseCapabilities, parseManifest, parseMetadata, parseOperatorSession, preparePublication, publicationHistory, revokePublication, validOrcid } from './client'
import { APPROVAL, ARTIFACT, artifactFixture, BROWSER_CSRF, capabilityFixture, MANIFEST, manifestFixture, metadataFixture, OPERATOR_CSRF, operatorFixture, REVIEW_HASH, RUN } from './test-fixtures'

afterEach(() => vi.unstubAllGlobals())

describe('exact publication contracts', () => {
  it('binds the review to document identity, bytes and all source version hashes', () => {
    const document = artifactFixture(), record = parseManifest(manifestFixture())
    expect(manifestMatchesArtifact(record, document)).toBe(true)
    for (const changed of [{ artifact_id: RUN }, { artifact_run_id: ARTIFACT }, { markdown_sha256: REVIEW_HASH }, { payload_sha256: REVIEW_HASH }, { basis_sha256: REVIEW_HASH }, { markdown_bytes: 1 }]) expect(manifestMatchesArtifact({ ...record, manifest: { ...record.manifest, ...changed } }, document)).toBe(false)
    expect(manifestMatchesArtifact(record, { ...document, kind: 'formalise' })).toBe(false)
    expect(() => parseManifest({ ...record, artifact_export_url: 'https://example.org/foreign' })).toThrow('invalid_response')
  })

  it('refuses a receipt for another manifest or hash and never accepts a published status', () => {
    const record = manifestFixture(true)
    for (const changed of [{ manifest_id: ARTIFACT }, { manifest_sha256: BROWSER_CSRF }, { revoked_at: '2026-10-29T00:00:00Z', valid: true }]) expect(() => parseManifest({ ...record, approval: { ...record.approval, ...changed } })).toThrow('invalid_response')
    expect(() => parseManifest({ ...record, publication_status: 'published' })).toThrow('invalid_response')
    expect(() => parseManifest({ ...record, manifest: { ...record.manifest, file_name: '../private.md' } })).toThrow('invalid_response')
  })

  it('validates metadata and identities without activating links or HTML', () => {
    expect(parseMetadata(metadataFixture()).license).toBe('cc-by-4.0')
    expect(validOrcid('0000-0002-1825-0097')).toBe(true)
    expect(validOrcid('0000-0002-1825-0098')).toBe(false)
    for (const changed of [{ creators: [] }, { title: '<b>Injected title</b>' }, { description: 'Visit https://example.org' }, { license: 'unknown' }, { keywords: ['SAME', 'same'] }, { creators: [{ name: 'Synthetic', orcid: '0000-0000-0000-0002' }] }]) expect(() => parseMetadata({ ...metadataFixture(), ...changed })).toThrow('invalid_response')
    expect(() => parseCapabilities({ ...capabilityFixture(), publisher: null })).toThrow('invalid_response')
    expect(() => parseOperatorSession({ ...operatorFixture(), csrf_token: OPERATOR_CSRF })).toThrow('invalid_response')
  })
})

describe('separate browser and publisher approval boundaries', () => {
  it('prepares metadata with browser CSRF, then approves only the exact hash with both CSRF tokens', async () => {
    const fetch = vi.fn(async (_: RequestInfo | URL, init?: RequestInit) => Response.json(init?.headers && 'X-Operator-CSRF-Token' in init.headers ? manifestFixture(true) : manifestFixture()))
    vi.stubGlobal('fetch', fetch)
    const record = await preparePublication(ARTIFACT, metadataFixture(), 'sandbox', BROWSER_CSRF)
    expect(fetch.mock.calls[0]).toEqual(['/api/v1/publications', expect.objectContaining({ method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error', headers: { Accept: 'application/json', 'X-CSRF-Token': BROWSER_CSRF, 'Content-Type': 'application/json' }, body: JSON.stringify({ artifact_id: ARTIFACT, metadata: metadataFixture(), destination: 'sandbox' }) })])
    await approvePublication(record, BROWSER_CSRF, OPERATOR_CSRF)
    expect(fetch.mock.calls[1]).toEqual([`/api/v1/publications/${MANIFEST}/approve`, expect.objectContaining({ headers: { Accept: 'application/json', 'X-CSRF-Token': BROWSER_CSRF, 'Content-Type': 'application/json', 'X-Operator-CSRF-Token': OPERATOR_CSRF }, body: JSON.stringify({ expected_manifest_sha256: REVIEW_HASH }) })])
  })

  it('uses an ephemeral credential only for login; logout and revoke use the operator CSRF', async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => Response.json(input.toString().endsWith('/logout') ? operatorFixture() : input.toString().endsWith('/session') ? operatorFixture(true) : { ...manifestFixture(true), approval: { ...manifestFixture(true).approval, valid: false, revoked_at: '2026-10-29T00:00:00Z' } }))
    vi.stubGlobal('fetch', fetch)
    await loginOperator('synthetic-ephemeral-access-code', BROWSER_CSRF)
    expect(JSON.parse(fetch.mock.calls[0][1]?.body as string)).toEqual({ token: 'synthetic-ephemeral-access-code' })
    await logoutOperator(BROWSER_CSRF, OPERATOR_CSRF)
    await revokePublication(manifestFixture(true), BROWSER_CSRF, OPERATOR_CSRF)
    expect(fetch.mock.calls[2][0]).toBe(`/api/v1/publications/approvals/${APPROVAL}/revoke`)
    expect(fetch.mock.calls[1][1]?.headers).toMatchObject({ 'X-CSRF-Token': BROWSER_CSRF, 'X-Operator-CSRF-Token': OPERATOR_CSRF })
    expect(fetch.mock.calls[2][1]?.body).toBeUndefined()
  })

  it('distinguishes expired publisher access from an expired browser session', async () => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    for (const [status, detail, code] of [[401, 'operator_session_required', 'operator_expired'], [403, 'operator_csrf_check_failed', 'operator_expired'], [401, 'research_session_required', 'browser_expired'], [403, 'csrf_check_failed', 'browser_changed'], [409, 'publication_manifest_changed', 'manifest_changed']] as const) {
      fetch.mockResolvedValueOnce(Response.json({ detail }, { status }))
      await expect(approvePublication(manifestFixture(), BROWSER_CSRF, OPERATOR_CSRF)).rejects.toMatchObject({ code })
    }
  })

  it('rejects changed review content or a foreign artifact even in an otherwise successful response', async () => {
    const changed = manifestFixture(true)
    changed.manifest.metadata.title = 'Another review title'
    const fetch = vi.fn(async () => Response.json(changed))
    vi.stubGlobal('fetch', fetch)
    await expect(approvePublication(manifestFixture(), BROWSER_CSRF, OPERATOR_CSRF)).rejects.toThrow('invalid_response')
    changed.manifest.artifact_id = RUN
    changed.manifest.file_name = `polymath-${RUN}.md`
    changed.artifact_export_url = `/api/v1/artifacts/${RUN}/export`
    await expect(publicationHistory(ARTIFACT)).rejects.toThrow('invalid_response')
  })

  it('refuses invalid writes locally without sending credentials or guessing a destination', async () => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    await expect(preparePublication('../private', metadataFixture(), 'sandbox', BROWSER_CSRF)).rejects.toThrow('invalid_request')
    await expect(approvePublication(manifestFixture(), BROWSER_CSRF, '')).rejects.toThrow('operator_expired')
    await expect(loginOperator('x'.repeat(257), BROWSER_CSRF)).rejects.toThrow('invalid_request')
    expect(fetch).not.toHaveBeenCalled()
  })
})
