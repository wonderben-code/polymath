import { useCallback, useEffect, useRef, useState } from 'react'
import { useNativeResearchSubmission } from '../research/nativeContext'
import { bridgeMatchesReceipt, ExploreCollectionError, getExploreCollectionSummary, listExploreBridges } from './client'
import { equal } from './primitives'
import type { ExploreBridgeDetail, ExploreCollectionIntent, ExploreCollectionSummary } from './types'
const ACTIVE = new Set(['queued', 'running', 'waiting_retry', 'cancel_requested'])
const TERMINAL = new Set(['succeeded', 'failed', 'cancelled', 'interrupted'])
export function useExploreCollectionWorkspace({ runId, intent, enabled, csrfToken, activitySequence, runStatus, onSessionExpired }: { runId: string; intent: ExploreCollectionIntent; enabled: boolean; csrfToken: string | null; activitySequence: number; runStatus: string; onSessionExpired: () => void }) {
  const authority = useNativeResearchSubmission(), conservative = authority.required && authority.access?.audience !== 'operator_browser'
  const [summary, setSummary] = useState<ExploreCollectionSummary | null>(null), [stages, setStages] = useState<ExploreBridgeDetail[]>([]), [phase, setPhase] = useState<'loading' | 'ready' | 'error' | 'expired'>('loading'), [error, setError] = useState(''), [reading, setReading] = useState(false), [paused, setPaused] = useState(false), [retryUntil, setRetryUntil] = useState<number | null>(null)
  const generation = useRef(0), blocked = useRef(false), requests = useRef(new Set<AbortController>()), read = useRef<(manual: boolean, terminal?: boolean) => void>(() => {}), notify = useRef(onSessionExpired), latest = useRef<{ summary: ExploreCollectionSummary | null; stages: ExploreBridgeDetail[] }>({ summary: null, stages: [] }), activity = useRef({ runId, runStatus, sequence: activitySequence })
  notify.current = onSessionExpired
  const expire = useCallback(() => { if (blocked.current) return; blocked.current = true; generation.current++; requests.current.forEach((request) => request.abort()); latest.current = { summary: null, stages: [] }; setSummary(null); setStages([]); setPhase('expired'); setError(''); setReading(false); setPaused(false); setRetryUntil(null); notify.current() }, [])
  useEffect(() => {
    const scope = ++generation.current; blocked.current = false; let active = true, inFlight = false, again = false, terminalPending = false, count = 0, stopped = false, nextReadAt = 0, limitedUntil = 0, timer: number | undefined
    requests.current.forEach((request) => request.abort()); requests.current.clear(); latest.current = { summary: null, stages: [] }; setSummary(null); setStages([]); setPhase('loading'); setError(''); setReading(false); setPaused(false); setRetryUntil(null)
    const current = (signal: AbortSignal) => active && scope === generation.current && !blocked.current && !signal.aborted
    function requestRead(manual: boolean, terminal = false) {
      if (!enabled || !csrfToken || !active || blocked.current || stopped && !manual && !terminal) return
      if (manual) { count = 0; stopped = false; setPaused(false) }
      if (inFlight) { again = true; terminalPending ||= terminal; return }
      window.clearTimeout(timer)
      const allowedAt = terminal || manual ? limitedUntil : Math.max(limitedUntil, nextReadAt)
      if (Date.now() < allowedAt) { timer = window.setTimeout(() => requestRead(manual, terminal), allowedAt - Date.now()); return }
      void load()
    }
    async function load() {
      inFlight = true; count++; again = false; terminalPending = false; nextReadAt = Date.now() + (conservative ? 15000 : 3000); setReading(true)
      const controller = new AbortController(); requests.current.add(controller)
      try {
        const saved = await listExploreBridges(runId, controller.signal), total = await getExploreCollectionSummary(runId, controller.signal)
        if (!current(controller.signal)) return
        if (!equal(total.explore_collection_intent, intent) || saved.stages.some((detail) => !total.stages.some((receipt) => receipt.stage_id === detail.stage.stage_id && bridgeMatchesReceipt(detail, receipt)))) throw new ExploreCollectionError()
        const previous = latest.current
        if (previous.summary?.stages.some((receipt) => !total.stages.some((next) => equal(next, receipt))) || previous.summary?.rankings.some((ranking) => !total.rankings.some((next) => equal(next, ranking))) || previous.stages.some((detail) => !saved.stages.some((next) => bridgeMatchesReceipt(next, detail.stage)))) throw new ExploreCollectionError()
        latest.current = { summary: total, stages: saved.stages }; setSummary(total); setStages(saved.stages); setPhase('ready'); setError(''); setRetryUntil(null); limitedUntil = 0
        if (saved.stages.length < total.stages.length) again = true
        if (count >= 120) { stopped = true; setPaused(true) }
      } catch (caught) {
        if (!current(controller.signal)) return
        if (caught instanceof ExploreCollectionError && caught.code === 'session_expired') { expire(); return }
        if (caught instanceof ExploreCollectionError && caught.code === 'resource_limited') {
          limitedUntil = Date.now() + (caught.retryAfterSeconds ?? 60) * 1000; setRetryUntil(limitedUntil); setError('Saved collection reads are temporarily limited. Existing bridges remain visible; no model task was started.'); again = true
        } else { stopped = true; setPaused(true); if (!latest.current.summary) setPhase('error'); setError(caught instanceof ExploreCollectionError && caught.code === 'explore_collection_not_found' ? 'This saved Explore collection is unavailable in the current browser session.' : 'The saved collection could not be verified. Refresh its saved records to check again; no model request was started.') }
      } finally {
        if (count >= 120) { stopped = true; if (scope === generation.current && active && !blocked.current) setPaused(true) }
        requests.current.delete(controller); inFlight = false
        if (scope === generation.current && active && !blocked.current) { setReading(false); if (!stopped && (again || ACTIVE.has(latest.current.summary?.run_status ?? runStatus))) { const terminal = terminalPending; timer = window.setTimeout(() => requestRead(false, terminal), terminal ? Math.max(0, limitedUntil - Date.now()) : Math.max(0, nextReadAt - Date.now(), limitedUntil - Date.now())) } }
      }
    }
    activity.current = { runId, runStatus, sequence: activitySequence }; read.current = requestRead; requestRead(false)
    return () => { active = false; generation.current++; window.clearTimeout(timer); requests.current.forEach((request) => request.abort()); requests.current.clear(); read.current = () => {} }
  }, [runId, intent.max_bridges, enabled, csrfToken, conservative, expire])
  useEffect(() => {
    const old = activity.current
    if (old.runId !== runId || old.runStatus !== runStatus || old.sequence !== activitySequence) { activity.current = { runId, runStatus, sequence: activitySequence }; read.current(false, old.runStatus !== runStatus && TERMINAL.has(runStatus)) }
  }, [runId, runStatus, activitySequence])
  return { summary, stages, phase, error, reading, paused, retryUntil, refresh: useCallback(() => read.current(true), []), expire }
}
