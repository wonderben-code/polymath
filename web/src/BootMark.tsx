// A brief branded mark while the workspace code loads; it draws a small
// constellation, the same visual language as the research Observatory.
export function BootMark({ label }: { label: string }) {
  return (
    <div className="boot-mark" role="status">
      <svg viewBox="0 0 120 80" aria-hidden="true">
        <path d="M14 56 L52 22 L104 34 L70 66 Z M52 22 L70 66" />
        <circle cx="14" cy="56" r="4" /><circle cx="52" cy="22" r="7" /><circle cx="104" cy="34" r="5" /><circle cx="70" cy="66" r="3.5" />
      </svg>
      <span>{label}</span>
    </div>
  )
}
