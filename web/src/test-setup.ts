import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(() => cleanup())

// Places are client-side paths; reset between tests so navigation cannot leak.
import { afterEach as resetAfterEach } from 'vitest'
resetAfterEach(() => window.history.replaceState(null, '', '/'))
