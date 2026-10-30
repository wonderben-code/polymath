import type { ReactNode } from 'react'
import { SiteFooter, SiteHeader } from '../shell/SiteChrome'
import './budget.css'

export function BudgetShell({ children }: { children: ReactNode }) {
  return <><a className="skip-link" href="#budget-desk">Skip to operator budget desk</a><div className="page-shell calm budget-shell"><SiteHeader /><main id="budget-desk">{children}</main><SiteFooter /></div></>
}
