import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ArtifactWorkspace } from './ArtifactWorkspace'
import type { ArtifactJob, SavedArtifact } from './types'
import { artifactFixture } from '../publications/test-fixtures'
import { BIBLIOGRAPHY } from '../research/bibliography.test-support'

const SNAPSHOT = '99999999-9999-4999-8999-999999999999'
const SNAPSHOT_HASH = 'd'.repeat(64), WINDOW_HASH = 'b'.repeat(64)
const QUOTE = '[equation unavailable] <math><mi>x</mi></math> \\alpha <img src="untrusted" onerror="alert(1)">'
function coveredArtifact(): SavedArtifact {
  const saved = artifactFixture()
  saved.passages = [100, 800].map((start, ordinal) => ({ ordinal, source_id: `arxiv-html:${SNAPSHOT}:${start}:${start + 200}`, title: `Synthetic extracted window ${ordinal + 1}`, url: 'https://arxiv.org/html/2610.00001v2', content_sha256: ordinal ? 'c'.repeat(64) : WINDOW_HASH, content_kind: 'full_text', quote: ordinal ? 'Another synthetic passage from the same paper.' : QUOTE, supports: 'Synthetic interpretation; no scientific verification.', claim_id: null, bibliography: BIBLIOGRAPHY, coverage: { snapshot_id: SNAPSHOT, snapshot_sha256: SNAPSHOT_HASH, arxiv_id: BIBLIOGRAPHY.arxiv_id, arxiv_version: 2, start, end: start + 200, total: 3000 } }))
  return saved
}
function server(saved: SavedArtifact) {
  const job: ArtifactJob = { id: saved.run_id, mode: 'explore', task_kind: saved.kind, goal: 'Synthetic saved document.', status: 'succeeded', created_at: saved.created_at, updated_at: saved.created_at, result_id: null, artifact_id: saved.id, error_code: null, external_outcome_unknown: false }
  const fetch = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit): Promise<Response> => {
    const path = input.toString()
    if (path.startsWith('/api/v1/artifacts/jobs?')) return Response.json({ jobs: [job], next_cursor: null })
    if (path === `/api/v1/artifacts/jobs/${saved.run_id}`) return Response.json(job)
    if (path === `/api/v1/artifacts/${saved.id}`) return Response.json(saved)
    if (path === '/api/v1/publications/capabilities') return Response.json({ configured: false, publisher: null, destinations: [], can_prepare: false })
    if (path === '/api/v1/operator/session') return Response.json({ configured: false, authenticated: false, actor_id: null, display_name: null, expires_at: null, csrf_token: null })
    if (path.startsWith('/api/v1/publications?')) return Response.json({ manifests: [], next_cursor: null })
    throw new Error('Unexpected synthetic saved-artifact request')
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}
function workspace(saved: SavedArtifact) { return render(<ArtifactWorkspace sourceResultId={saved.source_result_id} sourceKind="bridge" csrfToken={'a'.repeat(64)} enabled canGenerate={false} sourceCurrent={false} onSessionExpired={vi.fn()} />) }
afterEach(() => vi.unstubAllGlobals())

describe('saved document full-text excerpt provenance', () => {
  it('shows exact ranges, versioned paper and distinct snapshot/window hashes without rendering extracted markup', async () => {
    const saved = coveredArtifact(), fetch = server(saved), rendered = workspace(saved)
    await screen.findByRole('article', { name: 'Saved document preview' })
    const passages = screen.getByRole('list', { name: 'Document source passages' }), rows = within(passages).getAllByRole('listitem')
    expect(screen.getByText(/2 saved passages · 1 paper identity/)).toHaveTextContent('do not provide independent corroboration')
    expect(within(rows[0]).getAllByText('Full text excerpt')).toHaveLength(2)
    const coverage = within(rows[0]).getByLabelText('Saved full-text excerpt coverage')
    expect(coverage).toHaveTextContent(`saved characters 100–300 of ${(3000).toLocaleString()} extracted characters`)
    expect(coverage).toHaveTextContent('end excluded')
    expect(within(coverage).getByRole('link', { name: 'arXiv 2610.00001v2 · original versioned paper' })).toHaveAttribute('href', 'https://arxiv.org/html/2610.00001v2')
    expect(coverage).toHaveTextContent('Figures, assets, and non-article navigation are omitted')
    expect(coverage).toHaveTextContent('Table layout may be lost')
    expect(coverage).toHaveTextContent('TeX are extracted as plain text; their semantics are unverified')
    expect(within(rows[0]).getByText(QUOTE, { selector: 'blockquote' })).toBeVisible()
    expect(rendered.container.querySelector('math, img, script')).toBeNull()
    await userEvent.click(within(rows[0]).getByText('Full-text extraction version'))
    expect(within(rows[0]).getByText('Extracted snapshot SHA-256')).toBeVisible()
    expect(within(rows[0]).getByText(SNAPSHOT_HASH)).toBeVisible()
    await userEvent.click(within(rows[0]).getByText('Saved source version'))
    expect(within(rows[0]).getByText('Excerpt content hash')).toBeVisible()
    expect(within(rows[0]).getByText(WINDOW_HASH)).toBeVisible()
    expect(screen.getByRole('link', { name: /Download saved Markdown/ })).toHaveAttribute('href', `/api/v1/artifacts/${saved.id}/export`)
    expect(fetch.mock.calls.every(([, init]) => init?.method === 'GET')).toBe(true)
  })

  it('groups an original abstract and extracted window as one paper without claiming independent support', async () => {
    const saved = coveredArtifact()
    saved.passages[1] = { ...saved.passages[1], source_id: 'arxiv:2610.00001v2', title: 'Synthetic original abstract', url: 'https://arxiv.org/abs/2610.00001v2', content_kind: 'abstract', coverage: undefined }
    server(saved); workspace(saved)
    await screen.findByRole('article', { name: 'Saved document preview' })
    expect(screen.getByText(/2 saved passages · 1 paper identity/)).toHaveTextContent('do not provide independent corroboration')
    const rows = within(screen.getByRole('list', { name: 'Document source passages' })).getAllByRole('listitem')
    expect(within(rows[1]).getByText('Abstract only')).toBeVisible()
    expect(screen.getAllByLabelText('Saved full-text excerpt coverage')).toHaveLength(1)
  })

  it('keeps legacy full-text snapshots readable while explicitly leaving their reading range unspecified', async () => {
    const saved = artifactFixture(); saved.passages[0].content_kind = 'full_text'
    server(saved); workspace(saved)
    await screen.findByRole('article', { name: 'Saved document preview' })
    expect(screen.getByText(saved.passages[0].quote, { selector: 'blockquote' })).toBeVisible()
    expect(screen.getByText('The saved record does not specify this full-text snapshot’s reading range.')).toBeVisible()
    expect(screen.queryByLabelText('Saved full-text excerpt coverage')).not.toBeInTheDocument()
    expect(screen.queryByText(/saved characters/)).not.toBeInTheDocument()
  })
})
