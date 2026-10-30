import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { openResearchSession, ResearchApiError } from '../research/client'
import type { ResearchSession } from '../research/client'
import { loginOperator, logoutOperator, operatorSession, PublicationError } from '../publications/client'
import type { OperatorSession } from '../publications/client'
import { BudgetShell } from './BudgetShell'
import { BudgetWorkspace } from './BudgetWorkspace'
import { budgetDeadline } from './deadline'
import { VerificationWorkspace } from './verificationWorkspace'
import { ActivationWorkspace } from './activationWorkspace'
import { PublicActivationWorkspace } from './publicActivationWorkspace'

function lockedOperator(configured: boolean): OperatorSession { return { configured, authenticated: false, actor_id: null, display_name: null, expires_at: null, csrf_token: null } }

export function BudgetDesk() {
  const [browser, setBrowser] = useState<ResearchSession | null>(null), [operator, setOperator] = useState<OperatorSession | null>(null), [phase, setPhase] = useState<'loading' | 'ready' | 'error' | 'expired'>('loading')
  const [credential, setCredential] = useState(''), [busy, setBusy] = useState<string | null>(null), [error, setError] = useState(''), [refresh, setRefresh] = useState(0)
  const [panel, setPanel] = useState<'preparation' | 'verification' | 'research' | 'public'>(() => window.location.hash === '#public' ? 'public' : window.location.hash === '#research' ? 'research' : 'preparation')
  const scope = useRef(0), requests = useRef(new Set<AbortController>()), locked = useRef(false), blocked = useRef(false)
  const expireBrowser = useCallback(() => { blocked.current = true; scope.current++; requests.current.forEach((request) => request.abort()); requests.current.clear(); locked.current = false; setBrowser(null); setOperator(null); setCredential(''); setBusy(null); setError(''); setPhase('expired') }, [])
  const expireOperator = useCallback(() => { scope.current++; requests.current.forEach((request) => request.abort()); requests.current.clear(); locked.current = false; setOperator((previous) => lockedOperator(previous?.configured === true)); setCredential(''); setBusy(null); setError('Operator access expired or changed. Budget information and unsaved review data have been cleared.') }, [])
  useEffect(() => {
    const generation = ++scope.current, controller = new AbortController(); requests.current.add(controller); blocked.current = false; locked.current = false
    setBrowser(null); setOperator(null); setCredential(''); setBusy(null); setError(''); setPhase('loading')
    const timer = window.setTimeout(() => { controller.abort(); if (generation === scope.current && !blocked.current) setPhase('error') }, 10000)
    openResearchSession(controller.signal).then(async (session) => {
      if (generation !== scope.current || controller.signal.aborted || blocked.current) return
      if (Date.parse(session.expires_at) <= Date.now()) { expireBrowser(); return }
      const access = await operatorSession(controller.signal)
      if (generation === scope.current && !controller.signal.aborted && !blocked.current) { setBrowser(session); setOperator(access); setPhase('ready') }
    }).catch((caught: unknown) => { if (generation === scope.current && !controller.signal.aborted && !blocked.current) { if (caught instanceof ResearchApiError && ['session_expired', 'session_changed'].includes(caught.code) || caught instanceof PublicationError && ['browser_expired', 'browser_changed'].includes(caught.code)) expireBrowser(); else { setBrowser(null); setOperator(null); setPhase('error') } } }).finally(() => { window.clearTimeout(timer); requests.current.delete(controller) })
    return () => { scope.current++; controller.abort(); requests.current.forEach((request) => request.abort()); requests.current.clear(); window.clearTimeout(timer) }
  }, [refresh, expireBrowser])
  useEffect(() => {
    if (!browser) return
    return budgetDeadline(browser.expires_at, expireBrowser)
  }, [browser, expireBrowser])
  useEffect(() => {
    if (!operator?.authenticated || !operator.expires_at) return
    return budgetDeadline(operator.expires_at, expireOperator)
  }, [operator, expireOperator])
  async function authorize(kind: 'login' | 'logout') {
    if (locked.current || blocked.current || !browser || !operator || kind === 'login' && (!credential || !operator.configured) || kind === 'logout' && !operator.csrf_token) return
    const token = credential; setCredential(''); locked.current = true; setBusy(kind); setError('')
    const generation = scope.current, controller = new AbortController(); requests.current.add(controller)
    if (kind === 'logout') setOperator(lockedOperator(operator.configured))
    try {
      const access = kind === 'login' ? await loginOperator(token, browser.csrf_token, controller.signal) : await logoutOperator(browser.csrf_token, operator.csrf_token!, controller.signal)
      if (generation === scope.current && !controller.signal.aborted && !blocked.current) setOperator(access)
    } catch (caught) {
      if (generation !== scope.current || controller.signal.aborted || blocked.current) return
      if (caught instanceof PublicationError && ['browser_expired', 'browser_changed'].includes(caught.code)) expireBrowser()
      else if (caught instanceof PublicationError && caught.code === 'operator_expired') expireOperator()
      else setError(caught instanceof PublicationError && caught.code === 'invalid_credential' ? 'Operator access was not accepted. Check the access code and try again.' : 'Operator access could not be confirmed. Reload access before starting another action.')
    } finally { requests.current.delete(controller); if (generation === scope.current && !blocked.current) { locked.current = false; setBusy(null) } }
  }
  function login(event: FormEvent<HTMLFormElement>) { event.preventDefault(); void authorize('login') }
  return <BudgetShell><div className="budget-intro"><span className="eyebrow accent">THE OPERATOR BUDGET DESK</span><h1>Prepare the limits before the intelligence runs.</h1><p>Set the spending limits, verify Meta with one call, and switch research on for yourself or for visitors. Nothing here spends until you arm it.</p><details className="budget-safety"><summary>How this desk keeps spending safe</summary><p className="budget-warning">Preparation makes no model call. A protocol verification requires its own exact review and explicit one-call arm, including its scoped kill-switch clear. Private research requires its own exact operator/browser activation. Public visitor funding requires a separate durable delegation. Signing out does not stop public funding; use its explicit stop or disable the shared account. Unknown charges retain their holds.</p></details></div>
    {phase === 'loading' && <p role="status">Opening a private browser session and checking operator access…</p>}{phase === 'error' && <p className="budget-warning" role="alert">The operator desk could not establish a private session. Check the connection and reload access.</p>}{phase === 'expired' && <p className="budget-warning" role="alert">The browser session expired or changed. Private budget data and unsaved review inputs have been cleared.</p>}
    {phase !== 'loading' && <div className="budget-actions"><button type="button" disabled={!!busy} onClick={() => setRefresh((value) => value + 1)}>Reload operator access</button></div>}
    {phase === 'ready' && operator && browser && <><section className="budget-card" aria-label="Operator budget access"><span className="eyebrow accent">EXPLICIT OPERATOR ACCESS</span><h2>Budget authority is a separate role.</h2><p className="budget-note">An operator must be explicitly allowed to manage budgets by the server. Publisher access alone grants no budget authority.</p>{operator.authenticated ? <><p>Signed in as {operator.display_name} until {operator.expires_at && new Date(operator.expires_at).toLocaleString()}.</p><p className="budget-warning">Locking operator access does not stop an active public funding delegation. Stop public research or disable the shared account to prevent further model calls.</p><div className="budget-actions"><button type="button" disabled={!!busy} onClick={() => void authorize('logout')}>{busy === 'logout' ? 'Locking operator access…' : 'Lock operator access'}</button></div></> : operator.configured ? <form onSubmit={login}><label className="budget-field" htmlFor="budget-operator-code">Operator access code<input id="budget-operator-code" type="password" autoComplete="off" maxLength={256} value={credential} disabled={!!busy} onChange={(event) => setCredential(event.target.value)} /></label><div className="budget-actions"><button type="submit" disabled={!credential || !!busy}>{busy === 'login' ? 'Checking operator access…' : 'Unlock operator access'}</button></div><p className="budget-note">The entered code is erased when submitted and stays out of browser storage. Signing in does not save or enable a proposal.</p></form> : <p className="budget-warning">Operator access has not been configured on this server.</p>}{error && <p role="alert" className="budget-warning">{error}</p>}</section>
      <div className="budget-actions" role="group" aria-label="Choose operator budget workspace"><button type="button" aria-pressed={panel === 'preparation'} onClick={() => setPanel('preparation')}>Preparation and accounts</button><button type="button" aria-pressed={panel === 'verification'} onClick={() => setPanel('verification')}>One-call verification</button><button type="button" aria-pressed={panel === 'research'} onClick={() => setPanel('research')}>Research activation</button><button type="button" aria-pressed={panel === 'public'} onClick={() => setPanel('public')}>Public visitor funding</button></div><p className="budget-note">Switching workspaces clears unsaved review inputs. Saved attestations, ledger facts and recorded jobs remain available.</p>
      {panel === 'preparation' ? <BudgetWorkspace key={`${browser.csrf_token}:${operator.csrf_token ?? 'locked'}`} csrfToken={browser.csrf_token} operatorCsrf={operator.csrf_token} authenticated={operator.authenticated} onBrowserExpired={expireBrowser} onOperatorExpired={expireOperator} /> : panel === 'verification' ? <VerificationWorkspace key={`verification:${browser.csrf_token}:${operator.csrf_token ?? 'locked'}`} csrfToken={browser.csrf_token} operatorCsrf={operator.csrf_token} authenticated={operator.authenticated} onBrowserExpired={expireBrowser} onOperatorExpired={expireOperator} /> : panel === 'public' ? <PublicActivationWorkspace key={`public:${browser.csrf_token}:${operator.csrf_token ?? 'locked'}`} csrfToken={browser.csrf_token} operatorCsrf={operator.csrf_token} authenticated={operator.authenticated} onBrowserExpired={expireBrowser} onOperatorExpired={expireOperator} /> : <ActivationWorkspace key={`research:${browser.csrf_token}:${operator.csrf_token ?? 'locked'}`} csrfToken={browser.csrf_token} operatorCsrf={operator.csrf_token} authenticated={operator.authenticated} onBrowserExpired={expireBrowser} onOperatorExpired={expireOperator} />}
    </>}
  </BudgetShell>
}
