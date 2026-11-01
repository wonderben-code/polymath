import { useEffect, useRef, useState } from 'react'
import { createArtifactJob } from '../artifacts/client'
import type { NativeSubmissionContext } from '../research/nativeAdmission'
import { attachZenodoWrite, nextZenodoStep, ZenodoError } from './client'
import type { ZenodoLevel } from './client'

const TICK_MS = 6000

function note(failure: unknown): string {
  const code = failure && typeof failure === 'object' && 'code' in failure ? String((failure as { code: unknown }).code) : ''
  if (code === 'active_run_limit') return 'Waiting for your current investigation to finish before writing.'
  if (['research_unavailable', 'meta_research_unavailable', 'public_research_unavailable'].includes(code)) return 'Writing waits until research is available here.'
  if (code.includes('limit') || code.includes('quota')) return 'Today’s free writing is used up; Polymath carries on tomorrow.'
  if (failure instanceof ZenodoError && code === 'session_expired') return 'Your session changed. Reload the page to carry on.'
  return 'A step could not be completed. It will be tried again shortly.'
}

/** While Your library is open, writes up new findings through the ordinary
 * route; the server prepares and publishes records on its own. */
export function useZenodoAutopilot({ csrf, level, native, onProgress }: { csrf: string | null; level: ZenodoLevel; native?: NativeSubmissionContext; onProgress: () => void }) {
  const [status, setStatus] = useState('')
  const busy = useRef(false)
  const latest = useRef({ csrf, native, onProgress })
  latest.current = { csrf, native, onProgress }
  const active = level >= 2 && !!csrf

  useEffect(() => {
    if (!active) { setStatus(''); return }
    let cancelled = false
    const tick = async () => {
      const current = latest.current
      if (busy.current || !current.csrf) return
      busy.current = true
      try {
        const step = await nextZenodoStep(current.csrf)
        if (cancelled) return
        if (step.action === 'write') {
          setStatus('Writing up a new finding…')
          const job = await createArtifactJob(step.kind, step.result_id, current.csrf, undefined, undefined, current.native)
          await attachZenodoWrite(step.item_id, job.id, current.csrf)
        } else if (step.action === 'wait') setStatus('Writing up a new finding…')
        else setStatus(step.reason === 'daily_cap' ? 'Today’s limit is reached; Polymath carries on tomorrow.' : '')
        current.onProgress()
      } catch (failure) { if (!cancelled) setStatus(note(failure)) } finally { busy.current = false }
    }
    void tick()
    const timer = window.setInterval(() => void tick(), TICK_MS)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [active])

  return { status, active }
}
