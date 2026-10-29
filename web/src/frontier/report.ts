import type { FrontierNode, FrontierNodeStatus, FrontierProgramme } from './client'
import { STATUS_LABELS } from './FrontierMap'

const KIND = { question: 'Question', gap: 'Gap', step: 'Step', convergence: 'Convergence' } as const
const STRENGTH: FrontierNodeStatus[] = ['supported', 'transferred', 'derived', 'conjectured', 'open']

// Markdown is built only from the saved programme snapshot; text is escaped so
// a statement cannot inject headings, links or HTML into the report.
function escape(value: string): string {
  return value.replace(/[\\`*_[\]<>#|]/g, (character) => `\\${character}`).replace(/\s+/g, ' ').trim()
}

function weakestOf(nodes: FrontierNode[]): FrontierNodeStatus | null {
  const ranked = nodes.map((node) => node.resolved_status).filter((status) => STRENGTH.includes(status))
  return ranked.length ? ranked.reduce((worst, status) => (STRENGTH.indexOf(status) > STRENGTH.indexOf(worst) ? status : worst)) : null
}

export function frontierReport(programme: FrontierProgramme, generatedAt = new Date()): string {
  const children = new Map<string | null, FrontierNode[]>()
  for (const node of programme.nodes) children.set(node.parent_id, [...(children.get(node.parent_id) ?? []), node])
  for (const list of children.values()) list.sort((left, right) => left.ordinal - right.ordinal)
  const lines: string[] = []
  const tree = (node: FrontierNode, depth: number) => {
    lines.push(`${'  '.repeat(depth)}- **${KIND[node.kind]}** (${STATUS_LABELS[node.resolved_status]}): ${escape(node.statement)}`)
    for (const child of children.get(node.id) ?? []) tree(child, depth + 1)
  }
  for (const root of children.get(null) ?? []) tree(root, 0)
  const resolvable = programme.nodes.filter((node) => node.kind !== 'question' && node.parent_id !== null && programme.nodes.find((item) => item.id === node.parent_id)?.kind === 'question')
  const weakest = weakestOf(resolvable)
  const open = programme.nodes.filter((node) => node.status === 'open')
  const counts = STRENGTH.map((status) => `${STATUS_LABELS[status]}: ${programme.nodes.filter((node) => node.resolved_status === status).length}`)
  return [
    `# Frontier report: ${escape(programme.question)}`,
    '',
    `*Experimental Polymath Frontier programme · generated ${generatedAt.toISOString()} · status: ${programme.status}*`,
    '',
    programme.success_criteria ? `**Success criteria:** ${escape(programme.success_criteria)}\n` : '',
    '## Proposed solve path',
    '',
    weakest ? `This is a proposed solve path. Its weakest top-level branch is **${STATUS_LABELS[weakest].toLowerCase()}**, so the path is only as strong as that.` : 'No branch has been resolved yet.',
    '',
    ...lines,
    '',
    '## Open frontier',
    '',
    ...(open.length ? open.map((node) => `- ${escape(node.statement)}`) : ['- No node is currently marked open.']),
    '',
    '## Summary',
    '',
    `${programme.attempts_used} of ${programme.limits.max_attempts} investigations used. ${counts.join(' · ')}.`,
    '',
    '## How to read this',
    '',
    'Supported means exact quotations from saved sources. Solved in another field means a cross-field bridge with its mapping and objection. Derived and conjectured steps are model reasoning, labelled as such. Open means attempted and still unresolved. Each node links to an ordinary Polymath investigation with its own sources.',
    '',
  ].filter((line, index, all) => !(line === '' && all[index - 1] === '')).join('\n')
}
