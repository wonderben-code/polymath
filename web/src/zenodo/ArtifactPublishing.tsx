import { useEffect, useState } from 'react'
import type { SavedArtifact } from '../artifacts/types'
import { PublicationPanel } from '../publications/PublicationPanel'
import { zenodoStatus } from './client'
import type { ZenodoStatus } from './client'
import { PublishToZenodo } from './PublishToZenodo'

interface Props { artifact: SavedArtifact; csrfToken: string | null; enabled: boolean; sourceCurrent: boolean; onSessionExpired: () => void }

/** Where every visitor can connect Zenodo, they publish under their own name;
 * otherwise the site publisher's reviewed path is unchanged. */
export function ArtifactPublishing({ artifact, csrfToken, enabled, sourceCurrent, onSessionExpired }: Props) {
  const [status, setStatus] = useState<ZenodoStatus | null>(null)
  useEffect(() => {
    if (!enabled) return
    const controller = new AbortController()
    zenodoStatus(controller.signal).then(setStatus).catch(() => setStatus(null))
    return () => controller.abort()
  }, [enabled])
  if (enabled && status?.available) return <PublishToZenodo key={artifact.id} artifact={artifact} csrfToken={csrfToken} status={status} sourceCurrent={sourceCurrent} />
  return <PublicationPanel key={artifact.id} artifact={artifact} csrfToken={csrfToken} enabled={enabled} sourceCurrent={sourceCurrent} onSessionExpired={onSessionExpired} />
}
