import { useCallback, useEffect, useRef, useState } from 'react'
import { DiscoveryError, getDiscoveryStage, getDiscoverySummary, listDiscoveryStages, stageMatchesReceipt } from './client'
import type { DiscoveryIntent, DiscoveryStage, DiscoverySummary } from './types'

const ACTIVE = new Set(['queued', 'running', 'waiting_retry', 'cancel_requested'])
export function useDiscoveryWorkspace({ runId, intent, enabled, csrfToken, activitySequence, runStatus, onSessionExpired }: { runId: string; intent: DiscoveryIntent; enabled: boolean; csrfToken: string | null; activitySequence: number; runStatus: string; onSessionExpired: () => void }) {
  const [summary, setSummary] = useState<DiscoverySummary | null>(null), [stages, setStages] = useState<DiscoveryStage[]>([]), [selectedId, setSelectedId] = useState<string | null>(null), [phase, setPhase] = useState<'loading' | 'ready' | 'error' | 'expired'>('loading'), [error, setError] = useState(''), [paused, setPaused] = useState(false), [reading, setReading] = useState(false), [selectingId, setSelectingId] = useState<string | null>(null)
  const generation = useRef(0), blocked = useRef(false), requests = useRef(new Set<AbortController>()), read = useRef<(manual: boolean) => void>(() => {}), expiredCallback = useRef(onSessionExpired), latest = useRef<{ summary: DiscoverySummary | null; stages: DiscoveryStage[] }>({ summary: null, stages: [] })
  const lastActivity = useRef(`${runId}:${runStatus}:${activitySequence}`)
  expiredCallback.current = onSessionExpired
  const expire = useCallback(() => { if (blocked.current) return; blocked.current = true; generation.current++; requests.current.forEach((request) => request.abort()); requests.current.clear(); latest.current = { summary: null, stages: [] }; setSummary(null); setStages([]); setSelectedId(null); setError(''); setPaused(false); setReading(false); setSelectingId(null); setPhase('expired'); expiredCallback.current() }, [])
  useEffect(() => {
    const scope = ++generation.current; blocked.current = false; let active = true, inFlight = false, again = false, reads = 0, stopped = false, timer: number | undefined
    requests.current.forEach((request) => request.abort()); requests.current.clear(); latest.current = { summary: null, stages: [] }; setSummary(null); setStages([]); setSelectedId(null); setError(''); setPaused(false); setReading(false); setSelectingId(null); setPhase('loading')
    const current = (signal: AbortSignal) => active && scope === generation.current && !blocked.current && !signal.aborted
    function requestRead(manual: boolean) {
      if (!enabled || !csrfToken || !active || blocked.current || stopped && !manual) return
      if (manual) { reads = 0; stopped = false; setPaused(false) }
      if (inFlight) { again = true; return }
      window.clearTimeout(timer); void load()
    }
    async function load() {
      if (inFlight || !active || blocked.current) return
      inFlight = true; again = false; setReading(true); const controller = new AbortController(); requests.current.add(controller)
      try {
        // Read the list first: a later summary may include a newly committed stage,
        // but no listed immutable receipt may disappear or change underneath it.
        const saved = await listDiscoveryStages(runId, controller.signal), total = await getDiscoverySummary(runId, controller.signal)
        if (!current(controller.signal)) return
        if (total.discovery_intent.max_stages !== intent.max_stages || total.discovery_intent.max_depth !== intent.max_depth || saved.stages.some((detail) => !total.stages.some((receipt) => receipt.stage_id === detail.stage.stage_id && stageMatchesReceipt(detail, receipt)))) throw new DiscoveryError('invalid_response')
        const previous = latest.current
        if (previous.stages.some((detail) => !saved.stages.some((next) => next.stage.stage_id === detail.stage.stage_id && stageMatchesReceipt(next, detail.stage)))) throw new DiscoveryError('invalid_response')
        latest.current = { summary: total, stages: saved.stages }; setSummary(total); setStages(saved.stages); setSelectedId((old) => old && saved.stages.some((detail) => detail.stage.stage_id === old) ? old : saved.stages[0]?.stage.stage_id ?? null); setError(''); setPhase('ready'); reads++
        if (saved.stages.length < total.stages.length) again = true
        if (reads >= 120) { stopped = true; setPaused(true) }
        else if (again || ACTIVE.has(total.run_status)) timer = window.setTimeout(() => requestRead(false), again ? 0 : 3000)
      } catch (caught) {
        if (!current(controller.signal)) return
        if (caught instanceof DiscoveryError && caught.code === 'session_expired') expire()
        else { stopped = true; setPaused(true); if (!latest.current.summary) setPhase('error'); setError(caught instanceof DiscoveryError && caught.code === 'discovery_not_found' ? 'This discovery run is unavailable in the current browser session.' : 'Saved discoveries could not be verified. No model or provider request was started. Refresh the saved records to check again.') }
      } finally { requests.current.delete(controller); inFlight = false; if (scope === generation.current && !blocked.current) setReading(false) }
    }
    lastActivity.current = `${runId}:${runStatus}:${activitySequence}`; read.current = requestRead; requestRead(false)
    return () => { active = false; generation.current++; window.clearTimeout(timer); requests.current.forEach((request) => request.abort()); requests.current.clear(); read.current = () => {} }
  }, [runId, intent.max_stages, intent.max_depth, enabled, csrfToken, expire])
  useEffect(() => { const signal = `${runId}:${runStatus}:${activitySequence}`; if (lastActivity.current !== signal) { lastActivity.current = signal; read.current(false) } }, [runId, activitySequence, runStatus])
  const refresh = useCallback(() => read.current(true), [])
  async function select(stageId: string) {
    if (blocked.current || !enabled || !csrfToken || !latest.current.stages.some((detail) => detail.stage.stage_id === stageId)) return
    setSelectedId(stageId); setSelectingId(stageId); const scope = generation.current, controller = new AbortController(); requests.current.add(controller)
    try { const detail = await getDiscoveryStage(runId, stageId, controller.signal); if (scope !== generation.current || blocked.current || controller.signal.aborted) return
      const receipt = latest.current.summary?.stages.find((item) => item.stage_id === stageId)
      if (!receipt || !stageMatchesReceipt(detail, receipt)) throw new DiscoveryError('invalid_response')
      setStages((old) => { const next = old.map((item) => item.stage.stage_id === stageId ? detail : item); latest.current = { ...latest.current, stages: next }; return next }); setError('')
    } catch (caught) { if (scope === generation.current && !controller.signal.aborted && !blocked.current) { if (caught instanceof DiscoveryError && caught.code === 'session_expired') expire(); else setError('This saved stage could not be verified. Its earlier displayed facts have not been used to authorize a new model task.') } }
    finally { requests.current.delete(controller); if (scope === generation.current && !blocked.current) setSelectingId((old) => old === stageId ? null : old) }
  }
  return { summary, stages, selectedId, selected: stages.find((detail) => detail.stage.stage_id === selectedId) ?? null, phase, error, paused, reading, selecting: selectingId === selectedId && selectingId !== null, refresh, select, expire }
}
