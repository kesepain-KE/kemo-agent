import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getHistory, releaseSessionLease, touchSessionLease } from './api/client'
import { ApiError } from './api/transport'
import { watchWebSession } from './webSessionPresence'

vi.mock('./api/client', () => ({
  getHistory: vi.fn(),
  releaseSessionLease: vi.fn(),
  touchSessionLease: vi.fn(),
}))

describe('Web document presence', () => {
  let visibility: DocumentVisibilityState
  const watches: ReturnType<typeof watchWebSession>[] = []
  const touch = vi.mocked(touchSessionLease)
  const release = vi.mocked(releaseSessionLease)
  const start = (isCurrent = () => true) => {
    const onAccess = vi.fn()
    const onResume = vi.fn()
    const watch = watchWebSession({ user: 'alice', sessionId: 's1', clientId: 'document', leaseProven: false, isCurrent, onAccess, onResume })
    watches.push(watch)
    return { watch, onAccess, onResume }
  }
  const setVisibility = (state: DocumentVisibilityState) => {
    visibility = state
    document.dispatchEvent(new Event('visibilitychange'))
  }

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'performance'] })
    vi.resetAllMocks()
    visibility = 'visible'
    vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visibility)
    touch.mockResolvedValue(true)
    release.mockResolvedValue(undefined)
  })

  afterEach(() => {
    for (const watch of watches.splice(0)) watch.dispose()
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it('checkpoints on hidden, keeps ticking, freezes once, and coalesces resume events', async () => {
    const { watch, onResume } = start()
    setVisibility('hidden')
    expect(touch).toHaveBeenNthCalledWith(2, 'alice', 's1', 'document', true, undefined)
    await vi.advanceTimersByTimeAsync(15_000)
    expect(touch).toHaveBeenNthCalledWith(3, 'alice', 's1', 'document', false, expect.anything())
    document.dispatchEvent(new Event('freeze'))
    window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true }))
    expect(touch).toHaveBeenCalledTimes(4)
    expect(touch).toHaveBeenLastCalledWith('alice', 's1', 'document', true, undefined)
    window.dispatchEvent(new Event('online'))
    await vi.advanceTimersByTimeAsync(90_000)
    expect(touch).toHaveBeenCalledTimes(4)
    expect(release).not.toHaveBeenCalled()

    document.dispatchEvent(new Event('resume'))
    window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }))
    setVisibility('visible')
    expect(touch).toHaveBeenCalledTimes(5)
    expect(touch).toHaveBeenLastCalledWith('alice', 's1', 'document', false, expect.anything())
    expect(onResume).toHaveBeenCalledTimes(2) // boot and one coalesced restore
    await vi.advanceTimersByTimeAsync(15_000)
    expect(touch).toHaveBeenCalledTimes(6)

    window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: false }))
    window.dispatchEvent(new Event('online'))
    watch.dispose()
    await vi.advanceTimersByTimeAsync(15_000)
    expect(touch).toHaveBeenCalledTimes(6)
    expect(release).toHaveBeenCalledExactlyOnceWith('alice', 's1', 'document', true)
  })

  it('keeps a frozen document lease through cleanup but releases it on a session switch', () => {
    const { watch } = start()
    document.dispatchEvent(new Event('freeze'))
    watch.dispose()
    expect(release).not.toHaveBeenCalled()

    let current = true
    const switched = start(() => current)
    document.dispatchEvent(new Event('freeze'))
    current = false
    switched.watch.dispose()
    expect(release).toHaveBeenCalledExactlyOnceWith('alice', 's1', 'document', true)
  })

  it('ignores a stale lease 404 after a newer restore or a change of scope', async () => {
    let reject!: (error: ApiError) => void
    touch.mockImplementationOnce(() => new Promise((_, fail) => { reject = fail }))
    let current = true
    const { watch, onAccess } = start(() => current)
    await watch.recheck()
    expect(onAccess).toHaveBeenLastCalledWith({ kind: 'open' }, true)
    const error = new ApiError('gone', 404, 'not_found')
    reject(error)
    await Promise.resolve()
    expect(getHistory).not.toHaveBeenCalled()

    touch.mockRejectedValueOnce(error)
    const stale = watch.recheck()
    current = false
    await stale
    expect(getHistory).not.toHaveBeenCalled()
    expect(onAccess).toHaveBeenCalledOnce()
  })

  it('does not treat an HTTP success without a lease as writable', async () => {
    touch.mockResolvedValue(false)
    const { onAccess } = start()
    await Promise.resolve()
    expect(onAccess).toHaveBeenCalledExactlyOnceWith({ kind: 'unverified' }, false)
  })

  it('supersedes a stuck presence request without queuing a burst of heartbeats', async () => {
    touch.mockImplementationOnce(() => new Promise(() => {}))
    const { onAccess } = start()
    const oldSignal = touch.mock.calls[0][4]
    await vi.advanceTimersByTimeAsync(15_000)
    expect(touch).toHaveBeenCalledOnce()
    expect(oldSignal?.aborted).toBe(false)
    await vi.advanceTimersByTimeAsync(15_000)
    expect(touch).toHaveBeenCalledTimes(2)
    expect(oldSignal?.aborted).toBe(true)
    expect(onAccess).toHaveBeenCalledExactlyOnceWith({ kind: 'open' }, false)
  })
})
