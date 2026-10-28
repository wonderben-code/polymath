import type { ReactNode } from 'react'
import './sharing.css'

export function PublicShell({ children }: { children: ReactNode }) {
  return <><a className="skip-link" href="#public-content">Skip to shared knowledge</a><div className="page-shell public-shell">
    <header className="site-header"><a className="brand" href="/" aria-label="Polymath home"><span className="brand-symbol" aria-hidden="true">p<span>·</span></span><span>polymath</span></a><div className="header-note">An open field of thought<span className="header-rule" />Shared knowledge</div><nav className="public-navigation" aria-label="Polymath navigation"><a href="/library">Public library</a><a href="/">Research desk</a></nav></header>
    <main id="public-content">{children}</main><footer className="site-footer"><span>Polymath · Open questions, shared knowledge.</span><span>Shared model assessments · Keep the questions open.</span></footer>
  </div></>
}

export function PublicUnavailable() {
  return <section className="public-empty" aria-labelledby="public-unavailable-title"><span className="eyebrow accent">SHARED KNOWLEDGE</span><h1 id="public-unavailable-title">This shared finding is unavailable.</h1><p>Public snapshots are available for a limited time and can be withdrawn by their owner.</p><a href="/library">Browse the public library</a></section>
}
