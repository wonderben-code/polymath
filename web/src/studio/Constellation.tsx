import type { StudioProgramme } from './client'
import { readStudio } from './dossier'

export const SPECTRUM = ['var(--violet)', 'var(--blue)', 'var(--green)', 'var(--amber)', 'var(--teal)', 'var(--rose)']
const SLOTS = [[86, 64], [374, 64], [86, 256], [374, 256]] as const
const CENTRE = [230, 160] as const

function clip(value: string, max: number): string { return value.length > max ? `${value.slice(0, max - 1).trimEnd()}…` : value }

/** Up to two short lines that fit inside the invention's circle. */
export function wrapLabel(value: string, width = 12): string[] {
  const words = value.split(/\s+/).filter(Boolean)
  const lines: string[] = []
  let line = ''
  for (const word of words) {
    const next = line ? `${line} ${word}` : word
    if (next.length <= width || !line) { line = next; continue }
    lines.push(line); line = word
    if (lines.length === 2) break
  }
  if (lines.length < 2 && line) lines.push(line)
  const used = lines.join(' ').length
  return lines.slice(0, 2).map((item, index) => clip(index === 1 && used < value.replace(/\s+/g, ' ').trim().length ? `${item}…` : item, width + 1))
}

/** Where the ideas came from: each inspiration in its field's colour, flowing into the invention. */
export function Constellation({ programme }: { programme: StudioProgramme }) {
  const reading = readStudio(programme)
  const inspirations = programme.steps.filter((step) => step.stage === 'inspire').slice(0, SLOTS.length)
  const designing = programme.steps.some((step) => step.stage === 'design' && step.status !== 'done')
  const label = wrapLabel(reading.design ? reading.design.title.split(':')[0] : designing ? 'Inventing…' : 'Your invention')
  const described = inspirations.map((step) => step.finding?.kind === 'bridge' ? `${step.finding.field_left}: ${step.finding.title}` : null).filter(Boolean).join('; ')
  return (
    <figure className="constellation">
      <svg viewBox="0 0 460 320" role="img" aria-label={`Where the ideas came from${described ? `: ${described}` : ''}`}>
        <defs>
          <linearGradient id="studio-spectrum" x1="0" x2="1" y1="0" y2="1">
            <stop offset="0" stopColor="#6a45f5" /><stop offset=".35" stopColor="#1f6fe0" /><stop offset=".65" stopColor="#3e8a2c" /><stop offset="1" stopColor="#b86e00" />
          </linearGradient>
        </defs>
        {inspirations.map((step, index) => {
          const [x, y] = SLOTS[index]
          const colour = SPECTRUM[index % SPECTRUM.length]
          const held = step.finding?.kind === 'bridge'
          const used = reading.inspirations.find((item) => item.step.id === step.id)?.used ?? false
          const state = held ? (used || !reading.design ? 'held' : 'unused') : step.status === 'done' || step.status === 'failed' ? 'empty' : 'seeking'
          const bend = (x < CENTRE[0] ? 60 : -60)
          return (
            <g key={step.id} className={`constellation-node is-${state}`} style={{ ['--node' as string]: colour }}>
              <path d={`M${x} ${y} C ${x + bend} ${y}, ${CENTRE[0] - bend / 2} ${CENTRE[1]}, ${CENTRE[0]} ${CENTRE[1]}`} className="constellation-thread" />
              <circle cx={x} cy={y} r={held ? 9 : 7} className="constellation-dot" />
              <text x={x} y={y < CENTRE[1] ? y - 18 : y + 28} textAnchor="middle" className="constellation-field">{held && step.finding?.kind === 'bridge' ? clip(step.finding.field_left, 22) : state === 'seeking' ? 'Searching…' : 'No fit found'}</text>
              {held && step.finding?.kind === 'bridge' && <text x={x} y={y < CENTRE[1] ? y + 26 : y - 16} textAnchor="middle" className="constellation-idea">{clip(step.finding.title, 26)}</text>}
            </g>
          )
        })}
        <circle cx={CENTRE[0]} cy={CENTRE[1]} r={36} className={`constellation-core${reading.design ? ' is-made' : ''}`} />
        <text x={CENTRE[0]} y={CENTRE[1] + (label.length > 1 ? -3 : 4)} textAnchor="middle" className="constellation-core-label">{label.map((line, index) => <tspan key={index} x={CENTRE[0]} dy={index ? 14 : 0}>{line}</tspan>)}</text>
      </svg>
      <figcaption>Idea map</figcaption>
    </figure>
  )
}
