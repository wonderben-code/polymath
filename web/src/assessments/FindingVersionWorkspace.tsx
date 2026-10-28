import { useCallback, useEffect, useRef, useState } from 'react'
import { AssessmentError, getFindingVersion } from './client'
import type { FindingVersion } from './types'
import { useFindingHead } from './useFindingHead'
import { ResearchRunView } from '../research/ResearchRunView'
import { ArtifactWorkspace } from '../artifacts/ArtifactWorkspace'

export function FindingVersionWorkspace({ resultId, enabled, csrfToken, canGenerate, refreshToken, onSessionExpired, onOpenFinding, onOpenRun, onBusyChange, onClose }: {
  resultId: string
  enabled: boolean
  csrfToken: string | null
  canGenerate: boolean
  refreshToken?: number
  onSessionExpired: () => void
  onOpenFinding: (id: string) => void
  onOpenRun: (id: string) => void
  onBusyChange?: (busy: boolean) => void
  onClose: () => void
}) {
  const [version, setVersion] = useState<FindingVersion | null>(null)
  const [phase, setPhase] = useState<'loading' | 'ready' | 'error'>('loading')
  const [refresh, setRefresh] = useState(0)
  const blocked = useRef(false), request = useRef<AbortController | null>(null), notifyExpired = useRef(onSessionExpired)
  notifyExpired.current = onSessionExpired
  const expire = useCallback(() => { if (blocked.current) return; blocked.current = true; request.current?.abort(); setVersion(null); setPhase('error'); notifyExpired.current() }, [])
  const head = useFindingHead({ resultId, enabled: enabled && !!csrfToken, onSessionExpired: expire, refreshToken })
  useEffect(() => { blocked.current = false; return () => { blocked.current = true; request.current?.abort() } }, [resultId, enabled, csrfToken])
  useEffect(() => {
    const controller = new AbortController(); let active = true
    request.current = controller
    setVersion(null); setPhase('loading')
    if (!enabled || !csrfToken || blocked.current) return () => controller.abort()
    getFindingVersion(resultId, controller.signal).then((saved) => {
      if (active && !controller.signal.aborted && !blocked.current) { setVersion(saved); setPhase('ready') }
    }).catch((error: unknown) => {
      if (!active || controller.signal.aborted) return
      setVersion(null); setPhase('error')
      if (error instanceof AssessmentError && error.code === 'session_expired') expire()
    })
    return () => { active = false; controller.abort() }
  }, [resultId, enabled, csrfToken, refresh, expire])
  return <section className="finding-version-workspace" aria-label="Exact saved finding version">
    <div className="assessment-row"><p className="assessment-note">An exact saved finding version, with its original evidence and current assessment status.</p><button type="button" className="text-button" onClick={onClose}>Close finding version</button></div>
    {phase === 'loading' && <p role="status">Loading the exact saved finding and source versions…</p>}
    {phase === 'error' && <div role="alert" className="assessment-warning"><p>{head.phase === 'expired' ? 'The browser session expired or changed. Private finding text and source versions have been cleared.' : 'This finding version is unavailable or could not be verified in this browser session. Unapplied candidates do not become current findings.'}</p><button type="button" disabled={blocked.current} onClick={() => setRefresh((previous) => previous + 1)}>Reload finding version</button></div>}
    {version && <><ResearchRunView run={version.run} result={version.payload} events={version.payload.events} connection="closed" onOpenRun={onOpenRun} findingVersion={{ head: head.head, phase: head.phase, pollPaused: head.pollPaused, onRefresh: head.refresh, onOpenFinding }} />
      {(version.payload.finding?.outcome === 'bridge' || version.payload.finding?.outcome === 'answer') && <ArtifactWorkspace key={version.id} sourceResultId={version.id} sourceKind={version.payload.finding.outcome} csrfToken={csrfToken} enabled={enabled} canGenerate={canGenerate && head.canGenerate} sourceCurrent={head.canGenerate} onSessionExpired={expire} onBusyChange={onBusyChange} />}
    </>}
  </section>
}
