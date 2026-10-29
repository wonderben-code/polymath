import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { NativeResearchNotice } from './NativeResearchNotice'
import type { NativeResearchContextValue } from './nativeContext'

function context(overrides: Partial<NativeResearchContextValue> = {}): NativeResearchContextValue {
  return { required: true, phase: 'disabled', activation: null, submission: null, access: null, canSubmit: false, pollPaused: false, reason: null, invalidate: vi.fn(), refresh: vi.fn(), ...overrides }
}

describe('native research notice', () => {
  it('shows visitors a plain paused status without operator controls when research is off', () => {
    render(<NativeResearchNotice context={context()} />)
    expect(screen.getByRole('region', { name: 'Live research status' })).toHaveTextContent('Live investigations are paused right now')
    expect(screen.getByRole('link', { name: 'public library' })).toHaveAttribute('href', '/library')
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.queryByText(/native/i)).not.toBeInTheDocument()
  })

  it('keeps the operator refresh control for other phases', () => {
    render(<NativeResearchNotice context={context({ phase: 'operator_required' })} />)
    expect(screen.getByRole('button', { name: 'Refresh current native authority' })).toBeEnabled()
  })

  it('renders nothing when native research is not required', () => {
    const { container } = render(<NativeResearchNotice context={context({ required: false })} />)
    expect(container).toBeEmptyDOMElement()
  })
})
