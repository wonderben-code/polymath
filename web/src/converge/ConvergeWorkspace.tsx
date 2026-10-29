import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { OwnedRun } from '../research/client'
import { attachConvergeRun, changeConvergence, ConvergeApiError, dollars, extendConvergence, listConvergences, loadConvergence, nextConvergeStep } from './client'
import type { ConvergeProgramme, ConvergeStep, ConvergeSummary } from './client'
import { Enso } from './Enso'
import { convergeReport } from './report'
import './converge.css'

const POLL_MS = 4000
const SHOWN = 6

export interface ConvergeWorkspaceProps {
  enabled: boolean
  csrfToken: string | null
  canSubmit: boolean
  focusId: string | null
  submit: (mode: 'explore', goal: string, parentResultIds: readonly string[]) => Promise<OwnedRun | undefined>
  /** The latest recorded activity of the investigation now running, if any. */
  liveActivity?: { runId: string; summary: string } | null
  onOpenRun: (id: string) => void
  onGoDeeper?: (resultId: string) => void
  onSessionExpired: () => void
  onFinished?: (programme: ConvergeProgramme) => void
  notifyControl?: ReactNode
}

function message(code: string): string {
  if (code === 'session_expired') return 'Your browser session expired. Reload to continue.'
  if (code === 'converge_unavailable') return 'Converge is switched off on this server.'
  if (code === 'converge_finished') return 'This circle has finished.'
  if (code === 'invalid_converge_ceiling') return 'The new ceiling must be at least the current one, and at most $150.'
  return 'Converge could not be reached. Try again in a moment.'
}

const FINISHED: Record<string, string> = {
  converged: 'Finished: everything that held has been drawn inward.',
  no_connections: 'Finished: no connection held this time. Widening the circle can try again.',
  ceiling: 'Paused at your spending ceiling. Raise it to go further.',
  step_limit: 'Finished: the step limit was reached.',
}

function fieldNames(step: ConvergeStep, programme: ConvergeProgramme): string[] {
  return step.field_indices.map((index) => programme.fields[index]?.name).filter((name): name is string => !!name)
}

export function ConvergeWorkspace({ enabled, csrfToken, canSubmit, focusId, submit, liveActivity, onOpenRun, onGoDeeper, onSessionExpired, onFinished, notifyControl }: ConvergeWorkspaceProps) {
  const [circles, setCircles] = useState<ConvergeSummary[]>([])
  const [programme, setProgramme] = useState<ConvergeProgramme | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [addCount, setAddCount] = useState(10)
  const [ceilingDraft, setCeilingDraft] = useState('')
  const busy = useRef(false)
  const canSubmitRef = useRef(canSubmit)
  canSubmitRef.current = canSubmit

  const fail = useCallback((failure: unknown) => {
    const code = failure instanceof ConvergeApiError ? failure.code : 'unavailable'
    if (code === 'session_expired') onSessionExpired()
    setError(message(code))
  }, [onSessionExpired])

  const refreshList = useCallback(async () => {
    if (!enabled) return
    try { setCircles(await listConvergences()) } catch (failure) { fail(failure) }
  }, [enabled, fail])
  const opened = useRef(false)

  const open = useCallback(async (id: string) => {
    setError('')
    try { setProgramme(await loadConvergence(id)); setSelected(null) } catch (failure) { fail(failure) }
  }, [fail])

  useEffect(() => { void refreshList() }, [refreshList])
  // The most recent circle opens on its own, so returning finds it waiting.
  useEffect(() => {
    if (opened.current || focusId || programme || !circles.length) return
    opened.current = true
    void open(circles[0].id)
  }, [circles, focusId, programme, open])
  useEffect(() => { if (focusId) { void open(focusId); void refreshList(); setRunning(true) } }, [focusId, open, refreshList])

  // One step at a time: absorb finished work, then queue the next due step
  // through the ordinary investigation path, which applies every approval and
  // budget check. The circle only advances while this page is open.
  const tick = useCallback(async () => {
    if (!programme || !csrfToken || busy.current) return
    busy.current = true
    try {
      const { next, programme: updated } = await nextConvergeStep(programme.id, csrfToken)
      setProgramme(updated)
      if (next.state === 'due') {
        if (!canSubmitRef.current) { setStatus('Waiting until live research is available for this browser.'); return }
        setStatus(next.kind === 'thread' ? 'Choosing two fields that may share one structure…' : `Looking for what these connections share · level ${next.level}…`)
        const run = await submit('explore', next.goal, next.parent_result_ids)
        if (!run) { setRunning(false); setStatus('The next step could not be queued. Live research may be unavailable; the circle is saved.'); return }
        setProgramme(await attachConvergeRun(programme.id, csrfToken, { kind: next.kind, parent_result_ids: next.parent_result_ids, run_id: run.id }))
      } else if (next.state === 'waiting') {
        setStatus('')
      } else {
        setRunning(false)
        setStatus(next.state === 'completed' ? FINISHED[next.reason ?? ''] ?? 'Finished.' : next.state === 'paused' ? 'Paused.' : 'Stopped.')
        if (next.state === 'completed') { onFinished?.(updated); void refreshList() }
      }
    } catch (failure) {
      setRunning(false)
      fail(failure)
    } finally { busy.current = false }
  }, [programme, csrfToken, submit, fail, onFinished, refreshList])

  // The timer reads the latest step through a ref, so a step's own update
  // does not restart the effect.
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
      setProgramme(await changeConvergence(programme.id, csrfToken, kind))
      setRunning(kind === 'resume')
      setStatus(kind === 'pause' ? 'Paused.' : kind === 'stop' ? 'Stopped.' : '')
      void refreshList()
    } catch (failure) { fail(failure) }
  }

  async function extend(body: { add_count?: number; ceiling_microdollars?: number }) {
    if (!programme || !csrfToken) return
    setError('')
    try {
      setProgramme(await extendConvergence(programme.id, csrfToken, body))
      setRunning(true)
      setStatus('')
      void refreshList()
    } catch (failure) { fail(failure) }
  }

  function raiseCeiling() {
    const value = Number(ceilingDraft)
    if (!Number.isFinite(value) || value <= 0) return
    void extend({ ceiling_microdollars: Math.round(value * 1_000_000) })
    setCeilingDraft('')
  }

  function download(current: ConvergeProgramme) {
    const url = URL.createObjectURL(new Blob([convergeReport(current)], { type: 'text/markdown;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = `converge-report-${current.id.slice(0, 8)}.md`
    link.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  if (!programme && circles.length === 0) return null
  const steps = programme?.steps ?? []
  const byId = new Map(steps.map((step) => [step.id, step]))
  const runningStep = steps.find((step) => step.status === 'running')
  const held = steps.filter((step) => step.status === 'held')
  const tested = steps.filter((step) => step.kind === 'thread' && step.status !== 'running').length
  const touched = programme?.fields.filter((field) => field.touched).length ?? 0
  const depth = Math.max(0, ...held.map((step) => step.level))
  const deepest = programme ? programme.deepest_step_ids.map((id) => byId.get(id)).filter((step): step is ConvergeStep => !!step && step.kind === 'meta') : []
  const levels = [...new Set(held.map((step) => step.level))].sort((a, b) => b - a)
  const focus = selected ? byId.get(selected) : undefined
  const narration = runningStep
    ? liveActivity && liveActivity.runId === runningStep.run_id ? liveActivity.summary : runningStep.kind === 'thread' ? 'Meta’s model is choosing two fields that may share one structure…' : `Looking for what these connections share · level ${runningStep.level}`
    : status

  return (
    <section className="converge-workspace" aria-labelledby="converge-title">
      <h2 className="converge-title" id="converge-title">Your circles</h2>
      {circles.length > 1 && <ul className="converge-list" aria-label="Your converge circles">{circles.map((item) => <li key={item.id}><button type="button" aria-pressed={programme?.id === item.id} onClick={() => void open(item.id)}><span>{item.theme ?? 'An open circle'}</span><span className="converge-list-meta">{item.field_count} fields · {item.connections_held} holding</span></button></li>)}</ul>}
      {error && <p className="converge-error" role="alert">{error}</p>}
      {programme && <div className="converge-programme">
        <div className="converge-heading">
          <h3>{programme.theme ?? `An open circle of ${programme.fields.length} fields`}</h3>
          <div className="converge-controls">
            {programme.status === 'active' && !running && <button type="button" className="converge-primary" onClick={() => setRunning(true)} disabled={!csrfToken}>Continue</button>}
            {programme.status === 'active' && running && <button type="button" onClick={() => void change('pause')}>Pause</button>}
            {programme.status === 'paused' && <button type="button" className="converge-primary" onClick={() => void change('resume')}>Continue</button>}
            {(programme.status === 'active' || programme.status === 'paused') && <button type="button" onClick={() => void change('stop')}>Stop</button>}
          </div>
        </div>
        <p className="converge-narration" role="status">{running && runningStep && <span className="converge-live-dot" aria-hidden="true" />}{narration || (programme.status === 'active' ? 'Ready. Continue to let Polymath work through the circle; keep this tab open.' : programme.status === 'completed' ? FINISHED[programme.finished_reason ?? ''] ?? 'Finished.' : `Circle ${programme.status}.`)}</p>
        {running && notifyControl}
        <Enso programme={programme} selectedId={selected} onSelect={setSelected} seeking={running && !!runningStep && runningStep.kind === 'thread'} />
        <p className="converge-tally"><span><strong>{touched}</strong> of {programme.fields.length} fields connected</span><span><strong>{tested}</strong> tested</span><span><strong>{held.length}</strong> holding</span><span><strong>{depth}</strong> {depth === 1 ? 'level' : 'levels'} deep</span><span>{dollars(programme.spent_microdollars)} of {dollars(programme.ceiling_microdollars)}</span></p>

        {focus && <article className="converge-detail" aria-label="Selected connection">
          <span className="converge-kicker">{focus.kind === 'thread' ? 'A connection between two fields' : `Level ${focus.level} · what ${focus.premise_step_ids.length} connections share`}</span>
          <h4>{focus.title ?? (focus.status === 'running' ? 'Being tested now…' : 'This one did not hold')}</h4>
          <p className="converge-fields-line">{fieldNames(focus, programme).join(' · ')}</p>
          <div className="converge-detail-actions">
            <button type="button" onClick={() => onOpenRun(focus.run_id)}>Read the evidence</button>
            {focus.status === 'held' && focus.result_id && onGoDeeper && <button type="button" onClick={() => onGoDeeper(focus.result_id!)}>Go deeper here</button>}
          </div>
        </article>}

        {deepest.length > 0 && <section className="converge-deepest" aria-labelledby="converge-deepest-title">
          <span className="converge-kicker" id="converge-deepest-title">{programme.status === 'completed' && deepest.length === 1 ? 'The deepest convergence' : 'Deepest so far'}</span>
          {deepest.map((step) => <button type="button" key={step.id} onClick={() => setSelected(step.id)}><span className="converge-deepest-title">{step.title}</span><span className="converge-fields-line">Unites {fieldNames(step, programme).length} fields · level {step.level}</span></button>)}
        </section>}

        {levels.length > 0 && <section className="converge-levels" aria-label="Connections by level">
          {levels.map((level) => <div key={level} className="converge-level">
            <h4>{level === 1 ? 'Connections between two fields' : `Level ${level}`}<span>{held.filter((step) => step.level === level).length}</span></h4>
            {(() => {
              const items = held.filter((step) => step.level === level)
              const item = (step: ConvergeStep) => <li key={step.id}><button type="button" aria-pressed={selected === step.id} onClick={() => setSelected(step.id)}><span>{step.title}</span><span className="converge-fields-line">{step.kind === 'thread' ? `${step.field_left} · ${step.field_right}` : `${fieldNames(step, programme).length} fields`}</span></button></li>
              return <>
                <ul>{items.slice(0, SHOWN).map(item)}</ul>
                {items.length > SHOWN && <details className="converge-show-all"><summary>Show all {items.length}</summary><ul>{items.slice(SHOWN).map(item)}</ul></details>}
              </>
            })()}
          </div>)}
        </section>}

        {programme.status !== 'stopped' && <details className="converge-more">
          <summary>Widen the circle or go further</summary>
          <div className="converge-more-row">
            <label>Add <input type="number" min={1} max={200} value={addCount} onChange={(event) => setAddCount(Math.max(1, Math.min(200, Number(event.target.value) || 1)))} /> more fields</label>
            <button type="button" onClick={() => void extend({ add_count: addCount })} disabled={!csrfToken || programme.fields.length >= 240}>Widen</button>
          </div>
          <div className="converge-more-row">
            <label>Raise the ceiling to $<input type="number" min={0.1} max={150} step="any" value={ceilingDraft} placeholder={String(programme.ceiling_microdollars / 1_000_000)} onChange={(event) => setCeilingDraft(event.target.value)} /></label>
            <button type="button" onClick={raiseCeiling} disabled={!csrfToken || !ceilingDraft}>Raise</button>
          </div>
        </details>}

        <details className="converge-list-view">
          <summary>Read the circle as a list</summary>
          <ol className="converge-field-list" aria-label="Fields on the circle">{programme.fields.map((field, index) => <li key={index}><span className={`converge-dot${field.touched ? ' is-touched' : ''}`} aria-hidden="true" />{field.name}<span className="converge-origin">{field.origin === 'chosen' ? 'your choice' : field.origin === 'found' ? 'found along the way' : ''}{field.touched ? ' · connected' : ''}</span></li>)}</ol>
        </details>
        <p className="converge-note"><span className="converge-badge">Experimental</span> A connection holds only when exact quotations from real papers support it and it survives its strongest objection. Deeper levels are model reasoning across those connections, tested against the same original evidence. <button type="button" className="text-button" onClick={() => download(programme)}>Download report</button></p>
      </div>}
    </section>
  )
}
