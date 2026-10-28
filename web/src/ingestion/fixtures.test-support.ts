import type { LiteratureWatch, WatchCapabilities, WatchSource, WatchSourceDetail, WatchTick } from './client'

export const WATCH = '11111111-1111-4111-8111-111111111111'
export const RUN = '22222222-2222-4222-8222-222222222222'
export const SOURCE = '33333333-3333-4333-8333-333333333333'
export const RESULT = '44444444-4444-4444-8444-444444444444'
export const HASH = 'a'.repeat(64)
export const capabilities = (available = true): WatchCapabilities => ({ execution_available: available, reason: available ? null : 'ingestion_execution_disabled', model_calls: false, min_interval_seconds: 21600, max_interval_seconds: 604800, watch_limit: 10, source_limit_per_watch: 2000, coverage: 'bounded_provider_windows' })
export function watch(overrides: Partial<LiteratureWatch> = {}): LiteratureWatch {
  return { id: WATCH, label: 'Synthetic watch', query: 'Synthetic evidence query', enabled: true, interval_seconds: 86400, next_due_at: '2099-10-29T00:00:00Z', expires_at: '2100-01-01T00:00:00Z', created_at: '2099-10-28T00:00:00Z', updated_at: '2099-10-28T00:00:00Z', source_result_id: RESULT, active_run_id: null, active_status: null, last_run_at: null, source_count: 0, cursors: { openalex: { since: '2099-10-21T00:00:00Z', until: '2099-10-28T00:00:00Z', cursor: 'private-provider-cursor', exhausted: false, revision: 1 }, arxiv: { since: '2099-10-21T00:00:00Z', until: '2099-10-28T00:00:00Z', cursor: 0, exhausted: false, revision: 0 } }, coverage: 'current_browser_session', ...overrides }
}
export function source(overrides: Partial<WatchSource> = {}): WatchSource {
  return { id: SOURCE, source_id: 'arxiv:2099.00001', title: 'Synthetic paper, not scientific evidence', url: 'https://arxiv.org/abs/2099.00001', content_kind: 'abstract', content_sha256: HASH, first_seen_at: '2099-10-28T00:00:00Z', run_id: RUN, ...overrides }
}
export function tick(overrides: Partial<WatchTick> = {}): WatchTick {
  return { id: RUN, run_id: RUN, watch_id: WATCH, status: 'succeeded', slot: '2099-10-28T00:00:00Z', created_at: '2099-10-28T00:00:00Z', updated_at: '2099-10-28T00:00:01Z', added_sources: 1, diagnostics: { openalex: { status: 'succeeded', sources_seen: 2, added_sources: 1, exhausted: false }, arxiv: { status: 'failed', code: 'provider_unavailable', sources_seen: 0, added_sources: 0, exhausted: false } }, coverage: 'bounded_provider_window', ...overrides }
}
export function detail(overrides: Partial<WatchSourceDetail> = {}): WatchSourceDetail {
  return { id: SOURCE, watch_id: WATCH, run_id: RUN, version_id: SOURCE, retrieval_id: RESULT, content_sha256: HASH, metadata_sha256: 'b'.repeat(64), document: { source_id: 'arxiv:2099.00001', title: 'Synthetic paper, not scientific evidence', url: 'https://arxiv.org/abs/2099.00001', text: 'Synthetic original abstract. This fixture is not a research finding.', content_kind: 'abstract', retrieved_at: '2099-10-28T00:00:00Z', extraction_method: 'synthetic_fixture' }, assessment_status: 'unassessed', ...overrides }
}
