import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { AppShell } from '../components/AppShell'
import { KnowledgePage } from './KnowledgePage'
import { SensePage } from './SensePage'
import { SettingsPage } from './SettingsPage'
import { SkillsPage } from './SkillsPage'
import { TasksPage } from './TasksPage'

const pages = [
  { path: 'tasks', element: <TasksPage /> },
  { path: 'knowledge', element: <KnowledgePage /> },
  { path: 'skills', element: <SkillsPage /> },
  { path: 'sense', element: <SensePage /> },
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
    expect(await screen.findByRole('heading', { name: 'runtime' })).toBeInTheDocument()
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
})
