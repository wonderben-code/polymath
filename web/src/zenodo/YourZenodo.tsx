import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { PLACES } from '../shell/SiteChrome'
import type { Destination } from '../publications/client'
import { beginZenodoConnect, completeZenodoConnect, DESTINATION_WORDS, disconnectZenodo, errorWords, retryZenodoRecord, STATUS_WORDS, zenodoRecords, zenodoStatus, ZenodoError } from './client'
import type { VisitorPublication, ZenodoStatus } from './client'
import './zenodo.css'

export const CALLBACK_PATH = '/zenodo/callback'
const ACTIVE = new Set(['queued', 'running', 'checking'])

export const navigation = { assign: (url: string) => window.location.assign(url) }

/** Leave for Zenodo's own sign-in page; Polymath never sees the password. */
export async function connectZenodo(target: Destination, csrf: string | null): Promise<void> {
  navigation.assign(await beginZenodoConnect(target, csrf))
}

export function recordLine(record: VisitorPublication): string {
  const where = DESTINATION_WORDS[record.destination].name
  if (record.status === 'published') return `${where} · ${record.doi ? `DOI ${record.doi}` : 'Published'}`
  if (record.status === 'missing') return `${where} · Nothing reached Zenodo, so nothing was published.`
  if (record.status === 'reconnect') return `${where} · Connect Zenodo again, then try again.`
  return `${where} · ${STATUS_WORDS[record.status]}`
}

interface Props { csrfToken: string | null; enabled: boolean }

export function YourZenodo({ csrfToken, enabled }: Props) {
  const uid = useId()
  const heading = useRef<HTMLHeadingElement>(null)
  const pending = useRef<{ code: string; state: string } | null>(null)
  const [status, setStatus] = useState<ZenodoStatus | null>(null)
  const [records, setRecords] = useState<VisitorPublication[]>([])
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const value = await zenodoStatus(signal)
      setStatus(value)
      if (value.available) setRecords(await zenodoRecords(undefined, signal))
    } catch { /* Stays as it was; nothing here is required to research. */ }
  }, [])

  // Zenodo returns here once. Take the one-time code out of the address bar at once.
  useEffect(() => {
    if (window.location.pathname !== CALLBACK_PATH) return
    const params = new URLSearchParams(window.location.search)
    window.history.replaceState(null, '', PLACES.find((item) => item.id === 'library')!.path)
    const code = params.get('code'), state = params.get('state')
    if (params.get('error') || !code || !state) setMessage('Zenodo sign-in was cancelled, so nothing was connected.')
    else { pending.current = { code, state }; setMessage('Finishing the connection with Zenodo…') }
    window.setTimeout(() => heading.current?.scrollIntoView?.({ block: 'start' }), 0)
  }, [])

  useEffect(() => {
    if (!enabled || !csrfToken) return
    const controller = new AbortController()
    const finish = pending.current
    pending.current = null
    void (async () => {
      if (finish) {
        try {
          const target = await completeZenodoConnect(finish.code, finish.state, csrfToken)
          setMessage(`Connected to ${DESTINATION_WORDS[target].name}. Open any written finding to publish it under your name.`)
        } catch (failure) { setMessage(errorWords(failure instanceof ZenodoError ? failure.code : '')) }
      }
      await refresh(controller.signal)
    })()
    return () => controller.abort()
  }, [enabled, csrfToken, refresh])

  const working = records.some((record) => ACTIVE.has(record.status))
  useEffect(() => {
    if (!working) return
    const timer = window.setInterval(() => void refresh(), 4000)
    return () => window.clearInterval(timer)
  }, [working, refresh])

  async function act(work: () => Promise<void>) {
    setBusy(true); setMessage('')
    try { await work() } catch (failure) { setMessage(errorWords(failure instanceof ZenodoError ? failure.code : '')) } finally { setBusy(false) }
  }

  if (!status?.available) return message ? <p className="zenodo-message" role="status">{message}</p> : null
  const published = records.filter((record) => record.status !== 'preview')
  return (
    <section className="zenodo" aria-labelledby={`${uid}-title`}>
      <h2 id={`${uid}-title`} ref={heading}>Your Zenodo</h2>
      <p className="zenodo-lede">Publish findings under your own name. Zenodo asks you to approve Polymath on its own site; Polymath never sees your password, and can only create and publish records. Disconnect at any time.</p>
      {message && <p className="zenodo-message" role="status">{message}</p>}
      <ul className="zenodo-destinations">
        {status.destinations.map((row) => <li key={row.destination} className={row.connected ? 'is-connected' : ''}>
          <div><span className="zenodo-destination-name">{DESTINATION_WORDS[row.destination].name}</span><span className="zenodo-destination-line">{row.connected ? 'Connected.' : DESTINATION_WORDS[row.destination].line}</span></div>
          {row.connected
            ? <button type="button" disabled={busy} onClick={() => void act(async () => { setStatus(await disconnectZenodo(row.destination, csrfToken)); setMessage(`Disconnected from ${DESTINATION_WORDS[row.destination].name}. Its sign-in has been deleted.`) })}>Disconnect</button>
            : <button type="button" className="zenodo-primary" disabled={busy || !csrfToken} onClick={() => void act(() => connectZenodo(row.destination, csrfToken))}>Connect</button>}
        </li>)}
      </ul>
      {published.length > 0 && <section className="zenodo-records" aria-labelledby={`${uid}-records`}>
        <h3 id={`${uid}-records`}>Your records</h3>
        <ul>{published.map((record) => <li key={record.id} className={`zenodo-record is-${record.status}`}>
          <div><span className="zenodo-record-title">{record.title}</span><span className="zenodo-record-meta">{recordLine(record)}</span></div>
          <div className="zenodo-record-actions">
            {record.status === 'published' && (record.record_url || record.doi_url) && <a href={record.record_url ?? record.doi_url ?? undefined} target="_blank" rel="noreferrer">Open on Zenodo</a>}
            {['failed', 'missing', 'reconnect'].includes(record.status) && <button type="button" disabled={busy} onClick={() => void act(async () => { await retryZenodoRecord(record.id, csrfToken); await refresh() })}>Try again</button>}
          </div>
        </li>)}</ul>
      </section>}
    </section>
  )
}
