import { describe, expect, it } from 'vitest'
import { stripBackendJudgmentAsides } from './assistantOutput'

describe('stripBackendJudgmentAsides', () => {
  it('removes parenthetical backend duplicate-call decisions but keeps surrounding content', () => {
    expect(stripBackendJudgmentAsides(
      '当前时间：2026-11-04。\n（第二次调用被系统判定为重复调用已拦截，以上一次实测结果为准。）',
    )).toBe('当前时间：2026-11-04。')
  })

  it('preserves normal parenthetical details and removes machine status codes', () => {
    expect(stripBackendJudgmentAsides(
      '时区偏移 +0800（Asia/Shanghai）。\n(status=duplicate_blocked)',
    )).toBe('时区偏移 +0800（Asia/Shanghai）。')
  })
})
