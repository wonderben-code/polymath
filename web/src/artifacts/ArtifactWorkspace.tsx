import { useEffect, useId, useState } from 'react'
import { exportUrl } from './client'
import type { ArtifactKind, ArtifactPassage, CitedText, DocumentDraft, FiniteCheckResult, FormalisationDraft, SavedArtifact } from './types'
import { useArtifactWorkspace } from './useArtifactWorkspace'
import './artifacts.css'

interface ArtifactWorkspaceProps {
  sourceResultId: string
  sourceKind: 'bridge' | 'answer'
  csrfToken: string | null
  enabled: boolean
  canGenerate: boolean
  onSessionExpired: () => void
  onBusyChange?: (busy: boolean) => void
}

const taskNames: Record<ArtifactKind, string> = { formalise: 'Conjecture', write_summary: 'Plain summary', write_paper: 'Research paper' }
const jobNames: Record<string, string> = {
  queued: 'Queued', running: 'Working', waiting_retry: 'Waiting', cancel_requested: 'Cancelling',
  cancelled: 'Cancelled', succeeded: 'Complete', failed: 'Stopped', interrupted: 'Interrupted',
}
const connectionNames = { connecting: 'Connecting to activity', live: 'Live activity', reconnecting: 'Reconnecting', closed: 'Saved activity', error: 'Activity connection unavailable' }
const operationalEvents = new Set(['checkpoint', 'external_started', 'external_observed', 'external_reconciled'])

function safeUrl(value: string): string | null {
  try {
    const parsed = new URL(value)
    return parsed.protocol === 'https:' && !parsed.username && !parsed.password ? parsed.href : null
  } catch { return null }
}

function PassageLink({ passage }: { passage: ArtifactPassage }) {
  const url = safeUrl(passage.url)
  return url ? <a href={url} target="_blank" rel="noopener noreferrer">{passage.title}<span aria-hidden="true"> ↗</span></a> : <span>{passage.title}</span>
}

function Paragraph({ paragraph, artifactId }: { paragraph: CitedText; artifactId: string }) {
  return <div className={`artifact-paragraph artifact-claim-${paragraph.label}`}>
    <span className="artifact-label">{paragraph.label === 'known' ? 'Known' : paragraph.label === 'postulated' ? 'Postulated' : 'Speculative'}</span>
    <p>{paragraph.text}<span className="artifact-citations">{paragraph.citation_ordinals.map((ordinal) => <a key={ordinal} href={`#artifact-passage-${artifactId}-${ordinal}`} aria-label={`Read source passage ${ordinal + 1}`}>[{ordinal + 1}]</a>)}</span></p>
  </div>
}

function Check({ check, number }: { check: FiniteCheckResult; number: number }) {
  const labels: Record<FiniteCheckResult['status'], string> = {
    counterexample_found: 'Counterexample to the encoded statement',
    no_counterexample_in_domain: 'No counterexample in the finite domain',
    no_applicable_cases: 'No cases satisfied the assumptions',
    limit_reached: 'The bounded check stopped at its limit',
  }
  return <article className={`artifact-check check-${check.status}`} aria-label={`Finite check ${number}`}>
    <h5>{labels[check.status]}</h5>
    <p>{check.applicable_cases} applicable {check.applicable_cases === 1 ? 'case' : 'cases'} checked · {check.assignments_tested} of {check.total_assignments} assignments visited</p>
    <dl className="artifact-domain">{check.conjecture.variables.map((variable) => <div key={variable.name}><dt>{variable.name}</dt><dd>Integers {variable.lower} to {variable.upper}</dd></div>)}</dl>
    {check.witness && <div className="artifact-witness"><h6>Counterexample</h6><dl>{Object.entries(check.witness).map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl>{check.lhs !== null && check.rhs !== null && <p>Left side: <code>{check.lhs}</code><br />Right side: <code>{check.rhs}</code></p>}</div>}
    <p className="artifact-check-scope">This checks a finite, model-generated encoding. It is not a general proof or an independent verification of the written conjecture.</p>
    <details><summary>Inspect the checked statement</summary><pre>{JSON.stringify(check.conjecture, null, 2)}</pre><p className="artifact-hash">Statement hash: {check.conjecture_sha256}</p></details>
  </article>
}

function Formalisation({ draft, artifact }: { draft: FormalisationDraft; artifact: SavedArtifact }) {
  return <>
    <div className="artifact-open-status"><span className="artifact-status-dot" aria-hidden="true" /><strong>Open conjecture · proof attempt unverified</strong><p>The definitions and mathematical statement below are generated interpretations of the cited research. Bounded checks cannot establish a general proof.</p></div>
    <section className="artifact-document-section"><h4>Definitions</h4><dl className="artifact-definitions">{draft.definitions.map((definition) => <div key={definition.symbol}><dt>{definition.symbol}</dt><dd><Paragraph paragraph={definition.meaning} artifactId={artifact.id} /></dd></div>)}</dl></section>
    <section className="artifact-document-section"><h4>Assumptions</h4>{draft.assumptions.map((paragraph, index) => <Paragraph key={index} paragraph={paragraph} artifactId={artifact.id} />)}</section>
    <section className="artifact-document-section artifact-conjecture"><h4>Conjecture</h4><Paragraph paragraph={draft.conjecture} artifactId={artifact.id} /></section>
    <section className="artifact-document-section"><h4>Proof attempt</h4><p className="artifact-scope">A generated argument to examine, with its gaps retained.</p>{draft.proof_attempt.map((paragraph, index) => <Paragraph key={index} paragraph={paragraph} artifactId={artifact.id} />)}</section>
    <section className="artifact-document-section"><h4>Bounded mathematical checks</h4>{artifact.payload.checks.length ? artifact.payload.checks.map((check, index) => <Check key={`${check.conjecture_sha256}-${index}`} check={check} number={index + 1} />) : <p className="artifact-scope">No bounded mathematical check was performed for this conjecture.</p>}</section>
  </>
}

function Document({ draft, artifactId }: { draft: DocumentDraft; artifactId: string }) {
  return <>
    <section className="artifact-document-section"><h4>{draft.kind === 'paper' ? 'Abstract' : 'At a glance'}</h4>{draft.abstract.map((paragraph, index) => <Paragraph key={index} paragraph={paragraph} artifactId={artifactId} />)}</section>
    {draft.sections.map((section) => <section key={section.heading} className="artifact-document-section"><h4>{section.heading}</h4>{section.paragraphs.map((paragraph, index) => <Paragraph key={index} paragraph={paragraph} artifactId={artifactId} />)}</section>)}
  </>
}

function Preview({ artifact }: { artifact: SavedArtifact }) {
  const draft = artifact.payload.draft
  if (!draft) return <div className="artifact-empty-outcome"><h3>{artifact.status === 'insufficient_evidence' ? 'More evidence is needed' : artifact.status === 'limit_reached' ? 'Generation reached its limit' : 'No complete document was produced'}</h3><p>{artifact.payload.reason || 'The task stopped before it could produce a supported complete draft. The recorded activity is retained.'}</p></div>
  return <article className="artifact-preview" aria-label="Saved document preview">
    <header className="artifact-preview-heading"><span className="eyebrow accent">{draft.kind === 'formalisation' ? 'MATHEMATICAL CONJECTURE' : draft.kind === 'paper' ? 'RESEARCH PAPER' : 'PLAIN-ENGLISH SUMMARY'}</span><h3>{draft.title}</h3><p>Source-bound draft · {new Date(artifact.created_at).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })}</p></header>
    <p className="artifact-scope">Claim labels describe the model’s assessment. Checked citations establish passage integrity, not scientific truth or independent corroboration.</p>
    {draft.kind === 'formalisation' ? <Formalisation draft={draft} artifact={artifact} /> : <Document draft={draft} artifactId={artifact.id} />}
    <section className="artifact-document-section"><h4>{draft.kind === 'formalisation' ? 'Limitations and open work' : 'Scope and remaining limits'}</h4><ul>{draft.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul></section>
    {artifact.formalisation_id && <p className="artifact-scope">This document incorporates a saved conjecture. Its mathematical arguments retain their unverified status.</p>}
    {artifact.formalisation_id && artifact.formalisation_checks.length > 0 && <section className="artifact-document-section"><h4>Checks from the saved conjecture</h4><p className="artifact-scope">These are the recorded checks of the exact conjecture version included in this document. Their outcomes remain visible alongside the generated account.</p>{artifact.formalisation_checks.map((check, index) => <Check key={`${check.conjecture_sha256}-${index}`} check={check} number={index + 1} />)}</section>}
    <section className="artifact-document-section artifact-references"><h4>Original source passages</h4><p className="artifact-scope">Each citation opens an exact passage from the saved finding’s original evidence.</p><ol aria-label="Document source passages">{artifact.passages.map((passage) => <li key={passage.ordinal} id={`artifact-passage-${artifact.id}-${passage.ordinal}`} tabIndex={-1} value={passage.ordinal + 1}>
      <div className="artifact-reference-heading"><PassageLink passage={passage} /><span>{passage.content_kind === 'abstract' ? 'Abstract only' : 'Full-text snapshot'}</span></div>
      <blockquote>{passage.quote}</blockquote><p className="artifact-reference-support">Recorded association: {passage.supports}</p>
      <details><summary>Saved source version</summary><dl><dt>Source</dt><dd>{passage.source_id}</dd><dt>Content hash</dt><dd>{passage.content_sha256}</dd></dl></details>
    </li>)}</ol></section>
    <footer className="artifact-export"><a className="artifact-download" href={exportUrl(artifact.id)} download>Download saved Markdown<span aria-hidden="true"> ↓</span></a><p>The download contains the exact saved document, citations, and limitations.</p><details><summary>Document version</summary><dl><dt>Document hash</dt><dd>{artifact.markdown_sha256}</dd><dt>Source finding</dt><dd>{artifact.source_result_id}</dd><dt>Source finding hash</dt><dd>{artifact.source_sha256}</dd></dl></details></footer>
  </article>
}

export function ArtifactWorkspace({ sourceResultId, sourceKind, csrfToken, enabled, canGenerate, onSessionExpired, onBusyChange }: ArtifactWorkspaceProps) {
  const headingId = useId()
  const workspace = useArtifactWorkspace({ sourceResultId, csrfToken, enabled, canGenerate, onSessionExpired })
  const [formalisationId, setFormalisationId] = useState<string | null>(null)
  const [includeConjecture, setIncludeConjecture] = useState(true)
  const visibleArtifact = workspace.artifact?.source_result_id === sourceResultId ? workspace.artifact : null
  const activity = (workspace.events.length ? workspace.events : visibleArtifact?.payload.events ?? []).filter((event) => !operationalEvents.has(event.kind))
  const canCancel = workspace.activeJob && ['queued', 'running', 'waiting_retry', 'interrupted'].includes(workspace.activeJob.status)

  useEffect(() => { setFormalisationId(null); setIncludeConjecture(true) }, [sourceResultId])

  useEffect(() => {
    if (visibleArtifact?.kind === 'formalise' && visibleArtifact.status === 'ready') {
      setFormalisationId(visibleArtifact.id)
      setIncludeConjecture(true)
    }
  }, [visibleArtifact?.id, visibleArtifact?.kind, visibleArtifact?.status])

  useEffect(() => {
    onBusyChange?.(workspace.busy)
    return () => onBusyChange?.(false)
  }, [workspace.busy, onBusyChange])

  return <section className="artifact-workspace" aria-labelledby={headingId}>
    <header className="artifact-heading"><div><span className="eyebrow accent">DEVELOP THE FINDING</span><h2 id={headingId}>Make it precise. Write it clearly.</h2><p>Turn this saved finding into a mathematical conjecture, a plain-English summary, or a research paper. Every draft stays linked to its original evidence.</p></div><button type="button" className="text-button" disabled={!enabled || workspace.phase === 'loading'} onClick={workspace.refresh}>Reload saved work</button></header>
    <div className="artifact-actions" role="group" aria-label="Develop this finding">
      <button type="button" disabled={!workspace.canCreate || sourceKind !== 'bridge'} onClick={() => void workspace.create('formalise')}><span>01</span><strong>Formalise</strong><small>Definitions, conjecture, and a proof attempt</small></button>
      <button type="button" disabled={!workspace.canCreate} onClick={() => void workspace.create('write_summary', includeConjecture ? formalisationId ?? undefined : undefined)}><span>02</span><strong>Write summary</strong><small>A clear account of the finding and its limits</small></button>
      <button type="button" disabled={!workspace.canCreate} onClick={() => void workspace.create('write_paper', includeConjecture ? formalisationId ?? undefined : undefined)}><span>03</span><strong>Write paper</strong><small>Background, findings, evidence, and limitations</small></button>
    </div>
    {sourceKind === 'answer' && <p className="artifact-scope">Summaries and papers are available for answers. Formalise starts from a saved bridge.</p>}
    {!canGenerate && <p className="artifact-availability">Generation is currently unavailable. Saved documents can still be inspected and downloaded.</p>}
    {formalisationId && <label className="artifact-include-conjecture"><input type="checkbox" checked={includeConjecture} disabled={workspace.busy} onChange={(event) => setIncludeConjecture(event.target.checked)} /><span>Include the saved conjecture in writing<small>Its assumptions and proof attempt will remain explicitly unverified.</small></span></label>}
    {workspace.phase === 'loading' && <p className="artifact-message" role="status">Loading saved conjectures and documents…</p>}
    {workspace.error && <div className="artifact-message artifact-error" role="alert"><p>{workspace.error}</p>{workspace.phase !== 'expired' && <button type="button" onClick={workspace.refresh}>Reload saved work to check</button>}</div>}
    {workspace.phase === 'ready' && workspace.jobs.length === 0 && <p className="artifact-message">No conjecture or document has been generated from this finding yet.</p>}
    {workspace.jobs.length > 0 && <nav className="artifact-history" aria-label="Saved conjectures and documents"><ul>{workspace.jobs.map((job) => <li key={job.id}><button type="button" aria-pressed={workspace.selectedId === job.id} onClick={() => workspace.selectJob(job.id)}><strong>{taskNames[job.task_kind]}</strong><span>{jobNames[job.status]} · {new Date(job.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</span></button></li>)}</ul>{workspace.nextCursor && <button type="button" className="text-button" disabled={workspace.loadingMore} onClick={() => void workspace.loadMore()}>{workspace.loadingMore ? 'Loading earlier work…' : 'Load earlier documents'}</button>}</nav>}
    {workspace.activeJob && <div className="artifact-job-status"><div><span className="artifact-job-label">{taskNames[workspace.activeJob.task_kind]} · {jobNames[workspace.activeJob.status]}</span><p>{workspace.activeJob.status === 'interrupted' ? 'This task stopped before completion. Its saved evidence is retained. Cancel it to begin another task.' : workspace.activeJob.status === 'cancel_requested' ? 'Cancellation is being confirmed. Saved work is retained.' : workspace.activeJob.status === 'cancelled' ? 'This task was cancelled. No further generation will be started by it.' : workspace.activeJob.status === 'succeeded' ? 'The recorded outcome is saved below.' : workspace.activeJob.status === 'failed' ? 'This task stopped. Its recorded outcome and activity are retained.' : 'The task continues if you browse another finding or close the page.'}</p></div>{canCancel && <button type="button" disabled={workspace.cancelling} onClick={() => void workspace.cancel()}>{workspace.cancelling ? 'Requesting cancellation…' : 'Cancel this task'}</button>}</div>}
    {(workspace.activeJob || activity.length > 0) && <details className="artifact-activity" open={!visibleArtifact}><summary>Recorded generation activity<span>{connectionNames[workspace.connection]}</span></summary>{activity.length ? <ol aria-label="Recorded generation activity">{activity.map((event) => <li key={event.sequence}>{event.summary}</li>)}</ol> : <p>No recorded generation activity yet.</p>}</details>}
    {workspace.selectedId && !workspace.activeJob && workspace.phase === 'ready' && <p className="artifact-message" role="status">Loading this saved task…</p>}
    {visibleArtifact && <Preview artifact={visibleArtifact} />}
  </section>
}
