import { NativeResumeControl } from '../research/NativeResumeControl'
import { useCallback, useEffect, useRef, useState } from 'react'
import { AssessmentError, assessmentHistory, assessmentMatchesJob, cancelAssessmentJob, createAssessmentJob, getAssessment, getAssessmentJob, listAssessmentJobs, loadAssessmentCapabilities } from './client'
import type { AssessmentCapabilities, AssessmentJob, AssessmentRecord } from './types'
import type { WatchSource } from '../ingestion/client'
import { AssessmentPreview, dispositionNames } from './AssessmentPreview'
import { FindingVersionNotice } from './FindingVersionNotice'
import { useFindingHead } from './useFindingHead'
import { useAssessmentActivity } from './useAssessmentActivity'
import { useNativeResearchSubmission } from '../research/nativeContext'
import './assessments.css'

interface Props {
  watchId: string
  sourceResultId: string
  sources: WatchSource[]
  csrfToken: string | null
  enabled: boolean
  canSubmit?: boolean
  onSessionExpired: () => void
  onAssessmentChanged?: (record: AssessmentRecord) => void
  onBusyChange?: (busy: boolean) => void
  onOpenFinding?: (id: string) => void
}
const BLOCKING = new Set(['queued', 'running', 'waiting_retry', 'cancel_requested', 'interrupted'])
const operational = new Set(['checkpoint', 'external_started', 'external_observed', 'external_reconciled'])
function latest(previous: AssessmentJob | undefined, incoming: AssessmentJob): AssessmentJob {
  if (!previous || Date.parse(incoming.updated_at) > Date.parse(previous.updated_at)) return incoming
  if (Date.parse(incoming.updated_at) < Date.parse(previous.updated_at) || previous.status === 'cancel_requested' && ['queued', 'running', 'waiting_retry'].includes(incoming.status)) return previous
  if (['succeeded', 'failed', 'cancelled'].includes(previous.status) && previous.status !== incoming.status) return previous
  return incoming
}
function failureMessage(error: unknown): string {
  if (!(error instanceof AssessmentError)) return 'The saved assessment could not be checked. Reload its history before continuing.'
  if (error.code === 'resource_limited') return `A request or visitor allowance limit was reached. Wait${error.retryAfterSeconds === null ? '' : ` at least ${error.retryAfterSeconds} seconds`} before trying again. No reassessment was retried.`
  if (['queue_limit', 'research_queue_full', 'research_session_queue_full'].includes(error.code)) return 'A research, writing, or assessment task is already active, or the queue is full. Finish or cancel existing work before starting another.'
  if (['assessment_changed', 'assessment_source_changed', 'assessment_head_changed'].includes(error.code)) return 'The finding or its current head changed. Reload the exact versions and review the selection again.'
  if (['not_found', 'assessment_not_found', 'assessment_source_not_found', 'assessment_not_available'].includes(error.code)) return 'This finding, assessment, or saved source is unavailable in this browser session.'
  if (error.code === 'research_unavailable') return 'Meta reassessment is disabled or unavailable. Saved assessments remain readable.'
  if (error.code === 'invalid_request' || error.code === 'invalid_assessment_request') return 'Select one to five exact source versions from this watch and review the finding version.'
  return 'The saved assessment could not be checked. Reload its history before continuing.'
}

export function AssessmentWorkspace({ watchId, sourceResultId, sources, csrfToken, enabled, canSubmit = true, onSessionExpired, onAssessmentChanged, onBusyChange, onOpenFinding }: Props) {
  const native = useNativeResearchSubmission()
  const [capabilities, setCapabilities] = useState<AssessmentCapabilities | null>(null)
  const [jobs, setJobs] = useState<AssessmentJob[]>([])
  const [history, setHistory] = useState<AssessmentRecord[]>([])
  const [nextJobs, setNextJobs] = useState<string | null>(null)
  const [nextHistory, setNextHistory] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [activeJob, setActiveJob] = useState<AssessmentJob | null>(null)
  const [record, setRecord] = useState<AssessmentRecord | null>(null)
  const [chosen, setChosen] = useState<string[]>([])
  const [consent, setConsent] = useState(false)
  const [phase, setPhase] = useState<'loading' | 'ready' | 'error' | 'expired'>('loading')
  const [error, setError] = useState('')
  const [uncertain, setUncertain] = useState(false)
  const [action, setAction] = useState<'create' | 'cancel' | 'history' | null>(null)
  const [refresh, setRefresh] = useState(0)
  const [headRefresh, setHeadRefresh] = useState(0)
  const [detailRefresh, setDetailRefresh] = useState(0)
  const scope = useRef(0), selected = useRef<string | null>(null)
  const mutation = useRef<AbortController | null>(null)
  const reads = useRef(new Set<AbortController>())
  const known = useRef(new Map<string, AssessmentJob>())
  const notified = useRef(new Set<string>())
  const blocked = useRef(false)
  const expireCallback = useRef(onSessionExpired), changeCallback = useRef(onAssessmentChanged)
  expireCallback.current = onSessionExpired; changeCallback.current = onAssessmentChanged
  const clear = useCallback(() => {
    setCapabilities(null); setJobs([]); setHistory([]); setNextJobs(null); setNextHistory(null); setSelectedId(null); selected.current = null
    setActiveJob(null); setRecord(null); setChosen([]); setConsent(false); setUncertain(false); setAction(null); setError(''); known.current.clear(); notified.current.clear()
  }, [])
  const expire = useCallback(() => {
    if (blocked.current) return
    blocked.current = true
    scope.current++; mutation.current?.abort(); mutation.current = null; reads.current.forEach((controller) => controller.abort()); clear(); setPhase('expired'); expireCallback.current()
  }, [clear])
  const fail = useCallback((failure: unknown) => {
    setConsent(false)
    if (failure instanceof AssessmentError && failure.code === 'native_authority_changed') { native.invalidate(); setError('Native research authority changed or expired. Refresh the current activation; this reassessment was not retried.'); return }
    if (failure instanceof AssessmentError && failure.code === 'session_expired') { expire(); return }
    setError(failureMessage(failure))
    if (failure instanceof AssessmentError && failure.uncertain) setUncertain(true)
  }, [expire, native.invalidate])
  const head = useFindingHead({ resultId: sourceResultId, enabled: enabled && !!csrfToken && phase !== 'expired', onSessionExpired: expire, refreshToken: headRefresh })
  const headRoot = useRef<string | null>(null)
  headRoot.current = head.head?.root_result_id ?? null

  const remember = useCallback((incoming: AssessmentJob) => { const job = latest(known.current.get(incoming.id), incoming); known.current.set(job.id, job); return job }, [])
  const acceptRecord = useCallback((incoming: AssessmentRecord, job: AssessmentJob) => {
    if (!assessmentMatchesJob(incoming, job)) throw new AssessmentError('invalid_response')
    setRecord(incoming)
    setHistory((previous) => previous.some((item) => item.id === incoming.id) ? previous.map((item) => item.id === incoming.id ? incoming : item) : [incoming, ...previous])
    if (!notified.current.has(incoming.id)) {
      notified.current.add(incoming.id); setHeadRefresh((previous) => previous + 1); changeCallback.current?.(incoming)
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController(), generation = ++scope.current
    blocked.current = false; clear(); setPhase('loading')
    if (!enabled || !csrfToken) return () => controller.abort()
    reads.current.add(controller)
    Promise.all([loadAssessmentCapabilities(controller.signal), listAssessmentJobs(sourceResultId, controller.signal)]).then(([caps, saved]) => {
      if (controller.signal.aborted || generation !== scope.current) return
      const incoming = saved.jobs.map(remember)
      setCapabilities(caps); setJobs(incoming); setNextJobs(saved.next_cursor); setPhase('ready'); setUncertain(false)
      const target = incoming[0]?.id ?? null; selected.current = target; setSelectedId(target)
    }).catch((failure: unknown) => { if (!controller.signal.aborted && generation === scope.current) { setPhase('error'); fail(failure) } }).finally(() => reads.current.delete(controller))
    return () => { controller.abort(); reads.current.delete(controller); scope.current++; mutation.current?.abort(); reads.current.forEach((read) => read.abort()) }
  }, [enabled, csrfToken, sourceResultId, watchId, refresh, clear, fail, remember])

  useEffect(() => {
    if (!enabled || phase !== 'ready' || !head.head?.root_result_id) return
    const controller = new AbortController(), generation = scope.current; reads.current.add(controller)
    assessmentHistory(head.head.root_result_id, controller.signal).then((saved) => {
      if (!controller.signal.aborted && generation === scope.current) { setHistory(saved.assessments); setNextHistory(saved.next_cursor) }
    }).catch((failure: unknown) => { if (!controller.signal.aborted && generation === scope.current) fail(failure) }).finally(() => reads.current.delete(controller))
    return () => { controller.abort(); reads.current.delete(controller) }
  }, [enabled, phase, head.head?.root_result_id, head.head?.revision, refresh, fail])

  const loadSelected = useCallback(async () => {
    const id = selected.current
    if (!enabled || !csrfToken || !id || phase !== 'ready') return
    const controller = new AbortController(), generation = scope.current; reads.current.add(controller)
    try {
      const incoming = await getAssessmentJob(id, controller.signal)
      if (controller.signal.aborted || generation !== scope.current || selected.current !== id) return
      if (headRoot.current && incoming.root_result_id !== headRoot.current) throw new AssessmentError('invalid_response')
      const job = remember(incoming)
      setActiveJob(job); setJobs((previous) => previous.some((item) => item.id === job.id) ? previous.map((item) => item.id === job.id ? job : item) : [job, ...previous])
      if (job.assessment_id) {
        const result = await getAssessment(job.assessment_id, controller.signal)
        if (!controller.signal.aborted && generation === scope.current && selected.current === id) acceptRecord(result, job)
      }
    } catch (failure) { if (!controller.signal.aborted && generation === scope.current && selected.current === id) fail(failure) }
    finally { reads.current.delete(controller) }
  }, [enabled, csrfToken, phase, remember, acceptRecord, fail])

  useEffect(() => { setActiveJob(null); setRecord(null); if (selectedId) void loadSelected() }, [selectedId, detailRefresh, loadSelected])
  const activity = useAssessmentActivity({ runId: selectedId, enabled: enabled && phase === 'ready', onStatus: loadSelected, onSessionExpired: expire })
  const busy = action === 'create' || jobs.some((job) => BLOCKING.has(job.status))
  useEffect(() => { onBusyChange?.(busy); return () => onBusyChange?.(false) }, [busy, onBusyChange])
  const sourceScope = sources.map((source) => `${source.id}:${source.content_sha256}`).join(',')
  const selectedScope = chosen.map((id) => `${id}:${sources.find((source) => source.id === id)?.content_sha256 ?? ''}`).join(',')
  useEffect(() => { const valid = chosen.filter((id) => sources.some((source) => source.id === id)); if (valid.length !== chosen.length) { setChosen(valid); setConsent(false) } }, [sourceScope, chosen, sources])
  useEffect(() => setConsent(false), [selectedScope])
  useEffect(() => setConsent(false), [head.head?.revision, head.head?.current_result_id, head.head?.state])

  const headReady = head.phase === 'ready' && !head.pollPaused && !!head.head?.is_current
  const canChoose = enabled && phase === 'ready' && !!csrfToken && !!capabilities?.execution_available && canSubmit && native.canSubmit && headReady && !busy && !action && !uncertain
  const canCreate = canChoose && consent && chosen.length >= 1 && chosen.length <= 5 && chosen.every((id) => sources.some((source) => source.id === id))
  function choose(id: string) {
    if (!canChoose) return
    setConsent(false); setChosen((previous) => previous.includes(id) ? previous.filter((selected) => selected !== id) : previous.length < 5 ? [...previous, id] : previous)
  }
  function selectJob(id: string) { selected.current = id; setSelectedId(id); setRecord(null); setActiveJob(null); setDetailRefresh((previous) => previous + 1); setError('') }
  function reload() { setConsent(false); setHeadRefresh((previous) => previous + 1); setRefresh((previous) => previous + 1) }
  async function create() {
    if (!canCreate || mutation.current || !csrfToken || !head.head) return
    const controller = new AbortController(), generation = scope.current; mutation.current = controller; setAction('create'); setConsent(false); setError('')
    try {
      const incoming = await createAssessmentJob({ source_result_id: sourceResultId, watch_id: watchId, new_source_version_ids: [...chosen], expected_head_revision: head.head.revision }, csrfToken, controller.signal, native.submission ?? undefined)
      if (controller.signal.aborted || generation !== scope.current) return
      const job = remember(incoming); setJobs((previous) => [job, ...previous.filter((item) => item.id !== job.id)]); setChosen([]); selectJob(job.id)
    } catch (failure) { if (!controller.signal.aborted && generation === scope.current) fail(failure) }
    finally { if (mutation.current === controller) { mutation.current = null; setAction(null) } }
  }
  async function cancel() {
    if (!enabled || !csrfToken || !activeJob || !BLOCKING.has(activeJob.status) || mutation.current || uncertain) return
    const controller = new AbortController(), generation = scope.current; mutation.current = controller; setAction('cancel'); setError('')
    try {
      await cancelAssessmentJob(activeJob.id, csrfToken, controller.signal)
      if (!controller.signal.aborted && generation === scope.current) await loadSelected()
    } catch (failure) { if (!controller.signal.aborted && generation === scope.current) fail(failure) }
    finally { if (mutation.current === controller) { mutation.current = null; setAction(null) } }
  }
  async function older(kind: 'jobs' | 'assessments') {
    const cursor = kind === 'jobs' ? nextJobs : nextHistory
    if (!enabled || !cursor || !head.head || mutation.current || phase !== 'ready') return
    const controller = new AbortController(), generation = scope.current; mutation.current = controller; setAction('history')
    try {
      if (kind === 'jobs') { const saved = await listAssessmentJobs(sourceResultId, controller.signal, cursor); if (!controller.signal.aborted && generation === scope.current) { setJobs((previous) => [...previous, ...saved.jobs.filter((item) => !previous.some((prior) => prior.id === item.id))]); setNextJobs(saved.next_cursor === cursor ? null : saved.next_cursor) } }
      else { const saved = await assessmentHistory(head.head.root_result_id, controller.signal, cursor); if (!controller.signal.aborted && generation === scope.current) { setHistory((previous) => [...previous, ...saved.assessments.filter((item) => !previous.some((prior) => prior.id === item.id))]); setNextHistory(saved.next_cursor === cursor ? null : saved.next_cursor) } }
    } catch (failure) { if (!controller.signal.aborted && generation === scope.current) fail(failure) }
    finally { if (mutation.current === controller) { mutation.current = null; setAction(null) } }
  }

  return <section className="assessment-workspace" aria-label="Reassess this finding with new evidence">
    <div className="assessment-row"><h5>Does the finding still hold?</h5><button type="button" onClick={reload} disabled={!enabled || !!action || phase === 'loading' || phase === 'expired'}>Reload assessments</button></div>
    <p>Choose one to five saved source versions from this watch. An explicit Meta assessment compares them with the exact earlier finding and its original evidence.</p>
    <FindingVersionNotice head={head.head} phase={head.phase} pollPaused={head.pollPaused} onRefresh={head.refresh} onOpenFinding={onOpenFinding} />
    {phase === 'loading' && <p role="status">Loading saved reassessments…</p>}
    {phase === 'expired' && <p role="alert">The browser session expired or changed. Private reassessments and source selections have been cleared.</p>}
    {phase === 'error' && <p className="assessment-warning">Saved assessment history could not be loaded. Reload it before starting work.</p>}
    {error && <p role="alert" className="assessment-warning">{error}</p>}
    {uncertain && <p role="alert" className="assessment-warning">The last request’s outcome is unknown. It may already have been queued. Reload saved assessments before making another change.</p>}
    {capabilities && !capabilities.execution_available && <p className="assessment-warning" role="status">Meta reassessment is disabled or unavailable. Literature watches do not start model calls automatically. Saved assessments remain readable.</p>}
    {head.head && !head.head.is_current && <p className="assessment-warning">This watch is linked to an earlier finding version. Open the current finding and create a watch linked to that exact version before a new reassessment.</p>}
    <fieldset disabled={!canChoose}><legend>Select exact saved source versions · {chosen.length} of 5</legend>{sources.length ? <ul className="assessment-source-choices">{sources.map((source) => <li key={source.id}><label><input type="checkbox" checked={chosen.includes(source.id)} disabled={!canChoose || chosen.length >= 5 && !chosen.includes(source.id)} onChange={() => choose(source.id)} aria-label={`Reassess with source: ${source.title}`} /><span>{source.title}<small>{source.content_kind === 'abstract' ? 'Abstract only' : 'Full-text snapshot'} · observed {new Date(source.first_seen_at).toLocaleString()}</small></span></label></li>)}</ul> : <p>No saved source versions are available to select. Provider reading and model reassessment are separate steps.</p>}</fieldset>
    <label className="assessment-consent"><input type="checkbox" checked={consent} disabled={!canChoose || !chosen.length} onChange={(event) => setConsent(event.target.checked)} /><span>I want Meta to reassess this exact finding using the selected saved source versions.<small className="assessment-note">This starts a model task and may use API credits. No reassessment starts from a provider check, page refresh, or saved selection.</small></span></label>
    <button type="button" className="assessment-primary" onClick={() => { void create() }} disabled={!canCreate}>{action === 'create' ? 'Queuing Meta reassessment…' : 'Reassess selected evidence'}</button>
    {jobs.length > 0 && <nav className="assessment-history" aria-label="Reassessment jobs"><ul>{jobs.map((job) => <li key={job.id}><button type="button" aria-pressed={selectedId === job.id} disabled={!!action} onClick={() => selectJob(job.id)}><strong>{job.status.replaceAll('_', ' ')}</strong><small>{new Date(job.created_at).toLocaleString()}</small></button></li>)}</ul>{nextJobs && <button type="button" disabled={!!action} onClick={() => { void older('jobs') }}>Load earlier assessment jobs</button>}</nav>}
    {activeJob && <section className="assessment-status" aria-label="Selected reassessment status"><div className="assessment-row"><strong role="status">Meta reassessment · {activeJob.status.replaceAll('_', ' ')}</strong>{BLOCKING.has(activeJob.status) && <button type="button" disabled={!!action || uncertain} onClick={() => { void cancel() }}>{action === 'cancel' ? 'Requesting cancellation…' : 'Cancel reassessment'}</button>}</div><p>{activeJob.status === 'interrupted' ? 'This assessment stopped unexpectedly. Its saved activity is retained; it will not repeat an unknown model request automatically.' : activeJob.status === 'cancel_requested' ? 'Further work is being stopped. A model request already sent may still use credits.' : activeJob.status === 'cancelled' ? 'The assessment was cancelled. No change to the finding is inferred from cancellation.' : activeJob.status === 'failed' ? 'The assessment could not complete. Its job status does not revise or disprove the earlier finding.' : activeJob.status === 'succeeded' ? 'The saved disposition and whether it applied are recorded below.' : 'The queued task continues if this page closes. Its input versions remain pinned.'}</p></section>}
    {activeJob && <NativeResumeControl run={activeJob} csrfToken={csrfToken} onResumed={reload} onSessionExpired={expire} />}
    {activity.events.length > 0 && <details><summary>Recorded reassessment activity · {activity.connection}</summary><ol className="assessment-events" aria-label="Recorded reassessment activity">{activity.events.filter((event) => !operational.has(event.kind)).map((event) => <li key={event.sequence}>{event.summary}</li>)}</ol></details>}
    {record && <AssessmentPreview record={record} head={head.head} onOpenFinding={onOpenFinding} />}
    {history.length > 0 && <nav className="assessment-history" aria-label="Finding assessment history"><h6>Saved assessment history</h6><ul>{history.map((item) => <li key={item.id}><button type="button" aria-pressed={record?.id === item.id} disabled={!!action} onClick={() => selectJob(item.run_id)}><strong>{dispositionNames[item.disposition]}</strong><small>{item.applied ? `Applied at revision ${item.head_revision}` : 'Unapplied assessment'} · {new Date(item.created_at).toLocaleDateString()}</small></button></li>)}</ul>{nextHistory && <button type="button" disabled={!!action} onClick={() => { void older('assessments') }}>Load earlier saved assessments</button>}</nav>}
  </section>
}
