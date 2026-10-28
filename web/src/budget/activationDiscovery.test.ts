import { afterEach, describe, expect, it, vi } from 'vitest'
import { activateResearch, parseCurrentResearchActivation, parseMetaResearchManifest, previewResearchActivation } from './activationClient'
import { activationPreview, activationPreviewRequest, BROWSER_CSRF, OPERATOR_CSRF, currentActivation } from './activationFixtures'
import { discoveryActivation, discoveryActivationPreview, discoveryCurrent, discoveryManifest } from './activationDiscoveryFixtures'
import { createResearchRun, parseRun } from '../research/client'
import type { OwnedRun } from '../research/client'
import { intent, RUN } from '../discovery/test-support'
const created = (discovery = false): OwnedRun => ({ id: RUN, mode: 'surprise', task_kind: 'research', goal: '', status: 'queued', created_at: '2026-10-28T00:00:00Z', updated_at: '2026-10-28T00:00:00Z', result_id: null, error_code: null, external_outcome_unknown: false, ...(discovery ? { discovery_intent: intent() } : {}) })
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })
describe('optional explicit discovery authority', () => {
  it('preserves old manifest and current projection without adding an absent extension', () => {
    const old = activationPreview().manifest, parsed = parseMetaResearchManifest(old)
    expect(parsed).toEqual(old); expect(Object.hasOwn(parsed, 'discovery_ladder_profile')).toBe(false)
    expect(parseCurrentResearchActivation(currentActivation())).toEqual(currentActivation())
    expect(JSON.stringify(parsed)).not.toContain('discovery_ladder_profile')
  })
  it('retains the exact separate ladder profile while keeping the original five task profiles unchanged', () => {
    const manifest = discoveryManifest(), parsed = parseMetaResearchManifest(manifest)
    expect(parsed.discovery_ladder_profile).toEqual(manifest.discovery_ladder_profile)
    expect(parsed.task_profiles).toEqual(activationPreview().manifest.task_profiles)
    expect(parsed.task_profiles[0].profile.max_actions).toBe(16)
    expect(parsed.discovery_ladder_profile!.profile.max_actions).toBe(20)
    expect(parseCurrentResearchActivation(discoveryCurrent())).toEqual(discoveryCurrent())
  })
  it.each(['null', 'ordinary_actions', 'too_deep', 'inflated_output', 'extra'])('rejects a malformed optional extension: %s', (change) => {
    const manifest = discoveryManifest() as unknown as Record<string, any>
    if (change === 'null') manifest.discovery_ladder_profile = null
    else if (change === 'ordinary_actions') manifest.discovery_ladder_profile.profile.max_actions = 16
    else if (change === 'too_deep') manifest.discovery_ladder_profile.profile.max_depth = 4
    else if (change === 'inflated_output') manifest.discovery_ladder_profile.profile.max_output_tokens = 4095
    else manifest.discovery_ladder_profile.unreviewed = true
    expect(() => parseMetaResearchManifest(manifest)).toThrow('invalid_response')
  })
  it('requires an explicitly requested fresh extension and pins its exact stage/depth limits', async () => {
    const fetch = vi.fn(async (_path: RequestInfo | URL, _options?: RequestInit) => Response.json(discoveryActivationPreview())); vi.stubGlobal('fetch', fetch)
    await expect(previewResearchActivation({ ...activationPreviewRequest(), discovery: intent() }, BROWSER_CSRF, OPERATOR_CSRF)).resolves.toEqual(discoveryActivationPreview())
    expect(JSON.parse(fetch.mock.calls[0][1]?.body?.toString() ?? '{}')).toMatchObject({ discovery: intent() })
    await expect(previewResearchActivation(activationPreviewRequest(), BROWSER_CSRF, OPERATOR_CSRF)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('posts only the exact newly reviewed extension manifest and hash on activation', async () => {
    const fetch = vi.fn(async (_path: RequestInfo | URL, _options?: RequestInit) => Response.json(discoveryActivation())); vi.stubGlobal('fetch', fetch)
    const preview = discoveryActivationPreview(); await activateResearch({ manifest: preview.manifest, expected_manifest_sha256: preview.manifest_sha256 }, BROWSER_CSRF, OPERATOR_CSRF)
    expect(JSON.parse(fetch.mock.calls[0][1]?.body?.toString() ?? '{}')).toEqual({ manifest: preview.manifest, expected_manifest_sha256: preview.manifest_sha256 })
  })
  it('pins opt-in Surprise input exactly and omits it from every ordinary submission', async () => {
    const fetch = vi.fn(async (_path: RequestInfo | URL, options?: RequestInit) => Response.json(created(Object.hasOwn(JSON.parse(options!.body!.toString()), 'discovery_intent')))); vi.stubGlobal('fetch', fetch)
    const native = { operatorCsrfToken: OPERATOR_CSRF, expectedActivationSha256: discoveryActivationPreview().manifest_sha256 }
    await createResearchRun('surprise', '', BROWSER_CSRF, undefined, [], native, intent())
    await createResearchRun('surprise', '', BROWSER_CSRF, undefined, [], native)
    expect(JSON.parse(fetch.mock.calls[0][1]!.body!.toString())).toEqual({ mode: 'surprise', goal: '', expected_activation_sha256: native.expectedActivationSha256, discovery_intent: intent() })
    expect(JSON.parse(fetch.mock.calls[1][1]!.body!.toString())).not.toHaveProperty('discovery_intent')
    await expect(createResearchRun('explore', 'Question', BROWSER_CSRF, undefined, [], native, intent())).rejects.toMatchObject({ code: 'invalid_request' })
    expect(fetch).toHaveBeenCalledTimes(2)
  })
  it('refuses a silently upgraded or downgraded enqueue response and keeps run metadata exact on reload', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(created(true))))
    await expect(createResearchRun('surprise', '', BROWSER_CSRF)).rejects.toMatchObject({ code: 'invalid_response' })
    expect(parseRun(created(true)).discovery_intent).toEqual(intent())
    expect(() => parseRun({ ...created(true), mode: 'explore' })).toThrow('invalid_response')
  })
})
