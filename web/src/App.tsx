import { useCallback, useEffect, useState } from 'react'
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

type Mode = 'explore' | 'ask' | 'surprise'

const modes: { id: Mode; label: string; number: string }[] = [
  { id: 'explore', label: 'Explore', number: '01' },
  { id: 'ask', label: 'Ask', number: '02' },
  { id: 'surprise', label: 'Surprise me', number: '03' },
]

const copy = {
  explore: {
    eyebrow: 'FOLLOW A CONCEPT',
    title: 'Ideas travel further than disciplines.',
    description: 'Start with something you’re curious about. Investigate where its underlying structure appears in another field.',
    label: 'What would you like to explore?',
    placeholder: 'A concept, a phenomenon, an unexpected connection…',
    action: 'Explore connections',
    note: 'A good investigation can end with a connection—or a reason to reject one.',
    examples: ['Self-organised criticality', 'Error-correcting codes', 'Phase transitions in learning'],
  },
  ask: {
    eyebrow: 'INVESTIGATE A QUESTION',
    title: 'Good answers begin with better questions.',
    description: 'Bring an open question. Investigate what the literature supports, what follows from it, and what remains uncertain.',
    label: 'What question is on your mind?',
    placeholder: 'What is the truth about…?',
    action: 'Investigate question',
    note: 'Answers will distinguish known findings, postulated connections, and speculation.',
    examples: ['Why is sleep necessary?', 'What limits how fast cities grow?'],
  },
  surprise: {
    eyebrow: 'MAKE ROOM FOR SERENDIPITY',
    title: 'Find the question you hadn’t thought to ask.',
    description: 'Let Polymath choose distant fields, look for shared structures, and investigate what holds up under scrutiny.',
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

export function App() {
  const [mode, setMode] = useState<Mode>('explore')
  const [prompts, setPrompts] = useState<Record<Mode, string>>({ explore: '', ask: '', surprise: '' })
  const [system, setSystem] = useState<SystemStatus | null>(null)
  const [connection, setConnection] = useState<'loading' | 'connected' | 'offline'>('loading')
  const [retry, setRetry] = useState(0)
  const [notice, setNotice] = useState('')
  const [artifactBusy, setArtifactBusy] = useState(false)
  const [assessmentBusy, setAssessmentBusy] = useState(false)
  const [assessmentRefresh, setAssessmentRefresh] = useState(0)
  const [selectedFindingId, setSelectedFindingId] = useState<string | null>(null)
  const content = copy[mode]
  const workspace = useResearchWorkspace(
    connection === 'connected' && !!system?.database && system.migrations,
    !!system?.researchAvailable,
  )
  const findingResultId = workspace.activeRun?.status === 'succeeded' && (workspace.result?.finding?.outcome === 'bridge' || workspace.result?.finding?.outcome === 'answer') ? workspace.activeRun.result_id : null
  const findingHead = useFindingHead({ resultId: findingResultId, enabled: workspace.phase === 'ready' && selectedFindingId === null, onSessionExpired: workspace.invalidateSession, refreshToken: assessmentRefresh })
  const assessmentChanged = useCallback(() => setAssessmentRefresh((previous) => previous + 1), [])
  const openFinding = useCallback((id: string) => { if (/^[a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12}$/.test(id)) setSelectedFindingId(id) }, [])
  const openRun = useCallback((id: string) => { setSelectedFindingId(null); workspace.selectRun(id) }, [workspace.selectRun])
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
  const researchConnected = workspace.canSubmit && !artifactBusy && !assessmentBusy && (mode === 'surprise' || !!prompts[mode].trim())
  const unavailable = connection === 'offline'
    ? 'The research service could not be reached. Check the connection and try again.'
    : 'Live investigations are not available yet. The research engine is being connected.'

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!researchConnected) { setNotice(unavailable); return }
    setNotice('')
    void workspace.submit(mode, prompts[mode])
  }

  return (
    <>
      <a className="skip-link" href="#workspace">Skip to research workspace</a>
      <div className="page-shell">
        <header className="site-header">
          <a className="brand" href="/" aria-label="Polymath home">
            <span className="brand-symbol" aria-hidden="true">p<span>·</span></span>
            <span>polymath</span>
          </a>
          <div className="header-note">A wider field of thought<span className="header-rule" />Research workspace</div>
          <nav className="public-navigation" aria-label="Polymath navigation"><a href="/library">Public library</a><a href="#approach">Our approach <Arrow diagonal /></a></nav>
        </header>

        <main id="workspace">
          <div className="workspace-heading">
            <span className="eyebrow">THE RESEARCH DESK</span>
            <span className="edition">01 / An open beginning</span>
          </div>

          <div className="workspace-grid">
            <section className="research-panel" aria-labelledby="research-title">
              <div className="mode-switch" role="group" aria-label="Research mode">
                {modes.map((item) => (
                  <button key={item.id} type="button" aria-pressed={mode === item.id}
                    onClick={() => { setMode(item.id); setNotice('') }}>
                    <span className="mode-number">{item.number}</span>{item.label}
                  </button>
                ))}
              </div>

              <div className="research-intro">
                <span className="eyebrow accent">{content.eyebrow}</span>
                <h1 id="research-title">{content.title}</h1>
                <p>{content.description}</p>
              </div>

              <form onSubmit={handleSubmit}>
                <label htmlFor="research-prompt">{content.label}</label>
                <div className="prompt-box">
                  <textarea id="research-prompt" value={prompts[mode]}
                    onChange={(event) => setPrompts((previous) => ({ ...previous, [mode]: event.target.value }))}
                    placeholder={content.placeholder} rows={3} maxLength={2000}
                    aria-describedby="prompt-note research-availability" />
                  <div className="prompt-footer">
                    <span>Curiosity starts here</span>
                    <button className="primary-button" type="submit" disabled={!researchConnected}
                      aria-describedby="research-availability">{workspace.submitting ? 'Queuing investigation…' : content.action}<Arrow /></button>
                  </div>
                </div>
                <p id="prompt-note" className="prompt-note">{content.note}</p>
                {content.examples.length > 0 && (
                  <div className="example-row">
                    <span>A starting point</span>
                    {content.examples.map((example) => (
                      <button type="button" key={example}
                        onClick={() => setPrompts((previous) => ({ ...previous, [mode]: example }))}>
                        {example}<span aria-hidden="true"> +</span>
                      </button>
                    ))}
                  </div>
                )}
              </form>

              <div className="availability-note" id="research-availability" role="status">
                <span className="small-star" aria-hidden="true">✳︎</span>
                <p>{connection === 'loading' ? 'Checking the research service…' : artifactBusy ? 'A formalisation or writing task is active. Follow its progress below.' : assessmentBusy ? 'An evidence reassessment is active. Follow its saved progress in the literature watch.' : workspace.canSubmit ? 'Research is available. Your investigation will be saved to this browser session.' : unavailable}</p>
              </div>
              {notice && <p role="alert">{notice}</p>}
            </section>

            <aside className="desk-sidebar" aria-label="Research desk status">
              <div className="sidebar-heading"><span className="eyebrow">ON YOUR DESK</span><Arrow diagonal /></div>
              {workspace.runs.length === 0 && <><div className="empty-illustration" aria-hidden="true">
                <svg viewBox="0 0 280 160" fill="none">
                  <path d="M39 102C80 102 80 49 125 49S180 115 231 74M39 102L125 49L231 74M125 49L154 130L231 74M39 102L154 130" stroke="currentColor" strokeWidth="1" />
                  <circle cx="39" cy="102" r="7" /><circle cx="125" cy="49" r="12" /><circle cx="231" cy="74" r="8" /><circle cx="154" cy="130" r="5" />
                  <circle cx="125" cy="49" r="22" className="orbit" /><circle cx="231" cy="74" r="16" className="orbit" />
                </svg>
                <span>Different fields. Shared structures.</span>
              </div>
              <h2>Your next connection<br />starts with a question.</h2>
              <p className="sidebar-description">When an investigation runs, its searches, source passages, and tested connections will appear here.</p>
              <div className="empty-label"><span className="dot waiting" aria-hidden="true" />{workspace.phase === 'loading' ? 'Loading saved research…' : workspace.phase === 'error' || workspace.phase === 'expired' ? 'Saved research unavailable' : 'No investigation started'}</div></>}

              <section className="research-history" aria-labelledby="history-title">
                <div className="system-heading"><h3 id="history-title">Saved investigations</h3><button type="button" className="text-button" disabled={workspace.phase === 'loading' || connection !== 'connected' || !system?.database || !system.migrations} onClick={workspace.refresh}>Reload saved research</button></div>
                {workspace.phase === 'ready' && workspace.runs.length === 0 && <p className="history-note">No saved investigations in this browser session.</p>}
                {workspace.runs.length > 0 && <ul aria-label="Your saved investigations">{workspace.runs.map((run) => <li key={run.id}><button type="button" aria-pressed={workspace.selectedId === run.id} onClick={() => openRun(run.id)}><span className="history-mode">{run.mode === 'surprise' ? 'Surprise me' : run.mode === 'ask' ? 'Ask' : 'Explore'} · {run.status.replaceAll('_', ' ')}</span><span className="history-goal">{run.goal || 'An open discovery'}</span></button></li>)}</ul>}
                {workspace.nextCursor && <button className="history-more" type="button" disabled={workspace.loadingMore} onClick={() => void workspace.loadMore()}>{workspace.loadingMore ? 'Loading more…' : 'Load earlier investigations'}</button>}
                {workspace.error && <p className="history-error" role="alert">{workspace.error}</p>}
              </section>

              <section className="system-status" aria-labelledby="system-title">
                <div className="system-heading"><h3 id="system-title">Workspace connection</h3>
                  <button type="button" className="text-button" disabled={connection === 'loading'}
                    onClick={() => setRetry((value) => value + 1)}>Check again</button>
                </div>
                <ul aria-live="polite">
                  <StatusLine label="Application" state={connection === 'loading' ? 'unknown' : system?.api ? 'ok' : 'waiting'}
                    value={connection === 'loading' ? 'Checking' : system?.api ? 'Connected' : 'Unreachable'} />
                  <StatusLine label="Research memory" state={system?.database && system.migrations ? 'ok' : 'unknown'}
                    value={!system ? 'Not checked' : system.database && system.migrations ? 'Ready' : 'Not ready'} />
                  <StatusLine label="Meta connection" state="unknown"
                    value={!system ? 'Not checked' : system.metaConfigured ? 'Key present · unverified' : 'Not configured'} />
                </ul>
              </section>
            </aside>
          </div>

          {workspace.selectedId && !workspace.activeRun && <p className="history-note" role="status">Loading the saved investigation…</p>}
          {workspace.detailError && <div className="research-detail-error" role="alert"><p>{workspace.detailError}</p>{workspace.selectedId && <button type="button" className="text-button" onClick={() => workspace.selectRun(workspace.selectedId!)}>Reload investigation</button>}</div>}
          {!selectedFindingId && workspace.activeRun && <ResearchRunView run={workspace.activeRun} events={workspace.events} result={workspace.result} connection={workspace.connection} onCancel={workspace.canCancel ? () => void workspace.cancel() : undefined} cancelling={workspace.cancelling} onOpenRun={openRun} findingVersion={findingResultId ? { head: findingHead.head, phase: findingHead.phase, pollPaused: findingHead.pollPaused, onRefresh: findingHead.refresh, onOpenFinding: openFinding } : undefined} />}

          {!selectedFindingId && workspace.phase === 'ready' && workspace.activeRun?.status === 'succeeded' && workspace.activeRun.result_id && workspace.result?.finding && (workspace.result.finding.outcome === 'bridge' || workspace.result.finding.outcome === 'answer') && <ArtifactWorkspace key={workspace.activeRun.result_id} sourceResultId={workspace.activeRun.result_id} sourceKind={workspace.result.finding.outcome} csrfToken={workspace.csrfToken} enabled={workspace.phase === 'ready'} canGenerate={workspace.canSubmit && findingHead.canGenerate && !assessmentBusy} sourceCurrent={findingHead.canGenerate} onSessionExpired={workspace.invalidateSession} onBusyChange={setArtifactBusy} />}
          {!selectedFindingId && findingResultId && <ShareWorkspace key={`share:${findingResultId}`} sourceResultId={findingResultId} enabled={workspace.phase === 'ready'} csrfToken={workspace.csrfToken} sourceCurrent={findingHead.canGenerate} refreshToken={assessmentRefresh} onSessionExpired={workspace.invalidateSession} />}
          {selectedFindingId && <FindingVersionWorkspace key={selectedFindingId} resultId={selectedFindingId} enabled={workspace.phase === 'ready'} csrfToken={workspace.csrfToken} canGenerate={workspace.canSubmit && !assessmentBusy} refreshToken={assessmentRefresh} onSessionExpired={workspace.invalidateSession} onOpenFinding={openFinding} onOpenRun={openRun} onBusyChange={setArtifactBusy} onClose={() => setSelectedFindingId(null)} />}

          <KnowledgeBrowser enabled={workspace.phase === 'ready'} onOpenRun={openRun} onOpenFinding={openFinding} refreshToken={`${workspace.activeRun?.result_id ?? ''}:${assessmentRefresh}`} canInvestigate={workspace.canSubmit && !artifactBusy && !assessmentBusy} investigating={workspace.submitting} onInvestigateBridges={workspace.investigateBridges} csrfToken={workspace.csrfToken} onSessionExpired={workspace.invalidateSession} onAssessmentApplied={assessmentChanged} onAssessmentBusyChange={setAssessmentBusy} />

          <section className="approach-section" id="approach" aria-labelledby="approach-title">
            <div className="approach-heading"><span className="eyebrow">HOW WE INVESTIGATE</span><h2 id="approach-title">Follow the evidence.<br /> <em>Keep the questions open.</em></h2></div>
            <div className="principle"><span className="principle-number">01</span><h3>Read across boundaries</h3><p>Find where distant disciplines describe a shared structure in different words.</p></div>
            <div className="principle"><span className="principle-number">02</span><h3>Challenge the connection</h3><p>Look for objections and counterexamples. Keep only what the evidence supports.</p></div>
            <div className="principle"><span className="principle-number">03</span><h3>Make it inspectable</h3><p>Trace findings to source passages, with assumptions and uncertainty in view.</p></div>
          </section>
        </main>
        <footer className="site-footer"><span>Polymath · Open questions, shared knowledge.</span><span>Designed for Meta intelligence <span className="footer-asterisk">✳︎</span></span></footer>
      </div>
    </>
  )
}
