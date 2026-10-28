import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { KnowledgeReferenceDrawer } from './KnowledgeReferenceDrawer'
import { knowledgeCategoryKey } from '../knowledgeCategories'

const documents = [
  { scope: 'user', relative_path: 'notes.md', title: '个人笔记', size: 120, updated_at: 1, active_for_main_agent: true },
  { scope: 'shared', relative_path: 'team.md', title: '共享规范', size: 90, updated_at: 1, active_for_main_agent: true },
  { scope: 'global', relative_path: 'guide.md', title: '全局指南', size: 160, updated_at: 1, active_for_main_agent: true },
]

const classifiedDocuments = [
  ...documents.map((document) => ({ ...document, category: '开发规范' })),
  { ...documents[0], relative_path: 'uncategorized.md', title: '个人未分类文件' },
  { ...documents[1], relative_path: 'uncategorized.md', title: '共享未分类文件', category: '' },
  { ...documents[2], relative_path: 'all.md', title: '特殊分类文件', category: 'all' },
]

describe('KnowledgeReferenceDrawer', () => {
  it('关闭时保留抽屉节点并通过 show 类驱动过渡', () => {
    const props = { documents, onClose: vi.fn(), onReference: vi.fn() }
    const { rerender } = render(<KnowledgeReferenceDrawer open={false} {...props} />)
    const drawer = screen.getByRole('dialog', { hidden: true })
    expect(drawer).toHaveAttribute('aria-label', '知识库引用')

    expect(drawer).not.toHaveClass('show')
    expect(drawer).toHaveAttribute('aria-hidden', 'true')
    expect(drawer).toHaveAttribute('inert')

    rerender(<KnowledgeReferenceDrawer open {...props} />)
    expect(screen.getByRole('dialog', { name: '知识库引用' })).toHaveClass('show')
    expect(drawer).toHaveAttribute('aria-hidden', 'false')
    expect(drawer).not.toHaveAttribute('inert')
  })

  it('按层级和名称过滤知识卡片并触发引用', () => {
    const onReference = vi.fn()
    render(<KnowledgeReferenceDrawer open documents={documents} onClose={() => undefined} onReference={onReference} />)
    const drawer = screen.getByRole('dialog', { name: '知识库引用' })

    fireEvent.click(within(drawer).getByRole('button', { name: '共享' }))
    expect(within(drawer).getByText('共享规范')).toBeInTheDocument()
    expect(within(drawer).queryByText('个人笔记')).not.toBeInTheDocument()

    fireEvent.click(within(drawer).getByRole('button', { name: '全部' }))
    fireEvent.change(within(drawer).getByRole('textbox', { name: '搜索知识库' }), { target: { value: '指南' } })
    fireEvent.click(within(drawer).getByRole('button', { name: '引用 全局指南' }))
    expect(onReference).toHaveBeenCalledWith(documents[2])
  })

  it.each(['configured', 'all-only', 'missing'] as const)('复用分类统计或文件回退，按来源隔离同名分类和未分类：%s', (mode) => {
    const categories = mode === 'missing' ? undefined : {
      all: [{ name: '开发规范', count: 3 }, { name: '未分类', count: 2 }, { name: 'all', count: 1 }],
      ...(mode === 'configured' ? {
        user: [{ name: '开发规范', count: 1 }, { name: '未分类', count: 1 }],
        shared: [{ name: '开发规范', count: 1 }, { name: '未分类', count: 1 }],
        global: [{ name: '开发规范', count: 1 }, { name: 'all', count: 1 }],
      } : {}),
    }
    render(<KnowledgeReferenceDrawer open documents={classifiedDocuments} categories={categories} onClose={vi.fn()} onReference={vi.fn()} />)
    const select = screen.getByRole('combobox', { name: '知识库分类' })
    for (const [scope, label, title] of [
      ['user', '用户层', '个人笔记'],
      ['shared', '共享层', '共享规范'],
      ['global', '全局层', '全局指南'],
    ]) {
      expect(within(select).getByRole('option', { name: `${label} · 开发规范（1）` })).toHaveValue(knowledgeCategoryKey(scope, '开发规范'))
      fireEvent.change(select, { target: { value: knowledgeCategoryKey(scope, '开发规范') } })
      expect(screen.getAllByRole('article')).toHaveLength(1)
      expect(within(screen.getByRole('article')).getByText(title)).toBeInTheDocument()
    }
    for (const [scope, title] of [['user', '个人未分类文件'], ['shared', '共享未分类文件']]) {
      fireEvent.change(select, { target: { value: knowledgeCategoryKey(scope, undefined) } })
      expect(screen.getAllByRole('article')).toHaveLength(1)
      expect(within(screen.getByRole('article')).getByText(title)).toBeInTheDocument()
      expect(within(screen.getByRole('article')).getByText('未分类')).toBeInTheDocument()
    }
    fireEvent.change(select, { target: { value: knowledgeCategoryKey('global', 'all') } })
    expect(screen.getAllByRole('article')).toHaveLength(1)
    expect(screen.getByText('特殊分类文件')).toBeInTheDocument()
  })

  it('分类与层级、路径搜索叠加，切换层级及关闭后重置筛选', () => {
    const onReference = vi.fn()
    const props = { documents: classifiedDocuments, onClose: vi.fn(), onReference }
    const { rerender } = render(<KnowledgeReferenceDrawer open {...props} />)
    const drawer = screen.getByRole('dialog', { name: '知识库引用' })
    const select = within(drawer).getByRole('combobox', { name: '知识库分类' })
    const search = within(drawer).getByRole('textbox', { name: '搜索知识库' })
    fireEvent.change(select, { target: { value: knowledgeCategoryKey('shared', '开发规范') } })
    fireEvent.change(search, { target: { value: 'team.md' } })
    expect(within(drawer).getByText('匹配 1 / 共 6 个知识文件')).toBeInTheDocument()
    fireEvent.click(within(drawer).getByRole('button', { name: '引用 共享规范' }))
    expect(onReference).toHaveBeenCalledWith(classifiedDocuments[1])
    fireEvent.click(within(drawer).getByRole('button', { name: '用户' }))
    expect(select).toHaveValue('all')
    expect(within(select).getByRole('option', { name: '开发规范（1）' })).toBeInTheDocument()
    expect(within(select).queryByRole('option', { name: /共享层|全局层/ })).not.toBeInTheDocument()
    expect(within(drawer).getByText('匹配 0 / 共 6 个知识文件')).toBeInTheDocument()
    expect(within(drawer).getByText('请调整搜索词、分类或知识层级。')).toBeInTheDocument()
    fireEvent.change(search, { target: { value: 'notes' } })
    fireEvent.change(select, { target: { value: knowledgeCategoryKey('user', '开发规范') } })
    expect(screen.getAllByRole('article')).toHaveLength(1)
    rerender(<KnowledgeReferenceDrawer open={false} {...props} />)
    rerender(<KnowledgeReferenceDrawer open {...props} />)
    expect(select).toHaveValue('all')
    expect(search).toHaveValue('')
    expect(within(drawer).getByRole('button', { name: '全部' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getAllByRole('article')).toHaveLength(6)
    expect(within(drawer).getByText('6 个可见知识文件')).toBeInTheDocument()
  })

  it('分类随文件刷新消失时回到全部分类，不保留无效筛选', () => {
    const props = { onClose: vi.fn(), onReference: vi.fn() }
    const { rerender } = render(<KnowledgeReferenceDrawer open documents={classifiedDocuments} {...props} />)
    const select = screen.getByRole('combobox', { name: '知识库分类' })
    fireEvent.change(select, { target: { value: knowledgeCategoryKey('user', '开发规范') } })
    rerender(<KnowledgeReferenceDrawer open documents={documents} {...props} />)
    expect(select).toHaveValue('all')
    expect(screen.getAllByRole('article')).toHaveLength(3)
    rerender(<KnowledgeReferenceDrawer open documents={classifiedDocuments} {...props} />)
    expect(select).toHaveValue('all')
    expect(screen.getAllByRole('article')).toHaveLength(6)
  })

  it.each([
    { loading: true, error: false, text: '正在加载知识库' },
    { loading: false, error: true, text: '知识库加载失败' },
    { loading: false, error: false, text: '暂无知识文件' },
  ])('加载、失败与空数据时禁用分类：$text', ({ loading, error, text }) => {
    render(<KnowledgeReferenceDrawer open documents={[]} loading={loading} error={error} onClose={vi.fn()} onReference={vi.fn()} />)
    expect(screen.getByRole('combobox', { name: '知识库分类' })).toBeDisabled()
    expect(screen.getByText(text)).toBeInTheDocument()
    expect(screen.queryByRole('article')).not.toBeInTheDocument()
  })

  it('长分类名保留完整提示，改变筛选后回到文件列表顶部', () => {
    const category = '这是一个很长的知识分类名称，用于检查省略显示与完整悬停提示'
    render(<KnowledgeReferenceDrawer open documents={[{ ...documents[0], category }]} onClose={vi.fn()} onReference={vi.fn()} />)
    expect(within(screen.getByRole('article')).getByText(category)).toHaveAttribute('title', category)
    const results = screen.getByRole('article').parentElement!
    results.scrollTop = 300
    fireEvent.change(screen.getByRole('combobox', { name: '知识库分类' }), { target: { value: knowledgeCategoryKey('user', category) } })
    expect(results.scrollTop).toBe(0)
  })
})
