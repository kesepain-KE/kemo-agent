import { AlertTriangle, RotateCw } from 'lucide-react'
import { isRouteErrorResponse, useRouteError } from 'react-router-dom'

export function RouteErrorPage() {
  const error = useRouteError()
  const message = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`.trim()
    : error instanceof Error
      ? error.message
      : '页面模块暂时无法加载'

  return <main className="route-error-page">
    <section className="route-error-card" role="alert">
      <span className="route-error-icon"><AlertTriangle size={24} /></span>
      <div>
        <strong>页面加载失败</strong>
        <p>{message}</p>
        <small>会话数据仍保存在后台。刷新页面后可继续使用。</small>
      </div>
      <button type="button" onClick={() => window.location.reload()}><RotateCw size={16} />重新加载</button>
    </section>
  </main>
}
