import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useSpeechRecognition } from './useSpeechRecognition'

class FakeRecognition {
  static instances: FakeRecognition[] = []
  lang = ''
  interimResults = true
  continuous = true
  onresult: ((event: { results: { transcript: string }[][] }) => void) | null = null
  onerror: ((event: { error: string }) => void) | null = null
  onend: (() => void) | null = null
  start = vi.fn()
  stop = vi.fn()
  abort = vi.fn()
  constructor() { FakeRecognition.instances.push(this) }
}

beforeEach(() => {
  FakeRecognition.instances = []
  vi.stubGlobal('SpeechRecognition', FakeRecognition)
  vi.stubGlobal('webkitSpeechRecognition', undefined)
  vi.spyOn(console, 'warn').mockImplementation(() => {})
})
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })
const current = () => FakeRecognition.instances.at(-1)!

describe('browser speech lifecycle', () => {
  it('supports standard API, avoids duplicate starts and delivers to the latest callback', () => {
    const first = vi.fn(), latest = vi.fn()
    const { result, rerender } = renderHook(({ callback }) => useSpeechRecognition(callback),
      { initialProps: { callback: first } })
    expect(result.current.supported).toBe(true)
    act(() => { result.current.start(); result.current.start() })
    expect(FakeRecognition.instances).toHaveLength(1)
    expect(current()).toMatchObject({ lang: 'zh-CN', continuous: false, interimResults: false })
    expect(result.current.listening).toBe(true)
    rerender({ callback: latest })
    act(() => current().onresult?.({ results: [[{ transcript: ' hello ' }], [{ transcript: 'world' }]] }))
    expect(first).not.toHaveBeenCalled()
    expect(latest).toHaveBeenCalledWith('hello world')
    act(() => current().onresult?.({ results: [[{ transcript: '  ' }]] }))
    expect(latest).toHaveBeenCalledTimes(1)
  })

  it('clears an errored recognizer even when the browser omits onend', () => {
    const callback = vi.fn()
    const { result } = renderHook(() => useSpeechRecognition(callback))
    act(() => result.current.start())
    const old = current()
    const lateEnd = old.onend, lateResult = old.onresult
    act(() => old.onerror?.({ error: 'not-allowed' }))
    expect(result.current.listening).toBe(false)
    expect(old.abort).toHaveBeenCalledOnce()
    act(() => result.current.start())
    expect(FakeRecognition.instances).toHaveLength(2)
    act(() => { lateEnd?.(); lateResult?.({ results: [[{ transcript: 'stale' }]] }) })
    expect(result.current.listening).toBe(true)
    expect(callback).not.toHaveBeenCalled()
  })

  it('lets stop deliver final text until onend, then permits restart', () => {
    const callback = vi.fn()
    const { result } = renderHook(() => useSpeechRecognition(callback))
    act(() => result.current.start())
    act(() => result.current.stop())
    expect(current().stop).toHaveBeenCalledOnce()
    expect(result.current.listening).toBe(false)
    act(() => {
      current().onresult?.({ results: [[{ transcript: 'final' }]] })
      current().onend?.()
    })
    expect(callback).toHaveBeenCalledWith('final')
    act(() => result.current.start())
    expect(FakeRecognition.instances).toHaveLength(2)
  })

  it('detaches and aborts on unmount, ignoring already queued callbacks', () => {
    const callback = vi.fn()
    const { result, unmount } = renderHook(() => useSpeechRecognition(callback))
    act(() => result.current.start())
    const old = current(), late = old.onresult
    unmount()
    expect(old.abort).toHaveBeenCalledOnce()
    expect(old.onresult).toBeNull()
    expect(old.onerror).toBeNull()
    expect(old.onend).toBeNull()
    act(() => late?.({ results: [[{ transcript: 'after unmount' }]] }))
    expect(callback).not.toHaveBeenCalled()
  })

  it('recovers from a synchronous start error', () => {
    class StartFailure extends FakeRecognition {
      start = vi.fn(() => { throw new Error('device unavailable') })
    }
    vi.stubGlobal('SpeechRecognition', StartFailure)
    const { result } = renderHook(() => useSpeechRecognition(vi.fn()))
    act(() => result.current.start())
    expect(result.current.listening).toBe(false)
    expect(current().onresult).toBeNull()
    vi.stubGlobal('SpeechRecognition', FakeRecognition)
    act(() => result.current.start())
    expect(result.current.listening).toBe(true)
  })

  it('degrades safely without either browser API', () => {
    vi.stubGlobal('SpeechRecognition', undefined)
    const { result } = renderHook(() => useSpeechRecognition(vi.fn()))
    expect(result.current.supported).toBe(false)
    act(() => { result.current.start(); result.current.stop() })
    expect(FakeRecognition.instances).toHaveLength(0)
    expect(result.current.listening).toBe(false)
  })
})
