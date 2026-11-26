import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { createMemoryRouter, Outlet, RouterProvider } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import { http } from 'msw'
import { server } from './test/server'
import { router as productionRouter } from './router'
import type { ShellOutletContext } from './components/appShellTypes'
import { ReferencePage } from './reference/ReferencePage'
import { ModuleWorkbench } from './module-workspace/ModuleWorkbench'
import { MessageWorkbench } from './module-workspace/MessageWorkbench'

afterEach(cleanup)

describe('current UI production routes', () => {
  it.each([
    ['status', '运行诊断'], ['tasks', '任务中枢'], ['knowledge', '知识库'],
    ['memory', '记忆'], ['agents', '子智能体'], ['skills', '工具与技能'],
    ['runtime', '运行模块'],
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
  it('冷启动直进 Token 不读取提示词、日志或模型能力，顶栏打开模型菜单后才查能力', async () => {
    const statusReads: string[] = []
    let logs = 0
    let capabilities = 0
    server.use(
      http.get('/api/users/kesepain/runtime/status', ({ request }) => {
        statusReads.push(new URL(request.url).searchParams.get('sections') || '')
      }),
      http.get('/api/users/kesepain/runtime/logs', () => { logs++ }),
      http.get('/api/users/kesepain/provider/model-capabilities', () => { capabilities++ }),
    )
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const router = createMemoryRouter(productionRouter.routes, { initialEntries: ['/status?user=kesepain&session=s1&tab=tokens'] })
    const view = render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>)
    try {
      expect(await screen.findByText('按小时明细')).toBeVisible()
      const modelMenu = screen.getByTitle('模型与思考档位')
      await waitFor(() => expect(modelMenu).toHaveTextContent('test-model'))
      expect([...statusReads].sort()).toEqual(['api', 'maintenance', 'tokens'])
      expect(logs).toBe(0)
      expect(capabilities).toBe(0)
      fireEvent.click(modelMenu)
      await waitFor(() => expect(capabilities).toBe(1))
      expect(screen.getByText('按小时明细')).toBeVisible()
    } finally {
      view.unmount(); router.dispose(); client.clear()
    }
  })
  it.each(['sense', 'expand'])('%s loads the module workbench', async path => {
    const route = productionRouter.routes[0].children!.find(item => item.path === path)!
    const loaded = await (route.lazy as () => Promise<{ Component: unknown }>)()
    expect(loaded.Component).toBe(ModuleWorkbench)
  })
  it('messages loads the message workbench', async () => {
    const route = productionRouter.routes[0].children!.find(item => item.path === 'messages')!
    const loaded = await (route.lazy as () => Promise<{ Component: unknown }>)()
    expect(loaded.Component).toBe(MessageWorkbench)
  })
})
