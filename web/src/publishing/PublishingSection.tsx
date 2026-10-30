import { useCallback, useEffect, useId, useState } from 'react'
import type { FormEvent } from 'react'
import { startPublication, publicationJob, publicationDoiUrl } from '../publications/deliveryClient'
import { approvePublication, getPublication, loginOperator, operatorSession, PublicationError } from '../publications/client'
import type { OperatorSession } from '../publications/client'
import type { NativeSubmissionContext } from '../research/nativeAdmission'
import { attachPublishingStep, publishingItems, publishingPreferences, publishingStatus, savePublishingPreferences, skipPublishingItem } from './client'
import type { Level, PublishingItem, PublishingPreferences, PublishingStatus } from './client'
import { usePublishingAutopilot } from './usePublishingAutopilot'
import './publishing.css'

const LEVELS: { level: Level; title: string; line: string }[] = [
  { level: 1, title: 'Ask me every time', line: 'Nothing happens on its own. Write and publish by hand from any finding.' },
  { level: 2, title: 'Draft for me', line: 'When a finding holds, Polymath writes it up and prepares the Zenodo record. You tap Publish.' },
  { level: 3, title: 'Publish to the sandbox', line: 'As Draft for me, then published to Zenodo’s sandbox, a private test space. Nothing permanent.' },
  { level: 4, title: 'Publish for real', line: 'Published to Zenodo with a permanent DOI, within your evidence bar and daily cap. Confirmed once.' },
]

const STATUS_WORDS: Record<PublishingItem['status'], string> = {
  pending_write: 'Waiting to be written', writing: 'Being written', drafted: 'Written · preparing the record', ready: 'Ready to publish',
  approved: 'Approved · starting to publish', queued: 'Publishing', published: 'Published', failed: 'Did not complete', skipped: 'Skipped',
}

const REASONS: Record<string, string> = {
  write_up_not_ready: 'the write-up could not be completed from the evidence',
  publication_failed: 'Zenodo did not accept it',
  publication_cancelled: 'publishing was cancelled',
  skipped_by_publisher: 'you skipped it',
  publishing_metadata_invalid: 'the default authors need attention',
}

function needs(level: Level): 'sandbox' | 'production' | null { return level === 3 ? 'sandbox' : level === 4 ? 'production' : null }

export interface PublishingSectionProps {
  csrfToken: string | null
  enabled: boolean
  native?: NativeSubmissionContext
}

export function PublishingSection({ csrfToken, enabled, native }: PublishingSectionProps) {
  const uid = useId()
  const [status, setStatus] = useState<PublishingStatus | null>(null)
  const [operator, setOperator] = useState<OperatorSession | null>(null)
  const [code, setCode] = useState('')
  const [saved, setSaved] = useState<PublishingPreferences | null>(null)
  const [draft, setDraft] = useState<PublishingPreferences | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [items, setItems] = useState<PublishingItem[]>([])
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const operatorCsrf = operator?.authenticated ? operator.csrf_token : null

  const refreshItems = useCallback(async () => { try { setItems(await publishingItems()) } catch { /* The list stays as it was. */ } }, [])

  useEffect(() => {
    if (!enabled) return
    const controller = new AbortController()
    // Visitors' browsers only ask about a publisher session where publishing exists.
    publishingStatus(controller.signal).then((value) => {
      setStatus(value)
      if (value.configured) operatorSession(controller.signal).then(setOperator).catch(() => setOperator(null))
    }).catch(() => setStatus(null))
    return () => controller.abort()
  }, [enabled])

  useEffect(() => {
    if (!operatorCsrf) { setSaved(null); setDraft(null); setItems([]); return }
    const controller = new AbortController()
    publishingPreferences(controller.signal).then((value) => { setSaved(value); setDraft(value) }).catch(() => setMessage('Your publishing choice could not be loaded.'))
    void refreshItems()
    return () => controller.abort()
  }, [operatorCsrf, refreshItems])

  const autopilot = usePublishingAutopilot({ csrf: csrfToken, operatorCsrf, level: saved?.level ?? 1, native, onProgress: () => void refreshItems(), onOperatorExpired: () => setOperator((value) => value ? { ...value, authenticated: false, csrf_token: null } : value) })

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!csrfToken || !code) return
    setBusy(true); setMessage('')
    try { setOperator(await loginOperator(code, csrfToken)); setCode('') } catch { setMessage('That publisher code was not accepted.') } finally { setBusy(false) }
  }

  async function save(confirmProduction = false) {
    if (!draft || !csrfToken || !operatorCsrf) return
    if (draft.level === 4 && !saved?.production_confirmed && !confirmProduction) { setConfirming(true); return }
    setBusy(true); setMessage('')
    try {
      const { enabled_at: _enabled, production_confirmed: _confirmed, ...body } = draft
      const value = await savePublishingPreferences({ ...body, confirm_production: confirmProduction }, csrfToken, operatorCsrf)
      setSaved(value); setDraft(value); setConfirming(false)
      setMessage(value.level === 1 ? 'Saved. Polymath will not publish on its own.' : 'Saved. New findings that hold from now on will follow this choice.')
    } catch (failure) {
      const reason = failure instanceof PublicationError ? failure.code : ''
      setMessage(reason === 'invalid_request' ? 'Add at least one author with a name to let Polymath draft or publish.' : 'This choice could not be saved. Check that this destination is connected.')
    } finally { setBusy(false) }
  }

  async function publishNow(item: PublishingItem) {
    if (!csrfToken || !operatorCsrf || !item.manifest_id) return
    setBusy(true); setMessage('')
    try {
      const record = await getPublication(item.manifest_id)
      const approved = await approvePublication(record, csrfToken, operatorCsrf)
      if (!approved.approval) throw new PublicationError('invalid_response')
      await attachPublishingStep(item.id, 'approve', approved.approval.id, csrfToken, operatorCsrf)
      const job = await startPublication(approved, approved.approval.id, csrfToken, operatorCsrf)
      await attachPublishingStep(item.id, 'queue', job.id, csrfToken, operatorCsrf)
    } catch { setMessage('It could not be published right now. It stays ready; try again shortly.') } finally { setBusy(false); void refreshItems() }
  }

  async function skip(item: PublishingItem) {
    if (!csrfToken || !operatorCsrf) return
    try { await skipPublishingItem(item.id, csrfToken, operatorCsrf) } catch { /* Shown unchanged. */ } finally { void refreshItems() }
  }

  const destinations = status?.destinations ?? []
  const connection = !status ? 'Checking the Zenodo connection…'
    : !status.configured ? 'Zenodo is not connected on this server yet.'
    : `Connected to Zenodo ${destinations.length === 2 ? 'sandbox and production' : destinations[0] === 'production' ? 'production' : 'sandbox'} as ${status.publisher}.`
  const creator = draft?.creators[0] ?? { name: '', affiliation: null, orcid: null }

  // Visitors publish through Your Zenodo; this is the site publisher's own path.
  if (status && !status.configured) return null
  return (
    <section className="publishing" aria-labelledby={`${uid}-title`}>
      <h2 id={`${uid}-title`}>Publish to Zenodo</h2>
      <p className="publishing-lede">Turn findings that hold into citable records with a DOI. Choose once how much Polymath does on its own.</p>
      <p className={`publishing-connection${status?.configured ? ' is-connected' : ''}`}>{connection}{status?.configured && !status.publishing_runs ? ' Publishing itself is paused, so prepared records wait safely.' : ''}</p>
      {status && !status.configured && <p className="publishing-note">The owner connects Zenodo once in the server settings, then chooses a level here.</p>}

      {status?.configured && !operatorCsrf && <form className="publishing-signin" onSubmit={(event) => void signIn(event)}>
        <label htmlFor={`${uid}-code`}>Publisher sign-in</label>
        <p className="publishing-note">Publishing acts under the publisher’s Zenodo connection, so it needs the publisher’s code.</p>
        <div><input id={`${uid}-code`} type="password" autoComplete="off" value={code} onChange={(event) => setCode(event.target.value)} maxLength={256} /><button type="submit" disabled={!code || busy || !csrfToken}>Sign in</button></div>
      </form>}

      {operatorCsrf && draft && <>
        <fieldset className="publishing-levels">
          <legend>How much should Polymath do?</legend>
          {LEVELS.map((option) => {
            const destination = needs(option.level)
            const available = !destination || destinations.includes(destination)
            return <label key={option.level} className={`publishing-level${draft.level === option.level ? ' is-chosen' : ''}${available ? '' : ' is-unavailable'}`}>
              <input type="radio" name={`${uid}-level`} value={option.level} checked={draft.level === option.level} disabled={!available} onChange={() => { setDraft({ ...draft, level: option.level }); setConfirming(false) }} />
              <span className="publishing-level-title">{option.title}</span>
              <span className="publishing-level-line">{available ? option.line : 'Not connected on this server.'}</span>
            </label>
          })}
        </fieldset>

        {confirming && <div className="publishing-confirm" role="alertdialog" aria-labelledby={`${uid}-confirm`}>
          <h3 id={`${uid}-confirm`}>Publish permanently, on your behalf?</h3>
          <p>Each new finding that holds{draft.full_text_only ? ' and quotes full text' : ''} will be written up and published to Zenodo with a permanent, public DOI, up to {draft.daily_cap} a day. You can lower the level at any time; anything already published stays published.</p>
          <div><button type="button" className="publishing-primary" onClick={() => void save(true)} disabled={busy}>Yes, publish for real</button><button type="button" onClick={() => setConfirming(false)}>Not now</button></div>
        </div>}

        <details className="publishing-defaults">
          <summary>Authors, licence and write-up</summary>
          <div className="publishing-grid">
            <label>Author name<input value={creator.name} maxLength={200} onChange={(event) => setDraft({ ...draft, creators: [{ ...creator, name: event.target.value }, ...draft.creators.slice(1)] })} placeholder="Family name, Given name" /></label>
            <label>Affiliation (optional)<input value={creator.affiliation ?? ''} maxLength={300} onChange={(event) => setDraft({ ...draft, creators: [{ ...creator, affiliation: event.target.value || null }, ...draft.creators.slice(1)] })} /></label>
            <label>ORCID (optional)<input value={creator.orcid ?? ''} maxLength={19} placeholder="0000-0000-0000-0000" onChange={(event) => setDraft({ ...draft, creators: [{ ...creator, orcid: event.target.value || null }, ...draft.creators.slice(1)] })} /></label>
            <label>Licence<select value={draft.license} onChange={(event) => setDraft({ ...draft, license: event.target.value as PublishingPreferences['license'] })}><option value="cc-by-4.0">CC BY 4.0</option><option value="cc0-1.0">CC0 1.0</option></select></label>
            <label>Write-up<select value={draft.write_kind} onChange={(event) => setDraft({ ...draft, write_kind: event.target.value as PublishingPreferences['write_kind'] })}><option value="write_paper">A research paper</option><option value="write_summary">A plain-English summary</option></select></label>
            <label>Keywords (comma separated)<input value={draft.keywords.join(', ')} onChange={(event) => setDraft({ ...draft, keywords: event.target.value.split(',').map((value) => value.trim()).filter(Boolean).slice(0, 20) })} /></label>
            <label>At most this many a day<input type="number" min={1} max={10} step={1} value={draft.daily_cap} onChange={(event) => setDraft({ ...draft, daily_cap: Math.max(1, Math.min(10, Number(event.target.value) || 1)) })} /></label>
            {draft.level === 2 && destinations.length === 2 && <label>Drafts publish to<select value={draft.draft_destination} onChange={(event) => setDraft({ ...draft, draft_destination: event.target.value as PublishingPreferences['draft_destination'] })}><option value="sandbox">Zenodo sandbox</option><option value="production">Zenodo (permanent)</option></select></label>}
          </div>
          <label className="publishing-check"><input type="checkbox" checked={draft.full_text_only} onChange={(event) => setDraft({ ...draft, full_text_only: event.target.checked })} />Only findings that quote full-text papers, not abstracts alone</label>
        </details>

        <div className="publishing-save">
          <button type="button" className="publishing-primary" onClick={() => void save()} disabled={busy || JSON.stringify(draft) === JSON.stringify(saved)}>Save this choice</button>
          {(message || autopilot.status) && <p role="status">{message || autopilot.status}</p>}
        </div>

        <section className="publishing-items" aria-labelledby={`${uid}-items`}>
          <h3 id={`${uid}-items`}>Your publications</h3>
          {items.length === 0 ? <p className="publishing-note">{saved && saved.level >= 2 ? 'Nothing yet. New findings that hold will appear here as Polymath writes them up.' : 'Nothing yet.'}</p>
            : <ul>{items.map((item) => <PublishingRow key={item.id} item={item} canPublish={item.status === 'ready' && !busy} onPublish={() => void publishNow(item)} onSkip={() => void skip(item)} />)}</ul>}
        </section>
      </>}
      {message && !operatorCsrf && <p className="publishing-note" role="status">{message}</p>}
    </section>
  )
}

function PublishingRow({ item, canPublish, onPublish, onSkip }: { item: PublishingItem; canPublish: boolean; onPublish: () => void; onSkip: () => void }) {
  const [doi, setDoi] = useState<string | null>(null)
  useEffect(() => {
    if (item.status !== 'published' || !item.manifest_id || !item.job_id) return
    const controller = new AbortController()
    getPublication(item.manifest_id, controller.signal).then((record) => publicationJob(item.job_id!, record, controller.signal)).then((job) => setDoi(job.doi)).catch(() => setDoi(null))
    return () => controller.abort()
  }, [item.status, item.manifest_id, item.job_id])
  const link = doi ? publicationDoiUrl(doi) : null
  return <li className={`publishing-item is-${item.status}`}>
    <div>
      <span className="publishing-item-title">{item.title}</span>
      <span className="publishing-item-meta">{STATUS_WORDS[item.status]}{item.status === 'failed' && item.reason ? `: ${REASONS[item.reason] ?? 'see the record for details'}` : ''} · {item.destination === 'production' ? 'Zenodo' : 'Zenodo sandbox'} · {item.write_kind === 'write_paper' ? 'paper' : 'summary'}</span>
    </div>
    <div className="publishing-item-actions">
      {item.status === 'ready' && item.level === 2 && <button type="button" className="publishing-primary" onClick={onPublish} disabled={!canPublish}>Publish</button>}
      {['pending_write', 'writing', 'drafted', 'ready', 'approved'].includes(item.status) && <button type="button" onClick={onSkip}>Skip</button>}
      {link && <a href={link} target="_blank" rel="noopener noreferrer">DOI {doi}</a>}
    </div>
  </li>
}
