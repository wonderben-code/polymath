// Sign in with ORCID, GitHub or Google, or continue as a guest (docs/ACCOUNTS.md).
export type AccountProvider = 'orcid' | 'github' | 'google'
export interface AccountStatus { available: boolean; providers: AccountProvider[]; signed_in: boolean; account: { display_name: string; provider: AccountProvider; orcid: string | null } | null }

export class AccountError extends Error {
  constructor(public readonly code: string) { super(code); this.name = 'AccountError' }
}

const PROVIDERS: AccountProvider[] = ['orcid', 'github', 'google']
const HASH = /^[0-9a-f]{64}$/
function invalid(): never { throw new AccountError('invalid_response') }
function object(value: unknown): Record<string, unknown> { return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : invalid() }
function provider(value: unknown): AccountProvider { return PROVIDERS.includes(value as AccountProvider) ? value as AccountProvider : invalid() }

export function parseAccount(value: unknown): AccountStatus {
  const item = object(value)
  const providers = Array.isArray(item.providers) && item.providers.length <= 3 ? item.providers.map(provider) : invalid()
  let account: AccountStatus['account'] = null
  if (item.account !== null && item.account !== undefined) {
    const entry = object(item.account)
    const name = typeof entry.display_name === 'string' && entry.display_name.length <= 120 ? entry.display_name : invalid()
    const orcid = entry.orcid === null || entry.orcid === undefined ? null : typeof entry.orcid === 'string' && /^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$/.test(entry.orcid) ? entry.orcid : invalid()
    account = { display_name: name, provider: provider(entry.provider), orcid }
  }
  return { available: item.available === true, providers, signed_in: item.signed_in === true && account !== null, account }
}

async function request(path: string, method: 'GET' | 'POST' = 'GET', csrf?: string | null, body?: unknown): Promise<unknown> {
  if (method === 'POST' && (!csrf || !HASH.test(csrf))) throw new AccountError('session_changed')
  let response: Response
  try {
    response = await fetch(path, {
      method, credentials: 'same-origin', cache: 'no-store', redirect: 'error',
      headers: { Accept: 'application/json', ...(method === 'POST' ? { 'X-CSRF-Token': csrf as string } : {}), ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    })
  } catch { throw new AccountError('connection_failed') }
  let payload: unknown = null
  try { payload = await response.json() } catch { if (response.ok) throw new AccountError('invalid_response') }
  if (!response.ok) {
    const detail = payload && typeof payload === 'object' && typeof (payload as { detail?: unknown }).detail === 'string' ? (payload as { detail: string }).detail : ''
    throw new AccountError(response.status === 401 ? 'session_expired' : response.status === 403 ? 'session_changed' : detail || 'unavailable')
  }
  return payload
}

const AUTHORIZE_HOSTS: Record<AccountProvider, string[]> = { orcid: ['orcid.org', 'sandbox.orcid.org'], github: ['github.com'], google: ['accounts.google.com'] }

export async function accountStatus(): Promise<AccountStatus> { return parseAccount(await request('/api/v1/account')) }
export async function beginSignIn(target: AccountProvider, csrf: string | null): Promise<string> {
  const url = new URL(String(object(await request('/api/v1/account/sign-in', 'POST', csrf, { provider: target })).authorize_url))
  if (url.protocol !== 'https:' || !AUTHORIZE_HOSTS[target].includes(url.host)) invalid()
  return url.toString()
}
export async function completeSignIn(code: string, state: string, csrf: string | null): Promise<string> {
  return String(object(await request('/api/v1/account/complete', 'POST', csrf, { code, state })).result)
}
export async function signOut(csrf: string | null): Promise<void> { await request('/api/v1/account/sign-out', 'POST', csrf) }
export async function deleteAccount(csrf: string | null): Promise<void> { await request('/api/v1/account/delete', 'POST', csrf) }

export function accountErrorWords(code: string): string {
  switch (code) {
    case 'account_state_invalid': return 'That sign-in link has expired. Please try again.'
    case 'account_sign_in_refused': return 'The sign-in was not completed, so nothing changed.'
    case 'account_sign_out_first': return 'This browser is signed in to another account. Sign out first.'
    case 'session_expired': case 'session_changed': return 'Your session changed. Reload the page and try again.'
    default: return 'Signing in is not available just now. You can carry on as a guest.'
  }
}
