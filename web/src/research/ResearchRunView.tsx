import type { ReactNode } from 'react'
import type {
  BridgeFinding,
  ResearchActivityEvent,
  ResearchResult,
  ResearchRunViewProps,
  SourceDocument,
  VerifiedQuote,
} from './types'
import './research.css'

const modeNames = { explore: 'Explore', ask: 'Ask', surprise: 'Surprise me' }
const states: Record<string, { label: string; description: string }> = {
  queued: { label: 'Queued', description: 'The investigation is waiting to begin.' },
  running: { label: 'Investigating', description: 'Follow the recorded research activity below.' },
  waiting_retry: { label: 'Waiting', description: 'The investigation is waiting before its next permitted attempt.' },
  cancel_requested: { label: 'Cancellation requested', description: 'Further research is being stopped. An operation already in progress may still finish.' },
  cancelled: { label: 'Cancelled', description: 'This investigation has stopped. Completed evidence is retained; an operation already sent may still incur usage.' },
  succeeded: { label: 'Complete', description: 'Review the recorded outcome and its supporting material below.' },
  failed: { label: 'Could not complete', description: 'The investigation stopped before completing. Any saved evidence remains available.' },
  interrupted: { label: 'Interrupted', description: 'The investigation paused unexpectedly. Saved evidence is retained, and research will not restart automatically.' },
}

const connectionNames: Record<ResearchRunViewProps['connection'], string> = {
  connecting: 'Connecting to saved activity',
  live: 'Activity connected',
  reconnecting: 'Reconnecting · recorded activity is retained',
  closed: 'Activity stream closed',
  error: 'Activity connection unavailable · recorded activity is retained',
}

// These records remain persisted and drive the SSE cursor. They are operational
// bookkeeping, rather than research activity to present in the workspace.
const operationalEvents = new Set(['checkpoint', 'external_started', 'external_observed', 'external_reconciled'])

function orderedEvents(events: ResearchActivityEvent[]): ResearchActivityEvent[] {
  const distinct = new Map<number, ResearchActivityEvent>()
  for (const event of events) {
    if (Number.isInteger(event.sequence) && event.sequence > 0 && !distinct.has(event.sequence)) {
      distinct.set(event.sequence, event)
    }
  }
  return [...distinct.values()]
    .filter((event) => !operationalEvents.has(event.kind))
    .sort((left, right) => left.sequence - right.sequence)
}

function safeSourceUrl(value: string): string | null {
  if (/[\s\u0000-\u001f\u007f]/u.test(value)) return null
  try {
    const url = new URL(value)
    const hostname = url.hostname.toLowerCase().replace(/\.+$/u, '')
    if (
      url.protocol !== 'https:' || url.username || url.password || !hostname ||
      hostname === 'localhost' || hostname.endsWith('.localhost') || hostname.endsWith('.local') ||
      hostname.startsWith('[') || /^\d+(?:\.\d+){0,3}$/.test(hostname)
    ) return null
    return url.href
  } catch {
    return null
  }
}

function SourceLink({ source }: { source: SourceDocument }) {
  const url = safeSourceUrl(source.url)
  return url
    ? <a className="research-source-link" href={url} target="_blank" rel="noopener noreferrer">{source.title}<svg className="research-source-arrow" width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" fill="none"><path d="M3 9 9 3M3 3h6v6" stroke="currentColor" strokeWidth="1.2" /></svg><span className="research-sr-only"> (opens in a new tab)</span></a>
    : <span className="research-source-title">{source.title}<span className="research-source-warning">Source link unavailable</span></span>
}

function sourceKind(kind: SourceDocument['content_kind']) {
  return kind === 'abstract' ? 'Abstract only' : 'Full text retrieved'
}

function sourceOrigin(source: SourceDocument): string {
  if (source.extraction_method.startsWith('openalex_')) return 'Saved OpenAlex abstract snapshot'
  if (source.extraction_method.startsWith('arxiv_')) return 'Saved arXiv abstract snapshot'
  return 'Saved source snapshot'
}

function quoteMatches(quote: VerifiedQuote, source: SourceDocument | undefined): boolean {
  if (!source || quote.content_kind !== source.content_kind ||
    !Number.isInteger(quote.start) || !Number.isInteger(quote.end) ||
    quote.start < 0 || quote.end <= quote.start || !/^[a-f0-9]{64}$/i.test(quote.source_sha256)) return false
  // Python records Unicode code-point offsets; JavaScript string offsets use UTF-16.
  const text = Array.from(source.text)
  return quote.end <= text.length && quote.end - quote.start === Array.from(quote.quote).length &&
    text.slice(quote.start, quote.end).join('') === quote.quote
}

function OutcomeNotice({ title, children }: { title: string; children: ReactNode }) {
  return <div className="research-outcome-notice"><h3>{title}</h3><p>{children}</p></div>
}


function PendingOutcome({ status }: { status: string }) {
  if (status === 'interrupted') return <OutcomeNotice title="Saved progress is retained">This investigation needs review before it can continue. It will not automatically repeat an operation whose outcome is unknown.</OutcomeNotice>
  if (status === 'cancel_requested') return <OutcomeNotice title="Cancellation is pending">Further research is being stopped. An operation already in progress may still return an outcome; recorded activity is retained.</OutcomeNotice>
  if (status === 'cancelled') return <OutcomeNotice title="No completed finding saved">The investigation was stopped before a completed finding was saved. Its recorded activity is retained.</OutcomeNotice>
  if (status === 'failed') return <OutcomeNotice title="No completed outcome saved">The investigation could not complete. Review the retained activity for its last recorded progress.</OutcomeNotice>
  if (status === 'succeeded') return <OutcomeNotice title="Completed outcome unavailable">The investigation is marked complete, but its saved outcome is not available in this view.</OutcomeNotice>
  return <OutcomeNotice title="No completed outcome yet">Completed findings and their source passages will appear here when an outcome is saved.</OutcomeNotice>
}

function Outcome({ result }: { result: ResearchResult }) {
  if (result.status === 'insufficient_evidence' && result.finding?.outcome === 'insufficient_evidence') {
    return <OutcomeNotice title="No supported connection established">{result.finding.reason}</OutcomeNotice>
  }
  if (result.status === 'limit_reached') {
    return <OutcomeNotice title="Research stopped at its limit">The investigation reached a configured research limit. Saved sources are available below; this outcome does not establish a connection.</OutcomeNotice>
  }
  if (result.status === 'failed') {
    return <OutcomeNotice title="The investigation could not complete">A research operation did not produce a usable outcome. Saved material is retained; this is not evidence that no connection exists.</OutcomeNotice>
  }
  if (result.status === 'bridge' && result.finding?.outcome === 'bridge') {
    return <Bridge finding={result.finding} />
  }
  return <OutcomeNotice title="Outcome unavailable">A complete research outcome is not available. Review the recorded activity and any saved sources.</OutcomeNotice>
}

function Bridge({ finding }: { finding: BridgeFinding }) {
  return (
    <article className="research-bridge" aria-labelledby="bridge-title">
      <div className="research-bridge-heading"><span className="eyebrow accent">CANDIDATE CONNECTION</span><p>{finding.field_left}<span aria-hidden="true"> ↔ </span><span className="research-sr-only"> and </span>{finding.field_right}</p></div>
      <h3 id="bridge-title">{finding.title}</h3>
      <p className="research-explanation">{finding.explanation}</p>
      <div className="research-mapping-wrap" role="region" aria-label="Structural mapping" tabIndex={0}>
        <table className="research-mapping"><caption>Proposed structural mapping</caption><thead><tr><th scope="col">{finding.field_left}</th><th scope="col">{finding.field_right}</th><th scope="col">Assumption</th></tr></thead><tbody>{finding.mappings.map((mapping, index) => <tr key={index}><td>{mapping.left}</td><td>{mapping.right}</td><td>{mapping.assumption}</td></tr>)}</tbody></table>
      </div>
      <div className="research-question"><h4>The open question</h4><p>{finding.open_question}</p></div>
      <div className="research-scrutiny"><div><h4>Strongest recorded objection</h4><p>{finding.strongest_objection}</p></div><div><h4>Model’s assessment</h4><p>{finding.objection_verdict}</p></div></div>
      <div className="research-novelty"><h4>Prior-work search and limitations</h4><p>{finding.novelty_limitations}</p><p className="research-scope-note">A limited literature search cannot establish that a connection is new. Model assessments are not independent scientific verification.</p></div>
    </article>
  )
}

function Evidence({ result }: { result: ResearchResult }) {
  const sources = new Map(result.sources.map((source) => [source.source_id, source]))
  return (
    <section className="research-evidence" aria-labelledby="research-evidence-title">
      <div className="research-section-heading"><div><span className="eyebrow accent">CHECK THE SOURCES</span><h3 id="research-evidence-title">Evidence you can inspect</h3></div><span className="research-count">{result.sources.length} saved {result.sources.length === 1 ? 'source' : 'sources'}</span></div>
      <p className="research-scope-note">Quotation matching checks the saved text. It does not establish whether a scientific interpretation or connection is correct.</p>
      {result.quotes.length > 0 && <div className="research-quotes">{result.quotes.map((quote, index) => {
        const source = sources.get(quote.source_id)
        const matched = quoteMatches(quote, source)
        return <details className="research-quote" key={`${quote.source_id}-${quote.start}-${index}`}><summary><span>{source ? source.title : 'Source unavailable'}</span><span className={`research-evidence-label${matched ? '' : ' needs-review'}`}>{matched ? sourceKind(quote.content_kind) : 'Quotation needs review'}</span></summary><div className="research-quote-body"><p className="research-quote-scope">{matched ? 'Quotation matches the saved source text.' : 'This quotation could not be matched to the saved source and must not be treated as verified evidence.'}</p><blockquote>{quote.quote}</blockquote><p className="research-claim"><span>Recorded support:</span> {quote.supports}</p>{source && <SourceLink source={source} />}</div></details>
      })}</div>}
      {result.sources.length > 0 ? <ul className="research-source-list" aria-label="Saved sources">{result.sources.map((source) => <li key={source.source_id}><div><SourceLink source={source} /><span className="research-source-kind">{sourceKind(source.content_kind)}</span></div><p>{sourceOrigin(source)}</p></li>)}</ul> : <p className="research-empty">No source text has been saved for this outcome.</p>}
    </section>
  )
}

export function ResearchRunView({ run, events, result, connection, onCancel, cancelling = false }: ResearchRunViewProps) {
  const state = states[run.status] ?? { label: 'Status unavailable', description: 'The investigation status could not be identified. Recorded material remains below.' }
  const activity = orderedEvents(events)
  const canCancel = onCancel && ['queued', 'running', 'waiting_retry'].includes(run.status)
  return (
    <section className="research-run-view" aria-labelledby="research-run-title">
      <header className="research-run-heading"><div><span className="eyebrow accent">{modeNames[run.mode].toUpperCase()} / RESEARCH RECORD</span><h2 id="research-run-title">{run.goal || 'An open discovery'}</h2></div><span className={`research-run-state state-${run.status}`} role="status">{state.label}</span></header>
      <div className="research-run-summary"><p>{state.description}</p>{canCancel && <button type="button" className="research-cancel" onClick={onCancel} disabled={cancelling}>{cancelling ? 'Requesting cancellation…' : 'Cancel investigation'}</button>}</div>
      <div className="research-run-layout">
        <section className="research-activity" aria-labelledby="research-activity-title"><div className="research-section-heading"><h3 id="research-activity-title">Research activity</h3><span className={`research-connection connection-${connection}`} aria-live="polite">{connectionNames[connection]}</span></div>{activity.length ? <ol className="research-timeline" aria-label="Recorded research activity" aria-live="polite" aria-relevant="additions">{activity.map((event, index) => <li key={event.sequence}><span className="research-event-number" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span><p>{event.summary}</p></li>)}</ol> : <p className="research-empty">No recorded activity yet.</p>}</section>
        <section className="research-results" aria-labelledby="research-outcome-title"><h3 className="research-sr-only" id="research-outcome-title">Research outcome</h3>{result ? <><Outcome result={result} /><Evidence result={result} /></> : <PendingOutcome status={run.status} />}</section>
      </div>
    </section>
  )
}
