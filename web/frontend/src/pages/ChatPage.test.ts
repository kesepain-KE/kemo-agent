import { describe, expect, it } from 'vitest'
import { reduceRunEvent } from './ChatPage'
import type { ChatItem } from '../types/api'

describe('reduceRunEvent', () => {
  it('流式合并正文和思考', () => {
    let items: ChatItem[] = []
    items = reduceRunEvent(items, { type: 'reasoning_delta', content: '思' })
    items = reduceRunEvent(items, { type: 'reasoning_delta', content: '考' })
    items = reduceRunEvent(items, { type: 'text_delta', content: '你' })
    items = reduceRunEvent(items, { type: 'text_delta', content: '好' })
    expect(items).toHaveLength(2)
    expect(items[0]).toMatchObject({ kind: 'reasoning', content: '思考' })
    expect(items[1]).toMatchObject({ kind: 'message', content: '你好' })
  })

  it('按 tool_call_id 配对开始和结果', () => {
    let items: ChatItem[] = reduceRunEvent([], { type: 'tool_call_start', tool_call_id: 'c1', tool_name: 'time', arguments: { zone: 'local' } })
    items = reduceRunEvent(items, { type: 'tool_call_result', tool_call_id: 'c1', tool_name: 'time', result: { ok: false }, metadata: { status: 'failed', elapsed_ms: 12 } })
    expect(items[0]).toMatchObject({ kind: 'tool', callId: 'c1', status: 'error', result: { ok: false }, elapsedMs: 12 })
  })

  it('done 结束流式标记，error 生成错误项', () => {
    let items: ChatItem[] = [{ id: 'a', kind: 'message', role: 'assistant', content: 'ok', streaming: true }]
    items = reduceRunEvent(items, { type: 'done' })
    expect(items[0]).toMatchObject({ streaming: false })
    items = reduceRunEvent(items, { type: 'error', error: { message: 'failed' } })
    expect(items.at(-1)).toMatchObject({ kind: 'error', content: 'failed' })
  })

  it('done 生成可持久化的逐轮统计卡片', () => {
    const items = reduceRunEvent([], { type: 'done', usage: { prompt_tokens: 10, completion_tokens: 2, total_tokens: 12 }, metadata: { elapsed_ms: 35, tool_calls: 1 } })
    expect(items[0]).toMatchObject({ kind: 'usage', elapsedMs: 35, toolCalls: 1, usage: { total_tokens: 12 } })
  })
})
