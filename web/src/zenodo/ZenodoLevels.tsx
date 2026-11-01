import { useCallback, useEffect, useId, useState } from 'react'
import type { FormEvent } from 'react'
import { validOrcid } from '../publications/client'
import type { Destination } from '../publications/client'
import type { NativeSubmissionContext } from '../research/nativeAdmission'
import { DESTINATION_WORDS, errorWords, publishZenodoRecord, saveZenodoPreferences, skipZenodoItem, zenodoItems, zenodoPreferences, zenodoRecord, ZenodoError } from './client'
import type { ZenodoItem, ZenodoLevel, ZenodoPreferences, ZenodoStatus } from './client'
import { useZenodoAutopilot } from './useZenodoAutopilot'

const LEVELS: { level: ZenodoLevel; title: string; line: string; needs: Destination | 'any' | null }[] = [
  { level: 1, title: 'Ask me every time', line: 'Nothing happens on its own. Publish any written finding when you choose.', needs: null },
  { level: 2, title: 'Draft for me', line: 'When a finding holds, Polymath writes it up and prepares your record. You tap Publish.', needs: 'any' },
  { level: 3, title: 'Publish to practice', line: 'As Draft for me, then published to your Zenodo sandbox. Nothing permanent.', needs: 'sandbox' },
  { level: 4, title: 'Publish for real', line: 'Published to your Zenodo with a permanent DOI, within your daily limit. Confirmed once.', needs: 'production' },
]

const ITEM_WORDS: Record<ZenodoItem['status'], string> = {
  pending_write: 'Waiting to be written', writing: 'Being written', drafted: 'Written · preparing the record', ready: 'Ready to publish',
  queued: 'Publishing', published: 'Published', failed: 'Did not complete', skipped: 'Skipped',
}

const REASONS: Record<string, string> = {
  write_up_not_ready: 'the write-up could not be completed from the evidence',
  skipped_by_you: 'you skipped it',
  publishing_name_required: 'add your name above',
  zenodo_failed: 'Zenodo did not accept it',
  zenodo_missing: 'nothing reached Zenodo',
  zenodo_reconnect: 'connect Zenodo again',
  zenodo_finding_not_current: 'the finding changed after it was written',
  zenodo_daily_limit: 'waiting for tomorrow’s limit',
}

const AUTHOR_KEY = 'polymath.zenodo.author'
function rememberedAuthor(): { name: string; orcid: string } {
  try { const value = JSON.parse(window.localStorage.getItem(AUTHOR_KEY) ?? '{}') as { name?: unknown; orcid?: unknown }; return { name: typeof value.name === 'string' ? value.name : '', orcid: typeof value.orcid === 'string' ? value.orcid : '' } } catch { return { name: '', orcid: '' } }
}

interface Props { csrfToken: string | null; status: ZenodoStatus; native?: NativeSubmissionContext }

export function ZenodoLevels({ csrfToken, status, native }: Props) {
  const uid = useId()
  const connected = status.destinations.filter((row) => row.connected).map((row) => row.destination)
  const [saved, setSaved] = useState<ZenodoPreferences | null>(null)
  const [level, setLevel] = useState<ZenodoLevel>(1)
  const [name, setName] = useState('')
  const [orcid, setOrcid] = useState('')
  const [cap, setCap] = useState(3)
  const [kind, setKind] = useState<'write_summary' | 'write_paper'>('write_paper')
  const [fullText, setFullText] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [items, setItems] = useState<ZenodoItem[]>([])
  const [sure, setSure] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)

  const refreshItems = useCallback(async () => { try { setItems(await zenodoItems()) } catch { /* Stays as it was. */ } }, [])

  useEffect(() => {
    const controller = new AbortController()
    zenodoPreferences(controller.signal).then((value) => {
      const author = rememberedAuthor()
      setSaved(value); setLevel(value.level); setCap(value.daily_cap); setKind(value.write_kind); setFullText(value.full_text_only)
      setName(value.creators[0]?.name ?? author.name); setOrcid(value.creators[0]?.orcid ?? author.orcid)
    }).catch(() => undefined)
    void refreshItems()
    return () => controller.abort()
  }, [refreshItems])

  const autopilot = useZenodoAutopilot({ csrf: csrfToken, level: saved?.level ?? 1, native, onProgress: refreshItems })

  const available = (needs: Destination | 'any' | null) => needs === null || (needs === 'any' ? connected.length > 0 : connected.includes(needs))
  const orcidValid = !orcid.trim() || validOrcid(orcid.trim())
  const changed = !!saved && (saved.level !== level || saved.daily_cap !== cap || saved.write_kind !== kind || saved.full_text_only !== fullText || (saved.creators[0]?.name ?? '') !== name.trim() || (saved.creators[0]?.orcid ?? '') !== orcid.trim())
  const canSave = !!saved && changed && available(LEVELS[level - 1].needs) && orcidValid && (level < 2 || !!name.trim()) && !busy

  async function act(work: () => Promise<void>) {
    setBusy(true); setMessage('')
    try { await work() } catch (failure) { setMessage(errorWords(failure instanceof ZenodoError ? failure.code : '')) } finally { setBusy(false) }
  }

  function save(event?: FormEvent, confirmProduction = false) {
    event?.preventDefault()
    if (!canSave) return
    if (level === 4 && !saved?.production_confirmed && !confirmProduction) { setConfirming(true); return }
    void act(async () => {
      const value = await saveZenodoPreferences({
        level, daily_cap: cap, write_kind: kind, full_text_only: fullText, license: saved?.license ?? 'cc-by-4.0',
        draft_destination: connected.includes('sandbox') ? 'sandbox' : 'production',
        creators: name.trim() ? [{ name: name.trim(), affiliation: null, orcid: orcid.trim() || null }] : [],
        ...(confirmProduction ? { confirm_production: true } : {}),
      }, csrfToken)
      setSaved(value); setConfirming(false)
      try { window.localStorage.setItem(AUTHOR_KEY, JSON.stringify({ name: name.trim(), orcid: orcid.trim() })) } catch { /* A convenience only. */ }
      setMessage(value.level === 1 ? 'Saved. Nothing will happen on its own.' : 'Saved. Polymath looks after new findings from now on while this page is open.')
    })
  }

  function publish(item: ZenodoItem) {
    if (!item.publication_id) return
    if (item.destination === 'production' && sure !== item.id) { setSure(item.id); return }
    void act(async () => {
      const record = await zenodoRecord(item.publication_id!)
      await publishZenodoRecord(record, item.destination === 'production', csrfToken)
      setSure(null); await refreshItems()
    })
  }

  if (!saved || !connected.length) return null
  return (
    <section className="zenodo-levels" aria-labelledby={`${uid}-title`}>
      <h3 id={`${uid}-title`}>How much should Polymath do?</h3>
      <form onSubmit={save}>
        <fieldset className="zenodo-level-grid" aria-labelledby={`${uid}-title`}>
          {LEVELS.map((option) => {
            const open = available(option.needs)
            return <label key={option.level} className={`zenodo-level${level === option.level ? ' is-chosen' : ''}${open ? '' : ' is-unavailable'}`}>
              <input type="radio" name={`${uid}-level`} checked={level === option.level} disabled={!open || busy} onChange={() => { setLevel(option.level); setConfirming(false) }} />
              <span className="zenodo-destination-name">{option.title}</span>
              <span className="zenodo-destination-line">{open ? option.line : `Connect ${DESTINATION_WORDS[option.needs === 'production' ? 'production' : 'sandbox'].name} above first.`}</span>
            </label>
          })}
        </fieldset>
        {level >= 2 && <div className="zenodo-grid">
          <label>Your name, on every record<input value={name} maxLength={200} required autoComplete="name" onChange={(event) => setName(event.target.value)} /></label>
          <label>ORCID (optional)<input value={orcid} maxLength={19} placeholder="0000-0000-0000-0000" aria-invalid={!orcidValid} onChange={(event) => setOrcid(event.target.value)} /></label>
          <label>At most each day<select value={cap} onChange={(event) => setCap(Number(event.target.value))}>{[1, 2, 3, 5, 10].map((value) => <option key={value} value={value}>{value} {value === 1 ? 'record' : 'records'}</option>)}</select></label>
          <label>Write each as<select value={kind} onChange={(event) => setKind(event.target.value as typeof kind)}><option value="write_paper">A short paper</option><option value="write_summary">A plain summary</option></select></label>
          <label className="zenodo-check is-wide"><input type="checkbox" checked={fullText} onChange={(event) => setFullText(event.target.checked)} />Only findings backed by full-text evidence</label>
        </div>}
        {!orcidValid && <p className="zenodo-message" role="alert">That ORCID does not look right. Check it, or leave it empty.</p>}
        {confirming ? <div className="zenodo-confirm" role="alertdialog" aria-labelledby={`${uid}-confirm`}>
          <h5 id={`${uid}-confirm`}>Publish permanently, on your behalf?</h5>
          <p>Polymath will publish new findings to your Zenodo with a permanent DOI, up to {cap} a day. Records cannot be deleted. You can lower the level at any time.</p>
          <div className="zenodo-actions"><button type="button" className="zenodo-primary" disabled={busy} onClick={() => save(undefined, true)}>Yes, publish for real</button><button type="button" disabled={busy} onClick={() => setConfirming(false)}>Not yet</button></div>
        </div> : <div className="zenodo-actions"><button type="submit" className="zenodo-primary" disabled={!canSave}>Save this choice</button>{autopilot.status && <span className="zenodo-autopilot" role="status">{autopilot.status}</span>}</div>}
      </form>
      {message && <p className="zenodo-message" role="status">{message}</p>}
      {items.length > 0 && <section className="zenodo-records" aria-labelledby={`${uid}-items`}>
        <h3 id={`${uid}-items`}>Written for you</h3>
        <ul>{items.map((item) => <li key={item.id} className={`zenodo-record is-${item.status}`}>
          <div><span className="zenodo-record-title">{item.title}</span><span className="zenodo-record-meta">{DESTINATION_WORDS[item.destination].name} · {ITEM_WORDS[item.status]}{item.status === 'published' && item.doi ? ` · DOI ${item.doi}` : ''}{item.reason && REASONS[item.reason] ? ` · ${REASONS[item.reason]}` : ''}</span></div>
          <div className="zenodo-record-actions">
            {item.status === 'published' && item.record_url && <a href={item.record_url} target="_blank" rel="noreferrer">Open on Zenodo</a>}
            {item.status === 'ready' && <button type="button" className="zenodo-primary" disabled={busy} onClick={() => publish(item)}>{item.destination === 'production' ? (sure === item.id ? 'Yes, publish permanently' : 'Publish permanently') : 'Publish'}</button>}
            {['pending_write', 'writing', 'drafted', 'ready'].includes(item.status) && <button type="button" disabled={busy} onClick={() => void act(async () => { await skipZenodoItem(item.id, csrfToken); await refreshItems() })}>Skip</button>}
          </div>
        </li>)}</ul>
      </section>}
    </section>
  )
}
