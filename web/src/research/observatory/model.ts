import type { ResearchActivityEvent } from '../types'

// Everything here is derived from recorded research events. Summaries are
// parsed only to label the picture; an unrecognised wording still counts as a
// generic step and never invents data.

export type SearchPurpose = 'discovery' | 'counterevidence' | 'prior_work'
export type HypothesisState = 'proposed' | 'revised' | 'discarded'

export interface ObservedSearch { sequence: number; purpose: SearchPurpose; query: string; found: number; partial: boolean }
export interface ObservedRead { sequence: number; title: string; kind: 'abstract' | 'full_text' | 'excerpt'; revisit: boolean }
export interface ObservedHypothesis { sequence: number; state: HypothesisState; text: string }
export interface ObservedMemory { sequence: number; label: string }

export interface ObservatoryState {
  searches: ObservedSearch[]
  reads: ObservedRead[]
  memories: ObservedMemory[]
  hypotheses: ObservedHypothesis[]
  fields: string[]
  setbacks: number
  lastSetback: number | null
  modelCalls: number
  thinking: number | null
  findings: number
  concluded: boolean
  latest: ResearchActivityEvent | null
  narration: string
}

const SEARCH = /^Searched literature for (discovery|counterevidence|prior_work): "([\s\S]*)"; found (\d+) sources\.( Some providers were unavailable\.)?/
const READ = /^Read (abstract|full_text): ([\s\S]+)$/
const REVISIT = /^Revisited (?:original|saved) (abstract|full_text): ([\s\S]+)$/
const WINDOW = /arXiv (\S+?)v(\d+);/
const NOTE = /^(proposed|revised|discarded): ([\s\S]+)$/
const FIELDS = /^Selected fields: ([\s\S]+?)\. /
const MEMORY_SEARCH = /^Searched saved research for "([\s\S]*)"/
const MEMORY_READ = /^Revisited saved finding: ([\s\S]+)$/
const STAGE = /^Saved discovery stage \d+: ([\s\S]+)$/

const purposeWords: Record<SearchPurpose, string> = {
  discovery: 'Searching the literature for',
  counterevidence: 'Looking for evidence against',
  prior_work: 'Checking whether anyone has already found',
}

function ordered(events: ResearchActivityEvent[]): ResearchActivityEvent[] {
  const distinct = new Map<number, ResearchActivityEvent>()
  for (const event of events) if (Number.isInteger(event.sequence) && event.sequence > 0 && !distinct.has(event.sequence)) distinct.set(event.sequence, event)
  return [...distinct.values()].sort((left, right) => left.sequence - right.sequence)
}

function clip(value: string, length = 90): string {
  const text = value.replace(/\s+/g, ' ').trim()
  return text.length > length ? `${text.slice(0, length - 1)}…` : text
}

export function narrate(event: ResearchActivityEvent | null, state: Pick<ObservatoryState, 'thinking'>, terminal: boolean): string {
  if (state.thinking !== null && !terminal) return 'Meta’s model is thinking about what to do next.'
  if (!event) return terminal ? 'This investigation recorded no research activity.' : 'Preparing the investigation.'
  const summary = event.summary
  switch (event.kind) {
    case 'queued': return 'Waiting for a research slot.'
    case 'running': return 'The investigation has started.'
    case 'search_completed': {
      const match = SEARCH.exec(summary)
      return match ? `${purposeWords[match[1] as SearchPurpose]} “${clip(match[2], 60)}”, found ${match[3]}.` : 'Searched the literature.'
    }
    case 'source_read': { const match = READ.exec(summary); return match ? `Reading ${match[1] === 'abstract' ? 'the abstract of' : ''} “${clip(match[2])}”.`.replace('  ', ' ') : 'Reading a source.' }
    case 'full_text_opened': return 'Opening a paper’s table of contents.'
    case 'full_text_window_read': return 'Reading a chosen section of the full paper.'
    case 'stage_source_read':
    case 'memory_source_read': { const match = REVISIT.exec(summary); return match ? `Rereading “${clip(match[2])}”.` : 'Rereading an earlier source.' }
    case 'memory_searched': { const match = MEMORY_SEARCH.exec(summary); return match ? `Searching its own earlier findings for “${clip(match[1], 60)}”.` : 'Searching earlier findings.' }
    case 'memory_finding_read': case 'stage_parent_read': return 'Revisiting an earlier finding.'
    case 'research_note': {
      const match = NOTE.exec(summary)
      if (!match) return 'Recording a hypothesis.'
      return match[1] === 'discarded' ? 'Discarding a hypothesis that did not hold up.' : match[1] === 'revised' ? 'Revising a hypothesis.' : 'Forming a new hypothesis.'
    }
    case 'fields_selected': { const match = FIELDS.exec(summary); return match ? `Chose the fields: ${clip(match[1], 80)}.` : 'Chose the fields to explore.' }
    case 'parents_selected': return 'Choosing earlier bridges to build on.'
    case 'stage_committed': { const match = STAGE.exec(summary); return match ? `Saved a bridge: “${clip(match[1])}”.` : 'Saved a bridge.' }
    case 'explore_ranked': return 'Ranking the bridges it found.'
    case 'concluded': case 'discovery_finished': case 'explore_finished': return 'Reached a conclusion.'
    case 'artifact_basis_loaded': return 'Opening the saved finding and its quoted evidence.'
    case 'finite_check_completed': return 'Ran a bounded mathematical check (not a proof).'
    case 'artifact_draft_ready': return 'A cited draft is ready.'
    case 'artifact_insufficient': return 'The saved evidence did not support a useful draft.'
    case 'tool_failed': case 'model_failed': case 'action_rejected': case 'artifact_action_rejected': return 'Hit a setback and is adjusting.'
    case 'stopped': return 'Stopped at its research limits.'
    case 'cancelled': return 'The investigation was cancelled.'
    case 'interrupted': return 'The investigation paused and is waiting for review.'
    default: return clip(summary, 110)
  }
}

export function observe(events: ResearchActivityEvent[], terminal = false): ObservatoryState {
  const state: ObservatoryState = { searches: [], reads: [], memories: [], hypotheses: [], fields: [], setbacks: 0, lastSetback: null, modelCalls: 0, thinking: null, findings: 0, concluded: false, latest: null, narration: '' }
  const notes = new Map<string, ObservedHypothesis>()
  for (const event of ordered(events)) {
    const summary = event.summary
    switch (event.kind) {
      case 'external_started': state.modelCalls += 1; state.thinking = event.sequence; continue
      case 'external_observed': case 'external_reconciled': state.thinking = null; continue
      case 'checkpoint': continue
      case 'search_completed': {
        const match = SEARCH.exec(summary)
        state.searches.push(match ? { sequence: event.sequence, purpose: match[1] as SearchPurpose, query: match[2], found: Math.min(Number(match[3]), 50), partial: !!match[4] } : { sequence: event.sequence, purpose: 'discovery', query: '', found: 0, partial: false })
        break
      }
      case 'source_read': { const match = READ.exec(summary); state.reads.push({ sequence: event.sequence, title: match?.[2] ?? 'A source', kind: match?.[1] === 'full_text' ? 'full_text' : 'abstract', revisit: false }); break }
      case 'full_text_window_read': { const match = WINDOW.exec(summary); state.reads.push({ sequence: event.sequence, title: match ? `arXiv ${match[1]}` : 'A full-text excerpt', kind: 'excerpt', revisit: false }); break }
      case 'stage_source_read': case 'memory_source_read': { const match = REVISIT.exec(summary); state.reads.push({ sequence: event.sequence, title: match?.[2] ?? 'An earlier source', kind: match?.[1] === 'full_text' ? 'full_text' : 'abstract', revisit: true }); break }
      case 'memory_searched': { const match = MEMORY_SEARCH.exec(summary); state.memories.push({ sequence: event.sequence, label: match?.[1] ?? 'Earlier findings' }); break }
      case 'memory_finding_read': case 'stage_parent_read': { const match = MEMORY_READ.exec(summary); state.memories.push({ sequence: event.sequence, label: match?.[1] ?? 'An earlier finding' }); break }
      case 'research_note': {
        const match = NOTE.exec(summary)
        if (match) {
          const key = clip(match[2], 60).toLowerCase()
          const existing = notes.get(key)
          if (existing) existing.state = match[1] as HypothesisState
          else { const note = { sequence: event.sequence, state: match[1] as HypothesisState, text: match[2] }; notes.set(key, note); state.hypotheses.push(note) }
        }
        break
      }
      case 'fields_selected': { const match = FIELDS.exec(summary); if (match) state.fields = match[1].split(', ').map((field) => clip(field, 40)).filter(Boolean).slice(0, 6); break }
      case 'stage_committed': state.findings += 1; break
      case 'concluded': case 'discovery_finished': case 'explore_finished': state.concluded = true; break
      case 'artifact_draft_ready': case 'artifact_insufficient': state.concluded = true; break
      case 'tool_failed': case 'model_failed': case 'action_rejected': case 'artifact_action_rejected': state.setbacks += 1; state.lastSetback = event.sequence; break
    }
    state.latest = event
  }
  if (terminal) state.thinking = null
  state.narration = narrate(state.latest, state, terminal)
  return state
}

export function papersFound(state: ObservatoryState): number {
  return state.searches.reduce((total, search) => total + search.found, 0)
}
