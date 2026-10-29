import { useCallback, useEffect, useRef, useState } from 'react'
import type { OwnedRun } from '../research/client'
import { attachFrontierRun, changeProgramme, FrontierApiError, listProgrammes, loadProgramme, nextFrontierAction, pruneFrontierNode } from './client'
import type { FrontierNodeStatus, FrontierProgramme, FrontierSummary } from './client'
import { FrontierMap, STATUS_LABELS } from './FrontierMap'
import './frontier.css'

const ACTION_LABELS = { survey: 'Surveying the literature', transfer: 'Searching other fields for a solution', decompose: 'Breaking the gap into smaller steps', attempt: 'Attempting a step' } as const
const LEGEND: FrontierNodeStatus[] = ['supported', 'transferred', 'derived', 'conjectured', 'open', 'pending', 'skipped']
const POLL_MS = 4000

export interface FrontierWorkspaceProps {
  enabled: boolean
  csrfToken: string | null
  canSubmit: boolean
  focusId: string | null
  submit: (mode: 'ask' | 'explore', goal: string) => Promise<OwnedRun | undefined>
  onOpenRun: (id: string) => void
  onSessionExpired: () => void
  onFinished?: (programme: FrontierProgramme) => void
}

function message(code: string): string {
  if (code === 'session_expired') return 'Your browser session expired. Reload to continue.'
  if (code === 'frontier_unavailable') return 'Frontier programmes are switched off on this server.'
  if (code === 'frontier_limit_reached') return 'This browser already has the maximum number of programmes.'
  if (code === 'frontier_finished') return 'This programme has finished.'
  return 'Frontier could not be reached. Try again in a moment.'
}

export function FrontierWorkspace({ enabled, csrfToken, canSubmit, focusId, submit, onOpenRun, onSessionExpired, onFinished }: FrontierWorkspaceProps) {
  const [programmes, setProgrammes] = useState<FrontierSummary[]>([])
  const [programme, setProgramme] = useState<FrontierProgramme | null>(null)
  const [selectedNode, setSelectedNode] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const busy = useRef(false)
  const canSubmitRef = useRef(canSubmit)
  canSubmitRef.current = canSubmit

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
    try { setProgramme(await loadProgramme(id)); setSelectedNode(null) } catch (failure) { fail(failure) }
  }, [fail])

  useEffect(() => { void refreshList() }, [refreshList])
  useEffect(() => { if (focusId) { void open(focusId); void refreshList(); setRunning(true) } }, [focusId, open, refreshList])

  // One step at a time: absorb finished work, then queue the next due attempt
  // through the ordinary investigation path, which applies every approval and
  // budget check. The programme only advances while this page is open.
  const tick = useCallback(async () => {
    if (!programme || !csrfToken || busy.current) return
    busy.current = true
    try {
      const { next, programme: updated } = await nextFrontierAction(programme.id, csrfToken)
      setProgramme(updated)
      if (next.state === 'due') {
        if (!canSubmitRef.current) { setStatus('Waiting until live research is available for this browser.'); return }
        setStatus(`${ACTION_LABELS[next.action]}…`)
        const run = await submit(next.mode, next.goal)
        if (!run) { setRunning(false); setStatus('The next step could not be queued. Live research may be unavailable; the programme is saved.'); return }
        setProgramme(await attachFrontierRun(programme.id, csrfToken, { node_id: next.node_id, action: next.action, run_id: run.id }))
      } else if (next.state === 'waiting') {
        setStatus('An investigation for this programme is in progress.')
      } else {
        setRunning(false)
        setStatus(next.state === 'completed' ? (next.reason === 'attempt_limit' ? 'Finished: the attempt limit was reached.' : 'Finished: every branch has been explored.') : `Programme ${next.state}.`)
        if (next.state === 'completed') { onFinished?.(updated); void refreshList() }
      }
    } catch (failure) {
      setRunning(false)
      fail(failure)
    } finally { busy.current = false }
  }, [programme, csrfToken, submit, fail, onFinished, refreshList])

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

  async function prune(nodeId: string) {
    if (!programme || !csrfToken) return
    try { setProgramme(await pruneFrontierNode(programme.id, nodeId, csrfToken)) } catch (failure) { fail(failure) }
  }

  const node = programme?.nodes.find((item) => item.id === selectedNode) ?? null
  const counts = programme ? LEGEND.map((key) => [key, programme.nodes.filter((item) => item.resolved_status === key).length] as const) : []

  return (
    <section className="frontier-workspace" aria-labelledby="frontier-title">
      <header className="frontier-heading">
        <div><span className="eyebrow accent">FRONTIER RESEARCH <span className="frontier-badge">Experimental</span></span><h2 id="frontier-title">Frontier programmes</h2>
          <p>Each programme turns an unsolved problem into a growing map: the literature is surveyed, gaps are named and attacked across fields, and stubborn gaps are broken into smaller steps. Every node shows how well it is supported.</p></div>
      </header>
      {programmes.length > 0 && <ul className="frontier-list" aria-label="Your frontier programmes">{programmes.map((item) => <li key={item.id}><button type="button" aria-pressed={programme?.id === item.id} onClick={() => void open(item.id)}><span className="frontier-list-status">{item.status}</span><span>{item.question}</span></button></li>)}</ul>}
      {programmes.length === 0 && !programme && <p className="frontier-empty">No programmes yet. Choose <strong>Frontier</strong> above, describe an unsolved problem and start one.</p>}
      {error && <p className="frontier-error" role="alert">{error}</p>}
      {programme && <div className="frontier-programme">
        <div className="frontier-programme-heading">
          <div><h3>{programme.question}</h3>{programme.success_criteria && <p className="frontier-criteria">Success looks like: {programme.success_criteria}</p>}</div>
          <div className="frontier-controls">
            {programme.status === 'active' && !running && <button type="button" className="frontier-primary" onClick={() => setRunning(true)} disabled={!csrfToken}>Run programme</button>}
            {programme.status === 'active' && running && <button type="button" onClick={() => void change('pause')}>Pause</button>}
            {programme.status === 'paused' && <button type="button" className="frontier-primary" onClick={() => void change('resume')}>Resume</button>}
            {(programme.status === 'active' || programme.status === 'paused') && <button type="button" onClick={() => void change('stop')}>Stop</button>}
          </div>
        </div>
        <p className="frontier-progress" role="status">{running ? <span className="frontier-live-dot" aria-hidden="true" /> : null}{status || (programme.status === 'active' ? 'Ready. Run the programme to research one step at a time; keep this tab open.' : `Programme ${programme.status}.`)} <span className="frontier-usage">{programme.attempts_used} of {programme.limits.max_attempts} investigations used · depth up to {programme.limits.max_depth}</span></p>
        <ul className="frontier-legend" aria-label="Frontier status legend">{counts.map(([key, count]) => <li key={key}><span className={`frontier-swatch status-${key}`} aria-hidden="true" />{STATUS_LABELS[key]} <strong>{count}</strong></li>)}</ul>
        <FrontierMap nodes={programme.nodes} selectedId={selectedNode} onSelect={setSelectedNode} />
        {node && <article className="frontier-detail" aria-label="Selected frontier node">
          <span className="eyebrow accent">{node.kind.toUpperCase()} · {STATUS_LABELS[node.resolved_status]}</span>
          <p className="frontier-detail-statement">{node.statement}</p>
          {node.pending_action && <p>Next: {ACTION_LABELS[node.pending_action].toLowerCase()}.</p>}
          {node.attempts.length > 0 && <ul className="frontier-attempts">{node.attempts.map((attempt) => <li key={attempt.run_id}><span>{ACTION_LABELS[attempt.action]}{attempt.outcome ? ` · ${attempt.outcome.replaceAll('_', ' ')}` : ' · in progress'}</span><button type="button" onClick={() => onOpenRun(attempt.run_id)}>Open investigation</button></li>)}</ul>}
          {node.kind !== 'question' && node.pending_action && <button type="button" onClick={() => void prune(node.id)}>Skip this branch</button>}
        </article>}
        <p className="frontier-note">Experimental. Supported means exact quotations from saved sources; derived and conjectured steps are model reasoning, clearly labelled. A solve path is only as strong as its weakest step.</p>
      </div>}
    </section>
  )
}
