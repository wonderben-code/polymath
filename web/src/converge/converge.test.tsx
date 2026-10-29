import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { parseProgramme } from './client'
import type { ConvergeStep } from './client'
import { ConvergeComposer } from './ConvergeComposer'
import { ConvergeWorkspace } from './ConvergeWorkspace'
import { placeSteps, ringRadius } from './Enso'
import { convergeReport } from './report'

const PID = '11111111-1111-4111-8111-111111111111'
const T1 = '22222222-2222-4222-8222-222222222222'
const T2 = '33333333-3333-4333-8333-333333333333'
const M1 = '44444444-4444-4444-8444-444444444444'
const RUN = '55555555-5555-4555-8555-555555555555'
const R1 = '66666666-6666-4666-8666-666666666666'
const R2 = '77777777-7777-4777-8777-777777777777'
const CSRF = 'a'.repeat(64)

function step(overrides: Partial<ConvergeStep> = {}): ConvergeStep {
  return { id: T1, kind: 'thread', level: 1, status: 'held', title: 'A synthetic shared structure', field_left: 'Ecology', field_right: 'Optics', field_indices: [0, 1], premise_step_ids: [], run_id: RUN, result_id: R1, ...overrides }
}

function programme(steps: ConvergeStep[] = [step()], extra: Record<string, unknown> = {}) {
  return {
    id: PID, theme: 'A synthetic theme', status: 'active', field_count: 4, connections_held: steps.filter((item) => item.status === 'held').length, created_at: '2026-10-29T00:00:00Z', updated_at: '2026-10-29T00:00:00Z',
    finished_reason: null, fields: ['Ecology', 'Optics', 'Economics', 'Linguistics'].map((name, index) => ({ name, origin: index === 0 ? 'chosen' : 'spread', touched: index < 2 })),
    ceiling_microdollars: 20_000_000, spent_microdollars: 1_500_000, step_estimate_microdollars: 500_000, max_steps: 400, steps_used: steps.length, deepest_step_ids: [], steps, ...extra,
  }
}

const deep = () => [
  step(),
  step({ id: T2, field_left: 'Economics', field_right: 'Linguistics', field_indices: [2, 3], run_id: '88888888-8888-4888-8888-888888888888', result_id: R2 }),
  step({ id: M1, kind: 'meta', level: 2, title: 'What both connections share', field_left: 'Living systems', field_right: 'Signals', field_indices: [0, 1, 2, 3], premise_step_ids: [T1, T2], run_id: '99999999-9999-4999-8999-999999999999', result_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' }),
]

afterEach(() => { vi.unstubAllGlobals() })

describe('converge client and circle', () => {
  it('parses strictly and rejects steps that point outside the circle', () => {
    expect(parseProgramme(programme()).steps).toHaveLength(1)
    expect(() => parseProgramme(programme([step({ field_indices: [0, 9] })]))).toThrow('invalid_response')
    expect(() => parseProgramme(programme([step({ premise_step_ids: [M1] })]))).toThrow('invalid_response')
  })

  it('draws threads across the circle and gathers deeper levels toward the centre', () => {
    const parsed = parseProgramme(programme(deep(), { deepest_step_ids: [M1], status: 'completed' }))
    const placed = placeSteps(parsed)
    expect(placed.filter((item) => item.path)).toHaveLength(2)
    const knot = placed.find((item) => item.step.id === M1)!
    // The single deepest convergence of a finished circle sits at the centre.
    expect(knot.at).toEqual({ x: 340, y: 340 })
    expect(ringRadius(3)).toBeLessThan(ringRadius(2))
    expect(ringRadius(2)).toBeLessThan(ringRadius(1))
  })

  it('builds an escaped report that leads with the deepest convergence', () => {
    const report = convergeReport(parseProgramme(programme([...deep().slice(0, 2), { ...deep()[2], title: '# Injected <b>' }], { deepest_step_ids: [M1] })), new Date('2026-10-29T00:00:00Z'))
    expect(report).toContain('## Deepest convergence\n\n- **Level 2:** \\# Injected \\<b\\> — unites Ecology, Optics, Economics, Linguistics')
    expect(report).not.toContain('<b>')
  })
})

describe('converge workspace', () => {
  it('queues the due step through the ordinary submit path and attaches its run', async () => {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push(`${init?.method ?? 'GET'} ${url}`)
      if (url === '/api/v1/converge') return Response.json({ programmes: [programme()] })
      if (url.endsWith('/next')) return Response.json({ next: { state: 'due', kind: 'meta', level: 2, mode: 'explore', goal: 'A deeper synthetic goal', parent_result_ids: [R1, R2] }, programme: programme(deep().slice(0, 2)) })
      if (url.endsWith('/attach')) { expect(JSON.parse(String(init?.body))).toEqual({ kind: 'meta', parent_result_ids: [R1, R2], run_id: RUN }); return Response.json(programme([...deep().slice(0, 2), { ...deep()[2], status: 'running', title: null, field_indices: [] }])) }
      return Response.json(programme(deep().slice(0, 2)))
    }))
    const submit = vi.fn(async () => ({ id: RUN, mode: 'explore', goal: 'g', status: 'queued', created_at: '', updated_at: '', result_id: null, error_code: null, external_outcome_unknown: false } as never))
    render(<ConvergeWorkspace enabled csrfToken={CSRF} canSubmit focusId={PID} submit={submit} onOpenRun={vi.fn()} onSessionExpired={vi.fn()} />)
    await waitFor(() => expect(submit).toHaveBeenCalledWith('explore', 'A deeper synthetic goal', [R1, R2]))
    await waitFor(() => expect(calls).toContain(`POST /api/v1/converge/${PID}/attach`))
    expect(await screen.findByText(/Looking for what these connections share · level 2/)).toBeInTheDocument()
    expect(screen.getByRole('group', { name: /A circle of 4 fields/ })).toBeInTheDocument()
  })

  it('reads the deepest convergence first and lets a connection open its evidence or go deeper', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/v1/converge') return Response.json({ programmes: [programme(deep(), { status: 'completed', deepest_step_ids: [M1], finished_reason: 'converged' })] })
      return Response.json(programme(deep(), { status: 'completed', deepest_step_ids: [M1], finished_reason: 'converged' }))
    }))
    const onOpenRun = vi.fn(), onGoDeeper = vi.fn()
    render(<ConvergeWorkspace enabled csrfToken={CSRF} canSubmit={false} focusId={PID} submit={vi.fn()} onOpenRun={onOpenRun} onGoDeeper={onGoDeeper} onSessionExpired={vi.fn()} />)
    expect(await screen.findByText('The deepest convergence')).toBeInTheDocument()
    expect(screen.getByText('Unites 4 fields · level 2')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('everything that held has been drawn inward')
    await userEvent.click(screen.getByRole('button', { name: /Connection: Ecology and Optics/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Read the evidence' }))
    expect(onOpenRun).toHaveBeenCalledWith(RUN)
    await userEvent.click(screen.getByRole('button', { name: 'Go deeper here' }))
    expect(onGoDeeper).toHaveBeenCalledWith(R1)
  })
})

describe('converge composer', () => {
  it('shows a plain estimate, takes chosen fields as chips and sends one exact request', async () => {
    let body: unknown
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/capabilities')) return Response.json({ converge_available: true, min_fields: 3, max_fields: 200, min_ceiling_microdollars: 100000, max_ceiling_microdollars: 150000000, step_estimate_microdollars: 500000 })
      if (url.startsWith('/api/v1/converge/fields')) return Response.json({ fields: ['Ecology'] })
      if (url === '/api/v1/converge' && init?.method === 'POST') { body = JSON.parse(String(init.body)); return Response.json(programme(), { status: 201 }) }
      throw new Error('Unexpected synthetic request')
    }))
    const onCreated = vi.fn()
    render(<ConvergeComposer csrfToken={CSRF} ready onCreated={onCreated} onSessionExpired={vi.fn()} />)
    expect(await screen.findByText(/About 40 investigations fit within \$20/)).toBeInTheDocument()
    expect(screen.getByRole('slider', { name: 'How many fields' })).toHaveAttribute('max', '100')
    await userEvent.click(screen.getByRole('button', { name: 'More, up to 200' }))
    expect(screen.getByRole('slider', { name: 'How many fields' })).toHaveAttribute('max', '200')
    await userEvent.type(screen.getByRole('combobox', { name: /Fields you want included/ }), 'Ecology{Enter}')
    expect(screen.getByRole('button', { name: 'Remove Ecology' })).toBeInTheDocument()
    await userEvent.type(screen.getByRole('textbox', { name: /question or theme/ }), 'A synthetic theme')
    await userEvent.click(screen.getByRole('button', { name: 'Begin converging' }))
    await waitFor(() => expect(onCreated).toHaveBeenCalled())
    expect(body).toEqual({ theme: 'A synthetic theme', field_count: 12, fields: ['Ecology'], ceiling_microdollars: 20_000_000 })
  })
})
