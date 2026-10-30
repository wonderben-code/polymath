import type { ReactNode } from 'react'
import { SiteFooter, SiteHeader } from '../shell/SiteChrome'
import './sharing.css'

export function PublicShell({ children }: { children: ReactNode }) {
  return <><a className="skip-link" href="#public-content">Skip to shared knowledge</a><div className="page-shell calm public-shell">
    <SiteHeader />
    <main id="public-content">{children}</main><SiteFooter />
  </div></>
}

export function PublicUnavailable() {
  return <section className="public-empty" aria-labelledby="public-unavailable-title"><span className="eyebrow accent">SHARED KNOWLEDGE</span><h1 id="public-unavailable-title">This shared finding is unavailable.</h1><p>Public snapshots are available for a limited time and can be withdrawn by their owner.</p><a href="/library">Browse the public library</a></section>
}
