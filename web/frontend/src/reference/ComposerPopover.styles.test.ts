import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const production = readFileSync(resolve(process.cwd(), 'src/reference/styles/production.css'), 'utf8')
const sample = readFileSync(resolve(process.cwd(), 'src/reference/styles/chat.css'), 'utf8')

describe('输入坞对话操作菜单样式', () => {
  it('上下文环缩小四分之一并加粗七成，SVG 中保留完整描边空间', () => {
    const ring = production.match(/\.context-usage-ring\s*\{([^}]*)\}/)?.[1] ?? ''
    const circles = production.match(/\.context-usage-ring circle\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(ring).toContain('width: 19.5px')
    expect(ring).toContain('height: 19.5px')
    expect(circles).toContain('stroke-width: 5.95')
  })
  it('编辑重发提示内缩到输入卡中，不通过裁切外框来掩盖重叠', () => {
    const notice = production.match(/\.composer\.composer-inner > \.edit-resend-banner\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(notice).toContain('margin: 10px 12px 0')
    expect(notice).toContain('padding: 9px 12px')
    expect(notice).toContain('border-radius: var(--r-md)')
    expect(notice).not.toContain('position: absolute')
  })

  it('生产输入框禁用原生内容高度计算，与自动高度 hook 使用同一上下限', () => {
    const input = production.match(/\.composer\.composer-inner \.composer-textarea\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(input).toContain('field-sizing: fixed')
    expect(input).toContain('min-height: 3.2rem')
    expect(input).toContain('max-height: max(3.2rem, min(12rem, 40vh))')
    expect(sample).toContain('field-sizing: content')
  })

  it('只解除生产输入卡的裁切，不修改样例圆角容器规则', () => {
    const productionComposer = production.match(/\.composer\.composer-inner\s*\{([^}]*)\}/)?.[1] ?? ''
    const sampleComposer = sample.match(/\.composer\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(sampleComposer).toContain('overflow: hidden')
    expect(productionComposer).toContain('overflow: visible')
  })

  it('保持菜单向上定位并覆盖输入坞内容', () => {
    const menu = production.match(/\.conversation-menu\s*\{([^}]*)\}/)?.[1] ?? ''
    const toolbar = production.match(/\.composer-bar\s*\{([^}]*)\}/)?.[1] ?? ''
    const trigger = production.match(/\.composer-more\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(toolbar).toContain('position: relative')
    expect(trigger).toContain('position: static')
    expect(menu).toContain('position: absolute')
    expect(menu).toContain('bottom: calc(100% + 12px)')
    expect(menu).toContain('z-index: 45')
  })

  it('矮窗口和移动端限制菜单高度，内容在菜单内滚动', () => {
    const menu = production.match(/\.conversation-menu\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(menu).toContain('max-height: min(560px, calc(100dvh - 160px))')
    expect(menu).toContain('overflow-y: auto')
    expect(menu).toContain('overscroll-behavior: contain')
  })
})
