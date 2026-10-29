import { useEffect, useId, useState } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
import { ConvergeApiError, createConvergence, dollars, loadConvergeCapabilities, suggestFields } from './client'
import type { ConvergeCapabilities, ConvergeProgramme } from './client'

const MARKS = [5, 12, 30, 100]
const DEFAULT_CEILING = 20
const STANDARD_MAX = 100

export interface ConvergeComposerProps {
  csrfToken: string | null
  ready: boolean
  onCreated: (programme: ConvergeProgramme) => void
  onSessionExpired: () => void
}

export function ConvergeComposer({ csrfToken, ready, onCreated, onSessionExpired }: ConvergeComposerProps) {
  const uid = useId()
  const [capabilities, setCapabilities] = useState<ConvergeCapabilities | null>(null)
  const [theme, setTheme] = useState('')
  const [count, setCount] = useState(12)
  const [more, setMore] = useState(false)
  const [chosen, setChosen] = useState<string[]>([])
  const [draft, setDraft] = useState('')
  const [suggestions, setSuggestions] = useState<string[]>([])
  const [ceiling, setCeiling] = useState(String(DEFAULT_CEILING))
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    loadConvergeCapabilities(controller.signal).then(setCapabilities).catch(() => setCapabilities(null))
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (draft.trim().length < 2) { setSuggestions([]); return }
    const controller = new AbortController()
    const timer = window.setTimeout(() => { suggestFields(draft, controller.signal).then(setSuggestions).catch(() => setSuggestions([])) }, 180)
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [draft])

  const maxFields = capabilities?.maxFields ?? 200
  const sliderMax = more ? maxFields : STANDARD_MAX
  const dollarsValue = Number(ceiling)
  const ceilingValid = Number.isFinite(dollarsValue) && dollarsValue * 1_000_000 >= (capabilities?.minCeiling ?? 100_000) && dollarsValue * 1_000_000 <= (capabilities?.maxCeiling ?? 150_000_000)
  const steps = capabilities && ceilingValid ? Math.floor((dollarsValue * 1_000_000) / capabilities.stepEstimate) : 0
  const available = !!capabilities?.available && ready && !!csrfToken

  function add(name: string) {
    const value = name.replace(/\s+/g, ' ').trim().slice(0, 100)
    if (value.length < 2 || chosen.some((item) => item.toLowerCase() === value.toLowerCase()) || chosen.length >= count) return
    setChosen((items) => [...items, value])
    setDraft('')
    setSuggestions([])
  }

  function onKey(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter' || event.key === ',') { event.preventDefault(); add(draft) }
    if (event.key === 'Backspace' && !draft && chosen.length) setChosen((items) => items.slice(0, -1))
  }

  async function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!available || !ceilingValid || creating || !csrfToken) return
    setCreating(true)
    setError('')
    try {
      const created = await createConvergence(csrfToken, { theme: theme.trim() || null, field_count: Math.max(count, chosen.length), fields: chosen, ceiling_microdollars: Math.round(dollarsValue * 1_000_000) })
      setTheme('')
      setChosen([])
      onCreated(created)
    } catch (failure) {
      if (failure instanceof ConvergeApiError && failure.code === 'session_expired') onSessionExpired()
      setError('The circle could not be created. Try again in a moment.')
    } finally { setCreating(false) }
  }

  return (
    <form className="converge-composer" onSubmit={start} aria-label="Start a converge circle">
      <label htmlFor={`${uid}-theme`} className="composer-label">A question or theme (optional)</label>
      <div className="prompt-box">
        <textarea id={`${uid}-theme`} value={theme} onChange={(event) => setTheme(event.target.value)} placeholder="A question or theme, or leave it open to see what fields share…" rows={2} maxLength={2000} />
      </div>

      <div className="converge-setting">
        <div className="converge-setting-head"><label htmlFor={`${uid}-count`}>How many fields</label><output htmlFor={`${uid}-count`}>{count} fields</output></div>
        <input id={`${uid}-count`} type="range" min={capabilities?.minFields ?? 3} max={sliderMax} value={Math.min(count, sliderMax)} onChange={(event) => setCount(Math.max(Number(event.target.value), chosen.length))} list={`${uid}-marks`} />
        <datalist id={`${uid}-marks`}>{MARKS.map((mark) => <option key={mark} value={mark} />)}</datalist>
        <div className="converge-marks" aria-hidden="true">{MARKS.map((mark) => <span key={mark} style={{ left: `${((mark - 3) / (sliderMax - 3)) * 100}%` }}>{mark}</span>)}</div>
        <button type="button" className="text-button converge-more-toggle" aria-pressed={more} onClick={() => { setMore((value) => !value); if (more) setCount((value) => Math.min(value, STANDARD_MAX)) }}>{more ? `Up to ${STANDARD_MAX}` : `More, up to ${maxFields}`}</button>
      </div>

      <div className="converge-setting">
        <label htmlFor={`${uid}-field`}>Fields you want included (optional)</label>
        <p className="converge-hint">Polymath spreads the rest across the whole map of knowledge, so the circle holds distant fields.</p>
        <div className="converge-chips">
          {chosen.map((name) => <span key={name} className="converge-chip">{name}<button type="button" aria-label={`Remove ${name}`} onClick={() => setChosen((items) => items.filter((item) => item !== name))}>×</button></span>)}
          <input id={`${uid}-field`} value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={onKey} placeholder={chosen.length ? 'Add another…' : 'Type a field, then press Enter'} list={`${uid}-suggest`} maxLength={100} disabled={chosen.length >= count} />
          <datalist id={`${uid}-suggest`}>{suggestions.map((name) => <option key={name} value={name} />)}</datalist>
        </div>
      </div>

      <div className="converge-setting converge-ceiling">
        <label htmlFor={`${uid}-ceiling`}>Spending ceiling</label>
        <span className="converge-currency">$<input id={`${uid}-ceiling`} type="number" min={0.1} max={150} step="any" value={ceiling} onChange={(event) => setCeiling(event.target.value)} aria-describedby={`${uid}-estimate`} /></span>
        <p id={`${uid}-estimate`} className="converge-hint">{ceilingValid && capabilities ? `About ${steps} ${steps === 1 ? 'investigation fits' : 'investigations fit'} within ${dollars(dollarsValue * 1_000_000)}, at a planning estimate of ${dollars(capabilities.stepEstimate)} each. The circle never starts a step that could cross it.` : 'Choose a ceiling between $0.10 and $150.'}</p>
      </div>

      <div className="prompt-footer converge-start">
        <button className="primary-button" type="submit" disabled={!available || !ceilingValid || creating}>{creating ? 'Starting…' : 'Begin converging'}<svg className="arrow-icon" width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M2 8h12M9 3l5 5-5 5" stroke="currentColor" strokeWidth="1.2" /></svg></button>
      </div>
      <p className="prompt-note">Each step is an ordinary investigation with its own evidence. Keep this tab open while it works; close it and the circle waits, then continues when you return.</p>
      {error && <p className="composer-alert" role="alert">{error}</p>}
    </form>
  )
}
