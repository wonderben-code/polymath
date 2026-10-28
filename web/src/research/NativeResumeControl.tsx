import { useEffect, useRef, useState } from 'react'
import { getResearchRun, ResearchApiError, resumeNativeResearchRun } from './client'
import type { ResearchRunSummary } from './types'
import { useNativeResearchSubmission } from './nativeContext'

/** Continues only the original native task under its original still-live approval. */
export function NativeResumeControl({ run, csrfToken, onResumed, onSessionExpired }: { run: Pick<ResearchRunSummary, 'id' | 'status' | 'native_research'>; csrfToken: string | null; onResumed: () => void; onSessionExpired: () => void }) {
  const native = useNativeResearchSubmission(), [checked, setChecked] = useState<typeof run | null>(null), [busy, setBusy] = useState(false), [unknown, setUnknown] = useState(false), [error, setError] = useState('')
  const generation = useRef(0), locked = useRef(false), request = useRef<AbortController | null>(null), callbacks = useRef({ onResumed, onSessionExpired })
  callbacks.current = { onResumed, onSessionExpired }
  useEffect(() => { generation.current++; request.current?.abort(); locked.current = false; setChecked(null); setBusy(false); setUnknown(false); setError(''); return () => { generation.current++; request.current?.abort() } }, [run.id, run.status, run.native_research?.expected_lease_token, csrfToken])
  const actual = checked ?? run, control = actual.native_research
  const matches = !!control && (native.canResume ?? native.canSubmit) && native.submission?.expectedActivationSha256 === control.activation_sha256
  async function act(readOnly: boolean) {
    if (locked.current || !csrfToken || !control || !readOnly && (!matches || !control.can_resume || unknown || actual.status !== 'interrupted')) return
    locked.current = true; setBusy(true); setError(''); const scope = generation.current, controller = new AbortController(); request.current = controller
    try {
      const next = readOnly ? await getResearchRun(actual.id, controller.signal) : await resumeNativeResearchRun(actual.id, control.expected_lease_token, csrfToken, native.submission!, controller.signal)
      if (scope !== generation.current || controller.signal.aborted) return
      setChecked(next); setUnknown(false); callbacks.current.onResumed()
    } catch (caught) {
      if (scope !== generation.current || controller.signal.aborted) return
      const code = caught instanceof ResearchApiError ? caught.code : 'connection_failed'
      if (['session_expired', 'session_changed'].includes(code)) { setChecked(null); callbacks.current.onSessionExpired() }
      else if (code === 'resource_limited' || code === 'queue_limit') setError(`Continuation was limited. Wait${caught instanceof ResearchApiError && caught.retryAfterSeconds !== null ? ` at least ${caught.retryAfterSeconds} seconds` : ''} before trying again; no task was resent.`)
      else if (code === 'native_authority_changed') { native.invalidate(); setError('The original approval is no longer current. No new authority, deadline, or model request was created.') }
      else { if (!readOnly) setUnknown(true); setError('The continuation response could not be confirmed. Read the saved task status before considering another action.') }
    } finally { if (scope === generation.current) { locked.current = false; setBusy(false) } }
  }
  if (!run.native_research || actual.status !== 'interrupted') return null
  return <section className="native-resume" aria-label="Continue interrupted native task"><p>This task can continue only from its saved checkpoint, under the same live funding approval. Continuation preserves its original input, remaining budget, deadline, and controller limits.</p>{!control?.can_resume && <p>Continuation is blocked while a prepared request, unknown charge, or other unresolved state remains. No automatic resend is available.</p>}{control?.can_resume && !matches && <p>Reload the current research approval. A different or expired activation cannot authorize this earlier task.</p>}{error && <p role="alert">{error}</p>}<div className="budget-actions">{unknown ? <button type="button" disabled={busy || !csrfToken} onClick={() => void act(true)}>Read saved task status</button> : <button type="button" disabled={busy || !csrfToken || !matches || !control?.can_resume} onClick={() => void act(false)}>{busy ? 'Checking the original continuation…' : 'Continue this saved task'}</button>}</div></section>
}
