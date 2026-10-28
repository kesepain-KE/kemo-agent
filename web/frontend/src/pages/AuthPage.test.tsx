import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it, vi } from 'vitest'
import { server } from '../test/server'
import { useUiStore } from '../store/ui'
import type { AuthStatusResponse } from '../types/api'
import { AuthPage } from './AuthPage'

const tokenStatus: AuthStatusResponse = {
  enabled: true,
  authenticated: false,
  stage: 'token',
  requires_both: false,
  methods: { token: true, password: false },
  session_cookie_configured: true,
}
const passwordStatus: AuthStatusResponse = {
  ...tokenStatus, stage: 'password', methods: { token: false, password: true },
}

describe('AuthPage', () => {
  it('认证前也应用已保存的主题与字号', () => {
    const previous = useUiStore.getState()
    useUiStore.setState({ theme: 'dark', fontSize: 'large' })
    const { unmount } = render(<AuthPage status={tokenStatus} onAuthenticated={vi.fn()} />)
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(document.documentElement.dataset.fontsize).toBe('large')
    unmount()
    useUiStore.setState({ theme: previous.theme, fontSize: previous.fontSize })
    document.documentElement.dataset.theme = previous.theme
    document.documentElement.dataset.fontsize = previous.fontSize === 'small' ? 'compact' : previous.fontSize === 'large' ? 'large' : 'default'
  })

  it('双重认证显示两个步骤并标记当前步骤，单步认证不显示多余步骤', () => {
    const { rerender } = render(<AuthPage status={{ ...tokenStatus, requires_both: true, methods: { token: true, password: true } }} onAuthenticated={vi.fn()} />)
    const steps = screen.getByRole('list', { name: '认证步骤' })
    expect(within(steps).getAllByRole('listitem')).toHaveLength(2)
    expect(within(steps).getByText('访问令牌').closest('li')).toHaveAttribute('aria-current', 'step')
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('验证访问令牌')
    rerender(<AuthPage status={tokenStatus} onAuthenticated={vi.fn()} />)
    expect(screen.queryByRole('list', { name: '认证步骤' })).not.toBeInTheDocument()
  })

  it('账号阶段明确显示令牌已通过，并把焦点交给用户名', () => {
    render(<AuthPage status={{ ...passwordStatus, requires_both: true, methods: { token: true, password: true } }} onAuthenticated={vi.fn()} />)
    const steps = screen.getByRole('list', { name: '认证步骤' })
    expect(within(steps).getByText('，已通过').closest('li')).toHaveAttribute('data-complete', 'true')
    expect(within(steps).getByText('账号登录').closest('li')).toHaveAttribute('aria-current', 'step')
    expect(screen.getByLabelText('用户名')).toHaveFocus()
    expect(screen.queryByLabelText('访问令牌')).not.toBeInTheDocument()
  })

  it.each([
    { status: tokenStatus, field: '访问令牌', show: '显示访问令牌', hide: '隐藏访问令牌' },
    { status: passwordStatus, field: '密码', show: '显示密码', hide: '隐藏密码' },
  ])('支持 $field 显隐且不触发表单提交', async ({ status, field, show, hide }) => {
    const onAuthenticated = vi.fn()
    render(<AuthPage status={status} onAuthenticated={onAuthenticated} />)
    const input = screen.getByLabelText(field)
    fireEvent.change(input, { target: { value: 'test-only-secret' } })
    expect(input).toHaveAttribute('type', 'password')
    fireEvent.click(screen.getByRole('button', { name: show }))
    expect(input).toHaveAttribute('type', 'text')
    expect(input).toHaveValue('test-only-secret')
    expect(screen.getByRole('button', { name: hide })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('button', { name: hide }))
    expect(input).toHaveAttribute('type', 'password')
    expect(onAuthenticated).not.toHaveBeenCalled()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('令牌错误显示在表单内，修改后清除旧错误，支持按 Enter 重试', async () => {
    const user = userEvent.setup()
    const onAuthenticated = vi.fn()
    const seenTokens: string[] = []
    server.use(http.post('/api/auth/token', async ({ request }) => {
      const { token } = await request.json() as { token: string }
      seenTokens.push(token)
      return token === 'correct-test-token'
        ? HttpResponse.json({ ...tokenStatus, authenticated: true, stage: 'authenticated' })
        : HttpResponse.json({ error: { message: '认证信息无效', code: 'invalid_credentials' } }, { status: 401 })
    }))
    render(<AuthPage status={tokenStatus} onAuthenticated={onAuthenticated} />)
    const input = screen.getByLabelText('访问令牌')
    await user.type(input, 'incorrect-test-token{Enter}')
    const alert = await screen.findByRole('alert')
    expect(screen.getByRole('form', { name: 'Token 认证' })).toContainElement(alert)
    expect(input).toHaveAttribute('aria-invalid', 'true')
    expect(input).toHaveAccessibleDescription('认证信息无效')
    expect(input).toHaveValue('incorrect-test-token')
    await user.clear(input)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(input).toHaveAttribute('aria-invalid', 'false')
    await user.type(input, 'correct-test-token{Enter}')
    await waitFor(() => expect(onAuthenticated).toHaveBeenCalledOnce())
    expect(seenTokens).toEqual(['incorrect-test-token', 'correct-test-token'])
    expect(input).toHaveValue('')
  })

  it('账号验证失败保留输入供修正，成功清空密码', async () => {
    const onAuthenticated = vi.fn()
    server.use(http.post('/api/auth/login', async ({ request }) => {
      const body = await request.json() as { username: string; password: string }
      return body.username === 'test-user' && body.password === 'correct-test-password'
        ? HttpResponse.json({ ...passwordStatus, authenticated: true, stage: 'authenticated' })
        : HttpResponse.json({ error: { message: '认证信息无效' } }, { status: 401 })
    }))
    render(<AuthPage status={passwordStatus} onAuthenticated={onAuthenticated} />)
    const username = screen.getByLabelText('用户名')
    const password = screen.getByLabelText('密码')
    fireEvent.change(username, { target: { value: 'test-user' } })
    fireEvent.change(password, { target: { value: 'incorrect-test-password' } })
    fireEvent.click(screen.getByRole('button', { name: '登录工作区' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('认证信息无效')
    expect(username).toHaveValue('test-user')
    expect(password).toHaveValue('incorrect-test-password')
    fireEvent.change(password, { target: { value: 'correct-test-password' } })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '登录工作区' }))
    await waitFor(() => expect(onAuthenticated).toHaveBeenCalledOnce())
    expect(password).toHaveValue('')
  })

  it('请求期间锁定输入与显隐按钮，避免重复提交', async () => {
    let release!: () => void
    const responseReady = new Promise<void>(resolve => { release = resolve })
    let requests = 0
    const onAuthenticated = vi.fn()
    server.use(http.post('/api/auth/token', async () => {
      requests += 1
      await responseReady
      return HttpResponse.json({ ...tokenStatus, authenticated: true, stage: 'authenticated' })
    }))
    render(<AuthPage status={tokenStatus} onAuthenticated={onAuthenticated} />)
    const input = screen.getByLabelText('访问令牌')
    fireEvent.change(input, { target: { value: 'test-only-token' } })
    fireEvent.click(screen.getByRole('button', { name: '使用令牌进入' }))
    expect(screen.getByRole('form')).toHaveAttribute('aria-busy', 'true')
    expect(input).toBeDisabled()
    expect(screen.getByRole('button', { name: '显示访问令牌' })).toBeDisabled()
    fireEvent.submit(screen.getByRole('form'))
    await waitFor(() => expect(requests).toBe(1))
    await act(async () => { release() })
    await waitFor(() => expect(onAuthenticated).toHaveBeenCalledOnce())
    expect(input).not.toBeDisabled()
  })

  it('网络失败提供可重试反馈，输入不会丢失', async () => {
    server.use(http.post('/api/auth/token', () => HttpResponse.error()))
    render(<AuthPage status={tokenStatus} onAuthenticated={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('访问令牌'), { target: { value: 'test-only-token' } })
    fireEvent.click(screen.getByRole('button', { name: '使用令牌进入' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('认证请求失败，请稍后重试')
    expect(screen.getByLabelText('访问令牌')).toHaveValue('test-only-token')
    expect(screen.getByRole('button', { name: '使用令牌进入' })).toBeEnabled()
  })
})
