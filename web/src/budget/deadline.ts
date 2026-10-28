/** Recheck long documentary windows instead of overflowing browser timers. */
export function budgetDeadline(expiresAt: string, expire: () => void): () => void {
  let active = true, timer: number | undefined
  function check() {
    if (!active) return
    const remaining = Date.parse(expiresAt) - Date.now()
    if (!Number.isFinite(remaining) || remaining <= 0) expire()
    else timer = window.setTimeout(check, Math.min(2147483647, remaining))
  }
  check()
  return () => { active = false; window.clearTimeout(timer) }
}
