import type { MouseEvent, ReactNode } from 'react'

export const PLACES = [
  { id: 'research', label: 'Research', path: '/' },
  { id: 'library', label: 'Your library', path: '/your-research' },
  { id: 'frontier', label: 'Frontier', path: '/frontier' },
  { id: 'about', label: 'About', path: '/about' },
] as const
export type Place = (typeof PLACES)[number]['id']

/** One quiet header for every page, so Polymath always feels like one place. */
export function SiteHeader({ current, onNavigate }: { current?: Place; onNavigate?: (place: Place) => void }) {
  const follow = (place: Place) => (event: MouseEvent<HTMLAnchorElement>) => { if (onNavigate) { event.preventDefault(); onNavigate(place) } }
  return (
    <header className="site-header">
      <a className="brand" href="/" aria-label="Polymath home" onClick={follow('research')}>
        <span className="brand-symbol" aria-hidden="true">p<span>·</span></span>
        <span>polymath</span>
      </a>
      <nav className="place-navigation" aria-label="Polymath">
        {PLACES.map((item) => <a key={item.id} href={item.path} aria-current={current === item.id ? 'page' : undefined} onClick={follow(item.id)}>{item.label}{item.id === 'frontier' && <span className="nav-badge">Experimental</span>}</a>)}
      </nav>
    </header>
  )
}

/** The shared footer; pages may add a status line before the links. */
export function SiteFooter({ children }: { children?: ReactNode }) {
  return (
    <footer className="site-footer">
      {children}
      <span className="footer-links"><a href="/library">Public library</a><a href="/operator/budget">Operator desk</a><span>A personal project by Ekram Alam</span></span>
    </footer>
  )
}
