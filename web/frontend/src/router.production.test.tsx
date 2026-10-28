import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { createMemoryRouter, Outlet, RouterProvider } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import { router as productionRouter } from './router'
import type { ShellOutletContext } from './components/appShellTypes'
import { ReferencePage } from './reference/ReferencePage'

afterEach(cleanup)

describe('current UI production routes', () => {
  it.each([
    ['status', '运行诊断'], ['tasks', '任务中枢'], ['knowledge', '知识库'],
    ['memory', '记忆'], ['agents', '子智能体'], ['skills', '工具与技能'],
    ['sense', '感知'], ['expand', '拓展'], ['messages', '外部消息'], ['runtime', '运行模块'],
  ])('%s loads the current ReferencePage UI, never the obsolete duplicate', async (path, heading) => {
    const children = productionRouter.routes[0].children!
    const route = children.find(item => item.path === path)
    expect(route?.lazy).toBeTypeOf('function')
    const loaded = await (route!.lazy as () => Promise<{ Component: unknown }>)()
    expect(loaded.Component).toBe(ReferencePage)
    const context = { user: 'kesepain', sessionId: 's1', chatRunning: false } as ShellOutletContext
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const router = createMemoryRouter([{ path: '/', element: <Outlet context={context} />, children }], { initialEntries: [`/${path}`] })
    const view = render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>)
    try {
      expect(await screen.findByRole('heading', { name: heading })).toBeInTheDocument()
    } finally {
      view.unmount()
      router.dispose()
      client.clear()
    }
  })
})
