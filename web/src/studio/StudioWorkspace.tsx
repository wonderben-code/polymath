import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
import type { OwnedRun } from '../research/client'
import { attachStudioRun, changeStudio, createStudio, listStudio, loadStudio, MAX_SPECS, nextStudioStep, StudioApiError } from './client'
import type { Claim, StudioProgramme, StudioStage, StudioStep, StudioSummary, Wildness } from './client'
import { Constellation, SPECTRUM } from './Constellation'
import { buildDossier, buildDossierPage, buildMetaBriefing, dossierFileName, LABEL_WORDS, readStudio, WILDNESS_WORDS } from './dossier'
import type { StudioReading } from './dossier'
import './studio.css'

const POLL_MS = 4000
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
  submit: (mode: 'ask' | 'explore', goal: string, parentResultIds: readonly string[]) => Promise<OwnedRun | undefined>
  onOpenRun: (id: string) => void
  onSolveGap: (gap: string) => void
  onSessionExpired: () => void
}

function message(code: string): string {
  if (code === 'session_expired') return 'Your browser session expired. Reload to continue.'
  if (code === 'studio_unavailable') return 'Studio is switched off on this server.'
  if (code === 'studio_limit_reached') return 'You have reached the number of saved inventions for this browser.'
  if (code === 'studio_finished') return 'This invention has finished.'
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

export function StudioWorkspace({ enabled, csrfToken, canSubmit, submit, onOpenRun, onSolveGap, onSessionExpired }: StudioWorkspaceProps) {
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
  const busy = useRef(false)
  const briefRef = useRef<HTMLTextAreaElement>(null)
  const canSubmitRef = useRef(canSubmit)
  canSubmitRef.current = canSubmit

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
      setProgramme(created); setRunning(true); setBrief(''); setSpecs([]); setSpec('')
      void refreshList()
    } catch (failure) { fail(failure) } finally { setCreating(false) }
  }

  // One stage at a time through the ordinary investigation path, which applies
  // every approval and budget check. It advances while Polymath is open.
  const tick = useCallback(async () => {
    if (!programme || !csrfToken || busy.current) return
    busy.current = true
    try {
      const { next, programme: updated } = await nextStudioStep(programme.id, csrfToken)
      setProgramme(updated)
      const doing = (stage: StudioStage) => PIPELINE.find((item) => item.stage === stage)!.doing
      if (next.state === 'due') {
        if (!canSubmitRef.current) { setStatus('Waiting until live research is available for this browser.'); return }
        setStatus(`${doing(next.stage)}…`)
        const run = await submit(next.mode, next.goal, next.parent_result_ids)
        if (!run) { setRunning(false); setStatus('The next stage could not be started. Live research may be unavailable; your brief is saved.'); return }
        setProgramme(await attachStudioRun(programme.id, csrfToken, { step_id: next.step_id, stage: next.stage, run_id: run.id }))
      } else if (next.state === 'waiting') {
        const stage = updated.steps.find((item) => item.status === 'running')?.stage
        setStatus(stage ? `${doing(stage)}…` : 'Working…')
      } else {
        setRunning(false); setStatus('')
        if (next.state === 'completed') void refreshList()
      }
    } catch (failure) { setRunning(false); fail(failure) } finally { busy.current = false }
  }, [programme, csrfToken, submit, fail, refreshList])

  const tickRef = useRef(tick)
  tickRef.current = tick
  const activeId = programme?.status === 'active' ? programme.id : null
  useEffect(() => {
    if (!running || !activeId) return
    void tickRef.current()
    const timer = window.setInterval(() => void tickRef.current(), POLL_MS)
    return () => window.clearInterval(timer)
  }, [running, activeId])

  async function change(kind: 'pause' | 'resume' | 'stop') {
    if (!programme || !csrfToken) return
    try { setProgramme(await changeStudio(programme.id, csrfToken, kind)); setRunning(kind === 'resume'); void refreshList() } catch (failure) { fail(failure) }
  }

  function download(kind: 'html' | 'md') {
    if (!programme) return
    const content = kind === 'html' ? buildDossierPage(programme, window.location.origin) : buildDossier(programme, window.location.origin)
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
  const ready = enabled && !!csrfToken
  return (
    <div className="studio">
      <section className="composer studio-composer" aria-labelledby="studio-title">
        <p className="composer-tagline">Innovation Studio · Experimental</p>
        <h1 id="studio-title">Invent something that doesn’t exist yet.</h1>
        <p className="studio-lede">Describe anything you want to create. Meta’s Muse Spark hunts the strangest corners of science for mechanisms that already work, invents a design from them, engineers it with calculations, and tests it against your brief.</p>
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
        {programme.outcome && programme.outcome !== 'designed' && <p className="studio-outcome">{OUTCOME_WORDS[programme.outcome]}. {programme.outcome === 'inspired' ? 'The ideas below still stand on their own, and you can try again with different requirements or a wilder search.' : 'Try wider fields, or fewer requirements.'}</p>}
        {!programme.outcome && <p className="studio-status" role="status">{status || (programme.status === 'active' ? 'Ready to continue.' : `Studio ${programme.status}.`)}</p>}
        {programme.status === 'active' && !running && !programme.outcome && <button type="button" className="primary-button" disabled={!canSubmit} onClick={() => setRunning(true)}>Continue inventing</button>}
        {programme.status === 'active' && running && <button type="button" className="text-button" onClick={() => void change('pause')}>Pause</button>}
        {programme.status === 'paused' && <button type="button" className="text-button" onClick={() => void change('resume')}>Resume</button>}

        <div className="studio-grid">
          <Constellation programme={programme} />
          {programme.specs.length > 0 && <section className="studio-card" aria-labelledby="studio-specs-title"><h3 id="studio-specs-title">Your brief must meet</h3><ul className="studio-spec-list">{programme.specs.map((item) => <li key={item}>{item}</li>)}</ul><p className="studio-muted">Ideas from: {WILDNESS_WORDS[programme.wildness]}</p></section>}
        </div>

        {reading.inspirations.length > 0 && <section className="studio-section" aria-labelledby="studio-ideas"><h3 id="studio-ideas">Where the ideas came from</h3>
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
          {reading.design.mappings.length > 0 && <><h4>How the sources become the design</h4><ul className="studio-mappings">{reading.design.mappings.map((pair, index) => <li key={index}><span>{pair.left}</span><span aria-hidden="true">→</span><span>{pair.right}</span><small>Assumes: {pair.assumption}</small></li>)}</ul></>}
          <div className="studio-objection"><h4>The strongest objection</h4><p>{reading.design.objection}</p><p><strong>Verdict:</strong> {reading.design.verdict}</p><p><strong>Limits of novelty:</strong> {reading.design.limitations}</p></div>
          {reading.designStep && reading.design.quotes.length > 0 && <details className="studio-evidence"><summary>Evidence behind the invention ({reading.design.quotes.length})</summary>{reading.design.quotes.map((item, at) => <blockquote key={at}>“{item.quote}”<Cite number={reading.cite(reading.designStep!, item.source_id)} /></blockquote>)}</details>}
          {reading.designStep?.run_id && <button type="button" className="text-button" onClick={() => onOpenRun(reading.designStep!.run_id!)}>See how it was invented</button>}
        </section>}

        {reading.engineering && <section className="studio-section" aria-labelledby="studio-engineering"><h3 id="studio-engineering">Engineering and calculations</h3><p>{reading.engineering.summary}</p><Claims claims={reading.engineering.claims} step={programme.steps.find((item) => item.stage === 'engineer')!} reading={reading} /></section>}
        {reading.test && <section className="studio-section" aria-labelledby="studio-test"><h3 id="studio-test">Stress test against your brief</h3><p>{reading.test.summary}</p><Claims claims={reading.test.claims} step={programme.steps.find((item) => item.stage === 'test')!} reading={reading} /></section>}
        {reading.stillToInvent.length > 0 && <section className="studio-section" aria-labelledby="studio-gaps"><h3 id="studio-gaps">Still to invent</h3><ul className="studio-gaps">{reading.stillToInvent.map((gap) => <li key={gap}><span>{gap}</span><button type="button" className="text-button" onClick={() => onSolveGap(gap)}>Solve this</button></li>)}</ul></section>}

        {reading.references.length > 0 && <section className="studio-section" aria-labelledby="studio-refs"><h3 id="studio-refs">Sources</h3><ol className="studio-refs">{reading.references.map((source, index) => { const link = source.doi ? `https://doi.org/${source.doi}` : source.url; return <li key={link} id={`studio-ref-${index + 1}`}>{source.authors.length > 0 && <>{source.authors.join(', ')}{source.more_authors ? ' et al.' : ''}. </>}<a href={link} target="_blank" rel="noreferrer">{source.title}</a>{source.venue ? `. ${source.venue}` : ''}{source.year ? `, ${source.year}` : ''}.</li> })}</ol></section>}

        {reading.design && <section className="studio-section studio-actions" aria-labelledby="studio-next"><h3 id="studio-next">Take it further</h3>
          <div className="studio-action-row">
            <button type="button" className="primary-button" onClick={() => download('html')}>Download the design dossier</button>
            <button type="button" className="secondary-button" onClick={() => void continueInMeta()}>Continue in Meta AI</button>
            <button type="button" className="secondary-button" onClick={refine}>Refine this design</button>
          </div>
          <p className="studio-muted">The dossier opens in any browser and prints or saves as a PDF. <button type="button" className="text-button" onClick={() => download('md')}>Download as Markdown</button></p>
          {shared && <p className="studio-muted" role="status">{shared}</p>}
          <p className="studio-muted studio-vision">Coming next: connect your Meta account and keep iterating with Meta without leaving Polymath.</p>
        </section>}
      </article>}

      {programmes.length > 0 && <nav className="studio-list" aria-label="Your inventions"><h3>Your inventions</h3><ul>{programmes.map((item) => <li key={item.id}><button type="button" aria-pressed={programme?.id === item.id} onClick={() => void open(item.id)}><span>{item.brief}</span><span className="studio-muted">{item.outcome ? OUTCOME_WORDS[item.outcome] : item.status === 'active' ? 'In progress' : item.status}</span></button></li>)}</ul></nav>}
    </div>
  )
}
