const GUIDE = 'https://github.com/wonderben-code/polymath/blob/main/docs/RUN_YOUR_OWN.md'

/** The three ways to use Polymath, each stated plainly. */
export function WaysToUse({ remaining, daily }: { remaining?: number | null; daily?: number | null }) {
  const allowance = remaining != null && daily != null ? `${remaining} of ${daily} free investigations left today in this browser.` : 'A few free investigations a day in each browser, while free use is open.'
  return (
    <section className="about-section ways" id="ways" aria-labelledby="ways-title">
      <h2 id="ways-title">Ways to use Polymath</h2>
      <ul className="ways-list">
        <li className="ways-card">
          <span className="ways-kicker">Free · limited daily use</span>
          <h3>Use it free</h3>
          <p>{allowance} Polymath pays for these, so each browser has a daily limit.</p>
        </li>
        <li className="ways-card">
          <span className="ways-kicker">Unlimited · available now</span>
          <h3>Run your own Polymath</h3>
          <p>Polymath is open source. Run your own copy with your own Meta key, which stays on your own server and is never shared with anyone, including us.</p>
          <a href={GUIDE} target="_blank" rel="noopener noreferrer">Read the step-by-step guide<span className="research-sr-only"> (opens in a new tab)</span></a>
        </li>
        <li className="ways-card is-proposed">
          <span className="ways-kicker">A vision · not a feature yet</span>
          <h3>Connect your Meta account</h3>
          <p>An idea for how Meta and Polymath could work together. One sign-in with Meta, and your own Meta allowance would power your research; Meta would handle the connection, so Polymath would never see your key. Not available today.</p>
        </li>
      </ul>
    </section>
  )
}
