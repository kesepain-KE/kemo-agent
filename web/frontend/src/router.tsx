import { Navigate, createBrowserRouter, useLocation } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { AuthGate } from './components/AuthGate'
import { RouteErrorPage } from './components/RouteErrorPage'

const routeFallback = <div className="route-loading">正在加载模块…</div>

function ChatRedirect() {
  const { search, hash } = useLocation()
  return <Navigate to={{ pathname: '/chat', search, hash }} replace />
}

export const router = createBrowserRouter([
  {
    path: '/',
    element: <AuthGate><AppShell /></AuthGate>,
    errorElement: <RouteErrorPage />,
    children: [
      { index: true, element: <ChatRedirect /> },
      { path: 'chat', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./pages/ChatPage')).ChatPage }) },
      { path: 'tasks', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./reference/ReferencePage')).ReferencePage }) },
      { path: 'knowledge', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./reference/ReferencePage')).ReferencePage }) },
      { path: 'memory', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./reference/ReferencePage')).ReferencePage }) },
      { path: 'agents', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./reference/ReferencePage')).ReferencePage }) },
      { path: 'skills', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./reference/ReferencePage')).ReferencePage }) },
      { path: 'sense', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./reference/ReferencePage')).ReferencePage }) },
      { path: 'expand', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./reference/ReferencePage')).ReferencePage }) },
      { path: 'files', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./reference/FilesPage')).FilesPage }) },
      { path: 'messages', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./reference/ReferencePage')).ReferencePage }) },
      { path: 'status', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./reference/ReferencePage')).ReferencePage }) },
      { path: 'runtime', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./reference/ReferencePage')).ReferencePage }) },
      { path: 'profile', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./pages/ProfilePage')).ProfilePage }) },
      { path: 'settings', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./pages/SettingsPage')).SettingsPage }) },
      { path: '*', element: <ChatRedirect /> },
    ],
  },
])
