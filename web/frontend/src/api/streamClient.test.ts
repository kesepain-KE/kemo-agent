import { describe, expect, it, vi } from 'vitest'
import { cancelRun, streamChat } from './streamClient'

describe('SSE forward compatibility and reader lifetime', () => {
  it('sends explicit subagent-cascade intent only when selected', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
      run_id: 'run-1', user: 'alice', session_id: 's1', status: 'stopping',
    }), { headers: { 'Content-Type': 'application/json' } }))
    try {
      await cancelRun('alice', 'run-1', 's1', 'web', true)
      expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toMatchObject({
        cancel_subagents: true, session_id: 's1',
      })
    } finally {
      fetchMock.mockRestore()
    }
  })

  it.each([true, false])('ignores unknown events, logs a bounded count and releases the reader (newline=%s)', async (newline) => {
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(
          'event: future_event\ndata: {"type":"future_event","private":"never log"}\n\n'
          + 'event: text_delta\ndata: {"type":"text_delta","content":"hello"}\n\n'
          + 'event: done\ndata: {"type":"done"}' + (newline ? '\n\n' : ''),
        ))
        controller.close()
      },
    })
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(body))
    const warning = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const onEvent = vi.fn()
    try {
      await expect(streamChat({ user: 'a', sessionId: 's', runId: 'r', prompt: 'hello', onEvent })).resolves.toBeUndefined()
      expect(onEvent.mock.calls.map(([event]) => event.type)).toEqual(['text_delta', 'done'])
      expect(fetchMock).toHaveBeenCalledTimes(1)
      expect(body.locked).toBe(false)
      expect(warning).toHaveBeenCalledTimes(1)
      expect(JSON.stringify(warning.mock.calls)).not.toContain('never log')
    } finally {
      fetchMock.mockRestore()
      warning.mockRestore()
    }
  })

  it('releases and cancels the reader on malformed known events and callback errors', async () => {
    for (const data of ['{"type":"text_delta","content":12}', '{"type":"text_delta","content":"a"}']) {
      const cancel = vi.fn()
      const body = new ReadableStream({
        start(controller) { controller.enqueue(new TextEncoder().encode(`data: ${data}\n\n`)) },
        cancel,
      })
      const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(body))
      try {
        await expect(streamChat({
          user: 'a', sessionId: 's', runId: 'r', prompt: 'hello',
          onEvent() { throw new Error('callback failed') },
        })).rejects.toThrow()
        expect(body.locked).toBe(false)
        expect(cancel).toHaveBeenCalledTimes(1)
      } finally { fetchMock.mockRestore() }
    }
  })
})
