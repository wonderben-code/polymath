import { useEffect, useRef, useState } from 'react'
import { createArtifactJob } from '../artifacts/client'
import { startPublication } from '../publications/deliveryClient'
import { approvePublication, getPublication, parseMetadata, PublicationError, preparePublication } from '../publications/client'
import type { NativeSubmissionContext } from '../research/nativeAdmission'
import { attachPublishingStep, nextPublishingStep } from './client'
import type { Level, PublishingStep } from './client'

const TICK_MS = 6000

export interface AutopilotOptions {
  csrf: string | null
  operatorCsrf: string | null
  level: Level
  native?: NativeSubmissionContext
  onProgress: () => void
  onOperatorExpired: () => void
}

function codeOf(failure: unknown): string {
  if (failure instanceof PublicationError) return failure.code
  if (failure && typeof failure === 'object' && 'code' in failure && typeof (failure as { code: unknown }).code === 'string') return (failure as { code: string }).code
  return 'unavailable'
}

function note(code: string): string {
  if (code === 'active_run_limit') return 'Waiting for the current investigation to finish before writing.'
  if (code === 'research_unavailable' || code === 'meta_research_unavailable') return 'Writing waits until live research is available.'
  if (code === 'publication_execution_disabled' || code === 'unavailable') return 'Publishing is paused on this server; prepared records wait safely.'
  return 'A step could not be completed. It will be tried again shortly.'
}

/** Carry out one due publishing step at a time through the ordinary routes. */
export async function performStep(step: PublishingStep, csrf: string, operatorCsrf: string, native?: NativeSubmissionContext): Promise<boolean> {
  if (step.action === 'write') {
    const job = await createArtifactJob(step.kind, step.result_id, csrf, undefined, undefined, native)
    await attachPublishingStep(step.item_id, 'write', job.id, csrf, operatorCsrf)
    return true
  }
  if (step.action === 'prepare') {
    const record = await preparePublication(step.artifact_id, parseMetadata(step.metadata), step.destination, csrf)
    await attachPublishingStep(step.item_id, 'prepare', record.id, csrf, operatorCsrf)
    return true
  }
  if (step.action === 'approve') {
    const record = await getPublication(step.manifest_id)
    if (record.manifest_sha256 !== step.manifest_sha256) throw new PublicationError('manifest_changed')
    const approved = await approvePublication(record, csrf, operatorCsrf)
    if (!approved.approval) throw new PublicationError('invalid_response')
    await attachPublishingStep(step.item_id, 'approve', approved.approval.id, csrf, operatorCsrf)
    return true
  }
  if (step.action === 'queue') {
    const record = await getPublication(step.manifest_id)
    if (record.manifest_sha256 !== step.manifest_sha256) throw new PublicationError('manifest_changed')
    const job = await startPublication(record, step.approval_id, csrf, operatorCsrf)
    await attachPublishingStep(step.item_id, 'queue', job.id, csrf, operatorCsrf)
    return true
  }
  return false
}

export function usePublishingAutopilot({ csrf, operatorCsrf, level, native, onProgress, onOperatorExpired }: AutopilotOptions) {
  const [status, setStatus] = useState('')
  const busy = useRef(false)
  const latest = useRef({ csrf, operatorCsrf, native, onProgress, onOperatorExpired })
  latest.current = { csrf, operatorCsrf, native, onProgress, onOperatorExpired }
  const active = level >= 2 && !!csrf && !!operatorCsrf

  useEffect(() => {
    if (!active) { setStatus(''); return }
    let cancelled = false
    const tick = async () => {
      const current = latest.current
      if (busy.current || !current.csrf || !current.operatorCsrf) return
      busy.current = true
      try {
        const step = await nextPublishingStep(current.csrf, current.operatorCsrf)
        if (cancelled) return
        if (step.action === 'none') setStatus(step.reason === 'daily_cap' ? 'Today’s publishing cap is reached; it resumes tomorrow.' : '')
        else if (step.action === 'wait') setStatus('Writing a finding up…')
        else if (await performStep(step, current.csrf, current.operatorCsrf, current.native)) { setStatus(''); current.onProgress() }
      } catch (failure) {
        const code = codeOf(failure)
        if (code === 'operator_expired') { setStatus('Publisher sign-in expired. Sign in again to continue.'); current.onOperatorExpired() }
        else setStatus(note(code))
      } finally { busy.current = false }
    }
    void tick()
    const timer = window.setInterval(() => void tick(), TICK_MS)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [active])

  return { status, active }
}
