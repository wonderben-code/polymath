import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { DiscoveryIntent } from '../discovery/types'
import {
  cancelResearchRun, createResearchRun, getResearchResult, getResearchRun,
  listResearchRuns, openResearchSession, parseActivity, ResearchApiError,
} from './client'
import type { OwnedRun, ResearchApiErrorCode } from './client'
import type { ResearchActivityEvent, ResearchResult, ResearchRunViewProps } from './types'
import { useNativeResearchContext } from './useNativeResearchContext'

const TERMINAL = new Set(['succeeded', 'failed', 'cancelled', 'interrupted'])
const RETRY_DELAYS = [500, 1500, 4000]
type Phase = 'idle' | 'loading' | 'ready' | 'error' | 'expired'

function message(code: ResearchApiErrorCode, retry: number | null = null): string {
  switch (code) {
    case 'session_expired': return 'Your research session expired. Reload saved research to continue.'
    case 'session_changed': return 'Your research session changed. Reload saved research before trying again.'
    case 'run_unavailable': return 'This investigation is unavailable in your current session.'
    case 'resource_limited': return `A request or visitor allowance limit was reached. ${retry === null ? 'Wait before trying again.' : `Wait at least ${retry} seconds before trying again.`} No action was retried; saved research remains available.`
    case 'queue_limit': return 'The research queue or your active-investigation limit has been reached.'
    case 'invalid_request': return 'Check the research request and try again.'
    case 'invalid_response': return 'The research service returned an unreadable record. Reload saved research to try again.'
    case 'native_authority_changed': return 'Native research authority changed or expired. Refresh the current activation before starting another task; this request was not retried.'
    default: return 'Saved research could not be loaded. Check the connection and try again.'
  }
}

function code(error: unknown): ResearchApiErrorCode {
  return error instanceof ResearchApiError ? error.code : 'connection_failed'
}

function updated(runs: OwnedRun[], run: OwnedRun): OwnedRun[] {
  return runs.some((item) => item.id === run.id)
    ? runs.map((item) => item.id === run.id ? latest(item, run) : item)
    : [run, ...runs]
}

function latest(previous: OwnedRun | undefined, incoming: OwnedRun): OwnedRun {
  if (!previous) return incoming
  const before = Date.parse(previous.updated_at)
  const after = Date.parse(incoming.updated_at)
  if (after < before) return previous
  const active = new Set(['queued', 'running', 'waiting_retry'])
  if (previous.status === 'cancel_requested' && active.has(incoming.status)) return previous
  if (after === before && TERMINAL.has(previous.status) && previous.status !== incoming.status) return previous
  return incoming
}

export function useResearchWorkspace(enabled: boolean, researchAvailable: boolean, nativeRequired = false) {
  const [phase, setPhase] = useState<Phase>('idle')
  const [csrf, setCsrf] = useState<string | null>(null)
  const [runs, setRuns] = useState<OwnedRun[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState('')
  const [detailError, setDetailError] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [activeRun, setActiveRun] = useState<OwnedRun | null>(null)
  const [events, setEvents] = useState<ResearchActivityEvent[]>([])
  const [result, setResult] = useState<ResearchResult | null>(null)
  const [connection, setConnection] = useState<ResearchRunViewProps['connection']>('closed')
  const [submitting, setSubmitting] = useState(false)
  const [unconfirmedSubmission, setUnconfirmedSubmission] = useState(false)
  const [cancellingId, setCancellingId] = useState<string | null>(null)
  const [refreshVersion, setRefreshVersion] = useState(0)
  const [detailVersion, setDetailVersion] = useState(0)
  const generation = useRef(0)
  const selected = useRef<string | null>(null)
  const bootstrap = useRef<AbortController | null>(null)
  const mutation = useRef<AbortController | null>(null)
  const pagination = useRef<AbortController | null>(null)
  const knownRuns = useRef(new Map<string, OwnedRun>())

  const remember = useCallback((run: OwnedRun) => {
    const current = latest(knownRuns.current.get(run.id), run)
    knownRuns.current.set(run.id, current)
    return current
  }, [])

  const clearPrivateState = useCallback(() => {
    selected.current = null
    setSelectedId(null)
    setActiveRun(null)
    setEvents([])
    setResult(null)
    setRuns([])
    setNextCursor(null)
    setCsrf(null)
    setDetailError('')
    setConnection('closed')
    mutation.current?.abort()
    mutation.current = null
    pagination.current?.abort()
    pagination.current = null
    setSubmitting(false)
    setUnconfirmedSubmission(false)
    setCancellingId(null)
    knownRuns.current.clear()
    setLoadingMore(false)
  }, [])

  const invalidate = useCallback((failure: ResearchApiErrorCode) => {
    generation.current += 1
    bootstrap.current?.abort()
    clearPrivateState()
    setPhase('expired')
    setError(message(failure))
  }, [clearPrivateState])

  const refresh = useCallback(() => {
    generation.current += 1
    clearPrivateState()
    setRefreshVersion((version) => version + 1)
  }, [clearPrivateState])

  const native = useNativeResearchContext({ enabled: phase === 'ready' && enabled, csrfToken: csrf, required: nativeRequired, available: researchAvailable, onBrowserExpired: () => invalidate('session_expired') })
  const ownActiveResearch = native.access?.audience === 'public_sessions' && runs.some((run) => ['queued', 'running', 'waiting_retry', 'cancel_requested', 'interrupted'].includes(run.status))
  const ladderProfile = native.activation?.discovery_ladder_profile?.profile
  const discoveryIntent = useMemo<DiscoveryIntent | null>(() => native.access?.audience === 'public_sessions' ? native.access.limits?.discovery_intent ?? null : ladderProfile ? { protocol: 'polymath-surprise-ladder-v1', max_stages: Math.min(3, ladderProfile.max_stages), max_depth: Math.min(2, ladderProfile.max_depth) } : null, [ladderProfile?.max_stages, ladderProfile?.max_depth, native.access])

  useEffect(() => {
    const controller = new AbortController()
    bootstrap.current = controller
    const current = ++generation.current
    clearPrivateState()
    setError('')
    if (!enabled) { setPhase('idle'); return () => controller.abort() }
    setPhase('loading')
    void (async () => {
      try {
        const session = await openResearchSession(controller.signal)
        const history = await listResearchRuns(controller.signal)
        if (controller.signal.aborted || generation.current !== current) return
        setCsrf(session.csrf_token)
        setRuns([...new Map(history.runs.map((run) => [run.id, remember(run)])).values()])
        setNextCursor(history.next_cursor)
        setPhase('ready')
      } catch (failure) {
        if (controller.signal.aborted || generation.current !== current) return
        const failureCode = code(failure)
        clearPrivateState()
        setPhase(failureCode === 'session_expired' || failureCode === 'session_changed' ? 'expired' : 'error')
        setError(message(failureCode))
      }
    })()
    return () => {
      controller.abort()
      mutation.current?.abort()
      pagination.current?.abort()
    }
  }, [enabled, refreshVersion, clearPrivateState, remember])

  const selectRun = useCallback((runId: string) => {
    // Map records and memory can refer to older runs outside the loaded history
    // page. The server checks ownership before returning that research record.
    if (phase !== 'ready' || !/^[a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12}$/i.test(runId)) return
    selected.current = runId
    setSelectedId(runId)
    setDetailVersion((version) => version + 1)
  }, [phase])

  useEffect(() => {
    if (!enabled || phase !== 'ready' || !selectedId) return
    const controller = new AbortController()
    let stream: EventSource | null = null
    let closeStream = () => {}
    let retryTimer: number | undefined
    let active = true
    let retries = 0
    let cursor = 0
    let terminalResultLoading = false
    const recorded = new Map<number, ResearchActivityEvent>()
    const isActive = () => active && !controller.signal.aborted && selected.current === selectedId
    setActiveRun(null)
    setEvents([])
    setResult(null)
    setDetailError('')
    setConnection('connecting')

    function fail(failure: unknown): void {
      if (!isActive()) return
      const failureCode = code(failure)
      if (failureCode === 'session_expired' || failureCode === 'session_changed') {
        invalidate(failureCode)
      } else {
        setDetailError(message(failureCode))
        if (failureCode === 'run_unavailable') {
          selected.current = null
          setSelectedId(null)
          setActiveRun(null)
          setEvents([])
          setResult(null)
          setRuns((items) => items.filter((item) => item.id !== selectedId))
        }
      }
    }

    async function loadRun(): Promise<OwnedRun> {
      const response = await getResearchRun(selectedId!, controller.signal)
      const run = isActive() ? remember(response) : response
      if (isActive()) {
        setActiveRun(run)
        setRuns((items) => updated(items, run))
      }
      return run
    }

    async function loadResult(run: OwnedRun): Promise<void> {
      if (!run.result_id || terminalResultLoading) return
      terminalResultLoading = true
      try {
        const saved = await getResearchResult(run.id, controller.signal)
        if (isActive()) setResult(saved)
      } catch (failure) { fail(failure) }
      finally { terminalResultLoading = false }
    }

    function openStream(): void {
      if (!isActive()) return
      if (typeof EventSource === 'undefined') {
        setConnection('error')
        setDetailError('Live activity is unavailable in this browser. Reload the investigation to check saved progress.')
        return
      }
      stream = new EventSource(`/api/v1/runs/${selectedId}/events?after=${cursor}`)
      const source = stream
      let deadline: number | undefined
      const close = () => { window.clearTimeout(deadline); source.close(); if (stream === source) stream = null }
      closeStream = close
      const watch = (milliseconds: number) => {
        window.clearTimeout(deadline)
        deadline = window.setTimeout(disconnected, milliseconds)
      }
      source.addEventListener('open', () => {
        if (isActive() && stream === source) { setConnection('live'); watch(45000) }
      })
      source.addEventListener('heartbeat', () => {
        if (isActive() && stream === source) watch(45000)
      })
      source.addEventListener('activity', (event) => {
        if (!isActive() || stream !== source) return
        try {
          const incoming = event as MessageEvent<string>
          if (typeof incoming.data !== 'string' || incoming.data.length > 16000) throw new ResearchApiError('invalid_response')
          const activity = parseActivity(JSON.parse(incoming.data) as unknown)
          if (incoming.lastEventId && incoming.lastEventId !== String(activity.sequence)) throw new ResearchApiError('invalid_response')
          const previous = recorded.get(activity.sequence)
          if (previous && (previous.kind !== activity.kind || previous.summary !== activity.summary)) throw new ResearchApiError('invalid_response')
          if (!previous) {
            if (recorded.size >= 2000) throw new ResearchApiError('invalid_response')
            recorded.set(activity.sequence, activity)
            cursor = Math.max(cursor, activity.sequence)
            setEvents([...recorded.values()].sort((left, right) => left.sequence - right.sequence))
          }
          watch(45000)
        } catch {
          close()
          setConnection('error')
          setDetailError('A saved activity record could not be read. Reload the investigation before continuing.')
        }
      })
      source.addEventListener('session_expired', () => {
        if (!isActive() || stream !== source) return
        close()
        invalidate('session_expired')
      })
      source.addEventListener('end', () => {
        if (!isActive() || stream !== source) return
        close()
        setConnection('closed')
        void loadRun().then((run) => { if (isActive()) return loadResult(run) }).catch(fail)
      })
      function disconnected() {
        if (!isActive() || stream !== source) return
        close()
        if (retries >= RETRY_DELAYS.length) {
          setConnection('error')
          setDetailError('The activity connection stopped after several attempts. Saved activity is retained; reload the investigation to reconnect.')
          void loadRun().then((run) => { if (isActive() && TERMINAL.has(run.status)) return loadResult(run) }).catch(fail)
          return
        }
        setConnection('reconnecting')
        retryTimer = window.setTimeout(() => {
          void loadRun().then((run) => {
            if (!isActive()) return
            if (TERMINAL.has(run.status)) void loadResult(run)
            openStream()
          }).catch((failure) => { fail(failure); if (isActive()) setConnection('error') })
        }, RETRY_DELAYS[retries++])
      }
      source.addEventListener('error', disconnected)
      watch(10000)
    }

    void loadRun().then((run) => {
      if (!isActive()) return
      if (TERMINAL.has(run.status)) void loadResult(run)
      openStream()
    }).catch((failure) => { fail(failure); if (isActive()) setConnection('error') })

    return () => {
      active = false
      controller.abort()
      window.clearTimeout(retryTimer)
      closeStream()
    }
  }, [enabled, phase, selectedId, detailVersion, invalidate, remember])

  const loadMore = useCallback(async () => {
    if (phase !== 'ready' || !nextCursor || pagination.current) return
    const controller = new AbortController()
    const current = generation.current
    pagination.current = controller
    setLoadingMore(true)
    try {
      const history = await listResearchRuns(controller.signal, nextCursor)
      if (controller.signal.aborted || current !== generation.current) return
      const incoming = history.runs.map(remember)
      setRuns((previous) => [...new Map([...previous, ...incoming].map((run) => [run.id, run])).values()])
      setNextCursor(history.next_cursor === nextCursor ? null : history.next_cursor)
    } catch (failure) {
      if (controller.signal.aborted || current !== generation.current) return
      const failureCode = code(failure)
      if (failureCode === 'session_expired' || failureCode === 'session_changed') invalidate(failureCode)
      else setError(message(failureCode))
    } finally {
      if (pagination.current === controller) { pagination.current = null; setLoadingMore(false) }
    }
  }, [phase, nextCursor, invalidate, remember])

  const cancel = useCallback(async () => {
    if (phase !== 'ready' || !csrf || !activeRun || mutation.current) return
    const controller = new AbortController()
    const current = generation.current
    mutation.current = controller
    const target = activeRun.id
    setCancellingId(target)
    setDetailError('')
    try {
      const response = await cancelResearchRun(target, csrf, controller.signal)
      if (controller.signal.aborted || current !== generation.current) return
      const run = remember(response)
      setRuns((items) => updated(items, run))
      if (selected.current === run.id) setActiveRun(run)
    } catch (failure) {
      if (controller.signal.aborted || current !== generation.current) return
      const failureCode = code(failure)
      if (failureCode === 'session_expired' || failureCode === 'session_changed') invalidate(failureCode)
      else if (selected.current === target) setDetailError(failureCode === 'connection_failed' ? 'Cancellation could not be confirmed. Reload the investigation to check its status before trying again.' : message(failureCode, failure instanceof ResearchApiError ? failure.retryAfterSeconds : null))
    } finally {
      if (mutation.current === controller) { mutation.current = null; setCancellingId(null) }
    }
  }, [phase, csrf, activeRun, invalidate, remember])

  const submit = useCallback(async (mode: OwnedRun['mode'], goal: string, parentResultIds: readonly string[] = []) => {
    if (!enabled || !researchAvailable || !native.canSubmit || ownActiveResearch || unconfirmedSubmission || phase !== 'ready' || !csrf || mutation.current) return
    const controller = new AbortController()
    const current = generation.current
    mutation.current = controller
    setSubmitting(true)
    setError('')
    try {
      const response = await createResearchRun(mode, goal, csrf, controller.signal, parentResultIds, native.submission ?? undefined, mode === 'surprise' ? discoveryIntent ?? undefined : undefined)
      if (controller.signal.aborted || current !== generation.current) return
      const run = remember(response)
      setRuns((items) => updated(items, run))
      selected.current = run.id
      setSelectedId(run.id)
      setDetailVersion((version) => version + 1)
    } catch (failure) {
      if (controller.signal.aborted || current !== generation.current) return
      const failureCode = code(failure)
      if (failureCode === 'session_expired' || failureCode === 'session_changed') invalidate(failureCode)
      else if (failureCode === 'native_authority_changed') { native.invalidate(); setError(message(failureCode)) }
      else { if (['connection_failed', 'invalid_response', 'unavailable'].includes(failureCode)) setUnconfirmedSubmission(true); setError(failureCode === 'connection_failed' ? 'Could not confirm whether the investigation was queued. Reload saved research before trying again.' : message(failureCode, failure instanceof ResearchApiError ? failure.retryAfterSeconds : null)) }
    } finally {
      if (mutation.current === controller) { mutation.current = null; setSubmitting(false) }
    }
  }, [enabled, researchAvailable, phase, csrf, unconfirmedSubmission, invalidate, remember, native.canSubmit, native.submission, native.invalidate, discoveryIntent, ownActiveResearch])

  const investigateBridges = useCallback((parentResultIds: readonly string[]) => submit(
    'explore',
    'Investigate a shared structure across these saved bridges, test it against the original literature, and report limitations.',
    parentResultIds,
  ), [submit])

  const invalidateSession = useCallback(() => invalidate('session_expired'), [invalidate])

  return {
    phase, runs, nextCursor, loadingMore, error, detailError, selectedId, activeRun, events, result,
    connection, submitting, cancelling: cancellingId === activeRun?.id,
    canCancel: !submitting && (!cancellingId || cancellingId === activeRun?.id),
    canSubmit: enabled && researchAvailable && native.canSubmit && !ownActiveResearch && !unconfirmedSubmission && phase === 'ready' && !!csrf && !submitting && !cancellingId,
    csrfToken: csrf, invalidateSession,
    refresh, selectRun, loadMore, cancel, submit, investigateBridges, native, discoveryIntent, ownActiveResearch,
  }
}
