import type { DiscoveryIntent } from '../discovery/types'
import type { NativeDiscoveryProfile, NativeDiscoveryProfileBinding } from './activationTypes'
import { activationPreview, currentActivation, researchActivation, researchManifest, taskProfile } from './activationFixtures'
import { sha256 } from './test-support'
function canonical(value: unknown, key = ''): string { if (Array.isArray(value)) return `[${value.map((item) => canonical(item)).join(',')}]`; if (value && typeof value === 'object') return `{${Object.entries(value).sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0).map(([name, item]) => `${JSON.stringify(name)}:${canonical(item, name)}`).join(',')}}`; return typeof value === 'number' && key.endsWith('_seconds') && Number.isInteger(value) ? value.toFixed(1) : JSON.stringify(value) }
export function discoveryProfile(intent: DiscoveryIntent = { protocol: 'polymath-surprise-ladder-v1', max_stages: 3, max_depth: 2 }): NativeDiscoveryProfileBinding {
  const profile: NativeDiscoveryProfile = { ...taskProfile('research'), discovery_protocol: intent.protocol, controller_protocol: 'polymath-meta-discovery-controller-v1', controller_sha256: sha256('Synthetic discovery ladder controller'), task_kind: 'research', max_actions: 20, tool_timeout_seconds: 20, max_stages: intent.max_stages, max_depth: intent.max_depth }
  return { profile, profile_sha256: sha256(canonical(profile)) }
}
export function discoveryManifest(intent?: DiscoveryIntent) { return researchManifest({ discovery_ladder_profile: discoveryProfile(intent) }) }
export function discoveryActivationPreview(intent?: DiscoveryIntent) { return activationPreview(discoveryManifest(intent)) }
export function discoveryActivation(intent?: DiscoveryIntent) { return researchActivation({}, discoveryManifest(intent)) }
export function discoveryCurrent(intent?: DiscoveryIntent) { const record = discoveryActivation(intent), current = currentActivation(record); return { ...current, activation: { ...current.activation!, discovery_ladder_profile: record.manifest.discovery_ladder_profile! } } }
