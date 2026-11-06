import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useCompressionStatus } from './compressionStatus'

afterEach(() => {
  vi.useRealTimers()
  useCompressionStatus.setState({ entries: {} })
})

describe('compression status registry', () => {
  it('retains an active compression across view unmounts and clears its completion notice after a delay', async () => {
    vi.useFakeTimers()
    const key = JSON.stringify(['alice', 'session-1'])
    const { result, unmount } = renderHook(() => useCompressionStatus((state) => state.entries[key]))
    act(() => useCompressionStatus.getState().setStatus('alice', 'session-1', {
      status: 'started', trigger: 'manual', historyTokensBefore: 0,
      historyTokensAfter: 0, absorbedCards: 0, content: '',
    }))
    expect(result.current?.status).toBe('started')
    unmount()

    act(() => useCompressionStatus.getState().setStatus('alice', 'session-1', {
      status: 'ready', trigger: 'manual', historyTokensBefore: 100,
      historyTokensAfter: 30, absorbedCards: 4, content: '',
    }))
    expect(useCompressionStatus.getState().entries[key].status).toBe('ready')
    act(() => vi.advanceTimersByTime(5000))
    expect(useCompressionStatus.getState().entries[key]).toBeUndefined()
  })

  it('keeps statuses isolated by user and session', () => {
    useCompressionStatus.getState().setStatus('alice', 'one', {
      status: 'started', trigger: 'occupancy', historyTokensBefore: 10,
      historyTokensAfter: 0, absorbedCards: 0, content: '',
    })
    useCompressionStatus.getState().setStatus('bob', 'one', {
      status: 'failed', trigger: 'occupancy', historyTokensBefore: 10,
      historyTokensAfter: 0, absorbedCards: 0, content: 'failed',
    })
    expect(Object.keys(useCompressionStatus.getState().entries)).toHaveLength(2)
  })
})
