import type { Claim, StepFinding, StudioProgramme, StudioStep } from './client'

export const LABEL_WORDS: Record<string, string> = { known: 'Known', postulated: 'Postulated', speculative: 'Speculative' }
export const WILDNESS_WORDS = { near: 'Nearby fields', far: 'Far fields', strange: 'Strange corners' } as const

type Bridge = Extract<StepFinding, { kind: 'bridge' }>
type Answer = Extract<StepFinding, { kind: 'answer' }>

export interface StudioReading {
  brief?: Answer
  inspirations: { step: StudioStep; finding: Bridge; used: boolean }[]
  setAside: StudioStep[]
  design?: Bridge
  designStep?: StudioStep
  engineering?: Answer
  test?: Answer
  stillToInvent: string[]
}

/** The saved stages, read once for the sheet, the dossier and the briefing. */
export function readStudio(programme: StudioProgramme): StudioReading {
  const of = (stage: StudioStep['stage']) => programme.steps.filter((step) => step.stage === stage)
  const answer = (step?: StudioStep) => step?.finding?.kind === 'answer' ? step.finding : undefined
  const designStep = of('design')[0]
  const design = designStep?.finding?.kind === 'bridge' ? designStep.finding : undefined
  const premises = new Set(designStep?.premise_result_ids ?? [])
  const inspirations = of('inspire').flatMap((step) => step.finding?.kind === 'bridge'
    ? [{ step, finding: step.finding, used: !!design && (premises.size === 0 || (step.result_id !== null && premises.has(step.result_id))) }]
    : [])
  const setAside = of('inspire').filter((step) => step.status === 'done' && step.finding?.kind !== 'bridge')
  const test = answer(of('test')[0])
  const engineering = answer(of('engineer')[0])
  const stillToInvent = [...new Set([...(test?.open_questions ?? []), ...(engineering?.open_questions ?? [])])].slice(0, 4)
  return { brief: answer(of('brief')[0]), inspirations, setAside, design, designStep, engineering, test, stillToInvent }
}

function claims(list: Claim[]): string {
  return list.map((claim) => `- **${LABEL_WORDS[claim.label] ?? claim.label}.** ${claim.text}`).join('\n')
}

/** A complete, self-contained design dossier in Markdown. */
export function buildDossier(programme: StudioProgramme, origin = 'https://askpolymath.com'): string {
  const reading = readStudio(programme)
  const lines: string[] = []
  const title = reading.design?.title ?? programme.brief
  lines.push(`# ${title}`, '', `*Designed with Polymath Innovation Studio (${origin}). A theoretical invention: not built or tested. Every inspiration and claim below comes from a saved investigation whose quotations were checked against their sources.*`, '')
  lines.push('## The brief', '', programme.brief, '')
  if (programme.specs.length) lines.push('**Must meet:**', '', ...programme.specs.map((spec) => `- ${spec}`), '')
  lines.push(`**Looked for ideas in:** ${WILDNESS_WORDS[programme.wildness]}`, '')
  if (reading.brief) lines.push('## Where today’s designs stand', '', reading.brief.summary, '', claims(reading.brief.claims), '')
  if (reading.inspirations.length) {
    lines.push('## Where the ideas came from', '')
    for (const { finding, used } of reading.inspirations) {
      lines.push(`### ${finding.title}`, '', `*${finding.field_left} → ${finding.field_right}${used ? '' : ' · considered, not used'}*`, '', finding.explanation, '', `**Where it may not transfer:** ${finding.objection}`, '')
    }
  }
  if (reading.design) {
    lines.push('## The invention', '', reading.design.explanation, '')
    if (reading.design.mappings.length) lines.push('**How the sources map into the design:**', '', ...reading.design.mappings.map((pair) => `- ${pair.left} → ${pair.right}`), '')
    lines.push(`**Strongest objection:** ${reading.design.objection}`, '', `**Verdict:** ${reading.design.verdict}`, '')
  }
  if (reading.engineering) lines.push('## Engineering and calculations', '', reading.engineering.summary, '', claims(reading.engineering.claims), '')
  if (reading.test) lines.push('## Stress test against the brief', '', reading.test.summary, '', claims(reading.test.claims), '')
  if (reading.stillToInvent.length) lines.push('## Still to invent', '', ...reading.stillToInvent.map((gap) => `- ${gap}`), '')
  lines.push('---', '', 'Labels: **Known** is stated in the literature; **Postulated** is reasoned from it; **Speculative** goes beyond it. Estimates are rough and should be checked before any real use.', '')
  return lines.join('\n')
}

/** A compact briefing to keep refining the design in a Meta AI conversation. */
export function buildMetaBriefing(programme: StudioProgramme): string {
  const reading = readStudio(programme)
  const parts = [
    'I designed this invention with Polymath (askpolymath.com), using Meta’s Muse Spark and the scientific literature. Please help me refine it: question its weakest assumptions, improve the design, and check the numbers.',
    '',
    `Brief: ${programme.brief}`,
    programme.specs.length ? `Must meet: ${programme.specs.join('; ')}` : '',
    reading.design ? `\nThe invention: ${reading.design.title}. ${reading.design.explanation}` : '',
    reading.inspirations.length ? `\nInspired by: ${reading.inspirations.map(({ finding }) => `${finding.title} (${finding.field_left})`).join('; ')}` : '',
    reading.engineering ? `\nEngineering: ${reading.engineering.summary}` : '',
    reading.test ? `\nStress test: ${reading.test.summary}` : '',
    reading.design ? `\nStrongest objection: ${reading.design.objection}` : '',
    reading.stillToInvent.length ? `\nStill to invent: ${reading.stillToInvent.join('; ')}` : '',
  ]
  const briefing = parts.filter(Boolean).join('\n')
  return briefing.length > 6000 ? `${briefing.slice(0, 5999)}…` : briefing
}

export function dossierFileName(programme: StudioProgramme): string {
  const title = readStudio(programme).design?.title ?? programme.brief
  const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60) || 'design'
  return `polymath-studio-${slug}.md`
}
