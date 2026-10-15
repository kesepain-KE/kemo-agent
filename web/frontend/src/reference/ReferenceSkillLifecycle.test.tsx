import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../test/server'
import { ReferencePage } from './ReferencePage'
import type { ShellOutletContext } from '../components/appShellTypes'

describe('自动技能来源生命周期', () => {
  it('失效技能仍展示待复核和来源，并保留人工编辑删除而不自动启用', async () => {
    const name = 'agent_create/deployment'
    server.use(
      http.get('/api/users/alice/skills', () => HttpResponse.json({
        user: 'alice',
        catalog_summary: { total: 1, enabled: 0, builtin: 0, shared: 0, agent_generated: 1, user_created: 0 },
        prompt_summary: { registered: 1, active: 0 },
        items: [{
          id: `agent_generated:${name}`, category: 'agent_generated', name, title: '部署检查',
          description: '核对已有部署。', path: `users/alice/user_skills/${name}`,
          version: '', enabled: false, editable: true, toggleable: false, downloadable: false,
          memory_lifecycle: { status: 'stale', reason: '来源记忆已修正，请人工复核',
            sources: [{ fragment_id: 7, filename: '部署规则.md', content_hash: 'digest' }] },
        }],
      })),
      http.get('/api/users/alice/skills/agent_generated/document', () => HttpResponse.json({
        name, content: '# 部署检查\n\n保留供人工复核的原技能。', editable: true,
      })),
    )
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const context = { user: 'alice', sessionId: '', chatRunning: false } as ShellOutletContext
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/skills']}>
      <Routes><Route element={<Outlet context={context} />}><Route path="*" element={<ReferencePage />} /></Route></Routes>
    </MemoryRouter></QueryClientProvider>)
    expect((await screen.findAllByText('来源已失效 · 待复核')).length).toBeGreaterThan(0)
    expect(screen.getByText(/部署规则\.md/)).toHaveTextContent('来源记忆已修正，请人工复核')
    expect(await screen.findByRole('heading', { name: '部署检查' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '删除' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '编辑' })).toBeEnabled()
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
  })
})
