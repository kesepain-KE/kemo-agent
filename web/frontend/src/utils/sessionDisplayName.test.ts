import { describe, expect, it } from 'vitest'
import { sessionDisplayName } from './sessionDisplayName'

describe('shared session display name', () => {
  it.each([
    ['  Named task  ', 'background:cron:night', 'web_1234567890123456', undefined, undefined, 'Named task'],
    ['', 'background:cron:night:run', 'id', undefined, undefined, '定时任务 · night:run'],
    ['First user message', 'web', 'web_1234567890123456', undefined, 'queued', '未命名对话'],
    ['Manual name', 'web', 'web_short', 'manual', 'queued', 'Manual name'],
    ['Generated name', 'web', 'web_short', 'summary', 'completed', 'Generated name'],
    ['', 'web', 'web_short', undefined, undefined, '未命名对话'],
    ['', undefined, 'ordinary-id', undefined, undefined, '历史对话'],
  ])('uses resolved titles and neutral fallbacks for %j / %j', (
    title, source, session_id, title_source, summary_status, expected,
  ) => {
    expect(sessionDisplayName({ title, source, session_id, title_source, summary_status })).toBe(expected)
  })
})
