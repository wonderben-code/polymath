import type { FindingHead } from './types'
import './assessments.css'

export interface FindingVersionNoticeProps {
  head: FindingHead | null
  phase: 'idle' | 'loading' | 'ready' | 'error' | 'expired'
  pollPaused?: boolean
  onRefresh: () => void
  onOpenRun?: (id: string) => void
  onOpenFinding?: (id: string) => void
}
export function FindingVersionNotice({ head, phase, pollPaused = false, onRefresh, onOpenRun, onOpenFinding }: FindingVersionNoticeProps) {
  if (phase === 'idle') return null
  const current = phase === 'ready' && !!head && !pollPaused
  return <section className={`finding-version-notice${!current || !head?.can_generate ? ' finding-version-warning' : ''}`} aria-label="Finding version status">
    <div className="assessment-row"><strong>{phase === 'loading' ? 'Checking finding version…' : !current ? 'Current finding status unavailable' : head.state === 'unsupported' ? 'Unsupported by the latest model assessment' : head.is_historical ? 'Historical finding · a newer version is current' : 'Current supported model assessment'}</strong><button type="button" onClick={onRefresh} disabled={phase === 'loading' || phase === 'expired'}>Refresh finding status</button></div>
    {!current ? <p>{phase === 'expired' ? 'This browser session has expired or changed. Reload saved research to inspect private versions.' : pollPaused ? 'Automatic version checks have paused. Refresh this finding’s status before generating or publishing new work.' : phase === 'loading' ? 'New writing, formalisation, and publication wait for the saved version check.' : 'The current finding head could not be verified. New writing, formalisation, and publication are unavailable until it can be checked.'}</p> : <>
      <p>{head.state === 'unsupported' ? 'A cited reassessment no longer supports this finding. Its earlier text and documents remain saved as historical material; they cannot authorize new generation or publication.' : head.is_historical ? 'This saved finding is preserved for inspection. New generation and publication must use the current supported version.' : 'This is the current saved finding. “Supported” records the model’s assessment of the cited evidence; it does not establish scientific truth.'}</p>
      <p className="assessment-note">Recorded head revision {head.revision}. Earlier findings and source versions remain accessible.</p>
      {head.current_result_id !== head.requested_result_id && (onOpenFinding || onOpenRun) && <button type="button" onClick={() => onOpenFinding ? onOpenFinding(head.current_result_id) : onOpenRun?.(head.current_run_id)}>Open current finding version</button>}
    </>}
  </section>
}
