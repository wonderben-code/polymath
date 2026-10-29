import { useEffect, useState } from 'react'

// Long-established, textbook connections, shown only to illustrate the kind
// of link Polymath looks for. They are not Polymath findings.
export const HERO_EXAMPLES = [
  { left: 'Heat flow', right: 'Option pricing', shared: 'the same equation' },
  { left: 'Ant colonies', right: 'Internet routing', shared: 'the same way of finding paths' },
  { left: 'Epidemics', right: 'Rumours', shared: 'the same spreading pattern' },
] as const

const CYCLE_MS = 4800

function prefersStill(): boolean {
  return !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
}

/** A calm, wordless explanation of what Polymath does: two distant fields, one shared idea. */
export function HeroBridge() {
  const [index, setIndex] = useState(0)
  useEffect(() => {
    if (prefersStill()) return
    const timer = window.setInterval(() => setIndex((value) => (value + 1) % HERO_EXAMPLES.length), CYCLE_MS)
    return () => window.clearInterval(timer)
  }, [])
  const example = HERO_EXAMPLES[index]
  const description = `Examples of hidden connections: ${HERO_EXAMPLES.map((item) => `${item.left.toLowerCase()} and ${item.right.toLowerCase()} share ${item.shared}`).join('; ')}.`
  return (
    <figure className="hero-bridge" aria-label={description}>
      <div className="hero-bridge-scene" key={index} aria-hidden="true">
        <span className="hero-bridge-field is-left">{example.left}</span>
        <svg className="hero-bridge-arc" viewBox="0 0 240 70" preserveAspectRatio="none">
          <path d="M 6 64 Q 120 -26 234 64" pathLength={1} />
          <circle cx="6" cy="64" r="4" />
          <circle cx="234" cy="64" r="4" />
        </svg>
        <span className="hero-bridge-field is-right">{example.right}</span>
        <span className="hero-bridge-shared">{example.shared}</span>
      </div>
      <figcaption>Famous examples of the kind of connection Polymath looks for.</figcaption>
    </figure>
  )
}
