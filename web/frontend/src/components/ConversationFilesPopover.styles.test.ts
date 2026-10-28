import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(resolve(process.cwd(), 'src/components/ConversationFilesPopover.module.css'), 'utf8')
const panel = css.match(/\.panel\s*\{([^}]*)\}/)?.[1] ?? ''

describe('相关文件与未展开轮次导航间距', () => {
  it('浮层整体左移32px，保持视口定位且不改动内部内容布局', () => {
    expect(panel).toContain('position: fixed')
    expect(panel).toContain('right: calc(var(--s-6) + 32px)')
    expect(panel).toContain('left: auto')
    expect(panel).not.toMatch(/transform:|margin-right:/)
  })

  it('窄屏同步收窄以保留原有头像侧安全留白', () => {
    expect(panel).toContain('width: min(280px, calc(100vw - 112px))')
    expect(panel).toContain('max-width: calc(100vw - 112px)')
    for (const viewport of [320, 390, 600, 768, 1600]) {
      for (const spacing of [20, 24]) {
        const right = spacing + 32
        const width = Math.min(280, viewport - 112)
        const railLeftInset = viewport <= 600 ? 8 + 28 : 12 + 32
        expect(right - railLeftInset).toBeGreaterThanOrEqual(8)
        expect(viewport - right - width).toBeGreaterThanOrEqual(56)
      }
    }
  })

  it('栏头固定，正文统一内部滚动并阻止滚动传递到聊天', () => {
    const body = css.match(/\.body\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(css.match(/\.head\s*\{([^}]*)\}/)?.[1]).toContain('flex-shrink: 0')
    expect(body).toContain('min-height: 0')
    expect(body).toContain('overflow-y: auto')
    expect(body).toContain('overflow-x: hidden')
    expect(body).toContain('overscroll-behavior-y: contain')
    expect(body).toContain('touch-action: pan-y')
    expect(body).toContain('scrollbar-gutter: stable')
    expect(panel).toContain('overflow: hidden')
  })
})
