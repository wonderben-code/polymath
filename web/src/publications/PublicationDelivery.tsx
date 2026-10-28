import { useEffect, useId, useRef, useState } from 'react'
import { PublicationError } from './client'
import type { PublicationManifest } from './client'
import {
  cancelPublication, publicationDoiUrl, publicationEvents, publicationExecution, publicationJob,
  publicationJobs, publicationRecordUrl, reconcilePublication, resumePublication, startPublication,
} from './deliveryClient'
import type { PublicationExecution, PublicationJob, PublicationJobEvent } from './deliveryClient'

interface Props {
  record: PublicationManifest
  csrfToken: string | null
  operatorCsrf: string | null
  operatorAuthenticated: boolean
  enabled: boolean
  blocked: boolean
  sourceCurrent: boolean
  onAuthorizationError: (error: PublicationError) => void
}
type Action = 'publish' | 'cancel' | 'reconcile' | 'resume'
const POLL_INTERVAL = 3000
const MAX_POLLS = 200
const stages: Record<PublicationJob['stage'], string> = { queued: 'Waiting for the publication worker', create: 'Preparing the approved remote draft', upload: 'Uploading the exact saved document', verify: 'Verifying the document and publication details', publish: 'Submitting the approved record', reconcile: 'Checking the existing remote record', complete: 'Remote record verified' }

function outcome(job: PublicationJob): string {
  if (job.verified) return job.sandbox ? 'Verified sandbox publication · test record' : 'Verified public publication'
  if (job.work_kind === 'reconcile' && (job.status === 'queued' || job.status === 'running')) return job.status === 'queued' ? 'Remote verification queued' : 'Verifying remote status'
  if (job.publication_reported) return 'Remote publication reported · verification required'
  if (job.external_outcome_unknown) return 'Remote outcome needs verification'
  if (job.status === 'queued') return job.work_kind === 'reconcile' ? 'Remote verification queued' : 'Publication queued'
  if (job.status === 'running') return job.work_kind === 'reconcile' ? 'Verifying remote status' : 'Publishing the approved version'
  if (job.status === 'cancelled') return 'Publication job cancelled'
  if (job.status === 'failed') return 'Publication job stopped'
  if (job.status === 'published') return 'Remote publication reported · verification required'
  return 'Publication paused'
}
function shouldPoll(job: PublicationJob): boolean { return !job.verified && (job.status === 'queued' || job.status === 'running' || job.external_outcome_unknown) }
function sameVersion(job: PublicationJob, record: PublicationManifest): boolean { return job.manifest_id === record.id && job.manifest_sha256 === record.manifest_sha256 && job.destination === record.manifest.destination }
function latest(previous: PublicationJob | undefined, incoming: PublicationJob): PublicationJob {
  if (!previous) return incoming
  // A proven remote publication remains true if a delayed local cancellation
  // snapshot arrives later. Only exact-version DTOs enter this cache.
  if (previous.verified && !incoming.verified || Date.parse(previous.updated_at) > Date.parse(incoming.updated_at)) return previous
  if (Date.parse(previous.updated_at) === Date.parse(incoming.updated_at) && ['cancelled', 'failed'].includes(previous.status) && ['queued', 'running'].includes(incoming.status) && previous.work_kind === incoming.work_kind) return previous
  return incoming
}

export function PublicationDelivery({ record, csrfToken, operatorCsrf, operatorAuthenticated, enabled, blocked, sourceCurrent, onAuthorizationError }: Props) {
  const headingId = useId()
  const [execution, setExecution] = useState<PublicationExecution | null>(null)
  const [jobs, setJobs] = useState<PublicationJob[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [events, setEvents] = useState<PublicationJobEvent[]>([])
  const [phase, setPhase] = useState<'loading' | 'ready' | 'error'>('loading')
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState<Action | null>(null)
  const [uncertain, setUncertain] = useState(false)
  const [privilegeLost, setPrivilegeLost] = useState(false)
  const [error, setError] = useState('')
  const [pollPaused, setPollPaused] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const [detailRefresh, setDetailRefresh] = useState(0)
  const version = useRef(0)
  const mutation = useRef<AbortController | null>(null)
  const known = useRef(new Map<string, PublicationJob>())
  const onAuth = useRef(onAuthorizationError)
  onAuth.current = onAuthorizationError
  const job = jobs.find((item) => item.id === selectedId && sameVersion(item, record)) ?? null

  function remember(incoming: PublicationJob): PublicationJob {
    const saved = latest(known.current.get(incoming.id), incoming)
    known.current.set(saved.id, saved)
    return saved
  }
  function fail(failure: unknown, write = false) {
    setConfirmed(false)
    if (failure instanceof PublicationError && ['browser_expired', 'browser_changed', 'operator_expired'].includes(failure.code)) {
      if (failure.code === 'operator_expired') setPrivilegeLost(true)
      else { version.current += 1; setJobs([]); setSelectedId(null); setEvents([]); known.current.clear(); setPhase('error') }
      onAuth.current(failure)
      return
    }
    if (!write) setUncertain(true)
    if (write && (!(failure instanceof PublicationError) || ['connection_failed', 'invalid_response', 'manifest_changed'].includes(failure.code))) {
      setUncertain(true)
      setError('The publication action could not be confirmed. Reload its saved status before taking another action; do not start a second deposit.')
    } else if (failure instanceof PublicationError && failure.code === 'manifest_changed') { setUncertain(true); setError('The job or approval changed. Reload the saved status and review the current exact approval before continuing.') }
    else if (failure instanceof PublicationError && failure.code === 'unavailable') setError('Publication execution is unavailable. Saved status and approval are retained.')
    else if (failure instanceof PublicationError && failure.code === 'not_found') setError('This publication job is unavailable in your current research session.')
    else { setUncertain(true); setError('The saved publication status could not be read. Reload it before continuing.') }
  }

  useEffect(() => {
    const controller = new AbortController()
    const current = ++version.current
    setPhase('loading'); setJobs([]); setSelectedId(null); setEvents([]); setError(''); setConfirmed(false); setPollPaused(false)
    known.current.clear()
    if (!enabled) return () => controller.abort()
    void Promise.all([publicationExecution(controller.signal), publicationJobs(record, controller.signal)]).then(([capability, history]) => {
      if (controller.signal.aborted || current !== version.current) return
      setExecution(capability)
      setJobs(history.jobs.map(remember))
      setSelectedId(history.jobs[0]?.id ?? null)
      setUncertain(false)
      setPhase('ready')
    }).catch((failure: unknown) => { if (!controller.signal.aborted && current === version.current) { setPhase('error'); fail(failure) } })
    return () => { controller.abort(); mutation.current?.abort() }
    // Manifest IDs and hashes define the immutable scope; callbacks/receipts
    // do not change ordinary read access to its existing publication status.
  }, [record.id, record.manifest_sha256, enabled, refresh])

  useEffect(() => { setConfirmed(false) }, [record.id, record.manifest_sha256, record.approval?.id, operatorCsrf, operatorAuthenticated, job?.id, job?.status, job?.can_resume, job?.external_outcome_unknown, job?.verified])
  useEffect(() => { setPrivilegeLost(false) }, [operatorCsrf, operatorAuthenticated])
  useEffect(() => { setConfirmed(false) }, [sourceCurrent])

  useEffect(() => {
    if (!enabled || phase !== 'ready' || !selectedId) return
    const controller = new AbortController()
    const current = version.current
    let active = true
    let timer: number | undefined
    let cursor = 0
    let polls = 0
    const activity = new Map<number, PublicationJobEvent>()
    const isActive = () => active && !controller.signal.aborted && current === version.current
    setEvents([]); setPollPaused(false)
    async function poll() {
      try {
        const [incoming, savedEvents] = await Promise.all([publicationJob(selectedId!, record, controller.signal), publicationEvents(selectedId!, cursor, controller.signal)])
        if (!isActive()) return
        const latestJob = remember(incoming)
        setJobs((previous) => previous.map((item) => item.id === latestJob.id ? latestJob : item))
        for (const event of savedEvents.events) {
          const prior = activity.get(event.sequence)
          if (prior && (prior.kind !== event.kind || prior.summary !== event.summary || prior.created_at !== event.created_at) || activity.size >= 2000 && !prior) throw new PublicationError('invalid_response')
          activity.set(event.sequence, event)
        }
        cursor = savedEvents.next_cursor
        setEvents([...activity.values()].sort((a, b) => a.sequence - b.sequence))
        polls += 1
        if (shouldPoll(latestJob)) {
          if (polls < MAX_POLLS) timer = window.setTimeout(() => void poll(), POLL_INTERVAL)
          else setPollPaused(true)
        }
      } catch (failure) { if (isActive()) { setPollPaused(true); fail(failure) } }
    }
    void poll()
    return () => { active = false; controller.abort(); window.clearTimeout(timer) }
  }, [enabled, phase, selectedId, detailRefresh, record.id, record.manifest_sha256, record.approval?.id, operatorAuthenticated, operatorCsrf])

  const approval = record.approval
  const approvalValid = !!approval?.valid && !approval.revoked_at && Date.parse(approval.expires_at) > Date.now()
  const authorized = enabled && phase === 'ready' && !!csrfToken && !!operatorCsrf && operatorAuthenticated && !privilegeLost && !blocked && !busy && !uncertain
  const canPublish = sourceCurrent && authorized && !!execution?.execution_available && approvalValid && confirmed && jobs.length === 0
  const canResume = sourceCurrent && authorized && !!execution?.execution_available && approvalValid && confirmed && !!job?.can_resume && !job.external_outcome_unknown && !job.publication_reported && !job.frozen && !job.verified
  const canVerify = authorized && !!execution?.execution_available && !!job?.can_reconcile && !job.verified
  const canCancel = authorized && !!job?.can_cancel && !job.verified

  async function perform(action: Action) {
    if (!authorized || mutation.current || !csrfToken || !operatorCsrf) return
    if (action === 'publish' && !canPublish || action === 'resume' && !canResume || action === 'reconcile' && !canVerify || action === 'cancel' && !canCancel) return
    const controller = new AbortController()
    const current = version.current
    mutation.current = controller
    setBusy(action); setError(''); setConfirmed(false)
    try {
      const response = action === 'publish' ? await startPublication(record, approval!.id, csrfToken, operatorCsrf, controller.signal)
        : action === 'resume' ? await resumePublication(job!.id, record, approval!.id, csrfToken, operatorCsrf, controller.signal)
          : action === 'reconcile' ? await reconcilePublication(job!.id, record, csrfToken, operatorCsrf, controller.signal)
            : await cancelPublication(job!.id, record, csrfToken, operatorCsrf, controller.signal)
      if (controller.signal.aborted || current !== version.current) return
      const saved = remember(response)
      setJobs((previous) => previous.some((item) => item.id === saved.id) ? previous.map((item) => item.id === saved.id ? saved : item) : [saved, ...previous])
      setSelectedId(saved.id)
      setDetailRefresh((value) => value + 1)
    } catch (failure) { if (!controller.signal.aborted && current === version.current) fail(failure, true) }
    finally { if (mutation.current === controller) { mutation.current = null; setBusy(null) } }
  }

  if (!enabled) return null
  const verifiedRecord = job?.verified && job.record_url && job.doi ? { url: publicationRecordUrl(job.record_url, job.destination), doi: publicationDoiUrl(job.doi) } : null
  const needsConfirmation = jobs.length === 0 || !!job?.can_resume && !job.external_outcome_unknown && !job.publication_reported && !job.verified
  return <section className="publication-delivery" aria-labelledby={headingId}>
    <header className="publication-delivery-heading"><h4 id={headingId}>Publish this approved version</h4><button type="button" className="text-button" disabled={phase === 'loading' || !!busy} onClick={() => { setConfirmed(false); setRefresh((value) => value + 1) }}>Reload publication status</button></header>
    <p className="publication-field-note">{record.manifest.destination === 'sandbox' ? 'Sandbox publication creates a test record. It is separate from a production publication.' : 'Production publication creates a public record and DOI for this exact saved version.'}</p>
    {phase === 'loading' && <p className="publication-message" role="status">Loading saved publication status…</p>}
    {phase === 'error' && <p className="publication-message">Saved publication status is unavailable. Reload it before continuing.</p>}
    {error && <p className="publication-message publication-error" role="alert">{error}</p>}
    {phase === 'ready' && <>
      {!execution?.execution_available && <p className="publication-message">Publishing execution is disabled or unavailable. Saved approvals and publication status can still be inspected.</p>}
      {jobs.length > 1 && <nav className="publication-job-history" aria-label="Publication jobs"><ul>{jobs.map((item) => <li key={item.id}><button type="button" disabled={!!busy || uncertain} aria-pressed={item.id === selectedId} onClick={() => { setSelectedId(item.id); setConfirmed(false); setDetailRefresh((value) => value + 1) }}>{outcome(item)}</button></li>)}</ul></nav>}
      {job ? <section className="publication-job" aria-label="Saved publication job"><h5 role="status">{outcome(job)}</h5><p>{job.verified ? 'The remote record and exact approved file have been verified.' : job.work_kind === 'reconcile' && (job.status === 'running' || job.status === 'queued') ? 'This verification reads the existing remote state. It does not create, upload, or publish another deposit.' : job.status === 'running' ? stages[job.stage] : job.status === 'queued' ? 'The job is saved. It continues if you close this page.' : job.status === 'cancelled' ? 'Cancellation stops further requests. A request already granted can still complete remotely.' : 'This job has stopped taking new publication steps. Its saved observations are retained.'}</p>
        {job.external_outcome_unknown && <p className="publication-unknown">An earlier request may have completed. Verify the remote status before continuing. Verification reads the existing deposit rather than blindly repeating a publishing request.</p>}
        {job.frozen && !job.verified && <p className="publication-field-note">This job is held for review. Its existing remote state must be resolved before publication can continue.</p>}
        {verifiedRecord?.url && verifiedRecord.doi && <div className="publication-verified-record"><a href={verifiedRecord.url} target="_blank" rel="noopener noreferrer">{job.sandbox ? 'Open verified sandbox record' : 'Open verified public record'}<span aria-hidden="true"> ↗</span></a><a href={verifiedRecord.doi} target="_blank" rel="noopener noreferrer">{job.sandbox ? 'Sandbox DOI (test)' : 'Published DOI'}: {job.doi}</a><p>Published records remain available if an approval later expires or is revoked.</p></div>}
        {job.status === 'published' && !job.verified && <p className="publication-unknown">A publication has been reported, but the record and exact file are not yet verified. No DOI is shown as a verified result.</p>}
        {!job.verified && <div className="publication-job-actions">{job.can_reconcile && <button type="button" disabled={!canVerify} onClick={() => void perform('reconcile')}>{busy === 'reconcile' ? 'Queuing remote verification…' : 'Verify remote status'}</button>}{job.can_cancel && <button type="button" disabled={!canCancel} onClick={() => void perform('cancel')}>{busy === 'cancel' ? 'Requesting cancellation…' : 'Cancel publication job'}</button>}</div>}
        {events.length > 0 && <details className="publication-job-activity"><summary>Recorded publication activity</summary><ol aria-label="Recorded publication activity">{events.map((event) => <li key={event.sequence}>{event.summary}</li>)}</ol></details>}
        {pollPaused && !job.verified && <p className="publication-field-note">Automatic status checks have paused. Reload publication status to check the saved job again.</p>}
      </section> : <p className="publication-field-note">No publication job has been started for this exact preview.</p>}
      {!operatorAuthenticated && <p className="publication-field-note">Unlock publisher approval to start publication, verify remote status, or continue an existing job.</p>}
      {!approvalValid && !job?.verified && <p className="publication-field-note">A current approval of this exact preview is required before publishing or continuing. Review and approve the version above.</p>}
      {needsConfirmation && !job?.verified && <>
        <label className="publication-consent publication-send-consent"><input type="checkbox" checked={confirmed} disabled={!sourceCurrent || !authorized || !execution?.execution_available || !approvalValid || !!job && (!job.can_resume || job.external_outcome_unknown || job.frozen)} onChange={(event) => setConfirmed(event.target.checked)} /><span>{job ? 'I want to continue this existing publication job using the current exact approval.' : record.manifest.destination === 'sandbox' ? 'I want to publish this exact approved version to the Zenodo sandbox.' : 'I want to publish this exact approved version publicly on Zenodo production.'}<small>{record.manifest.destination === 'sandbox' ? 'This creates a sandbox test record.' : 'This creates a public production record with a DOI.'}</small></span></label>
        <button type="button" className="publication-primary" disabled={job ? !canResume : !canPublish} onClick={() => void perform(job ? 'resume' : 'publish')}>{busy === 'publish' ? 'Queuing publication…' : busy === 'resume' ? 'Queuing continuation…' : job ? 'Continue this publication' : record.manifest.destination === 'sandbox' ? 'Publish to sandbox' : 'Publish public record'}</button>
      </>}
    </>}
  </section>
}
