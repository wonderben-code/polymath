import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
import type { OwnedRun } from '../research/client'
import { attachStudioRun, changeStudio, createStudio, listStudio, listStudioImages, loadStudio, MAX_SPECS, nextStudioStep, renderStudioImage, StudioApiError, studioImageUrl } from './client'
import type { ArtView, Claim, StudioImage, StudioProgramme, StudioStage, StudioStep, StudioSummary, Wildness } from './client'
import { Constellation, SPECTRUM } from './Constellation'
import { OwnKeyCard } from '../OwnKey'
import { buildDossier, buildDossierPage, buildMetaBriefing, dossierFileName, LABEL_WORDS, readStudio, WILDNESS_WORDS } from './dossier'
import type { Gallery, StudioReading } from './dossier'
import './studio.css'

const POLL_MS = 6000
const RETRY_HOLD_MS = 30000
const MAX_HOLDS = 10
const TRANSIENT = new Set(['unavailable', 'rate_limited', 'invalid_response', 'aborted'])
const BRIEF_LIMIT = 1000
const PIPELINE: { stage: StudioStage; name: string; doing: string }[] = [
  { stage: 'brief', name: 'Brief', doing: 'Understanding the brief and today’s best designs' },
  { stage: 'inspire', name: 'Inspiration', doing: 'Hunting across science for mechanisms that already work' },
  { stage: 'design', name: 'Invention', doing: 'Inventing a new design from the best ideas' },
  { stage: 'engineer', name: 'Engineering', doing: 'Engineering the components and working out the numbers' },
  { stage: 'test', name: 'Stress test', doing: 'Stress-testing the design against your brief' },
]
const OUTCOME_WORDS = {
  designed: 'A new invention, engineered and stress-tested',
  inspired: 'Inspirations found, but no design held up yet',
  none: 'No working mechanism found yet',
}
export const STARTERS: { label: string; brief: string; specs: string[]; wildness: Wildness }[] = [
  { label: 'A neural interface for the outdoors', brief: 'A bio-compatible neural interface that keeps working outdoors', specs: ['Safe in the body for ten years', 'Works in rain, heat and cold', 'Fully wireless'], wildness: 'strange' },
  { label: 'A submarine inspired by whales', brief: 'A submarine inspired by whales and the living ocean', specs: ['Dives to 1,000 metres', 'Nearly silent', 'Uses little energy'], wildness: 'far' },
  { label: 'A hospital-quiet helicopter', brief: 'A city helicopter quiet enough to land beside a hospital at night', specs: ['Under 65 dB at 150 metres', 'Carries four passengers', 'Safe if power fails'], wildness: 'strange' },
  { label: 'A Dyson swarm', brief: 'A theoretical Dyson swarm to gather the Sun’s energy', specs: ['Repairs itself', 'Built from asteroid material', 'Orbits stay stable for centuries'], wildness: 'strange' },
]

export interface StudioWorkspaceProps {
  enabled: boolean
  csrfToken: string | null
  canSubmit: boolean
  /** Present while the allowance poll has paused; a running programme calls it so live research is checked again. */
  refreshAccess?: () => void
  /** Live research is switched off or not funded for this browser right now. */
  paused?: boolean
  submit: (mode: 'ask' | 'explore', goal: string, parentResultIds: readonly string[], options?: { collection?: boolean }) => Promise<OwnedRun | undefined>
  onOpenRun: (id: string) => void
  onSolveGap: (gap: string) => void
  onSessionExpired: () => void
}

const VIEW_WORDS: Record<ArtView, string> = {
  concept: 'Concept art', in_use: 'In its world', schematic: 'Engineering cutaway', detail: 'The key mechanism, up close',
  sequence: 'How it works', scale: 'A sense of scale', origins: 'Where the ideas came from',
}
const ART_NOTE = 'Painted by Meta Muse Image from the design. An AI impression, not a photograph of a built device.'

async function dataUrl(url: string): Promise<string | undefined> {
  try {
    const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store' })
    const mime = (response.headers.get('content-type') ?? '').split(';')[0].trim()
    if (!response.ok || !/^image\/(webp|png|jpeg)$/.test(mime)) return undefined
    const bytes = new Uint8Array(await response.arrayBuffer())
    let binary = ''
    for (let index = 0; index < bytes.length; index += 0x8000) binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000))
    return `data:${mime};base64,${btoa(binary)}`
  } catch { return undefined }
}

function message(code: string): string {
  if (code === 'session_expired') return 'Your browser session expired. Reload to continue.'
  if (code === 'studio_unavailable') return 'Studio is switched off on this server.'
  if (code === 'studio_limit_reached') return 'You have reached the number of saved inventions for this browser.'
  if (code === 'studio_finished') return 'This invention has finished.'
  if (code === 'studio_image_daily_limit') return 'That is all the concept art for today. Try again tomorrow.'
  if (code === 'studio_image_limit') return 'This invention already has its concept art.'
  if (code === 'studio_images_unavailable') return 'Concept art is switched off on this server right now.'
  if (code === 'rate_limited') return 'Polymath is pacing requests from this browser; it continues in a moment.'
  return 'Studio could not be reached. Try again in a moment.'
}

function Cite({ number }: { number: number | null }) {
  return number === null ? null : <sup><a href={`#studio-ref-${number}`} aria-label={`Source ${number}`}>{number}</a></sup>
}

function Claims({ claims, step, reading }: { claims: Claim[]; step: StudioStep; reading: StudioReading }) {
  return <ul className="studio-claims">{claims.map((claim, index) => <li key={index}>
    <span className={`studio-label is-${claim.label}`}>{LABEL_WORDS[claim.label] ?? claim.label}</span>
    <div className="studio-claim-body">
      <p>{claim.text}{[...new Set(claim.quotes.map((item) => reading.cite(step, item.source_id)))].map((number, at) => <Cite key={at} number={number} />)}</p>
      {claim.derivation && <p className="studio-working"><strong>Working:</strong> {claim.derivation}</p>}
      {claim.caveats.map((caveat, at) => <p key={at} className="studio-caveat">Caveat: {caveat}</p>)}
      {claim.quotes.length > 0 && <details className="studio-evidence"><summary>Evidence ({claim.quotes.length})</summary>{claim.quotes.map((item, at) => <blockquote key={at}>“{item.quote}”<Cite number={reading.cite(step, item.source_id)} /></blockquote>)}</details>}
    </div>
  </li>)}</ul>
}

function stageState(programme: StudioProgramme, stage: StudioStage): 'done' | 'running' | 'waiting' | 'skipped' {
  const steps = programme.steps.filter((step) => step.stage === stage)
  if (steps.some((step) => step.status === 'running')) return 'running'
  if (steps.length && steps.every((step) => step.status === 'done' || step.status === 'failed')) return 'done'
  if (!steps.length && programme.status === 'completed') return 'skipped'
  return 'waiting'
}

export function StudioWorkspace({ enabled, csrfToken, canSubmit, refreshAccess, paused = false, submit, onOpenRun, onSolveGap, onSessionExpired }: StudioWorkspaceProps) {
  const [brief, setBrief] = useState('')
  const [specs, setSpecs] = useState<string[]>([])
  const [spec, setSpec] = useState('')
  const [wildness, setWildness] = useState<Wildness>('far')
  const [creating, setCreating] = useState(false)
  const [programmes, setProgrammes] = useState<StudioSummary[]>([])
  const [programme, setProgramme] = useState<StudioProgramme | null>(null)
  const [running, setRunning] = useState(false)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [shared, setShared] = useState('')
  const [images, setImages] = useState<StudioImage[]>([])
  const [imagesFor, setImagesFor] = useState<string | null>(null)
  const [imagesAvailable, setImagesAvailable] = useState(false)
  const [plan, setPlan] = useState<ArtView[]>([])
  const [painting, setPainting] = useState(false)
  const live = useRef(new Set<string>())
  const busy = useRef(false)
  const briefRef = useRef<HTMLTextAreaElement>(null)
  const holds = useRef(0)
  const holdUntil = useRef(0)
  const canSubmitRef = useRef(canSubmit)
  canSubmitRef.current = canSubmit
  // The tick awaits the server before submitting, so it must call the latest submit, not the one captured when the tick began (that one may predate the previous run finishing).
  const submitRef = useRef(submit)
  submitRef.current = submit
  const refreshAccessRef = useRef(refreshAccess)
  refreshAccessRef.current = refreshAccess
  const refreshedAt = useRef(0)

  const fail = useCallback((failure: unknown) => {
    const code = failure instanceof StudioApiError ? failure.code : 'unavailable'
    if (code === 'session_expired') onSessionExpired()
    setError(message(code))
  }, [onSessionExpired])

  const refreshList = useCallback(async () => {
    if (!enabled) return
    try { setProgrammes(await listStudio()) } catch (failure) { fail(failure) }
  }, [enabled, fail])
  useEffect(() => { void refreshList() }, [refreshList])

  async function open(id: string) {
    setError(''); setShared('')
    try { setProgramme(await loadStudio(id)); setRunning(false); setStatus('') } catch (failure) { fail(failure) }
  }

  function addSpec() {
    const value = spec.trim().replace(/\s+/g, ' ').slice(0, 200)
    if (value && specs.length < MAX_SPECS && !specs.includes(value)) setSpecs([...specs, value])
    setSpec('')
  }
  function specKey(event: KeyboardEvent<HTMLInputElement>) { if (event.key === 'Enter') { event.preventDefault(); addSpec() } }

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!csrfToken || !brief.trim() || creating) return
    setCreating(true); setError(''); setShared('')
    const pending = spec.trim() && specs.length < MAX_SPECS && !specs.includes(spec.trim()) ? [...specs, spec.trim()] : specs
    try {
      const created = await createStudio(csrfToken, { brief: brief.trim().slice(0, BRIEF_LIMIT), specs: pending, wildness })
      setProgramme(created); setRunning(true); setBrief(''); setSpecs([]); setSpec(''); live.current.add(created.id)
      void refreshList()
    } catch (failure) { fail(failure) } finally { setCreating(false) }
  }

  // One stage at a time through the ordinary investigation path, which applies
  // every approval and budget check. It advances while Polymath is open.
  const tick = useCallback(async () => {
    if (!programme || !csrfToken || busy.current) return
    if (Date.now() < holdUntil.current) return
    busy.current = true
    try {
      const { next, programme: updated } = await nextStudioStep(programme.id, csrfToken)
      setProgramme(updated)
      const doing = (stage: StudioStage) => PIPELINE.find((item) => item.stage === stage)!.doing
      if (next.state === 'due') {
        if (!canSubmitRef.current) {
          setStatus('Waiting until live research is available for this browser.')
          // The allowance poll pauses after ten minutes without a visitor action; a running programme is that action.
          if (refreshAccessRef.current && Date.now() - refreshedAt.current >= 30000) { refreshedAt.current = Date.now(); refreshAccessRef.current() }
          return
        }
        if (Date.now() < holdUntil.current) return
        setStatus(`${doing(next.stage)}…`)
        const run = await submitRef.current(next.mode, next.goal, next.parent_result_ids, { collection: false })
        if (!run) {
          // A step that could not be started (a blip, a restart, a rate limit) is tried again shortly; the programme stops only when it keeps failing.
          holds.current += 1; holdUntil.current = Date.now() + RETRY_HOLD_MS
          if (holds.current >= MAX_HOLDS) { setRunning(false); setStatus('The next stage could not be started. Live research may be unavailable; your brief is saved.'); return }
          setStatus('The next stage could not be started yet. Trying again shortly; your brief is saved.'); return
        }
        holds.current = 0
        setProgramme(await attachStudioRun(programme.id, csrfToken, { step_id: next.step_id, stage: next.stage, run_id: run.id }))
      } else if (next.state === 'waiting') {
        const stage = updated.steps.find((item) => item.status === 'running')?.stage
        setStatus(stage ? `${doing(stage)}…` : 'Working…')
      } else {
        setRunning(false); setStatus('')
        if (next.state === 'completed') void refreshList()
      }
    } catch (failure) { // A passing failure (the service restarting, a blip, a paced request) holds and tries again; a lost session or a persistent failure stops the programme.
      if (failure instanceof StudioApiError && TRANSIENT.has(failure.code) && holds.current + 1 < MAX_HOLDS) { holds.current += 1; holdUntil.current = Date.now() + RETRY_HOLD_MS; setStatus('Polymath could not be reached. Trying again shortly.'); return }
      setRunning(false); fail(failure) } finally { busy.current = false }
  }, [programme, csrfToken, fail, refreshList])

  const tickRef = useRef(tick)
  tickRef.current = tick
  const activeId = programme?.status === 'active' ? programme.id : null
  useEffect(() => {
    if (!running || !activeId) return
    void tickRef.current()
    const timer = window.setInterval(() => void tickRef.current(), POLL_MS)
    return () => window.clearInterval(timer)
  }, [running, activeId])

  const designed = !!programme && programme.steps.some((step) => step.stage === 'design' && step.finding?.kind === 'bridge')
  const programmeId = programme?.id ?? null
  const loadImages = useCallback(async (id: string) => {
    try { const result = await listStudioImages(id); setImages(result.images); setImagesAvailable(result.available); setPlan(result.plan); setImagesFor(id) } catch { /* The art is optional; the design stands without it. */ }
  }, [])
  useEffect(() => { setImages([]); setPlan([]); setImagesFor(null); if (programmeId && designed) void loadImages(programmeId) }, [programmeId, designed, loadImages])
  const rendering = images.some((image) => image.status === 'rendering')
  useEffect(() => {
    if (!rendering || !programmeId) return
    const timer = window.setInterval(() => void loadImages(programmeId), POLL_MS)
    return () => window.clearInterval(timer)
  }, [rendering, programmeId, loadImages])

  // The gallery paints one view at a time, in reading order. A view that has been
  // tried (painted, refused or held) is never retried automatically.
  const missing = plan.filter((view) => !images.some((image) => image.view === view))
  const paint = useCallback(async (view: ArtView) => {
    if (!programmeId || !csrfToken || painting) return
    setPainting(true); setError('')
    try { await renderStudioImage(programmeId, csrfToken, view); await loadImages(programmeId) } catch (failure) {
      fail(failure)
      // A paced request is tried again shortly; any other refusal leaves the gallery for the visitor to continue.
      if (failure instanceof StudioApiError && failure.code === 'rate_limited') setPaintRetry((value) => value + 1)
      else live.current.delete(programmeId)
    } finally { setPainting(false) }
  }, [programmeId, csrfToken, painting, loadImages, fail])
  const [paintRetry, setPaintRetry] = useState(0)
  const [paintTurn, setPaintTurn] = useState(0)
  useEffect(() => { if (!paintRetry) return; const timer = window.setTimeout(() => setPaintTurn((value) => value + 1), 20000); return () => window.clearTimeout(timer) }, [paintRetry])
  // An invention designed while this page is open fills its gallery automatically.
  const nextView = missing[0]
  useEffect(() => {
    if (programmeId && designed && imagesAvailable && imagesFor === programmeId && nextView && !rendering && !painting && live.current.has(programmeId)) void paint(nextView)
  }, [programmeId, designed, imagesAvailable, imagesFor, nextView, rendering, painting, paint, paintTurn])

  async function change(kind: 'pause' | 'resume' | 'stop') {
    if (!programme || !csrfToken) return
    try { setProgramme(await changeStudio(programme.id, csrfToken, kind)); setRunning(kind === 'resume'); if (kind === 'resume') live.current.add(programme.id); void refreshList() } catch (failure) { fail(failure) }
  }

  async function download(kind: 'html' | 'md') {
    if (!programme) return
    const gallery: Gallery = {}
    if (kind === 'html') {
      for (const [view, image] of Object.entries(art) as [ArtView, StudioImage][]) {
        const src = await dataUrl(studioImageUrl(image.id))
        if (src) gallery[view] = { src, caption: `${VIEW_WORDS[view]}. ${ART_NOTE}` }
      }
    }
    const content = kind === 'html' ? buildDossierPage(programme, window.location.origin, gallery) : buildDossier(programme, window.location.origin)
    const url = URL.createObjectURL(new Blob([content], { type: kind === 'html' ? 'text/html;charset=utf-8' : 'text/markdown;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url; link.download = dossierFileName(programme, kind); link.rel = 'noopener'
    document.body.appendChild(link); link.click(); link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  async function continueInMeta() {
    if (!programme) return
    try { await navigator.clipboard.writeText(buildMetaBriefing(programme)); setShared('Design briefing copied. Paste it into Meta AI to keep refining your invention.') } catch { setShared('Copy did not work in this browser. Download the dossier and share it with Meta AI instead.') }
    window.open('https://www.meta.ai/', '_blank', 'noopener,noreferrer')
  }

  function refine() {
    if (!programme) return
    const title = readStudio(programme).design?.title ?? programme.brief
    setBrief(`Improve “${title.slice(0, 200)}” (${programme.brief.slice(0, 400)}) so that `.slice(0, BRIEF_LIMIT))
    setSpecs(programme.specs); setWildness(programme.wildness)
    briefRef.current?.focus(); briefRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
  }

  const reading = programme ? readStudio(programme) : null
  // The newest picture of each view, and the one being painted now.
  const art: Partial<Record<ArtView, StudioImage>> = {}
  for (const image of images) if (image.status === 'succeeded') art[image.view] = image
  const current = images.find((image) => image.status === 'rendering')?.view
  const done = plan.filter((view) => art[view]).length
  const autoFilling = !!programmeId && live.current.has(programmeId) && missing.length > 0
  const figure = (view: ArtView, wide = false) => {
    const image = art[view]
    if (image && reading?.design) return <figure className={`studio-figure${wide ? ' is-wide' : ''} view-${view}`}><a href={studioImageUrl(image.id)} target="_blank" rel="noreferrer"><img src={studioImageUrl(image.id)} alt={`${VIEW_WORDS[view]}: ${reading.design.title}`} loading="lazy" /></a><figcaption><strong>{VIEW_WORDS[view]}.</strong> {ART_NOTE}</figcaption></figure>
    if (current === view) return <div className={`studio-art-placeholder${wide ? ' is-wide' : ''}`} role="status"><span aria-hidden="true" />Painting {VIEW_WORDS[view].toLowerCase()} with Meta Muse Image…</div>
    return null
  }
  const ready = enabled && !!csrfToken
  return (
    <div className="studio">
      <section className="composer studio-composer" aria-labelledby="studio-title">
        <p className="composer-tagline">Innovation Studio · Experimental</p>
        <h1 id="studio-title">Invent something that doesn’t exist yet.</h1>
        <p className="studio-lede">Describe anything you want to create. Meta’s frontier models bring the creativity and reasoning; Polymath brings mechanisms that already work, from the strangest corners of the world’s science. Together they invent a design, engineer it with calculations, and test it against your brief.</p>
        <form onSubmit={(event) => void create(event)}>
          <label htmlFor="studio-brief" className="composer-label">What would you like to create?</label>
          <div className="prompt-box studio-box">
            <textarea id="studio-brief" ref={briefRef} value={brief} onChange={(event) => setBrief(event.target.value)} rows={3} maxLength={BRIEF_LIMIT} placeholder="A submarine inspired by whales, a quieter helicopter, a new kind of neural interface…" />
            <div className="studio-specs">
              <span className="studio-row-label" id="studio-specs-label">Must meet</span>
              <ul aria-labelledby="studio-specs-label">{specs.map((item) => <li key={item}><span>{item}</span><button type="button" aria-label={`Remove “${item}”`} onClick={() => setSpecs(specs.filter((value) => value !== item))}>×</button></li>)}</ul>
              {specs.length < MAX_SPECS && <input aria-label="Add a requirement" value={spec} maxLength={200} onChange={(event) => setSpec(event.target.value)} onKeyDown={specKey} onBlur={addSpec} placeholder={specs.length ? 'Another requirement…' : 'Add a requirement, then press Enter'} />}
            </div>
            <div className="studio-wild" role="radiogroup" aria-label="Where to look for ideas">
              <span className="studio-row-label" aria-hidden="true">Look for ideas in</span>
              {(['near', 'far', 'strange'] as const).map((value) => <button key={value} type="button" role="radio" aria-checked={wildness === value} onClick={() => setWildness(value)}>{WILDNESS_WORDS[value]}</button>)}
            </div>
            <div className="prompt-footer"><button className="primary-button" type="submit" disabled={!ready || !brief.trim() || creating}>{creating ? 'Starting…' : 'Invent it'}</button></div>
          </div>
        </form>
        {paused && <p className="studio-paused" role="status">Live inventing is paused right now. You can still write a brief, try the examples and see how it works; Studio starts inventing as soon as live research is switched on.</p>}
        <OwnKeyCard compact ended={paused} />
        <div className="studio-starters" aria-label="Try an example">
          {STARTERS.map((item) => <button key={item.label} type="button" onClick={() => { setBrief(item.brief); setSpecs(item.specs); setWildness(item.wildness); setSpec('') }}>{item.label}</button>)}
        </div>
      </section>

      {error && <p className="composer-alert" role="alert">{error}</p>}

      {programme && reading && <article className="studio-sheet" aria-labelledby="studio-sheet-title">
        <ol className="studio-pipeline" aria-label="Progress">
          {PIPELINE.map((item) => { const state = stageState(programme, item.stage); return <li key={item.stage} className={`is-${state}`}><span className="studio-pip" aria-hidden="true" />{item.name}<span className="sr-only"> · {state}</span></li> })}
        </ol>
        <p className="studio-eyebrow">{programme.outcome === 'designed' ? 'Theoretical invention · not built or tested' : programme.outcome ? 'Studio result' : 'Designing'}</p>
        <h2 id="studio-sheet-title">{reading.design?.title ?? programme.brief}</h2>
        {reading.design && <p className="studio-brief-line">Brief: {programme.brief}</p>}
        {reading.design && (imagesAvailable || images.length > 0) && <section className="studio-art" aria-label="Gallery">
          {figure("concept", true)}
          {plan.length > 0 && done < plan.length && <p className="studio-gallery-line" role="status">{current || painting ? `Painting the gallery with Meta Muse Image: ${done} of ${plan.length}` : `Gallery: ${done} of ${plan.length} pictures`}{!autoFilling && !current && !painting && imagesAvailable && nextView && <button type="button" className="text-button" onClick={() => { if (programmeId) live.current.add(programmeId); void paint(nextView) }}>{done ? 'Paint the rest of the gallery' : 'Paint the gallery'}</button>}</p>}
          {images.some((image) => image.status === 'refused') && <p className="studio-muted">Meta declined to paint {images.filter((image) => image.status === 'refused').length === 1 ? 'one picture' : 'some pictures'}; nothing was charged for {images.filter((image) => image.status === 'refused').length === 1 ? 'it' : 'them'}.</p>}
          {images.some((image) => image.status === 'unknown') && <p className="studio-muted">A picture did not finish. It is held for review and will never be charged twice.</p>}
        </section>}
        {programme.outcome && programme.outcome !== 'designed' && <p className="studio-outcome">{OUTCOME_WORDS[programme.outcome]}. {programme.outcome === 'inspired' ? 'The ideas below still stand on their own, and you can try again with different requirements or a wilder search.' : 'Try wider fields, or fewer requirements.'}</p>}
        {!programme.outcome && <p className="studio-status" role="status">{status || (programme.status === 'active' ? 'Ready to continue.' : `Studio ${programme.status}.`)}</p>}
        {programme.status === 'active' && !running && !programme.outcome && <button type="button" className="primary-button" disabled={!canSubmit} onClick={() => { live.current.add(programme.id); setRunning(true) }}>Continue inventing</button>}
        {programme.status === 'active' && running && <button type="button" className="text-button" onClick={() => void change('pause')}>Pause</button>}
        {programme.status === 'paused' && <button type="button" className="text-button" onClick={() => void change('resume')}>Resume</button>}

        <div className="studio-grid">
          <Constellation programme={programme} />
          {programme.specs.length > 0 && <section className="studio-card" aria-labelledby="studio-specs-title"><h3 id="studio-specs-title">Your brief must meet</h3><ul className="studio-spec-list">{programme.specs.map((item) => <li key={item}>{item}</li>)}</ul><p className="studio-muted">Ideas from: {WILDNESS_WORDS[programme.wildness]}</p></section>}
        </div>

        {figure("in_use", true)}

        {reading.inspirations.length > 0 && <section className="studio-section" aria-labelledby="studio-ideas"><h3 id="studio-ideas">Where the ideas came from</h3>
          {figure("origins", true)}
          <ul className="studio-ideas">{reading.inspirations.map(({ step, finding, used }) => { const index = programme.steps.filter((item) => item.stage === 'inspire').indexOf(step); return (
            <li key={step.id} className={`studio-idea${used || !reading.design ? '' : ' is-unused'}`} style={{ ['--node' as string]: SPECTRUM[index % SPECTRUM.length] }}>
              <span className="studio-idea-field">{finding.field_left} → {finding.field_right}{used || !reading.design ? '' : ' · considered, not used'}</span>
              <h4>{finding.title}</h4>
              <p>{finding.explanation}</p>
              <p className="studio-muted"><strong>Where it may not transfer:</strong> {finding.objection}</p>
              {step.run_id && <button type="button" className="text-button" onClick={() => onOpenRun(step.run_id!)}>See the evidence</button>}
            </li>) })}</ul>
          {reading.setAside.length > 0 && <p className="studio-muted">{reading.setAside.length === 1 ? 'One search' : `${reading.setAside.length} searches`} found no mechanism strong enough to use.</p>}
        </section>}

        {reading.design && <section className="studio-section studio-invention" aria-labelledby="studio-invention"><h3 id="studio-invention">The invention</h3>
          <p className="studio-big">{reading.design.explanation}</p>
          {(art.schematic || art.detail || current === 'schematic' || current === 'detail') && <div className="studio-pair">{figure("schematic")}{figure("detail")}</div>}
          {reading.design.mappings.length > 0 && <><h4>How the sources become the design</h4><ul className="studio-mappings">{reading.design.mappings.map((pair, index) => <li key={index}><span>{pair.left}</span><span aria-hidden="true">→</span><span>{pair.right}</span><small>Assumes: {pair.assumption}</small></li>)}</ul></>}
          <div className="studio-objection"><h4>The strongest objection</h4><p>{reading.design.objection}</p><p><strong>Verdict:</strong> {reading.design.verdict}</p><p><strong>Limits of novelty:</strong> {reading.design.limitations}</p></div>
          {reading.designStep && reading.design.quotes.length > 0 && <details className="studio-evidence"><summary>Evidence behind the invention ({reading.design.quotes.length})</summary>{reading.design.quotes.map((item, at) => <blockquote key={at}>“{item.quote}”<Cite number={reading.cite(reading.designStep!, item.source_id)} /></blockquote>)}</details>}
          {reading.designStep?.run_id && <button type="button" className="text-button" onClick={() => onOpenRun(reading.designStep!.run_id!)}>See how it was invented</button>}
        </section>}

        {reading.engineering && <section className="studio-section" aria-labelledby="studio-engineering"><h3 id="studio-engineering">Engineering and calculations</h3>{figure("sequence", true)}<p>{reading.engineering.summary}</p><Claims claims={reading.engineering.claims} step={programme.steps.find((item) => item.stage === 'engineer')!} reading={reading} /></section>}
        {reading.test && <section className="studio-section" aria-labelledby="studio-test"><h3 id="studio-test">Stress test against your brief</h3>{figure("scale", true)}<p>{reading.test.summary}</p><Claims claims={reading.test.claims} step={programme.steps.find((item) => item.stage === 'test')!} reading={reading} /></section>}
        {reading.stillToInvent.length > 0 && <section className="studio-section" aria-labelledby="studio-gaps"><h3 id="studio-gaps">Still to invent</h3><ul className="studio-gaps">{reading.stillToInvent.map((gap) => <li key={gap}><span>{gap}</span><button type="button" className="text-button" onClick={() => onSolveGap(gap)}>Solve this</button></li>)}</ul></section>}

        {reading.references.length > 0 && <section className="studio-section" aria-labelledby="studio-refs"><h3 id="studio-refs">Sources</h3><ol className="studio-refs">{reading.references.map((source, index) => { const link = source.doi ? `https://doi.org/${source.doi}` : source.url; return <li key={link} id={`studio-ref-${index + 1}`}>{source.authors.length > 0 && <>{source.authors.join(', ')}{source.more_authors ? ' et al.' : ''}. </>}<a href={link} target="_blank" rel="noreferrer">{source.title}</a>{source.venue ? `. ${source.venue}` : ''}{source.year ? `, ${source.year}` : ''}.</li> })}</ol></section>}

        {reading.design && <section className="studio-section studio-actions" aria-labelledby="studio-next"><h3 id="studio-next">Take it further</h3>
          <div className="studio-action-row">
            <button type="button" className="primary-button" onClick={() => void download('html')}>Download the design dossier</button>
            <button type="button" className="secondary-button" onClick={() => void continueInMeta()}>Continue in Meta AI</button>
            <button type="button" className="secondary-button" onClick={refine}>Refine this design</button>
          </div>
          <p className="studio-muted">The dossier opens in any browser and prints or saves as a PDF. <button type="button" className="text-button" onClick={() => void download('md')}>Download as Markdown</button></p>
          {shared && <p className="studio-muted" role="status">{shared}</p>}
          <p className="studio-muted studio-vision">Coming next: connect your Meta account and keep iterating with Meta without leaving Polymath.</p>
        </section>}
      </article>}

      {programmes.length > 0 && <nav className="studio-list" aria-label="Your inventions"><h3>Your inventions</h3><ul>{programmes.map((item) => <li key={item.id}><button type="button" aria-pressed={programme?.id === item.id} onClick={() => void open(item.id)}><span>{item.brief}</span><span className="studio-muted">{item.outcome ? OUTCOME_WORDS[item.outcome] : item.status === 'active' ? 'In progress' : item.status}</span></button></li>)}</ul></nav>}
    </div>
  )
}
