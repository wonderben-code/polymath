import { useEffect, useRef, useState } from 'react'
import { parseActivity } from '../research/client'
import type { ResearchActivityEvent } from '../research/types'

export type AssessmentConnection = 'connecting' | 'live' | 'reconnecting' | 'closed' | 'error'
const RETRIES = [500, 1500, 4000]
const UUID = /^[a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12}$/

/** Read only: activity never authorizes a reassessment or retries model work. */
export function useAssessmentActivity({ runId, enabled, onStatus, onSessionExpired }: {
  runId: string | null
  enabled: boolean
  onStatus: () => void | Promise<void>
  onSessionExpired: () => void
}) {
  const [events, setEvents] = useState<ResearchActivityEvent[]>([])
  const [connection, setConnection] = useState<AssessmentConnection>('closed')
  const status = useRef(onStatus), expire = useRef(onSessionExpired)
  status.current = onStatus; expire.current = onSessionExpired
  useEffect(() => {
    setEvents([]); setConnection('closed')
    if (!enabled || !runId || !UUID.test(runId)) return
    let source: EventSource | null = null, deadline: number | undefined, retry: number | undefined, refresh: number | undefined
    let active = true, attempts = 0, cursor = 0
    const recorded = new Map<number, ResearchActivityEvent>()
    function close() { window.clearTimeout(deadline); window.clearTimeout(refresh); refresh = undefined; source?.close(); source = null }
    function requestStatus() { if (active) void Promise.resolve(status.current()).catch(() => { /* The owning workspace presents status errors. */ }) }
    function refreshSoon() {
      if (refresh !== undefined) return
      refresh = window.setTimeout(() => { refresh = undefined; requestStatus() }, 200)
    }
    function connect() {
      if (!active) return
      if (typeof EventSource === 'undefined') { setConnection('error'); requestStatus(); return }
      const stream = new EventSource(`/api/v1/runs/${runId}/events?after=${cursor}`)
      source = stream; setConnection(attempts ? 'reconnecting' : 'connecting')
      const current = () => active && source === stream
      const watch = (milliseconds: number) => { window.clearTimeout(deadline); deadline = window.setTimeout(disconnected, milliseconds) }
      function disconnected() {
        if (!current()) return
        close(); requestStatus()
        if (attempts >= RETRIES.length) { setConnection('error'); return }
        setConnection('reconnecting'); retry = window.setTimeout(connect, RETRIES[attempts++])
      }
      stream.addEventListener('open', () => { if (current()) { setConnection('live'); watch(45000) } })
      stream.addEventListener('heartbeat', () => { if (current()) watch(45000) })
      stream.addEventListener('activity', (raw) => {
        if (!current()) return
        try {
          const incoming = raw as MessageEvent<string>
          if (typeof incoming.data !== 'string' || incoming.data.length > 16000) throw new Error('invalid activity')
          const event = parseActivity(JSON.parse(incoming.data)), prior = recorded.get(event.sequence)
          if (prior && (prior.kind !== event.kind || prior.summary !== event.summary) || !prior && recorded.size >= 2000) throw new Error('inconsistent activity')
          recorded.set(event.sequence, event); cursor = Math.max(cursor, event.sequence)
          setEvents([...recorded.values()].sort((left, right) => left.sequence - right.sequence)); watch(45000); refreshSoon()
        } catch { close(); setConnection('error'); requestStatus() }
      })
      stream.addEventListener('session_expired', () => { if (current()) { close(); setEvents([]); setConnection('closed'); expire.current() } })
      stream.addEventListener('end', () => { if (current()) { close(); setConnection('closed'); requestStatus() } })
      stream.addEventListener('error', disconnected)
      watch(10000)
    }
    connect()
    return () => { active = false; close(); window.clearTimeout(retry); window.clearTimeout(refresh) }
  }, [runId, enabled])
  return { events, connection }
}
