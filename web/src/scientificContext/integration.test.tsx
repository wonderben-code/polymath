import { webcrypto } from 'node:crypto'
import { act, render, renderHook, screen, waitFor, within } from '@testing-library/react'
import { createElement } from 'react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useNativeResearchContext } from '../research/useNativeResearchContext'
import { NativeResearchProvider } from '../research/nativeContext'
import { useResearchWorkspace } from '../research/useResearchWorkspace'
import { useArtifactWorkspace } from '../artifacts/useArtifactWorkspace'
import { getArtifact, parseArtifactWithContext } from '../artifacts/client'
import { parseResult, createResearchRun } from '../research/client'
import { parseMetaResearchManifest, parseScientificContextProfiles } from '../budget/activationClient'
import { publicAccess, privateAccess } from '../research/accessFixtures.test-support'
import { operatorFixture } from '../publications/test-fixtures'
import { currentActivation } from '../budget/activationFixtures'
import { BROWSER_CSRF, DATE } from '../budget/test-support'
import { ClaimBoundOverview } from './ClaimBoundOverview'
import { PinnedScientificContext } from './PinnedScientificContext'
import { pythonScientificManifest } from './profile-fixtures.test-support'
import { pythonFixtures } from './python-fixtures.test-support'
import type { ArtifactScientificContextBinding, AskOverviewBinding } from './types'
import type { AnswerFinding, ResearchResult } from '../research/types'

const HASH = publicAccess().expected_activation_sha256!, RUN = '11111111-1111-4111-8111-111111111111'
const capabilities = ['artifact_scientific_context_v1', 'ask_claim_overview_v1', 'ask_reassessment_overview_v1']
const protocols = (hash = HASH) => ({ protocol: 'polymath-research-protocols-v1', activation_sha256: hash, capabilities })
const settings = () => ({ enabled: true, csrfToken: BROWSER_CSRF as string | null, required: true, available: true, onBrowserExpired: vi.fn() })
function run(mode = 'ask') { return { id: RUN, mode, task_kind: 'research', goal: 'Synthetic explicit question?', status: 'queued', created_at: DATE, updated_at: DATE, result_id: null, error_code: null, external_outcome_unknown: false } }
function answer() { const result = structuredClone(pythonFixtures.answer) as ResearchResult; const finding = result.finding as AnswerFinding; const citation = finding.claims[0].evidence[0]; result.sources = [{ source_id: citation.source_id, title: 'Synthetic exact source', url: 'https://example.org/fixture', text: citation.quote, content_kind: 'abstract', retrieved_at: DATE, extraction_method: 'fixture' }]; result.quotes = finding.claims.map((claim) => ({ ...claim.evidence[0], side: 'answer', claim_id: claim.id, start: 0, end: Array.from(citation.quote).length, source_sha256: 'a'.repeat(64), content_kind: 'abstract' })); return result }
function artifact(binding?: ArtifactScientificContextBinding) { const reference = (binding ?? pythonFixtures.original as ArtifactScientificContextBinding).context.source_reference; return { id: RUN, run_id: RUN, kind: 'write_summary', status: 'insufficient_evidence', created_at: DATE, source_result_id: reference.result_id, source_run_id: reference.run_id, source_sha256: reference.content_sha256, formalisation_id: null, formalisation_checks: [], passages: [{ ordinal: 0, source_id: 'fixture:left', title: 'Synthetic evidence', url: 'https://example.org/fixture', content_sha256: 'a'.repeat(64), content_kind: 'abstract', quote: 'An exact synthetic source quotation.', supports: 'Synthetic support.', claim_id: null }], payload: { kind: 'write_summary', status: 'insufficient_evidence', draft: null, checks: [], events: [], usage: { model_calls: 0, input_tokens_reported: 0, output_tokens_reported: 0, calls_without_usage: 0 }, error_code: null, reason: 'Synthetic insufficient evidence.', validation_scope: 'citation_integrity_and_bounded_checks' }, markdown: '# Exact saved bytes\n', markdown_sha256: 'a'.repeat(64), payload_sha256: 'b'.repeat(64), basis_sha256: 'c'.repeat(64), ...(binding ? { scientific_context: binding } : {}) } }
beforeEach(() => { vi.stubGlobal('crypto', webcrypto); vi.spyOn(Date, 'now').mockReturnValue(Date.parse(DATE)) })
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('same-authority scientific capability discovery', () => {
  it('uses one public GET per funding hash, never operator requests, and does not gate original Resume', async () => {
    vi.useFakeTimers(); vi.spyOn(Date, 'now').mockRestore(); vi.setSystemTime(new Date(DATE))
    const fetch = vi.fn(async (path: RequestInfo | URL) => Response.json(String(path).endsWith('/protocols') ? protocols() : publicAccess({ remaining_session_jobs_today: 0 }))); vi.stubGlobal('fetch', fetch)
    const { result } = renderHook(() => useNativeResearchContext(settings()))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(result.current.scientificCapabilities).toEqual(capabilities); expect(result.current.canSubmit).toBe(false); expect(result.current.canResume).toBe(true)
    await act(async () => { await vi.advanceTimersByTimeAsync(40000) })
    expect(fetch.mock.calls.filter(([path]) => String(path).endsWith('/protocols'))).toHaveLength(1)
    expect(fetch.mock.calls.every(([path]) => ['/api/v1/research/access', '/api/v1/research/protocols'].includes(String(path)))).toBe(true)
  })
  it('refuses stale hash capabilities without losing browser ownership or base funding', async () => {
    vi.stubGlobal('fetch', vi.fn(async (path: RequestInfo | URL) => Response.json(String(path).endsWith('/protocols') ? protocols('d'.repeat(64)) : publicAccess())))
    const props = settings(), { result } = renderHook(() => useNativeResearchContext(props)); await waitFor(() => expect(result.current.scientificPhase).toBe('error'))
    expect(result.current.scientificCapabilities).toEqual([]); expect(result.current.canResume).toBe(true); expect(props.onBrowserExpired).not.toHaveBeenCalled()
  })
  it('keeps variant invalidation scoped to its hash and restores it only on explicit access refresh', async () => {
    vi.stubGlobal('fetch', vi.fn(async (path: RequestInfo | URL) => Response.json(String(path).endsWith('/protocols') ? protocols() : publicAccess())))
    const { result } = renderHook(() => useNativeResearchContext(settings())); await waitFor(() => expect(result.current.scientificPhase).toBe('ready'))
    act(() => result.current.invalidateScientific?.('d'.repeat(64))); expect(result.current.scientificPhase).toBe('ready')
    act(() => result.current.invalidateScientific?.(HASH)); expect(result.current.scientificPhase).toBe('error'); expect(result.current.canResume).toBe(true)
    act(() => result.current.refresh()); await waitFor(() => expect(result.current.scientificPhase).toBe('ready'))
  })
  it('honors protocol-read Retry-After without a private lookup, new task or early automatic read', async () => {
    vi.useFakeTimers(); vi.spyOn(Date, 'now').mockRestore(); vi.setSystemTime(new Date(DATE))
    const fetch = vi.fn(async (path: RequestInfo | URL) => String(path).endsWith('/protocols') ? Response.json({ detail: 'public_request_rate_limited' }, { status: 429, headers: { 'Retry-After': '17' } }) : Response.json(publicAccess())); vi.stubGlobal('fetch', fetch)
    const props = settings(), { result } = renderHook(() => useNativeResearchContext(props)); await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(result.current.scientificPhase).toBe('error'); expect(result.current.retryAfterSeconds).toBe(17); expect(result.current.canResume).toBe(true)
    act(() => result.current.refresh()); await act(async () => { await vi.advanceTimersByTimeAsync(16000) }); expect(fetch).toHaveBeenCalledTimes(2)
    await act(async () => { await vi.advanceTimersByTimeAsync(1000) }); expect(fetch).toHaveBeenCalledTimes(3); expect(props.onBrowserExpired).not.toHaveBeenCalled()
  })
  it('clears private scope on protocol browser expiry, and aborts stale capability reads on owner rotation', async () => {
    const props = settings(); vi.stubGlobal('fetch', vi.fn(async (path: RequestInfo | URL) => String(path).endsWith('/protocols') ? Response.json({ detail: 'research_session_required' }, { status: 401 }) : Response.json(publicAccess())))
    const first = renderHook(() => useNativeResearchContext(props)); await waitFor(() => expect(props.onBrowserExpired).toHaveBeenCalledOnce()); expect(first.result.current.scientificCapabilities).toEqual([]); first.unmount()
    let resolve!: (value: Response) => void; let oldSignal: AbortSignal | undefined; let reads = 0
    vi.stubGlobal('fetch', vi.fn(async (path: RequestInfo | URL, options?: RequestInit) => String(path).endsWith('/protocols') ? ++reads === 1 ? new Promise<Response>((done) => { resolve = done; oldSignal = options?.signal as AbortSignal }) : Response.json(protocols('d'.repeat(64))) : Response.json(publicAccess({ expected_activation_sha256: reads ? 'd'.repeat(64) : HASH }))))
    const second = renderHook((input) => useNativeResearchContext(input), { initialProps: settings() }); await waitFor(() => expect(oldSignal).toBeDefined()); second.rerender({ ...settings(), csrfToken: 'b'.repeat(64) }); await waitFor(() => expect(second.result.current.scientificPhase).toBe('ready')); await act(async () => resolve(Response.json(protocols()))); expect(oldSignal!.aborted).toBe(true); expect(second.result.current.submission!.expectedActivationSha256).toBe('d'.repeat(64))
  })
  it('derives private features solely from the exact privileged extension and leaves old approvals baseline', async () => {
    const current = currentActivation(); current.activation!.profiles = pythonScientificManifest.task_profiles.map((binding) => binding.profile) as import('../budget/activationTypes').NativeTaskProfile[]; current.activation!.scientific_context_profiles = parseScientificContextProfiles(pythonScientificManifest.scientific_context_profiles)
    const fetch = vi.fn(async (path: RequestInfo | URL) => Response.json(String(path).endsWith('/access') ? privateAccess() : String(path).endsWith('/session') ? operatorFixture(true) : current)); vi.stubGlobal('fetch', fetch)
    const { result } = renderHook(() => useNativeResearchContext(settings())); await waitFor(() => expect(result.current.scientificPhase).toBe('ready'))
    expect(result.current.scientificCapabilities).toEqual(capabilities); expect(fetch.mock.calls.some(([path]) => String(path).endsWith('/protocols'))).toBe(false)
  })
})

describe('ordinary explicit task selects the approved variant once', () => {
  it('automatically pins Ask intent and exact public hash without operator headers or fallback', async () => {
    const fetch = vi.fn(async (path: RequestInfo | URL, options?: RequestInit) => {
      if (String(path) === '/api/v1/session') return Response.json({ csrf_token: BROWSER_CSRF, expires_at: '2100-01-01T00:00:00Z' })
      if (String(path).endsWith('/access')) return Response.json(publicAccess())
      if (String(path).endsWith('/protocols')) return Response.json(protocols())
      if (String(path) === '/api/v1/runs' && options?.method === 'POST') return Response.json({ ...run(), ask_overview_intent: { protocol: 'polymath-ask-claim-overview-v1' } })
      if (String(path) === '/api/v1/runs') return Response.json({ runs: [], next_cursor: null })
      return Response.json(run())
    }); vi.stubGlobal('fetch', fetch); vi.stubGlobal('EventSource', class extends EventTarget { close() {} })
    const { result } = renderHook(() => useResearchWorkspace(true, true, true)); await waitFor(() => expect(result.current.canAsk).toBe(true))
    await act(async () => { await result.current.submit('ask', 'Synthetic explicit question?') })
    const posts = fetch.mock.calls.filter(([, options]) => options?.method === 'POST'); expect(posts).toHaveLength(1)
    expect(JSON.parse(String(posts[0][1]?.body))).toEqual({ mode: 'ask', goal: 'Synthetic explicit question?', expected_activation_sha256: HASH, ask_overview_intent: { protocol: 'polymath-ask-claim-overview-v1' } }); expect(posts[0][1]?.headers).not.toHaveProperty('X-Operator-CSRF-Token')
  })
  it('does not fall back after a definitive scientific-feature rejection', async () => {
    const fetch = vi.fn(async () => Response.json({ detail: 'meta_research_scientific_protocol_unavailable' }, { status: 409 })); vi.stubGlobal('fetch', fetch)
    await expect(createResearchRun('ask', 'Synthetic explicit question?', BROWSER_CSRF, undefined, [], { audience: 'public_sessions', expectedActivationSha256: HASH }, undefined, { protocol: 'polymath-ask-claim-overview-v1' })).rejects.toMatchObject({ code: 'scientific_variant_unavailable' }); expect(fetch).toHaveBeenCalledTimes(1)
  })
  it('automatically pins the artifact context variant and never sends technical visitor consent', async () => {
    const binding = pythonFixtures.original as ArtifactScientificContextBinding, context = { required: true, phase: 'ready' as const, activation: null, submission: { audience: 'public_sessions' as const, expectedActivationSha256: HASH }, canSubmit: true, pollPaused: false, reason: null, invalidate: vi.fn(), refresh: vi.fn(), scientificPhase: 'ready' as const, scientificCapabilities: capabilities as ('artifact_scientific_context_v1'|'ask_claim_overview_v1'|'ask_reassessment_overview_v1')[] }
    const wrapper = ({ children }: { children: ReactNode }) => createElement(NativeResearchProvider, { value: context, children })
    const fetch = vi.fn(async (_path: RequestInfo | URL, options?: RequestInit) => Response.json(options?.method === 'POST' ? { ...run('explore'), task_kind: 'write_summary', artifact_id: null, scientific_context_intent: { protocol: 'polymath-artifact-scientific-context-v1' }, scientific_context_sha256: binding.context_sha256 } : { jobs: [], next_cursor: null })); vi.stubGlobal('fetch', fetch)
    const props = { sourceResultId: binding.context.source_reference.result_id, csrfToken: BROWSER_CSRF, enabled: true, canGenerate: true, onSessionExpired: vi.fn() }; const { result } = renderHook(() => useArtifactWorkspace(props), { wrapper }); await waitFor(() => expect(result.current.canCreate).toBe(true))
    await act(async () => { await result.current.create('write_summary') }); const posts = fetch.mock.calls.filter(([, options]) => options?.method === 'POST'); expect(posts).toHaveLength(1); expect(JSON.parse(String(posts[0][1]?.body))).toMatchObject({ scientific_context_intent: { protocol: 'polymath-artifact-scientific-context-v1' }, expected_activation_sha256: HASH }); expect(posts[0][1]?.headers).not.toHaveProperty('X-Operator-CSRF-Token')
  })
})

describe('saved scientific snapshots preserve exact evidence and history', () => {
  it('parses bound Ask output, rejects changed summary/null/wrong admission, and leaves legacy absent', () => {
    const value = answer(); expect(parseResult(value, true).overview_binding).toEqual(value.overview_binding)
    expect(() => parseResult({ ...value, finding: { ...value.finding, summary: 'Synthetic unsupported paraphrase.' } })).toThrow('invalid_response')
    expect(() => parseResult({ ...value, overview_binding: null })).toThrow(); expect(() => parseResult(value, false)).toThrow()
    const { overview_binding: _binding, ...legacy } = value; expect(parseResult(legacy)).not.toHaveProperty('overview_binding')
  })
  it('renders exact selected claims in saved order with original classifications, evidence and caveats', () => {
    const value = answer(), finding = value.finding as AnswerFinding, binding = value.overview_binding as AskOverviewBinding
    finding.claims[1].label = 'speculative'; finding.claims[1].caveats = ['Synthetic caveat <unsafe> remains literal.']; binding.claim_ids.reverse(); finding.summary = binding.claim_ids.map((id) => finding.claims.find((claim) => claim.id === id)!.text).join('\n\n')
    render(<ClaimBoundOverview finding={finding} binding={binding} claimAnchor={(id) => `saved-${id}`} />)
    const overview = screen.getByRole('region', { name: 'Overview linked to saved claims' }); const items = within(overview).getAllByRole('link'); expect(items[0]).toHaveAttribute('href', '#saved-claim_b'); expect(items[1]).toHaveAttribute('href', '#saved-claim_a'); expect(within(overview).getByText('speculative')).toBeInTheDocument(); expect(within(overview).getByText('Synthetic caveat <unsafe> remains literal.')).toBeInTheDocument()
  })
  it('verifies saved context hashes before rendering, cross-binds exact outer source, and retains exact export bytes', async () => {
    const binding = structuredClone(pythonFixtures.revised) as ArtifactScientificContextBinding, value = artifact(binding)
    const parsed = await parseArtifactWithContext(value); expect(parsed.scientific_context).toEqual(binding); expect(parsed.markdown).toBe(value.markdown)
    await expect(parseArtifactWithContext({ ...value, source_sha256: 'd'.repeat(64) })).rejects.toThrow('invalid_response'); await expect(parseArtifactWithContext({ ...value, scientific_context: null })).rejects.toThrow(); await expect(parseArtifactWithContext({ ...value, scientific_context: { ...binding, context_sha256: 'd'.repeat(64) } })).rejects.toThrow()
    render(<PinnedScientificContext binding={parsed.scientific_context!} />); expect(screen.getByText(/confidence and novelty notes belong to the original stage/)).toBeInTheDocument(); expect(screen.getAllByText(/uncalibrated model judgement/).length).toBeGreaterThan(0); expect(screen.getByText(/Applied model assessment/)).toBeInTheDocument()
    const fetch = vi.fn(async () => Response.json(value)); vi.stubGlobal('fetch', fetch); expect((await getArtifact(RUN)).markdown).toBe(value.markdown); expect(fetch).toHaveBeenCalledTimes(1)
  })
  it('accepts genuine pure Python extended profiles without changing the original five', () => {
    const parsed = parseMetaResearchManifest(pythonScientificManifest); expect(parsed.scientific_context_profiles?.bindings).toHaveLength(5)
    const { scientific_context_profiles: _extension, ...old } = pythonScientificManifest; expect(parseMetaResearchManifest(old)).not.toHaveProperty('scientific_context_profiles'); expect(parseMetaResearchManifest(old).task_profiles).toEqual(parsed.task_profiles)
    const changed = structuredClone(pythonScientificManifest); changed.scientific_context_profiles.bindings.reverse(); expect(() => parseMetaResearchManifest(changed)).toThrow()
  })
})
