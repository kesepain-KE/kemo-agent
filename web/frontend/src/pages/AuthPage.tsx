import { FormEvent, useState } from 'react'
import { AlertCircle, ArrowRight, Check, Eye, EyeOff, KeyRound, LoaderCircle, LockKeyhole, UserRound } from 'lucide-react'
import { ApiError, loginAuth, loginWithToken } from '../api/client'
import { AuthLayout } from '../components/AuthLayout'
import type { AuthStatusResponse } from '../types/api'
import styles from './AuthPage.module.css'

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
  const [visible, setVisible] = useState(false)
  const stage = status.stage || (status.methods.token ? 'token' : 'password')
  const isToken = stage === 'token'
  const isPending = Boolean(pending)
  const errorDescription = error ? 'auth-error' : undefined

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
    <AuthLayout>
      <section className={styles.card} aria-labelledby="auth-title">
        {status.requires_both && (
          <ol className={styles.steps} aria-label="认证步骤">
            <li className={styles.step} aria-current={isToken ? 'step' : undefined} data-complete={!isToken}>
              <span className={styles.stepNumber} aria-hidden="true">{isToken ? '1' : <Check size={14} />}</span>
              <span>访问令牌{!isToken && <span className="sr-only">，已通过</span>}</span>
            </li>
            <li className={styles.step} aria-current={!isToken ? 'step' : undefined}>
              <span className={styles.stepNumber} aria-hidden="true">2</span>
              <span>账号登录</span>
            </li>
          </ol>
        )}
        <header className={styles.heading}>
          <span className={styles.eyebrow}><LockKeyhole size={13} aria-hidden="true" />访问认证</span>
          <h1 id="auth-title">{isToken ? '验证访问令牌' : '登录工作区'}</h1>
          <p>{isToken
            ? status.requires_both ? '先验证当前实例的 Token，再使用账号登录。' : '输入当前实例的 Token，安全进入智能体工作区。'
            : status.requires_both ? '访问令牌已通过，请继续验证用户名和密码。' : '使用当前实例的用户名和密码登录。'}</p>
        </header>
        <form
          className={`${styles.form} auth-bubble`}
          onSubmit={isToken ? submitToken : submitPassword}
          aria-label={isToken ? 'Token 认证' : '用户名密码认证'}
          aria-busy={isPending}
        >
          {isToken && status.methods.token && (
            <div className={styles.field}>
              <label className={styles.label} htmlFor="auth-token">访问令牌 · Token</label>
              <div className={styles.input}>
                <KeyRound size={17} aria-hidden="true" />
                <input id="auth-token" name="token" type={visible ? 'text' : 'password'} value={token} onChange={(event) => { setToken(event.target.value); setError('') }} autoComplete="off" autoCapitalize="none" spellCheck={false} placeholder="输入访问令牌" aria-label="访问令牌" aria-invalid={Boolean(error)} aria-describedby={errorDescription} disabled={isPending} required />
                <button className={styles.reveal} type="button" aria-label={visible ? '隐藏访问令牌' : '显示访问令牌'} aria-pressed={visible} onClick={() => setVisible(!visible)} disabled={isPending}>{visible ? <EyeOff size={17} /> : <Eye size={17} />}</button>
              </div>
            </div>
          )}
          {!isToken && status.methods.password && (
            <>
              <div className={styles.field}>
                <label className={styles.label} htmlFor="auth-username">用户名</label>
                <div className={styles.input}>
                  <UserRound size={17} aria-hidden="true" />
                  <input id="auth-username" name="username" value={username} onChange={(event) => { setUsername(event.target.value); setError('') }} autoComplete="username" autoCapitalize="none" spellCheck={false} autoFocus={status.requires_both} placeholder="输入用户名" aria-label="用户名" aria-invalid={Boolean(error)} aria-describedby={errorDescription} disabled={isPending} required />
                </div>
              </div>
              <div className={styles.field}>
                <label className={styles.label} htmlFor="auth-password">密码</label>
                <div className={styles.input}>
                  <LockKeyhole size={17} aria-hidden="true" />
                  <input id="auth-password" name="password" type={visible ? 'text' : 'password'} value={password} onChange={(event) => { setPassword(event.target.value); setError('') }} autoComplete="current-password" placeholder="输入密码" aria-label="密码" aria-invalid={Boolean(error)} aria-describedby={errorDescription} disabled={isPending} required />
                  <button className={styles.reveal} type="button" aria-label={visible ? '隐藏密码' : '显示密码'} aria-pressed={visible} onClick={() => setVisible(!visible)} disabled={isPending}>{visible ? <EyeOff size={17} /> : <Eye size={17} />}</button>
                </div>
              </div>
            </>
          )}
          {error && <div className={styles.error} id="auth-error" role="alert"><AlertCircle size={16} aria-hidden="true" /><span>{error}</span></div>}
          <button className={styles.submit} type="submit" disabled={isPending || (isToken ? !token : !username || !password)}>
            {isPending
              ? <><LoaderCircle size={16} className="spin" aria-hidden="true" />{isToken ? '正在验证' : '正在登录'}</>
              : <>{isToken ? status.requires_both ? '继续账号验证' : '使用令牌进入' : '登录工作区'}<ArrowRight size={16} aria-hidden="true" /></>}
          </button>
        </form>
      </section>
    </AuthLayout>
  )
}
