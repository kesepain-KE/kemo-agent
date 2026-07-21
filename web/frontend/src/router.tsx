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
      { path: 'memory', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./pages/MemoryPage')).MemoryPage }) },
      { path: 'agents', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./pages/AgentsPage')).AgentsPage }) },
      { path: 'skills', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./pages/SkillsPage')).SkillsPage }) },
      { path: 'sense', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./pages/SensePage')).SensePage }) },
      { path: 'expand', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./pages/ExpandPage')).ExpandPage }) },
      { path: 'files', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./pages/FilesPage')).FilesPage }) },
      { path: 'messages', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./pages/MessagesPage')).MessagesPage }) },
      { path: 'status', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./pages/RuntimeStatusPage')).RuntimeStatusPage }) },
      { path: 'runtime', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./pages/RuntimeModulesPage')).RuntimeModulesPage }) },
      { path: 'profile', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./pages/ProfilePage')).ProfilePage }) },
      { path: 'settings', hydrateFallbackElement: routeFallback, lazy: async () => ({ Component: (await import('./pages/SettingsPage')).SettingsPage }) },
      { path: '*', element: <Navigate to="/chat" replace /> },
    ],
  },
])
