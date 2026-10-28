import { ReactNode, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { LoaderCircle, RefreshCw } from 'lucide-react'
import { useLocation } from 'react-router-dom'
import {
  AUTH_REQUIRED_EVENT,
  getAuthStatus,
} from '../api/client'
import { AuthPage } from '../pages/AuthPage'
import { AuthLayout } from './AuthLayout'
import styles from '../pages/AuthPage.module.css'

interface AuthGateProps {
  children: ReactNode
}

export function AuthGate({ children }: AuthGateProps) {
  const location = useLocation()
  const query = useQuery({
    queryKey: ['auth-status'],
    queryFn: ({ signal }) => getAuthStatus(signal),
    retry: false,
    staleTime: 0,
  })

  useEffect(() => {
    const refresh = () => { void query.refetch() }
    window.addEventListener(AUTH_REQUIRED_EVENT, refresh)
    return () => window.removeEventListener(AUTH_REQUIRED_EVENT, refresh)
  }, [query.refetch])

  useEffect(() => {
    const current = new URLSearchParams(location.search)
    if (!current.has('token')) return
    current.delete('token')
    const queryString = current.toString()
    window.history.replaceState(
      window.history.state,
      '',
      `${location.pathname}${queryString ? `?${queryString}` : ''}${location.hash}`,
    )
  }, [location.hash, location.pathname, location.search])

  if (query.isLoading) {
    return <AuthLayout><div className={`${styles.card} ${styles.state}`} role="status"><LoaderCircle className="spin" size={22} aria-hidden="true" /><strong>正在检查访问状态</strong></div></AuthLayout>
  }
  if (query.isError || !query.data) {
    return <AuthLayout><div className={`${styles.card} ${styles.state}`} role="alert"><h1>无法连接 Web 认证服务</h1><p>请确认后端已经启动，然后重试。</p><button className={styles.submit} onClick={() => void query.refetch()}><RefreshCw size={15} aria-hidden="true" />重新连接</button></div></AuthLayout>
  }
  if (query.data.authenticated) return children
  return (
    <AuthPage
      key={query.data.stage || (query.data.methods.token ? 'token' : 'password')}
      status={query.data}
      onAuthenticated={async () => { await query.refetch() }}
    />
  )
}
