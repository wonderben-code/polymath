import { useEffect, useId, useState } from 'react'
import type { FormEvent } from 'react'
import type { SavedArtifact } from '../artifacts/types'
import { validOrcid } from '../publications/client'
import type { Destination } from '../publications/client'
import { DESTINATION_WORDS, errorWords, previewZenodoRecord, publishZenodoRecord, retryZenodoRecord, zenodoRecord, zenodoRecords, ZenodoError } from './client'
import type { VisitorPublication, ZenodoMetadata, ZenodoStatus } from './client'
import { connectZenodo, recordLine } from './YourZenodo'
import './zenodo.css'

const AUTHOR_KEY = 'polymath.zenodo.author'
const ACTIVE = new Set(['queued', 'running', 'checking'])

function rememberedAuthor(): { name: string; orcid: string } {
  try {
    const value = JSON.parse(window.localStorage.getItem(AUTHOR_KEY) ?? '{}') as { name?: unknown; orcid?: unknown }
    return { name: typeof value.name === 'string' ? value.name.slice(0, 200) : '', orcid: typeof value.orcid === 'string' ? value.orcid.slice(0, 19) : '' }
  } catch { return { name: '', orcid: '' } }
}

function remember(name: string, orcid: string) {
  try { window.localStorage.setItem(AUTHOR_KEY, JSON.stringify({ name, orcid })) } catch { /* A convenience only. */ }
}

function summary(artifact: SavedArtifact): string {
  const draft = artifact.payload.draft
  return draft && draft.kind !== 'formalisation' ? draft.abstract.map((paragraph) => paragraph.text).join('\n\n').slice(0, 5000) : ''
}

interface Props { artifact: SavedArtifact; csrfToken: string | null; status: ZenodoStatus; sourceCurrent: boolean }

export function PublishToZenodo({ artifact, csrfToken, status, sourceCurrent }: Props) {
  const uid = useId()
  const connected = status.destinations.filter((row) => row.connected).map((row) => row.destination)
  const [open, setOpen] = useState(false)
  const [target, setTarget] = useState<Destination>(connected.includes('sandbox') || !connected.length ? 'sandbox' : 'production')
  const [author] = useState(rememberedAuthor)
  const [name, setName] = useState(author.name)
  const [orcid, setOrcid] = useState(author.orcid)
  const [title, setTitle] = useState(artifact.payload.draft?.title ?? '')
  const [description, setDescription] = useState(() => summary(artifact))
  const [license, setLicense] = useState<ZenodoMetadata['license']>('cc-by-4.0')
  const [record, setRecord] = useState<VisitorPublication | null>(null)
  const [earlier, setEarlier] = useState<VisitorPublication[]>([])
  const [confirming, setConfirming] = useState(false)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    zenodoRecords(artifact.id, controller.signal).then((list) => setEarlier(list.filter((item) => item.status !== 'preview'))).catch(() => undefined)
    return () => controller.abort()
  }, [artifact.id])

  const live = record && ACTIVE.has(record.status) ? record.id : null
  useEffect(() => {
    if (!live) return
    const timer = window.setInterval(() => { zenodoRecord(live).then(setRecord).catch(() => undefined) }, 3000)
    return () => window.clearInterval(timer)
  }, [live])

  async function act(work: () => Promise<void>) {
    setBusy(true); setMessage('')
    try { await work() } catch (failure) { setMessage(errorWords(failure instanceof ZenodoError ? failure.code : '')) } finally { setBusy(false) }
  }

  const orcidValid = !orcid.trim() || validOrcid(orcid.trim())
  const canReview = sourceCurrent && connected.includes(target) && !!name.trim() && !!title.trim() && !!description.trim() && orcidValid && !busy

  function review(event: FormEvent) {
    event.preventDefault()
    if (!canReview) return
    remember(name.trim(), orcid.trim())
    void act(async () => {
      setConfirming(false)
      setRecord(await previewZenodoRecord(artifact.id, target, {
        title: title.trim(), description: description.trim(), keywords: [], license,
        creators: [{ name: name.trim(), affiliation: null, orcid: orcid.trim() || null }],
      }, csrfToken))
    })
  }

  function publish() {
    if (!record) return
    if (record.destination === 'production' && !confirming) { setConfirming(true); return }
    void act(async () => { setRecord(await publishZenodoRecord(record, record.destination === 'production', csrfToken)); setConfirming(false) })
  }

  function edit() { setRecord(null); setConfirming(false) }

  return (
    <section className="zenodo-publish" aria-labelledby={`${uid}-title`}>
      <div className="zenodo-publish-head">
        <h3 id={`${uid}-title`}>Publish to your Zenodo</h3>
        {!open && <button type="button" onClick={() => setOpen(true)}>Publish this…</button>}
      </div>
      {earlier.length > 0 && !record && <ul className="zenodo-earlier">{earlier.map((item) => <li key={item.id}>{item.status === 'published' && (item.record_url || item.doi_url) ? <a href={item.record_url ?? item.doi_url ?? undefined} target="_blank" rel="noreferrer">{recordLine(item)}</a> : recordLine(item)}</li>)}</ul>}
      {open && !connected.length && <div className="zenodo-connect-first">
        <p>Connect your Zenodo account first. You approve Polymath on Zenodo’s own site, then come back here to your library.</p>
        <div>{status.destinations.map((row) => <button key={row.destination} type="button" className={row.destination === 'sandbox' ? 'zenodo-primary' : ''} disabled={busy || !csrfToken} onClick={() => void act(() => connectZenodo(row.destination, csrfToken))}>Connect {DESTINATION_WORDS[row.destination].name}</button>)}</div>
      </div>}
      {open && connected.length > 0 && !record && <form className="zenodo-form" onSubmit={review}>
        {!sourceCurrent && <p className="zenodo-message" role="alert">This finding has changed since it was written, so this version cannot be published.</p>}
        <fieldset className="zenodo-where">
          <legend>Where</legend>
          {status.destinations.map((row) => <label key={row.destination} className={`${target === row.destination ? 'is-chosen' : ''}${row.connected ? '' : ' is-unavailable'}`}>
            <input type="radio" name={`${uid}-where`} value={row.destination} checked={target === row.destination} disabled={!row.connected} onChange={() => setTarget(row.destination)} />
            <span className="zenodo-destination-name">{row.destination === 'sandbox' ? 'Practice' : 'For real'}</span>
            <span className="zenodo-destination-line">{row.connected ? DESTINATION_WORDS[row.destination].line : `Connect ${DESTINATION_WORDS[row.destination].name} in your library first.`}</span>
          </label>)}
        </fieldset>
        <div className="zenodo-grid">
          <label>Your name<input value={name} maxLength={200} required autoComplete="name" onChange={(event) => setName(event.target.value)} /></label>
          <label>ORCID (optional)<input value={orcid} maxLength={19} placeholder="0000-0000-0000-0000" aria-invalid={!orcidValid} onChange={(event) => setOrcid(event.target.value)} /></label>
          <label className="is-wide">Title<input value={title} maxLength={300} required onChange={(event) => setTitle(event.target.value)} /></label>
          <label className="is-wide">Summary<textarea value={description} maxLength={5000} rows={5} required onChange={(event) => setDescription(event.target.value)} /></label>
          <label>Licence<select value={license} onChange={(event) => setLicense(event.target.value as ZenodoMetadata['license'])}><option value="cc-by-4.0">CC BY 4.0 (credit required)</option><option value="cc0-1.0">CC0 (public domain)</option></select></label>
        </div>
        {!orcidValid && <p className="zenodo-message" role="alert">That ORCID does not look right. Check it, or leave it empty.</p>}
        <div className="zenodo-actions"><button type="submit" className="zenodo-primary" disabled={!canReview}>{busy ? 'Preparing…' : 'Review the record'}</button><button type="button" onClick={() => setOpen(false)} disabled={busy}>Not now</button></div>
      </form>}
      {record && <div className="zenodo-review" aria-label="The exact record">
        <p className="zenodo-review-where">{DESTINATION_WORDS[record.destination].name}{record.destination === 'production' ? ' · permanent, with a DOI' : ' · practice'}</p>
        <h4>{record.title}</h4>
        <p className="zenodo-review-authors">{record.creators.map((creator) => creator.name + (creator.orcid ? ` (ORCID ${creator.orcid})` : '')).join(', ')} · {record.license === 'cc0-1.0' ? 'CC0' : 'CC BY 4.0'}</p>
        <p className="zenodo-review-summary">{record.description}</p>
        <details className="zenodo-fingerprint"><summary>Exact file</summary><p>{record.file_name}<br />SHA-256 {record.markdown_sha256}</p></details>
        {record.status === 'preview' && !confirming && <div className="zenodo-actions"><button type="button" className="zenodo-primary" disabled={busy} onClick={publish}>{record.destination === 'production' ? 'Publish permanently' : 'Publish'}</button><button type="button" disabled={busy} onClick={edit}>Edit</button></div>}
        {record.status === 'preview' && confirming && <div className="zenodo-confirm" role="alertdialog" aria-labelledby={`${uid}-confirm`}>
          <h5 id={`${uid}-confirm`}>Publish permanently?</h5>
          <p>This creates a permanent public record with a DOI under your Zenodo account. It cannot be deleted.</p>
          <div className="zenodo-actions"><button type="button" className="zenodo-primary" disabled={busy} onClick={publish}>Yes, publish</button><button type="button" disabled={busy} onClick={() => setConfirming(false)}>Not yet</button></div>
        </div>}
        {record.status !== 'preview' && <p className={`zenodo-outcome is-${record.status}`} role="status">
          {record.status === 'published'
            ? <>Published. {(record.record_url || record.doi_url) && <a href={record.record_url ?? record.doi_url ?? undefined} target="_blank" rel="noreferrer">{record.doi ? `DOI ${record.doi}` : 'Open on Zenodo'}</a>}</>
            : recordLine(record)}
        </p>}
        {['failed', 'missing', 'reconnect'].includes(record.status) && <div className="zenodo-actions"><button type="button" disabled={busy} onClick={() => void act(async () => setRecord(await retryZenodoRecord(record.id, csrfToken)))}>Try again</button></div>}
      </div>}
      {message && <p className="zenodo-message" role="alert">{message}</p>}
    </section>
  )
}
