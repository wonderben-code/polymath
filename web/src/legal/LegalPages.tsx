import type { ReactNode } from 'react'
import { PublicShell } from '../sharing/PublicShell'
import './legal.css'

export const LEGAL_UPDATED = '1 November 2026'
export const CONTACT_EMAIL = 'hello@askpolymath.com'
const ISSUES = 'https://github.com/wonderben-code/polymath/issues'

function Legal({ eyebrow, title, lede, children }: { eyebrow: string; title: string; lede: string; children: ReactNode }) {
  return <PublicShell>
    <article className="legal" aria-labelledby="legal-title">
      <header className="legal-intro">
        <span className="eyebrow accent">{eyebrow}</span>
        <h1 id="legal-title">{title}</h1>
        <p className="legal-lede">{lede}</p>
        <p className="legal-updated">Last updated {LEGAL_UPDATED}</p>
      </header>
      {children}
      <footer className="legal-contact">
        <h2>Questions</h2>
        <p>Write to <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>, or open an issue at <a href={ISSUES} rel="noreferrer">github.com/wonderben-code/polymath</a>. Polymath was designed and built by Ekram Alam.</p>
        <p className="legal-also">{title === 'Privacy policy' ? <a href="/terms">Terms of use</a> : <a href="/privacy">Privacy policy</a>} · <a href="/">Back to Polymath</a></p>
      </footer>
    </article>
  </PublicShell>
}

export function PrivacyPage() {
  return <Legal eyebrow="PRIVACY" title="Privacy policy" lede="Polymath is a research tool, not an advertising business. It keeps only what it needs to run your research, never sells anything about you, and never sees a password.">
    <section aria-labelledby="privacy-short">
      <h2 id="privacy-short">In short</h2>
      <ul>
        <li>You can use everything as a guest. No account, email or name needed.</li>
        <li>No advertising, no analytics or tracking scripts, and no third-party cookies.</li>
        <li>Your research is private to you unless you choose to share or publish it.</li>
        <li>You can delete your account at any time, from the panel under your name.</li>
      </ul>
    </section>

    <section aria-labelledby="privacy-collect">
      <h2 id="privacy-collect">What Polymath keeps</h2>
      <dl className="legal-list">
        <dt>Your research</dt>
        <dd>The concepts, questions and problems you enter, and what Polymath produces for them: investigations, saved findings, the papers and passages they cite, maps, watches, formalisations and drafts. They belong to your browser session (or your account, if you sign in) and only you can open them.</dd>
        <dt>A session cookie</dt>
        <dd>One essential cookie, <code>polymath_session</code>, holds a random identifier so Polymath knows which research is yours. It is secure, HTTP-only and never shared. If you sign in, a second cookie, <code>polymath_guest</code>, keeps your device's guest work aside so it returns when you sign out. These cookies are needed for the service to work. The only other cookie belongs to the site operator's own desk.</dd>
        <dt>If you sign in</dt>
        <dd>The provider you chose (ORCID, GitHub or Google), its stable ID for you, a short display name and, for ORCID, your ORCID iD. Polymath asks providers only for your basic profile, not your email address. Their sign-in tokens are used once to read these details and then discarded.</dd>
        <dt>If you connect Zenodo</dt>
        <dd>The access Zenodo grants Polymath to create and publish records for you, stored encrypted (AES-256-GCM), and a record of what you published. Disconnecting deletes the stored access.</dd>
        <dt>Fair-use limits</dt>
        <dd>To keep free use fair, Polymath counts requests per visitor. It stores a keyed one-way fingerprint of your network, never your IP address itself.</dd>
        <dt>Server logs</dt>
        <dd>The web server keeps standard access logs (IP address, time, the page requested and browser type) to keep the service secure and working. They are rotated automatically and overwritten as new entries arrive, and are never used to profile anyone.</dd>
        <dt>In your browser only</dt>
        <dd>A few preferences, such as your evidence bar, notification choice and the author name you publish under, are kept in your browser's local storage and never sent to Polymath unless you publish.</dd>
      </dl>
    </section>

    <section aria-labelledby="privacy-share">
      <h2 id="privacy-share">Who else sees what</h2>
      <dl className="legal-list">
        <dt>Meta</dt>
        <dd>Polymath's research intelligence is Meta's AI (the Meta Model API). Your research text and the literature Polymath retrieves for it are sent to Meta to run each investigation. Your name, account details and cookies are not.</dd>
        <dt>Literature sources</dt>
        <dd>Search terms chosen during your research are sent to OpenAlex and arXiv to find and read papers.</dd>
        <dt>Sign-in providers and Zenodo</dt>
        <dd>When you sign in or connect Zenodo, you approve Polymath on that provider's own site, under its own privacy policy. Anything you publish to Zenodo is public under the licence you choose, and Zenodo records with a DOI are permanent.</dd>
        <dt>Sharing you choose</dt>
        <dd>If you create a public link or add a finding to the public library, that finding becomes visible to anyone with the link, for a limited time, and you can withdraw it.</dd>
        <dt>Hosting</dt>
        <dd>Polymath runs on a DigitalOcean server in Singapore, with secure HTTPS connections throughout.</dd>
      </dl>
      <p>Polymath never sells or rents personal information and never uses it for advertising. It would disclose information only if the law required it.</p>
    </section>

    <section aria-labelledby="privacy-keep">
      <h2 id="privacy-keep">How long it is kept</h2>
      <ul>
        <li><b>As a guest:</b> your session lasts 7 days from when it began. After that its research can no longer be opened from any browser, and it may be deleted from the server.</li>
        <li><b>Signed in:</b> your research is kept for a year from each sign-in, so it stays while you use it.</li>
        <li><b>Delete my account</b> removes your account details at once. The research in the browser you are using becomes guest work again for 7 days.</li>
        <li><b>Disconnect</b> in Publishing deletes Polymath's stored Zenodo access at once.</li>
      </ul>
    </section>

    <section aria-labelledby="privacy-rights">
      <h2 id="privacy-rights">Your choices and rights</h2>
      <p>You can use Polymath without signing in, delete your account, disconnect Zenodo and withdraw anything you have shared, all from inside Polymath. To ask what Polymath holds about you, or to have something corrected or deleted, write to the address below. Requests are answered within 30 days. Polymath is not directed at children under 16.</p>
      <p>If this policy changes, the date at the top changes too, and a significant change will be noted on the site.</p>
    </section>
  </Legal>
}

export function TermsPage() {
  return <Legal eyebrow="TERMS" title="Terms of use" lede="Polymath is a free research partner that looks for connections across fields of science. These terms are short and plain, and using Polymath means you accept them.">
    <section aria-labelledby="terms-use">
      <h2 id="terms-use">Using Polymath</h2>
      <ul>
        <li>You may use Polymath as a guest or signed in, for any lawful research purpose.</li>
        <li>Free use has fair-use limits, shown on the site. Polymath may change or pause free use, for example when its research budget is spent.</li>
        <li>Please don't misuse the service: no attempts to break its security or limits, no automated mass use that bypasses the published API and its limits, and nothing unlawful, harmful or that infringes others' rights.</li>
        <li>Polymath may limit or stop access for anyone who breaks these terms.</li>
      </ul>
    </section>

    <section aria-labelledby="terms-results">
      <h2 id="terms-results">What the results are, and are not</h2>
      <p>Findings are generated by AI from the literature Polymath retrieves. Every claim is tied to the passage behind it, and Polymath argues against its own connections, but results can still be incomplete or wrong. They are starting points for research, not established fact, and not medical, legal, financial or other professional advice. Check the cited sources before relying on anything, and use your own judgement.</p>
      <p>Labels such as <i>Known</i>, <i>Postulated</i>, <i>Speculative</i>, <i>found</i> and <i>forged</i> describe the evidence Polymath saw, not a guarantee.</p>
    </section>

    <section aria-labelledby="terms-yours">
      <h2 id="terms-yours">Your work</h2>
      <ul>
        <li>What you enter, and what Polymath produces for you, is yours to use, subject to the rights of the authors whose work it cites.</li>
        <li>Cited papers and passages remain their authors' and publishers' work; follow their licences when you reuse them.</li>
        <li>When you publish to Zenodo, you publish under your own name and account, and you are responsible for what you publish and the licence you choose. Polymath publishes only at the level of automation you choose, and asks you to confirm before it first publishes anything for real.</li>
      </ul>
    </section>

    <section aria-labelledby="terms-service">
      <h2 id="terms-service">The service</h2>
      <p>Polymath is provided free, as it is and as available, without warranties of any kind. It may change, pause or end. To the fullest extent the law allows, its maker is not liable for any loss arising from using it or relying on its results. Nothing in these terms limits rights you have under law that cannot be limited.</p>
      <p>Signing in and publishing also use ORCID, GitHub, Google and Zenodo, under their own terms. How Polymath handles your information is described in the <a href="/privacy">privacy policy</a>.</p>
      <p>If these terms change, the date at the top changes too. Continuing to use Polymath after a change means you accept the updated terms.</p>
    </section>
  </Legal>
}
