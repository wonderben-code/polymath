import type { ReactNode } from 'react'
import './budget.css'

export function BudgetShell({ children }: { children: ReactNode }) {
  return <><a className="skip-link" href="#budget-desk">Skip to operator budget desk</a><div className="page-shell budget-shell"><header className="site-header"><a className="brand" href="/" aria-label="Polymath home"><span className="brand-symbol" aria-hidden="true">p<span>·</span></span><span>polymath</span></a><div className="header-note">An accountable research desk<span className="header-rule" />Operator controls</div><nav className="budget-navigation" aria-label="Polymath navigation"><a href="/">Research desk</a><a href="/library">Public library</a></nav></header><main id="budget-desk">{children}</main><footer className="site-footer"><span>Polymath · Operator budget readiness</span><a href="/">Return to the research desk</a></footer></div></>
}
