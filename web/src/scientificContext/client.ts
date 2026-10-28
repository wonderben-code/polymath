import { ResearchAccessError } from '../research/access'
import { parseResearchProtocols } from './overviewBinding'
import type { ResearchProtocols } from './types'
/** Read-only feature discovery for an existing owner; no mint, operator lookup or paid action. */
export async function getResearchProtocols(signal?: AbortSignal): Promise<ResearchProtocols> {
  const controller = new AbortController(), abort = () => controller.abort(); signal?.addEventListener('abort', abort, { once: true }); if (signal?.aborted) controller.abort()
  const timer = window.setTimeout(abort, 10000)
  try {
    const response = await fetch('/api/v1/research/protocols', { method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error', referrerPolicy: 'no-referrer', headers: { Accept: 'application/json' }, signal: controller.signal })
    if (controller.signal.aborted) throw new ResearchAccessError('connection_failed')
    if (response.status === 401 || response.status === 403) throw new ResearchAccessError(response.status === 401 ? 'browser_expired' : 'browser_changed')
    if (response.status === 429) { const retry = response.headers.get('retry-after'); throw new ResearchAccessError('resource_limited', retry && /^\d{1,5}$/.test(retry) && Number(retry) <= 86400 ? Number(retry) : null) }
    if (!response.ok) throw new ResearchAccessError('unavailable')
    const reader = response.body?.getReader(); if (!reader) throw new ResearchAccessError('invalid_response')
    let total = 0, value = ''; const decoder = new TextDecoder('utf-8', { fatal: true })
    try { while (true) { const item = await reader.read(); if (controller.signal.aborted) throw new ResearchAccessError('connection_failed'); if (item.done) break; total += item.value.byteLength; if (total > 32768) { void reader.cancel(); throw new ResearchAccessError('invalid_response') }; value += decoder.decode(item.value, { stream: true }) }; value += decoder.decode() } finally { reader.releaseLock() }
    if (controller.signal.aborted) throw new ResearchAccessError('connection_failed')
    try { return parseResearchProtocols(JSON.parse(value)) } catch { throw new ResearchAccessError('invalid_response') }
  } catch (caught) { if (caught instanceof ResearchAccessError) throw caught; throw new ResearchAccessError('connection_failed') }
  finally { window.clearTimeout(timer); signal?.removeEventListener('abort', abort) }
}
