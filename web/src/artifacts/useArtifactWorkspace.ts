import { useCallback, useEffect, useRef, useState } from 'react'
import { parseActivity, ResearchApiError } from '../research/client'
import type { ResearchActivityEvent } from '../research/types'
import {
  cancelArtifactJob, createArtifactJob, getArtifact, getArtifactJob, listArtifactJobs,
} from './client'
import type { ArtifactJob, ArtifactKind, SavedArtifact } from './types'
import { useNativeResearchSubmission } from '../research/nativeContext'

const TERMINAL = new Set(['succeeded', 'failed', 'cancelled', 'interrupted'])
const BLOCKING = new Set(['queued', 'running', 'waiting_retry', 'cancel_requested', 'interrupted'])
const RETRY_DELAYS = [500, 1500, 4000]

function merge(previous: ArtifactJob | undefined, incoming: ArtifactJob): ArtifactJob {
  if (!previous) return incoming
  const before = Date.parse(previous.updated_at), after = Date.parse(incoming.updated_at)
  if (before > after) return previous
  if (previous.status === 'cancel_requested' && ['queued', 'running', 'waiting_retry'].includes(incoming.status)) return previous
  if (before === after && TERMINAL.has(previous.status) && previous.status !== incoming.status) return previous
  return incoming
}

function failureMessage(error: unknown): string {
  if (!(error instanceof ResearchApiError)) return 'The document service could not be reached. Reload saved work before trying again.'
  switch (error.code) {
    case 'resource_limited': return `A request or visitor allowance limit was reached. Wait${error.retryAfterSeconds === null ? '' : ` at least ${error.retryAfterSeconds} seconds`} before trying again. No action was retried; saved work is retained.`
    case 'queue_limit': return 'An investigation or writing task is already active, or the queue is full. Finish or cancel it before starting another.'
    case 'run_unavailable': return 'This saved finding or document is unavailable in your current session.'
    case 'unavailable': return 'Generation is unavailable right now. Your saved work is retained.'
    case 'invalid_response': return 'The saved document could not be read. Reload saved work before continuing.'
    case 'invalid_request': return 'The selected finding or conjecture could not be used. Reload saved work before trying again.'
    case 'native_authority_changed': return 'Native research authority changed or expired. Refresh the current activation; no writing or formalisation request was retried.'
    default: return 'The document service could not be reached. Reload saved work before trying again.'
  }
}

export function useArtifactWorkspace({ sourceResultId, csrfToken, enabled, canGenerate, onSessionExpired }: {
  sourceResultId: string
  csrfToken: string | null
  enabled: boolean
  canGenerate: boolean
  onSessionExpired: () => void
}) {
  const native = useNativeResearchSubmission()
  const [jobs, setJobs] = useState<ArtifactJob[]>([])
  const [phase, setPhase] = useState<'loading' | 'ready' | 'error' | 'expired'>('loading')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [activeJob, setActiveJob] = useState<ArtifactJob | null>(null)
  const [artifact, setArtifact] = useState<SavedArtifact | null>(null)
  const [events, setEvents] = useState<ResearchActivityEvent[]>([])
  const [error, setError] = useState('')
  const [connection, setConnection] = useState<'connecting' | 'live' | 'reconnecting' | 'closed' | 'error'>('closed')
  const [submitting, setSubmitting] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [refreshVersion, setRefreshVersion] = useState(0)
  const [detailVersion, setDetailVersion] = useState(0)
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)
  const [unconfirmedCreate, setUnconfirmedCreate] = useState(false)
  const version = useRef(0)
  const selected = useRef<string | null>(null)
  const mutation = useRef<AbortController | null>(null)
  const pagination = useRef<AbortController | null>(null)
  const known = useRef(new Map<string, ArtifactJob>())

  const remember = useCallback((incoming: ArtifactJob) => {
    const job = merge(known.current.get(incoming.id), incoming)
    known.current.set(job.id, job)
    return job
  }, [])

  const fail = useCallback((failure: unknown) => {
    if (failure instanceof ResearchApiError && failure.code === 'native_authority_changed') { native.invalidate(); setError(failureMessage(failure)); return }
    if (failure instanceof ResearchApiError && (failure.code === 'session_expired' || failure.code === 'session_changed')) {
      version.current += 1
      mutation.current?.abort()
      pagination.current?.abort()
      setJobs([]); setActiveJob(null); setArtifact(null); setEvents([]); setSelectedId(null)
      selected.current = null
      known.current.clear()
      setPhase('expired')
      setError('Your research session expired or changed. Reload saved research to continue.')
      onSessionExpired()
    } else setError(failureMessage(failure))
  }, [onSessionExpired, native.invalidate])

  useEffect(() => {
    const controller = new AbortController()
    const current = ++version.current
    setJobs([]); setArtifact(null); setActiveJob(null); setEvents([]); setError(''); setNextCursor(null)
    setPhase('loading')
    if (!enabled || !csrfToken) return () => controller.abort()
    void listArtifactJobs(sourceResultId, controller.signal).then((history) => {
      if (controller.signal.aborted || version.current !== current) return
      const incoming = history.jobs.map(remember)
      setJobs(incoming)
      setUnconfirmedCreate(false)
      setNextCursor(history.next_cursor)
      const target = incoming.some((job) => job.id === selected.current) ? selected.current : incoming[0]?.id ?? null
      selected.current = target
      setSelectedId(target)
      setDetailVersion((value) => value + 1)
      setPhase('ready')
    }).catch((failure: unknown) => {
      if (!controller.signal.aborted && version.current === current) { setPhase('error'); fail(failure) }
    })
    return () => {
      controller.abort()
      mutation.current?.abort()
      pagination.current?.abort()
    }
  }, [enabled, csrfToken, sourceResultId, refreshVersion, remember, fail])

  useEffect(() => {
    if (!enabled || phase !== 'ready' || !selectedId) return
    const controller = new AbortController()
    const current = version.current
    let active = true
    let stream: EventSource | null = null
    let deadline: number | undefined
    let retryTimer: number | undefined
    let retries = 0
    let cursor = 0
    let loadingArtifact = false
    const recorded = new Map<number, ResearchActivityEvent>()
    const isActive = () => active && !controller.signal.aborted && current === version.current && selected.current === selectedId
    setActiveJob(null); setArtifact(null); setEvents([]); setConnection('connecting'); setError('')

    async function loadJob(): Promise<ArtifactJob> {
      const response = await getArtifactJob(selectedId!, controller.signal)
      const job = isActive() ? remember(response) : response
      if (isActive()) {
        setActiveJob(job)
        setJobs((previous) => previous.some((item) => item.id === job.id) ? previous.map((item) => item.id === job.id ? merge(item, job) : item) : [job, ...previous])
      }
      return job
    }

    async function loadArtifact(job: ArtifactJob): Promise<void> {
      if (!job.artifact_id || loadingArtifact) return
      loadingArtifact = true
      try {
        const saved = await getArtifact(job.artifact_id, controller.signal)
        if (saved.run_id !== job.id || saved.source_result_id !== sourceResultId || saved.kind !== job.task_kind) throw new ResearchApiError('invalid_response')
        if (isActive()) setArtifact(saved)
      } catch (failure) { if (isActive()) fail(failure) }
      finally { loadingArtifact = false }
    }

    function closeStream() {
      window.clearTimeout(deadline)
      stream?.close()
      stream = null
    }

    function connect() {
      if (!isActive()) return
      if (typeof EventSource === 'undefined') { setConnection('error'); setError('Live updates are unavailable in this browser. Reload saved work to check progress.'); return }
      const source = new EventSource(`/api/v1/runs/${selectedId}/events?after=${cursor}`)
      stream = source
      const watch = (milliseconds: number) => {
        window.clearTimeout(deadline)
        deadline = window.setTimeout(disconnected, milliseconds)
      }
      const refreshStatus = async () => {
        const job = await loadJob()
        if (isActive() && job.artifact_id) await loadArtifact(job)
        return job
      }
      function disconnected() {
        if (!isActive() || stream !== source) return
        closeStream()
        if (retries >= RETRY_DELAYS.length) {
          setConnection('error')
          setError('The live connection stopped. Saved work is retained; reload it to reconnect.')
          void refreshStatus().catch((failure: unknown) => { if (isActive()) fail(failure) })
          return
        }
        setConnection('reconnecting')
        retryTimer = window.setTimeout(() => {
          void refreshStatus().then((job) => {
            if (!isActive()) return
            if (TERMINAL.has(job.status)) setConnection('closed')
            else connect()
          }).catch((failure: unknown) => { if (isActive()) { setConnection('error'); fail(failure) } })
        }, RETRY_DELAYS[retries++])
      }
      source.addEventListener('open', () => { if (isActive() && stream === source) { setConnection('live'); watch(45000) } })
      source.addEventListener('heartbeat', () => { if (isActive() && stream === source) watch(45000) })
      source.addEventListener('activity', (event) => {
        if (!isActive() || stream !== source) return
        try {
          const incoming = event as MessageEvent<string>
          if (typeof incoming.data !== 'string' || incoming.data.length > 16000) throw new ResearchApiError('invalid_response')
          const item = parseActivity(JSON.parse(incoming.data) as unknown)
          if (incoming.lastEventId && incoming.lastEventId !== String(item.sequence)) throw new ResearchApiError('invalid_response')
          const prior = recorded.get(item.sequence)
          if (prior && (prior.kind !== item.kind || prior.summary !== item.summary)) throw new ResearchApiError('invalid_response')
          if (!prior) {
            if (recorded.size >= 2000) throw new ResearchApiError('invalid_response')
            recorded.set(item.sequence, item)
            cursor = Math.max(cursor, item.sequence)
            setEvents([...recorded.values()].sort((a, b) => a.sequence - b.sequence))
          }
          watch(45000)
        } catch (failure) { closeStream(); setConnection('error'); fail(failure) }
      })
      source.addEventListener('session_expired', () => { if (isActive() && stream === source) { closeStream(); fail(new ResearchApiError('session_expired')) } })
      source.addEventListener('end', () => {
        if (!isActive() || stream !== source) return
        closeStream(); setConnection('closed')
        void refreshStatus().catch((failure: unknown) => { if (isActive()) fail(failure) })
      })
      source.addEventListener('error', disconnected)
      watch(10000)
    }

    void loadJob().then(async (job) => {
      if (!isActive()) return
      if (job.artifact_id) await loadArtifact(job)
      if (isActive()) {
        if (TERMINAL.has(job.status)) setConnection('closed')
        else connect()
      }
    }).catch((failure: unknown) => { if (isActive()) { setConnection('error'); fail(failure) } })

    return () => { active = false; controller.abort(); closeStream(); window.clearTimeout(retryTimer) }
  }, [enabled, phase, selectedId, detailVersion, sourceResultId, remember, fail])

  const selectJob = useCallback((id: string) => {
    if (phase !== 'ready' || !known.current.has(id)) return
    selected.current = id
    setSelectedId(id)
    setDetailVersion((value) => value + 1)
  }, [phase])

  const create = useCallback(async (kind: ArtifactKind, formalisationId?: string) => {
    if (!enabled || !canGenerate || !native.canSubmit || !csrfToken || phase !== 'ready' || unconfirmedCreate || mutation.current || jobs.some((job) => BLOCKING.has(job.status))) return
    const controller = new AbortController()
    const current = version.current
    mutation.current = controller
    setSubmitting(true); setError('')
    try {
      const response = await createArtifactJob(kind, sourceResultId, csrfToken, controller.signal, formalisationId, native.submission ?? undefined)
      if (controller.signal.aborted || current !== version.current) return
      const job = remember(response)
      setJobs((previous) => [job, ...previous.filter((item) => item.id !== job.id)])
      selected.current = job.id
      setSelectedId(job.id)
      setDetailVersion((value) => value + 1)
    } catch (failure) {
      if (!controller.signal.aborted && current === version.current) {
        if (failure instanceof ResearchApiError && ['connection_failed', 'invalid_response', 'unavailable'].includes(failure.code)) {
          setUnconfirmedCreate(true)
          setError('Could not confirm whether this task was queued. Reload saved work before trying again.')
        }
        else fail(failure)
      }
    } finally { if (mutation.current === controller) { mutation.current = null; setSubmitting(false) } }
  }, [enabled, canGenerate, csrfToken, phase, sourceResultId, jobs, unconfirmedCreate, remember, fail, native.canSubmit, native.submission])

  const cancel = useCallback(async () => {
    if (!enabled || !csrfToken || !activeJob || mutation.current || !BLOCKING.has(activeJob.status)) return
    const controller = new AbortController()
    const current = version.current
    mutation.current = controller
    setCancelling(true); setError('')
    try {
      const job = remember(await cancelArtifactJob(activeJob.id, csrfToken, controller.signal))
      if (!controller.signal.aborted && current === version.current) {
        setActiveJob(job)
        setJobs((previous) => previous.map((item) => item.id === job.id ? job : item))
      }
    } catch (failure) { if (!controller.signal.aborted && current === version.current) fail(failure) }
    finally { if (mutation.current === controller) { mutation.current = null; setCancelling(false) } }
  }, [enabled, csrfToken, activeJob, remember, fail])

  const loadMore = useCallback(async () => {
    if (!enabled || phase !== 'ready' || !nextCursor || pagination.current) return
    const controller = new AbortController()
    const current = version.current
    pagination.current = controller
    setLoadingMore(true)
    try {
      const history = await listArtifactJobs(sourceResultId, controller.signal, nextCursor)
      if (!controller.signal.aborted && current === version.current) {
        setJobs((previous) => [...new Map([...previous, ...history.jobs.map(remember)].map((job) => [job.id, job])).values()])
        setNextCursor(history.next_cursor === nextCursor ? null : history.next_cursor)
      }
    } catch (failure) { if (!controller.signal.aborted && current === version.current) fail(failure) }
    finally { if (pagination.current === controller) { pagination.current = null; setLoadingMore(false) } }
  }, [enabled, phase, nextCursor, sourceResultId, remember, fail])

  const blocking = jobs.some((job) => BLOCKING.has(job.status))
  return {
    phase, jobs, selectedId, activeJob, artifact, events, error, connection, submitting, cancelling,
    nextCursor, loadingMore, loadMore, selectJob, create, cancel,
    canCreate: enabled && canGenerate && native.canSubmit && !!csrfToken && phase === 'ready' && !submitting && !cancelling && !blocking && !unconfirmedCreate,
    busy: submitting || cancelling || blocking,
    refresh: () => setRefreshVersion((value) => value + 1),
  }
}
