import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { AppShell } from '../components/AppShell'
import { FilesPage } from './FilesPage'
import { KnowledgePage } from './KnowledgePage'
import { ProfilePage } from './ProfilePage'
import { RuntimeModulesPage } from './RuntimeModulesPage'
import { SensePage } from './SensePage'
import { SettingsPage } from './SettingsPage'
import { SkillsPage } from './SkillsPage'
import { TasksPage } from './TasksPage'

const pages = [
  { path: 'tasks', element: <TasksPage /> },
  { path: 'knowledge', element: <KnowledgePage /> },
  { path: 'skills', element: <SkillsPage /> },
  { path: 'sense', element: <SensePage /> },
  { path: 'files', element: <FilesPage /> },
  { path: 'runtime', element: <RuntimeModulesPage /> },
  { path: 'profile', element: <ProfilePage /> },
  { path: 'settings', element: <SettingsPage /> },
]

function renderPage(path: string) {
  const router = createMemoryRouter([{ path: '/', element: <AppShell />, children: pages }], { initialEntries: [`/${path}?user=kesepain`] })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>)
}

describe('V16 module pages', () => {
  it('任务页展示真实空态与任务分类', async () => {
    renderPage('tasks')
    expect(await screen.findByRole('heading', { name: '任务中枢' })).toBeInTheDocument()
    expect(await screen.findByText('暂无任务计划')).toBeInTheDocument()
    expect(screen.getAllByText('定时任务').length).toBeGreaterThan(0)
  })

  it('知识页展示文件索引元数据', async () => {
    renderPage('knowledge')
    expect(await screen.findByRole('heading', { name: '知识库' })).toBeInTheDocument()
    expect(await screen.findByText('个人笔记')).toBeInTheDocument()
    expect(await screen.findByText('共享笔记')).toBeInTheDocument()
    expect(screen.getAllByText('共享层').length).toBeGreaterThan(0)
    expect(screen.getByText('外接项目 · kemo-graph')).toBeInTheDocument()
  })

  it('技能页展示注册工具和层级', async () => {
    renderPage('skills')
    expect(await screen.findByRole('heading', { name: '技能中心' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'clock' })).toBeInTheDocument()
    expect(screen.getAllByText('基础插件').length).toBeGreaterThan(0)
    expect(screen.queryByText('项目层')).not.toBeInTheDocument()
  })

  it('感知页展示目录模块与主智能体过滤状态', async () => {
    renderPage('sense')
    expect(await screen.findByRole('heading', { name: '全局感知' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: '运行时感知' })).toBeInTheDocument()
    expect(screen.getAllByText('1 个来源').length).toBeGreaterThan(0)
    expect(screen.getByText('数据注册')).toBeInTheDocument()
    expect(screen.getByText('用户过滤')).toBeInTheDocument()
    expect(screen.getByText('当前注入')).toBeInTheDocument()
    expect(screen.getByText('System Prompt / Global Sense')).toBeInTheDocument()
    expect(screen.queryByText('项目层')).not.toBeInTheDocument()
  })

  it('配置页提供可操作的外观设置和只读边界', async () => {
    renderPage('settings')
    expect(await screen.findByRole('heading', { name: '配置概览' })).toBeInTheDocument()
    expect(screen.getByText('默认只读')).toBeInTheDocument()
    expect(screen.getByText('界面主题')).toBeInTheDocument()
    expect(screen.getByText('72%')).toBeInTheDocument()
    expect(screen.getByText('88%')).toBeInTheDocument()
    expect(screen.getByText('105%')).toBeInTheDocument()
    fireEvent.click(screen.getByText('Prompt 与 Expand'))
    expect(await screen.findByText('Prompt 注入诊断')).toBeInTheDocument()
    expect(await screen.findByText('user_soul')).toBeInTheDocument()
    fireEvent.click(screen.getByText('用户配置 JSON'))
    expect(await screen.findByLabelText('用户配置 JSON')).toBeInTheDocument()
    expect(screen.getByText('只读模式')).toBeInTheDocument()
  })

  it('文件空间浏览三类目录并执行二次确认删除', async () => {
    renderPage('files')
    expect(await screen.findByRole('heading', { name: '文件空间' })).toBeInTheDocument()
    expect((await screen.findAllByText('readme.txt')).length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('tab', { name: /全局临时/ }))
    expect((await screen.findAllByText('cache.tmp')).length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', { name: '删除文件' }))
    expect(screen.getByText('确认删除这个文件？')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '确认删除' }))
    expect(await screen.findByText('已删除 cache.tmp')).toBeInTheDocument()
  })

  it('运行模块展示子代理、消息传输和三层 Expand', async () => {
    renderPage('runtime')
    expect(await screen.findByRole('heading', { name: '运行模块' })).toBeInTheDocument()
    expect(await screen.findByText('context_manage')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: /外部消息/ }))
    expect(await screen.findByText('OneBot 正向 WebSocket')).toBeInTheDocument()
    expect(screen.getByText('123456')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: /Expand/ }))
    expect(await screen.findByText('example')).toBeInTheDocument()
    expect(screen.getByText('已注册')).toBeInTheDocument()
  })

  it('用户资料读取并原子保存人格 Markdown', async () => {
    renderPage('profile')
    expect(await screen.findByRole('heading', { name: '用户资料' })).toBeInTheDocument()
    const editor = await screen.findByLabelText('用户人格 Markdown')
    expect(editor).toHaveValue('# 用户人格')
    fireEvent.change(editor, { target: { value: '# 更新后的用户人格' } })
    fireEvent.click(screen.getByRole('button', { name: '保存用户人格' }))
    expect(await screen.findByText('用户人格已原子写入。')).toBeInTheDocument()
    expect(screen.getByLabelText('全局人格 Markdown')).toHaveValue('# 全局人格')
  })
})
