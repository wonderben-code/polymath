import type { FrontierNode, FrontierNodeStatus } from './client'

export const STATUS_LABELS: Record<FrontierNodeStatus, string> = {
  pending: 'Waiting',
  running: 'Being researched',
  surveyed: 'Surveyed',
  supported: 'Supported by the literature',
  transferred: 'Solved in another field',
  derived: 'Derived by model reasoning',
  conjectured: 'Conjectured',
  decomposed: 'Broken into steps',
  open: 'Open frontier',
  skipped: 'Skipped',
}

const KIND_LABELS = { question: 'Question', gap: 'Gap', step: 'Step' } as const
const COLUMN = 230, ROW = 74, PAD_X = 40, PAD_Y = 44

interface Placed { node: FrontierNode; x: number; y: number }

// A tidy left-to-right tree: leaves take successive rows, parents sit at the
// middle of their children. Layout depends only on the saved tree.
export function layout(nodes: FrontierNode[]): { placed: Placed[]; width: number; height: number } {
  const children = new Map<string | null, FrontierNode[]>()
  for (const node of nodes) children.set(node.parent_id, [...(children.get(node.parent_id) ?? []), node])
  for (const list of children.values()) list.sort((left, right) => left.ordinal - right.ordinal)
  const placed = new Map<string, Placed>()
  let row = 0, maxDepth = 0
  const visit = (node: FrontierNode, depth: number): number => {
    maxDepth = Math.max(maxDepth, depth)
    const kids = children.get(node.id) ?? []
    const y = kids.length ? kids.map((child) => visit(child, depth + 1)).reduce((sum, value) => sum + value, 0) / kids.length : PAD_Y + ROW * row++
    placed.set(node.id, { node, x: PAD_X + depth * COLUMN, y })
    return y
  }
  for (const root of children.get(null) ?? []) visit(root, 0)
  return { placed: [...placed.values()], width: PAD_X * 2 + maxDepth * COLUMN + 200, height: Math.max(140, PAD_Y * 2 + ROW * Math.max(0, row - 1)) }
}

function clip(value: string, length: number): string {
  return value.length > length ? `${value.slice(0, length - 1)}…` : value
}

function Shape({ kind, status }: { kind: FrontierNode['kind']; status: FrontierNodeStatus }) {
  const className = `frontier-shape status-${status}`
  if (kind === 'question') return <><circle className="frontier-question-ring" r="17" /><circle className={className} r="11" /></>
  if (kind === 'gap') return <rect className={className} x="-9" y="-9" width="18" height="18" rx="3" transform="rotate(45)" />
  return <circle className={className} r="9" />
}

export function FrontierMap({ nodes, selectedId, onSelect }: { nodes: FrontierNode[]; selectedId: string | null; onSelect: (id: string) => void }) {
  const { placed, width, height } = layout(nodes)
  const byId = new Map(placed.map((item) => [item.node.id, item]))
  return (
    <div className="frontier-map" role="region" aria-label="Frontier map" tabIndex={0}>
      <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="group" aria-label={`Frontier map with ${nodes.length} nodes`}>
        <defs>
          <linearGradient id="frontier-edge" x1="0" x2="1"><stop offset="0%" stopColor="#9fb3a6" /><stop offset="100%" stopColor="#c7cfc2" /></linearGradient>
        </defs>
        {placed.map(({ node, x, y }) => {
          const parent = node.parent_id ? byId.get(node.parent_id) : undefined
          if (!parent) return null
          const middle = (parent.x + x) / 2
          return <path key={`edge-${node.id}`} className={`frontier-edge${node.status === 'skipped' ? ' is-skipped' : ''}`} d={`M ${parent.x + 12} ${parent.y} C ${middle} ${parent.y}, ${middle} ${y}, ${x - 12} ${y}`} />
        })}
        {placed.map(({ node, x, y }) => {
          const status = node.resolved_status
          const label = `${KIND_LABELS[node.kind]}: ${node.statement}. ${STATUS_LABELS[status]}.`
          return (
            <g key={node.id} className={`frontier-node frontier-arrive${selectedId === node.id ? ' is-selected' : ''}${node.status === 'running' ? ' is-running' : ''}`} transform={`translate(${x} ${y})`}>
              <g role="button" tabIndex={0} aria-label={label} aria-pressed={selectedId === node.id} onClick={() => onSelect(node.id)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(node.id) } }}>
                {node.status === 'running' && <circle className="frontier-pulse" r="14" />}
                <Shape kind={node.kind} status={node.status === 'decomposed' ? status : node.status} />
                <text x="22" y="-4" className="frontier-node-kind">{KIND_LABELS[node.kind].toUpperCase()} · {STATUS_LABELS[status]}</text>
                <text x="22" y="13" className="frontier-node-text">{clip(node.statement, 34)}</text>
                <title>{label}</title>
              </g>
            </g>
          )
        })}
      </svg>
    </div>
  )
}
