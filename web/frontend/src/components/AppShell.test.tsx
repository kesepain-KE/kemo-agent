import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { AppShell } from './AppShell'
import { ChatPage } from '../pages/ChatPage'

function renderApp(path = '/chat') {
  const router = createMemoryRouter([{ path: '/', element: <AppShell />, children: [{ path: 'chat', element: <ChatPage /> }] }], { initialEntries: [path] })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>)
}

describe('AppShell navigation', () => {
  it('加载真实用户并展示空聊天入口', async () => {
    renderApp('/chat')
    await waitFor(() => expect(screen.getAllByText('kesepain').length).toBeGreaterThan(0))
    expect(screen.getByText(/Web 已接入真实用户/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '切换当前用户' })).toBeInTheDocument()
  })

  it('从 URL 恢复用户与会话并加载历史空状态', async () => {
    renderApp('/chat?user=kesepain&session=s1')
    await waitFor(() => expect(screen.getAllByText('s1').length).toBeGreaterThan(0))
    expect((await screen.findAllByText('kemo-agent')).length).toBeGreaterThan(0)
  })

  it('顶部栏包含上下文窗口、字号、主题和运行状态控件', async () => {
    renderApp('/chat')
    await waitFor(() => expect(screen.getAllByText('kesepain').length).toBeGreaterThan(0))
    expect(screen.getAllByTitle('查看上下文与运行状态').length).toBeGreaterThan(0)
    expect(screen.getAllByTitle('调整界面字号').length).toBeGreaterThan(0)
    expect(screen.getByTitle(/切换为高级/)).toBeInTheDocument()
    expect(screen.getByTitle('运行状态')).toBeInTheDocument()
    expect(screen.getByTitle('命令面板')).toBeInTheDocument()
  })
})
