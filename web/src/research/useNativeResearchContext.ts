import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { operatorSession, PublicationError } from '../publications/client'
import { BudgetError } from '../budget/client'
import { getCurrentResearchActivation } from '../budget/activationClient'
import type { EligibleActivation } from '../budget/activationTypes'
import { budgetDeadline } from '../budget/deadline'
import type { NativeResearchContextValue } from './nativeContext'

export function useNativeResearchContext({ enabled, csrfToken, required, available, onBrowserExpired }: { enabled: boolean; csrfToken: string | null; required: boolean; available: boolean; onBrowserExpired: () => void }): NativeResearchContextValue {
  const [phase, setPhase] = useState<NativeResearchContextValue['phase']>(required ? 'disabled' : 'not_required'), [activation, setActivation] = useState<EligibleActivation | null>(null), [operatorCsrf, setOperatorCsrf] = useState<string | null>(null), [operatorExpiry, setOperatorExpiry] = useState<string | null>(null), [reason, setReason] = useState<string | null>(null), [revision, setRevision] = useState(0), [pollPaused, setPollPaused] = useState(false)
  const [loadedCsrf, setLoadedCsrf] = useState<string | null>(null)
  const generation = useRef(0), request = useRef<AbortController | null>(null), browserExpired = useRef(onBrowserExpired)
  browserExpired.current = onBrowserExpired
  const invalidate = useCallback((why = 'meta_research_authority_expired') => { generation.current++; request.current?.abort(); setActivation(null); setOperatorCsrf(null); setOperatorExpiry(null); setLoadedCsrf(null); setPollPaused(false); setReason(why); setPhase('expired') }, [])
  const refresh = useCallback(() => { generation.current++; request.current?.abort(); setActivation(null); setOperatorCsrf(null); setOperatorExpiry(null); setPollPaused(false); setReason(null); setRevision((value) => value + 1) }, [])
  useEffect(() => {
    const scope = ++generation.current, controller = new AbortController(); request.current = controller; let active = true, timer: number | undefined, reads = 0
    setActivation(null); setOperatorCsrf(null); setOperatorExpiry(null); setPollPaused(false); setReason(null)
    if (!required) { setPhase('not_required'); return () => controller.abort() }
    if (!enabled || !csrfToken || !available) { setPhase('disabled'); return () => controller.abort() }
    setPhase('loading')
    const current = () => active && scope === generation.current && !controller.signal.aborted
    function failed(caught: unknown) {
      if (!current()) return
      setActivation(null); setOperatorCsrf(null); setOperatorExpiry(null); setPollPaused(false)
      const code = caught instanceof BudgetError || caught instanceof PublicationError ? caught.code : 'unavailable'
      setReason(code)
      if (['browser_expired', 'browser_changed'].includes(code)) { setPhase('expired'); generation.current++; controller.abort(); browserExpired.current() }
      else if (['operator_expired', 'operator_unavailable', 'readiness_role_unavailable', 'readiness_actor_forbidden', 'meta_research_actor_forbidden'].includes(code)) setPhase('operator_required')
      else setPhase('error')
    }
    async function readCurrent() {
      try { const currentActivation = await getCurrentResearchActivation(controller.signal); if (!current()) return
        reads++
        if (!currentActivation.activation || Date.parse(currentActivation.activation.expires_at) <= Date.now()) {
          setActivation(null); setReason(currentActivation.reason ?? 'meta_research_authority_expired'); setPhase('activation_required')
          if (currentActivation.reason === 'meta_research_prior_unresolved') {
            if (reads < 60) timer = window.setTimeout(() => void readCurrent(), 10000)
            else setPollPaused(true)
          } else { setOperatorCsrf(null); setOperatorExpiry(null); setLoadedCsrf(null) }
          return
        }
        setActivation(currentActivation.activation); setReason(null); setPhase('ready')
        if (reads < 60) timer = window.setTimeout(() => void readCurrent(), 10000)
        else setPollPaused(true)
      } catch (caught) { failed(caught) }
    }
    void (async () => {
      try { const access = await operatorSession(controller.signal); if (!current()) return
        if (!access.authenticated || !access.csrf_token || !access.expires_at || Date.parse(access.expires_at) <= Date.now()) { setPhase('operator_required'); setReason('operator_session_required'); return }
        setOperatorCsrf(access.csrf_token); setOperatorExpiry(access.expires_at); setLoadedCsrf(csrfToken); await readCurrent()
      } catch (caught) { failed(caught) }
    })()
    return () => { active = false; controller.abort(); window.clearTimeout(timer) }
  }, [enabled, csrfToken, required, available, revision])
  useEffect(() => { if (!operatorExpiry) return; const deadline = activation && Date.parse(activation.expires_at) < Date.parse(operatorExpiry) ? activation.expires_at : operatorExpiry; return budgetDeadline(deadline, () => invalidate('meta_research_authority_expired')) }, [activation, operatorExpiry, invalidate])
  const canSubmit = !required || enabled && available && phase === 'ready' && !pollPaused && loadedCsrf === csrfToken && !!operatorCsrf && !!operatorExpiry && Date.parse(operatorExpiry) > Date.now() && !!activation && Date.parse(activation.expires_at) > Date.now()
  const submission = useMemo(() => required && canSubmit && operatorCsrf && activation ? { operatorCsrfToken: operatorCsrf, expectedActivationSha256: activation.manifest_sha256 } : null, [required, canSubmit, operatorCsrf, activation?.manifest_sha256])
  return useMemo(() => ({ required, phase, activation, submission, canSubmit, pollPaused, reason, invalidate, refresh }), [required, phase, activation, submission, canSubmit, pollPaused, reason, invalidate, refresh])
}
