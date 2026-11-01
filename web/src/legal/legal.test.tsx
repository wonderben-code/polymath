import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApplicationRouter } from '../ApplicationRouter'

afterEach(() => vi.unstubAllGlobals())

describe('privacy and terms pages', () => {
  it.each([
    ['/privacy', 'Privacy policy', 'Terms of use'],
    ['/privacy/', 'Privacy policy', 'Terms of use'],
    ['/terms', 'Terms of use', 'Privacy policy'],
  ])('%s renders on its own without opening a research session', (path, title, other) => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
    render(<ApplicationRouter pathname={path} />)
    expect(screen.getByRole('heading', { level: 1, name: title })).toBeVisible()
    expect(screen.getByText(/Last updated 1 November 2026/)).toBeVisible()
    expect(screen.getByRole('link', { name: other })).toHaveAttribute('href', other === 'Privacy policy' ? '/privacy' : '/terms')
    expect(screen.getByRole('link', { name: 'hello@askpolymath.com' })).toHaveAttribute('href', 'mailto:hello@askpolymath.com')
    expect(fetch).not.toHaveBeenCalled()
  })

  it('names every party that receives data and the retention periods', () => {
    render(<ApplicationRouter pathname="/privacy" />)
    for (const party of ['Meta', 'Literature sources', 'Sign-in providers and Zenodo', 'Hosting']) expect(screen.getByText(party)).toBeVisible()
    expect(screen.getByText(/your session lasts 7 days/)).toBeVisible()
    expect(screen.getByText(/never your IP address itself/)).toBeVisible()
  })

  it('links both pages from the shared footer', () => {
    render(<ApplicationRouter pathname="/terms" />)
    expect(screen.getByRole('link', { name: 'Privacy' })).toHaveAttribute('href', '/privacy')
    expect(screen.getByRole('link', { name: 'Terms' })).toHaveAttribute('href', '/terms')
  })
})
