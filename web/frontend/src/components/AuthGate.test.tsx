import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { HttpResponse, http } from 'msw'
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { server } from '../test/server'
import { AuthGate } from './AuthGate'

function LocationProbe() {
  const location = useLocation()
  return <span data-testid="location">{location.pathname}{location.search}</span>
}

function renderGate(path = '/') {
  const router = createMemoryRouter([
    {
      path: '*',
      element: <AuthGate><div>protected workspace<LocationProbe /></div></AuthGate>,
    },
  ], { initialEntries: [path] })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>)
}

describe('AuthGate', () => {
  it('鉴权关闭时保持兼容并直接进入工作区', async () => {
    renderGate('/chat')
    expect(await screen.findByText('protected workspace')).toBeInTheDocument()
  })

  it('自动消费 URL token、清理地址并进入工作区', async () => {
    let authenticated = false
    let seenToken = ''
    server.use(
      http.get('/api/auth/status', ({ request }) => {
        const token = new URL(request.url).searchParams.get('token') || ''
        if (token) {
          seenToken = token
          authenticated = token === 'url-secret'
        }
        return HttpResponse.json({
          enabled: true,
          authenticated,
          methods: { token: true, password: false },
          session_cookie_configured: true,
        }, { status: token && !authenticated ? 401 : 200 })
      }),
    )
    window.history.replaceState({}, '', '/chat?token=url-secret&user=alice')
    renderGate('/chat?token=url-secret&user=alice')
    expect(await screen.findByText('protected workspace')).toBeInTheDocument()
    expect(seenToken).toBe('url-secret')
    expect(window.location.pathname + window.location.search).toBe('/chat?user=alice')
    expect(window.location.search).not.toContain('token=')
  })

  it('账号密码登录成功后挂载工作区', async () => {
    let authenticated = false
    server.use(
      http.get('/api/auth/status', () => HttpResponse.json({
        enabled: true,
        authenticated,
        methods: { token: false, password: true },
        session_cookie_configured: true,
      })),
      http.post('/api/auth/login', async ({ request }) => {
        const body = await request.json() as { username?: string; password?: string }
        authenticated = body.username === 'alice' && body.password === 'password'
        return HttpResponse.json({
          enabled: true,
          authenticated,
          methods: { token: false, password: true },
          session_cookie_configured: true,
        }, { status: authenticated ? 200 : 401 })
      }),
    )
    renderGate('/')
    fireEvent.change(await screen.findByPlaceholderText('输入用户名'), { target: { value: 'alice' } })
    fireEvent.change(screen.getByPlaceholderText('输入密码'), { target: { value: 'password' } })
    fireEvent.click(screen.getByRole('button', { name: '登录工作区' }))
    await waitFor(() => expect(screen.getByText('protected workspace')).toBeInTheDocument())
  })
})
