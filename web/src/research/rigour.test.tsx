import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createResearchRun, parseRun } from './client'
import { RigourBar } from './RigourBar'
import { setRigour } from './rigour'

const CSRF = 'a'.repeat(64)
const RUN = { id: '11111111-1111-4111-8111-111111111111', mode: 'explore', task_kind: 'research', goal: 'Synthetic concept', status: 'queued', created_at: '2026-11-01T00:00:00Z', updated_at: '2026-11-01T00:00:00Z', result_id: null, error_code: null, external_outcome_unknown: false }

afterEach(() => { vi.unstubAllGlobals(); setRigour('standard'); window.localStorage.clear() })

describe('the rigour bar', () => {
  it('offers four levels, explains the chosen one and remembers it', async () => {
    render(<RigourBar />)
    expect(screen.getByRole('button', { name: 'Standard' })).toHaveAttribute('aria-pressed', 'true')
    await userEvent.click(screen.getByRole('button', { name: 'Peer reviewed' }))
    expect(screen.getByRole('button', { name: 'Peer reviewed' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText('Only work published in journals and conference proceedings.')).toBeInTheDocument()
    expect(window.localStorage.getItem('polymath.rigour')).toBe('peer_reviewed')
  })

  it('sends a chosen bar with a new investigation, and leaves the default request unchanged', async () => {
    const bodies: unknown[] = []
    vi.stubGlobal('fetch', vi.fn(async (_: RequestInfo | URL, init?: RequestInit) => { bodies.push(JSON.parse(String(init?.body))); return Response.json({ ...RUN, ...(bodies.length > 1 ? { rigour: 'established' } : {}) }) }))
    await createResearchRun('explore', 'Synthetic concept', CSRF)
    setRigour('established')
    const run = await createResearchRun('explore', 'Synthetic concept', CSRF)
    expect(bodies).toEqual([{ mode: 'explore', goal: 'Synthetic concept' }, { mode: 'explore', goal: 'Synthetic concept', rigour: 'established' }])
    expect(run.rigour).toBe('established')
  })

  it('ignores an unknown bar in a reply', () => {
    expect(parseRun({ ...RUN, rigour: 'lenient' }).rigour).toBeUndefined()
  })
})
