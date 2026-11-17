import { describe, expect, it, vi } from 'vitest'
import { cancelRun, followRun, streamChat } from './streamClient'

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

  it('recovers a broken stream with one POST, a snapshot and deduplicated incremental events', async () => {
    const snapshot = {
      run_id: 'run_resume', user: 'alice', source: 'web', session_id: 's1',
      cursor: 4, history_seq: 2, events: [{ type: 'text_delta', content: 'snapshot' }],
      finished: false, stopping: false, snapshot_incomplete: false, terminal: null,
    }
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode('data: {"type":"text_delta","content":"initial"}\n\n'))
          controller.close()
        },
      })))
      .mockResolvedValueOnce(Response.json(snapshot))
      .mockResolvedValueOnce(Response.json({
        reset: false, finished: true, cursor: 6, events: [
          { type: 'text_delta', content: 'duplicate', metadata: { web_event_seq: 4 } },
          { type: 'text_delta', content: 'tail', metadata: { web_event_seq: 5 } },
          { type: 'done', metadata: { web_event_seq: 6, status: 'completed' } },
        ],
      }))
    const onReset = vi.fn().mockResolvedValue(undefined)
    const onEvent = vi.fn()
    try {
      await streamChat({ user: 'alice', sessionId: 's1', runId: 'run_resume', prompt: 'hello', onEvent, onReset })
      expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
      expect(onReset).toHaveBeenCalledWith(expect.objectContaining({ cursor: 4, history_seq: 2 }))
      expect(onEvent.mock.calls.map(([event]) => event.content || event.type)).toEqual(['initial', 'tail', 'done'])
      expect(String(fetchMock.mock.calls[2][0])).toContain('after=4&history_seq=2')
    } finally { fetchMock.mockRestore() }
  })

  it('aborts only the recovery subscription and never retries callback failures', async () => {
    const snapshot = {
      run_id: 'run_resume', user: 'alice', source: 'web', session_id: 's1',
      cursor: 1, history_seq: 1, events: [], finished: false, stopping: false,
      snapshot_incomplete: false, terminal: null,
    }
    const controller = new AbortController()
    controller.abort()
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValue(Response.json({ events: [
        { type: 'text_delta', content: 'tail', metadata: { web_event_seq: 2 } },
      ] }))
    try {
      await expect(followRun({
        user: 'alice', sessionId: 's1', runId: 'run_resume', signal: controller.signal,
        onReset: async () => {}, onEvent: vi.fn(),
      }, snapshot)).rejects.toThrow('连接已取消')
      expect(fetchMock).not.toHaveBeenCalled()
      await expect(followRun({
        user: 'alice', sessionId: 's1', runId: 'run_resume',
        onReset: async () => {}, onEvent: () => { throw new TypeError('callback failure') },
      }, snapshot)).rejects.toThrow('callback failure')
      expect(fetchMock).toHaveBeenCalledTimes(1)
    } finally { fetchMock.mockRestore() }
  })
})
