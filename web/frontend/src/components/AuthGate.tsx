import { ReactNode, useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { LoaderCircle, RefreshCw } from 'lucide-react'
import { useLocation } from 'react-router-dom'
import {
  AUTH_REQUIRED_EVENT,
  ApiError,
  bootstrapAuth,
  getAuthStatus,
} from '../api/client'
import { AuthPage } from '../pages/AuthPage'

interface AuthGateProps {
  children: ReactNode
}

export function AuthGate({ children }: AuthGateProps) {
  const location = useLocation()
  const consumedToken = useRef('')
  const [bootstrapping, setBootstrapping] = useState(false)
  const [bootstrapError, setBootstrapError] = useState('')
  const query = useQuery({
    queryKey: ['auth-status'],
    queryFn: getAuthStatus,
    retry: false,
    staleTime: 0,
  })

  const token = new URLSearchParams(location.search).get('token') || ''

  useEffect(() => {
    const refresh = () => { void query.refetch() }
    window.addEventListener(AUTH_REQUIRED_EVENT, refresh)
    return () => window.removeEventListener(AUTH_REQUIRED_EVENT, refresh)
  }, [query.refetch])

  useEffect(() => {
    const status = query.data
    if (!status || !token || consumedToken.current === token) return
    consumedToken.current = token
    const next = new URLSearchParams(location.search)
    next.delete('token')
    const nextQuery = next.toString()
    window.history.replaceState(
      window.history.state,
      '',
      `${location.pathname}${nextQuery ? `?${nextQuery}` : ''}${location.hash}`,
    )
    if (!status.enabled || status.authenticated) return
    if (!status.methods.token) {
      setBootstrapError('当前实例没有启用访问令牌认证')
      return
    }
    setBootstrapping(true)
    setBootstrapError('')
    void bootstrapAuth(token)
      .then(() => query.refetch())
      .catch((error: unknown) => {
        setBootstrapError(error instanceof ApiError ? error.message : '访问令牌验证失败')
      })
      .finally(() => setBootstrapping(false))
  }, [location.hash, location.pathname, location.search, query, token])

  if (query.isLoading) {
    return <main className="auth-screen"><div className="auth-state"><LoaderCircle className="spin" size={22} /><strong>正在检查访问状态</strong></div></main>
  }
  if (query.isError || !query.data) {
    return <main className="auth-screen"><div className="auth-state"><strong>无法连接 Web 认证服务</strong><span>请确认后端已经启动，然后重试。</span><button onClick={() => void query.refetch()}><RefreshCw size={15} />重新连接</button></div></main>
  }
  if (query.data.authenticated) return children
  return (
    <AuthPage
      status={query.data}
      bootstrapping={bootstrapping}
      bootstrapError={bootstrapError}
      onAuthenticated={async () => { await query.refetch() }}
    />
  )
}
