import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { GoDeeper } from './DeeperDiagram'

describe('go deeper', () => {
  it('explains the deeper step plainly and offers one action', async () => {
    const onChoose = vi.fn()
    render(<GoDeeper onChoose={onChoose} />)
    expect(screen.getByRole('complementary', { name: 'Go deeper' })).toHaveTextContent('looks for the structure they all share')
    await userEvent.click(screen.getByRole('button', { name: 'Choose connections to combine' }))
    expect(onChoose).toHaveBeenCalledOnce()
  })
})
