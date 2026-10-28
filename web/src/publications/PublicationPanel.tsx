import { useEffect, useId, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import type { SavedArtifact } from '../artifacts/types'
import {
  approvePublication, loginOperator, logoutOperator, manifestMatchesArtifact,
  operatorSession, parseMetadata, preparePublication, publicationCapabilities,
  publicationHistory, PublicationError, revokePublication, validOrcid,
} from './client'
import type { Destination, OperatorSession, PublicationCapabilities, PublicationManifest, PublicationMetadata } from './client'
import './publications.css'
import { PublicationDelivery } from './PublicationDelivery'

interface Props { artifact: SavedArtifact; csrfToken: string | null; enabled: boolean; onSessionExpired: () => void }
type Action = 'prepare' | 'approve' | 'revoke' | 'login' | 'logout'
const UNAUTHENTICATED: OperatorSession = { configured: false, authenticated: false, actor_id: null, display_name: null, expires_at: null, csrf_token: null }

function initialMetadata(artifact: SavedArtifact): PublicationMetadata {
  const draft = artifact.payload.draft
  return { title: draft?.title ?? '', description: draft && draft.kind !== 'formalisation' ? draft.abstract.map((paragraph) => paragraph.text).join('\n\n').slice(0, 5000) : '', creators: [{ name: '', affiliation: null, orcid: null }], keywords: [], license: 'cc-by-4.0', upload_type: 'publication', publication_type: 'preprint', access_right: 'open' }
}
function when(value: string): string { return new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) }
function destinationName(value: Destination): string { return value === 'sandbox' ? 'Zenodo sandbox · test record' : 'Zenodo production · public record' }
function status(record: PublicationManifest, now: number): string {
  const approval = record.approval
  if (!approval) return 'Prepared preview'
  if (approval.revoked_at) return 'Approval revoked'
  if (!approval.valid || Date.parse(approval.expires_at) <= now) return 'Approval expired'
  return 'Approval recorded'
}
function errorMessage(error: unknown): string {
  if (!(error instanceof PublicationError)) return 'Publication details could not be checked. Reload them before continuing.'
  switch (error.code) {
    case 'operator_expired': return 'Publisher access expired or changed. Unlock publisher approval again.'
    case 'invalid_credential': return 'Publisher access was not accepted. Check the access code and try again.'
    case 'unavailable': return 'Publication preparation or publisher approval is unavailable right now. Saved previews remain unchanged.'
    case 'manifest_changed': return 'The saved preview changed. Reload its exact document and details before approving it.'
    case 'not_found': return 'This publication preview is unavailable in your current research session.'
    case 'invalid_request': return 'Check the publication details. Use plain text and a valid creator name and optional ORCID.'
    case 'invalid_response': return 'The publication preview could not be verified against this saved document. Reload publication details before continuing.'
    default: return 'Publication details could not be checked. Reload them before continuing.'
  }
}

export function PublicationPanel({ artifact, csrfToken, enabled, onSessionExpired }: Props) {
  const headingId = useId()
  const [capabilities, setCapabilities] = useState<PublicationCapabilities | null>(null)
  const [operator, setOperator] = useState<OperatorSession>(UNAUTHENTICATED)
  const [metadata, setMetadata] = useState<PublicationMetadata>(() => initialMetadata(artifact))
  const [keywords, setKeywords] = useState('')
  const [destination, setDestination] = useState<Destination | ''>('')
  const [history, setHistory] = useState<PublicationManifest[]>([])
  const [record, setRecord] = useState<PublicationManifest | null>(null)
  const [reviewed, setReviewed] = useState(false)
  const [credential, setCredential] = useState('')
  const [phase, setPhase] = useState<'loading' | 'ready' | 'error' | 'expired'>('loading')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState<Action | null>(null)
  const [uncertain, setUncertain] = useState(false)
  const [edited, setEdited] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)
  const [now, setNow] = useState(Date.now())
  const version = useRef(0)
  const mutation = useRef<AbortController | null>(null)
  const pagination = useRef<AbortController | null>(null)

  function applyRecord(incoming: PublicationManifest) {
    if (!manifestMatchesArtifact(incoming, artifact)) throw new PublicationError('invalid_response')
    setRecord(incoming)
    setMetadata(incoming.manifest.metadata)
    setKeywords(incoming.manifest.metadata.keywords.join(', '))
    setDestination(incoming.manifest.destination)
    setReviewed(false); setEdited(false); setNow(Date.now())
    setHistory((previous) => previous.some((item) => item.id === incoming.id) ? previous.map((item) => item.id === incoming.id ? incoming : item) : [incoming, ...previous])
  }

  function fail(failure: unknown, write = false) {
    setReviewed(false); setCredential('')
    if (failure instanceof PublicationError && (failure.code === 'browser_expired' || failure.code === 'browser_changed')) {
      version.current += 1
      mutation.current?.abort(); pagination.current?.abort()
      setHistory([]); setRecord(null); setOperator(UNAUTHENTICATED); setCapabilities(null)
      setMetadata(initialMetadata(artifact)); setKeywords(''); setDestination('')
      setPhase('expired'); setError('Your research session expired or changed. Reload saved research to continue.')
      onSessionExpired()
      return
    }
    if (failure instanceof PublicationError && failure.code === 'operator_expired') setOperator((previous) => ({ ...UNAUTHENTICATED, configured: previous.configured }))
    if (write && (!(failure instanceof PublicationError) || ['connection_failed', 'invalid_response', 'manifest_changed'].includes(failure.code))) {
      setUncertain(true); setRecord(null)
      setError('The change could not be confirmed. Reload publication details before doing anything else.')
    } else setError(errorMessage(failure))
  }

  useEffect(() => {
    const controller = new AbortController()
    const current = ++version.current
    setPhase('loading'); setRecord(null); setHistory([]); setReviewed(false); setCredential(''); setError(''); setNextCursor(null)
    setOperator(UNAUTHENTICATED); setCapabilities(null)
    setMetadata(initialMetadata(artifact)); setKeywords(''); setDestination('')
    if (!enabled || !csrfToken || artifact.status !== 'ready' || artifact.kind === 'formalise') return () => controller.abort()
    void Promise.all([publicationCapabilities(controller.signal), operatorSession(controller.signal), publicationHistory(artifact.id, controller.signal)]).then(([capability, session, saved]) => {
      if (controller.signal.aborted || current !== version.current) return
      if (saved.manifests.some((item) => !manifestMatchesArtifact(item, artifact))) throw new PublicationError('invalid_response')
      setCapabilities(capability); setOperator(session); setHistory(saved.manifests); setNextCursor(saved.next_cursor)
      setDestination(capability.destinations.includes('sandbox') ? 'sandbox' : '')
      setUncertain(false); setEdited(false); setNow(Date.now())
      if (saved.manifests[0]) applyRecord(saved.manifests[0])
      setPhase('ready')
    }).catch((failure: unknown) => {
      if (!controller.signal.aborted && current === version.current) { setPhase('error'); fail(failure) }
    })
    return () => { controller.abort(); mutation.current?.abort(); pagination.current?.abort() }
    // The panel is keyed by the exact immutable artifact; callbacks cannot
    // change that boundary while an async response is pending.
  }, [artifact.id, artifact.markdown_sha256, enabled, csrfToken, refresh, onSessionExpired])

  useEffect(() => {
    const deadlines = [operator.expires_at, record?.approval?.revoked_at ? null : record?.approval?.expires_at].filter((value): value is string => !!value).map(Date.parse).filter((time) => time > now)
    if (!deadlines.length) return
    const timer = window.setTimeout(() => {
      const current = Date.now()
      setNow(current); setReviewed(false)
      if (operator.expires_at && Date.parse(operator.expires_at) <= current) setOperator((previous) => ({ ...UNAUTHENTICATED, configured: previous.configured }))
    }, Math.min(Math.max(1, Math.min(...deadlines) - Date.now() + 25), 2147483647))
    return () => window.clearTimeout(timer)
  }, [operator.expires_at, record?.approval?.id, record?.approval?.expires_at, record?.approval?.revoked_at, now])

  function changed() { setRecord(null); setReviewed(false); setEdited(true); setError('') }
  function update(values: Partial<PublicationMetadata>) { changed(); setMetadata((previous) => ({ ...previous, ...values })) }
  function updateCreator(index: number, field: 'name' | 'affiliation' | 'orcid', value: string) {
    changed()
    setMetadata((previous) => ({ ...previous, creators: previous.creators.map((creator, position) => position === index ? { ...creator, [field]: value || (field === 'name' ? '' : null) } : creator) }))
  }

  async function perform<T>(action: Action, request: (signal: AbortSignal) => Promise<T>, accept: (value: T) => void) {
    if (!enabled || !csrfToken || phase !== 'ready' || mutation.current || uncertain) return
    const controller = new AbortController()
    const current = version.current
    mutation.current = controller
    setBusy(action); setError(''); setReviewed(false)
    try {
      const result = await request(controller.signal)
      if (!controller.signal.aborted && current === version.current) accept(result)
    } catch (failure) { if (!controller.signal.aborted && current === version.current) fail(failure, true) }
    finally { if (mutation.current === controller) { mutation.current = null; setBusy(null) } }
  }

  const preparedMetadata = { ...metadata, title: metadata.title.trim(), description: metadata.description.trim(), creators: metadata.creators.map((creator) => ({ name: creator.name.trim(), affiliation: creator.affiliation?.trim() || null, orcid: creator.orcid?.trim() || null })), keywords: keywords.split(',').map((keyword) => keyword.trim()).filter(Boolean) }
  let validMetadata = false
  try { parseMetadata(preparedMetadata); validMetadata = true } catch { /* Inline fields remain editable until complete. */ }
  const authenticated = operator.authenticated && !!operator.csrf_token && !!operator.expires_at && Date.parse(operator.expires_at) > now
  const activeApproval = !!record?.approval?.valid && !record.approval.revoked_at && Date.parse(record.approval.expires_at) > now
  const matchesPolicy = !!record && !!capabilities?.publisher && record.manifest.publisher.identifier === capabilities.publisher.identifier && record.manifest.publisher.display_name === capabilities.publisher.display_name && capabilities.destinations.includes(record.manifest.destination)
  const canPrepare = enabled && phase === 'ready' && !!csrfToken && !!capabilities?.can_prepare && destination !== '' && capabilities.destinations.includes(destination) && validMetadata && !busy && !uncertain
  const canApprove = !!record && !!capabilities?.can_prepare && matchesPolicy && authenticated && reviewed && !activeApproval && !busy && !uncertain

  function prepare(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canPrepare || !csrfToken) return
    void perform('prepare', (signal) => preparePublication(artifact.id, preparedMetadata, destination, csrfToken, signal), applyRecord)
  }
  function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!credential || !operator.configured || !csrfToken) return
    const submitted = credential
    setCredential('')
    void perform('login', (signal) => loginOperator(submitted, csrfToken, signal), (session) => { setOperator(session); setReviewed(false); setNow(Date.now()) })
  }

  async function loadMore() {
    if (!nextCursor || phase !== 'ready' || pagination.current) return
    const controller = new AbortController()
    const current = version.current
    pagination.current = controller
    setLoadingMore(true)
    try {
      const saved = await publicationHistory(artifact.id, controller.signal, nextCursor)
      if (!controller.signal.aborted && current === version.current) {
        if (saved.manifests.some((item) => !manifestMatchesArtifact(item, artifact))) throw new PublicationError('invalid_response')
        setHistory((previous) => [...new Map([...previous, ...saved.manifests].map((item) => [item.id, item])).values()])
        setNextCursor(saved.next_cursor === nextCursor ? null : saved.next_cursor)
      }
    } catch (failure) { if (!controller.signal.aborted && current === version.current) fail(failure) }
    finally { if (pagination.current === controller) { pagination.current = null; setLoadingMore(false) } }
  }

  if (!enabled || phase === 'expired') return phase === 'expired' ? <p className="publication-message" role="alert">{error}</p> : null
  return <section className="publication-panel" aria-labelledby={headingId}>
    <header className="publication-heading"><span className="eyebrow accent">REVIEW BEFORE PUBLICATION</span><div><h3 id={headingId}>An exact version. A human decision.</h3><button type="button" className="text-button" disabled={phase === 'loading' || !!busy} onClick={() => setRefresh((value) => value + 1)}>Reload publication details</button></div><p>Prepare the saved document and its publication details for review. Approval applies only to the exact document, metadata, publisher, and destination shown below.</p></header>
    {phase === 'loading' && <p className="publication-message" role="status">Loading publication settings and saved previews…</p>}
    {phase === 'error' && <p className="publication-message" role="status">Publication settings could not be checked. Reload publication details to try again.</p>}
    {error && phase !== 'error' && <p className="publication-message publication-error" role="alert">{error}</p>}
    {phase === 'ready' && <>
      {!capabilities?.can_prepare && <p className="publication-message">Publication preparation is not configured yet. Saved previews remain available.</p>}
      <form className="publication-form" onSubmit={prepare}>
        <div className="publication-field"><label htmlFor={`${headingId}-title`}>Publication title</label><input id={`${headingId}-title`} maxLength={300} required value={metadata.title} disabled={!!busy || uncertain} onChange={(event) => update({ title: event.target.value })} /></div>
        <div className="publication-field"><label htmlFor={`${headingId}-description`}>Description</label><textarea id={`${headingId}-description`} maxLength={5000} required rows={4} value={metadata.description} disabled={!!busy || uncertain} onChange={(event) => update({ description: event.target.value })} /></div>
        <fieldset className="publication-creators"><legend>Creators</legend>{metadata.creators.map((creator, index) => <div className="publication-creator" key={index}>
          <div className="publication-field"><label htmlFor={`${headingId}-creator-${index}`}>Creator {index + 1} name</label><input id={`${headingId}-creator-${index}`} maxLength={200} required value={creator.name} disabled={!!busy || uncertain} autoComplete="name" onChange={(event) => updateCreator(index, 'name', event.target.value)} /></div>
          <div className="publication-field"><label htmlFor={`${headingId}-affiliation-${index}`}>Affiliation (optional)</label><input id={`${headingId}-affiliation-${index}`} maxLength={300} value={creator.affiliation ?? ''} disabled={!!busy || uncertain} onChange={(event) => updateCreator(index, 'affiliation', event.target.value)} /></div>
          <div className="publication-field"><label htmlFor={`${headingId}-orcid-${index}`}>ORCID (optional)</label><input id={`${headingId}-orcid-${index}`} maxLength={19} value={creator.orcid ?? ''} disabled={!!busy || uncertain} placeholder="0000-0000-0000-0000" aria-invalid={!!creator.orcid && !validOrcid(creator.orcid)} onChange={(event) => updateCreator(index, 'orcid', event.target.value)} />{creator.orcid && !validOrcid(creator.orcid) && <p className="publication-field-note">Enter a valid ORCID, including its check digit.</p>}</div>
          {metadata.creators.length > 1 && <button type="button" className="text-button" disabled={!!busy || uncertain} onClick={() => { changed(); setMetadata((previous) => ({ ...previous, creators: previous.creators.filter((_, position) => position !== index) })) }}>Remove creator {index + 1}</button>}
        </div>)}<button type="button" className="text-button" disabled={metadata.creators.length >= 10 || !!busy || uncertain} onClick={() => { changed(); setMetadata((previous) => ({ ...previous, creators: [...previous.creators, { name: '', affiliation: null, orcid: null }] })) }}>Add another creator</button></fieldset>
        <div className="publication-field"><label htmlFor={`${headingId}-keywords`}>Keywords (separate with commas)</label><input id={`${headingId}-keywords`} value={keywords} maxLength={1600} disabled={!!busy || uncertain} onChange={(event) => { changed(); setKeywords(event.target.value) }} /></div>
        <div className="publication-field-row"><div className="publication-field"><label htmlFor={`${headingId}-license`}>License</label><select id={`${headingId}-license`} value={metadata.license} disabled={!!busy || uncertain} onChange={(event) => update({ license: event.target.value as PublicationMetadata['license'] })}><option value="cc-by-4.0">CC BY 4.0 · attribution required</option><option value="cc0-1.0">CC0 1.0 · public domain dedication</option></select></div><div className="publication-field"><label htmlFor={`${headingId}-destination`}>Destination</label><select id={`${headingId}-destination`} value={destination} disabled={!!busy || uncertain} onChange={(event) => { changed(); setDestination(event.target.value as Destination | '') }}><option value="">Choose a destination</option><option value="sandbox" disabled={!capabilities?.destinations.includes('sandbox')}>Zenodo sandbox · test record</option><option value="production" disabled={!capabilities?.destinations.includes('production')}>Zenodo production · public record</option></select></div></div>
        <p className="publication-field-note">Open-access preprint · use plain text in these details. Source links are already preserved in the document.</p>
        <button type="submit" className="publication-primary" disabled={!canPrepare}>{busy === 'prepare' ? 'Preparing exact preview…' : 'Prepare publication preview'}</button>
        {edited && <p className="publication-field-note">Changes need a new preview. Earlier approvals apply only to their saved version.</p>}
      </form>
      {history.length > 0 && <nav className="publication-history" aria-label="Saved publication previews"><h4>Saved previews</h4><ul>{history.map((item) => <li key={item.id}><button type="button" aria-pressed={record?.id === item.id} disabled={!!busy || uncertain} onClick={() => { setError(''); applyRecord(item) }}><strong>{item.manifest.metadata.title}</strong><span>{item.manifest.destination === 'sandbox' ? 'Sandbox' : 'Production'} · {status(item, now)}</span></button></li>)}</ul>{nextCursor && <button type="button" className="text-button" disabled={loadingMore || !!busy || uncertain} onClick={() => void loadMore()}>{loadingMore ? 'Loading previews…' : 'Load earlier previews'}</button>}</nav>}
      {record && <section className="publication-exact-preview" aria-label="Exact publication preview">
        <h4>{record.manifest.metadata.title}</h4><p className="publication-approval-status" role="status">{status(record, now)}</p>
        <dl className="publication-preview-details"><dt>Destination</dt><dd className={record.manifest.destination === 'production' ? 'publication-production' : ''}>{destinationName(record.manifest.destination)}</dd><dt>Publisher</dt><dd>{record.manifest.publisher.display_name}</dd><dt>Creators</dt><dd>{record.manifest.metadata.creators.map((creator, index) => <div key={index}>{creator.name}{creator.affiliation && ` · ${creator.affiliation}`}{creator.orcid && <span className="publication-orcid">ORCID {creator.orcid}</span>}</div>)}</dd><dt>License</dt><dd>{record.manifest.metadata.license === 'cc-by-4.0' ? 'CC BY 4.0' : 'CC0 1.0'}</dd><dt>Type and access</dt><dd>Preprint · open access</dd><dt>Keywords</dt><dd>{record.manifest.metadata.keywords.join(', ') || 'None'}</dd><dt>Document</dt><dd><a href={record.artifact_export_url} download>{record.manifest.file_name}</a> · {record.manifest.markdown_bytes} bytes</dd></dl>
        <h5>Description</h5><p className="publication-description">{record.manifest.metadata.description}</p>
        <details className="publication-fingerprint"><summary>Exact document and review version</summary><dl><dt>Document hash</dt><dd>{record.manifest.markdown_sha256}</dd><dt>Review hash</dt><dd>{record.manifest_sha256}</dd><dt>Publisher identity</dt><dd>{record.manifest.publisher.identifier}</dd><dt>Record marker</dt><dd>{record.manifest.remote_marker}</dd><dt>Saved at</dt><dd>{when(record.created_at)}</dd></dl></details>
        {record.approval && <p className="publication-field-note">Approval by {record.approval.actor_id} · {when(record.approval.approved_at)} · expires {when(record.approval.expires_at)}{record.approval.revoked_at && ` · revoked ${when(record.approval.revoked_at)}`}</p>}
        {!matchesPolicy && capabilities?.can_prepare && <p className="publication-field-note">This saved preview uses a different publisher or destination from the current settings. Prepare a new preview to approve current details.</p>}
        <label className="publication-consent"><input type="checkbox" checked={reviewed} disabled={!authenticated || !!busy || uncertain || activeApproval || !matchesPolicy || !capabilities?.can_prepare} onChange={(event) => setReviewed(event.target.checked)} /><span>I approve this exact document, metadata, publisher, and destination.<small>{record.manifest.destination === 'sandbox' ? 'This approval is for a sandbox test record.' : 'This approval is for production publication of this exact version.'}</small></span></label>
        <div className="publication-approval-actions"><button type="button" className="publication-primary" disabled={!canApprove} onClick={() => { if (record && csrfToken && operator.csrf_token && canApprove) void perform('approve', (signal) => approvePublication(record, csrfToken, operator.csrf_token!, signal), applyRecord) }}>{busy === 'approve' ? 'Recording approval…' : 'Approve this exact version'}</button>{record.approval && !record.approval.revoked_at && <button type="button" className="publication-revoke" disabled={!authenticated || !!busy || uncertain} onClick={() => { if (record && csrfToken && operator.csrf_token) void perform('revoke', (signal) => revokePublication(record, csrfToken, operator.csrf_token!, signal), applyRecord) }}>{busy === 'revoke' ? 'Revoking approval…' : 'Revoke this approval'}</button>}</div>
        <p className="publication-not-published">Approval records a human decision. Publishing requires the separate action below.</p>
        <PublicationDelivery key={record.id} record={record} csrfToken={csrfToken} operatorCsrf={authenticated ? operator.csrf_token : null} operatorAuthenticated={authenticated} enabled={enabled} blocked={!!busy || uncertain || !matchesPolicy} onAuthorizationError={fail} />
      </section>}
      <section className="publication-operator" aria-label="Publisher approval access">
        <h4>Publisher approval</h4>{authenticated ? <div className="publication-operator-session"><p>Unlocked for {operator.display_name} until {operator.expires_at && when(operator.expires_at)}.</p><button type="button" className="text-button" disabled={!!busy || uncertain} onClick={() => { if (csrfToken && operator.csrf_token) { setCredential(''); setReviewed(false); void perform('logout', (signal) => logoutOperator(csrfToken, operator.csrf_token!, signal), setOperator) } }}>{busy === 'logout' ? 'Locking access…' : 'Lock publisher access'}</button></div> : operator.configured ? <form className="publication-login" onSubmit={login}><p>Only an authorized publisher can approve or revoke an exact version. Unlocking access does not approve anything.</p><label htmlFor={`${headingId}-access`}>Publisher access code</label><div><input id={`${headingId}-access`} type="password" autoComplete="off" maxLength={256} value={credential} disabled={!!busy || uncertain} onChange={(event) => setCredential(event.target.value)} /><button type="submit" disabled={!credential || !!busy || uncertain}>{busy === 'login' ? 'Unlocking…' : 'Unlock publisher approval'}</button></div></form> : <p>Publisher approval access has not been configured.</p>}
      </section>
    </>}
  </section>
}
