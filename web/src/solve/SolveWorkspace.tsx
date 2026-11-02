import { useCallback, useEffect, useRef, useState } from 'react'
import type { OwnedRun } from '../research/client'
import { attachSolveRun, changeSolve, listSolves, loadSolve, nextSolveStep, SolveApiError } from './client'
import type { SolveProgramme, SolveStep, SolveSummary } from './client'
import { LockAndKey } from './LockAndKey'
import './solve.css'

const POLL_MS = 6000
const RETRY_HOLD_MS = 30000
const MAX_HOLDS = 10
const TRANSIENT = new Set(['unavailable', 'rate_limited', 'invalid_response', 'aborted'])
const STAGE_WORDS = { find: 'Looking for an existing key anywhere in science', fit: 'Searching other fields for close fits', forge: 'Forging a new key from the close fits' }
const ANSWER_ONLY_WORDS = { title: 'An answer exists; no close fit was finished', line: 'The literature holds an answer with open gaps, shown below. No search for a close fit from another field could be finished, so nothing was forged.' }
const OUTCOME_WORDS = {
  found: { title: 'A key already exists', line: 'This problem has an answer in the literature. Here it is, with the passages behind it.' },
  forged: { title: 'A new key was forged', line: 'No existing answer fitted, so Polymath combined the closest answers from different fields into a new one. It is a proposal built from the evidence, not yet independently verified.' },
  close: { title: 'Close fits found', line: 'Some fields hold answers that almost fit. Here is where each fits, and where it falls short.' },
  none: { title: 'No key yet', line: 'Nothing in the literature fits this problem yet. That is a finding in itself: it may be genuinely open. Frontier can break it into smaller steps.' },
}

export interface SolveWorkspaceProps {
  enabled: boolean
  csrfToken: string | null
  canSubmit: boolean
  /** Present while the allowance poll has paused; a running programme calls it so live research is checked again. */
  refreshAccess?: () => void
  /** Cancels an investigation this programme created but could not attach. */
  cancelRun?: (runId: string) => Promise<void>
  focusId: string | null
  submit: (mode: 'ask' | 'explore', goal: string, parentResultIds: readonly string[], options?: { collection?: boolean }) => Promise<OwnedRun | undefined>
  onOpenRun: (id: string) => void
  onSessionExpired: () => void
}

function message(code: string): string {
  if (code === 'session_expired') return 'Your browser session expired. Reload to continue.'
  if (code === 'solve_unavailable') return 'Solve is switched off on this server.'
  if (code === 'solve_finished') return 'This problem has finished.'
  if (code === 'rate_limited') return 'Polymath is pacing requests from this browser; it continues in a moment.'
  return 'Solve could not be reached. Try again in a moment.'
}

function StepCard({ step, index, onOpenRun }: { step: SolveStep; index: number; onOpenRun: (id: string) => void }) {
  const finding = step.finding
  return (
    <li className={`solve-card is-${step.outcome === 'bridge' ? 'fit' : step.status}`}>
      {finding?.kind === 'bridge' && <>
        <span className="solve-card-kicker" style={{ ['--field' as string]: ['var(--violet)', 'var(--blue)', 'var(--teal)', 'var(--green)', 'var(--amber)', 'var(--rose)'][index % 6] }}>{finding.field_left} · {finding.field_right}</span>
        <h4>{finding.title}</h4>
        <p><strong>Where it fits:</strong> {finding.explanation}</p>
        <p><strong>Where it falls short:</strong> {finding.falls_short}</p>
      </>}
      {finding?.kind === 'answer' && <>
        <h4>{finding.summary}</h4>
        {finding.known.length > 0 && <ul>{finding.known.map((claim) => <li key={claim}>{claim}</li>)}</ul>}
        {finding.unsolved.length > 0 && <p><strong>Still unsolved:</strong> {finding.unsolved.join(' ')}</p>}
      </>}
      {finding?.kind === 'insufficient_evidence' && <p className="solve-muted">No fit was supported by the evidence here.</p>}
      {!finding && <p className="solve-muted">{step.status === 'running' ? 'In progress…' : step.status === 'failed' ? 'This search could not finish.' : 'Waiting its turn.'} <span className="solve-target">{step.target}</span></p>}
      {step.run_id && <button type="button" className="text-button" onClick={() => onOpenRun(step.run_id!)}>Open the investigation</button>}
    </li>
  )
}

export function SolveWorkspace({ enabled, csrfToken, canSubmit, refreshAccess, cancelRun, focusId, submit, onOpenRun, onSessionExpired }: SolveWorkspaceProps) {
  const [programmes, setProgrammes] = useState<SolveSummary[]>([])
  const [programme, setProgramme] = useState<SolveProgramme | null>(null)
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
  const cancelRunRef = useRef(cancelRun)
  cancelRunRef.current = cancelRun
  const refreshedAt = useRef(0)

  const fail = useCallback((failure: unknown) => {
    const code = failure instanceof SolveApiError ? failure.code : 'unavailable'
    if (code === 'session_expired') onSessionExpired()
    setError(message(code))
  }, [onSessionExpired])

  const refreshList = useCallback(async () => {
    if (!enabled) return
    try { setProgrammes(await listSolves()) } catch (failure) { fail(failure) }
  }, [enabled, fail])

  const open = useCallback(async (id: string) => {
    setError('')
    try { const loaded = await loadSolve(id); setProgramme(loaded); if (loaded.status === 'active' && loaded.steps.some((step) => step.status === 'running')) setRunning(true) } catch (failure) { fail(failure) }
  }, [fail])

  useEffect(() => { void refreshList() }, [refreshList])
  useEffect(() => { if (focusId) { void open(focusId); void refreshList(); setRunning(true) } }, [focusId, open, refreshList])

  // One stage at a time through the ordinary investigation path, which
  // applies every approval and budget check. It advances while this page is open.
  const tick = useCallback(async () => {
    if (!programme || !csrfToken || busy.current) return
    if (Date.now() < holdUntil.current) return
    busy.current = true
    try {
      const { next, programme: updated } = await nextSolveStep(programme.id, csrfToken)
      setProgramme(updated)
      if (next.state === 'due') {
        if (!canSubmitRef.current) {
          setStatus('Waiting until live research is available for this browser.')
          // The allowance poll pauses after ten minutes without a visitor action; a running programme is that action.
          if (refreshAccessRef.current && Date.now() - refreshedAt.current >= 30000) { refreshedAt.current = Date.now(); refreshAccessRef.current() }
          return
        }
        if (Date.now() < holdUntil.current) return
        setStatus(`${STAGE_WORDS[next.stage]}…`)
        const run = await submitRef.current(next.mode, next.goal, next.parent_result_ids, { collection: false })
        if (!run) {
          // A step that could not be started (a blip, a restart, a rate limit) is tried again shortly; the programme stops only when it keeps failing.
          holds.current += 1; holdUntil.current = Date.now() + RETRY_HOLD_MS
          if (holds.current >= MAX_HOLDS) { setRunning(false); setStatus('The next search could not be started. Live research may be unavailable; your problem is saved.'); return }
          setStatus('The next search could not be started yet. Trying again shortly; your problem is saved.'); return
        }
        holds.current = 0
        let attached
        try { attached = await attachSolveRun(programme.id, csrfToken, { step_id: next.step_id, stage: next.stage, run_id: run.id }) } catch (failure) {
          // The programme changed under the step (paused or stopped); the investigation it created must not run on alone.
          void cancelRunRef.current?.(run.id)
          throw failure
        }
        setProgramme(attached)
      } else if (next.state === 'waiting') {
        const stage = updated.steps.find((item) => item.status === 'running')?.stage
        setStatus(stage ? `${STAGE_WORDS[stage]}…` : 'Working…')
      } else {
        setRunning(false); setStatus('')
        if (next.state === 'completed') void refreshList()
      }
    } catch (failure) { // A passing failure (the service restarting, a blip, a paced request) holds and tries again; a lost session or a persistent failure stops the programme.
      if (failure instanceof SolveApiError && TRANSIENT.has(failure.code) && holds.current + 1 < MAX_HOLDS) { holds.current += 1; holdUntil.current = Date.now() + RETRY_HOLD_MS; setStatus('Polymath could not be reached. Trying again shortly.'); return }
      setRunning(false); fail(failure) } finally { busy.current = false }
  }, [programme, csrfToken, fail, refreshList])

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
    try { setProgramme(await changeSolve(programme.id, csrfToken, kind)); setRunning(kind === 'resume'); void refreshList() } catch (failure) { fail(failure) }
  }

  const find = programme?.steps.find((step) => step.stage === 'find')
  const fits = programme?.steps.filter((step) => step.stage === 'fit') ?? []
  const forge = programme?.steps.find((step) => step.stage === 'forge')
  // "Close fits" is honest only when a fit held; an answer with open gaps and no finished fit says so.
  const fitsHeld = !!programme && programme.steps.some((step) => step.stage === 'fit' && step.outcome === 'bridge')
  const outcome = !programme?.outcome ? null : programme.outcome === 'close' && !fitsHeld ? ANSWER_ONLY_WORDS : OUTCOME_WORDS[programme.outcome]
  return (
    <section className="solve" aria-labelledby="solve-title">
      <h2 id="solve-title" className="solve-heading">Solve</h2>
      {error && <p className="composer-alert" role="alert">{error}</p>}
      {programme && <article className="solve-programme" aria-labelledby="solve-problem-title">
        <p className="solve-kicker">The lock</p>
        <h3 id="solve-problem-title">{programme.problem}</h3>
        <LockAndKey programme={programme} />
        {outcome ? <div className={`solve-outcome is-${programme.outcome}`}><h3>{outcome.title}</h3><p>{outcome.line}</p></div> : <p className="solve-status" role="status">{status || (programme.status === 'active' ? 'Ready to continue.' : `Solve ${programme.status}.`)}</p>}
        {programme.status === 'active' && !running && !outcome && <button type="button" className="primary-button" disabled={!canSubmit} onClick={() => setRunning(true)}>Continue solving</button>}
        {programme.status === 'active' && running && <button type="button" className="text-button" onClick={() => void change('pause')}>Pause</button>}
        {programme.status === 'paused' && <button type="button" className="text-button" onClick={() => void change('resume')}>Resume</button>}
        {programme.outcome === 'forged' && forge && <section className="solve-stage" aria-labelledby="solve-forged"><h4 id="solve-forged" className="solve-stage-title">The forged key</h4><ul className="solve-cards"><StepCard step={forge} index={0} onOpenRun={onOpenRun} /></ul></section>}
        {find && <section className="solve-stage" aria-labelledby="solve-existing"><h4 id="solve-existing" className="solve-stage-title">{programme.outcome === 'found' ? 'The key that already exists' : 'What already exists'}</h4><ul className="solve-cards"><StepCard step={find} index={0} onOpenRun={onOpenRun} /></ul></section>}
        {fits.length > 0 && <section className="solve-stage" aria-labelledby="solve-fits"><h4 id="solve-fits" className="solve-stage-title">Close fits from other fields</h4><ul className="solve-cards">{fits.map((step, index) => <StepCard key={step.id} step={step} index={index} onOpenRun={onOpenRun} />)}</ul></section>}
      </article>}
      {programmes.length > 0 && <nav className="solve-list" aria-label="Your problems"><h3>Your problems</h3><ul>{programmes.map((item) => <li key={item.id}><button type="button" aria-pressed={programme?.id === item.id} onClick={() => void open(item.id)}><span>{item.problem}</span><span className="solve-muted">{item.outcome ? OUTCOME_WORDS[item.outcome].title : item.status === 'active' ? 'In progress' : item.status}</span></button></li>)}</ul></nav>}
    </section>
  )
}
