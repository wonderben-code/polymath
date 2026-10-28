import { createContext, useContext } from 'react'
import type { ReactNode } from 'react'
import type { EligibleActivation } from '../budget/activationTypes'
import type { ResearchAccess } from './access'
import type { NativeSubmissionContext } from './nativeAdmission'

export interface NativeResearchContextValue {
  required: boolean
  phase: 'not_required' | 'disabled' | 'loading' | 'ready' | 'operator_required' | 'activation_required' | 'error' | 'expired'
  activation: EligibleActivation | null
  submission: NativeSubmissionContext | null
  access?: ResearchAccess | null
  retryAfterSeconds?: number | null
  canResume?: boolean
  canSubmit: boolean
  pollPaused: boolean
  reason: string | null
  invalidate: (reason?: string) => void
  refresh: () => void
}
// Standalone legacy fixtures do not use native admission. The private production
// App always provides its explicit server-selected native requirement.
const legacy: NativeResearchContextValue = { required: false, phase: 'not_required', activation: null, submission: null, canSubmit: true, pollPaused: false, reason: null, invalidate: () => {}, refresh: () => {} }
export const NativeResearchContext = createContext<NativeResearchContextValue>(legacy)
export function useNativeResearchSubmission() { return useContext(NativeResearchContext) }
export function NativeResearchProvider({ value, children }: { value: NativeResearchContextValue; children: ReactNode }) {
  return <NativeResearchContext.Provider value={value}>{children}</NativeResearchContext.Provider>
}
