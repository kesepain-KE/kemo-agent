import { Navigate, createBrowserRouter } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { AuthGate } from './components/AuthGate'

const routeFallback = <div className="route-loading">正在加载模块…</div>

export const router = createBrowserRouter([
  {
    path: '/',
    element: <AuthGate><AppShell /></AuthGate>,
    children: [
      { index: true, element: <Navigate to="/chat" replace /> },
      { path: 'chat', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./pages/ChatPage')).ChatPage }) },
      { path: 'tasks', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./pages/TasksPage')).TasksPage }) },
      { path: 'knowledge', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./pages/KnowledgePage')).KnowledgePage }) },
      { path: 'skills', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./pages/SkillsPage')).SkillsPage }) },
      { path: 'sense', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./pages/SensePage')).SensePage }) },
      { path: 'settings', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./pages/SettingsPage')).SettingsPage }) },
      { path: '*', element: <Navigate to="/chat" replace /> },
    ],
  },
])
