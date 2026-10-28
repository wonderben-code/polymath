import { useEffect, useState } from 'react'
import { getPublicFinding, SharingError } from './client'
import type { PublicFinding } from './types'
import { PublicShell, PublicUnavailable } from './PublicShell'
import { PublicSnapshot, sharingDate, SnapshotStatus } from './PublicSnapshot'

export function PublicFindingPage({ publicId }: { publicId: string | null }) {
  const [finding, setFinding] = useState<PublicFinding | null>(null), [phase, setPhase] = useState<'loading' | 'ready' | 'missing' | 'error'>('loading'), [refresh, setRefresh] = useState(0)
  useEffect(() => {
    setFinding(null); setPhase('loading')
    if (!publicId) { setPhase('missing'); return }
    const controller = new AbortController(); let active = true
    const timeout = window.setTimeout(() => { controller.abort(); if (active) { setFinding(null); setPhase('error') } }, 10000)
    getPublicFinding(publicId, controller.signal).then((saved) => {
      if (active && !controller.signal.aborted) { if (Date.parse(saved.expires_at) <= Date.now()) setPhase('missing'); else { setFinding(saved); setPhase('ready') } }
    }).catch((error: unknown) => { if (active && !controller.signal.aborted) { setFinding(null); setPhase(error instanceof SharingError && error.code === 'sharing_not_found' ? 'missing' : 'error') } }).finally(() => window.clearTimeout(timeout))
    return () => { active = false; controller.abort(); window.clearTimeout(timeout) }
  }, [publicId, refresh])
  useEffect(() => {
    if (!finding) return
    const timer = window.setTimeout(() => { setFinding(null); setPhase('missing') }, Math.max(0, Date.parse(finding.expires_at) - Date.now()))
    return () => window.clearTimeout(timer)
  }, [finding])
  return <PublicShell>{phase === 'loading' && <p role="status">Loading the shared finding…</p>}{phase === 'missing' && <PublicUnavailable />}{phase === 'error' && <section className="public-empty" role="alert"><h1>The shared finding could not be loaded.</h1><p>Check the connection and try reading it again.</p><button className="public-reload" type="button" onClick={() => setRefresh((value) => value + 1)}>Reload shared finding</button></section>}
    {finding && <><div className="public-intro"><span className="eyebrow accent">AN EXPLICITLY SHARED SNAPSHOT</span><h1>Knowledge worth questioning.</h1><SnapshotStatus status={finding.status} /><p>Shared {sharingDate(finding.created_at)} · Available until {sharingDate(finding.expires_at)}.</p>{finding.status === 'superseded' && <p className="sharing-warning">This is a historical snapshot. A different finding is now current. Any newer private finding remains private; the shared content below has not been replaced.</p>}{finding.status === 'needs_review' && <p className="sharing-warning">The saved evidence for this snapshot needs review. The shared content remains the originally approved snapshot; treat its interpretation with caution.</p>}{finding.status === 'unsupported' && <p className="sharing-warning">The latest model assessment no longer supports this exact finding. Its originally shared text remains visible as a historical snapshot, with its original uncertainty.</p>}<button type="button" className="public-reload" onClick={() => setRefresh((value) => value + 1)}>Reload shared status</button></div><PublicSnapshot projection={finding.projection} /><details><summary>Shared snapshot integrity</summary><dl className="sharing-meta"><dt>Canonical public projection SHA-256</dt><dd className="sharing-hash">{finding.projection_sha256}</dd><dt>Availability ends</dt><dd>{sharingDate(finding.expires_at)}</dd></dl><p className="public-map-note">The content hash identifies this exact public snapshot. Current status and expiration are separate from that immutable content.</p></details></>}
  </PublicShell>
}
