import { useCallback, useEffect, useRef, useState } from 'react'
import { createShare, getSharePreview, listShares, loadSharingCapabilities, revokeShare, SharingError } from './client'
import type { CreateShare, SharePreview, ShareRecord, SharingCapabilities } from './types'
import { PublicSnapshot, publicTitle, sharingDate, SnapshotStatus } from './PublicSnapshot'

export function ShareWorkspace({ sourceResultId, enabled, csrfToken, sourceCurrent, refreshToken, onSessionExpired }: {
  sourceResultId: string; enabled: boolean; csrfToken: string | null; sourceCurrent: boolean; refreshToken?: string | number; onSessionExpired: () => void
}) {
  const [capabilities, setCapabilities] = useState<SharingCapabilities | null>(null), [shares, setShares] = useState<ShareRecord[]>([]), [cursor, setCursor] = useState<string | null>(null)
  const [preview, setPreview] = useState<SharePreview | null>(null), [selected, setSelected] = useState<string | null>(null), [phase, setPhase] = useState<'loading' | 'ready' | 'error' | 'expired'>('loading')
  const [busy, setBusy] = useState<string | null>(null), [error, setError] = useState(''), [uncertain, setUncertain] = useState(false), [previewExpired, setPreviewExpired] = useState(false)
  const [reload, setReload] = useState(0), [copyNotice, setCopyNotice] = useState('')
  const locked = useRef(false), blocked = useRef(false), requests = useRef(new Set<AbortController>()), generation = useRef(0), pending = useRef<CreateShare | null>(null), notifyExpired = useRef(onSessionExpired)
  const lastHead = useRef(`${sourceCurrent}:${refreshToken}`)
  notifyExpired.current = onSessionExpired
  const expire = useCallback(() => { if (blocked.current) return; blocked.current = true; generation.current++; requests.current.forEach((request) => request.abort()); requests.current.clear(); pending.current = null; locked.current = false; setCapabilities(null); setShares([]); setCursor(null); setPreview(null); setSelected(null); setUncertain(false); setBusy(null); setError(''); setCopyNotice(''); setPhase('expired'); notifyExpired.current() }, [])
  const fail = useCallback((caught: unknown, fallback: string) => {
    if (caught instanceof SharingError && caught.code === 'session_expired') { expire(); return }
    const code = caught instanceof SharingError ? caught.code : ''
    setError(code === 'sharing_source_not_current' ? 'This exact finding is no longer current and supported. Open the current finding before creating another public snapshot.' : code === 'sharing_projection_changed' ? 'The public projection changed. Load and review a fresh preview before sharing.' : code === 'sharing_active_limit' || code === 'sharing_global_limit' ? 'The active sharing limit has been reached. Existing public links remain available until revoked or expired.' : code === 'sharing_unavailable' ? 'New public sharing is disabled on this server. Existing owned snapshots can still be inspected or revoked.' : fallback)
  }, [expire])
  useEffect(() => {
    const scope = ++generation.current
    blocked.current = false; locked.current = false; pending.current = null; requests.current.forEach((request) => request.abort()); requests.current.clear()
    setCapabilities(null); setShares([]); setCursor(null); setPreview(null); setSelected(null); setBusy(null); setUncertain(false); setError(''); setCopyNotice(''); setPhase('loading')
    if (!enabled || !csrfToken) return () => { generation.current++; requests.current.forEach((request) => request.abort()) }
    const controller = new AbortController(); requests.current.add(controller)
    const timer = window.setTimeout(() => { controller.abort(); if (scope === generation.current && !blocked.current) setPhase('error') }, 10000)
    Promise.all([loadSharingCapabilities(controller.signal), listShares(sourceResultId, controller.signal)]).then(([available, history]) => {
      if (scope === generation.current && !blocked.current && !controller.signal.aborted) { setCapabilities(available); setShares(history.shares); setCursor(history.next_cursor); setPhase('ready') }
    }).catch((caught: unknown) => { if (scope === generation.current && !blocked.current && !controller.signal.aborted) { setPhase('error'); fail(caught, 'Saved sharing records could not be loaded.') } }).finally(() => { window.clearTimeout(timer); requests.current.delete(controller) })
    return () => { generation.current++; controller.abort(); window.clearTimeout(timer); requests.current.forEach((request) => request.abort()); requests.current.clear() }
  }, [sourceResultId, enabled, csrfToken, expire, fail, reload])
  useEffect(() => { if (!sourceCurrent) { setPreview(null); setSelected(null) } }, [sourceCurrent, refreshToken])
  useEffect(() => {
    const value = `${sourceCurrent}:${refreshToken}`
    if (lastHead.current === value) return
    lastHead.current = value
    if (!enabled || !csrfToken || blocked.current) return
    const controller = new AbortController(), scope = generation.current; requests.current.add(controller)
    const timer = window.setTimeout(() => controller.abort(), 10000)
    listShares(sourceResultId, controller.signal).then((history) => { if (scope === generation.current && !blocked.current && !controller.signal.aborted) { setShares(history.shares); setCursor(history.next_cursor) } }).catch((caught) => { if (scope === generation.current && !blocked.current && !controller.signal.aborted) fail(caught, 'Shared status could not be refreshed. Reload sharing records to check it.') }).finally(() => requests.current.delete(controller))
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [sourceCurrent, refreshToken, sourceResultId, enabled, csrfToken, fail])
  useEffect(() => {
    setPreviewExpired(false)
    if (!preview) return
    const wait = Date.parse(preview.expires_at) - Date.now()
    if (wait <= 0) { setPreviewExpired(true); return }
    const timer = window.setTimeout(() => { setPreviewExpired(true); setPreview(null); expire() }, wait)
    return () => window.clearTimeout(timer)
  }, [preview, expire])

  async function action(kind: string, work: (signal: AbortSignal, scope: number) => Promise<void>) {
    if (locked.current || blocked.current || !enabled || !csrfToken) return
    locked.current = true; setBusy(kind); setError(''); setCopyNotice('')
    const scope = generation.current, controller = new AbortController(); requests.current.add(controller)
    const timeout = window.setTimeout(() => controller.abort(), 15000)
    try { await work(controller.signal, scope) } catch (caught) { if (scope === generation.current && !blocked.current) fail(caught, 'The sharing request could not be completed. Reload saved sharing records before starting another action.') }
    finally { window.clearTimeout(timeout); requests.current.delete(controller); if (scope === generation.current && !blocked.current) { locked.current = false; setBusy(null) } }
  }
  const current = (scope: number, signal: AbortSignal) => scope === generation.current && !blocked.current && !signal.aborted
  async function readPreview() {
    if (!sourceCurrent || !capabilities?.sharing_available || pending.current) return
    await action('preview', async (signal, scope) => { const saved = await getSharePreview(sourceResultId, signal); if (current(scope, signal)) { setPreview(saved); setSelected(null); setUncertain(false) } })
  }
  async function share() {
    if (locked.current || blocked.current || !csrfToken || !pending.current && (!preview || previewExpired || !sourceCurrent || !capabilities?.sharing_available)) return
    const body = pending.current ?? { source_result_id: sourceResultId, expected_projection_sha256: preview!.projection_sha256, idempotency_key: crypto.randomUUID() }
    pending.current = body
    await action('share', async (signal, scope) => {
      try {
        const saved = await createShare(body, csrfToken, signal)
        if (current(scope, signal)) { pending.current = null; setUncertain(false); setShares((previous) => [saved, ...previous.filter((item) => item.public_id !== saved.public_id)]); setSelected(saved.public_id); setPreview(null) }
      } catch (caught) {
        if (scope !== generation.current || blocked.current) return
        if (caught instanceof SharingError && caught.code === 'session_expired') { expire(); return }
        if (caught instanceof SharingError && caught.uncertain || signal.aborted) {
          setUncertain(true); setError('The share response was not confirmed. Check this same action before starting another share; its original request and idempotency key are retained.')
          const read = new AbortController(); requests.current.add(read)
          const readTimeout = window.setTimeout(() => read.abort(), 10000)
          try { const history = await listShares(sourceResultId, read.signal); if (scope === generation.current && !blocked.current && !read.signal.aborted) { setShares(history.shares); setCursor(history.next_cursor); const existing = history.shares.find((item) => item.idempotency_key === body.idempotency_key && item.projection_sha256 === body.expected_projection_sha256); if (existing) { pending.current = null; setUncertain(false); setSelected(existing.public_id); setPreview(null); setError('The saved share was recovered. No second public link was created.') } } } catch (readError) { if (scope === generation.current && !blocked.current) fail(readError, 'The share response is still unknown. Check the original action again when the connection is available.') } finally { window.clearTimeout(readTimeout); requests.current.delete(read) }
        } else { pending.current = null; setUncertain(false); if (caught instanceof SharingError && ['sharing_source_not_current', 'sharing_projection_changed'].includes(caught.code)) setPreview(null); throw caught }
      }
    })
  }
  async function revoke(record: ShareRecord) {
    if (!csrfToken || pending.current) return
    await action(`revoke:${record.public_id}`, async (signal, scope) => {
      try { const saved = await revokeShare(record.public_id, csrfToken, signal); if (current(scope, signal)) setShares((previous) => previous.map((item) => item.public_id === saved.public_id ? saved : item)) }
      catch (caught) { if (scope === generation.current && !blocked.current && caught instanceof SharingError && caught.uncertain) { setError('The revoke response was not confirmed. Reload sharing records to check it; revoking the same link again is safe.'); const history = await listShares(sourceResultId, signal); if (current(scope, signal)) { setShares(history.shares); setCursor(history.next_cursor) } } else throw caught }
    })
  }
  async function more() { if (!cursor || pending.current) return; await action('history', async (signal, scope) => { const history = await listShares(sourceResultId, signal, cursor); if (current(scope, signal)) { setShares((previous) => [...previous, ...history.shares.filter((item) => !previous.some((old) => old.public_id === item.public_id))]); setCursor(history.next_cursor) } }) }
  async function copy(record: ShareRecord) { try { await navigator.clipboard.writeText(new URL(record.public_path, window.location.origin).href); if (!blocked.current) setCopyNotice('Public link copied.') } catch { if (!blocked.current) setCopyNotice('Copy is unavailable. Use the public link displayed below.') } }
  const selectedRecord = shares.find((record) => record.public_id === selected), canPreview = enabled && !!csrfToken && phase === 'ready' && capabilities?.sharing_available && sourceCurrent && !busy && !pending.current
  return <section className="sharing-workspace" aria-label="Share an exact public finding snapshot"><span className="eyebrow accent">SHARE KNOWLEDGE</span><h2>An exact snapshot, openly readable.</h2><p>Preview this finding’s public text and short citation excerpts before sharing. The public link does not grant access to your private research desk.</p>
    {phase === 'loading' && <p role="status">Loading sharing availability and your saved public links…</p>}{phase === 'expired' && <p role="alert">The browser session expired or changed. Private sharing previews and records have been cleared.</p>}{capabilities && !capabilities.sharing_available && <p className="sharing-warning">New public sharing is disabled on this server. Saved public links can still be inspected or revoked.</p>}{!sourceCurrent && phase === 'ready' && <p className="sharing-warning">New shares require this exact finding to be current and supported. Its existing snapshots remain available for inspection and revocation.</p>}
    <div className="sharing-actions"><button type="button" disabled={!canPreview} onClick={() => void readPreview()}>{busy === 'preview' ? 'Loading exact preview…' : 'Preview public snapshot'}</button><button type="button" disabled={!!busy || !!pending.current || phase === 'expired' || !enabled || !csrfToken} onClick={() => setReload((value) => value + 1)}>Reload sharing records</button></div>
    {error && <p className="sharing-warning" role="alert">{error}</p>}{copyNotice && <p role="status">{copyNotice}</p>}
    {preview && <section aria-label="Exact public sharing preview"><h3 className="public-section-title">Review everything that will be public</h3><dl className="sharing-meta"><dt>Available until</dt><dd>{sharingDate(preview.expires_at)}</dd><dt>Canonical projection SHA-256</dt><dd className="sharing-hash">{preview.projection_sha256}</dd></dl><PublicSnapshot projection={preview.projection} prefix="share-preview" /><p className="sharing-warning">Sharing makes all text above publicly readable and discoverable until {sharingDate(preview.expires_at)}. A later assessment or evidence review may mark it superseded, unsupported or needing review without exposing private findings or source details. You can revoke the link earlier from this browser. Losing this browser’s cookie loses early revocation access. Copies already saved by viewers cannot be recalled. Quotation caps do not establish licensing compliance or scientific correctness.</p><div className="sharing-actions"><button type="button" className="sharing-grant" disabled={!!busy || uncertain || !sourceCurrent || previewExpired || !capabilities?.sharing_available} onClick={() => void share()}>{busy === 'share' ? 'Saving public snapshot…' : 'Share this snapshot publicly'}</button><button type="button" disabled={!!busy || !!pending.current} onClick={() => setPreview(null)}>Close public preview</button></div></section>}
    {uncertain && <div className="sharing-actions"><button type="button" disabled={!!busy || phase === 'expired'} onClick={() => void share()}>{busy === 'share' ? 'Checking original share action…' : 'Check original share action'}</button><p className="public-map-note">Uses the exact same request and idempotency key. No new share action is created.</p></div>}
    <h3 className="public-section-title">Your saved public links</h3>{phase === 'ready' && !shares.length && <p>No public snapshot has been shared for this exact finding.</p>}
    {shares.length > 0 && <ul className="sharing-history" aria-label="Your public finding snapshots">{shares.map((record) => <li className="sharing-record" key={record.public_id}><SnapshotStatus status={record.status} /><h4>{publicTitle(record.projection)}</h4><p className="public-map-note">Shared {sharingDate(record.created_at)} · Expires {sharingDate(record.expires_at)}{record.revoked_at && ` · Revoked ${sharingDate(record.revoked_at)}`}</p><a className="sharing-record-link" href={record.public_path} target="_blank" rel="noopener noreferrer">{new URL(record.public_path, window.location.origin).href}</a><div className="sharing-actions"><button type="button" onClick={() => setSelected(selected === record.public_id ? null : record.public_id)}>{selected === record.public_id ? 'Hide shared snapshot' : 'Inspect shared snapshot'}</button><button type="button" disabled={record.status === 'revoked' || record.status === 'expired'} onClick={() => void copy(record)}>Copy public link</button><button type="button" disabled={!!busy || !!pending.current || record.status === 'revoked' || record.status === 'expired'} onClick={() => void revoke(record)}>{busy === `revoke:${record.public_id}` ? 'Revoking…' : 'Revoke this public link'}</button></div></li>)}</ul>}{cursor && <div className="sharing-actions"><button type="button" disabled={!!busy || !!pending.current} onClick={() => void more()}>Load earlier public links</button></div>}
    {selectedRecord && <section aria-label="Saved public snapshot inspection"><dl className="sharing-meta"><dt>Canonical projection SHA-256</dt><dd className="sharing-hash">{selectedRecord.projection_sha256}</dd></dl><PublicSnapshot projection={selectedRecord.projection} prefix={`saved-${selectedRecord.public_id}`} /></section>}
  </section>
}
