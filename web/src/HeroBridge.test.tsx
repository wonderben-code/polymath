import { act, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { HERO_EXAMPLES, HeroBridge } from './HeroBridge'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

describe('home bridge illustration', () => {
  it('describes every example for assistive technology and labels them as examples', () => {
    render(<HeroBridge />)
    const figure = screen.getByRole('figure')
    for (const example of HERO_EXAMPLES) expect(figure).toHaveAccessibleName(expect.stringContaining(example.shared))
    expect(screen.getByText('Famous examples of the kind of connection Polymath looks for.')).toBeInTheDocument()
  })

  it('cycles gently through the examples, and stays still under reduced motion', () => {
    vi.useFakeTimers()
    const { unmount } = render(<HeroBridge />)
    expect(screen.getByText('Heat flow')).toBeInTheDocument()
    act(() => { vi.advanceTimersByTime(4800) })
    expect(screen.getByText('Ant colonies')).toBeInTheDocument()
    unmount()
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query.includes('reduce'), media: query, addEventListener() {}, removeEventListener() {} }))
    render(<HeroBridge />)
    act(() => { vi.advanceTimersByTime(20000) })
    expect(screen.getByText('Heat flow')).toBeInTheDocument()
  })
})
