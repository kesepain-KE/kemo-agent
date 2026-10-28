import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const stylesheet = readFileSync(resolve(process.cwd(), 'src/components/KnowledgeReferenceDrawer.module.css'), 'utf8')
const rule = (selector: string) => stylesheet.match(new RegExp(`${selector.replaceAll('.', '\\.')}\\s*\\{([^}]*)\\}`))?.[1] ?? ''

describe('知识引用抽屉布局合同', () => {
  it('固定筛选区，只让文件列表滚动，卡片随字号和内容自然增高', () => {
    expect(rule('.body')).toContain('overflow: hidden')
    expect(rule('.categoryRow')).toContain('flex: 0 0 auto')
    expect(rule('.categoryRow')).toContain('minmax(0, 1fr)')
    expect(rule('.categorySelect')).toContain('height: 40px')
    expect(rule('.results')).toContain('min-height: 0')
    expect(rule('.results')).toContain('overflow-y: auto')
    expect(rule('.results')).toContain('grid-auto-rows: max-content')
    expect(rule('.knowledgeCard')).toContain('grid-template-rows: auto')
    expect(rule('.knowledgeCard')).not.toMatch(/(?:^|;)\s*height:/)
  })

  it('长分类名受文本列宽约束，不挤压独立的引用按钮列', () => {
    expect(rule('.cardCopy')).toContain('min-width: 0')
    expect(rule('.cardCopy .categoryBadge')).toContain('max-width: 100%')
    expect(stylesheet).toContain('text-overflow: ellipsis; white-space: nowrap')
    expect(rule('.knowledgeCard')).toContain('minmax(0, 1fr) 34px')
    expect(rule('.referenceButton')).toContain('grid-column: 4')
    expect(stylesheet).toContain('24px 36px minmax(0, 1fr) 32px')
  })
})
