import { ExploreWorkspace } from './exploreCollection/ExploreWorkspace'
import { DiscoveryWorkspace } from './discovery/DiscoveryWorkspace'
import { NativeResumeControl } from './research/NativeResumeControl'
import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { loadSystemStatus } from './api'
import type { SystemStatus } from './api'
import { ResearchRunView } from './research'
import { useResearchWorkspace } from './research/useResearchWorkspace'
import { KnowledgeBrowser } from './knowledge'
import { ArtifactWorkspace } from './artifacts/ArtifactWorkspace'
import { useFindingHead } from './assessments/useFindingHead'
import { FindingVersionWorkspace } from './assessments/FindingVersionWorkspace'
import { ShareWorkspace } from './sharing/ShareWorkspace'
import { NativeResearchProvider } from './research/nativeContext'
import { NativeResearchNotice } from './research/NativeResearchNotice'
import { FrontierWorkspace } from './frontier/FrontierWorkspace'
import { ConvergeComposer } from './converge/ConvergeComposer'
import { PublishingSection } from './publishing/PublishingSection'
import { CALLBACK_PATH, YourZenodo } from './zenodo/YourZenodo'
import { AccountControl } from './account/AccountControl'
import { Story } from './Story'
import { RigourBar } from './research/RigourBar'
import { SolveComposer } from './solve/SolveComposer'
import { SolveWorkspace } from './solve/SolveWorkspace'
import { createSolve } from './solve/client'
import { StudioWorkspace } from './studio/StudioWorkspace'
import { Credits } from './Credits'
import { ConvergeWorkspace } from './converge/ConvergeWorkspace'
import { HeroBridge } from './HeroBridge'
import { WaysToUse } from './WaysToUse'
import { PLACES, SiteHeader } from './shell/SiteChrome'
import type { Place } from './shell/SiteChrome'
import { CompletionToast, NotifyToggle, useNotifier, useRunCompletion } from './notify/notifier'
import { createProgramme, FrontierApiError } from './frontier/client'

type Mode = 'explore' | 'ask' | 'surprise'

type LibrarySection = 'findings' | 'map' | 'watching' | 'publishing'
const LIBRARY_SECTIONS: { id: LibrarySection; label: string }[] = [
  { id: 'findings', label: 'Findings' }, { id: 'map', label: 'Map' }, { id: 'watching', label: 'Watching' }, { id: 'publishing', label: 'Publishing' },
]

function placeFor(pathname: string): Place {
  if (pathname === CALLBACK_PATH) return 'library'
  return PLACES.find((item) => item.path !== '/' && (pathname === item.path || pathname === `${item.path}/`))?.id ?? 'research'
}

// Client-side places keep the research session, live streams and any running
// Frontier programme alive while people move around.
function usePlace(): [Place, (place: Place) => void] {
  const [place, setPlace] = useState<Place>(() => placeFor(window.location.pathname))
  useEffect(() => {
    const restore = () => setPlace(placeFor(window.location.pathname))
    window.addEventListener('popstate', restore)
    return () => window.removeEventListener('popstate', restore)
  }, [])
  const go = useCallback((next: Place) => {
    const path = PLACES.find((item) => item.id === next)!.path
    if (window.location.pathname !== path) window.history.pushState(null, '', path)
    setPlace(next)
    window.scrollTo?.({ top: 0 })
  }, [])
  return [place, go]
}

const modes: { id: Mode; label: string; number: string }[] = [
  { id: 'explore', label: 'Explore', number: '01' },
  { id: 'ask', label: 'Ask', number: '03' },
  { id: 'surprise', label: 'Surprise me', number: '04' },
]

const copy = {
  explore: {
    description: 'Name a concept. Polymath looks for the same structure in other disciplines and tests whether the connection really holds.',
    label: 'What would you like to explore?',
    placeholder: 'A concept, a phenomenon, an unexpected connection…',
    action: 'Explore connections',
    note: 'A good investigation can end with a connection—or a reason to reject one.',
    examples: ['Self-organised criticality', 'Error-correcting codes', 'Phase transitions in learning'],
  },
  ask: {
    description: 'Bring an open question. Polymath answers from the literature and labels every claim by how well it is supported.',
    label: 'What question is on your mind?',
    placeholder: 'What is the truth about…?',
    action: 'Investigate question',
    note: 'Answers will distinguish known findings, postulated connections, and speculation.',
    examples: ['Why is sleep necessary?', 'What limits how fast cities grow?'],
  },
  surprise: {
    description: 'Polymath picks distant disciplines, looks for a shared structure and keeps only what holds up.',
    label: 'What should guide the search? (optional)',
    placeholder: 'Leave this open, or offer a direction…',
    action: 'Start a discovery',
    note: 'The research model will choose the fields. No preselected pair is presented as a discovery.',
    examples: [],
  },
}

function Arrow({ diagonal = false }: { diagonal?: boolean }) {
  return <svg className="arrow-icon" width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
    <path d={diagonal ? 'M4 12 12 4M4 4h8v8' : 'M2 8h12M9 3l5 5-5 5'} stroke="currentColor" strokeWidth="1.2" />
  </svg>
}

function StatusLine({ label, value, state }: { label: string; value: string; state: 'ok' | 'waiting' | 'unknown' }) {
  return (
    <li>
      <span className="status-name"><span className={`dot ${state}`} aria-hidden="true" />{label}</span>
      <span>{value}</span>
    </li>
  )
}

function AboutPlace({ remaining, daily }: { remaining?: number | null; daily?: number | null }) {
  return <div className="about-place">
    <header className="place-heading"><h1>What Polymath is</h1><p>Science is split into thousands of specialties, and far more is published each year than anyone could read. So an answer found in one field often stays unknown in the field that needs it. Polymath is an AI research partner that reads across all of them. It finds where different fields have discovered the same idea, checks each connection against the original papers, builds deeper connections from the ones that hold, and takes on open problems with tools from other disciplines.</p></header>
    <section className="about-section" aria-labelledby="impact-title">
      <h2 id="impact-title">Why it matters</h2>
      <dl className="about-points">
        <div><dt>Discoveries between fields</dt><dd>Some of the biggest leaps came from connecting fields: the mathematics of heat became the pricing of options; how ant colonies find food inspired how networks route data. Polymath looks for leaps like these every day.</dd></div>
        <div><dt>Answers that already exist</dt><dd>A problem that is open in one field may be solved in another. Polymath helps find it, with the passages that show it.</dd></div>
        <div><dt>Gaps worth working on</dt><dd>It names what nobody has connected yet, so researchers can see where the next work could be.</dd></div>
        <div><dt>Inventions from across science</dt><dd>Innovation Studio turns a brief into a new design built from mechanisms that already work in other fields, engineered with calculations and tested against the brief.</dd></div>
        <div><dt>Open problems, step by step</dt><dd>Frontier breaks a hard problem into smaller steps and looks for each answer across disciplines.</dd></div>
      </dl>
    </section>
    <section className="about-section" aria-labelledby="intelligence-title">
      <h2 id="intelligence-title">Meta’s frontier models decide each step. <em>Polymath keeps it honest.</em></h2>
      <p className="about-intro">Polymath is Meta × the world’s science. Meta’s frontier Muse models bring the judgement and creativity: where to look, what to believe, how ideas combine. Polymath brings hundreds of millions of research works through OpenAlex and arXiv, reads full texts, checks every quotation, remembers every finding and keeps every call within budget. It runs on Muse Spark today and can move to any stronger Meta model as one arrives.</p>
      <dl className="about-points">
        <div><dt>Chooses where to look</dt><dd>The model picks every next action: search for support or counterevidence, read an abstract or a chosen section of a paper, revisit earlier findings, or stop.</dd></div>
        <div><dt>Picks its own fields</dt><dd>In Surprise me, the model selects distant fields itself and explains why. No list of fields is supplied.</dd></div>
        <div><dt>Argues against itself</dt><dd>Every connection must carry the strongest objection the model found, its verdict on it, and a search for prior work.</dd></div>
        <div><dt>Bounded by evidence</dt><dd>Polymath accepts only quotations that match text it actually saved, and reserves each model call against a hard budget before it is sent.</dd></div>
      </dl>
    </section>
    <section className="about-section" aria-labelledby="labels-title">
      <h2 id="labels-title">How to read what Polymath tells you</h2>
      <dl className="about-points">
        <div><dt>Known</dt><dd>Stated in the literature, with the exact passage.</dd></div>
        <div><dt>Postulated</dt><dd>Follows from connected sources, but is not yet stated anywhere.</dd></div>
        <div><dt>Speculative</dt><dd>An interesting possibility with weaker support.</dd></div>
        <div><dt>Model assessments</dt><dd>Confidence, novelty and objections are the model’s judgement, not independent verification. A limited search cannot prove a connection is new.</dd></div>
      </dl>
    </section>
    <WaysToUse remaining={remaining} daily={daily} />
    <section className="about-section about-credits" aria-labelledby="credits-title">
      <h2 id="credits-title">Made for the Meta hackathon</h2>
      <p>Created for the Meta Global AI Developer Hackathon with Cerebral Valley. The intelligence is Meta’s frontier Muse models (Muse Spark today), combined with Polymath’s engine across the world’s literature. Polymath is open source.</p>
    </section>
    <Credits />
  </div>
}

export function App() {
  const [place, go] = usePlace()
  const [mode, setMode] = useState<Mode>('explore')
  const [prompts, setPrompts] = useState<Record<Mode, string>>({ explore: '', ask: '', surprise: '' })
  const [frontierQuestion, setFrontierQuestion] = useState('')
  const [frontierFocus, setFrontierFocus] = useState<string | null>(null)
  const [creatingFrontier, setCreatingFrontier] = useState(false)
  const [system, setSystem] = useState<SystemStatus | null>(null)
  const [connection, setConnection] = useState<'loading' | 'connected' | 'offline'>('loading')
  const [retry, setRetry] = useState(0)
  const [notice, setNotice] = useState('')
  const [artifactBusy, setArtifactBusy] = useState(false)
  const [assessmentBusy, setAssessmentBusy] = useState(false)
  const [assessmentRefresh, setAssessmentRefresh] = useState(0)
  const [discoveryRefresh, setDiscoveryRefresh] = useState(0)
  const [selectedFindingId, setSelectedFindingId] = useState<string | null>(null)
  const [converging, setConverging] = useState(false)
  // Solve, the key finder and key maker, is a mode of its own like Converge.
  const [solving, setSolving] = useState(false)
  const [solveFocus, setSolveFocus] = useState<string | null>(null)
  const [convergeFocus, setConvergeFocus] = useState<string | null>(null)
  const [convergeReading, setConvergeReading] = useState<string | null>(null)
  const content = copy[mode]
  const notifier = useNotifier()
  const workspace = useResearchWorkspace(
    connection === 'connected' && !!system?.database && system.migrations,
    !!system?.researchAvailable,
    !!system?.nativeResearchRequired,
  )
  const findingResultId = !workspace.activeRun?.discovery_intent && !workspace.activeRun?.explore_collection_intent && workspace.activeRun?.status === 'succeeded' && (workspace.result?.finding?.outcome === 'bridge' || workspace.result?.finding?.outcome === 'answer') ? workspace.activeRun.result_id : null
  const findingHead = useFindingHead({ resultId: findingResultId, enabled: workspace.phase === 'ready' && selectedFindingId === null, onSessionExpired: workspace.invalidateSession, refreshToken: assessmentRefresh })
  useRunCompletion(workspace.activeRun, notifier)
  const onConvergeFinished = useCallback((programme: { id: string; theme: string | null }) => notifier.announce('Converge circle finished', programme.theme ?? 'An open circle of fields', programme.id), [notifier.announce])
  const onFrontierFinished = useCallback((programme: { id: string; question: string }) => notifier.announce('Frontier programme finished', programme.question, programme.id), [notifier.announce])
  const discoveryChanged = useCallback(() => setDiscoveryRefresh((previous) => previous + 1), [])
  const assessmentChanged = useCallback(() => setAssessmentRefresh((previous) => previous + 1), [])
  const openFinding = useCallback((id: string) => { if (/^[a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12}$/.test(id)) setSelectedFindingId(id) }, [])
  // Opening or starting an investigation glides to it, so the result is
  // never hidden below the composer.
  const focusAnchor = useRef<HTMLDivElement>(null)
  const pendingFocus = useRef(false)
  const openRun = useCallback((id: string) => { pendingFocus.current = true; setSelectedFindingId(null); workspace.selectRun(id); go('research') }, [workspace.selectRun, go])
  const openFindingHere = useCallback((id: string) => { pendingFocus.current = true; openFinding(id); go('research') }, [openFinding, go])
  const investigateBridges = useCallback(async (ids: string[]) => { pendingFocus.current = true; go('research'); await workspace.investigateBridges(ids) }, [workspace.investigateBridges, go])
  const [deeperFrom, setDeeperFrom] = useState<string | null>(null)
  // Your library shows one calm section at a time; Zenodo's return lands on Publishing.
  const [librarySection, setLibrarySection] = useState<LibrarySection>(() => window.location.pathname === CALLBACK_PATH ? 'publishing' : 'findings')
  const goDeeper = useCallback((resultId: string) => { setDeeperFrom(resultId); setLibrarySection('findings'); go('library') }, [go])
  const focusedId = selectedFindingId ?? workspace.activeRun?.id
  useEffect(() => {
    if (!pendingFocus.current || !focusedId) return
    pendingFocus.current = false
    const still = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    window.requestAnimationFrame(() => focusAnchor.current?.scrollIntoView?.({ behavior: still ? 'auto' : 'smooth', block: 'start' }))
  }, [focusedId])
  useEffect(() => { if (workspace.phase !== 'ready') { setSelectedFindingId(null); setAssessmentBusy(false) } }, [workspace.phase])
  useEffect(() => setSelectedFindingId(null), [workspace.selectedId])

  useEffect(() => {
    const controller = new AbortController()
    let active = true
    const timeout = window.setTimeout(() => {
      controller.abort()
      if (active) setConnection('offline')
    }, 10000)
    setConnection('loading')
    setSystem(null)
    loadSystemStatus(controller.signal)
      .then((status) => {
        if (active && !controller.signal.aborted) {
          setSystem(status)
          setConnection('connected')
        }
      })
      .catch(() => {
        // An aborted request during effect cleanup must not change the next request's state.
        if (active && !controller.signal.aborted) setConnection('offline')
      })
      .finally(() => window.clearTimeout(timeout))
    return () => {
      active = false
      controller.abort()
      window.clearTimeout(timeout)
    }
  }, [retry])

  // The server defaults to unavailable. A real cookie-owned session and healthy
  // storage are required as well; a capability flag alone cannot create a job.
  const frontierReady = workspace.phase === 'ready' && !!workspace.csrfToken && !creatingFrontier
  const researchReady = workspace.canSubmit && (mode !== 'ask' || workspace.canAsk) && (mode !== 'explore' || workspace.canExplore)
  const researchConnected = researchReady && !artifactBusy && !assessmentBusy && (mode === 'surprise' || !!prompts[mode].trim())
  const unavailable = connection === 'offline'
    ? 'The research service could not be reached. Check the connection and try again.'
    : mode === 'explore' && workspace.native.canSubmit && !workspace.canExplore ? workspace.native.explorePhase === 'loading' ? 'Checking the approved Explore features…' : 'Explore features could not be verified for this funding approval. Refresh research access before starting an investigation; saved findings remain readable.' : workspace.ownActiveResearch ? 'An investigation is active in this browser. Finish or cancel it before starting another model task.' : workspace.native.required && workspace.native.phase !== 'disabled' ? 'Current funding authority or this browser’s limits do not permit a new model task. Saved research remains readable.' : 'Live investigations are paused right now. Saved research and the public library remain open to read.'

  function handleFrontier(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (frontierReady && frontierQuestion.trim()) void startFrontier(frontierQuestion)
  }

  async function startFrontier(question: string) {
    if (!workspace.csrfToken) return
    setCreatingFrontier(true)
    try {
      const created = await createProgramme(workspace.csrfToken, { question: question.trim() })
      setFrontierFocus(created.id)
      setFrontierQuestion('')
    } catch (failure) {
      if (failure instanceof FrontierApiError && failure.code === 'session_expired') workspace.invalidateSession()
      setNotice('The frontier programme could not be created. Try again in a moment.')
    } finally { setCreatingFrontier(false) }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!researchConnected) { setNotice(unavailable); return }
    pendingFocus.current = true
    setNotice('')
    void workspace.submit(mode, prompts[mode])
  }

  const statusText = connection === 'loading' ? 'Checking the research service…' : artifactBusy ? 'A formalisation or writing task is running below.' : assessmentBusy ? 'An evidence reassessment is running in your library.' : researchReady ? 'Ready. Your investigation is saved to this browser.' : unavailable
  const recent = workspace.runs
  const freeAccess = workspace.native.access?.audience === 'public_sessions' ? workspace.native.access : null

  return (
    <NativeResearchProvider value={workspace.native}>
      <a className="skip-link" href="#workspace">Skip to main content</a>
      <div className="page-shell calm">
        <SiteHeader current={place} onNavigate={go} accessory={<AccountControl csrfToken={workspace.csrfToken} enabled={workspace.phase === 'ready'} />} />

        <main id="workspace">
          {place === 'research' && <>
            <section className="composer" aria-labelledby="research-title">
              <h1 id="research-title">Discoveries hide between fields of science. Polymath finds them.</h1>
              <p className="hero-lede">Science is split into thousands of specialties, and far more is published each year than anyone could read. So an answer found in one field often stays unknown in the field that needs it. Polymath fuses Meta’s frontier Muse models with an engine that reads across the open record of world science. Meta brings the reasoning and the creativity; Polymath brings the literature, the evidence checks and the memory. Together they find where fields have found the same idea, solve problems with answers from other fields, and invent new things from ideas that already work.</p>
              <p className="hero-fusion"><span>Meta frontier intelligence</span><span aria-hidden="true">×</span><span>Polymath’s engine across the world’s science</span></p>
              <HeroBridge />
              <div className="mode-switch" role="group" aria-label="Research mode">
                {modes.map((item, index) => <Fragment key={item.id}>
                  <button type="button" aria-pressed={!converging && !solving && mode === item.id}
                    onClick={() => { setMode(item.id); setConverging(false); setSolving(false); setNotice('') }}>
                    <span className="mode-number">{item.number}</span>{item.label}
                  </button>
                  {index === 0 && <button type="button" aria-pressed={solving} onClick={() => { setSolving(true); setConverging(false); setNotice('') }}><span className="mode-number">02</span>Solve</button>}
                </Fragment>)}
                <button type="button" aria-pressed={converging} onClick={() => { setConverging(true); setSolving(false); setNotice('') }}><span className="mode-number">05</span>Converge</button>
              </div>
              <p className="composer-description mode-description">{solving ? <>Describe a problem. Polymath looks for an answer anywhere in science, and if none fits, builds one from the closest answers in other fields.</> : converging ? <>Choose how many fields, and any you want included. Polymath connects them, then finds what those connections share, level after level. <span className="nav-badge">Experimental</span></> : content.description}</p>
              {converging && <ConvergeComposer csrfToken={workspace.csrfToken} ready={workspace.phase === 'ready'} onCreated={(programme) => setConvergeFocus(programme.id)} onSessionExpired={workspace.invalidateSession} />}
              {solving && <SolveComposer csrfToken={workspace.csrfToken} ready={workspace.phase === 'ready'} canSubmit={workspace.canSubmit && !artifactBusy && !assessmentBusy} onCreated={(programme) => setSolveFocus(programme.id)} onSessionExpired={workspace.invalidateSession} />}
              <form onSubmit={handleSubmit} hidden={converging || solving}>
                <label htmlFor="research-prompt" className="composer-label">{content.label}</label>
                <div className="prompt-box">
                  <textarea id="research-prompt" value={prompts[mode]}
                    onChange={(event) => setPrompts((previous) => ({ ...previous, [mode]: event.target.value }))}
                    placeholder={content.placeholder} rows={3} maxLength={2000}
                    aria-describedby="prompt-note research-availability" />
                  <div className="prompt-footer">
                    <button className="primary-button" type="submit" disabled={!researchConnected}
                      aria-describedby="research-availability">{workspace.submitting ? 'Starting…' : content.action}<Arrow /></button>
                  </div>
                </div>
                <p id="prompt-note" className="prompt-note">{content.note}</p>
                {content.examples.length > 0 && (
                  <div className="example-row">
                    <span>Try</span>
                    {content.examples.map((example) => (
                      <button type="button" key={example}
                        onClick={() => setPrompts((previous) => ({ ...previous, [mode]: example }))}>
                        {example}
                      </button>
                    ))}
                  </div>
                )}
                {mode === 'surprise' && workspace.discoveryIntent && <details className="calm-details"><summary>About this run’s limits</summary><p>This approved discovery run can save up to {workspace.discoveryIntent.max_stages} evidence-backed bridges, with local depth up to {workspace.discoveryIntent.max_depth}. The model chooses whether to explore, deepen, connect saved stages or stop. The ceilings do not promise a discovery.</p></details>}
              </form>
              {!converging && <RigourBar />}
              <p className="availability-note" id="research-availability" role="status">{statusText}</p>
              <p className="ways-line">{freeAccess?.remaining_session_jobs_today != null && freeAccess.limits ? `Free · ${freeAccess.remaining_session_jobs_today} of ${freeAccess.limits.max_jobs_per_session_day} investigations left today · ` : ''}<a href="/about#ways" onClick={(event) => { event.preventDefault(); go('about'); window.setTimeout(() => document.getElementById('ways')?.scrollIntoView?.(), 50) }}>Ways to use Polymath</a></p>
              <p className="sources-line">Reads from <b>arXiv</b> · <b>OpenAlex</b> <span aria-hidden="true">·</span> Publishes to <b>Zenodo</b> (CERN)</p>
              {notice && <p className="composer-alert" role="alert">{notice}</p>}
              {!(workspace.native.phase === 'disabled' && !workspace.native.access) && <NativeResearchNotice context={workspace.native} />}
            </section>
          </>}
          <div hidden={!(place === 'research' && solving)}>
            <SolveWorkspace enabled={workspace.phase === 'ready'} csrfToken={workspace.csrfToken} canSubmit={workspace.canSubmit && !artifactBusy && !assessmentBusy} focusId={solveFocus} submit={(solveMode, goal, parents) => workspace.submit(solveMode, goal, parents)} onOpenRun={(id) => { setSolving(false); openRun(id) }} onSessionExpired={workspace.invalidateSession} />
          </div>
          <div hidden={!(place === 'research' && converging)}>
            <ConvergeWorkspace enabled={workspace.phase === 'ready'} csrfToken={workspace.csrfToken} canSubmit={workspace.canSubmit && !artifactBusy && !assessmentBusy} focusId={convergeFocus} onFinished={onConvergeFinished} notifyControl={<NotifyToggle notifier={notifier} />} submit={(convergeMode, goal, parents) => workspace.submit(convergeMode, goal, parents)} liveActivity={workspace.activeRun && workspace.events.length ? { runId: workspace.activeRun.id, summary: workspace.events[workspace.events.length - 1].summary } : null} onOpenRun={(id) => { setConvergeReading(id); openRun(id) }} onGoDeeper={goDeeper} onSessionExpired={workspace.invalidateSession} />
          </div>
          {place === 'research' && !solving && (!converging || (convergeReading !== null && convergeReading === workspace.selectedId)) && <>
            <div ref={focusAnchor} className="focus-anchor" />
            {workspace.selectedId && !workspace.activeRun && <p className="history-note" role="status">Opening the investigation…</p>}
            {workspace.detailError && <div className="research-detail-error" role="alert"><p>{workspace.detailError}</p>{workspace.selectedId && <button type="button" className="text-button" onClick={() => workspace.selectRun(workspace.selectedId!)}>Reload investigation</button>}</div>}
            {!selectedFindingId && workspace.activeRun && <ResearchRunView notifyControl={<NotifyToggle notifier={notifier} />} onGoDeeper={workspace.activeRun.result_id && !workspace.activeRun.discovery_intent && !workspace.activeRun.explore_collection_intent ? () => goDeeper(workspace.activeRun!.result_id!) : undefined} hideOutcome={!!workspace.activeRun.discovery_intent || !!workspace.activeRun.explore_collection_intent} run={workspace.activeRun} events={workspace.events} result={workspace.result} connection={workspace.connection} onCancel={workspace.canCancel ? () => void workspace.cancel() : undefined} cancelling={workspace.cancelling} onOpenRun={openRun} findingVersion={findingResultId ? { head: findingHead.head, phase: findingHead.phase, pollPaused: findingHead.pollPaused, onRefresh: findingHead.refresh, onOpenFinding: openFinding } : undefined} />}
            {!selectedFindingId && workspace.activeRun?.explore_collection_intent && <ExploreWorkspace key={`collection:${workspace.activeRun.id}`} run={workspace.activeRun} events={workspace.events} enabled={workspace.phase === 'ready'} csrfToken={workspace.csrfToken} onSessionExpired={workspace.invalidateSession} onOpenFinding={openFinding} onStagesChanged={discoveryChanged} />}
            {!selectedFindingId && workspace.activeRun?.discovery_intent && <DiscoveryWorkspace key={`discovery:${workspace.activeRun.id}`} run={workspace.activeRun} events={workspace.events} finalResult={workspace.result} enabled={workspace.phase === 'ready'} csrfToken={workspace.csrfToken} canGenerate={workspace.canSubmit && !assessmentBusy} onSessionExpired={workspace.invalidateSession} onOpenFinding={openFinding} onOpenRun={openRun} onBusyChange={setArtifactBusy} onStagesChanged={discoveryChanged} />}
            {!selectedFindingId && workspace.activeRun && <NativeResumeControl run={workspace.activeRun} csrfToken={workspace.csrfToken} onResumed={workspace.refresh} onSessionExpired={workspace.invalidateSession} />}
            {!selectedFindingId && !workspace.activeRun?.discovery_intent && !workspace.activeRun?.explore_collection_intent && workspace.phase === 'ready' && workspace.activeRun?.status === 'succeeded' && workspace.activeRun.result_id && workspace.result?.finding && (workspace.result.finding.outcome === 'bridge' || workspace.result.finding.outcome === 'answer') && <ArtifactWorkspace key={workspace.activeRun.result_id} sourceResultId={workspace.activeRun.result_id} sourceKind={workspace.result.finding.outcome} csrfToken={workspace.csrfToken} enabled={workspace.phase === 'ready'} canGenerate={workspace.canSubmit && findingHead.canGenerate && !assessmentBusy} sourceCurrent={findingHead.canGenerate} onSessionExpired={workspace.invalidateSession} onBusyChange={setArtifactBusy} />}
            {!selectedFindingId && !workspace.activeRun?.discovery_intent && !workspace.activeRun?.explore_collection_intent && findingResultId && <ShareWorkspace key={`share:${findingResultId}`} sourceResultId={findingResultId} enabled={workspace.phase === 'ready'} csrfToken={workspace.csrfToken} sourceCurrent={findingHead.canGenerate} refreshToken={assessmentRefresh} onSessionExpired={workspace.invalidateSession} />}
            {selectedFindingId && <FindingVersionWorkspace key={selectedFindingId} resultId={selectedFindingId} enabled={workspace.phase === 'ready'} csrfToken={workspace.csrfToken} canGenerate={workspace.canSubmit && !assessmentBusy} refreshToken={assessmentRefresh} onSessionExpired={workspace.invalidateSession} onOpenFinding={openFinding} onOpenRun={openRun} onBusyChange={setArtifactBusy} onClose={() => setSelectedFindingId(null)} />}

            {!converging && !((workspace.phase === 'ready' || workspace.phase === 'idle') && recent.length === 0) && <section className="recent" aria-labelledby="history-title">
              <div className="recent-heading"><h2 id="history-title">Recent investigations</h2><button type="button" className="text-button" disabled={workspace.phase === 'loading' || connection !== 'connected' || !system?.database || !system.migrations} onClick={workspace.refresh}>Reload saved research</button></div>
              {recent.length === 0 && <div className="recent-empty">
                <svg className="recent-mark" viewBox="0 0 120 64" aria-hidden="true"><path d="M10 44 L44 16 L92 26 L62 54 Z M44 16 L62 54" /><circle cx="10" cy="44" r="3.5" /><circle cx="44" cy="16" r="5.5" /><circle cx="92" cy="26" r="4" /><circle cx="62" cy="54" r="3" /></svg>
                <p className="empty-label">{workspace.phase === 'loading' ? 'Loading saved research…' : workspace.phase === 'error' || workspace.phase === 'expired' ? 'Saved research unavailable' : 'No investigation started'}</p>
                {workspace.phase === 'ready' && <p className="history-note">No saved investigations in this browser session.</p>}
              </div>}
              {recent.length > 0 && <ul className="recent-list" aria-label="Your saved investigations">{recent.map((run) => <li key={run.id}><button type="button" className={`history-card mode-${run.mode} status-${run.status}`} aria-pressed={workspace.selectedId === run.id} onClick={() => openRun(run.id)}><span className="history-mode">{run.mode === 'surprise' ? 'Surprise me' : run.mode === 'ask' ? 'Ask' : 'Explore'} · {run.status.replaceAll('_', ' ')}</span><span className="history-goal">{run.goal || 'An open discovery'}</span></button></li>)}</ul>}
              {workspace.nextCursor && <button className="history-more" type="button" disabled={workspace.loadingMore} onClick={() => void workspace.loadMore()}>{workspace.loadingMore ? 'Loading more…' : 'Load earlier investigations'}</button>}
              {workspace.error && <p className="history-error" role="alert">{workspace.error}</p>}
            </section>}
            {!converging && !workspace.activeRun && !selectedFindingId && !workspace.selectedId && <><Story onFrontier={() => go('frontier')} onStudio={() => go('studio')} /><Credits /></>}
          </>}

          {place === 'library' && <>
            <header className="place-heading"><h1>Your library</h1><p>Everything you have saved: your findings, how they connect, the literature you are watching, and publishing.</p></header>
            <div className="library-sections-row"><div className="mode-switch library-sections" role="group" aria-label="Library section">
              {LIBRARY_SECTIONS.map((item) => <button key={item.id} type="button" aria-pressed={librarySection === item.id} onClick={() => setLibrarySection(item.id)}>{item.label}</button>)}
            </div></div>
            <div hidden={librarySection === 'publishing'}><KnowledgeBrowser section={librarySection === 'publishing' ? 'findings' : librarySection} enabled={workspace.phase === 'ready'} onOpenRun={openRun} onOpenFinding={openFindingHere} refreshToken={`${workspace.activeRun?.result_id ?? ''}:${assessmentRefresh}:${discoveryRefresh}`} canInvestigate={workspace.canSubmit && !artifactBusy && !assessmentBusy} investigating={workspace.submitting} onInvestigateBridges={investigateBridges} csrfToken={workspace.csrfToken} onSessionExpired={workspace.invalidateSession} onAssessmentApplied={assessmentChanged} onAssessmentBusyChange={setAssessmentBusy} deeperFrom={deeperFrom} onDeeperShown={() => setDeeperFrom(null)} /></div>
          </>}
          <div hidden={place !== 'library' || librarySection !== 'publishing'}>
            <YourZenodo csrfToken={workspace.csrfToken} enabled={workspace.phase === 'ready'} native={workspace.native.submission ?? undefined} standalone />
            <PublishingSection csrfToken={workspace.csrfToken} enabled={workspace.phase === 'ready'} native={workspace.native.submission ?? undefined} />
          </div>

          {place === 'frontier' && <section className="composer frontier-composer" aria-labelledby="frontier-composer-title">
            <p className="composer-tagline">Frontier research · Experimental</p>
            <h1 id="frontier-composer-title">Take on a problem nobody has solved yet.</h1>
            <p className="composer-description">Polymath surveys the literature, names the gaps, looks for solutions in other fields and breaks stubborn gaps into smaller steps, mapping the path from question to answer.</p>
            <form onSubmit={handleFrontier}>
              <label htmlFor="frontier-prompt" className="composer-label">What unsolved problem should Polymath work on?</label>
              <div className="prompt-box">
                <textarea id="frontier-prompt" value={frontierQuestion} onChange={(event) => setFrontierQuestion(event.target.value)} placeholder="An open problem, stated as precisely as you can…" rows={3} maxLength={2000} />
                <div className="prompt-footer"><button className="primary-button" type="submit" disabled={!frontierReady || !frontierQuestion.trim()}>{creatingFrontier ? 'Starting…' : 'Start a frontier programme'}<Arrow /></button></div>
              </div>
              <p className="prompt-note">Each step runs as an ordinary investigation with its own evidence and budget. Keep this tab open while it works.</p>
            </form>
            {notice && <p className="composer-alert" role="alert">{notice}</p>}
          </section>}
          <div hidden={place !== 'studio'}>
            <StudioWorkspace enabled={workspace.phase === 'ready'} csrfToken={workspace.csrfToken} canSubmit={workspace.canSubmit && !artifactBusy && !assessmentBusy} submit={(studioMode, goal, parents) => workspace.submit(studioMode, goal, parents)} onOpenRun={openRun} onSolveGap={(gap) => { if (!workspace.csrfToken) return; void createSolve(workspace.csrfToken, { problem: gap.slice(0, 2000) }).then((programme) => { setSolveFocus(programme.id); setSolving(true); setConverging(false); go('research') }).catch(() => setNotice('Solve could not start for that step. Try again in a moment.')) }} onSessionExpired={workspace.invalidateSession} />
          </div>
          <div hidden={place !== 'frontier'}>
            <FrontierWorkspace enabled={workspace.phase === 'ready'} csrfToken={workspace.csrfToken} canSubmit={workspace.canSubmit && !artifactBusy && !assessmentBusy} focusId={frontierFocus} onFinished={onFrontierFinished} onSolveStep={(programmeId, nodeId) => { if (!workspace.csrfToken) return; void createSolve(workspace.csrfToken, { frontier_programme_id: programmeId, frontier_node_id: nodeId }).then((programme) => { setSolveFocus(programme.id); setSolving(true); setConverging(false); go('research') }).catch(() => undefined) }} notifyControl={<NotifyToggle notifier={notifier} />} submit={(frontierMode, goal, parents) => workspace.submit(frontierMode, goal, parents)} onOpenRun={openRun} onSessionExpired={workspace.invalidateSession} />
          </div>

          {place === 'about' && <AboutPlace remaining={freeAccess?.remaining_session_jobs_today} daily={freeAccess?.limits?.max_jobs_per_session_day} />}
        </main>
        <CompletionToast notifier={notifier} />
        <footer className="site-footer">
          <ul className="status-strip" aria-label="Service status" aria-live="polite">
            <StatusLine label="Application" state={connection === 'loading' ? 'unknown' : system?.api ? 'ok' : 'waiting'} value={connection === 'loading' ? 'Checking' : system?.api ? 'Connected' : 'Unreachable'} />
            <StatusLine label="Research memory" state={system?.database && system.migrations ? 'ok' : 'unknown'} value={!system ? 'Not checked' : system.database && system.migrations ? 'Ready' : 'Not ready'} />
            <StatusLine label="Meta connection" state="unknown" value={!system ? 'Not checked' : system.metaConfigured ? 'Key present · unverified' : 'Not configured'} />
            <li><button type="button" className="text-button" disabled={connection === 'loading'} onClick={() => setRetry((value) => value + 1)}>Check again</button></li>
          </ul>
          <span className="footer-links"><a href="/library">Public library</a><a href="/operator/budget">Operator desk</a><span>Built for the Meta Global AI Developer Hackathon with Cerebral Valley · Intelligence by Meta’s frontier Muse models · A project by Ekram Alam</span></span>
        </footer>
      </div>
    </NativeResearchProvider>
  )
}
