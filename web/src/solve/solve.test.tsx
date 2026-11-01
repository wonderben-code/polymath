import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { parseProgramme } from './client'
import { SolveComposer } from './SolveComposer'
import { SolveWorkspace } from './SolveWorkspace'

const CSRF = 'a'.repeat(64)
const PROGRAMME = '11111111-1111-4111-8111-111111111111'
const FIND = '22222222-2222-4222-8222-222222222222'
const FIT_A = '33333333-3333-4333-8333-333333333333'
const FIT_B = '44444444-4444-4444-8444-444444444444'
const FORGE = '55555555-5555-4555-8555-555555555555'
const RUN = '66666666-6666-4666-8666-666666666666'
const RESULT_A = '77777777-7777-4777-8777-777777777777'
const RESULT_B = '88888888-8888-4888-8888-888888888888'
const DATE = '2026-11-01T00:00:00Z'

function bridge(title: string, left: string, right: string) {
  return { kind: 'bridge', title, field_left: left, field_right: right, explanation: `${title} fits the core of the problem.`, falls_short: `${title} ignores temperature.`, verdict: 'The objection is partly answered.', open_question: 'Does it scale?' }
}
function step(changes: Record<string, unknown>) {
  return { id: FIND, stage: 'find', ordinal: 0, target: 'Synthetic problem', status: 'pending', outcome: null, run_id: null, result_id: null, premise_result_ids: [], finding: null, ...changes }
}
function programme(changes: Record<string, unknown> = {}) {
  return { id: PROGRAMME, problem: 'Keep a synthetic battery charged for a thousand cycles', status: 'active', outcome: null, frontier_programme_id: null, frontier_node_id: null, created_at: DATE, updated_at: DATE, steps: [step({})], ...changes }
}
const forged = programme({
  status: 'completed', outcome: 'forged',
  steps: [
    step({ status: 'done', outcome: 'answer', run_id: RUN, result_id: RESULT_A, finding: { kind: 'answer', summary: 'Partial answers exist.', known: ['Coatings slow decay.'], other: [], unsolved: ['How to stop dendrites?'] } }),
    step({ id: FIT_A, stage: 'fit', ordinal: 1, status: 'done', outcome: 'bridge', run_id: RUN, result_id: RESULT_A, finding: bridge('Self-healing polymers', 'Materials science', 'Batteries') }),
    step({ id: FIT_B, stage: 'fit', ordinal: 2, status: 'done', outcome: 'bridge', run_id: RUN, result_id: RESULT_B, finding: bridge('Immune memory', 'Immunology', 'Batteries') }),
    step({ id: FORGE, stage: 'forge', ordinal: 4, status: 'done', outcome: 'bridge', run_id: RUN, premise_result_ids: [RESULT_A, RESULT_B], finding: bridge('A self-repairing electrode', 'Materials science', 'Immunology') }),
  ],
})

afterEach(() => vi.unstubAllGlobals())

describe('Solve', () => {
  it('rejects a malformed programme', () => {
    expect(() => parseProgramme({ ...programme(), outcome: 'solved' })).toThrow()
    expect(() => parseProgramme({ ...programme(), steps: [step({ stage: 'guess' })] })).toThrow()
  })

  it('starts a problem from the composer', async () => {
    const created = vi.fn()
    const calls: { url: string; body: unknown }[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => { calls.push({ url: String(input), body: init?.body ? JSON.parse(String(init.body)) : null }); return Response.json(programme()) }))
    render(<SolveComposer csrfToken={CSRF} ready canSubmit onCreated={created} onSessionExpired={vi.fn()} />)
    expect(screen.getByRole('button', { name: /Find or forge a key/ })).toBeDisabled()
    await userEvent.type(screen.getByLabelText('Describe the problem you want solved'), 'Keep a battery charged')
    await userEvent.click(screen.getByRole('button', { name: /Find or forge a key/ }))
    await waitFor(() => expect(created).toHaveBeenCalled())
    expect(calls[0]).toEqual({ url: '/api/v1/solve', body: { problem: 'Keep a battery charged' } })
  })

  it('queues each due stage through the ordinary investigation route and attaches it', async () => {
    const submit = vi.fn(async () => ({ id: RUN }) as never)
    const calls: { url: string; body: unknown }[] = []
    let attached = false
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null })
      if (url === '/api/v1/solve') return Response.json({ programmes: [] })
      if (url === `/api/v1/solve/${PROGRAMME}`) return Response.json(programme())
      if (url.endsWith('/next')) return Response.json(attached ? { next: { state: 'waiting', run_id: RUN }, programme: programme({ steps: [step({ status: 'running', run_id: RUN })] }) } : { next: { state: 'due', step_id: FIND, stage: 'find', mode: 'ask', goal: 'Has this problem already been solved, in any field of science?', parent_result_ids: [] }, programme: programme() })
      if (url.endsWith('/attach')) { attached = true; return Response.json(programme({ steps: [step({ status: 'running', run_id: RUN })] })) }
      throw new Error(`Unexpected ${url}`)
    }))
    render(<SolveWorkspace enabled csrfToken={CSRF} canSubmit focusId={PROGRAMME} submit={submit} onOpenRun={vi.fn()} onSessionExpired={vi.fn()} />)
    await waitFor(() => expect(submit).toHaveBeenCalledWith('ask', 'Has this problem already been solved, in any field of science?', []))
    await waitFor(() => expect(calls.find((call) => call.url.endsWith('/attach'))?.body).toEqual({ step_id: FIND, stage: 'find', run_id: RUN }))
    expect(await screen.findByText(/Looking for an existing key anywhere in science/)).toBeInTheDocument()
  })

  it('explains a forged key in plain words, with where each close fit fits and falls short', async () => {
    const opened = vi.fn()
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/v1/solve') return Response.json({ programmes: [{ ...forged, steps: undefined }] })
      if (url === `/api/v1/solve/${PROGRAMME}`) return Response.json(forged)
      if (url.endsWith('/next')) return Response.json({ next: { state: 'completed', outcome: 'forged' }, programme: forged })
      throw new Error(`Unexpected ${url}`)
    }))
    render(<SolveWorkspace enabled csrfToken={CSRF} canSubmit focusId={PROGRAMME} submit={vi.fn()} onOpenRun={opened} onSessionExpired={vi.fn()} />)
    expect(await screen.findByRole('heading', { name: 'A new key was forged' })).toBeInTheDocument()
    expect(screen.getByText(/not yet independently verified/)).toBeInTheDocument()
    expect(screen.getByRole('figure', { name: 'A new key forged from 2 close fits fits the lock.' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'A self-repairing electrode' })).toBeInTheDocument()
    expect(screen.getByText('Immune memory ignores temperature.')).toBeInTheDocument()
    await userEvent.click(screen.getAllByRole('button', { name: 'Open the investigation' })[0])
    expect(opened).toHaveBeenCalledWith(RUN)
  })
})
