// The rigour bar (docs/RIGOUR.md): which literature an investigation may use.
import { useSyncExternalStore } from 'react'

export type Rigour = 'frontier' | 'standard' | 'peer_reviewed' | 'established'
export const RIGOURS: { id: Rigour; label: string; line: string }[] = [
  { id: 'frontier', label: 'Frontier', line: 'Everything, including brand-new preprints. Fastest to new ideas, least vetted.' },
  { id: 'standard', label: 'Standard', line: 'Never retracted work. Preprints included, and labelled as such.' },
  { id: 'peer_reviewed', label: 'Peer reviewed', line: 'Only work published in journals and conference proceedings.' },
  { id: 'established', label: 'Established', line: 'Peer reviewed and cited at least ten times. Most vetted, fewer brand-new ideas.' },
]
export const DEFAULT_RIGOUR: Rigour = 'standard'
const KEY = 'polymath.rigour'
const listeners = new Set<() => void>()

function read(): Rigour {
  try { const value = window.localStorage.getItem(KEY); return RIGOURS.some((item) => item.id === value) ? value as Rigour : DEFAULT_RIGOUR } catch { return DEFAULT_RIGOUR }
}
let current: Rigour = typeof window === 'undefined' ? DEFAULT_RIGOUR : read()

export function currentRigour(): Rigour { return current }
export function setRigour(value: Rigour) {
  current = value
  try { window.localStorage.setItem(KEY, value) } catch { /* A convenience only. */ }
  listeners.forEach((listener) => listener())
}
export function useRigour(): Rigour {
  return useSyncExternalStore((listener) => { listeners.add(listener); return () => listeners.delete(listener) }, currentRigour, () => DEFAULT_RIGOUR)
}
export function rigourLabel(value: string | undefined): string | null {
  return RIGOURS.find((item) => item.id === value)?.label ?? null
}
