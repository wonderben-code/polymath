import { useCallback, useEffect, useRef, useState } from 'react'
import { AssessmentError, getAssessmentHead } from './client'
import type { FindingHead } from './types'

export function useFindingHead({ resultId, enabled, onSessionExpired, refreshToken }: {
  resultId: string | null
  enabled: boolean
  onSessionExpired: () => void
  refreshToken?: string | number | null
}) {
  const [head, setHead] = useState<FindingHead | null>(null)
  const [phase, setPhase] = useState<'idle' | 'loading' | 'ready' | 'error' | 'expired'>('idle')
  const [revision, setRevision] = useState(0)
  const [pollPaused, setPollPaused] = useState(false)
  const expire = useRef(onSessionExpired)
  expire.current = onSessionExpired
  const refresh = useCallback(() => { setHead(null); setPollPaused(false); setRevision((previous) => previous + 1) }, [])
  useEffect(() => {
    const controller = new AbortController()
    let active = true, timer: number | undefined, reads = 0
    setHead(null); setPollPaused(false)
    if (!enabled || !resultId) { setPhase('idle'); return () => controller.abort() }
    setPhase('loading')
    async function load() {
      try {
        const incoming = await getAssessmentHead(resultId!, controller.signal)
        if (!active || controller.signal.aborted) return
        setHead(incoming); setPhase('ready'); reads++
        if (reads < 60) timer = window.setTimeout(() => void load(), 10000)
        else setPollPaused(true)
      } catch (error) {
        if (!active || controller.signal.aborted) return
        setHead(null)
        if (error instanceof AssessmentError && error.code === 'session_expired') { setPhase('expired'); expire.current() }
        else setPhase('error')
      }
    }
    void load()
    return () => { active = false; controller.abort(); window.clearTimeout(timer) }
  }, [enabled, resultId, refreshToken, revision])
  // An unavailable or no-longer-refreshed head cannot authorize new work.
  const current = phase === 'ready' && !pollPaused && head?.requested_result_id === resultId ? head : null
  return { head, phase, pollPaused, refresh, canGenerate: current?.can_generate === true }
}
