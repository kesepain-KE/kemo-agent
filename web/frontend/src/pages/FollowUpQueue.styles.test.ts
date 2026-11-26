import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const stylesheet = readFileSync(resolve(process.cwd(), 'src/pages/FollowUpQueue.module.css'), 'utf8')

describe('FollowUpQueue height budget styles', () => {
  it('caps the queue at the shared 2.5-card budget', () => {
    const rule = stylesheet.match(/\.queue ol\s*\{([^}]*)\}/)?.[1] ?? ''

    expect(rule).toContain('max-height: var(--composer-follow-up-max, 26.25rem)')
    expect(rule).toContain('overflow-y: auto')
    expect(rule).toContain('overscroll-behavior: contain')
    expect(rule).toContain('scrollbar-gutter: stable')
  })

  // 2026-11-24 需求变更：一条跟进消息里可放多张图片/多媒体，原先「卡片内不滚动」
  // 会导致超出 26.25rem 的内容被 overflow:hidden 直接裁掉、看图看不到。
  // 改为卡片内可滚，外层队列滚动条保留（两者职责不同：外层翻消息，内层看单条全文）。
  it('caps one card while allowing the body to scroll its own overflow', () => {
    const bubbleRule = stylesheet.match(/\.bubble\s*\{([^}]*)\}/)?.[1] ?? ''
    const bodyRule = stylesheet.match(/\.body\s*\{([^}]*)\}/)?.[1] ?? ''

    expect(bubbleRule).toContain('max-height: var(--composer-follow-up-max, 26.25rem)')
    expect(bubbleRule).toContain('overflow: hidden')
    expect(bodyRule).toContain('min-height: 0')
    expect(bodyRule).toContain('overflow-y: auto')
    expect(bodyRule).not.toContain('overflow: hidden')
  })
})
