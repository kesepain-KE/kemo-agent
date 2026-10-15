import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(resolve(process.cwd(), 'src/components/UserMessageNavigator.module.css'), 'utf8')
const component = readFileSync(resolve(process.cwd(), 'src/components/UserMessageNavigator.tsx'), 'utf8')

describe('快捷轮次导航布局合同', () => {
  it('组件直接导入样式，导航脱离正文布局且关闭时不阻挡消息操作', () => {
    expect(component).toContain("import styles from './UserMessageNavigator.module.css'")
    const rule = css.match(/\.navigator\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(rule).toContain('position: absolute')
    expect(rule).toContain('pointer-events: none')
    expect(rule).toContain('calc(100% - 24px)')
    // 展开时弧线空隙也应保留悬停，避免卡片移动后预览被误关闭。
    expect(css.match(/\.navigator\[data-open="true"\] \.wheel\s*\{([^}]*)\}/)?.[1]).toContain('pointer-events: auto')
  })

  it('状态文字只对辅助技术可见，短轨道和指示器使用当前主题', () => {
    expect(css.match(/\.status\s*\{([^}]*)\}/)?.[1]).toContain('clip-path: inset(50%)')
    expect(css).toContain('var(--accent)')
    expect(css).toContain('var(--bg-raised)')
    expect(css).not.toMatch(/#[a-f0-9]{3,8}\b/i)
  })

  it('移动端保留轨道，矮聊天区域收起旁卡而不是隐藏导航功能', () => {
    expect(css).toContain('@media (max-width: 600px)')
    expect(css).toContain('@container (max-height: 240px)')
    expect(css).not.toMatch(/\.navigator\s*\{[^}]*display:\s*none/)
    expect(css).toContain('@media (prefers-reduced-motion: reduce)')
  })
})
