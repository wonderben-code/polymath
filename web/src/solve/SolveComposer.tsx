import { useState } from 'react'
import type { FormEvent } from 'react'
import { createSolve, SolveApiError } from './client'
import type { SolveProgramme } from './client'

interface Props { csrfToken: string | null; ready: boolean; canSubmit: boolean; onCreated: (programme: SolveProgramme) => void; onSessionExpired: () => void }

export function SolveComposer({ csrfToken, ready, canSubmit, onCreated, onSessionExpired }: Props) {
  const [problem, setProblem] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submit(event: FormEvent) {
    event.preventDefault()
    const text = problem.trim()
    if (!text || !csrfToken || busy) return
    setBusy(true); setError('')
    try { onCreated(await createSolve(csrfToken, { problem: text })); setProblem('') } catch (failure) {
      const code = failure instanceof SolveApiError ? failure.code : 'unavailable'
      if (code === 'session_expired') onSessionExpired()
      setError(code === 'solve_limit_reached' ? 'This browser already has the most problems it can hold. Open an earlier one below.' : 'Solve could not be started just now. Please try again.')
    } finally { setBusy(false) }
  }

  return (
    <form className="solve-composer" onSubmit={(event) => void submit(event)}>
      <label htmlFor="solve-problem" className="composer-label">Describe the problem you want solved</label>
      <div className="prompt-box">
        <textarea id="solve-problem" value={problem} onChange={(event) => setProblem(event.target.value)} rows={3} maxLength={2000} placeholder="A problem, as precisely as you can: what you need, and what you have tried…" />
        <div className="prompt-footer"><span /><button type="submit" className="primary-button" disabled={!ready || !canSubmit || !csrfToken || !problem.trim() || busy}>{busy ? 'Starting…' : 'Find or forge a key'} <span aria-hidden="true">→</span></button></div>
      </div>
      {error && <p className="composer-alert" role="alert">{error}</p>}
    </form>
  )
}
