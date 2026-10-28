import { lazy, Suspense } from 'react'
import { PublicFindingPage } from './sharing/PublicFindingPage'
import { PublicLibraryPage } from './sharing/PublicLibraryPage'

const PrivateApp = lazy(() => import('./App').then((module) => ({ default: module.App })))
const BudgetDesk = lazy(() => import('./budget/BudgetDesk').then((module) => ({ default: module.BudgetDesk })))

export function ApplicationRouter({ pathname = window.location.pathname }: { pathname?: string }) {
  if (pathname === '/operator/budget' || pathname === '/operator/budget/') return <Suspense fallback={<p role="status">Opening the operator budget desk…</p>}><BudgetDesk /></Suspense>
  if (pathname === '/library' || pathname === '/library/') return <PublicLibraryPage />
  if (pathname === '/shared' || pathname.startsWith('/shared/')) {
    const match = /^\/shared\/([A-Za-z0-9_-]{43})\/?$/.exec(pathname)
    return <PublicFindingPage publicId={match?.[1] ?? null} />
  }
  return <Suspense fallback={<p role="status">Opening the research desk…</p>}><PrivateApp /></Suspense>
}
