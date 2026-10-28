import { useCallback, useEffect, useId, useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
import { KnowledgeError, loadKnowledge } from './client'
import type { KnowledgeData, KnowledgeKind, KnowledgeNode } from './client'
import { WatchWorkspace } from '../ingestion/WatchWorkspace'
import { SourceCoverageNotice } from '../research/SourceCoverageNotice'
import { Bibliography } from '../research/bibliography'
import type { SourceCoverage } from '../research/sourceCoverage'
import { sourceIdentityCount } from '../research/sourcePresentation'
import './knowledge.css'

export interface KnowledgeBrowserProps {
  enabled: boolean
  onOpenRun: (id: string) => void
  refreshToken?: string | null
  canInvestigate?: boolean
  investigating?: boolean
  onInvestigateBridges?: (resultIds: string[]) => void | Promise<void>
  csrfToken?: string | null
  onSessionExpired?: () => void
  onAssessmentApplied?: () => void
  onAssessmentBusyChange?: (busy: boolean) => void
  onOpenFinding?: (id: string) => void
}

const kinds: Record<KnowledgeKind, string> = { field: 'Field', source: 'Source', finding: 'Finding' }
const DIAGRAM_RECORDS_PER_KIND = 8

function shortLabel(label: string): string {
  const characters = Array.from(label)
  return characters.length > 23 ? characters.slice(0, 22).join('') + '…' : label
}

function sourceKind(value: string | null, coverage?: SourceCoverage) {
  return coverage ? 'Full text excerpt' : value === 'full_text' ? 'Full-text snapshot' : value === 'abstract' ? 'Abstract snapshot' : null
}

function NodeDetail({ node, data, onSelect, onOpenRun, onOpenSource, onOpenFinding }: { node: KnowledgeNode; data: KnowledgeData; onSelect: (id: string) => void; onOpenRun: (id: string) => void; onOpenSource: (watchId: string, sourceId: string) => void; onOpenFinding?: (id: string) => void }) {
  const related = data.edges.filter((edge) => edge.source === node.id || edge.target === node.id).map((edge) => ({
    edge,
    node: data.nodes.find((item) => item.id === (edge.source === node.id ? edge.target : edge.source))!,
  }))
  return <section className="knowledge-node-detail" aria-label="Selected knowledge record">
    <span className="eyebrow accent">{kinds[node.kind]}</span>
    <h4>{node.label}</h4>
    {sourceKind(node.content_kind, node.coverage) && <p>{sourceKind(node.content_kind, node.coverage)}</p>}
    {node.source_id && <p className="knowledge-source-id">{node.source_id}</p>}
    {node.kind === 'source' && <SourceCoverageNotice coverage={node.coverage} contentKind={node.content_kind ?? undefined} />}
    {node.coverage && <Bibliography value={node.bibliography} />}
    {node.assessment && (!node.assessment.is_current || !node.assessment.is_supported) && <p className="knowledge-limit">{node.assessment.state === 'unsupported' ? 'Unsupported by the latest model assessment.' : 'Historical finding; a newer version is current.'} Saved text remains available. New source-based generation requires the current supported version.</p>}
    {node.assessment_status === 'unapplied_reassessment' && <p className="knowledge-limit">A saved source copy from an unapplied reassessment. It does not establish a current finding.</p>}
    <p>{node.kind === 'field' ? 'A field named in saved research. Its links show recorded research relationships.' : node.kind === 'source' ? node.task_kind === 'ingest_literature' ? 'An unassessed source version collected by a literature watch. Read its saved text before starting a separate investigation.' : 'A source version saved during an investigation. Open its research record to inspect the evidence.' : 'A saved research finding. Its links preserve the recorded mapping and source associations.'}</p>
    {related.length > 0 && <><h5>Recorded relationships</h5><ul className="knowledge-related" aria-label="Connected records">{related.map(({ edge, node: target }) => <li key={edge.id}><span>{edge.kind === 'supported_by' ? 'Evidence association' : edge.kind === 'selected_field' ? 'Research direction' : edge.kind === 'investigated_from' ? edge.source === node.id ? 'Earlier bridge investigated' : 'Follow-up investigation' : 'Proposed mapping'}</span><button type="button" onClick={() => onSelect(target.id)}>{target.label}</button></li>)}</ul></>}
    {node.task_kind === 'ingest_literature' && node.watch_id && node.source_version_id ? <button type="button" className="knowledge-open" onClick={() => onOpenSource(node.watch_id!, node.source_version_id!)}>Read unassessed source</button> : node.task_kind === 'reassess_finding' ? (node.assessment_result_id || node.kind === 'finding' && node.result_id) && onOpenFinding && <button type="button" className="knowledge-open" onClick={() => onOpenFinding((node.assessment_result_id || node.result_id)!)}>Open saved finding version</button> : node.run_id && <button type="button" className="knowledge-open" onClick={() => onOpenRun(node.run_id!)}>Open research record</button>}
  </section>
}

function MapView({ data, selected, onSelect, onOpenRun, onOpenSource, onOpenFinding }: { data: KnowledgeData; selected: string | null; onSelect: (id: string) => void; onOpenRun: (id: string) => void; onOpenSource: (watchId: string, sourceId: string) => void; onOpenFinding?: (id: string) => void }) {
  const columns: KnowledgeKind[] = ['field', 'finding', 'source']
  const visible = columns.flatMap((kind) => data.nodes.filter((node) => node.kind === kind).slice(0, DIAGRAM_RECORDS_PER_KIND))
  const locations = new Map<string, { x: number; y: number }>()
  columns.forEach((kind, column) => {
    const nodes = visible.filter((node) => node.kind === kind)
    nodes.forEach((node, index) => locations.set(node.id, { x: 85 + column * 300, y: 55 + (index + 1) * 345 / (nodes.length + 1) }))
  })
  function keyboard(event: KeyboardEvent<SVGGElement>, id: string) {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(id) }
  }
  const selectedNode = data.nodes.find((node) => node.id === selected)
  return <>
    <div className="knowledge-map-layout">
      <div className="knowledge-map">
        <svg viewBox="0 0 900 450" role="group" aria-label="Saved research relationship map">
          <title>Fields, findings, and saved source versions</title>
          {columns.map((kind, index) => <text key={kind} x={85 + index * 300} y="28" className="knowledge-map-column">{kind === 'source' ? 'Source versions' : `${kinds[kind]}s`}</text>)}
          <g aria-hidden="true">{data.edges.map((edge) => {
            const start = locations.get(edge.source), end = locations.get(edge.target)
            if (!start || !end) return null
            return edge.kind === 'investigated_from'
              ? <path key={edge.id} d={`M ${start.x} ${start.y} Q ${start.x - 100} ${(start.y + end.y) / 2} ${end.x} ${end.y}`} fill="none" className="knowledge-edge edge-investigated_from" />
              : <line key={edge.id} x1={start.x} y1={start.y} x2={end.x} y2={end.y} className={`knowledge-edge edge-${edge.kind}`} />
          })}</g>
          {visible.map((node) => {
            const point = locations.get(node.id)!
            return <g key={node.id} role="button" tabIndex={0} aria-label={`Select map node: ${kinds[node.kind]} ${node.label}`} aria-pressed={selected === node.id} onClick={() => onSelect(node.id)} onKeyDown={(event) => keyboard(event, node.id)} className={`knowledge-map-node node-${node.kind}`} transform={`translate(${point.x} ${point.y})`}>
              <title>{node.label}</title><circle r="12" /><text x="20" y="4">{shortLabel(node.label)}</text>
            </g>
          })}
        </svg>
      </div>
      {selectedNode ? <NodeDetail node={selectedNode} data={data} onSelect={onSelect} onOpenRun={onOpenRun} onOpenSource={onOpenSource} onOpenFinding={onOpenFinding} /> : <p className="knowledge-select-note">Choose a map node or a record below to inspect its saved relationships.</p>}
    </div>
    <p className="knowledge-map-note">The diagram can be scrolled horizontally. {visible.length < data.nodes.length ? `It shows ${visible.length} of ${data.nodes.length} records. ` : ''}All returned records are available in the keyboard-accessible list below.</p>
    <ul className="knowledge-node-list" aria-label="Saved knowledge records">{data.nodes.map((node) => <li key={node.id}><button type="button" aria-pressed={selected === node.id} onClick={() => onSelect(node.id)}><span>{kinds[node.kind]}</span>{' '}{node.label}</button></li>)}</ul>
  </>
}

export function KnowledgeBrowser({ enabled, onOpenRun, refreshToken, canInvestigate = false, investigating = false, onInvestigateBridges, csrfToken = null, onSessionExpired, onOpenFinding, onAssessmentApplied, onAssessmentBusyChange }: KnowledgeBrowserProps) {
  const inputId = useId()
  const [draft, setDraft] = useState('')
  const [query, setQuery] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [data, setData] = useState<KnowledgeData | null>(null)
  const [phase, setPhase] = useState<'idle' | 'loading' | 'ready' | 'error' | 'expired'>('idle')
  const [selected, setSelected] = useState<string | null>(null)
  const [parentResultIds, setParentResultIds] = useState<string[]>([])
  const [openWatchSource, setOpenWatchSource] = useState<{ watchId: string; sourceId: string; requestId: number } | null>(null)
  const sessionBlocked = useRef(false)
  const lastCsrf = useRef(csrfToken)
  const expireSession = useCallback(() => {
    sessionBlocked.current = true
    setData(null); setSelected(null); setParentResultIds([]); setOpenWatchSource(null); setDraft(''); setQuery(''); setPhase('expired')
    onSessionExpired?.()
  }, [onSessionExpired])
  const refreshCollectedSources = useCallback(() => setRefresh((previous) => previous + 1), [])
  const assessmentChanged = useCallback(() => { setRefresh((previous) => previous + 1); onAssessmentApplied?.() }, [onAssessmentApplied])

  useEffect(() => {
    const changedSession = lastCsrf.current !== csrfToken
    if (changedSession) { lastCsrf.current = csrfToken; sessionBlocked.current = false; setDraft(''); setQuery(''); setOpenWatchSource(null) }
    setData(null)
    setSelected(null)
    setParentResultIds([])
    if (!enabled) {
      sessionBlocked.current = false
      setDraft('')
      setQuery('')
      setOpenWatchSource(null)
      setPhase('idle')
      return
    }
    if (sessionBlocked.current) { setPhase('expired'); return }
    const controller = new AbortController()
    let current = true
    const timer = window.setTimeout(() => {
      controller.abort()
      if (current && !sessionBlocked.current) { setData(null); setPhase('error') }
    }, 10000)
    setPhase('loading')
    loadKnowledge(changedSession ? '' : query, controller.signal).then((found) => {
      if (current && !controller.signal.aborted && !sessionBlocked.current) { setData(found); setPhase('ready') }
    }).catch((error: unknown) => {
      if (current && !controller.signal.aborted && !sessionBlocked.current) {
        setData(null)
        setSelected(null)
        if (error instanceof KnowledgeError && error.code === 'session_expired') {
          expireSession()
        } else setPhase('error')
      }
    }).finally(() => window.clearTimeout(timer))
    return () => { current = false; controller.abort(); window.clearTimeout(timer) }
  }, [enabled, query, refresh, refreshToken, expireSession, csrfToken])

  useEffect(() => {
    if (!canInvestigate) setParentResultIds([])
  }, [canInvestigate])

  const bridges = data ? [...new Map(data.nodes.filter((node) => node.kind === 'finding' && node.finding_outcome === 'bridge' && node.result_id !== null).map((node) => [node.result_id!, node])).values()] : []
  const sourceRecords = data?.nodes.flatMap((node) => node.kind === 'source' && node.source_id && node.content_kind ? [{ source_id: node.source_id, content_kind: node.content_kind, coverage: node.coverage, bibliography: node.bibliography }] : []) ?? []
  const hasExcerpts = sourceRecords.some((source) => source.coverage), paperCount = sourceIdentityCount(sourceRecords)
  const canChoose = enabled && phase === 'ready' && canInvestigate && !investigating && !!onInvestigateBridges

  function chooseBridge(resultId: string) {
    if (!canChoose || !bridges.some((node) => node.result_id === resultId && node.assessment?.is_current && node.assessment.is_supported)) return
    setParentResultIds((previous) => previous.includes(resultId) ? previous.filter((id) => id !== resultId) : previous.length < 4 ? [...previous, resultId] : previous)
  }

  function investigateBridges() {
    if (!canChoose || parentResultIds.length < 2 || parentResultIds.length > 4 || !parentResultIds.every((id) => bridges.some((node) => node.result_id === id && node.assessment?.is_current && node.assessment.is_supported))) return
    void onInvestigateBridges!(parentResultIds)
    setParentResultIds([])
  }

  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!enabled || phase === 'loading' || phase === 'expired') return
    setQuery(draft.trim())
    setRefresh((version) => version + 1)
  }

  function readWatchSource(watchId: string, sourceId: string) {
    if (!enabled || phase !== 'ready' || !csrfToken) return
    setOpenWatchSource((previous) => ({ watchId, sourceId, requestId: (previous?.requestId ?? 0) + 1 }))
  }

  return <section className="knowledge-browser" aria-labelledby={`${inputId}-title`}>
    <header className="knowledge-heading"><div><span className="eyebrow accent">YOUR RESEARCH LIBRARY</span><h2 id={`${inputId}-title`}>Follow the connections.</h2><p>Browse fields, saved findings, and the source versions behind them. Relationships record the research; they do not establish scientific truth.</p></div></header>
    <form className="knowledge-search" onSubmit={search}>
      <label htmlFor={inputId}>Search saved research</label>
      <div><input id={inputId} type="search" maxLength={200} value={draft} disabled={!enabled || phase === 'expired'} onChange={(event) => setDraft(event.target.value)} placeholder="A field, paper, or finding…" /><button type="submit" disabled={!enabled || phase === 'loading' || phase === 'expired'}>Search library</button></div>
    </form>
    {!enabled && <p className="knowledge-message">Connect to the research workspace to browse this browser session’s saved knowledge.</p>}
    {enabled && phase === 'loading' && <p className="knowledge-message" role="status">Loading saved knowledge…</p>}
    {enabled && phase === 'expired' && <p className="knowledge-message" role="alert">Your research session has expired or changed. Reload saved research in the workspace before browsing the library.</p>}
    {enabled && phase === 'error' && <div className="knowledge-message" role="alert"><p>Saved knowledge could not be loaded. No records are shown until the connection is restored.</p><button type="button" onClick={() => setRefresh((version) => version + 1)}>Retry library connection</button></div>}
    {enabled && phase === 'ready' && data && <>
      <p className="knowledge-coverage">Current browser session only · {data.findingsConsidered} saved {data.findingsConsidered === 1 ? 'finding' : 'findings'} considered</p>
      {data.truncated && <p className="knowledge-limit" role="status">This is a bounded view of your library. Narrow the search to inspect more specific records.</p>}
      {data.items.length === 0 && data.nodes.length === 0 ? <p className="knowledge-message">No saved knowledge matches this search. Completed investigations will build the library.</p> : <>
        <div className="knowledge-search-results"><h3>Matching records</h3>{data.items.length ? <ul aria-label="Matching saved research">{data.items.map((item) => <li key={item.id}><div><span className="knowledge-record-kind">{item.coverage ? 'Full text excerpt' : item.task_kind === 'ingest_literature' ? 'Unassessed source version' : item.type === 'source' ? sourceKind(item.content_kind, item.coverage) || 'Source' : 'Finding'}</span><h4>{item.title}</h4>{item.summary && <p>{item.summary}</p>}{item.coverage && <p className="knowledge-map-note">Saved characters {item.coverage.start}–{item.coverage.end} of {item.coverage.total} (end excluded) · arXiv {item.coverage.arxiv_id}v{item.coverage.arxiv_version}</p>}</div>{item.task_kind === 'ingest_literature' && item.watch_id && item.source_version_id ? <button type="button" aria-label={`Read unassessed source: ${item.title}`} onClick={() => readWatchSource(item.watch_id!, item.source_version_id!)} disabled={!csrfToken}>Read unassessed source<span className="knowledge-sr-only">: {item.title}</span></button> : item.task_kind === 'reassess_finding' ? (item.assessment_result_id || item.type === 'finding' && item.result_id) && onOpenFinding ? <button type="button" aria-label={`Open saved finding version: ${item.title}`} onClick={() => onOpenFinding((item.assessment_result_id || item.result_id)!)}>Open saved finding version</button> : <span className="knowledge-record-kind">Unapplied reassessment material</span> : <button type="button" onClick={() => onOpenRun(item.run_id)}>Open investigation<span className="knowledge-sr-only">: {item.title}</span></button>}</li>)}</ul> : <p className="knowledge-message">No matching record summaries were returned. Saved relationships remain available below.</p>}</div>
        {data.nodes.length > 0 && <div className="knowledge-graph-section"><h3>Saved relationships</h3>{hasExcerpts && <p className="source-paper-count">{sourceRecords.length} saved source versions · {paperCount} paper {paperCount === 1 ? 'identity' : 'identities'}. Multiple excerpts of one paper are separate saved windows, not independent papers.</p>}<p className="knowledge-map-note">Field links describe a saved mapping or model-selected research direction. Source links identify evidence associated with a finding. Curved links show earlier bridges investigated by a follow-up; they do not provide independent support. Collected sources without finding links remain unassessed.</p><MapView data={data} selected={selected} onSelect={setSelected} onOpenRun={onOpenRun} onOpenSource={readWatchSource} onOpenFinding={onOpenFinding} /></div>}
        {bridges.length > 0 && <fieldset className="knowledge-bridge-selection">
          <legend>Investigate connections between bridges</legend>
          <p>Choose two to four saved bridges. Polymath will revisit their original evidence and test whether a shared structure holds up.</p>
          <div className="knowledge-bridge-choices">{bridges.map((node) => <label key={node.result_id}><input type="checkbox" checked={parentResultIds.includes(node.result_id!)} onChange={() => chooseBridge(node.result_id!)} disabled={!canChoose || !node.assessment?.is_current || !node.assessment.is_supported || parentResultIds.length >= 4 && !parentResultIds.includes(node.result_id!)} aria-label={`Use bridge: ${node.label}`} /><span>{node.label}</span></label>)}</div>
          <div className="knowledge-bridge-actions"><p role="status">{investigating ? 'Queuing the follow-up investigation…' : canChoose ? `${parentResultIds.length} of 4 bridges selected. ${parentResultIds.length < 2 ? 'Select at least two to continue.' : 'Ready to investigate the shared structure.'}` : 'New investigations are unavailable. You can still inspect saved research.'}</p><button type="button" onClick={investigateBridges} disabled={!canChoose || parentResultIds.length < 2}>{investigating ? 'Queuing investigation…' : 'Investigate these bridges'}</button></div>
        </fieldset>}
      </>}
    </>}
    {csrfToken !== null && <WatchWorkspace enabled={enabled && phase !== 'expired'} csrfToken={csrfToken} findings={data ? [...new Map(data.nodes.filter((node) => node.kind === 'finding' && node.result_id && (node.finding_outcome === 'bridge' || node.finding_outcome === 'answer') && node.assessment?.is_current && node.assessment.is_supported).map((node) => [node.result_id!, { id: node.result_id!, title: node.label }])).values()] : []} onSessionExpired={expireSession} openSource={openWatchSource} onSourcesChanged={refreshCollectedSources} canReassess={canInvestigate && !investigating} onAssessmentApplied={assessmentChanged} onAssessmentBusyChange={onAssessmentBusyChange} onOpenFinding={onOpenFinding} />}
  </section>
}
