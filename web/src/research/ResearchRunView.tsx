import { useId } from 'react'
import { ClaimBoundOverview } from '../scientificContext/ClaimBoundOverview'
import type { ReactNode } from 'react'
import { Bibliography } from './bibliography'
import { FindingVersionNotice } from '../assessments/FindingVersionNotice'
import { SourceCoverageNotice } from './SourceCoverageNotice'
import { groupSourceVersions, sourceContentLabel } from './sourcePresentation'
import type {
  AnswerClaim,
  AnswerFinding,
  BridgeFinding,
  FieldSelection,
  ResearchActivityEvent,
  ResearchResult,
  ResearchRunViewProps,
  SourceDocument,
  VerifiedQuote,
} from './types'
import './research.css'
import { Observatory } from './observatory/Observatory'
import { GoDeeper } from '../deeper/DeeperDiagram'

const terminalStatuses = new Set(['succeeded', 'failed', 'cancelled', 'interrupted'])
const modeNames = { explore: 'Explore', ask: 'Ask', surprise: 'Surprise me' }
const states: Record<string, { label: string; description: string }> = {
  queued: { label: 'Queued', description: 'The investigation is waiting to begin.' },
  running: { label: 'Investigating', description: 'Polymath is working. Watch each step as it happens, or ask to be told when it is done.' },
  waiting_retry: { label: 'Waiting', description: 'The investigation is waiting before its next permitted attempt.' },
  cancel_requested: { label: 'Cancellation requested', description: 'Further research is being stopped. An operation already in progress may still finish.' },
  cancelled: { label: 'Cancelled', description: 'This investigation has stopped. Completed evidence is retained; an operation already sent may still incur usage.' },
  succeeded: { label: 'Complete', description: 'Here is what Polymath found, with every source you can check.' },
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

// A short visible label per step makes the live record scannable. Unknown
// kinds still render, labelled generically.
const stepKinds: Record<string, { label: string; tone: string }> = {
  search_completed: { label: 'Search', tone: 'search' },
  source_read: { label: 'Reading', tone: 'read' },
  full_text_opened: { label: 'Reading', tone: 'read' },
  full_text_window_read: { label: 'Reading', tone: 'read' },
  stage_source_read: { label: 'Reading', tone: 'read' },
  memory_source_read: { label: 'Memory', tone: 'memory' },
  memory_searched: { label: 'Memory', tone: 'memory' },
  memory_finding_read: { label: 'Memory', tone: 'memory' },
  stage_parent_read: { label: 'Memory', tone: 'memory' },
  research_note: { label: 'Hypothesis', tone: 'idea' },
  fields_selected: { label: 'Direction', tone: 'idea' },
  parents_selected: { label: 'Direction', tone: 'idea' },
  stage_committed: { label: 'Discovery', tone: 'finding' },
  explore_ranked: { label: 'Ranking', tone: 'finding' },
  concluded: { label: 'Conclusion', tone: 'finding' },
  discovery_finished: { label: 'Conclusion', tone: 'finding' },
  explore_finished: { label: 'Conclusion', tone: 'finding' },
  tool_failed: { label: 'Setback', tone: 'setback' },
  model_failed: { label: 'Setback', tone: 'setback' },
  action_rejected: { label: 'Setback', tone: 'setback' },
  stopped: { label: 'Stopped', tone: 'setback' },
}

function stepKind(kind: string) {
  return stepKinds[kind] ?? { label: 'Step', tone: 'step' }
}

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

function sourceOrigin(source: SourceDocument): string {
  if (source.coverage) return 'Saved excerpt from a versioned arXiv HTML paper'
  if (source.extraction_method.startsWith('openalex_')) return 'Saved OpenAlex abstract snapshot'
  if (source.extraction_method.startsWith('arxiv_')) return 'Saved arXiv abstract snapshot'
  return 'Saved source snapshot'
}

function SourceVersions({ versions }: { versions: SourceDocument[] }) {
  const first = versions[0], excerpts = versions.filter((source) => source.coverage)
  const sameSnapshot = excerpts.length === versions.length && excerpts.every((source) => source.coverage?.snapshot_id === first.coverage?.snapshot_id)
  return <li className="research-paper-group"><div><SourceLink source={first} /><span className="research-source-kind">{versions.length === 1 ? sourceContentLabel(first.content_kind, first.coverage) : `${versions.length} saved ${sameSnapshot ? 'excerpts' : 'source versions'}`}</span></div>
    {versions.length > 1 && <p className="source-paper-count">{excerpts.length} saved full-text {excerpts.length === 1 ? 'excerpt' : 'excerpts'} from this paper identity. These versions are not independent papers.</p>}
    {versions.length === 1 && !first.coverage ? <><p>{sourceOrigin(first)}</p><SourceCoverageNotice contentKind={first.content_kind} /><Bibliography value={first.bibliography} /></> : <ul className="research-source-windows" aria-label={`Saved versions of ${first.title}`}>{versions.map((source) => <li key={source.source_id}><p><strong>{sourceContentLabel(source.content_kind, source.coverage)}</strong> · {sourceOrigin(source)}</p><SourceCoverageNotice coverage={source.coverage} contentKind={source.content_kind} />{source.coverage && <details className="research-source-window-text"><summary>Read this saved excerpt</summary><p className="research-source-text">{source.text}</p></details>}<Bibliography value={source.bibliography} /></li>)}</ul>}
  </li>
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
    return <OutcomeNotice title={result.mode === 'ask' ? 'No supported answer established' : 'No supported connection established'}>{result.finding.reason}</OutcomeNotice>
  }
  if (result.status === 'limit_reached') {
    return <OutcomeNotice title="Research stopped at its limit">The investigation reached a configured research limit. Saved sources are available below; this outcome does not establish {result.mode === 'ask' ? 'an answer' : 'a connection'}.</OutcomeNotice>
  }
  if (result.status === 'failed') {
    return <OutcomeNotice title="The investigation could not complete">A research operation did not produce a usable outcome. Saved material is retained; this is not evidence that {result.mode === 'ask' ? 'the question has no answer' : 'no connection exists'}.</OutcomeNotice>
  }
  if (result.status === 'bridge' && result.finding?.outcome === 'bridge') {
    return <Bridge finding={result.finding} />
  }
  if (result.status === 'answer' && result.finding?.outcome === 'answer') {
    return <Answer finding={result.finding} result={result} />
  }
  return <OutcomeNotice title="Outcome unavailable">A complete research outcome is not available. Review the recorded activity and any saved sources.</OutcomeNotice>
}

const claimLabels: Record<AnswerClaim['label'], string> = {
  known: 'Known', postulated: 'Postulated', speculative: 'Speculative',
}

function ClaimEvidence({ claim, result, ordinal }: { claim: AnswerClaim; result: ResearchResult; ordinal: number }) {
  const sources = new Map(result.sources.map((source) => [source.source_id, source]))
  const quotes = result.quotes.filter((quote) => quote.side === 'answer' && quote.claim_id === claim.id &&
    claim.evidence.some((item) => item.source_id === quote.source_id && item.quote === quote.quote && item.supports === quote.supports))
  return <section className="research-claim-evidence" aria-label={`Source evidence for claim ${ordinal}`}>
    {quotes.length ? quotes.map((quote, index) => {
      const source = sources.get(quote.source_id)
      const matched = quoteMatches(quote, source)
      return <details className="research-quote" key={`${claim.id}-${quote.source_id}-${index}`}><summary><span>{source?.title ?? 'Source unavailable'}</span><span className={`research-evidence-label${matched ? '' : ' needs-review'}`}>{matched ? sourceContentLabel(quote.content_kind, source?.coverage) : 'Quotation needs review'}</span></summary><div className="research-quote-body"><p className="research-quote-scope">{matched ? 'Quotation matches the saved source text.' : 'This quotation could not be matched to the saved source and must not be treated as verified evidence.'}</p><blockquote>{quote.quote}</blockquote><p className="research-claim"><span>Recorded support:</span> {quote.supports}</p>{source && <><SourceLink source={source} /><SourceCoverageNotice coverage={source.coverage} contentKind={source.content_kind} quoteStart={matched ? quote.start : undefined} quoteEnd={matched ? quote.end : undefined} />{source.coverage && matched && <details className="source-coverage-integrity"><summary>Saved excerpt content hash</summary><dl><dt>Excerpt content SHA-256</dt><dd>{quote.source_sha256}</dd></dl><p>This hash identifies the exact saved excerpt, separately from the complete extracted snapshot.</p></details>}</>}</div></details>
    }) : <p className="research-citation-warning">No matched source quotations saved for this claim.</p>}
  </section>
}

function Answer({ finding, result }: { finding: AnswerFinding; result: ResearchResult }) {
  const prefix = useId()
  const claimAnchor = (id: string) => `${prefix}-claim-${id}`
  if (!finding.claims.length) return <OutcomeNotice title="No cited answer saved">No source-backed claims were saved for this question. Review the unanswered questions and recorded activity.</OutcomeNotice>
  return <article className="research-answer" aria-labelledby="answer-title">
    <span className="eyebrow accent">EVIDENCE-LINKED ANSWER</span>
    <h3 id="answer-title">{finding.question}</h3>
    <ClaimSpectrum claims={finding.claims} />
    <details className="research-answer-labels"><summary>How to read the claim labels</summary><p><strong>Known</strong> is the model’s source-supported classification. <strong>Postulated</strong> is an inference across sources. <strong>Speculative</strong> is a tentative possibility.</p><p className="research-scope-note">These are model assessments, not guarantees of truth. Matched quotations do not independently verify the interpretation.</p></details>
    {result.overview_binding ? <ClaimBoundOverview finding={finding} binding={result.overview_binding} claimAnchor={claimAnchor} /> : <div className="research-answer-overview"><h4>Model overview</h4><p className="research-answer-summary">{finding.summary}</p><p className="research-scope-note">This overview is the model’s assessment. The classified claims below carry the source quotations.</p></div>}
    <ol className="research-answer-claims" aria-label="Answer claims">{finding.claims.map((claim, index) => <li key={claim.id} id={claimAnchor(claim.id)} tabIndex={-1}>
      <div className="research-answer-claim-heading"><span className={`research-claim-label label-${claim.label}`}>{claimLabels[claim.label]}</span><span className="research-claim-number">Claim {index + 1}</span></div>
      <p className="research-answer-claim-text">{claim.text}</p>
      {claim.derivation && <div className="research-derivation"><h4>How this inference was made</h4><p>{claim.derivation}</p></div>}
      {claim.caveats.length > 0 && <div className="research-answer-caveats"><h4>What limits this claim</h4><ul aria-label={`Caveats for claim ${index + 1}`}>{claim.caveats.map((caveat, position) => <li key={position}>{caveat}</li>)}</ul></div>}
      <ClaimEvidence claim={claim} result={result} ordinal={index + 1} />
    </li>)}</ol>
    <section className="research-answer-limits" aria-labelledby="answer-limits-title"><h4 id="answer-limits-title">Limits of this answer</h4><ul>{finding.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul></section>
    <section className="research-unanswered" aria-labelledby="answer-unanswered-title"><h4 id="answer-unanswered-title">What remains unanswered</h4>{finding.unanswered_questions.length ? <ul>{finding.unanswered_questions.map((item, index) => <li key={index}>{item}</li>)}</ul> : <p>No further questions were recorded. This does not establish that the question is fully resolved.</p>}</section>
  </article>
}

function SelectedFields({ selections }: { selections: FieldSelection[] }) {
  if (!selections.length) return null
  return <section className="research-selected-fields" aria-labelledby="selected-fields-title">
    <div className="research-section-heading"><div><span className="eyebrow accent">A DIRECTION CHOSEN BY THE MODEL</span><h3 id="selected-fields-title">Fields selected for this investigation</h3></div></div>
    <p className="research-scope-note">These are the research model’s recorded choices and concise rationale. Selecting fields does not establish a connection.</p>
    <ol className="research-field-directions" aria-label="Recorded research directions">{selections.map((selection, index) => <li key={index}>
      <span className="research-field-direction-label">{selections.length === 1 ? 'Selected direction' : index === selections.length - 1 ? 'Final selected direction' : `Earlier direction ${index + 1}`}</span>
      <ul className="research-field-tags" aria-label={`Fields in research direction ${index + 1}`}>{selection.fields.map((field) => <li key={field}>{field}</li>)}</ul>
      <p>{selection.rationale}</p>
    </li>)}</ol>
  </section>
}

function ResearchMemory({ result, onOpenRun }: { result: ResearchResult; onOpenRun?: (id: string) => void }) {
  const reads = result.memory_reads ?? []
  if (!reads.length) return null
  return <section className="research-memory" aria-labelledby="research-memory-title">
    <div className="research-section-heading"><div><span className="eyebrow accent">REVISITING EARLIER WORK</span><h3 id="research-memory-title">Saved findings consulted</h3></div><span className="research-count">{reads.length} {reads.length === 1 ? 'finding' : 'findings'}</span></div>
    <p className="research-scope-note">Earlier findings were revisited as research leads. Their conclusions do not provide independent evidence; inspect the original source passages and this investigation’s objections.</p>
    <ol className="research-memory-list" aria-label="Earlier findings consulted">{reads.map((read, index) => <li key={read.result_id}>
      {onOpenRun ? <button type="button" onClick={() => onOpenRun(read.run_id)}>Open earlier investigation {index + 1}</button> : <span>Earlier finding {index + 1}</span>}
      <details><summary>Saved version</summary><dl><dt>Finding</dt><dd>{read.result_id}</dd><dt>Content hash</dt><dd>{read.content_sha256}</dd></dl></details>
    </li>)}</ol>
  </section>
}

// A visual reading of the mapping table, which stays the accessible record.
function BridgeDiagram({ finding }: { finding: BridgeFinding }) {
  return <div className="bridge-diagram" aria-hidden="true">
    <div className="bridge-diagram-fields"><span>{finding.field_left}</span><svg className="bridge-diagram-arc" viewBox="0 0 200 40" preserveAspectRatio="none"><path d="M 4 36 Q 100 -14 196 36" pathLength={1} /></svg><span>{finding.field_right}</span></div>
    {finding.mappings.map((mapping, index) => <div className="bridge-diagram-row" key={index} style={{ animationDelay: `${index * 120}ms` }}>
      <span className="bridge-diagram-term is-left">{mapping.left}</span>
      <svg viewBox="0 0 100 20" preserveAspectRatio="none"><path d="M 0 10 C 35 0, 65 20, 100 10" pathLength={1} /><circle cx="0" cy="10" r="3" /><circle cx="100" cy="10" r="3" /></svg>
      <span className="bridge-diagram-term is-right">{mapping.right}</span>
    </div>)}
  </div>
}

function ClaimSpectrum({ claims }: { claims: AnswerClaim[] }) {
  const counts = (['known', 'postulated', 'speculative'] as const).map((label) => [label, claims.filter((claim) => claim.label === label).length] as const)
  return <figure className="claim-spectrum" aria-label={`${claims.length} claims: ${counts.map(([label, count]) => `${count} ${claimLabels[label].toLowerCase()}`).join(', ')}.`}>
    <div className="claim-spectrum-bar">{counts.filter(([, count]) => count).map(([label, count]) => <span key={label} className={`is-${label}`} style={{ flexGrow: count }} />)}</div>
    <figcaption>{counts.map(([label, count]) => <span key={label}><i className={`is-${label}`} aria-hidden="true" />{claimLabels[label]} <strong>{count}</strong></span>)}</figcaption>
  </figure>
}

function Bridge({ finding }: { finding: BridgeFinding }) {
  return (
    <article className="research-bridge" aria-labelledby="bridge-title">
      <div className="research-bridge-heading"><span className="eyebrow accent">CANDIDATE CONNECTION</span><p>{finding.field_left}<span aria-hidden="true"> ↔ </span><span className="research-sr-only"> and </span>{finding.field_right}</p></div>
      <h3 id="bridge-title">{finding.title}</h3>
      <p className="research-explanation">{finding.explanation}</p>
      <BridgeDiagram finding={finding} />
      <div className="research-mapping-wrap" role="region" aria-label="Structural mapping" tabIndex={0}>
        <table className="research-mapping"><caption>Proposed structural mapping</caption><thead><tr><th scope="col">{finding.field_left}</th><th scope="col">{finding.field_right}</th><th scope="col">Assumption</th></tr></thead><tbody>{finding.mappings.map((mapping, index) => <tr key={index}><td>{mapping.left}</td><td>{mapping.right}</td><td>{mapping.assumption}</td></tr>)}</tbody></table>
      </div>
      <div className="research-question"><h4>The open question</h4><p>{finding.open_question}</p></div>
      <div className="research-scrutiny"><div><h4>Strongest recorded objection</h4><p>{finding.strongest_objection}</p></div><div><h4>Model’s assessment</h4><p>{finding.objection_verdict}</p></div></div>
      <div className="research-novelty"><h4>Prior-work search and limitations</h4><p>{finding.novelty_limitations}</p><p className="research-scope-note">A limited literature search cannot establish that a connection is new. Model assessments are not independent scientific verification.</p></div>
    </article>
  )
}

function Evidence({ result, primaryPaperGroups }: { result: ResearchResult; primaryPaperGroups?: { paper_id: string; source_ids: string[] }[] }) {
  const sources = new Map(result.sources.map((source) => [source.source_id, source]))
  const groups = primaryPaperGroups ? primaryPaperGroups.map((group) => ({ identity: group.paper_id, versions: group.source_ids.flatMap((sourceId) => { const source = sources.get(sourceId); return source ? [source] : [] }) })) : groupSourceVersions(result.sources), hasExcerpts = result.sources.some((source) => source.coverage)
  return (
    <section className="research-evidence" aria-labelledby="research-evidence-title">
      <div className="research-section-heading"><div><span className="eyebrow accent">CHECK THE SOURCES</span><h3 id="research-evidence-title">Evidence you can inspect</h3></div><span className="research-count">{hasExcerpts || primaryPaperGroups ? `${result.sources.length} saved source versions · ${groups.length} paper ${groups.length === 1 ? 'identity' : 'identities'}` : `${result.sources.length} saved ${result.sources.length === 1 ? 'source' : 'sources'}`}</span></div>
      <p className="research-scope-note">Quotation matching checks the saved text. It does not establish whether a scientific interpretation or connection is correct.</p>{hasExcerpts && <p className="source-paper-count">Excerpts and versions from the same arXiv paper share one paper identity. Saved windows do not establish that the whole paper was read or provide independent corroboration.</p>}
      {result.finding?.outcome !== 'answer' && result.quotes.length > 0 && <div className="research-quotes">{result.quotes.map((quote, index) => {
        const source = sources.get(quote.source_id)
        const matched = quoteMatches(quote, source)
        return <details className="research-quote" key={`${quote.source_id}-${quote.start}-${index}`}><summary><span>{source ? source.title : 'Source unavailable'}</span><span className={`research-evidence-label${matched ? '' : ' needs-review'}`}>{matched ? sourceContentLabel(quote.content_kind, source?.coverage) : 'Quotation needs review'}</span></summary><div className="research-quote-body"><p className="research-quote-scope">{matched ? 'Quotation matches the saved source text.' : 'This quotation could not be matched to the saved source and must not be treated as verified evidence.'}</p><blockquote>{quote.quote}</blockquote><p className="research-claim"><span>Recorded support:</span> {quote.supports}</p>{source && <><SourceLink source={source} /><SourceCoverageNotice coverage={source.coverage} contentKind={source.content_kind} quoteStart={matched ? quote.start : undefined} quoteEnd={matched ? quote.end : undefined} />{source.coverage && matched && <details className="source-coverage-integrity"><summary>Saved excerpt content hash</summary><dl><dt>Excerpt content SHA-256</dt><dd>{quote.source_sha256}</dd></dl><p>This hash identifies the exact saved excerpt, separately from the complete extracted snapshot.</p></details>}</>}</div></details>
      })}</div>}
      {result.sources.length > 0 ? <ul className="research-source-list" aria-label="Saved sources">{groups.map((group) => <SourceVersions key={group.identity} versions={group.versions} />)}</ul> : <p className="research-empty">No source text has been saved for this outcome.</p>}
    </section>
  )
}

/** The normal scientific finding and original-evidence reader, without run totals. */
export function ResearchFindingView({ result, onOpenRun, mode = result.mode, primaryPaperGroups }: { result: ResearchResult; onOpenRun?: (id: string) => void; mode?: ResearchResult['mode']; primaryPaperGroups?: { paper_id: string; source_ids: string[] }[] }) {
  return <>{mode === 'surprise' && <SelectedFields selections={result.field_selections ?? []} />}<Outcome result={result} /><ResearchMemory result={result} onOpenRun={onOpenRun} /><Evidence result={result} primaryPaperGroups={primaryPaperGroups} /></>
}

export function ResearchRunView({ run, events, result, connection, onCancel, cancelling = false, onOpenRun, findingVersion, hideOutcome = false, primaryPaperGroups, notifyControl, onGoDeeper }: ResearchRunViewProps) {
  const state = states[run.status] ?? { label: 'Status unavailable', description: 'The investigation status could not be identified. Recorded material remains below.' }
  const activity = orderedEvents(events)
  const timestamps = run as { created_at?: string; updated_at?: string }
  const canCancel = onCancel && ['queued', 'running', 'waiting_retry', 'interrupted'].includes(run.status)
  const terminal = terminalStatuses.has(run.status)
  // A live run leads with the Observatory; a finished one leads with its
  // result and keeps the journey one tap away.
  const observatory = (events.length > 0 || !terminal) && <Observatory events={events} terminal={terminal} createdAt={timestamps.created_at} updatedAt={timestamps.updated_at} result={hideOutcome ? null : result} />
  const timeline = <section className="research-activity" aria-labelledby="research-activity-title"><div className="research-section-heading"><h3 id="research-activity-title">Research activity</h3><span className={`research-connection connection-${connection}`} aria-live="polite">{connectionNames[connection]}</span></div>{activity.length ? <ol className="research-timeline" aria-label="Recorded research activity" aria-live="polite" aria-relevant="additions">{activity.map((event, index) => { const step = stepKind(event.kind); return <li key={event.sequence} className={`research-step step-${step.tone}`}><span className="research-event-number" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span><p><span className="research-step-label">{step.label}</span>{event.summary}</p></li> })}</ol> : <p className="research-empty">No recorded activity yet.</p>}{run.status === 'running' && <p className="research-thinking"><span className="research-thinking-dots" aria-hidden="true"><span /><span /><span /></span>Thinking about the next step…</p>}</section>
  return (
    <section className={`research-run-view ${terminal ? 'is-finished' : 'is-live'}`} aria-labelledby="research-run-title">
      <header className="research-run-heading"><div><span className="eyebrow accent">{run.task_kind === 'reassess_finding' ? 'EVIDENCE REASSESSMENT' : modeNames[run.mode].toUpperCase()} / RESEARCH RECORD</span><h2 id="research-run-title">{run.goal || 'An open discovery'}</h2></div><span className={`research-run-state state-${run.status}`} role="status">{state.label}</span></header>
      <div className="research-run-summary"><p>{state.description}</p>{!terminalStatuses.has(run.status) && notifyControl}{canCancel && <button type="button" className="research-cancel" onClick={onCancel} disabled={cancelling}>{cancelling ? 'Requesting cancellation…' : 'Cancel investigation'}</button>}</div>
      {findingVersion && <FindingVersionNotice {...findingVersion} />}
      {!terminal && observatory}
      {!hideOutcome && <section className="research-results" aria-labelledby="research-outcome-title"><h3 className="research-sr-only" id="research-outcome-title">Research outcome</h3>{result ? <ResearchFindingView result={result} onOpenRun={onOpenRun} mode={run.mode} primaryPaperGroups={primaryPaperGroups} /> : <PendingOutcome status={run.status} />}</section>}
      {terminal && !hideOutcome && onGoDeeper && result?.finding?.outcome === 'bridge' && <GoDeeper onChoose={onGoDeeper} />}
      <details className="research-journey" open={hideOutcome || undefined}>
        <summary><span>{terminal ? 'How Polymath worked' : 'Step-by-step record'}</span><span className="research-journey-count">{activity.length} {activity.length === 1 ? 'step' : 'steps'}</span></summary>
        {terminal && observatory}
        {timeline}
      </details>
    </section>
  )
}
