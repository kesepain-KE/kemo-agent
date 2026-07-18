import { describe, expect, it } from 'vitest'
import { parseSseFrames, submitGuidance } from './client'

describe('parseSseFrames', () => {
  it('保留跨块残片并解析完整事件', () => {
    const first = parseSseFrames('event: text_delta\ndata: {"type":"text_')
    expect(first.frames).toEqual([])
    const second = parseSseFrames(first.rest + 'delta","content":"你"}\n\n')
    expect(second.frames).toEqual([{ event: 'text_delta', data: '{"type":"text_delta","content":"你"}' }])
    expect(second.rest).toBe('')
  })

  it('兼容 CRLF 与多行 data', () => {
    const parsed = parseSseFrames('event: done\r\ndata: {"type":"done",\r\ndata: "metadata":{}}\r\n\r\n')
    expect(parsed.frames[0]).toEqual({ event: 'done', data: '{"type":"done",\n"metadata":{}}' })
  })

  it('提交运行中引导到指定 run_id', async () => {
    const result = await submitGuidance('kesepain', 'run_test_123', 'adjust')
    expect(result).toMatchObject({ run_id: 'run_test_123', status: 'queued', queued: 1 })
  })
})
