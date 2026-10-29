import type { ConvergeProgramme, ConvergeStep } from './client'

const SIZE = 680, CENTER = SIZE / 2, RIM = 240
const LABEL_LIMIT = 30

interface Point { x: number; y: number }

function angleOf(index: number, count: number): number {
  return -Math.PI / 2 + (index / Math.max(1, count)) * Math.PI * 2
}

function onRim(index: number, count: number, radius = RIM): Point {
  const angle = angleOf(index, count)
  return { x: CENTER + Math.cos(angle) * radius, y: CENTER + Math.sin(angle) * radius }
}

/** Deeper levels gather toward the centre, each ring closer than the last. */
export function ringRadius(level: number): number {
  return level <= 1 ? RIM : RIM * Math.pow(0.6, level - 1)
}

function meanAngle(indices: number[], count: number): number {
  let x = 0, y = 0
  for (const index of indices) { const angle = angleOf(index, count); x += Math.cos(angle); y += Math.sin(angle) }
  return x === 0 && y === 0 ? -Math.PI / 2 : Math.atan2(y, x)
}

/** Field indices a step spans, following its premises while it is still running. */
export function spanOf(step: ConvergeStep, byId: Map<string, ConvergeStep>): number[] {
  if (step.field_indices.length || step.kind === 'thread') return step.field_indices
  return [...new Set(step.premise_step_ids.flatMap((premise) => { const found = byId.get(premise); return found ? spanOf(found, byId) : [] }))].sort((a, b) => a - b)
}

export interface Placed { step: ConvergeStep; at: Point; path?: string; span: number[] }

/** Where every held or running connection sits on the circle. Pure, for tests. */
export function placeSteps(programme: ConvergeProgramme): Placed[] {
  const count = programme.fields.length
  const byId = new Map(programme.steps.map((step) => [step.id, step]))
  const placed: Placed[] = []
  for (const step of programme.steps) {
    const span = spanOf(step, byId)
    if (step.kind === 'thread') {
      if (span.length !== 2) continue
      const a = onRim(span[0], count), b = onRim(span[1], count)
      const middle = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }
      // Each thread bows gently to one side, like a brush stroke, so threads
      // between distant fields never collapse into spokes through the centre.
      const dx = b.x - a.x, dy = b.y - a.y, length = Math.hypot(dx, dy) || 1
      const bow = length * 0.16
      const control = { x: CENTER + (middle.x - CENTER) * 0.35 + (-dy / length) * bow, y: CENTER + (middle.y - CENTER) * 0.35 + (dx / length) * bow }
      // The midpoint of the quadratic curve is where a deeper knot attaches.
      const at = { x: (a.x + 2 * control.x + b.x) / 4, y: (a.y + 2 * control.y + b.y) / 4 }
      placed.push({ step, at, span, path: `M ${a.x.toFixed(1)} ${a.y.toFixed(1)} Q ${control.x.toFixed(1)} ${control.y.toFixed(1)} ${b.x.toFixed(1)} ${b.y.toFixed(1)}` })
    } else if (span.length) {
      const deepest = programme.deepest_step_ids.length === 1 && programme.deepest_step_ids[0] === step.id && programme.status === 'completed'
      const radius = deepest ? 0 : ringRadius(step.level)
      const angle = meanAngle(span, count)
      placed.push({ step, span, at: { x: CENTER + Math.cos(angle) * radius, y: CENTER + Math.sin(angle) * radius } })
    }
  }
  return placed
}

function label(step: ConvergeStep, fields: ConvergeProgramme['fields']): string {
  const names = step.field_indices.map((index) => fields[index]?.name).filter(Boolean)
  const state = step.status === 'running' ? 'Being tested' : step.status === 'held' ? 'Holds' : 'Did not hold'
  if (step.kind === 'thread') return `Connection: ${names.join(' and ')}. ${step.title ?? ''} ${state}.`
  return `Level ${step.level} convergence across ${names.length} fields: ${step.title ?? 'still being tested'}. ${state}.`
}

export function Enso({ programme, selectedId, onSelect, seeking }: { programme: ConvergeProgramme; selectedId: string | null; onSelect: (id: string) => void; seeking: boolean }) {
  const count = programme.fields.length
  const placed = placeSteps(programme)
  const at = new Map(placed.map((item) => [item.step.id, item]))
  const selected = selectedId ? at.get(selectedId) : undefined
  const highlighted = new Set(selected?.span ?? [])
  const star = count > 120 ? 2.2 : count > 60 ? 2.8 : 3.6
  const levels = [...new Set(placed.filter((item) => item.step.kind === 'meta').map((item) => item.step.level))]
  return (
    <div className={`enso${count > 12 ? ' is-crowded' : ''}`} role="group" aria-label={`A circle of ${count} fields with ${placed.filter((item) => item.step.status === 'held').length} connections that hold`}>
      <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className={seeking ? 'is-seeking' : undefined}>
        <defs>
          <radialGradient id="enso-glow"><stop offset="0%" stopColor="#e8c45a" stopOpacity=".55" /><stop offset="100%" stopColor="#e8c45a" stopOpacity="0" /></radialGradient>
        </defs>
        <g aria-hidden="true">
          <circle className="enso-brush" cx={CENTER} cy={CENTER} r={RIM + 14} pathLength={1} />
          <circle className="enso-brush is-fine" cx={CENTER} cy={CENTER} r={RIM + 23} pathLength={1} />
          {levels.map((level) => <circle key={level} className="enso-ring" cx={CENTER} cy={CENTER} r={ringRadius(level)} />)}
          {seeking && <circle className="enso-seeker" r="4.5"><animateMotion dur="9s" repeatCount="indefinite" path={`M ${CENTER} ${CENTER - RIM - 14} a ${RIM + 14} ${RIM + 14} 0 1 1 -0.1 0`} /></circle>}
        </g>
        <g aria-hidden="true">
          {programme.fields.map((field, index) => {
            const point = onRim(index, count)
            const angle = angleOf(index, count)
            const showLabel = count <= LABEL_LIMIT || highlighted.has(index)
            const outward = onRim(index, count, RIM + 36)
            const anchor = Math.abs(Math.cos(angle)) < 0.2 ? 'middle' : Math.cos(angle) > 0 ? 'start' : 'end'
            return <g key={index} className={`enso-field origin-${field.origin}${field.touched ? ' is-touched' : ''}${highlighted.has(index) ? ' is-highlighted' : ''}`} style={{ animationDelay: `${Math.min(index * 18, 1600)}ms` }}>
              <circle cx={point.x} cy={point.y} r={star} />
              {showLabel && <text x={outward.x} y={outward.y + 4} textAnchor={anchor}>{field.name}</text>}
            </g>
          })}
        </g>
        <g>
          {placed.filter((item) => item.path).map(({ step, path }) => (
            <path key={step.id} d={path} pathLength={1} role="button" tabIndex={0} aria-label={label(step, programme.fields)} aria-pressed={selectedId === step.id}
              className={`enso-thread is-${step.status}${selectedId === step.id ? ' is-selected' : ''}`}
              onClick={() => onSelect(step.id)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(step.id) } }}><title>{label(step, programme.fields)}</title></path>
          ))}
          {placed.filter((item) => item.step.kind === 'meta').map(({ step, at: point }) => step.premise_step_ids.map((premise) => {
            const from = at.get(premise)?.at
            if (!from) return null
            return <path key={`${step.id}-${premise}`} aria-hidden="true" pathLength={1} className={`enso-rise is-${step.status}`} d={`M ${from.x.toFixed(1)} ${from.y.toFixed(1)} Q ${((from.x + point.x) / 2 + (CENTER - (from.x + point.x) / 2) * 0.2).toFixed(1)} ${((from.y + point.y) / 2 + (CENTER - (from.y + point.y) / 2) * 0.2).toFixed(1)} ${point.x.toFixed(1)} ${point.y.toFixed(1)}`} />
          }))}
          {placed.filter((item) => item.step.kind === 'meta').map(({ step, at: point }) => {
            const deepest = programme.deepest_step_ids.includes(step.id) && step.status === 'held'
            const radius = 4 + Math.min(step.level, 8) * 1.2
            return <g key={step.id} className={`enso-knot is-${step.status}${deepest ? ' is-deepest' : ''}${selectedId === step.id ? ' is-selected' : ''}`} transform={`translate(${point.x.toFixed(1)} ${point.y.toFixed(1)})`}
              role="button" tabIndex={0} aria-label={label(step, programme.fields)} aria-pressed={selectedId === step.id}
              onClick={() => onSelect(step.id)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(step.id) } }}>
              {deepest && <circle r={radius * 4} fill="url(#enso-glow)" className="enso-halo" />}
              <circle r={radius} />
              <title>{label(step, programme.fields)}</title>
            </g>
          })}
        </g>
      </svg>
    </div>
  )
}
