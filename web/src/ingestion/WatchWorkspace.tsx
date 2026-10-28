import { useCallback, useEffect, useId, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Bibliography } from '../research/bibliography'
import { AssessmentWorkspace } from '../assessments/AssessmentWorkspace'
import type { AssessmentRecord } from '../assessments/types'
import { SourceCoverageNotice } from '../research/SourceCoverageNotice'
import { changeWatch, checkWatch, createWatch, getWatch, getWatchSource, IngestionError, listWatches, listWatchSources, listWatchTicks, loadWatchCapabilities } from './client'
import type { LiteratureWatch, Page, Provider, WatchCapabilities, WatchSource, WatchSourceDetail, WatchTick } from './client'
import './ingestion.css'

interface FindingChoice { id: string; title: string }
export interface WatchWorkspaceProps {
  enabled: boolean
  csrfToken: string | null
  findings?: FindingChoice[]
  onSessionExpired: () => void
  openSource?: { watchId: string; sourceId: string; requestId: number } | null
  onSourcesChanged?: () => void
  canReassess?: boolean
  onAssessmentApplied?: () => void
  onAssessmentBusyChange?: (busy: boolean) => void
  onOpenFinding?: (id: string) => void
}
const PROVIDER_NAMES: Record<Provider, string> = { openalex: 'OpenAlex', arxiv: 'arXiv' }
const INTERVALS = [[21600, 'Every 6 hours'], [43200, 'Every 12 hours'], [86400, 'Every day'], [259200, 'Every 3 days'], [604800, 'Every 7 days']] as const
const EMPTY_SOURCES: Page<WatchSource> = { items: [], next_cursor: null }
const EMPTY_TICKS: Page<WatchTick> = { items: [], next_cursor: null }
function when(value: string | null): string { return value ? new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : 'Not checked yet' }
function status(value: string): string { return value.replaceAll('_', ' ') }
function assessmentLabel(value?: WatchSource['assessment_status']): string { return value === 'already_cited' ? 'Already cited in the finding' : value === 'assessed' ? 'Included in an applied model assessment' : 'Awaiting model assessment' }
function merge<T extends { id: string }>(newItems: T[], oldItems: T[]): T[] { const fresh = new Set(newItems.map((item) => item.id)); return [...newItems, ...oldItems.filter((item) => !fresh.has(item.id))] }
function message(error: unknown): string {
  if (!(error instanceof IngestionError)) return 'The literature service could not be reached. Refresh the saved watches to check their current state.'
  const messages: Record<string, string> = {
    ingestion_execution_disabled: 'Free-provider reading is currently disabled. Existing saved sources remain available.',
    watch_check_throttled: 'This watch was checked recently. Wait before requesting another check; its scheduled checks remain in place.',
    ingestion_watch_limit: 'This browser session has reached its watch limit.',
    ingestion_watch_source_limit: 'This watch has reached its saved source-version limit. Its provider position is preserved. Narrow the query in a new watch to collect a more focused set.',
    ingestion_queue_full: 'The reading queue is full. Wait for an existing check to finish.',
    ingestion_active_run_limit: 'This browser already has the maximum number of active checks. Wait for one to finish.',
    watch_paused: 'This watch is paused. Resume it before requesting a check.',
    watch_expired: 'This watch has expired with its browser session.',
    watch_not_found: 'This watch or source is unavailable in the current browser session.',
    invalid_watch_request: 'Check the watch name, search query, and reading interval.',
    invalid_response: 'The saved watch response could not be verified. Refresh before changing a watch.',
  }
  return messages[error.code] || 'The literature service could not be reached. Refresh the saved watches to check their current state.'
}

function TickCard({ tick }: { tick: WatchTick }) {
  const diagnostics = Object.entries(tick.diagnostics) as [Provider, NonNullable<WatchTick['diagnostics'][Provider]>][]
  const partial = diagnostics.some(([, detail]) => detail.status === 'failed')
  return <li className="watch-tick">
    <div className="watch-row"><strong>{tick.error_code === 'source_limit_reached' ? 'Source storage limit reached' : tick.ingestion_status === 'partial' ? 'Partial check' : status(tick.status)}</strong><time dateTime={tick.created_at}>{when(tick.created_at)}</time></div>
    <p>{tick.added_sources} source {tick.added_sources === 1 ? 'version' : 'versions'} added in this check.</p>
    {tick.ingestion_status === 'complete' && <p>This bounded check finished. Its provider windows and page limits still apply.</p>}
    {tick.ingestion_status === 'partial' && <p className="watch-warning">This check has partial coverage. Unread provider pages or skipped sources may remain.</p>}
    {tick.ingestion_status === 'failed' && <p className="watch-warning">This provider check failed. Earlier saved source versions remain available; no absence of literature can be inferred.</p>}
    {tick.error_code === 'source_limit_reached' && <p className="watch-warning">The watch’s source-version storage limit stopped further reading. Provider positions were preserved and papers may remain unread.</p>}
    {partial && <p className="watch-warning">Provider failures make this check incomplete. They do not establish an absence of relevant literature.</p>}
    {diagnostics.some(([, detail]) => detail.code === 'ingestion_watch_source_limit') && <p className="watch-warning">The saved source-version limit stopped this page. Its provider position was preserved; relevant papers may remain unread.</p>}
    {diagnostics.length > 0 ? <ul className="watch-diagnostics">{diagnostics.map(([provider, detail]) => <li key={provider}><strong>{PROVIDER_NAMES[provider]}</strong>: {detail.status === 'failed' ? 'Reading failed' : 'Page read'} · {detail.sources_seen} sources examined · {detail.added_sources} versions added. {detail.status === 'succeeded' && (detail.exhausted ? 'This provider window was exhausted.' : 'More provider pages may remain; this is a bounded page.')} {detail.since && detail.until && <span>Window: {when(detail.since)} – {when(detail.until)}. </span>}{detail.code && <span>Reason: {status(detail.code)}.</span>}</li>)}</ul> : <p>Provider results have not been recorded for this check.</p>}
    {tick.status === 'succeeded' && tick.added_sources === 0 && <p>No source additions were recorded in this bounded check. This does not show that no relevant papers exist.</p>}
  </li>
}

export function WatchWorkspace({ enabled, csrfToken, findings = [], onSessionExpired, openSource, onSourcesChanged, canReassess = false, onAssessmentApplied, onAssessmentBusyChange, onOpenFinding }: WatchWorkspaceProps) {
  const uid = useId()
  const [capabilities, setCapabilities] = useState<WatchCapabilities | null>(null)
  const [watches, setWatches] = useState<LiteratureWatch[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selected, setSelected] = useState<LiteratureWatch | null>(null)
  const [sources, setSources] = useState<Page<WatchSource>>(EMPTY_SOURCES)
  const [ticks, setTicks] = useState<Page<WatchTick>>(EMPTY_TICKS)
  const [inspection, setInspection] = useState<WatchSourceDetail | null>(null)
  const [inspecting, setInspecting] = useState<string | null>(null)
  const [phase, setPhase] = useState<'idle' | 'loading' | 'ready' | 'error' | 'expired'>('idle')
  const [revision, setRevision] = useState(0)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [uncertain, setUncertain] = useState(false)
  const [pollStopped, setPollStopped] = useState(false)
  const [label, setLabel] = useState('')
  const [query, setQuery] = useState('')
  const [interval, setInterval] = useState(86400)
  const [sourceResultId, setSourceResultId] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const mutation = useRef(false)
  const scope = useRef(0)
  const blocked = useRef(false)
  const requests = useRef(new Set<AbortController>())
  const inspectionRequest = useRef<AbortController | null>(null)
  const workspaceElement = useRef<HTMLElement | null>(null)
  const refreshRequested = useRef(false)
  const polls = useRef(0)
  const knownCounts = useRef(new Map<string, number>())
  const notifyExpired = useRef(onSessionExpired)
  const selectedWatch = useRef<string | null>(null)
  selectedWatch.current = selectedId
  notifyExpired.current = onSessionExpired

  const clear = useCallback(() => {
    setCapabilities(null); setWatches([]); setSelectedId(null); setSelected(null); setSources(EMPTY_SOURCES); setTicks(EMPTY_TICKS)
    setInspection(null); setInspecting(null); setLabel(''); setQuery(''); setSourceResultId(''); setInterval(86400); setCreateOpen(false); setError(null); setUncertain(false); setBusy(null)
  }, [])
  const expire = useCallback(() => {
    if (blocked.current) return
    blocked.current = true; scope.current++; requests.current.forEach((controller) => controller.abort()); inspectionRequest.current?.abort(); mutation.current = false
    clear(); setPhase('expired'); notifyExpired.current()
  }, [clear])
  const fail = useCallback((failure: unknown) => {
    if (failure instanceof IngestionError && failure.code === 'session_expired') { expire(); return }
    setError(message(failure))
    if (failure instanceof IngestionError && failure.uncertain) setUncertain(true)
  }, [expire])

  useEffect(() => {
    scope.current++; blocked.current = false; mutation.current = false; clear(); polls.current = 0; knownCounts.current.clear(); setPollStopped(false)
    return () => { scope.current++; requests.current.forEach((controller) => controller.abort()); inspectionRequest.current?.abort(); mutation.current = false }
  }, [enabled, csrfToken, clear])

  useEffect(() => {
    if (!enabled || !watches.length) return
    let changed = false
    for (const watch of watches) {
      if (watch.source_count > (knownCounts.current.get(watch.id) ?? 0)) changed = true
      knownCounts.current.set(watch.id, watch.source_count)
    }
    if (changed) onSourcesChanged?.()
  }, [enabled, watches, onSourcesChanged])

  useEffect(() => {
    if (!enabled || !csrfToken) { setPhase('idle'); return }
    if (blocked.current) return
    const controller = new AbortController(), generation = scope.current
    requests.current.add(controller)
    if (!capabilities) setPhase('loading')
    const refreshWasRequested = refreshRequested.current
    Promise.all([loadWatchCapabilities(controller.signal), listWatches(controller.signal), selectedId ? Promise.all([getWatch(selectedId, controller.signal), listWatchSources(selectedId, controller.signal), listWatchTicks(selectedId, controller.signal)]) : null]).then(([caps, list, detail]) => {
      if (controller.signal.aborted || generation !== scope.current) return
      if (detail && (detail[0].id !== selectedId || detail[2].items.some((tick) => tick.watch_id !== selectedId))) throw new IngestionError('invalid_response')
      setCapabilities(caps); setWatches(list.items); setPhase('ready')
      if (detail) {
        setSelected(detail[0])
        setSources((previous) => ({ items: merge(detail[1].items, previous.items), next_cursor: previous.items.length > 20 ? previous.next_cursor : detail[1].next_cursor }))
        setTicks((previous) => ({ items: merge(detail[2].items, previous.items), next_cursor: previous.items.length > 20 ? previous.next_cursor : detail[2].next_cursor }))
      }
      if (refreshWasRequested) { refreshRequested.current = false; setUncertain(false); setError(null) }
    }).catch((failure: unknown) => { if (!controller.signal.aborted && generation === scope.current) { fail(failure); if (!(failure instanceof IngestionError && failure.code === 'session_expired')) setPhase('error') } }).finally(() => { requests.current.delete(controller) })
    return () => { controller.abort(); requests.current.delete(controller) }
    // Capabilities are loaded with every bounded refresh, rather than changing the reading gate locally.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, csrfToken, selectedId, revision, fail])

  useEffect(() => {
    if (!enabled || phase !== 'ready' || !watches.length || uncertain || busy) return
    if (polls.current >= 120) { setPollStopped(true); return }
    const timer = window.setTimeout(() => { polls.current++; setRevision((previous) => previous + 1) }, 5000)
    return () => window.clearTimeout(timer)
  }, [enabled, phase, watches, revision, uncertain, busy])

  useEffect(() => {
    if (!enabled || !watches.length) return
    const expires = Math.min(...watches.map((watch) => Date.parse(watch.expires_at)))
    const remaining = expires - Date.now()
    if (remaining <= 0) { expire(); return }
    const timer = window.setTimeout(expire, Math.min(remaining, 2147483647))
    return () => window.clearTimeout(timer)
  }, [enabled, watches, expire])

  useEffect(() => {
    if (sourceResultId && !findings.some((finding) => finding.id === sourceResultId)) setSourceResultId('')
  }, [findings, sourceResultId])

  useEffect(() => {
    if (!enabled || !csrfToken || !openSource || blocked.current) return
    inspectionRequest.current?.abort()
    const controller = new AbortController(), generation = scope.current
    inspectionRequest.current = controller
    setSelected(null); setSources(EMPTY_SOURCES); setTicks(EMPTY_TICKS); setSelectedId(openSource.watchId); setInspection(null); setInspecting(openSource.sourceId); setError(null)
    setRevision((previous) => previous + 1)
    workspaceElement.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
    getWatchSource(openSource.watchId, openSource.sourceId, controller.signal).then((detail) => {
      if (!controller.signal.aborted && generation === scope.current) setInspection(detail)
    }).catch((failure: unknown) => { if (!controller.signal.aborted && generation === scope.current) fail(failure) }).finally(() => { if (!controller.signal.aborted && generation === scope.current) setInspecting(null) })
    return () => controller.abort()
  }, [enabled, csrfToken, openSource, fail])

  const available = enabled && !!csrfToken && phase !== 'expired' && !blocked.current
  const canRead = available && capabilities?.execution_available === true
  const canMutate = available && !busy && !uncertain && phase === 'ready'
  const active = selected?.active_run_id !== null && selected?.active_run_id !== undefined
  const formValid = !!label.trim() && Array.from(label.trim()).length <= 120 && !!query.trim() && Array.from(query.trim()).length <= 500

  function refresh() { refreshRequested.current = true; polls.current = 0; setPollStopped(false); setRevision((previous) => previous + 1) }
  function assessmentChanged(record: AssessmentRecord) {
    polls.current = 0; setPollStopped(false); setRevision((previous) => previous + 1); onAssessmentApplied?.()
    if (!selectedId) return
    const watchId = selectedId, generation = scope.current, controller = new AbortController()
    requests.current.add(controller)
    const sourceIds = new Set(record.applied ? record.new_source_version_ids.filter((id) => sources.items.some((source) => source.id === id)) : [])
    if (inspection) sourceIds.add(inspection.id)
    void Promise.allSettled([...sourceIds].map((id) => getWatchSource(watchId, id, controller.signal))).then((outcomes) => {
      if (controller.signal.aborted || generation !== scope.current || selectedWatch.current !== watchId) return
      for (const outcome of outcomes) {
        if (controller.signal.aborted || generation !== scope.current || selectedWatch.current !== watchId) return
        if (outcome.status === 'rejected') { fail(outcome.reason); continue }
        const detail = outcome.value, original = sources.items.find((source) => source.id === detail.id)
        if (detail.assessment_root_result_id && detail.assessment_root_result_id !== record.root_result_id || original && (detail.content_sha256 !== original.content_sha256 || detail.document.source_id !== original.source_id)) { fail(new IngestionError('invalid_response')); continue }
        setSources((previous) => ({ ...previous, items: previous.items.map((source) => source.id === detail.id ? { ...source, assessment_status: detail.assessment_status, assessment_root_result_id: detail.assessment_root_result_id, assessment_history_url: detail.assessment_history_url } : source) }))
        setInspection((previous) => previous?.id === detail.id ? detail : previous)
      }
    }).finally(() => requests.current.delete(controller))
  }
  function select(watchId: string) {
    inspectionRequest.current?.abort(); setInspection(null); setInspecting(null); setSelected(null); setSources(EMPTY_SOURCES); setTicks(EMPTY_TICKS); setError(null); setSelectedId(watchId); polls.current = 0; setPollStopped(false)
  }
  async function mutate(action: 'create' | 'pause' | 'resume' | 'check', event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault()
    if (!canMutate || mutation.current || !csrfToken || (action !== 'pause' && !canRead) || (action === 'create' && !formValid) || (action !== 'create' && !selected)) return
    mutation.current = true; setBusy(action); setError(null)
    const controller = new AbortController(), generation = scope.current; requests.current.add(controller)
    try {
      if (action === 'create') {
        const created = await createWatch({ label: label.trim(), query: query.trim().replace(/\s+/gu, ' '), interval_seconds: interval, source_result_id: sourceResultId || null }, csrfToken, controller.signal)
        if (controller.signal.aborted || generation !== scope.current) return
        setWatches((previous) => [created, ...previous.filter((watch) => watch.id !== created.id)]); select(created.id); setLabel(''); setQuery(''); setSourceResultId('')
      } else if (action === 'check') {
        await checkWatch(selected!.id, csrfToken, controller.signal)
      } else {
        const updated = await changeWatch(selected!.id, action, csrfToken, controller.signal)
        if (updated.id !== selected!.id) throw new IngestionError('invalid_response', true)
      }
      if (!controller.signal.aborted && generation === scope.current) { polls.current = 0; setPollStopped(false); setRevision((previous) => previous + 1) }
    } catch (failure: unknown) { if (!controller.signal.aborted && generation === scope.current) fail(failure) }
    finally { requests.current.delete(controller); if (generation === scope.current) { mutation.current = false; setBusy(null) } }
  }
  async function inspect(source: WatchSource) {
    if (!available || !selectedId) return
    inspectionRequest.current?.abort(); const controller = new AbortController(); inspectionRequest.current = controller
    const generation = scope.current
    setInspection(null); setInspecting(source.id); setError(null)
    try {
      const detail = await getWatchSource(selectedId, source.id, controller.signal)
      if (controller.signal.aborted || generation !== scope.current) return
      if (detail.document.source_id !== source.source_id || detail.document.content_kind !== source.content_kind || detail.content_sha256 !== source.content_sha256 || detail.run_id !== source.run_id) throw new IngestionError('invalid_response')
      setInspection(detail)
    } catch (failure: unknown) { if (!controller.signal.aborted && generation === scope.current) fail(failure) }
    finally { if (!controller.signal.aborted && generation === scope.current) setInspecting(null) }
  }
  async function older(kind: 'sources' | 'ticks') {
    const cursor = kind === 'sources' ? sources.next_cursor : ticks.next_cursor
    if (!available || !selectedId || !cursor || busy || mutation.current) return
    const controller = new AbortController(), generation = scope.current; requests.current.add(controller); mutation.current = true; setBusy(kind)
    try {
      if (kind === 'sources') { const next = await listWatchSources(selectedId, controller.signal, cursor); if (!controller.signal.aborted && generation === scope.current) setSources((previous) => ({ items: [...previous.items, ...next.items.filter((item) => !previous.items.some((prior) => prior.id === item.id))], next_cursor: next.next_cursor })) }
      else { const next = await listWatchTicks(selectedId, controller.signal, cursor); if (next.items.some((tick) => tick.watch_id !== selectedId)) throw new IngestionError('invalid_response'); if (!controller.signal.aborted && generation === scope.current) setTicks((previous) => ({ items: [...previous.items, ...next.items.filter((item) => !previous.items.some((prior) => prior.id === item.id))], next_cursor: next.next_cursor })) }
    } catch (failure: unknown) { if (!controller.signal.aborted && generation === scope.current) fail(failure) }
    finally { requests.current.delete(controller); if (generation === scope.current) { mutation.current = false; setBusy(null) } }
  }

  return <section className="watch-workspace" aria-labelledby={`${uid}-title`} ref={workspaceElement}>
    <header className="watch-heading"><div><span className="eyebrow accent">KEEP READING</span><h3 id={`${uid}-title`}>Literature watches</h3><p>Follow a question as new source versions arrive. Reading providers saves papers for inspection; it does not call the research model or reassess a finding.</p></div>{available && <button type="button" onClick={refresh} disabled={!!busy}>Refresh watches</button>}</header>
    <p className="watch-scope">Watches belong to this browser session and stop when it expires, at most seven days after the session began. Keeping this page open does not extend that period.</p>
    {!enabled || !csrfToken ? <p>Connect to the research workspace to read this browser session’s watches.</p> : <>
      {phase === 'loading' && <p role="status">Loading literature watches…</p>}
      {phase === 'expired' && <p role="alert">Your browser session has expired or changed. Private watches and sources have been cleared. Reload saved research in the workspace.</p>}
      {capabilities && !capabilities.execution_available && <p className="watch-gate" role="status">Free-provider reading is disabled. New watches, resumed reading, and checks are unavailable. Existing saved watches and sources can still be inspected or paused.</p>}
      {error && <p className="watch-warning" role="alert">{error}</p>}
      {uncertain && <p className="watch-warning" role="alert">The last request’s outcome is unknown. Refresh watches before making another change. It may already have been saved.</p>}
      {pollStopped && <p role="status">Automatic status refresh has paused after ten minutes. Refresh watches to retrieve the latest saved state.</p>}
      <div className="watch-create"><button type="button" aria-expanded={createOpen} aria-controls={`${uid}-create`} onClick={() => setCreateOpen((previous) => !previous)}>Create a literature watch</button>{createOpen && <form id={`${uid}-create`} className="watch-form" onSubmit={(event) => { void mutate('create', event) }} aria-label="Create literature watch">
        <div><label htmlFor={`${uid}-label`}>Watch name</label><input id={`${uid}-label`} maxLength={120} value={label} onChange={(event) => setLabel(event.target.value)} disabled={!canRead || !!busy || uncertain} required /></div>
        <div><label htmlFor={`${uid}-query`}>Literature search query</label><textarea id={`${uid}-query`} maxLength={500} rows={2} value={query} onChange={(event) => setQuery(event.target.value)} disabled={!canRead || !!busy || uncertain} required /><small>Up to 500 characters. Use a focused topic or question.</small></div>
        <div className="watch-form-options"><div><label htmlFor={`${uid}-interval`}>Reading interval</label><select id={`${uid}-interval`} value={interval} onChange={(event) => setInterval(Number(event.target.value))} disabled={!canRead || !!busy || uncertain}>{INTERVALS.map(([value, title]) => <option key={value} value={value}>{title}</option>)}</select></div><div><label htmlFor={`${uid}-finding`}>Link a saved finding (optional)</label><select id={`${uid}-finding`} value={sourceResultId} onChange={(event) => setSourceResultId(event.target.value)} disabled={!canRead || !!busy || uncertain}><option value="">Independent literature watch</option>{findings.map((finding) => <option value={finding.id} key={finding.id}>{finding.title}</option>)}</select></div></div>
        <p className="watch-note">Creating a watch authorizes scheduled reading from OpenAlex and arXiv during this browser session. Saved source versions remain unassessed until a separate investigation reviews them.</p>
        <button type="submit" disabled={!canMutate || !canRead || !formValid || watches.length >= (capabilities?.watch_limit ?? 10)}>{busy === 'create' ? 'Saving watch…' : 'Create literature watch'}</button>
      </form>}</div>
      {watches.length > 0 ? <div className="watch-layout"><nav aria-label="Saved literature watches"><ul className="watch-list">{watches.map((watch) => <li key={watch.id}><button type="button" aria-pressed={selectedId === watch.id} onClick={() => select(watch.id)} disabled={!!busy}><strong>{watch.label}</strong><span>{watch.enabled ? 'Reading enabled' : 'Paused'} · {watch.source_count} saved source versions</span><small>Next due: {watch.enabled ? when(watch.next_due_at) : 'Paused'}</small></button></li>)}</ul></nav>
        {selected ? <section className="watch-detail" aria-label="Selected literature watch"><div className="watch-row"><h4>{selected.label}</h4><span className="watch-badge">{selected.enabled ? 'Reading enabled' : 'Paused'}</span></div><p className="watch-query">{selected.query}</p><dl className="watch-facts"><div><dt>Last check started</dt><dd>{when(selected.last_run_at)}</dd></div><div><dt>Next scheduled check</dt><dd>{selected.enabled ? when(selected.next_due_at) : 'Paused'}</dd></div><div><dt>Session expires</dt><dd>{when(selected.expires_at)}</dd></div><div><dt>Reading interval</dt><dd>{INTERVALS.find(([seconds]) => seconds === selected.interval_seconds)?.[1] ?? `Every ${selected.interval_seconds / 3600} hours`}</dd></div></dl>
          {selected.source_result_id && <p className="watch-note">Linked to an exact saved finding. New sources do not change that finding or establish independent support.</p>}
          {capabilities && selected.source_count >= capabilities.source_limit_per_watch && <p className="watch-warning">This watch reached its limit of {capabilities.source_limit_per_watch} saved source versions. Provider pages may remain unread. Keep the saved evidence and create a narrower watch for future reading.</p>}
          {active && <p role="status">Current provider check: {status(selected.active_status || 'queued')}. No research model is running for this watch.</p>}
          <div className="watch-actions"><button type="button" onClick={() => { void mutate(selected.enabled ? 'pause' : 'resume') }} disabled={!canMutate || (!selected.enabled && !canRead)}>{busy === 'pause' ? 'Pausing…' : busy === 'resume' ? 'Resuming…' : selected.enabled ? 'Pause reading' : 'Resume reading'}</button><button type="button" onClick={() => { void mutate('check') }} disabled={!canMutate || !canRead || !selected.enabled || active}>{busy === 'check' ? 'Queuing check…' : 'Check providers now'}</button></div>
          <p className="watch-note">Checks are throttled. Pausing stops future checks and fences an active check; provider requests already sent may still finish.</p>
          <details className="watch-windows"><summary>Provider windows and coverage</summary><p>Each check reads bounded provider pages. Windows can overlap, and continuation pages may remain. This is not a complete literature review.</p>{(Object.entries(selected.cursors) as [Provider, NonNullable<LiteratureWatch['cursors'][Provider]>][]).map(([provider, cursor]) => <p key={provider}><strong>{PROVIDER_NAMES[provider]}</strong>: {when(cursor.since)} – {when(cursor.until)}. {cursor.exhausted ? 'This recorded window was exhausted.' : cursor.cursor !== null ? 'Continuation pages remain for this window.' : 'This window has not been exhausted.'}</p>)}</details>
          <section className="watch-sources" aria-label="Watch source versions"><h5>Saved source versions</h5><p>These are saved source versions, not confirmed findings. A changed abstract can create another version of the same paper.</p>{sources.items.length ? <ul>{sources.items.map((source) => <li key={source.id}><span className="watch-badge">{assessmentLabel(source.assessment_status)} · {source.content_kind === 'abstract' ? 'abstract' : 'full text'}</span><h6>{source.title}</h6><p>First observed {when(source.first_seen_at)}</p><div className="watch-actions"><button type="button" aria-label={`${inspecting === source.id ? 'Loading saved text' : 'Read saved source'}: ${source.title}`} onClick={() => { void inspect(source) }} disabled={!!inspecting}>{inspecting === source.id ? 'Loading saved text…' : 'Read saved source'}<span className="watch-sr">: {source.title}</span></button><a href={source.url} target="_blank" rel="noopener noreferrer">Provider page<span className="watch-sr">: {source.title}</span></a></div></li>)}</ul> : <p>No saved source versions are shown for this watch. Check history and provider coverage before drawing any conclusion.</p>}{sources.next_cursor && <button type="button" onClick={() => { void older('sources') }} disabled={!!busy}>Load older sources</button>}</section>
          {inspection && <section className="watch-inspection" aria-label="Saved source text"><div className="watch-row"><h5>{inspection.document.title}</h5><button type="button" onClick={() => setInspection(null)}>Close saved source</button></div><p className="watch-warning">{assessmentLabel(inspection.assessment_status)}. Collected source text is not a confirmed finding. Inspect cited assessments separately before drawing conclusions.</p><p className="watch-source-text">{inspection.document.text}</p><p>Retrieved {when(inspection.document.retrieved_at)} · {inspection.document.content_kind === 'abstract' ? 'Abstract' : 'Full text'} · {inspection.document.extraction_method}</p><SourceCoverageNotice coverage={inspection.document.coverage} contentKind={inspection.document.content_kind} /><Bibliography value={inspection.document.bibliography} /><details><summary>Saved source integrity</summary><dl><dt>Content SHA-256</dt><dd>{inspection.content_sha256}</dd><dt>Metadata SHA-256</dt><dd>{inspection.metadata_sha256}</dd></dl></details></section>}
          <section className="watch-history" aria-label="Literature check history"><h5>Check history</h5>{ticks.items.length ? <ol>{ticks.items.map((tick) => <TickCard key={tick.id} tick={tick} />)}</ol> : <p>No provider checks have been recorded yet.</p>}{ticks.next_cursor && <button type="button" onClick={() => { void older('ticks') }} disabled={!!busy}>Load older checks</button>}</section>
          {selected.source_result_id && onOpenFinding && <AssessmentWorkspace key={`${selected.id}:${selected.source_result_id}`} watchId={selected.id} sourceResultId={selected.source_result_id} sources={sources.items.filter((source) => !source.assessment_status || source.assessment_status === 'unassessed')} csrfToken={csrfToken} enabled={available} canSubmit={canReassess} onSessionExpired={expire} onAssessmentChanged={assessmentChanged} onBusyChange={onAssessmentBusyChange} onOpenFinding={onOpenFinding} />}
        </section> : <p className="watch-note">{selectedId ? 'Loading the selected watch…' : 'Choose a watch to inspect saved sources, check history, and provider coverage.'}</p>}
      </div> : phase === 'ready' && <p>No literature watches have been saved in this browser session.</p>}
    </>}
  </section>
}
