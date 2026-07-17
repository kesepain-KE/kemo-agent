import { Navigate, createBrowserRouter } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { ChatPage } from './pages/ChatPage'
import { PendingModulePage } from './pages/PendingModulePage'

export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/chat" replace /> },
      { path: 'chat', element: <ChatPage /> },
      { path: 'tasks', element: <PendingModulePage kind="tasks" /> },
      { path: 'knowledge', element: <PendingModulePage kind="knowledge" /> },
      { path: 'skills', element: <PendingModulePage kind="skills" /> },
      { path: 'sense', element: <PendingModulePage kind="sense" /> },
      { path: 'settings', element: <PendingModulePage kind="settings" /> },
      { path: '*', element: <Navigate to="/chat" replace /> },
    ],
  },
])
