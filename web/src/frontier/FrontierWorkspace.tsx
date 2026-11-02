import { useCallback, useEffect, useRef, useState } from 'react'
import type { OwnedRun } from '../research/client'
import { attachFrontierRun, changeProgramme, FrontierApiError, listProgrammes, loadProgramme, nextFrontierAction, pruneFrontierNode } from './client'
import type { FrontierNodeStatus, FrontierProgramme, FrontierSummary } from './client'
import { FrontierMap, FrontierOutline, STATUS_LABELS } from './FrontierMap'
import { frontierReport } from './report'
import './frontier.css'

const ACTION_LABELS = { survey: 'Surveying the literature', transfer: 'Searching other fields for a solution', decompose: 'Breaking the gap into smaller steps', attempt: 'Attempting a step', converge: 'Looking for a connection between the bridges' } as const
const LEGEND: FrontierNodeStatus[] = ['supported', 'transferred', 'derived', 'conjectured', 'open', 'pending', 'skipped']
const POLL_MS = 6000
const RETRY_HOLD_MS = 30000
const MAX_HOLDS = 10
const TRANSIENT = new Set(['unavailable', 'rate_limited', 'invalid_response', 'aborted'])

export interface FrontierWorkspaceProps {
  enabled: boolean
  csrfToken: string | null
  canSubmit: boolean
  /** Present while the allowance poll has paused; a running programme calls it so live research is checked again. */
  refreshAccess?: () => void
  focusId: string | null
  submit: (mode: 'ask' | 'explore', goal: string, parentResultIds: readonly string[], options?: { collection?: boolean }) => Promise<OwnedRun | undefined>
  onOpenRun: (id: string) => void
  onSessionExpired: () => void
  onFinished?: (programme: FrontierProgramme) => void
  notifyControl?: import('react').ReactNode
  /** Send one step to Solve, the key finder and key maker. */
  onSolveStep?: (programmeId: string, nodeId: string) => void
}

function message(code: string): string {
  if (code === 'session_expired') return 'Your browser session expired. Reload to continue.'
  if (code === 'frontier_unavailable') return 'Frontier programmes are switched off on this server.'
  if (code === 'frontier_limit_reached') return 'This browser already has the maximum number of programmes.'
  if (code === 'frontier_finished') return 'This programme has finished.'
  if (code === 'rate_limited') return 'Polymath is pacing requests from this browser; it continues in a moment.'
  return 'Frontier could not be reached. Try again in a moment.'
}

export function FrontierWorkspace({ enabled, csrfToken, canSubmit, refreshAccess, focusId, submit, onOpenRun, onSessionExpired, onFinished, notifyControl, onSolveStep }: FrontierWorkspaceProps) {
  const [programmes, setProgrammes] = useState<FrontierSummary[]>([])
  const [programme, setProgramme] = useState<FrontierProgramme | null>(null)
  const [selectedNode, setSelectedNode] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const busy = useRef(false)
  const holds = useRef(0)
  const holdUntil = useRef(0)
  const canSubmitRef = useRef(canSubmit)
  canSubmitRef.current = canSubmit
  // The tick awaits the server before submitting, so it must call the latest submit, not the one captured when the tick began (that one may predate the previous run finishing).
  const submitRef = useRef(submit)
  submitRef.current = submit
  const refreshAccessRef = useRef(refreshAccess)
  refreshAccessRef.current = refreshAccess
  const refreshedAt = useRef(0)

  const fail = useCallback((failure: unknown) => {
    const code = failure instanceof FrontierApiError ? failure.code : 'unavailable'
    if (code === 'session_expired') onSessionExpired()
    setError(message(code))
  }, [onSessionExpired])

  const refreshList = useCallback(async () => {
    if (!enabled) return
    try { setProgrammes(await listProgrammes()) } catch (failure) { fail(failure) }
  }, [enabled, fail])

  const open = useCallback(async (id: string) => {
    setError('')
    try { const loaded = await loadProgramme(id); setProgramme(loaded); setSelectedNode(null); if (loaded.status === 'active' && loaded.nodes.some((node) => node.status === 'running')) setRunning(true) } catch (failure) { fail(failure) }
  }, [fail])

  useEffect(() => { void refreshList() }, [refreshList])
  useEffect(() => { if (focusId) { void open(focusId); void refreshList(); setRunning(true) } }, [focusId, open, refreshList])

  // One step at a time: absorb finished work, then queue the next due attempt
  // through the ordinary investigation path, which applies every approval and
  // budget check. The programme only advances while this page is open.
  const tick = useCallback(async () => {
    if (!programme || !csrfToken || busy.current) return
    if (Date.now() < holdUntil.current) return
    busy.current = true
    try {
      const { next, programme: updated } = await nextFrontierAction(programme.id, csrfToken)
      setProgramme(updated)
      if (next.state === 'due') {
        if (!canSubmitRef.current) {
          setStatus('Waiting until live research is available for this browser.')
          // The allowance poll pauses after ten minutes without a visitor action; a running programme is that action.
          if (refreshAccessRef.current && Date.now() - refreshedAt.current >= 30000) { refreshedAt.current = Date.now(); refreshAccessRef.current() }
          return
        }
        if (Date.now() < holdUntil.current) return
        setStatus(`${ACTION_LABELS[next.action]}…`)
        const run = await submitRef.current(next.mode, next.goal, next.parent_result_ids, { collection: false })
        if (!run) {
          // A step that could not be queued (a blip, a restart, a rate limit) is tried again shortly; the programme stops only when it keeps failing.
          holds.current += 1; holdUntil.current = Date.now() + RETRY_HOLD_MS
          if (holds.current >= MAX_HOLDS) { setRunning(false); setStatus('The next step could not be queued. Live research may be unavailable; the programme is saved.'); return }
          setStatus('The next step could not be queued yet. Trying again shortly; the programme is saved.'); return
        }
        holds.current = 0
        setProgramme(await attachFrontierRun(programme.id, csrfToken, { node_id: next.node_id, action: next.action, run_id: run.id }))
      } else if (next.state === 'waiting') {
        setStatus('An investigation for this programme is in progress.')
      } else {
        setRunning(false)
        setStatus(next.state === 'completed' ? (next.reason === 'attempt_limit' ? 'Finished: the attempt limit was reached.' : 'Finished: every branch has been explored.') : `Programme ${next.state}.`)
        if (next.state === 'completed') { onFinished?.(updated); void refreshList() }
      }
    } catch (failure) {
      // A passing failure (the service restarting, a blip, a paced request) holds and tries again; a lost session or a persistent failure stops the programme.
      if (failure instanceof FrontierApiError && TRANSIENT.has(failure.code) && holds.current + 1 < MAX_HOLDS) { holds.current += 1; holdUntil.current = Date.now() + RETRY_HOLD_MS; setStatus('Polymath could not be reached. Trying again shortly.'); return }
      setRunning(false); fail(failure)
    } finally { busy.current = false }
  }, [programme, csrfToken, fail, onFinished, refreshList])

  // The timer reads the latest step through a ref: each step updates the
  // programme, and restarting the effect on that change would loop.
  const tickRef = useRef(tick)
  tickRef.current = tick
  const activeId = programme?.status === 'active' ? programme.id : null
  useEffect(() => {
    if (!running || !activeId) return
    void tickRef.current()
    const timer = window.setInterval(() => void tickRef.current(), POLL_MS)
    return () => window.clearInterval(timer)
  }, [running, activeId])

  async function change(kind: 'pause' | 'resume' | 'stop') {
    if (!programme || !csrfToken) return
    try {
      setProgramme(await changeProgramme(programme.id, csrfToken, kind))
      setRunning(kind === 'resume')
      void refreshList()
    } catch (failure) { fail(failure) }
  }

  function download(current: FrontierProgramme) {
    const url = URL.createObjectURL(new Blob([frontierReport(current)], { type: 'text/markdown;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = `frontier-report-${current.id.slice(0, 8)}.md`
    link.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  async function prune(nodeId: string) {
    if (!programme || !csrfToken) return
    try { setProgramme(await pruneFrontierNode(programme.id, nodeId, csrfToken)) } catch (failure) { fail(failure) }
  }

  const node = programme?.nodes.find((item) => item.id === selectedNode) ?? null
  const counts = programme ? LEGEND.map((key) => [key, programme.nodes.filter((item) => item.resolved_status === key).length] as const) : []

  return (
    <section className="frontier-workspace" aria-labelledby="frontier-title">
      <h2 className="frontier-title" id="frontier-title">Your programmes</h2>
      {programmes.length > 0 && <ul className="frontier-list" aria-label="Your frontier programmes">{programmes.map((item) => <li key={item.id}><button type="button" aria-pressed={programme?.id === item.id} onClick={() => void open(item.id)}><span className="frontier-list-status">{item.status}</span><span>{item.question}</span></button></li>)}</ul>}
      {programmes.length === 0 && !programme && <p className="frontier-empty">No programmes yet. Describe an unsolved problem to start one.</p>}
      {error && <p className="frontier-error" role="alert">{error}</p>}
      {programme && <div className="frontier-programme">
        <div className="frontier-programme-heading">
          <div><h3>{programme.question}</h3>{programme.success_criteria && <p className="frontier-criteria">Success looks like: {programme.success_criteria}</p>}</div>
          <div className="frontier-controls">
            {programme.status === 'active' && !running && <button type="button" className="frontier-primary" onClick={() => setRunning(true)} disabled={!csrfToken}>Run programme</button>}
            {programme.status === 'active' && running && <button type="button" onClick={() => void change('pause')}>Pause</button>}
            {programme.status === 'paused' && <button type="button" className="frontier-primary" onClick={() => void change('resume')}>Resume</button>}
            {(programme.status === 'active' || programme.status === 'paused') && <button type="button" onClick={() => void change('stop')}>Stop</button>}
            <button type="button" onClick={() => download(programme)}>Download report</button>
          </div>
        </div>
        <p className="frontier-progress" role="status">{running ? <span className="frontier-live-dot" aria-hidden="true" /> : null}{status || (programme.status === 'active' ? 'Ready. Run the programme to research one step at a time; keep this tab open.' : `Programme ${programme.status}.`)} <span className="frontier-usage">{programme.attempts_used} of {programme.limits.max_attempts} investigations used · depth up to {programme.limits.max_depth}</span></p>
        {running && notifyControl}
        <ul className="frontier-legend" aria-label="Frontier status legend">{counts.filter(([, count]) => count > 0).map(([key, count]) => <li key={key}><span className={`frontier-swatch status-${key}`} aria-hidden="true" />{STATUS_LABELS[key]} <strong>{count}</strong></li>)}</ul>
        <FrontierMap nodes={programme.nodes} selectedId={selectedNode} onSelect={setSelectedNode} />
        <details className="frontier-outline-toggle"><summary>Read the map as a list</summary><FrontierOutline nodes={programme.nodes} selectedId={selectedNode} onSelect={setSelectedNode} /></details>
        {node && <article className="frontier-detail" aria-label="Selected frontier node">
          <span className="eyebrow accent">{node.kind.toUpperCase()} · {STATUS_LABELS[node.resolved_status]}</span>
          <p className="frontier-detail-statement">{node.statement}</p>
          {node.pending_action && <p>Next: {ACTION_LABELS[node.pending_action].toLowerCase()}.</p>}
          {node.attempts.length > 0 && <ul className="frontier-attempts">{node.attempts.map((attempt) => <li key={attempt.run_id}><span>{ACTION_LABELS[attempt.action]}{attempt.outcome ? ` · ${attempt.outcome.replaceAll('_', ' ')}` : ' · in progress'}</span><button type="button" onClick={() => onOpenRun(attempt.run_id)}>Open investigation</button></li>)}</ul>}
          {node.kind !== 'question' && node.pending_action && <button type="button" onClick={() => void prune(node.id)}>Skip this branch</button>}
          {onSolveStep && programme && node.kind !== 'convergence' && <button type="button" onClick={() => onSolveStep(programme.id, node.id)}>Solve this step</button>}
        </article>}
        <p className="frontier-note"><span className="frontier-badge">Experimental</span> Supported means exact quotations from saved sources; derived and conjectured steps are model reasoning, clearly labelled. A solve path is only as strong as its weakest step.</p>
      </div>}
    </section>
  )
}
