import { describe, expect, it } from 'vitest'
import { sessionDisplayName } from './sessionDisplayName'

describe('shared session display name', () => {
  it.each([
    ['  Named task  ', 'background:cron:night', 'web_1234567890123456', 'Named task'],
    ['', 'background:cron:night:run', 'id', '定时任务 · night:run'],
    ['  ', 'web', 'web_1234567890123456', 'Web 会话 · 12345678'],
    ['', 'web', 'web_short', 'web_short'],
    ['', undefined, 'ordinary-id', 'ordinary-id'],
  ])('preserves history labels for %j / %j', (title, source, session_id, expected) => {
    expect(sessionDisplayName({ title, source, session_id })).toBe(expected)
  })
})
