import { act, render, renderHook, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CompletionToast, NotifyToggle, useNotifier, useRunCompletion } from './notifier'

class FakeNotification {
  static permission: NotificationPermission = 'default'
  static requestPermission = vi.fn(async () => { FakeNotification.permission = 'granted'; return 'granted' as NotificationPermission })
  static created: { title: string; options?: NotificationOptions }[] = []
  onclick: (() => void) | null = null
  constructor(title: string, options?: NotificationOptions) { FakeNotification.created.push({ title, options }) }
  close() {}
}

function setHidden(hidden: boolean) {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
}

beforeEach(() => {
  FakeNotification.permission = 'default'
  FakeNotification.created = []
  vi.stubGlobal('Notification', FakeNotification)
  window.localStorage.clear()
  document.title = 'Polymath'
  setHidden(false)
})
afterEach(() => vi.unstubAllGlobals())

describe('notify when done', () => {
  it('asks permission once, remembers the choice and notifies only while hidden', async () => {
    const { result } = renderHook(() => useNotifier())
    await act(async () => { await result.current.toggle() })
    expect(FakeNotification.requestPermission).toHaveBeenCalledOnce()
    expect(result.current.enabled).toBe(true)
    expect(window.localStorage.getItem('polymath.notify-when-done')).toBe('on')
    act(() => result.current.announce('Investigation finished', 'Visible tab', 'a'))
    expect(FakeNotification.created).toHaveLength(0)
    setHidden(true)
    act(() => result.current.announce('Investigation finished', 'Hidden tab', 'b'))
    expect(FakeNotification.created).toEqual([{ title: 'Investigation finished', options: { body: 'Hidden tab', tag: 'b' } }])
    expect(document.title).toBe('✓ Investigation finished · Polymath')
  })

  it('announces only when the same run moves from active to finished', () => {
    const { result: notifier } = renderHook(() => useNotifier())
    const announce = vi.spyOn(notifier.current, 'announce')
    const { rerender } = renderHook(({ run }) => useRunCompletion(run, { ...notifier.current, announce }), { initialProps: { run: { id: 'r1', status: 'succeeded', goal: 'Old run' } } })
    expect(announce).not.toHaveBeenCalled()
    rerender({ run: { id: 'r2', status: 'running', goal: 'Live run' } })
    expect(document.title).toBe('● Researching · Polymath')
    rerender({ run: { id: 'r2', status: 'succeeded', goal: 'Live run' } })
    expect(announce).toHaveBeenCalledWith('Investigation finished', 'Live run', 'r2')
    expect(document.title).toBe('Polymath')
  })

  it('renders a toggle and a dismissible completion toast', async () => {
    function Harness() {
      const notifier = useNotifier()
      return <><NotifyToggle notifier={notifier} /><button type="button" onClick={() => notifier.announce('Investigation finished', 'Shown in page', 'x')}>finish</button><CompletionToast notifier={notifier} /></>
    }
    render(<Harness />)
    expect(screen.getByRole('button', { name: /Notify me when it’s done/ })).toHaveAttribute('aria-pressed', 'false')
    await userEvent.click(screen.getByRole('button', { name: 'finish' }))
    expect(screen.getByRole('status')).toHaveTextContent('Shown in page')
    await userEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })
})
