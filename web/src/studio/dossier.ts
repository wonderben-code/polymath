import type { Claim, Quote, Source, StepFinding, StudioProgramme, StudioStep } from './client'

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
  references: Source[]
  /** The reference number for a step's quoted source, or null when it is not saved. */
  cite: (step: StudioStep, sourceId: string | null) => number | null
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
  const stillToInvent = [...new Set([...(test?.open_questions ?? []), ...(engineering?.open_questions ?? [])])].slice(0, 6)
  // One numbered reference list for the whole invention, in reading order.
  const references: Source[] = []
  const numbers = new Map<string, number>()
  const byStep = new Map<string, Map<string, number>>()
  for (const step of programme.steps) {
    const sources = step.finding && step.finding.kind !== 'insufficient_evidence' ? step.finding.sources : []
    const local = new Map<string, number>()
    for (const source of sources) {
      const key = source.doi ? `doi:${source.doi}` : source.url
      if (!numbers.has(key)) { references.push(source); numbers.set(key, references.length) }
      local.set(source.id, numbers.get(key)!)
    }
    byStep.set(step.id, local)
  }
  const cite = (step: StudioStep, sourceId: string | null) => sourceId === null ? null : byStep.get(step.id)?.get(sourceId) ?? null
  return { brief: answer(of('brief')[0]), inspirations, setAside, design, designStep, engineering, test, stillToInvent, references, cite }
}

// A dossier is built once as blocks, then written as a print-ready page or Markdown.
type Block =
  | { t: 'title'; text: string; note: string }
  | { t: 'h2' | 'h3' | 'p' | 'lead'; text: string }
  | { t: 'meta'; text: string }
  | { t: 'kv'; label: string; text: string }
  | { t: 'list'; items: string[] }
  | { t: 'mappings'; items: { left: string; right: string; assumption: string }[] }
  | { t: 'claims'; items: { label: string; text: string; derivation: string | null; caveats: string[]; quotes: { text: string; cite: number | null }[] }[] }
  | { t: 'quotes'; items: { text: string; cite: number | null }[] }
  | { t: 'refs'; items: Source[] }

function quotes(reading: StudioReading, step: StudioStep, items: Quote[]) {
  return items.map((item) => ({ text: item.quote, cite: reading.cite(step, item.source_id) }))
}
function claims(reading: StudioReading, step: StudioStep, items: Claim[]): Block {
  return { t: 'claims', items: items.map((claim) => ({ label: claim.label, text: claim.text, derivation: claim.derivation, caveats: claim.caveats, quotes: quotes(reading, step, claim.quotes) })) }
}

export function dossierBlocks(programme: StudioProgramme, origin: string): Block[] {
  const reading = readStudio(programme)
  const blocks: Block[] = []
  const stepOf = (stage: StudioStep['stage']) => programme.steps.find((step) => step.stage === stage)!
  blocks.push({ t: 'title', text: reading.design?.title ?? programme.brief, note: `A theoretical invention designed with Polymath Innovation Studio (${origin}), powered by Meta’s frontier Muse models. Not built or tested. Every inspiration and claim comes from a saved investigation whose quotations were checked against their sources.` })
  blocks.push({ t: 'h2', text: 'The brief' }, { t: 'lead', text: programme.brief })
  if (programme.specs.length) blocks.push({ t: 'kv', label: 'Must meet', text: '' }, { t: 'list', items: programme.specs })
  blocks.push({ t: 'kv', label: 'Looked for ideas in', text: WILDNESS_WORDS[programme.wildness] })
  if (reading.brief) blocks.push({ t: 'h2', text: 'Where today’s designs stand' }, { t: 'p', text: reading.brief.summary }, claims(reading, stepOf('brief'), reading.brief.claims))
  if (reading.inspirations.length) {
    blocks.push({ t: 'h2', text: 'Where the ideas came from' })
    for (const { step, finding, used } of reading.inspirations) {
      blocks.push({ t: 'h3', text: finding.title }, { t: 'meta', text: `${finding.field_left} → ${finding.field_right}${used ? '' : ' · considered, not used'}` }, { t: 'p', text: finding.explanation })
      if (finding.mappings.length) blocks.push({ t: 'mappings', items: finding.mappings })
      blocks.push({ t: 'kv', label: 'Where it may not transfer', text: finding.objection }, { t: 'quotes', items: quotes(reading, step, finding.quotes) })
    }
  }
  if (reading.design && reading.designStep) {
    blocks.push({ t: 'h2', text: 'The invention' }, { t: 'lead', text: reading.design.explanation })
    if (reading.design.mappings.length) blocks.push({ t: 'h3', text: 'How the sources become the design' }, { t: 'mappings', items: reading.design.mappings })
    blocks.push({ t: 'kv', label: 'Strongest objection', text: reading.design.objection }, { t: 'kv', label: 'Verdict', text: reading.design.verdict }, { t: 'kv', label: 'Limits of novelty', text: reading.design.limitations })
    blocks.push({ t: 'h3', text: 'Evidence' }, { t: 'quotes', items: quotes(reading, reading.designStep, reading.design.quotes) })
  }
  if (reading.engineering) {
    blocks.push({ t: 'h2', text: 'Engineering and calculations' }, { t: 'p', text: reading.engineering.summary }, claims(reading, stepOf('engineer'), reading.engineering.claims))
    if (reading.engineering.limitations.length) blocks.push({ t: 'kv', label: 'Limitations', text: '' }, { t: 'list', items: reading.engineering.limitations })
  }
  if (reading.test) {
    blocks.push({ t: 'h2', text: 'Stress test against the brief' }, { t: 'p', text: reading.test.summary }, claims(reading, stepOf('test'), reading.test.claims))
    if (reading.test.limitations.length) blocks.push({ t: 'kv', label: 'Limitations', text: '' }, { t: 'list', items: reading.test.limitations })
  }
  if (reading.stillToInvent.length) blocks.push({ t: 'h2', text: 'Still to invent' }, { t: 'list', items: reading.stillToInvent })
  if (reading.references.length) blocks.push({ t: 'h2', text: 'References' }, { t: 'refs', items: reading.references })
  blocks.push({ t: 'meta', text: 'Labels: Known is stated in the literature; Postulated is reasoned from it, with the working shown; Speculative goes beyond it, with its caveats. Estimates are rough and must be checked before any real use.' })
  return blocks
}

function reference(source: Source): string {
  const names = `${source.authors.join(', ')}${source.more_authors ? ' et al' : ''}`
  const authors = source.authors.length ? `${names}. ` : ''
  return `${authors}${source.title}.${source.venue ? ` ${source.venue}.` : ''}${source.year ? ` ${source.year}.` : ''}`
}
const mark = (cite: number | null) => cite === null ? '' : ` [${cite}]`

export function renderMarkdown(blocks: Block[]): string {
  const out: string[] = []
  for (const block of blocks) {
    if (block.t === 'title') out.push(`# ${block.text}`, '', `*${block.note}*`, '')
    else if (block.t === 'h2') out.push(`## ${block.text}`, '')
    else if (block.t === 'h3') out.push(`### ${block.text}`, '')
    else if (block.t === 'p' || block.t === 'lead') out.push(block.text, '')
    else if (block.t === 'meta') out.push(`*${block.text}*`, '')
    else if (block.t === 'kv') out.push(block.text ? `**${block.label}:** ${block.text}` : `**${block.label}:**`, '')
    else if (block.t === 'list') out.push(...block.items.map((item) => `- ${item}`), '')
    else if (block.t === 'mappings') out.push(...block.items.map((item) => `- ${item.left} → ${item.right}  \n  *Assumes:* ${item.assumption}`), '')
    else if (block.t === 'quotes') out.push(...block.items.map((item) => `> “${item.text}”${mark(item.cite)}`), '')
    else if (block.t === 'claims') {
      for (const claim of block.items) {
        out.push(`- **${LABEL_WORDS[claim.label] ?? claim.label}.** ${claim.text}${claim.quotes.map((item) => mark(item.cite)).join('')}`)
        if (claim.derivation) out.push(`  - *Working:* ${claim.derivation}`)
        for (const caveat of claim.caveats) out.push(`  - *Caveat:* ${caveat}`)
        for (const item of claim.quotes) out.push(`  - > “${item.text}”${mark(item.cite)}`)
      }
      out.push('')
    } else if (block.t === 'refs') out.push(...block.items.map((source, index) => `${index + 1}. ${reference(source)} ${source.doi ? `https://doi.org/${source.doi}` : source.url}`), '')
  }
  return out.join('\n')
}

const escape = (value: string) => value.replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]!)
const sup = (cite: number | null) => cite === null ? '' : `<sup><a href="#ref-${cite}">${cite}</a></sup>`

const PAGE_STYLE = `:root{--ink:#16181d;--muted:#5b606b;--line:#e8e5de;--paper:#fbfaf7}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.7 -apple-system,'Segoe UI',Inter,system-ui,sans-serif}main{max-width:820px;margin:0 auto;padding:48px 28px 72px;background:#fff;border-top:4px solid;border-image:linear-gradient(90deg,#6a45f5,#1f6fe0,#0b8a7a,#3e8a2c,#b86e00,#cc3366) 1}h1,h2,h3{font-family:'Iowan Old Style',Palatino,Georgia,serif;font-weight:400;line-height:1.2}h1{font-size:38px;margin:0 0 12px;letter-spacing:-.5px}h2{font-size:26px;margin:40px 0 12px;padding-top:18px;border-top:1px solid var(--line)}h3{font-size:20px;margin:26px 0 6px}.note,.meta{color:var(--muted);font-size:13.5px}.lead{font-family:'Iowan Old Style',Palatino,Georgia,serif;font-size:19px;line-height:1.6}blockquote{margin:8px 0;padding:6px 14px;border-left:3px solid var(--line);color:#3b3e45;font-size:14px}.label{display:inline-block;padding:1px 9px;margin-right:8px;border-radius:999px;font-size:11.5px;font-weight:600}.known{background:#eef3ee;color:#3e8a2c}.postulated{background:#eef2fb;color:#1f6fe0}.speculative{background:#fbf2e6;color:#b86e00}.claim{margin:0 0 16px;padding:14px 16px;border:1px solid var(--line);border-radius:12px}.working{margin:8px 0 0;padding:10px 12px;background:#f7f5f0;border-radius:8px;font-size:14px;white-space:pre-wrap}.caveat{margin:6px 0 0;color:#8a5a00;font-size:14px}.map{margin:6px 0;padding:10px 12px;background:#f7f5f0;border-radius:8px}.map small{display:block;color:var(--muted)}ol.refs li{margin:0 0 8px;font-size:14px;overflow-wrap:anywhere}a{color:#1f6fe0}sup a{text-decoration:none;font-size:11px}@media print{body{background:#fff}main{padding:0;border:0;max-width:none}h2{break-after:avoid}.claim,.map,blockquote{break-inside:avoid}a{color:inherit}}`

export function renderHtml(blocks: Block[], title: string): string {
  const out: string[] = []
  for (const block of blocks) {
    if (block.t === 'title') out.push(`<h1>${escape(block.text)}</h1><p class="note">${escape(block.note)}</p>`)
    else if (block.t === 'h2' || block.t === 'h3') out.push(`<${block.t}>${escape(block.text)}</${block.t}>`)
    else if (block.t === 'p') out.push(`<p>${escape(block.text)}</p>`)
    else if (block.t === 'lead') out.push(`<p class="lead">${escape(block.text)}</p>`)
    else if (block.t === 'meta') out.push(`<p class="meta">${escape(block.text)}</p>`)
    else if (block.t === 'kv') out.push(`<p><strong>${escape(block.label)}:</strong> ${escape(block.text)}</p>`)
    else if (block.t === 'list') out.push(`<ul>${block.items.map((item) => `<li>${escape(item)}</li>`).join('')}</ul>`)
    else if (block.t === 'mappings') out.push(block.items.map((item) => `<div class="map">${escape(item.left)} → ${escape(item.right)}<small>Assumes: ${escape(item.assumption)}</small></div>`).join(''))
    else if (block.t === 'quotes') out.push(block.items.map((item) => `<blockquote>“${escape(item.text)}”${sup(item.cite)}</blockquote>`).join(''))
    else if (block.t === 'claims') out.push(block.items.map((claim) => `<div class="claim"><span class="label ${escape(claim.label)}">${escape(LABEL_WORDS[claim.label] ?? claim.label)}</span>${escape(claim.text)}${claim.quotes.map((item) => sup(item.cite)).join('')}${claim.derivation ? `<div class="working"><strong>Working:</strong> ${escape(claim.derivation)}</div>` : ''}${claim.caveats.map((caveat) => `<p class="caveat">Caveat: ${escape(caveat)}</p>`).join('')}${claim.quotes.map((item) => `<blockquote>“${escape(item.text)}”${sup(item.cite)}</blockquote>`).join('')}</div>`).join(''))
    else if (block.t === 'refs') out.push(`<ol class="refs">${block.items.map((source, index) => { const link = source.doi ? `https://doi.org/${source.doi}` : source.url; return `<li id="ref-${index + 1}">${escape(reference(source))} <a href="${escape(link)}">${escape(link)}</a></li>` }).join('')}</ol>`)
  }
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escape(title)}</title><style>${PAGE_STYLE}</style></head><body><main>${out.join('\n')}</main></body></html>`
}

/** A complete, self-contained design dossier in Markdown. */
export function buildDossier(programme: StudioProgramme, origin = 'https://askpolymath.com'): string {
  return renderMarkdown(dossierBlocks(programme, origin))
}

/** The same dossier as a print-ready page: open it and print, or save as PDF. */
export function buildDossierPage(programme: StudioProgramme, origin = 'https://askpolymath.com'): string {
  return renderHtml(dossierBlocks(programme, origin), readStudio(programme).design?.title ?? programme.brief)
}

/** A compact briefing to keep refining the design in a Meta AI conversation. */
export function buildMetaBriefing(programme: StudioProgramme): string {
  const reading = readStudio(programme)
  const numbers = reading.engineering?.claims.filter((claim) => claim.derivation).slice(0, 4).map((claim) => `${claim.text} (${claim.derivation})`) ?? []
  const parts = [
    'I designed this invention with Polymath (askpolymath.com), using Meta’s frontier Muse models and Polymath’s engine across the scientific literature. Please help me refine it: question its weakest assumptions, improve the design, and check the numbers.',
    '',
    `Brief: ${programme.brief}`,
    programme.specs.length ? `Must meet: ${programme.specs.join('; ')}` : '',
    reading.design ? `\nThe invention: ${reading.design.title}. ${reading.design.explanation}` : '',
    reading.inspirations.length ? `\nInspired by: ${reading.inspirations.map(({ finding }) => `${finding.title} (${finding.field_left})`).join('; ')}` : '',
    reading.engineering ? `\nEngineering: ${reading.engineering.summary}` : '',
    numbers.length ? `Key numbers: ${numbers.join(' | ')}` : '',
    reading.test ? `\nStress test: ${reading.test.summary}` : '',
    reading.design ? `\nStrongest objection: ${reading.design.objection}` : '',
    reading.stillToInvent.length ? `\nStill to invent: ${reading.stillToInvent.join('; ')}` : '',
  ]
  const briefing = parts.filter(Boolean).join('\n')
  return briefing.length > 6000 ? `${briefing.slice(0, 5999)}…` : briefing
}

export function dossierFileName(programme: StudioProgramme, extension: 'md' | 'html' = 'md'): string {
  const title = readStudio(programme).design?.title ?? programme.brief
  const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60) || 'design'
  return `polymath-studio-${slug}.${extension}`
}
