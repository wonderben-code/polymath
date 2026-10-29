import { useEffect, useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
import { loadPublicLibrary, loadPublicMap, SharingError } from './client'
import type { PublicLibrary, PublicMap, PublicMapNode } from './types'
import { PublicShell } from './PublicShell'
import { ScholarlyLinks, sharingDate, SnapshotStatus } from './PublicSnapshot'

function short(text: string) { const characters = Array.from(text); return characters.length > 24 ? characters.slice(0, 23).join('') + '…' : text }

function PublicMapView({ map }: { map: PublicMap }) {
  const [selected, setSelected] = useState<string | null>(null)
  useEffect(() => setSelected(null), [map])
  const kinds = ['field', 'finding', 'paper'] as const, locations = new Map<string, { x: number; y: number }>()
  const visible = kinds.flatMap((kind) => map.nodes.filter((node) => node.kind === kind).slice(0, 8))
  kinds.forEach((kind, column) => visible.filter((node) => node.kind === kind).forEach((node, index, records) => locations.set(node.id, { x: 55 + column * 290, y: 55 + (index + 1) * 320 / (records.length + 1) })))
  const node = map.nodes.find((item) => item.id === selected)
  const connected = node ? map.edges.filter((edge) => edge.source === node.id || edge.target === node.id).map((edge) => ({ edge, node: map.nodes.find((item) => item.id === (edge.source === node.id ? edge.target : edge.source))! })) : []
  function keyboard(event: KeyboardEvent<SVGGElement>, id: string) { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setSelected(id) } }
  function nodeLink(item: PublicMapNode) { return item.public_path && <a href={item.public_path}>Read this shared finding</a> }
  return <section className="public-map" aria-label="Public knowledge relationship map"><h2 className="public-section-title">A map of shared knowledge</h2><p className="public-map-note">Only explicitly shared, currently supported snapshots appear here. Field links describe model-proposed relationships. Citation links are saved evidence associations; they do not independently verify a claim.</p>
    {map.truncated && <p className="sharing-warning">This is a bounded map, limited to {map.coverage.max_findings} findings, {map.coverage.max_nodes} nodes, and {map.coverage.max_edges} edges. It does not represent all shared knowledge.</p>}
    {map.nodes.length ? <><div className="public-map-layout"><div className="public-map-scroll"><svg viewBox="0 0 900 420" role="group" aria-label="Shared fields, findings, and papers"><title>Public field, finding, and paper relationships</title>{kinds.map((kind, index) => <text key={kind} className="public-map-column" x={55 + index * 290} y="30">{kind === 'paper' ? 'Cited papers' : `${kind}s`}</text>)}<g aria-hidden="true">{map.edges.map((edge) => { const from = locations.get(edge.source), to = locations.get(edge.target); return from && to && <line key={edge.id} x1={from.x} y1={from.y} x2={to.x} y2={to.y} /> })}</g>{visible.map((item) => { const point = locations.get(item.id)!; return <g key={item.id} role="button" tabIndex={0} aria-pressed={item.id === selected} aria-label={`Select public map ${item.kind}: ${item.label}`} onClick={() => setSelected(item.id)} onKeyDown={(event) => keyboard(event, item.id)} transform={`translate(${point.x} ${point.y})`}><title>{item.label}</title><circle r="10" /><text x="18" y="4">{short(item.label)}</text></g> })}</svg></div>
      {node ? <section className="public-map-detail" aria-label="Selected public knowledge record"><span className="eyebrow accent">{node.kind}</span><h3>{node.label}</h3>{nodeLink(node)}<ScholarlyLinks links={node.links} />{connected.length > 0 && <><h4>Public relationships</h4><ul>{connected.map(({ edge, node: target }) => <li key={edge.id}><p className="public-map-note">{edge.kind === 'cites' ? 'Saved citation association' : 'Model-proposed field relationship'}</p><button className="text-button" type="button" onClick={() => setSelected(target.id)}>{target.label}</button></li>)}</ul></>}</section> : <p className="public-map-note">Select a map node or a record below to inspect its public relationships.</p>}</div><p className="public-map-note">The diagram can scroll horizontally. It shows {visible.length} of {map.nodes.length} returned records; every returned record is available in the list below.</p><ul className="public-map-records" aria-label="Public knowledge map records">{map.nodes.map((item) => <li key={item.id}><button type="button" aria-pressed={item.id === selected} onClick={() => setSelected(item.id)}>{item.kind} · {item.label}</button></li>)}</ul></> : <p className="public-map-note">No public relationships match this search.</p>}
  </section>
}

export function PublicLibraryPage() {
  const [draft, setDraft] = useState(''), [query, setQuery] = useState(''), [refresh, setRefresh] = useState(0)
  const [library, setLibrary] = useState<PublicLibrary | null>(null), [map, setMap] = useState<PublicMap | null>(null), [phase, setPhase] = useState<'loading' | 'ready' | 'error' | 'closed'>('loading'), [moreBusy, setMoreBusy] = useState(false), [moreError, setMoreError] = useState('')
  const generation = useRef(0), moreRequest = useRef<AbortController | null>(null), moreLocked = useRef(false)
  useEffect(() => {
    const scope = ++generation.current, controller = new AbortController(); let active = true
    moreRequest.current?.abort(); moreLocked.current = false; setMoreBusy(false); setMoreError(''); setLibrary(null); setMap(null); setPhase('loading')
    const timeout = window.setTimeout(() => { controller.abort(); if (active && scope === generation.current) setPhase('error') }, 10000)
    Promise.all([loadPublicLibrary(query, controller.signal), loadPublicMap(query, controller.signal)]).then(([found, graph]) => { if (active && scope === generation.current && !controller.signal.aborted) { setLibrary(found); setMap(graph); setPhase('ready') } }).catch((error: unknown) => { if (active && scope === generation.current && !controller.signal.aborted) { setLibrary(null); setMap(null); setPhase(error instanceof SharingError && error.code === 'sharing_not_found' ? 'closed' : 'error') } }).finally(() => window.clearTimeout(timeout))
    return () => { active = false; generation.current++; controller.abort(); moreRequest.current?.abort(); window.clearTimeout(timeout) }
  }, [query, refresh])
  useEffect(() => {
    if (!library?.items.length) return
    const wait = Math.min(...library.items.map((item) => Date.parse(item.expires_at))) - Date.now()
    const timer = window.setTimeout(() => { setLibrary(null); setMap(null); setRefresh((value) => value + 1) }, Math.max(0, wait))
    return () => window.clearTimeout(timer)
  }, [library])
  function search(event: FormEvent<HTMLFormElement>) { event.preventDefault(); setQuery(draft.trim()); setRefresh((value) => value + 1) }
  async function more() {
    if (!library?.next_cursor || moreLocked.current) return
    moreLocked.current = true; setMoreBusy(true); setMoreError('')
    const scope = generation.current, controller = new AbortController(); moreRequest.current = controller
    const timer = window.setTimeout(() => controller.abort(), 10000)
    try { const found = await loadPublicLibrary(query, controller.signal, library.next_cursor); if (scope === generation.current && !controller.signal.aborted) setLibrary((old) => old && ({ items: [...old.items, ...found.items.filter((item) => !old.items.some((record) => record.public_id === item.public_id))], next_cursor: found.next_cursor })) }
    catch { if (scope === generation.current) setMoreError('Earlier public findings could not be loaded. Try reading this page again.') }
    finally { window.clearTimeout(timer); if (scope === generation.current) { moreLocked.current = false; setMoreBusy(false) } }
  }
  return <PublicShell><div className="public-intro"><span className="eyebrow accent">THE PUBLIC LIBRARY</span><h1>A wider field of shared thought.</h1><p>Explore findings that their owners chose to share. Read the complete model assessment, its uncertainty, and short excerpts from its saved evidence.</p><p className="public-notice">This library contains active, current shared snapshots. Public reading does not start an investigation, retrieve papers, or require a private browser session. Each snapshot expires and can be withdrawn.</p></div><form className="public-controls" onSubmit={search}><label htmlFor="public-search">Search public finding text<input id="public-search" value={draft} maxLength={200} onChange={(event) => setDraft(event.target.value)} placeholder="A concept, a field, an open question…" /></label><button type="submit" disabled={phase === 'loading'}>Search public knowledge</button><button type="button" disabled={phase === 'loading'} onClick={() => setRefresh((value) => value + 1)}>Reload public library</button></form>
    {phase === 'loading' && <p role="status">Loading explicitly shared knowledge…</p>}{phase === 'error' && <p role="alert" className="sharing-warning">The public library could not be loaded. Check the connection and reload it.</p>}{phase === 'closed' && <div className="public-empty" role="status"><h2 className="public-section-title">The public library is not open yet.</h2><p>Findings appear here only after their owners choose to share them. Nothing has been published from this site so far.</p></div>}{phase === 'ready' && library && <>{library.items.length ? <ul className="public-library-list" aria-label="Public finding snapshots">{library.items.map((item) => <li className="public-library-card" key={item.public_id}><span className="eyebrow accent">{item.mode === 'ask' ? 'ASK · ANSWER' : item.mode === 'surprise' ? 'SURPRISE · CONNECTION' : 'EXPLORE · CONNECTION'}</span><h2><a href={`/shared/${item.public_id}`}>{item.title}</a></h2><SnapshotStatus status={item.status} /><p>{item.summary}</p>{item.fields.length > 0 && <p>{item.fields.join(' · ')}</p>}<small>Shared {sharingDate(item.created_at)}<br />Available until {sharingDate(item.expires_at)}</small></li>)}</ul> : <div className="public-empty"><h2 className="public-section-title">No shared findings match this search.</h2><p>Only explicitly shared, current supported findings appear here. An empty result is not evidence that a scientific connection does not exist.</p></div>}{library.next_cursor && <button className="public-reload" type="button" disabled={moreBusy} onClick={() => void more()}>{moreBusy ? 'Loading earlier findings…' : 'Load earlier shared findings'}</button>}{moreError && <p role="alert">{moreError}</p>}{map && <PublicMapView map={map} />}</>}
  </PublicShell>
}
