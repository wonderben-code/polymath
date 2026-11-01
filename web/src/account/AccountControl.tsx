import { useEffect, useId, useRef, useState } from 'react'
import { accountErrorWords, accountStatus, beginSignIn, completeSignIn, deleteAccount, signOut, AccountError } from './client'
import type { AccountProvider, AccountStatus } from './client'
import './account.css'

export const ACCOUNT_CALLBACK = '/account/callback'
const RETURN_KEY = 'polymath.account.return'
const AUTHOR_KEY = 'polymath.zenodo.author'

export const accountNavigation = {
  assign: (url: string) => window.location.assign(url),
  replace: (url: string) => window.location.replace(url),
}

const NAMES: Record<AccountProvider, { label: string; hint?: string }> = {
  orcid: { label: 'Continue with ORCID', hint: 'For researchers. Adds your ORCID to anything you publish.' },
  github: { label: 'Continue with GitHub' },
  google: { label: 'Continue with Google' },
}

function storedReturn(): string {
  try { const value = window.sessionStorage.getItem(RETURN_KEY); window.sessionStorage.removeItem(RETURN_KEY); return value && value.startsWith('/') && !value.startsWith('//') ? value : '/' } catch { return '/' }
}

function wordsFor(failure: unknown): string { return accountErrorWords(failure instanceof AccountError ? failure.code : '') }

/** Everyone starts as a guest; signing in only keeps their work on any device. */
export function AccountControl({ csrfToken, enabled }: { csrfToken: string | null; enabled: boolean }) {
  const uid = useId()
  const [status, setStatus] = useState<AccountStatus | null>(null)
  const [open, setOpen] = useState(false)
  const [forgetting, setForgetting] = useState(false)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const pending = useRef<{ code: string; state: string } | null>(null)
  const panel = useRef<HTMLDivElement>(null)
  const toggle = useRef<HTMLButtonElement>(null)

  // The provider returns here once. Take the one-time code out of the address bar at once.
  useEffect(() => {
    if (window.location.pathname !== ACCOUNT_CALLBACK) return
    const params = new URLSearchParams(window.location.search)
    window.history.replaceState(null, '', '/')
    const code = params.get('code'), state = params.get('state')
    if (params.get('error') || !code || !state) { setMessage('Sign-in was cancelled, so nothing changed. You can carry on as a guest.'); setOpen(true) }
    else pending.current = { code, state }
  }, [])

  useEffect(() => {
    if (!enabled || !csrfToken) return
    let cancelled = false
    const finish = pending.current
    pending.current = null
    void (async () => {
      if (finish) {
        try {
          await completeSignIn(finish.code, finish.state, csrfToken)
          // Reload so this page opens with the account's session.
          accountNavigation.replace(storedReturn())
          return
        } catch (failure) { if (!cancelled) { setMessage(wordsFor(failure)); setOpen(true) } }
      }
      try {
        const value = await accountStatus()
        if (cancelled) return
        setStatus(value)
        const orcid = value.account?.orcid
        if (orcid && value.account) {
          // Researchers who sign in with ORCID see their name and ORCID ready on records.
          try { if (!window.localStorage.getItem(AUTHOR_KEY)) window.localStorage.setItem(AUTHOR_KEY, JSON.stringify({ name: value.account.display_name, orcid })) } catch { /* A convenience only. */ }
        }
      } catch { /* Accounts are optional; research carries on without them. */ }
    })()
    return () => { cancelled = true }
  }, [enabled, csrfToken])

  useEffect(() => {
    if (!open) return
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') { setOpen(false); toggle.current?.focus() } }
    const outside = (event: MouseEvent) => { if (!panel.current?.contains(event.target as Node) && !toggle.current?.contains(event.target as Node)) setOpen(false) }
    document.addEventListener('keydown', close)
    document.addEventListener('mousedown', outside)
    panel.current?.querySelector<HTMLElement>('button')?.focus()
    return () => { document.removeEventListener('keydown', close); document.removeEventListener('mousedown', outside) }
  }, [open])

  async function act(work: () => Promise<void>) {
    setBusy(true); setMessage('')
    try { await work() } catch (failure) { setMessage(wordsFor(failure)) } finally { setBusy(false) }
  }

  function start(target: AccountProvider) {
    void act(async () => {
      const url = await beginSignIn(target, csrfToken)
      try { window.sessionStorage.setItem(RETURN_KEY, window.location.pathname) } catch { /* Returns home instead. */ }
      accountNavigation.assign(url)
    })
  }

  if (!status?.available && !message) return null
  const account = status?.signed_in ? status.account : null
  return (
    <div className="account-control">
      <button ref={toggle} type="button" className={`account-toggle${account ? ' is-signed-in' : ''}`} aria-expanded={open} aria-controls={`${uid}-panel`} onClick={() => { setOpen((value) => !value); setForgetting(false) }}>
        {account ? <><span className="account-dot" aria-hidden="true" />{account.display_name}</> : 'Sign in'}
      </button>
      {open && <div ref={panel} id={`${uid}-panel`} className="account-panel" role="dialog" aria-labelledby={`${uid}-title`}>
        {account ? <>
          <h2 id={`${uid}-title`}>{account.display_name}</h2>
          <p>Signed in with {account.provider === 'orcid' ? 'ORCID' : account.provider === 'github' ? 'GitHub' : 'Google'}{account.orcid ? ` · ORCID ${account.orcid}` : ''}. Your work is kept on any device where you sign in.</p>
          {!forgetting ? <div className="account-actions">
            <button type="button" disabled={busy} onClick={() => void act(async () => { await signOut(csrfToken); accountNavigation.replace('/') })}>Sign out</button>
            <button type="button" className="account-quiet" disabled={busy} onClick={() => setForgetting(true)}>Delete my account</button>
          </div> : <div className="account-confirm">
            <p>Polymath forgets your account and your name. Your work stays in this browser as a guest for 7 days.</p>
            <div className="account-actions"><button type="button" disabled={busy} onClick={() => void act(async () => { await deleteAccount(csrfToken); accountNavigation.replace('/') })}>Yes, delete it</button><button type="button" className="account-quiet" disabled={busy} onClick={() => setForgetting(false)}>Keep it</button></div>
          </div>}
        </> : <>
          <h2 id={`${uid}-title`}>Keep your work, on any device</h2>
          <p>Sign in and everything you have done here comes with you. Polymath only learns your name, never your password.</p>
          <div className="account-providers">{(status?.providers ?? []).map((name) => <button key={name} type="button" className={name === 'orcid' ? 'is-orcid' : ''} disabled={busy || !csrfToken} onClick={() => start(name)}>{NAMES[name].label}{NAMES[name].hint && <span>{NAMES[name].hint}</span>}</button>)}</div>
          <p className="account-guest">Or simply carry on as a guest. Your work stays in this browser for 7 days.</p>
        </>}
        {message && <p className="account-message" role="status">{message}</p>}
      </div>}
    </div>
  )
}
