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

  it('Token 通过 POST 请求验证且不会从 URL 自动登录', async () => {
    let authenticated = false
    let seenToken = ''
    server.use(
      http.get('/api/auth/status', () => HttpResponse.json({
          enabled: true,
          authenticated,
          stage: authenticated ? 'authenticated' : 'token',
          requires_both: false,
          methods: { token: true, password: false },
          session_cookie_configured: true,
      })),
      http.post('/api/auth/token', async ({ request }) => {
        const body = await request.json() as { token?: string }
        seenToken = body.token || ''
        authenticated = seenToken === 'form-secret'
        return HttpResponse.json({
          enabled: true,
          authenticated,
          stage: authenticated ? 'authenticated' : 'token',
          requires_both: false,
          methods: { token: true, password: false },
          session_cookie_configured: true,
        }, { status: authenticated ? 200 : 401 })
      }),
    )
    window.history.replaceState({}, '', '/chat?token=url-secret&user=alice')
    renderGate('/chat?token=url-secret&user=alice')
    expect(await screen.findByPlaceholderText('输入访问令牌')).toHaveValue('')
    fireEvent.change(screen.getByPlaceholderText('输入访问令牌'), { target: { value: 'form-secret' } })
    fireEvent.click(screen.getByRole('button', { name: '使用令牌进入' }))
    expect(await screen.findByText('protected workspace')).toBeInTheDocument()
    expect(seenToken).toBe('form-secret')
    expect(window.location.pathname + window.location.search).toBe('/chat?user=alice')
    expect(window.location.search).not.toContain('token=')
  })

  it('账号密码登录成功后挂载工作区', async () => {
    let authenticated = false
    server.use(
      http.get('/api/auth/status', () => HttpResponse.json({
        enabled: true,
        authenticated,
        stage: authenticated ? 'authenticated' : 'password',
        requires_both: false,
        methods: { token: false, password: true },
        session_cookie_configured: true,
      })),
      http.post('/api/auth/login', async ({ request }) => {
        const body = await request.json() as { username?: string; password?: string }
        authenticated = body.username === 'alice' && body.password === 'password'
        return HttpResponse.json({
          enabled: true,
          authenticated,
          stage: authenticated ? 'authenticated' : 'password',
          requires_both: false,
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

  it('同时启用两种认证时先验证 Token 再显示账号密码', async () => {
    let stage: 'token' | 'password' | 'authenticated' = 'token'
    server.use(
      http.get('/api/auth/status', () => HttpResponse.json({
        enabled: true,
        authenticated: stage === 'authenticated',
        stage,
        requires_both: true,
        methods: { token: true, password: true },
        session_cookie_configured: true,
      })),
      http.post('/api/auth/token', async ({ request }) => {
        const body = await request.json() as { token?: string }
        if (body.token !== 'token-secret') return HttpResponse.json({ error: { message: '认证信息无效' } }, { status: 401 })
        stage = 'password'
        return HttpResponse.json({ enabled: true, authenticated: false, stage, requires_both: true, methods: { token: true, password: true }, session_cookie_configured: true })
      }),
      http.post('/api/auth/login', async ({ request }) => {
        const body = await request.json() as { username?: string; password?: string }
        if (body.username !== 'alice' || body.password !== 'password') return HttpResponse.json({ error: { message: '认证信息无效' } }, { status: 401 })
        stage = 'authenticated'
        return HttpResponse.json({ enabled: true, authenticated: true, stage, requires_both: true, methods: { token: true, password: true }, session_cookie_configured: true })
      }),
    )
    renderGate('/')
    expect(await screen.findByPlaceholderText('输入访问令牌')).toBeInTheDocument()
    expect(screen.queryByPlaceholderText('输入用户名')).not.toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('输入访问令牌'), { target: { value: 'token-secret' } })
    fireEvent.click(screen.getByRole('button', { name: '继续账号验证' }))
    expect(await screen.findByPlaceholderText('输入用户名')).toBeInTheDocument()
    expect(screen.queryByPlaceholderText('输入访问令牌')).not.toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('输入用户名'), { target: { value: 'alice' } })
    fireEvent.change(screen.getByPlaceholderText('输入密码'), { target: { value: 'password' } })
    fireEvent.click(screen.getByRole('button', { name: '登录工作区' }))
    await waitFor(() => expect(screen.getByText('protected workspace')).toBeInTheDocument())
  })
})
