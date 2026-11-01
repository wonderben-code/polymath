import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { freeUseEnded, OwnKeyCard } from './OwnKey'

describe('your own Meta key', () => {
  it('is offered on the home page with the guide, and reworded once free use has ended', () => {
    const { rerender } = render(<OwnKeyCard />)
    expect(screen.getByRole('heading', { name: 'Run Polymath with your own Meta key' })).toBeVisible()
    expect(screen.getByRole('link', { name: /Set up your own Polymath/ })).toHaveAttribute('href', expect.stringContaining('RUN_YOUR_OWN.md'))
    expect(screen.getByText('Unlimited · open source')).toBeVisible()
    rerender(<OwnKeyCard ended />)
    expect(screen.getByText('Free use has ended · unlimited option')).toBeVisible()
    expect(screen.getByText(/The free allowance here is used up for now/)).toBeVisible()
  })

  it('knows when a visitor cannot start free research for a funding reason', () => {
    expect(freeUseEnded(null, 3, false)).toBe(false)
    expect(freeUseEnded('public_research_budget_exhausted', 3, false)).toBe(true)
    expect(freeUseEnded('public_research_authority_expired', null, false)).toBe(true)
    expect(freeUseEnded(null, 0, false)).toBe(true)
    expect(freeUseEnded(null, null, true)).toBe(true)
    expect(freeUseEnded('public_research_prior_unresolved', 2, false)).toBe(false)
  })
})
