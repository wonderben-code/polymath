import { afterEach, describe, expect, it, vi } from 'vitest'
import { cancelArtifactJob, createArtifactJob, exportUrl, getArtifact, getArtifactJob, listArtifactJobs, parseArtifact, parseArtifactJob } from './client'
import { BIBLIOGRAPHY } from '../research/bibliography.test-support'

const RUN = '11111111-1111-4111-8111-111111111111'
const ARTIFACT = '22222222-2222-4222-8222-222222222222'
const SOURCE = '33333333-3333-4333-8333-333333333333'
const SOURCE_RUN = '44444444-4444-4444-8444-444444444444'
const FORMALISATION = '55555555-5555-4555-8555-555555555555'
const HASH = 'a'.repeat(64)
const DATE = '2026-10-28T00:00:00Z'
const paragraph = () => ({ text: 'Synthetic cited statement.', label: 'postulated', citation_ordinals: [0] })
const encoded = () => ({ variables: [{ name: 'n', lower: 0, upper: 1 }], assumptions: [], conclusion: { left: { op: 'variable', name: 'n' }, operator: 'eq', right: { op: 'constant', value: 0 } } })

function job(kind = 'write_summary') {
  return { id: RUN, mode: 'explore', task_kind: kind, goal: 'Synthetic source-backed document', status: 'succeeded', created_at: DATE, updated_at: DATE, result_id: null, artifact_id: ARTIFACT, error_code: null, external_outcome_unknown: false }
}
function saved() {
  return {
    id: ARTIFACT, run_id: RUN, kind: 'write_summary', status: 'ready', created_at: DATE,
    source_result_id: SOURCE, source_run_id: SOURCE_RUN, source_sha256: HASH, formalisation_id: null,
    passages: [{ ordinal: 0, source_id: 'fixture:source', title: 'Synthetic source', url: 'https://example.org/source', content_sha256: HASH, content_kind: 'abstract', quote: 'This is an exact synthetic source passage.', supports: 'Synthetic fixture support', claim_id: null }],
    payload: {
      kind: 'write_summary', status: 'ready',
      draft: { kind: 'summary', title: 'Synthetic summary', abstract: [paragraph()], sections: [{ heading: 'The finding', paragraphs: [paragraph()] }, { heading: 'What remains open', paragraphs: [paragraph()] }], limitations: ['Synthetic fixtures are not science.'] },
      checks: [] as unknown[], events: [], usage: { model_calls: 1, input_tokens_reported: 20, output_tokens_reported: 80, calls_without_usage: 0 },
      error_code: null, reason: null, validation_scope: 'citation_integrity_and_bounded_checks',
    },
    markdown: '# Synthetic summary\n', markdown_sha256: HASH, payload_sha256: HASH, basis_sha256: HASH,
  }
}
function formalised() {
  const fixture: Record<string, unknown> = saved()
  fixture.kind = 'formalise'
  const payload = fixture.payload as Record<string, unknown>
  payload.kind = 'formalise'
  payload.draft = { kind: 'formalisation', title: 'Synthetic conjecture', definitions: [{ symbol: 'n', meaning: paragraph() }], assumptions: [paragraph()], conjecture: paragraph(), proof_attempt: [paragraph()], limitations: ['This is not a general proof.'], finite_conjecture: encoded() }
  payload.checks = [{ conjecture: encoded(), conjecture_sha256: HASH, status: 'counterexample_found', assignments_tested: 2, applicable_cases: 2, total_assignments: 2, witness: { n: 1 }, lhs: '1', rhs: '0', error_code: null, scope: 'finite_integer_domain', proof_status: 'not_a_general_proof' }]
  return fixture
}

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

describe('artifact contracts', () => {
  it('preserves bounded full-text passage coverage and enforces its exact reference context', () => {
    const fixture = saved(), snapshot = '99999999-9999-4999-8999-999999999999'
    const coverage = { snapshot_id: snapshot, snapshot_sha256: 'c'.repeat(64), arxiv_id: BIBLIOGRAPHY.arxiv_id, arxiv_version: 2, start: 100, end: 300, total: 10000 }
    const passage = { ...fixture.passages[0], source_id: `arxiv-html:${snapshot}:100:300`, content_kind: 'full_text', url: 'https://arxiv.org/html/2610.00001v2', bibliography: BIBLIOGRAPHY, coverage }
    expect(parseArtifact({ ...fixture, passages: [passage] }).passages[0].coverage).toEqual(coverage)
    expect(parseArtifact(fixture).passages[0]).not.toHaveProperty('coverage')
    for (const change of [{ content_kind: 'abstract' }, { bibliography: undefined }, { source_id: `arxiv-html:${snapshot}:101:300` }, { url: 'https://arxiv.org/html/2610.00001v3' }, { coverage: { ...coverage, snapshot_sha256: 'bad' } }, { source_id: `arxiv-html:${snapshot}:100:110`, coverage: { ...coverage, end: 110 } }]) expect(() => parseArtifact({ ...fixture, passages: [{ ...passage, ...change }] })).toThrow('invalid_response')
  })
  it('preserves bibliography for exact original passages while retaining absent historical fields', () => {
    const fixture = saved()
    expect(parseArtifact({ ...fixture, passages: fixture.passages.map((passage) => ({ ...passage, bibliography: BIBLIOGRAPHY })) }).passages[0].bibliography).toEqual(BIBLIOGRAPHY)
    expect(parseArtifact(fixture).passages[0]).not.toHaveProperty('bibliography')
  })
  it('keeps artifact jobs separate from scientific finding result IDs', () => {
    expect(parseArtifactJob(job())).toMatchObject({ task_kind: 'write_summary', artifact_id: ARTIFACT, result_id: null })
    expect(() => parseArtifactJob({ ...job(), result_id: SOURCE })).toThrow('invalid_response')
    expect(() => parseArtifactJob({ ...job(), task_kind: 'research' })).toThrow('invalid_response')
    expect(() => parseArtifactJob({ ...job(), status: 'invented' })).toThrow('invalid_response')
  })

  it('preserves immutable source, version hashes, cited paragraphs, and literal text', () => {
    const fixture = saved()
    fixture.payload.draft.abstract[0].text = '<script>this remains plain text</script>'
    const parsed = parseArtifact(fixture)
    expect(parsed).toMatchObject({ source_result_id: SOURCE, source_sha256: HASH, basis_sha256: HASH, markdown: fixture.markdown })
    expect(parsed.payload.draft?.kind === 'summary' && parsed.payload.draft.abstract[0].text).toBe('<script>this remains plain text</script>')
  })

  it('rejects invented citation ordinals and duplicate source identities', () => {
    const unknown = saved()
    unknown.payload.draft.sections[0].paragraphs[0].citation_ordinals = [1]
    expect(() => parseArtifact(unknown)).toThrow('invalid_response')
    const repeated = saved()
    repeated.passages.push({ ...repeated.passages[0] })
    expect(() => parseArtifact(repeated)).toThrow('invalid_response')
    const repeatedCitation = saved()
    repeatedCitation.payload.draft.abstract[0].citation_ordinals = [0, 0]
    expect(() => parseArtifact(repeatedCitation)).toThrow('invalid_response')
  })

  it('rejects draft kind mismatches, malformed hashes, unsupported proof status, and unsafe source URLs', () => {
    for (const changed of [
      { ...saved(), kind: 'write_paper' },
      { ...saved(), markdown_sha256: 'not-a-hash' },
      { ...saved(), payload_sha256: 'B'.repeat(64) },
      { ...saved(), passages: [{ ...saved().passages[0], url: 'javascript:alert(1)' }] },
      { ...saved(), passages: [{ ...saved().passages[0], url: 'https://credential@example.org/source' }] },
    ]) expect(() => parseArtifact(changed)).toThrow('invalid_response')
    const proof = formalised()
    ;((proof.payload as Record<string, unknown>).checks as Record<string, unknown>[])[0].proof_status = 'proved'
    expect(() => parseArtifact(proof)).toThrow('invalid_response')
  })

  it('requires complete papers and does not accept unchecked formal encodings', () => {
    const paper = saved()
    paper.kind = 'write_paper'
    paper.payload.kind = 'write_paper'
    paper.payload.draft.kind = 'paper'
    expect(() => parseArtifact(paper)).toThrow('invalid_response')
    paper.payload.draft.sections = ['Background', 'Findings', 'Evidence', 'Limitations'].map((heading) => ({ heading, paragraphs: [paragraph()] }))
    expect(parseArtifact(paper).payload.draft?.kind).toBe('paper')
    const unchecked = formalised()
    ;(unchecked.payload as Record<string, unknown>).checks = []
    expect(() => parseArtifact(unchecked)).toThrow('invalid_response')
  })

  it('keeps mathematical counterexamples exact without granting proof status', () => {
    const fixture = formalised()
    const checks = (fixture.payload as Record<string, unknown>).checks as Record<string, unknown>[]
    checks[0].lhs = '9007199254740993'
    const parsed = parseArtifact(fixture)
    expect(parsed.payload.checks[0]).toMatchObject({ lhs: '9007199254740993', scope: 'finite_integer_domain', proof_status: 'not_a_general_proof' })
    checks[0].lhs = 9007199254740993
    expect(() => parseArtifact(fixture)).toThrow('invalid_response')
  })

  it('retains inherited formalisation checks under their exact saved basis', () => {
    const checks = (formalised().payload as Record<string, unknown>).checks as Record<string, unknown>[]
    checks[0].lhs = '9007199254740993'
    const fixture = { ...saved(), formalisation_id: FORMALISATION, formalisation_checks: checks }
    expect(parseArtifact(fixture).formalisation_checks[0]).toMatchObject({ lhs: '9007199254740993', status: 'counterexample_found', proof_status: 'not_a_general_proof' })
    expect(parseArtifact(saved()).formalisation_checks).toEqual([])
    expect(() => parseArtifact({ ...fixture, formalisation_id: null })).toThrow('invalid_response')
    checks[0].proof_status = 'proved'
    expect(() => parseArtifact(fixture)).toThrow('invalid_response')
  })

  it('refuses forged finite check counts, witnesses, huge integers, and executable expressions', () => {
    for (const changes of [{ applicable_cases: 3 }, { witness: { n: 2 } }, { total_assignments: 3 }, { lhs: (2n ** 256n).toString() }, { lhs: '-0' }]) {
      const fixture = formalised()
      Object.assign(((fixture.payload as Record<string, unknown>).checks as Record<string, unknown>[])[0], changes)
      expect(() => parseArtifact(fixture)).toThrow('invalid_response')
    }
    const fixture = formalised()
    const draft = (fixture.payload as Record<string, unknown>).draft as Record<string, unknown>
    draft.finite_conjecture = { ...encoded(), conclusion: { left: { op: 'eval', code: 'arbitrary instructions' }, operator: 'eq', right: { op: 'constant', value: 0 } } }
    expect(() => parseArtifact(fixture)).toThrow('invalid_response')
  })

  it('retains honest non-ready outcomes without a fabricated preview', () => {
    const fixture = saved()
    const changed = { ...fixture, status: 'insufficient_evidence', payload: { ...fixture.payload, status: 'insufficient_evidence', draft: null, reason: 'Source passages do not support a useful document.' } }
    expect(parseArtifact(changed).payload.draft).toBeNull()
    expect(() => parseArtifact({ ...changed, payload: { ...changed.payload, reason: null } })).toThrow('invalid_response')
  })
})

describe('owned artifact requests', () => {
  it('uses the existing cookie/CSRF boundary without creating another browser session', async () => {
    const fetch = vi.fn(async () => Response.json(job('write_paper')))
    vi.stubGlobal('fetch', fetch)
    await createArtifactJob('write_paper', SOURCE, HASH, undefined, FORMALISATION)
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(fetch.mock.calls[0]).toEqual(['/api/v1/artifacts/jobs', expect.objectContaining({ credentials: 'same-origin', cache: 'no-store', redirect: 'error', method: 'POST', headers: { Accept: 'application/json', 'X-CSRF-Token': HASH, 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: 'write_paper', source_result_id: SOURCE, formalisation_id: FORMALISATION }) })])
  })

  it('rejects invalid write/export inputs before sending a request', async () => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    await expect(createArtifactJob('write_summary', '../source', HASH)).rejects.toThrow('invalid_request')
    await expect(createArtifactJob('formalise', SOURCE, HASH, undefined, FORMALISATION)).rejects.toThrow('invalid_request')
    await expect(createArtifactJob('write_summary', SOURCE, 'bad-csrf')).rejects.toThrow('session_changed')
    expect(() => exportUrl('../private')).toThrow('invalid_request')
    expect(exportUrl(ARTIFACT)).toBe(`/api/v1/artifacts/${ARTIFACT}/export`)
    expect(fetch).not.toHaveBeenCalled()
  })

  it('binds fetched job, artifact and cancellation records to their requested identifiers', async () => {
    const fetch = vi.fn(async () => Response.json({ ...job(), id: SOURCE_RUN }))
    vi.stubGlobal('fetch', fetch)
    await expect(getArtifactJob(RUN)).rejects.toThrow('invalid_response')
    await expect(cancelArtifactJob(RUN, HASH)).rejects.toThrow('invalid_response')
    fetch.mockImplementation(async () => Response.json({ ...saved(), id: FORMALISATION }))
    await expect(getArtifact(ARTIFACT)).rejects.toThrow('invalid_response')
    fetch.mockImplementation(async () => Response.json(job('formalise')))
    await expect(createArtifactJob('write_summary', SOURCE, HASH)).rejects.toThrow('invalid_response')
  })

  it('filters paginated history by the selected immutable source result', async () => {
    const fetch = vi.fn(async () => Response.json({ jobs: [job()], next_cursor: null }))
    vi.stubGlobal('fetch', fetch)
    expect((await listArtifactJobs(SOURCE, undefined, RUN)).jobs[0].id).toBe(RUN)
    expect(fetch.mock.calls[0]).toEqual([`/api/v1/artifacts/jobs?source_result_id=${SOURCE}&before=${RUN}`, expect.objectContaining({ method: 'GET', credentials: 'same-origin', cache: 'no-store' })])
  })

  it.each([[401, 'session_expired'], [403, 'session_changed'], [404, 'run_unavailable'], [422, 'invalid_request'], [429, 'queue_limit'], [503, 'unavailable']] as const)('sanitizes HTTP %s errors', async (status, expected) => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ private_detail: 'Never show this raw server body.' }, { status })))
    await expect(getArtifactJob(RUN)).rejects.toThrow(expected)
  })

  it('rejects oversized or invalid JSON responses', async () => {
    const fetch = vi.fn(async () => new Response(new Uint8Array(2 * 1024 * 1024 + 1)))
    vi.stubGlobal('fetch', fetch)
    await expect(getArtifactJob(RUN)).rejects.toThrow('invalid_response')
    fetch.mockImplementation(async () => new Response('{malformed'))
    await expect(getArtifactJob(RUN)).rejects.toThrow('invalid_response')
  })
})
