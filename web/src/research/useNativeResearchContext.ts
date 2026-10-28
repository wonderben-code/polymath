import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { operatorSession, PublicationError } from '../publications/client'
import { BudgetError } from '../budget/client'
import { getCurrentResearchActivation } from '../budget/activationClient'
import type { EligibleActivation } from '../budget/activationTypes'
import { budgetDeadline } from '../budget/deadline'
import { getResearchProtocols } from '../scientificContext/client'
import type { ScientificCapability } from '../scientificContext/types'
import { getResearchAccess, ResearchAccessError } from './access'
import type { ResearchAccess } from './access'
import type { NativeSubmissionContext } from './nativeAdmission'
import type { NativeResearchContextValue } from './nativeContext'

export function useNativeResearchContext({ enabled, csrfToken, required, available, onBrowserExpired }: { enabled: boolean; csrfToken: string | null; required: boolean; available: boolean; onBrowserExpired: () => void }): NativeResearchContextValue {
  const [phase, setPhase] = useState<NativeResearchContextValue['phase']>(required ? 'disabled' : 'not_required'), [activation, setActivation] = useState<EligibleActivation | null>(null), [access, setAccess] = useState<ResearchAccess | null>(null), [operatorCsrf, setOperatorCsrf] = useState<string | null>(null), [operatorExpiry, setOperatorExpiry] = useState<string | null>(null), [reason, setReason] = useState<string | null>(null), [revision, setRevision] = useState(0), [pollPaused, setPollPaused] = useState(false)
  const [loadedCsrf, setLoadedCsrf] = useState<string | null>(null), [retryUntil, setRetryUntil] = useState<number | null>(null), [scientificCapabilities, setScientificCapabilities] = useState<ScientificCapability[]>([]), [scientificPhase, setScientificPhase] = useState<'disabled' | 'loading' | 'ready' | 'error'>('disabled')
  const generation = useRef(0), request = useRef<AbortController | null>(null), browserExpired = useRef(onBrowserExpired), scientificBlockedHash = useRef<string | null>(null), authorityHash = useRef<string | null>(null)
  browserExpired.current = onBrowserExpired
  authorityHash.current = access?.expected_activation_sha256 ?? activation?.manifest_sha256 ?? null
  const invalidateScientific = useCallback((expectedHash?: string) => { if (expectedHash && expectedHash !== authorityHash.current) return; scientificBlockedHash.current = authorityHash.current; setScientificCapabilities([]); setScientificPhase('error') }, [])
  const invalidate = useCallback((why = 'meta_research_authority_expired') => { generation.current++; request.current?.abort(); setActivation(null); setScientificCapabilities([]); setScientificPhase('disabled'); setOperatorCsrf(null); setOperatorExpiry(null); setLoadedCsrf(null); setPollPaused(false); setReason(why); setPhase('expired') }, [])
  const refresh = useCallback(() => { if (retryUntil !== null && retryUntil > Date.now()) return; generation.current++; request.current?.abort(); setActivation(null); setAccess(null); setScientificCapabilities([]); setScientificPhase('disabled'); setOperatorCsrf(null); setOperatorExpiry(null); setLoadedCsrf(null); setPollPaused(false); setReason(null); setRevision((value) => value + 1) }, [retryUntil])
  useEffect(() => {
    scientificBlockedHash.current = null
    const scope = ++generation.current, controller = new AbortController(); request.current = controller; let active = true, timer: number | undefined, reads = 0, protocolsHash: string | null = null
    setActivation(null); setAccess(null); setScientificCapabilities([]); setScientificPhase('disabled'); setOperatorCsrf(null); setOperatorExpiry(null); setLoadedCsrf(null); setRetryUntil(null); setPollPaused(false); setReason(null)
    if (!required) { setPhase('not_required'); return () => controller.abort() }
    if (!enabled || !csrfToken || !available) { setPhase('disabled'); return () => controller.abort() }
    setPhase('loading')
    const current = () => active && scope === generation.current && !controller.signal.aborted
    function failed(caught: unknown) {
      if (!current()) return
      setActivation(null); setScientificCapabilities([]); setScientificPhase('disabled'); setOperatorCsrf(null); setOperatorExpiry(null); setPollPaused(false)
      const code = caught instanceof BudgetError || caught instanceof PublicationError || caught instanceof ResearchAccessError ? caught.code : 'unavailable'
      setReason(code)
      if (caught instanceof ResearchAccessError && caught.code === 'resource_limited' && caught.retryAfterSeconds !== null) setRetryUntil(Date.now() + caught.retryAfterSeconds * 1000)
      if (['browser_expired', 'browser_changed'].includes(code)) { setAccess(null); setPhase('expired'); generation.current++; controller.abort(); browserExpired.current() }
      else if (['operator_expired', 'operator_unavailable', 'readiness_role_unavailable', 'readiness_actor_forbidden', 'meta_research_actor_forbidden'].includes(code)) setPhase('operator_required')
      else setPhase('error')
    }
    async function readCurrent() {
      try { const currentActivation = await getCurrentResearchActivation(controller.signal); if (!current()) return
        reads++
        if (!currentActivation.activation || Date.parse(currentActivation.activation.expires_at) <= Date.now()) {
          setActivation(null); setScientificCapabilities([]); setScientificPhase('disabled'); setReason(currentActivation.reason ?? 'meta_research_authority_expired'); setPhase('activation_required')
          if (currentActivation.reason === 'meta_research_prior_unresolved') {
            if (reads < 60) timer = window.setTimeout(() => void readCurrent(), 10000)
            else setPollPaused(true)
          } else { setOperatorCsrf(null); setOperatorExpiry(null); setLoadedCsrf(null) }
          return
        }
        setActivation(currentActivation.activation); if (scientificBlockedHash.current !== currentActivation.activation.manifest_sha256) { setScientificCapabilities(currentActivation.activation.scientific_context_profiles ? ['artifact_scientific_context_v1', 'ask_claim_overview_v1', 'ask_reassessment_overview_v1'] : []); setScientificPhase('ready') }; setReason(null); setPhase('ready')
        if (reads < 60) timer = window.setTimeout(() => void readCurrent(), 10000)
        else setPollPaused(true)
      } catch (caught) { failed(caught) }
    }
    async function readAccess() {
      let protocolRetrySeconds = 0
      try {
        const next = await getResearchAccess(controller.signal); if (!current()) return
        setAccess(next)
        if (next.audience === 'operator_browser') {
          const operator = await operatorSession(controller.signal); if (!current()) return
          if (!operator.authenticated || !operator.csrf_token || !operator.expires_at || Date.parse(operator.expires_at) <= Date.now()) { setPhase('operator_required'); setReason('operator_session_required'); return }
          setOperatorCsrf(operator.csrf_token); setOperatorExpiry(operator.expires_at); setLoadedCsrf(csrfToken); await readCurrent(); return
        }
        reads++; setActivation(null); setOperatorCsrf(null); setOperatorExpiry(null)
        const live = next.audience === 'public_sessions' && next.available && !!next.expires_at && Date.parse(next.expires_at) > Date.now()
        setLoadedCsrf(live ? csrfToken : null); setPhase(live ? 'ready' : 'activation_required'); setReason(live ? null : next.reason ?? 'public_research_authority_expired')
        if (!live) { protocolsHash = null; setScientificCapabilities([]); setScientificPhase('disabled') }
        else if (protocolsHash !== next.expected_activation_sha256) {
          protocolsHash = next.expected_activation_sha256; setScientificCapabilities([]); setScientificPhase('loading')
          try {
            const protocols = await getResearchProtocols(controller.signal); if (!current()) return
            if (protocols.activation_sha256 !== next.expected_activation_sha256) { setScientificCapabilities([]); setScientificPhase('error') }
            else if (scientificBlockedHash.current !== protocols.activation_sha256) { setScientificCapabilities(protocols.capabilities); setScientificPhase('ready') }
          } catch (caught) {
            if (!current()) return
            if (caught instanceof ResearchAccessError && ['browser_expired', 'browser_changed'].includes(caught.code)) { failed(caught); return }
            setScientificCapabilities([]); setScientificPhase('error')
            if (caught instanceof ResearchAccessError && caught.retryAfterSeconds !== null) { protocolRetrySeconds = caught.retryAfterSeconds; setRetryUntil(Date.now() + caught.retryAfterSeconds * 1000) }
          }
        }
        // Only GETs can refresh funding; an earlier task keeps its original hash.
        if (reads < 60) timer = window.setTimeout(() => void readAccess(), Math.max(10000, protocolRetrySeconds * 1000, (next.retry_after_seconds ?? 0) * 1000))
        else setPollPaused(true)
      } catch (caught) { failed(caught) }
    }
    void readAccess()
    return () => { active = false; controller.abort(); window.clearTimeout(timer) }
  }, [enabled, csrfToken, required, available, revision])
  useEffect(() => {
    const deadline = access?.audience === 'public_sessions' ? access.expires_at : operatorExpiry ? activation && Date.parse(activation.expires_at) < Date.parse(operatorExpiry) ? activation.expires_at : operatorExpiry : null
    if (!deadline || Date.parse(deadline) <= Date.now()) return
    return budgetDeadline(deadline, () => invalidate(access?.audience === 'public_sessions' ? 'public_research_authority_expired' : 'meta_research_authority_expired'))
  }, [access?.audience, access?.expires_at, activation, operatorExpiry, invalidate])
  useEffect(() => { if (retryUntil === null) return; return budgetDeadline(new Date(retryUntil).toISOString(), () => setRetryUntil(null)) }, [retryUntil])
  const retryAfterSeconds = retryUntil === null ? null : Math.max(0, Math.ceil((retryUntil - Date.now()) / 1000))
  const publicLive = access?.audience === 'public_sessions' && access.available && !!access.expires_at && Date.parse(access.expires_at) > Date.now() && !!access.expected_activation_sha256
  const privateLive = !!operatorCsrf && !!operatorExpiry && Date.parse(operatorExpiry) > Date.now() && !!activation && Date.parse(activation.expires_at) > Date.now()
  const canResume = !required || enabled && available && phase === 'ready' && !pollPaused && loadedCsrf === csrfToken && (publicLive || privateLive)
  const canSubmit = canResume && (access?.audience !== 'public_sessions' || (access.remaining_session_jobs_today ?? 0) > 0)
  const submission = useMemo<NativeSubmissionContext | null>(() => !required || !canResume ? null : access?.audience === 'public_sessions' && access.expected_activation_sha256 ? { audience: 'public_sessions', expectedActivationSha256: access.expected_activation_sha256 } : operatorCsrf && activation ? { operatorCsrfToken: operatorCsrf, expectedActivationSha256: activation.manifest_sha256 } : null, [required, canResume, access?.audience, access?.expected_activation_sha256, operatorCsrf, activation?.manifest_sha256])
  return useMemo(() => ({ required, phase, activation, access, scientificCapabilities, scientificPhase, invalidateScientific, submission, canSubmit, canResume, retryAfterSeconds, pollPaused, reason, invalidate, refresh }), [required, phase, activation, access, scientificCapabilities, scientificPhase, invalidateScientific, submission, canSubmit, canResume, retryAfterSeconds, pollPaused, reason, invalidate, refresh])
}
