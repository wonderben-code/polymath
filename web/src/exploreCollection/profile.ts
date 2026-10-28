import { parseNativeTaskProfile } from '../budget/activationClient'
import type { NativeTaskProfile } from '../budget/activationTypes'
import { equal, hash, integer, invalid, object } from './primitives'
import { parseExploreCollectionIntent } from './parsers'
import type { ExploreCollectionIntent } from './types'
export interface ExploreCollectionProfile extends NativeTaskProfile { task_kind: 'research'; collection_protocol: 'polymath-explore-collection-v1'; max_bridges: number }
export interface ExploreCollectionProfileBinding { profile: ExploreCollectionProfile; profile_sha256: string }
export function parseExploreCollectionProfileBinding(value: unknown, ordinary?: NativeTaskProfile): ExploreCollectionProfileBinding {
  const binding = object(value, ['profile', 'profile_sha256'])
  const item = object(binding.profile, ['task_kind', 'controller_protocol', 'controller_sha256', 'request_protocol', 'max_model_calls', 'max_actions', 'max_repairs', 'max_output_tokens', 'max_context_characters', 'request_timeout_seconds', 'model_timeout_seconds', 'tool_timeout_seconds', 'max_sources', 'collection_protocol', 'max_bridges'])
  const { collection_protocol: protocol, max_bridges: maximum, ...raw } = item
  if (protocol !== 'polymath-explore-collection-v1' || item.controller_protocol !== 'polymath-meta-explore-collection-v1') return invalid()
  let base: NativeTaskProfile; try { base = parseNativeTaskProfile(raw) } catch { return invalid() }
  if (base.task_kind !== 'research') return invalid()
  if (ordinary) {
    const { controller_protocol: _oldProtocol, controller_sha256: _oldHash, ...oldLimits } = ordinary
    const { controller_protocol: _newProtocol, controller_sha256: _newHash, ...newLimits } = base
    if (!equal(oldLimits, newLimits)) return invalid()
  }
  return { profile: { ...base, task_kind: 'research', collection_protocol: 'polymath-explore-collection-v1', max_bridges: integer(maximum, 1, 4) }, profile_sha256: hash(binding.profile_sha256) }
}
/** Approval metadata stays omitted in historical records. This is not a feature flag that upgrades them. */
export function parseOptionalExploreProfile(value: unknown, ordinary?: NativeTaskProfile): ExploreCollectionProfileBinding | undefined { return value === undefined ? undefined : parseExploreCollectionProfileBinding(value, ordinary) }
export function parseOperatorExploreMarker(value: unknown): ExploreCollectionIntent | undefined { return value === undefined ? undefined : parseExploreCollectionIntent(value) }
