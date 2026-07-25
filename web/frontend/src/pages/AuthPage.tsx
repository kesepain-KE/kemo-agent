import { FormEvent, useState } from 'react'
import { KeyRound, LoaderCircle, LockKeyhole, UserRound } from 'lucide-react'
import { ApiError, loginAuth, loginWithToken } from '../api/client'
import type { AuthStatusResponse } from '../types/api'

interface AuthPageProps {
  status: AuthStatusResponse
  onAuthenticated: () => Promise<void> | void
}

function messageOf(error: unknown) {
  return error instanceof ApiError ? error.message : '认证请求失败，请稍后重试'
}

export function AuthPage({
  status,
  onAuthenticated,
}: AuthPageProps) {
  const [token, setToken] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [pending, setPending] = useState<'token' | 'password' | ''>('')
  const [error, setError] = useState('')
  const stage = status.stage || (status.methods.token ? 'token' : 'password')

  const submitToken = async (event: FormEvent) => {
    event.preventDefault()
    if (!token || pending) return
    setPending('token')
    setError('')
    try {
      await loginWithToken(token)
      setToken('')
      await onAuthenticated()
    } catch (caught) {
      setError(messageOf(caught))
    } finally {
      setPending('')
    }
  }

  const submitPassword = async (event: FormEvent) => {
    event.preventDefault()
    if (!username || !password || pending) return
    setPending('password')
    setError('')
    try {
      await loginAuth(username, password)
      setPassword('')
      await onAuthenticated()
    } catch (caught) {
      setError(messageOf(caught))
    } finally {
      setPending('')
    }
  }

  return (
    <main className="auth-screen">
      <section className="auth-panel" aria-label="Web 访问认证">
        <div className="auth-brand">
          <span className="auth-logo"><img src="/kemo-agent.jpg" width={571} height={568} alt="kemo-agent logo" /></span>
          <span><strong>kemo-agent</strong><small>Personal Agent Runtime</small></span>
        </div>
        <div className="auth-heading">
          <span className="auth-kicker">Secure Session</span>
          <h1>访问认证</h1>
          <p>完成验证后进入当前实例的智能体工作区。</p>
        </div>

        {error && <div className="auth-error" role="alert">{error}</div>}

        <div className="auth-methods">
          {stage === 'token' && status.methods.token && (
            <form className="auth-form" onSubmit={submitToken}>
              <div className="auth-method-title"><KeyRound size={18} /><span><strong>访问令牌</strong><small>{status.requires_both ? '第 1 步 · 验证当前实例配置的 Token' : '使用当前实例配置的 Token 建立会话'}</small></span></div>
              <label>
                <span>Token</span>
                <div className="auth-input"><LockKeyhole size={16} /><input type="password" value={token} onChange={(event) => setToken(event.target.value)} autoComplete="off" placeholder="输入访问令牌" /></div>
              </label>
              <button className="auth-submit" type="submit" disabled={!token || Boolean(pending)}>{pending === 'token' ? <><LoaderCircle size={16} className="spin" />正在验证</> : status.requires_both ? '继续账号验证' : '使用令牌进入'}</button>
            </form>
          )}

          {stage === 'password' && status.methods.password && (
            <form className="auth-form" onSubmit={submitPassword}>
              <div className="auth-method-title"><UserRound size={18} /><span><strong>账号登录</strong><small>{status.requires_both ? 'Token 已通过 · 第 2 步验证用户名和密码' : '使用当前实例配置的用户名和密码'}</small></span></div>
              <label>
                <span>用户名</span>
                <div className="auth-input"><UserRound size={16} /><input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" placeholder="输入用户名" /></div>
              </label>
              <label>
                <span>密码</span>
                <div className="auth-input"><LockKeyhole size={16} /><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" placeholder="输入密码" /></div>
              </label>
              <button className="auth-submit" type="submit" disabled={!username || !password || Boolean(pending)}>{pending === 'password' ? <><LoaderCircle size={16} className="spin" />正在登录</> : '登录工作区'}</button>
            </form>
          )}
        </div>
        <div className="auth-foot">认证信息只用于建立签名会话，不会进入智能体上下文或设置接口。</div>
      </section>
    </main>
  )
}
