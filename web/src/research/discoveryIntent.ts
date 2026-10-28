import type { DiscoveryIntent } from '../discovery/types'

/** The original opt-in input, never reconstructed from generated stages. */
export function parseDiscoveryIntent(value: unknown, invalid: () => never): DiscoveryIntent | undefined {
  if (value === undefined) return undefined
  if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid()
  const item = value as Record<string, unknown>
  if (Object.keys(item).length !== 3 || item.protocol !== 'polymath-surprise-ladder-v1' || !Number.isSafeInteger(item.max_stages) || (item.max_stages as number) < 1 || (item.max_stages as number) > 4 || !Number.isSafeInteger(item.max_depth) || (item.max_depth as number) < 1 || (item.max_depth as number) > 3) return invalid()
  return { protocol: item.protocol, max_stages: item.max_stages as number, max_depth: item.max_depth as number }
}
