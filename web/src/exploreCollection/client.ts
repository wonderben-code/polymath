import { ExploreCollectionError, invalid, uuid } from './primitives'
import { parseExploreBridgeDetail, parseExploreBridgeHistory, parseExploreCollectionAccess, parseExploreCollectionSummary, parseExploreRankingHistory } from './parsers'
export { ExploreCollectionError } from './primitives'
export * from './parsers'

async function request<T>(path: string, parse: (raw: unknown) => T, signal?: AbortSignal, maximum = 4 * 1024 * 1024): Promise<T> {
  if (signal?.aborted) throw new ExploreCollectionError('aborted')
  const controller = new AbortController(), abort = () => controller.abort(); signal?.addEventListener('abort', abort, { once: true }); const timer = window.setTimeout(abort, 10000)
  try {
    const response = await fetch(path, { method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error', referrerPolicy: 'no-referrer', signal: controller.signal, headers: { Accept: 'application/json' } })
    if (controller.signal.aborted) throw new ExploreCollectionError('aborted')
    if (response.status === 401 || response.status === 403) throw new ExploreCollectionError('session_expired')
    if (response.status === 429) { const retry = response.headers.get('retry-after'); throw new ExploreCollectionError('resource_limited', retry && /^\d{1,5}$/.test(retry) && Number(retry) <= 86400 ? Number(retry) : null) }
    if (!response.ok) throw new ExploreCollectionError(response.status === 404 ? 'explore_collection_not_found' : 'unavailable')
    const size = response.headers.get('content-length'); if (size !== null && (!/^\d+$/.test(size) || Number(size) > maximum)) return invalid()
    const reader = response.body?.getReader(); if (!reader) return invalid()
    const decoder = new TextDecoder('utf-8', { fatal: true }); let total = 0, value = ''
    const decode = (bytes?: Uint8Array, stream = false) => { try { return decoder.decode(bytes, { stream }) } catch { return invalid() } }
    try { while (true) { const chunk = await reader.read(); if (controller.signal.aborted) throw new ExploreCollectionError('aborted'); if (chunk.done) break; total += chunk.value.byteLength; if (total > maximum) { void reader.cancel(); return invalid() }; value += decode(chunk.value, true) }; value += decode() } finally { reader.releaseLock() }
    if (controller.signal.aborted) throw new ExploreCollectionError('aborted')
    try { return parse(JSON.parse(value)) } catch (caught) { if (caught instanceof ExploreCollectionError) throw caught; return invalid() }
  } catch (caught) { if (caught instanceof ExploreCollectionError) throw caught; throw new ExploreCollectionError(signal?.aborted ? 'aborted' : 'connection_failed') }
  finally { signal?.removeEventListener('abort', abort); window.clearTimeout(timer) }
}
function inputId(value: string): string { try { return uuid(value) } catch { throw new ExploreCollectionError('invalid_request') } }
const base = (runId: string) => `/api/v1/runs/${inputId(runId)}`
export const getExploreProtocols = (signal?: AbortSignal) => request('/api/v1/research/explore-protocols', parseExploreCollectionAccess, signal, 32768)
export const getExploreCollectionSummary = (runId: string, signal?: AbortSignal) => request(`${base(runId)}/explore-collection`, (raw) => { const value = parseExploreCollectionSummary(raw); return value.run_id === runId ? value : invalid() }, signal)
export const listExploreBridges = (runId: string, signal?: AbortSignal) => request(`${base(runId)}/bridges`, (raw) => { const value = parseExploreBridgeHistory(raw); return value.run_id === runId ? value : invalid() }, signal, 16 * 1024 * 1024)
export const getExploreBridge = (runId: string, stageId: string, signal?: AbortSignal) => request(`${base(runId)}/bridges/${inputId(stageId)}`, (raw) => { const value = parseExploreBridgeDetail(raw); return value.stage.run_id === runId && value.stage.stage_id === stageId ? value : invalid() }, signal)
export const listExploreRankings = (runId: string, signal?: AbortSignal) => request(`${base(runId)}/rankings`, (raw) => { const value = parseExploreRankingHistory(raw); return value.run_id === runId ? value : invalid() }, signal)
