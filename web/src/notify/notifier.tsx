import { useCallback, useEffect, useRef, useState } from 'react'
import './notify.css'

// Browser notifications only reach this tab while it stays open (in the
// background is fine); nothing is sent to a server or stored remotely.
const KEY = 'polymath.notify-when-done'
const BASE_TITLE = 'Polymath'

function stored(): boolean {
  try { return window.localStorage.getItem(KEY) === 'on' } catch { return false }
}

function remember(value: boolean) {
  try { window.localStorage.setItem(KEY, value ? 'on' : 'off') } catch { /* Preference stays in memory only. */ }
}

export function notificationsSupported(): boolean {
  return typeof window !== 'undefined' && 'Notification' in window
}

export interface Notifier {
  enabled: boolean
  supported: boolean
  denied: boolean
  toggle: () => Promise<void>
  announce: (title: string, body: string, tag: string) => void
  toast: { title: string; body: string } | null
  dismiss: () => void
}

export function useNotifier(): Notifier {
  const supported = notificationsSupported()
  const [enabled, setEnabled] = useState(() => supported && stored() && Notification.permission === 'granted')
  const [denied, setDenied] = useState(() => supported && Notification.permission === 'denied')
  const [toast, setToast] = useState<{ title: string; body: string } | null>(null)

  const toggle = useCallback(async () => {
    if (!supported) return
    if (enabled) { setEnabled(false); remember(false); return }
    let permission = Notification.permission
    if (permission === 'default') {
      try { permission = await Notification.requestPermission() } catch { permission = 'denied' }
    }
    setDenied(permission === 'denied')
    const granted = permission === 'granted'
    setEnabled(granted)
    remember(granted)
  }, [enabled, supported])

  const announce = useCallback((title: string, body: string, tag: string) => {
    setToast({ title, body })
    if (document.hidden) document.title = `✓ ${title} · ${BASE_TITLE}`
    if (!enabled || !supported || Notification.permission !== 'granted' || !document.hidden) return
    try {
      const notification = new Notification(title, { body: body.slice(0, 180), tag })
      notification.onclick = () => { window.focus(); notification.close() }
    } catch { /* Some browsers only allow notifications from a service worker. */ }
  }, [enabled, supported])

  useEffect(() => {
    const restore = () => { if (!document.hidden && document.title.startsWith('✓ ')) document.title = BASE_TITLE }
    document.addEventListener('visibilitychange', restore)
    return () => document.removeEventListener('visibilitychange', restore)
  }, [])

  return { enabled, supported, denied, toggle, announce, toast, dismiss: () => setToast(null) }
}

const TERMINAL = new Set(['succeeded', 'failed', 'cancelled', 'interrupted'])
const OUTCOME: Record<string, string> = { succeeded: 'Investigation finished', failed: 'Investigation stopped', cancelled: 'Investigation cancelled', interrupted: 'Investigation paused' }

/** Announce when the watched run finishes, and keep the tab title honest meanwhile. */
export function useRunCompletion(run: { id: string; status: string; goal: string } | null, notifier: Notifier) {
  const previous = useRef<{ id: string; status: string } | null>(null)
  useEffect(() => {
    const before = previous.current
    previous.current = run ? { id: run.id, status: run.status } : null
    if (!run) return
    if (!TERMINAL.has(run.status)) { document.title = `● Researching · ${BASE_TITLE}`; return }
    if (document.title.startsWith('● ')) document.title = BASE_TITLE
    if (before && before.id === run.id && !TERMINAL.has(before.status)) notifier.announce(OUTCOME[run.status] ?? 'Investigation finished', run.goal || 'An open discovery', run.id)
  }, [run?.id, run?.status, run?.goal, notifier.announce])
}

export function NotifyToggle({ notifier }: { notifier: Notifier }) {
  if (!notifier.supported) return <p className="notify-note">Keep this tab open; the record updates live.</p>
  return (
    <div className="notify-toggle">
      <button type="button" aria-pressed={notifier.enabled} onClick={() => void notifier.toggle()}>
        <span className="notify-bell" aria-hidden="true">🔔</span>{notifier.enabled ? 'You’ll be notified when it’s done' : 'Notify me when it’s done'}
      </button>
      <span className="notify-note">{notifier.denied ? 'Notifications are blocked in this browser’s settings.' : 'Works while this tab stays open in the background.'}</span>
    </div>
  )
}

export function CompletionToast({ notifier }: { notifier: Notifier }) {
  useEffect(() => {
    if (!notifier.toast) return
    const timer = window.setTimeout(notifier.dismiss, 9000)
    return () => window.clearTimeout(timer)
  }, [notifier.toast, notifier.dismiss])
  if (!notifier.toast) return null
  return (
    <div className="completion-toast" role="status">
      <span className="completion-toast-mark" aria-hidden="true">✓</span>
      <div><strong>{notifier.toast.title}</strong><p>{notifier.toast.body}</p></div>
      <button type="button" aria-label="Dismiss" onClick={notifier.dismiss}>×</button>
    </div>
  )
}
