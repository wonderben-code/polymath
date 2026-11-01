import type { SolveProgramme } from './client'

const SPECTRUM = ['var(--violet)', 'var(--blue)', 'var(--teal)', 'var(--green)', 'var(--amber)', 'var(--rose)']

/** One key: its teeth at the tip, a shaft of coloured segments, and a ring to hold. */
function Key({ x, y, colours, faint = false, seeking = false }: { x: number; y: number; colours: string[]; faint?: boolean; seeking?: boolean }) {
  const length = 150, each = length / Math.max(colours.length, 1)
  return (
    <g className={`lk-key${faint ? ' is-faint' : ''}${seeking ? ' is-seeking' : ''}`} transform={`translate(${x} ${y})`}>
      {colours.map((colour, index) => <line key={index} x1={index * each} y1={0} x2={(index + 1) * each} y2={0} stroke={colour} strokeWidth={7} strokeLinecap={index === 0 ? 'round' : 'butt'} />)}
      <path d="M 8 0 v 14 h 9 v -8 h 9 v 12 h 9 V 0" fill="none" stroke={colours[0]} strokeWidth={5} strokeLinejoin="round" />
      <circle cx={length + 22} cy={0} r={20} fill="none" stroke={colours[colours.length - 1]} strokeWidth={7} />
    </g>
  )
}

/** The problem as a lock; keys found, close fits, and a key forged from them. */
export function LockAndKey({ programme }: { programme: SolveProgramme }) {
  const fits = programme.steps.filter((step) => step.stage === 'fit')
  const held = fits.filter((step) => step.outcome === 'bridge')
  const forge = programme.steps.find((step) => step.stage === 'forge')
  const colourOf = (index: number) => SPECTRUM[index % SPECTRUM.length]
  const inserted = programme.outcome === 'found' ? ['var(--ink)'] : programme.outcome === 'forged' ? held.map((_, index) => colourOf(index)) : null
  const searching = programme.status === 'active' && !programme.outcome
  const description = programme.outcome === 'found' ? 'An existing key fits the lock.' : programme.outcome === 'forged' ? `A new key forged from ${held.length} close fits fits the lock.` : programme.outcome === 'close' ? `${held.length} close ${held.length === 1 ? 'fit' : 'fits'} found; no key fits fully.` : programme.outcome === 'none' ? 'No key fits yet.' : 'Searching for a key.'
  return (
    <figure className="lk" aria-label={description}>
      <svg viewBox="0 0 660 230" role="img" aria-hidden="true">
        <g className={`lk-lock${inserted ? ' is-open' : ''}`}>
          <path className="lk-shackle" d="M 64 92 V 64 a 41 41 0 0 1 82 0 V 92" fill="none" strokeWidth={9} />
          <rect x={44} y={90} width={122} height={104} rx={18} />
          <circle cx={105} cy={134} r={10} className="lk-hole" />
          <rect x={101} y={138} width={8} height={26} rx={3} className="lk-hole" />
        </g>
        {inserted
          ? <Key x={112} y={142} colours={inserted} />
          : <>
            {(held.length ? held : fits).slice(0, 3).map((step, index) => <Key key={step.id} x={300} y={60 + index * 55} colours={[step.outcome === 'bridge' ? colourOf(index) : 'var(--line)']} faint={step.outcome !== 'bridge'} seeking={step.status === 'running'} />)}
            {searching && fits.length === 0 && <Key x={300} y={115} colours={['var(--line)']} faint seeking />}
          </>}
        {forge && forge.status === 'running' && <Key x={300} y={200} colours={held.map((_, index) => colourOf(index))} seeking />}
      </svg>
      <figcaption>{description}</figcaption>
    </figure>
  )
}
