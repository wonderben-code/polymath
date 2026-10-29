import { dollars } from './client'
import type { ConvergeProgramme, ConvergeStep } from './client'

// Markdown is built only from the saved circle; text is escaped so a title
// cannot inject headings, links or HTML into the report.
function escape(value: string): string {
  return value.replace(/[\\`*_[\]<>#|]/g, (character) => `\\${character}`).replace(/\s+/g, ' ').trim()
}

function names(step: ConvergeStep, programme: ConvergeProgramme): string {
  return step.field_indices.map((index) => programme.fields[index]?.name).filter(Boolean).map((name) => escape(name!)).join(', ')
}

export function convergeReport(programme: ConvergeProgramme, generatedAt = new Date()): string {
  const held = programme.steps.filter((step) => step.status === 'held')
  const levels = [...new Set(held.map((step) => step.level))].sort((a, b) => b - a)
  const deepest = programme.deepest_step_ids.map((id) => programme.steps.find((step) => step.id === id)).filter((step): step is ConvergeStep => !!step)
  const lines = [
    '# Polymath Converge report (experimental)',
    '',
    `Generated ${generatedAt.toISOString()}. ${programme.fields.length} fields · ${held.length} connections holding · ${dollars(programme.spent_microdollars)} of a ${dollars(programme.ceiling_microdollars)} ceiling.`,
    '',
    `**Theme:** ${programme.theme ? escape(programme.theme) : 'none (an open circle)'}`,
    '',
  ]
  if (deepest.length) {
    lines.push('## Deepest convergence', '')
    for (const step of deepest) lines.push(`- **Level ${step.level}:** ${escape(step.title ?? '')} — unites ${names(step, programme)}`)
    lines.push('')
  }
  for (const level of levels) {
    lines.push(level === 1 ? '## Connections between two fields' : `## Level ${level}`, '')
    for (const step of held.filter((item) => item.level === level)) lines.push(`- ${escape(step.title ?? '')} (${names(step, programme)})`)
    lines.push('')
  }
  lines.push('## The circle', '', programme.fields.map((field) => `${escape(field.name)}${field.touched ? ' ✓' : ''}`).join(' · '), '')
  lines.push('A connection holds only when exact quotations from saved sources support it and it survives its strongest objection. Deeper levels are model reasoning across those connections, tested against the same original evidence. Model assessments are not independent scientific verification.')
  return lines.join('\n') + '\n'
}
