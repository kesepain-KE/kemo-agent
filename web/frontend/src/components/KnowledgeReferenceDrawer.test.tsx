import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { KnowledgeReferenceDrawer } from './KnowledgeReferenceDrawer'

const documents = [
  { scope: 'user', relative_path: 'notes.md', title: '个人笔记', size: 120, updated_at: 1, active_for_main_agent: true },
  { scope: 'shared', relative_path: 'team.md', title: '共享规范', size: 90, updated_at: 1, active_for_main_agent: true },
  { scope: 'global', relative_path: 'guide.md', title: '全局指南', size: 160, updated_at: 1, active_for_main_agent: true },
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

  it('保留路径与层级搜索，关闭后重置，不再提供分类控件', () => {
    const props = { documents, onClose: vi.fn(), onReference: vi.fn() }
    const { rerender } = render(<KnowledgeReferenceDrawer open {...props} />)
    const search = screen.getByRole('textbox', { name: '搜索知识库' })
    fireEvent.click(screen.getByRole('button', { name: '共享' }))
    fireEvent.change(search, { target: { value: 'team.md' } })
    expect(screen.getAllByRole('article')).toHaveLength(1)
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '用户' }))
    expect(screen.getByText('请调整搜索词或知识层级。')).toBeInTheDocument()
    rerender(<KnowledgeReferenceDrawer open={false} {...props} />)
    rerender(<KnowledgeReferenceDrawer open {...props} />)
    expect(search).toHaveValue('')
    expect(screen.getAllByRole('article')).toHaveLength(3)
    expect(screen.getByRole('button', { name: '全部' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('长文件路径保留完整提示，改变搜索后回到文件列表顶部', () => {
    const path = 'architecture/deeply/nested/directory/long-document-name.md'
    render(<KnowledgeReferenceDrawer open documents={[{ ...documents[0], relative_path: path }]} onClose={vi.fn()} onReference={vi.fn()} />)
    expect(within(screen.getByRole('article')).getByText(path)).toHaveAttribute('title', path)
    const results = screen.getByRole('article').parentElement!
    results.scrollTop = 300
    fireEvent.change(screen.getByRole('textbox', { name: '搜索知识库' }), { target: { value: 'architecture' } })
    expect(results.scrollTop).toBe(0)
  })
})
