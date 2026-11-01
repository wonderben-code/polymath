export const OWN_KEY_GUIDE = 'https://github.com/wonderben-code/polymath/blob/main/docs/RUN_YOUR_OWN.md'

/** The unlimited way to use Polymath: your own copy, your own Meta key. */
export function OwnKeyCard({ compact = false, ended = false }: { compact?: boolean; ended?: boolean }) {
  if (compact) return (
    <p className="own-key-line">
      {ended ? 'Keep going without limits: ' : 'Unlimited use: '}
      <a href={OWN_KEY_GUIDE} target="_blank" rel="noopener noreferrer">run your own Polymath with your own Meta key<span className="research-sr-only"> (opens in a new tab)</span></a>. Open source; the key stays on your server.
    </p>
  )
  return (
    <aside className="own-key" aria-labelledby="own-key-title">
      <span className="own-key-kicker">{ended ? 'Free use has ended · unlimited option' : 'Unlimited · open source'}</span>
      <h2 id="own-key-title">Run Polymath with your own Meta key</h2>
      <p>{ended ? 'The free allowance here is used up for now. ' : 'The free use here is limited, because Polymath pays for it. '}Your own copy has no limits: it runs on your own small server with your own Meta Model API key, which never leaves it. Setup takes about fifteen minutes, and it is free apart from your Meta usage.</p>
      <a className="own-key-link" href={OWN_KEY_GUIDE} target="_blank" rel="noopener noreferrer">Set up your own Polymath<span className="research-sr-only"> (opens in a new tab)</span></a>
    </aside>
  )
}

const ENDED = new Set(['public_research_authority_expired', 'public_research_stopped', 'public_research_budget_exhausted', 'public_research_daily_budget_exhausted', 'public_research_session_budget_exhausted', 'public_research_session_daily_budget_exhausted', 'public_research_disabled'])

/** True when a visitor cannot start free research right now for a funding reason. */
export function freeUseEnded(reason: string | null | undefined, remainingJobs: number | null | undefined, liveDisabled: boolean): boolean {
  return liveDisabled || (reason != null && ENDED.has(reason)) || remainingJobs === 0
}
