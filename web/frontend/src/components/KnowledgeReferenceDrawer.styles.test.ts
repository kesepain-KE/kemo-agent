import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const stylesheet = readFileSync(resolve(process.cwd(), 'src/components/KnowledgeReferenceDrawer.module.css'), 'utf8')
const rule = (selector: string) => stylesheet.match(new RegExp(`${selector.replaceAll('.', '\\.')}\\s*\\{([^}]*)\\}`))?.[1] ?? ''

describe('聊天知识目录树布局合同', () => {
  it('只让目录树滚动，折叠行按路径深度缩进', () => {
    expect(rule('.body')).toContain('overflow: hidden')
    expect(rule('.results')).toContain('min-height: 0')
    expect(rule('.results')).toContain('overflow-y: auto')
    expect(rule('.treeRow')).toContain('var(--tree-depth, 0)')
    expect(stylesheet).not.toContain('.scopeTabs')
    expect(stylesheet).not.toContain('.knowledgeCard')
  })

  it('长文件名受树节点宽度约束，引用按钮不会挤开文字列', () => {
    expect(rule('.treeRow')).toContain('minmax(0, 1fr)')
    expect(rule('.entryName')).toContain('text-overflow: ellipsis')
    expect(rule('.treeRow')).toContain('min-width: 0')
    expect(rule('.referenceButton')).toContain('grid-column: 5')
  })

  it('预览和菜单在抽屉之外作为浮层显示', () => {
    expect(rule('.menu')).toContain('position: fixed')
    expect(rule('.previewOverlay')).toContain('position: fixed')
    expect(rule('.previewDialog')).toContain('max-height')
  })
})
