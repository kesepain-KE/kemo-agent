import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { transferableAbortController } from 'node:util'
import { afterAll, afterEach, beforeAll } from 'vitest'
import { server } from './server'

// jsdom replaces AbortController but retains Node's undici Request/fetch.
// Keep their WebIDL brands aligned so real query cancellation is exercised,
// rather than dropping signals in production to accommodate the test realm.
const nativeController = transferableAbortController()
globalThis.AbortController = Object.getPrototypeOf(nativeController).constructor
globalThis.AbortSignal = Object.getPrototypeOf(nativeController.signal).constructor

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => { cleanup(); server.resetHandlers() })
afterAll(() => server.close())
