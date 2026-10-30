import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { WaysToUse } from './WaysToUse'

describe('ways to use Polymath', () => {
  it('shows the free limit, the own-copy guide and an honestly labelled proposed Meta connection', () => {
    render(<WaysToUse remaining={3} daily={5} />)
    expect(screen.getByText(/3 of 5 free investigations left today/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /step-by-step guide/ })).toHaveAttribute('href', expect.stringContaining('RUN_YOUR_OWN.md'))
    expect(screen.getByText('A vision · not a feature yet')).toBeInTheDocument()
    expect(screen.getByText(/Not available today/)).toBeInTheDocument()
    // The proposed option is a description, never a control that looks like it works.
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})
