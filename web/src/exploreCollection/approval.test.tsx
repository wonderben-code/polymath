import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ActivationWorkspace } from '../budget/activationWorkspace'
import { PublicActivationWorkspace } from '../budget/publicActivationWorkspace'
import { parseCurrentResearchActivation, parseMetaResearchManifest } from '../budget/activationClient'
import { researchManifest, activationProposal, researchReview, READINESS_ID, RECEIPT_ID, activationCapabilities } from '../budget/activationFixtures'
import { publicCapabilities, publicManifest, publicPreviewRequest } from '../budget/publicActivationFixtures.test-support'
import { account, record, ACCOUNT_ID, BROWSER_CSRF, OPERATOR_CSRF, DATE, sha256 } from '../budget/test-support'
import { completedJob, attestation } from '../budget/verificationFixtures'
import type { MetaResearchManifest } from '../budget/activationTypes'
function extension(manifest: MetaResearchManifest, maximum = 2) { return { profile: { ...manifest.task_profiles[0].profile, task_kind: 'research' as const, controller_protocol: 'polymath-meta-explore-collection-v1', collection_protocol: 'polymath-explore-collection-v1' as const, max_bridges: maximum }, profile_sha256: 'd'.repeat(64) } }
beforeEach(() => vi.spyOn(Date, 'now').mockReturnValue(Date.parse(DATE)))
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })
describe('fresh exact optional operator Explore scope', () => {
  it.each([false, true])('requires an unchecked fresh review and clears stale preview when the ceiling changes (public=%s)', async (isPublic) => {
    const writes: { path: string; body: Record<string, unknown>; headers?: HeadersInit }[] = [], base = isPublic ? '/api/v1/budget/research/public' : '/api/v1/budget/research'
    const fetch = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
      const path = String(input)
      if (options?.method === 'POST') writes.push({ path, body: JSON.parse(String(options.body)), headers: options.headers })
      if (path === `${base}/capabilities`) return Response.json(isPublic ? publicCapabilities() : activationCapabilities())
      if (path.includes('/readiness?')) return Response.json({ proposals: [record(activationProposal())], next_cursor: null })
      if (path.includes('/verification/jobs?')) return Response.json({ jobs: [completedJob()], next_cursor: null })
      if (path.includes('/activations?')) return Response.json({ activations: [], next_cursor: null })
      if (path === `${base}/preview`) { const body = JSON.parse(String(options?.body)), manifest = isPublic ? publicManifest() : researchManifest(); if (body.explore_collection) manifest.explore_collection_profile = extension(researchManifest(), body.explore_collection.max_bridges); return Response.json({ manifest, manifest_sha256: sha256(JSON.stringify(manifest)), research_execution_available: false }) }
      if (path === `/api/v1/budget/accounts/${ACCOUNT_ID}`) return Response.json(account())
      throw new Error(`Unexpected optional approval fixture route: ${path}`)
    }); vi.stubGlobal('fetch', fetch)
    const props = { csrfToken: BROWSER_CSRF, operatorCsrf: OPERATOR_CSRF, authenticated: true, onBrowserExpired: vi.fn(), onOperatorExpired: vi.fn() }
    render(isPublic ? <PublicActivationWorkspace {...props} /> : <ActivationWorkspace {...props} />)
    await screen.findByText(isPublic ? 'No public funding delegation is saved in this authorized scope.' : 'No research activation has been saved in this authorized scope.')
    const optional = screen.getByRole('checkbox', { name: 'Include Meta-ranked multi-bridge Explore in this freshly reviewed approval.' }); expect(optional).not.toBeChecked(); expect(screen.queryByLabelText('Maximum bridges per seeded Explore')).toBeNull(); expect(writes).toHaveLength(0)
    fireEvent.change(screen.getByLabelText('Exact saved readiness attestation'), { target: { value: READINESS_ID } }); fireEvent.change(screen.getByLabelText(isPublic ? 'Exact passed protocol receipt' : 'Exact passed native protocol receipt'), { target: { value: RECEIPT_ID } })
    for (const checkbox of within(screen.getByRole('group', { name: isPublic ? 'Fresh human review' : 'Fresh human review required' })).getAllByRole('checkbox')) fireEvent.click(checkbox)
    if (isPublic) { const body = publicPreviewRequest(); fireEvent.change(screen.getByLabelText('Exact public funding deadline in UTC'), { target: { value: body.valid_until } }); fireEvent.change(screen.getByLabelText('Reviewed public funding JSON'), { target: { value: JSON.stringify({ review: body.review, provider_account_attestation: body.provider_account_attestation, public_limits: body.public_limits }) } }) }
    else { fireEvent.change(screen.getByLabelText('Fresh review dates and evidence JSON'), { target: { value: JSON.stringify(researchReview()) } }); fireEvent.change(screen.getByLabelText('Reviewed research provider-account attestation JSON'), { target: { value: JSON.stringify(attestation()) } }) }
    fireEvent.click(optional); fireEvent.change(screen.getByLabelText('Maximum bridges per seeded Explore'), { target: { value: '2' } }); fireEvent.click(screen.getByRole('button', { name: isPublic ? 'Preview exact public funding' : 'Preview exact research activation' }))
    const preview = await screen.findByRole('region', { name: isPublic ? 'Exact public funding approval' : 'Research activation approval review' }); await waitFor(() => expect(within(preview).getByRole('checkbox')).toBeEnabled())
    expect(within(preview).getByRole('heading', { name: 'Multi-bridge Explore · explicitly included' })).toBeVisible(); expect(preview).toHaveTextContent('20 / 16 / 2')
    expect(writes).toHaveLength(1); expect(writes[0].body.explore_collection).toEqual({ protocol: 'polymath-explore-collection-v1', max_bridges: 2 }); expect(writes[0].headers).toMatchObject({ 'X-CSRF-Token': BROWSER_CSRF, 'X-Operator-CSRF-Token': OPERATOR_CSRF })
    const activate = screen.getByRole('button', { name: isPublic ? 'Activate THIS public funding delegation' : 'Activate research for THIS operator and browser' }); expect(activate).toBeDisabled(); fireEvent.click(within(preview).getByRole('checkbox')); expect(activate).toBeEnabled()
    fireEvent.change(screen.getByLabelText('Maximum bridges per seeded Explore'), { target: { value: '4' } }); expect(screen.queryByRole('region', { name: isPublic ? 'Exact public funding approval' : 'Research activation approval review' })).toBeNull(); expect(writes).toHaveLength(1)
  })
  it('preserves original five profile bytes and historical absent fields, and rejects broadened current metadata', () => {
    const original = researchManifest(), full = { ...original, explore_collection_profile: extension(original) }, parsed = parseMetaResearchManifest(full)
    expect(parsed.task_profiles).toEqual(original.task_profiles); expect(parseMetaResearchManifest(original)).not.toHaveProperty('explore_collection_profile')
    const value = { activation: { id: READINESS_ID, manifest_sha256: 'a'.repeat(64), audience: 'operator_browser', expires_at: original.valid_until, profiles: original.task_profiles.map((item) => item.profile), research_execution_available: true, explore_collection_profile: full.explore_collection_profile }, reason: null }
    expect(parseCurrentResearchActivation(value).activation?.explore_collection_profile).toEqual(full.explore_collection_profile)
    expect(() => parseCurrentResearchActivation({ ...value, activation: { ...value.activation, explore_collection_profile: { ...full.explore_collection_profile, profile: { ...full.explore_collection_profile.profile, max_model_calls: 21 } } } })).toThrow('invalid_response')
    expect(() => parseMetaResearchManifest({ ...original, explore_collection_profile: null })).toThrow('invalid_response')
  })
})
