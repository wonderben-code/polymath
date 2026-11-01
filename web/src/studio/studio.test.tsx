import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { parseProgramme } from './client'
import { buildDossier, buildMetaBriefing, dossierFileName } from './dossier'
import { StudioWorkspace } from './StudioWorkspace'

const CSRF = 'a'.repeat(64)
const PROGRAMME = '11111111-1111-4111-8111-111111111111'
const BRIEF_STEP = '22222222-2222-4222-8222-222222222222'
const IDEA_A = '33333333-3333-4333-8333-333333333333'
const IDEA_B = '44444444-4444-4444-8444-444444444444'
const IDEA_C = '45454545-4545-4545-8545-454545454545'
const DESIGN = '55555555-5555-4555-8555-555555555555'
const ENGINEER = '56565656-5656-4565-8565-565656565656'
const TEST = '57575757-5757-4575-8575-575757575757'
const RUN = '66666666-6666-4666-8666-666666666666'
const RESULT_A = '77777777-7777-4777-8777-777777777777'
const RESULT_B = '88888888-8888-4888-8888-888888888888'
const RESULT_C = '99999999-9999-4999-8999-999999999999'
const DATE = '2026-11-01T00:00:00Z'
const BRIEF = 'A city helicopter quiet enough to land beside a hospital at night'

function bridge(title: string, left: string, right: string) {
  return { kind: 'bridge', title, field_left: left, field_right: right, explanation: `${title} works in ${left}.`, mappings: [{ left: `${left} mechanism`, right: 'Rotor blade' }], objection: `${title} may not scale.`, verdict: 'Partly answered.', open_question: 'Does it scale?' }
}
function answer(summary: string, claims: { label: string; text: string }[], open: string[] = []) {
  return { kind: 'answer', summary, claims, open_questions: open }
}
function step(changes: Record<string, unknown>) {
  return { id: BRIEF_STEP, stage: 'brief', ordinal: 0, target: BRIEF, status: 'pending', outcome: null, run_id: null, result_id: null, premise_result_ids: [], finding: null, ...changes }
}
function programme(changes: Record<string, unknown> = {}) {
  return { id: PROGRAMME, brief: BRIEF, specs: ['Under 65 dB at 150 metres'], wildness: 'strange', status: 'active', outcome: null, created_at: DATE, updated_at: DATE, steps: [step({})], ...changes }
}
const designed = programme({
  status: 'completed', outcome: 'designed',
  steps: [
    step({ status: 'done', outcome: 'answer', run_id: RUN, finding: answer('Rotor noise dominates.', [{ label: 'known', text: 'Blade-vortex interaction is the main source.' }], ['How could a rotor achieve quiet lift?']) }),
    step({ id: IDEA_A, stage: 'inspire', ordinal: 1, status: 'done', outcome: 'bridge', run_id: RUN, result_id: RESULT_A, finding: bridge('Serrated owl feathers', 'Ornithology', 'Aeroacoustics') }),
    step({ id: IDEA_B, stage: 'inspire', ordinal: 2, status: 'done', outcome: 'bridge', run_id: RUN, result_id: RESULT_B, finding: bridge('Spinning maple seeds', 'Botany', 'Aerospace') }),
    step({ id: IDEA_C, stage: 'inspire', ordinal: 3, status: 'done', outcome: 'bridge', run_id: RUN, result_id: RESULT_C, finding: bridge('Pleated dragonfly wings', 'Entomology', 'Aerospace') }),
    step({ id: DESIGN, stage: 'design', ordinal: 5, status: 'done', outcome: 'bridge', run_id: RUN, premise_result_ids: [RESULT_A, RESULT_B], finding: bridge('Hush: a rotor that flies like an owl', 'Ornithology', 'Botany') }),
    step({ id: ENGINEER, stage: 'engineer', ordinal: 6, status: 'done', outcome: 'answer', run_id: RUN, finding: answer('Four blades of 4.2 m.', [{ label: 'postulated', text: 'Tip speed 180 m/s from v = ωr with ω = 43 rad/s.' }]) }),
    step({ id: TEST, stage: 'test', ordinal: 7, status: 'done', outcome: 'answer', run_id: RUN, finding: answer('Plausible against the noise target.', [{ label: 'speculative', text: 'About 6 dB quieter than a plain blade.' }], ['A serration that survives rain erosion']) }),
  ],
})

function server(responses: Record<string, unknown>) {
  const calls: { url: string; method: string; body: unknown }[] = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : null })
    const key = Object.keys(responses).find((prefix) => url === prefix || url.endsWith(prefix))
    if (!key) throw new Error(`Unexpected ${url}`)
    return Response.json(responses[key])
  }))
  return calls
}

function workspace(overrides: Partial<Parameters<typeof StudioWorkspace>[0]> = {}) {
  const props = { enabled: true, csrfToken: CSRF, canSubmit: true, submit: vi.fn(async () => ({ id: RUN }) as never), onOpenRun: vi.fn(), onSolveGap: vi.fn(), onSessionExpired: vi.fn(), ...overrides }
  render(<StudioWorkspace {...props} />)
  return props
}

afterEach(() => vi.unstubAllGlobals())

describe('Innovation Studio', () => {
  it('rejects malformed programmes', () => {
    expect(() => parseProgramme({ ...programme(), wildness: 'wild' })).toThrow()
    expect(() => parseProgramme({ ...programme(), steps: [step({ stage: 'guess' })] })).toThrow()
    expect(parseProgramme(designed).steps.map((item) => item.stage)).toEqual(['brief', 'inspire', 'inspire', 'inspire', 'design', 'engineer', 'test'])
  })

  it('starts from a starter or a typed brief with requirements and a wildness, then queues the first stage', async () => {
    const calls = server({
      '/api/v1/studio': programme(),
      [`/api/v1/studio/${PROGRAMME}/next`]: { next: { state: 'due', step_id: BRIEF_STEP, stage: 'brief', mode: 'ask', goal: 'Design brief: …', parent_result_ids: [] }, programme: programme() },
      [`/api/v1/studio/${PROGRAMME}/attach`]: programme({ steps: [step({ status: 'running', run_id: RUN })] }),
    })
    const props = workspace()
    expect(screen.getByRole('heading', { name: 'Invent something that doesn’t exist yet.' })).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'A submarine inspired by whales' }))
    expect(screen.getByLabelText('What would you like to create?')).toHaveValue('A submarine inspired by whales and the living ocean')
    expect(screen.getByText('Dives to 1,000 metres')).toBeVisible()
    await userEvent.clear(screen.getByLabelText('What would you like to create?'))
    await userEvent.type(screen.getByLabelText('What would you like to create?'), BRIEF)
    await userEvent.click(screen.getByRole('button', { name: 'Remove “Nearly silent”' }))
    await userEvent.type(screen.getByLabelText('Add a requirement'), 'Carries four passengers{Enter}')
    await userEvent.click(screen.getByRole('radio', { name: 'Strange corners' }))
    expect(screen.getByRole('radio', { name: 'Strange corners' })).toHaveAttribute('aria-checked', 'true')
    await userEvent.click(screen.getByRole('button', { name: 'Invent it' }))
    await waitFor(() => expect(props.submit).toHaveBeenCalledWith('ask', 'Design brief: …', []))
    const create = calls.find((call) => call.url === '/api/v1/studio' && call.method === 'POST')
    expect(create?.body).toEqual({ brief: BRIEF, specs: ['Dives to 1,000 metres', 'Uses little energy', 'Carries four passengers'], wildness: 'strange' })
    await waitFor(() => expect(calls.some((call) => call.url.endsWith('/attach') && (call.body as { run_id: string }).run_id === RUN)).toBe(true))
    expect(screen.getByRole('list', { name: 'Progress' })).toBeVisible()
  })

  it('presents the invention, its sources, engineering and stress test, and every way to take it further', async () => {
    server({ '/api/v1/studio': { programmes: [designed] }, [`/api/v1/studio/${PROGRAMME}`]: designed })
    const props = workspace()
    await userEvent.click(await screen.findByRole('button', { name: /A city helicopter quiet enough/ }))
    expect(await screen.findByRole('heading', { level: 2, name: 'Hush: a rotor that flies like an owl' })).toBeVisible()
    expect(screen.getByText('Theoretical invention · not built or tested')).toBeVisible()
    expect(screen.getByRole('img', { name: /Ornithology: Serrated owl feathers/ })).toBeVisible()
    const ideas = within(screen.getByRole('region', { name: 'Where the ideas came from' }))
    expect(ideas.getByText('Pleated dragonfly wings')).toBeVisible()
    expect(ideas.getByText(/considered, not used/)).toBeVisible()
    expect(within(screen.getByRole('region', { name: 'Engineering and calculations' })).getByText('Postulated')).toBeVisible()
    expect(screen.getByText(/Tip speed 180 m\/s/)).toBeVisible()
    expect(within(screen.getByRole('region', { name: 'Stress test against your brief' })).getByText('Speculative')).toBeVisible()
    await userEvent.click(within(screen.getByRole('region', { name: 'Still to invent' })).getByRole('button', { name: 'Solve this' }))
    expect(props.onSolveGap).toHaveBeenCalledWith('A serration that survives rain erosion')

    const created: Blob[] = []
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn((blob: Blob) => { created.push(blob); return 'blob:dossier' }), revokeObjectURL: vi.fn() }))
    await userEvent.click(screen.getByRole('button', { name: 'Download the design dossier' }))
    expect(await created[0].text()).toContain('## Engineering and calculations')

    const writeText = vi.fn(async (_text: string) => undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    const open = vi.fn(); vi.stubGlobal('open', open)
    await userEvent.click(screen.getByRole('button', { name: 'Continue in Meta AI' }))
    expect(writeText.mock.calls[0][0]).toContain('Hush: a rotor that flies like an owl')
    expect(open).toHaveBeenCalledWith('https://www.meta.ai/', '_blank', 'noopener,noreferrer')
    expect(await screen.findByText(/Design briefing copied/)).toBeVisible()

    await userEvent.click(screen.getByRole('button', { name: 'Refine this design' }))
    expect(screen.getByLabelText('What would you like to create?')).toHaveValue(`Improve “Hush: a rotor that flies like an owl” (${BRIEF}) so that `)
    expect(screen.getByText('Under 65 dB at 150 metres', { selector: '.studio-specs span' })).toBeVisible()
  })

  it('writes a complete dossier and a bounded Meta briefing', () => {
    const parsed = parseProgramme(designed)
    const dossier = buildDossier(parsed, 'https://askpolymath.com')
    for (const heading of ['# Hush: a rotor that flies like an owl', '## The brief', '## Where the ideas came from', '## The invention', '## Engineering and calculations', '## Stress test against the brief', '## Still to invent']) expect(dossier).toContain(heading)
    expect(dossier).toContain('**Postulated.** Tip speed 180 m/s')
    expect(dossier).toContain('considered, not used')
    expect(dossierFileName(parsed)).toBe('polymath-studio-hush-a-rotor-that-flies-like-an-owl.md')
    const long = parseProgramme({ ...designed, brief: 'b'.repeat(1000) })
    long.steps[4] = { ...long.steps[4], finding: { ...(long.steps[4].finding as never as Record<string, unknown>), explanation: 'e'.repeat(1400) } as never }
    expect(buildMetaBriefing(long).length).toBeLessThanOrEqual(6000)
  })

  it('says plainly when nothing held up', async () => {
    const none = programme({ status: 'completed', outcome: 'none', steps: [step({ status: 'done', outcome: 'insufficient_evidence', run_id: RUN, finding: { kind: 'insufficient_evidence' } })] })
    server({ '/api/v1/studio': { programmes: [none] }, [`/api/v1/studio/${PROGRAMME}`]: none })
    workspace()
    await userEvent.click(await screen.findByRole('button', { name: /A city helicopter quiet enough/ }))
    expect(await screen.findByText(/No working mechanism found yet\. Try wider fields/)).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Download the design dossier' })).toBeNull()
  })
})

describe('constellation label', () => {
  it('fits a long name into two short lines', async () => {
    const { wrapLabel } = await import('./Constellation')
    expect(wrapLabel('Hush')).toEqual(['Hush'])
    expect(wrapLabel('Your invention')).toEqual(['Your', 'invention'])
    const lines = wrapLabel('A self-repairing electrode that learns from damage')
    expect(lines).toHaveLength(2)
    expect(lines.every((line) => line.length <= 13)).toBe(true)
    expect(lines[1].endsWith('…')).toBe(true)
  })
})
