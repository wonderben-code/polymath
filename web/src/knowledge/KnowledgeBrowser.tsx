import { useEffect, useId, useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
import { KnowledgeError, loadKnowledge } from './client'
import type { KnowledgeData, KnowledgeKind, KnowledgeNode } from './client'
import './knowledge.css'

export interface KnowledgeBrowserProps {
  enabled: boolean
  onOpenRun: (id: string) => void
  refreshToken?: string | null
}

const kinds: Record<KnowledgeKind, string> = { field: 'Field', source: 'Source', finding: 'Finding' }
const DIAGRAM_RECORDS_PER_KIND = 8

function shortLabel(label: string): string {
  const characters = Array.from(label)
  return characters.length > 23 ? characters.slice(0, 22).join('') + '…' : label
}

function sourceKind(value: string | null) {
  return value === 'full_text' ? 'Full-text snapshot' : value === 'abstract' ? 'Abstract snapshot' : null
}

function NodeDetail({ node, data, onSelect, onOpenRun }: { node: KnowledgeNode; data: KnowledgeData; onSelect: (id: string) => void; onOpenRun: (id: string) => void }) {
  const related = data.edges.filter((edge) => edge.source === node.id || edge.target === node.id).map((edge) => ({
    edge,
    node: data.nodes.find((item) => item.id === (edge.source === node.id ? edge.target : edge.source))!,
  }))
  return <section className="knowledge-node-detail" aria-label="Selected knowledge record">
    <span className="eyebrow accent">{kinds[node.kind]}</span>
    <h4>{node.label}</h4>
    {sourceKind(node.content_kind) && <p>{sourceKind(node.content_kind)}</p>}
    {node.source_id && <p className="knowledge-source-id">{node.source_id}</p>}
    <p>{node.kind === 'field' ? 'A field named in saved research. Its links show recorded research relationships.' : node.kind === 'source' ? 'A source version saved during an investigation. Open its research record to inspect the evidence.' : 'A saved research finding. Its links preserve the recorded mapping and source associations.'}</p>
    {related.length > 0 && <><h5>Recorded relationships</h5><ul className="knowledge-related" aria-label="Connected records">{related.map(({ edge, node: target }) => <li key={edge.id}><span>{edge.kind === 'supported_by' ? 'Evidence association' : edge.kind === 'selected_field' ? 'Research direction' : 'Proposed mapping'}</span><button type="button" onClick={() => onSelect(target.id)}>{target.label}</button></li>)}</ul></>}
    {node.run_id && <button type="button" className="knowledge-open" onClick={() => onOpenRun(node.run_id!)}>Open research record</button>}
  </section>
}

function MapView({ data, selected, onSelect, onOpenRun }: { data: KnowledgeData; selected: string | null; onSelect: (id: string) => void; onOpenRun: (id: string) => void }) {
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
          {columns.map((kind, index) => <text key={kind} x={85 + index * 300} y="28" className="knowledge-map-column">{kinds[kind]}s</text>)}
          <g aria-hidden="true">{data.edges.map((edge) => {
            const start = locations.get(edge.source), end = locations.get(edge.target)
            return start && end ? <line key={edge.id} x1={start.x} y1={start.y} x2={end.x} y2={end.y} className={`knowledge-edge edge-${edge.kind}`} /> : null
          })}</g>
          {visible.map((node) => {
            const point = locations.get(node.id)!
            return <g key={node.id} role="button" tabIndex={0} aria-label={`Select map node: ${kinds[node.kind]} ${node.label}`} aria-pressed={selected === node.id} onClick={() => onSelect(node.id)} onKeyDown={(event) => keyboard(event, node.id)} className={`knowledge-map-node node-${node.kind}`} transform={`translate(${point.x} ${point.y})`}>
              <title>{node.label}</title><circle r="12" /><text x="20" y="4">{shortLabel(node.label)}</text>
            </g>
          })}
        </svg>
      </div>
      {selectedNode ? <NodeDetail node={selectedNode} data={data} onSelect={onSelect} onOpenRun={onOpenRun} /> : <p className="knowledge-select-note">Choose a map node or a record below to inspect its saved relationships.</p>}
    </div>
    <p className="knowledge-map-note">The diagram can be scrolled horizontally. {visible.length < data.nodes.length ? `It shows ${visible.length} of ${data.nodes.length} records. ` : ''}All returned records are available in the keyboard-accessible list below.</p>
    <ul className="knowledge-node-list" aria-label="Saved knowledge records">{data.nodes.map((node) => <li key={node.id}><button type="button" aria-pressed={selected === node.id} onClick={() => onSelect(node.id)}><span>{kinds[node.kind]}</span>{' '}{node.label}</button></li>)}</ul>
  </>
}

export function KnowledgeBrowser({ enabled, onOpenRun, refreshToken }: KnowledgeBrowserProps) {
  const inputId = useId()
  const [draft, setDraft] = useState('')
  const [query, setQuery] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [data, setData] = useState<KnowledgeData | null>(null)
  const [phase, setPhase] = useState<'idle' | 'loading' | 'ready' | 'error' | 'expired'>('idle')
  const [selected, setSelected] = useState<string | null>(null)
  const sessionBlocked = useRef(false)

  useEffect(() => {
    setData(null)
    setSelected(null)
    if (!enabled) {
      sessionBlocked.current = false
      setDraft('')
      setQuery('')
      setPhase('idle')
      return
    }
    if (sessionBlocked.current) { setPhase('expired'); return }
    const controller = new AbortController()
    let current = true
    const timer = window.setTimeout(() => {
      controller.abort()
      if (current) { setData(null); setPhase('error') }
    }, 10000)
    setPhase('loading')
    loadKnowledge(query, controller.signal).then((found) => {
      if (current && !controller.signal.aborted) { setData(found); setPhase('ready') }
    }).catch((error: unknown) => {
      if (current && !controller.signal.aborted) {
        setData(null)
        setSelected(null)
        if (error instanceof KnowledgeError && error.code === 'session_expired') {
          sessionBlocked.current = true
          setDraft('')
          setQuery('')
          setPhase('expired')
        } else setPhase('error')
      }
    }).finally(() => window.clearTimeout(timer))
    return () => { current = false; controller.abort(); window.clearTimeout(timer) }
  }, [enabled, query, refresh, refreshToken])

  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!enabled || phase === 'loading' || phase === 'expired') return
    setQuery(draft.trim())
    setRefresh((version) => version + 1)
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
        <div className="knowledge-search-results"><h3>Matching records</h3>{data.items.length ? <ul aria-label="Matching saved research">{data.items.map((item) => <li key={item.id}><div><span className="knowledge-record-kind">{item.type === 'source' ? sourceKind(item.content_kind) || 'Source' : 'Finding'}</span><h4>{item.title}</h4>{item.summary && <p>{item.summary}</p>}</div><button type="button" onClick={() => onOpenRun(item.run_id)}>Open investigation<span className="knowledge-sr-only">: {item.title}</span></button></li>)}</ul> : <p className="knowledge-message">No matching record summaries were returned. Saved relationships remain available below.</p>}</div>
        {data.nodes.length > 0 && <div className="knowledge-graph-section"><h3>Saved relationships</h3><p className="knowledge-map-note">Field links describe a saved mapping or model-selected research direction. Source links identify evidence associated with a finding.</p><MapView data={data} selected={selected} onSelect={setSelected} onOpenRun={onOpenRun} /></div>}
      </>}
    </>}
  </section>
}
