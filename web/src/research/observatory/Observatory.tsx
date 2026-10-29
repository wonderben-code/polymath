import { useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import type { ResearchActivityEvent, ResearchResult } from '../types'
import { observe, papersFound } from './model'
import type { SearchPurpose } from './model'
import './observatory.css'

const WIDTH = 800, HEIGHT = 420, CX = 400, CY = 210
const RING = 176, MEMORY_RING = 88, FIELD_RING = 132, SLOTS = 72

type Point = { x: number; y: number }

function polar(radius: number, angle: number): Point {
  const radians = ((angle - 90) * Math.PI) / 180
  return { x: CX + radius * Math.cos(radians), y: CY + radius * Math.sin(radians) }
}

// A stable pseudo-random angle per event, so the picture never reshuffles.
function angleFor(sequence: number, salt = 0): number {
  let value = (sequence * 2654435761 + salt * 40503) >>> 0
  value ^= value >>> 15
  return value % 360
}

function settle(point: Point): CSSProperties {
  return { '--dx': `${CX - point.x}px`, '--dy': `${CY - point.y}px` } as CSSProperties
}

const purposeClass: Record<SearchPurpose, string> = { discovery: 'search', counterevidence: 'counter', prior_work: 'prior' }

function duration(milliseconds: number): string {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000))
  const minutes = Math.floor(seconds / 60)
  return minutes >= 60 ? `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m` : `${minutes}:${String(seconds % 60).padStart(2, '0')}`
}

function parseTime(value: unknown): number | null {
  if (typeof value !== 'string') return null
  const time = Date.parse(value)
  return Number.isFinite(time) ? time : null
}

export interface ObservatoryProps {
  events: ResearchActivityEvent[]
  terminal: boolean
  createdAt?: string
  updatedAt?: string
  result?: ResearchResult | null
}

export function Observatory({ events, terminal, createdAt, updatedAt, result }: ObservatoryProps) {
  const state = useMemo(() => observe(events, terminal), [events, terminal])
  const [now, setNow] = useState(() => Date.now())
  // Thinking time is shown only for calls this page saw begin; a call already
  // in flight when the page opened has no trustworthy start time here.
  const seenAtMount = useRef<Set<number> | null>(null)
  const thinkingSince = useRef(new Map<number, number | null>())
  if (seenAtMount.current === null) seenAtMount.current = new Set(events.map((event) => event.sequence))
  if (state.thinking !== null && !thinkingSince.current.has(state.thinking)) thinkingSince.current.set(state.thinking, seenAtMount.current.has(state.thinking) ? null : Date.now())

  useEffect(() => {
    if (terminal) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [terminal])

  const started = parseTime(createdAt)
  const finished = terminal ? parseTime(updatedAt) : null
  const elapsed = started === null ? null : (finished ?? now) - started
  const thinkingStart = state.thinking === null ? null : thinkingSince.current.get(state.thinking) ?? null
  const found = papersFound(state)

  // Found papers settle onto the literature ring in search order; read papers
  // take the next slots and are threaded to the core.
  // Golden-angle placement spreads papers evenly around the whole ring, like
  // seeds in a sunflower, however many searches have run.
  const dots: { key: string; point: Point; purpose: SearchPurpose; read: boolean; search: number }[] = []
  let slot = 0
  for (const search of state.searches) {
    for (let index = 0; index < search.found && slot < SLOTS; index += 1, slot += 1) {
      dots.push({ key: `${search.sequence}:${index}`, point: polar(RING + ((slot * 7) % 3) * 5 - 5, slot * 137.508), purpose: search.purpose, read: false, search: search.sequence })
    }
  }
  // Each read links to an unread paper from the latest search before it.
  const threads = state.reads.slice(-SLOTS).map((read) => {
    const before = state.searches.filter((search) => search.sequence < read.sequence).at(-1)?.sequence
    const target = dots.find((dot) => !dot.read && dot.search === before) ?? dots.find((dot) => !dot.read)
    if (target) target.read = true
    return { read, point: target?.point ?? polar(RING, angleFor(read.sequence, 3)) }
  })
  const latestSearch = state.searches.at(-1)
  const bridge = result?.finding?.outcome === 'bridge' ? result.finding : null
  const answer = result?.finding?.outcome === 'answer' ? result.finding : null
  const thinking = state.thinking !== null && !terminal
  const label = `Research map: ${state.searches.length} searches, ${found} papers found, ${state.reads.length} read, ${state.hypotheses.length} hypotheses, ${state.setbacks} setbacks.`

  return (
    <figure className={`observatory${thinking ? ' is-thinking' : ''}${terminal ? ' is-terminal' : ''}`} aria-labelledby="observatory-narration">
      <div className="observatory-stage">
        <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={label} preserveAspectRatio="xMidYMid meet">
          <defs>
            <radialGradient id="obs-core" cx="50%" cy="45%" r="60%"><stop offset="0%" stopColor="#4f9a83" /><stop offset="70%" stopColor="#266b58" /><stop offset="100%" stopColor="#1d5345" /></radialGradient>
            <radialGradient id="obs-glow" cx="50%" cy="50%" r="50%"><stop offset="0%" stopColor="#266b58" stopOpacity=".18" /><stop offset="100%" stopColor="#266b58" stopOpacity="0" /></radialGradient>
            <filter id="obs-soft" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="2.4" /></filter>
          </defs>
          <circle cx={CX} cy={CY} r={RING + 30} fill="url(#obs-glow)" />
          {[MEMORY_RING, FIELD_RING, RING].map((radius) => <circle key={radius} className="obs-guide" cx={CX} cy={CY} r={radius} />)}

          {state.memories.map((memory, index) => {
            const point = polar(MEMORY_RING, angleFor(memory.sequence, 7) + index * 11)
            return <rect key={memory.sequence} className="obs-memory obs-arrive" x={point.x - 4} y={point.y - 4} width="8" height="8" transform={`rotate(45 ${point.x} ${point.y})`} />
          })}

          {state.fields.map((field, index) => {
            const point = polar(FIELD_RING, (index * 360) / state.fields.length + 20)
            return <g key={field} className="obs-field obs-arrive" style={{ animationDelay: `${index * 140}ms` }}>
              <circle cx={point.x} cy={point.y} r="6" />
              <text x={point.x} y={point.y + (point.y < CY ? -12 : 20)} textAnchor="middle">{field}</text>
            </g>
          })}

          {dots.map((dot) => <circle key={dot.key} className={`obs-paper obs-${purposeClass[dot.purpose]}${dot.read ? ' is-read' : ''} obs-settle`} style={settle(dot.point)} cx={dot.point.x} cy={dot.point.y} r={dot.read ? 4.2 : 2.4} />)}

          {threads.map(({ read, point }) => {
            const control = polar(RING * 0.45, angleFor(read.sequence, 11))
            return <g key={read.sequence} className={`obs-thread${read.kind === 'excerpt' || read.kind === 'full_text' ? ' is-deep' : ''}${read.revisit ? ' is-revisit' : ''}`}>
              <path d={`M ${CX} ${CY} Q ${control.x} ${control.y} ${point.x} ${point.y}`} pathLength={1} />
              <circle className="obs-thread-halo" cx={point.x} cy={point.y} r="9" />
            </g>
          })}


          {latestSearch && !terminal && <circle key={`ripple-${latestSearch.sequence}`} className={`obs-ripple obs-${purposeClass[latestSearch.purpose]}`} cx={CX} cy={CY} r={RING} />}
          {state.lastSetback !== null && !terminal && <circle key={`setback-${state.lastSetback}`} className="obs-setback" cx={CX} cy={CY} r="44" />}

          {bridge && (() => {
            const left = polar(FIELD_RING + 8, 252), right = polar(FIELD_RING + 8, 108)
            return <g className="obs-bridge obs-arrive">
              <path d={`M ${left.x} ${left.y} Q ${CX} ${CY - 170} ${right.x} ${right.y}`} pathLength={1} />
              <circle cx={left.x} cy={left.y} r="8" /><circle cx={right.x} cy={right.y} r="8" />
              <text x={left.x} y={left.y + 24} textAnchor="middle">{bridge.field_left.slice(0, 32)}</text>
              <text x={right.x} y={right.y + 24} textAnchor="middle">{bridge.field_right.slice(0, 32)}</text>
            </g>
          })()}
          {answer && answer.claims.map((claim, index) => {
            const point = polar(54, (index * 360) / answer.claims.length)
            return <circle key={claim.id} className={`obs-claim is-${claim.label} obs-arrive`} style={{ animationDelay: `${index * 120}ms` }} cx={point.x} cy={point.y} r="6" />
          })}

          {thinking && <circle className="obs-think-ring" cx={CX} cy={CY} r="30" />}
          <circle className="obs-core" cx={CX} cy={CY} r="26" fill="url(#obs-core)" />
          <circle className="obs-core-light" cx={CX - 8} cy={CY - 9} r="7" filter="url(#obs-soft)" />
          {state.hypotheses.map((hypothesis) => {
            const point = polar(72 + (angleFor(hypothesis.sequence, 5) % 40), angleFor(hypothesis.sequence, 9))
            const size = hypothesis.state === 'discarded' ? 7 : 12
            return <path key={hypothesis.sequence} className={`obs-hypothesis obs-arrive is-${hypothesis.state}`} d={`M ${point.x} ${point.y - size} L ${point.x + size * 0.3} ${point.y - size * 0.3} L ${point.x + size} ${point.y} L ${point.x + size * 0.3} ${point.y + size * 0.3} L ${point.x} ${point.y + size} L ${point.x - size * 0.3} ${point.y + size * 0.3} L ${point.x - size} ${point.y} L ${point.x - size * 0.3} ${point.y - size * 0.3} Z`} />
          })}
        </svg>
      </div>
      <figcaption>
        <p id="observatory-narration" className="observatory-narration">
          {thinking && <span className="observatory-thinking-dot" aria-hidden="true" />}
          {state.narration}
          {thinking && thinkingStart !== null && <span className="observatory-timer"> · {duration(now - thinkingStart)}</span>}
        </p>
        <dl className="observatory-stats">
          <div><dt>Elapsed</dt><dd>{elapsed === null ? '—' : duration(elapsed)}</dd></div>
          <div><dt>Model calls</dt><dd>{state.modelCalls}</dd></div>
          <div><dt>Searches</dt><dd>{state.searches.length}</dd></div>
          <div><dt>Papers found</dt><dd>{found}</dd></div>
          <div><dt>Papers read</dt><dd>{state.reads.length}</dd></div>
          <div><dt>Hypotheses</dt><dd>{state.hypotheses.length}</dd></div>
        </dl>
        <ul className="observatory-legend" aria-label="Map legend">
          <li><span className="legend-swatch obs-search" />Search</li>
          <li><span className="legend-swatch obs-counter" />Counter-search</li>
          <li><span className="legend-swatch is-read" />Paper read</li>
          <li><span className="legend-swatch obs-memory" />Memory</li>
          <li><span className="legend-swatch obs-hypothesis" />Hypothesis</li>
          <li><span className="legend-swatch obs-setback-swatch" />Setback</li>
        </ul>
      </figcaption>
    </figure>
  )
}
