export interface SystemStatus {
  api: boolean
  database: boolean
  migrations: boolean
  metaConfigured: boolean | null
  researchAvailable: boolean
  nativeResearchRequired: boolean
  reason: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

async function getJson(path: string, signal: AbortSignal, acceptUnavailable = false) {
  const response = await fetch(path, { signal, headers: { Accept: 'application/json' } })
  if (!response.ok && !(acceptUnavailable && response.status === 503)) {
    throw new Error('Service response unavailable')
  }
  const payload: unknown = await response.json()
  if (!isRecord(payload)) throw new Error('Invalid service response')
  return payload
}

export async function loadSystemStatus(signal: AbortSignal): Promise<SystemStatus> {
  const [health, readiness, capabilities] = await Promise.all([
    getJson('/api/v1/health', signal),
    getJson('/api/v1/readiness', signal, true),
    getJson('/api/v1/capabilities', signal),
  ])
  if (
    health.status !== 'ok' ||
    health.service !== 'polymath' ||
    !isRecord(readiness.checks) ||
    typeof capabilities.research_available !== 'boolean' ||
    capabilities.native_research_required !== undefined && typeof capabilities.native_research_required !== 'boolean' ||
    typeof capabilities.meta_configured !== 'boolean'
  ) {
    throw new Error('Invalid service response')
  }
  return {
    api: true,
    database: readiness.checks.database === 'ok',
    migrations: readiness.checks.migrations === 'ok',
    metaConfigured: capabilities.meta_configured,
    researchAvailable: capabilities.research_available,
    nativeResearchRequired: capabilities.native_research_required === true,
    reason: typeof capabilities.reason === 'string' ? capabilities.reason : 'unavailable',
  }
}
