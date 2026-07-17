import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'

export const handlers = [
  http.get('/api/health', () => HttpResponse.json({ status: 'ok', service: 'kemo-agent-web', version: 1 })),
  http.get('/api/users', () => HttpResponse.json({ users: [{ name: 'kesepain' }] })),
  http.get('/api/users/kesepain/sessions', () => HttpResponse.json({
    user: 'kesepain', source: 'web', sessions: [{ session_id: 's1', window: 'w1', rounds: 2, updated_at: 'now' }],
  })),
  http.get('/api/users/kesepain/sessions/s1/history', () => HttpResponse.json({
    user: 'kesepain', source: 'web', session_id: 's1', messages: [],
  })),
  http.get('/api/users/kesepain/overview', () => HttpResponse.json({
    user: 'kesepain', session_id: '',
    context: { usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, estimated: false }, limit: 120000, percent: 0 },
    provider: { type: 'kemo', base_url: 'http://127.0.0.1:8741/v1', model: 'test-model', timeout: 120, stream: false, credential_source: 'environment', configured: true },
    counts: { sessions: 1, knowledge_documents: 3, enabled_tools: 2, enabled_agents: 1, active_tasks: 0 },
    active_plan: null, activities: [],
  })),
  http.get('/api/users/kesepain/tasks', () => HttpResponse.json({ user: 'kesepain', summary: { active_plans: 0, waiting_plans: 0, enabled_crons: 0, completed_plans: 0 }, plans: [], cron_tasks: [] })),
  http.get('/api/users/kesepain/knowledge', () => HttpResponse.json({ user: 'kesepain', enabled: true, retrieval: { max_items: 4, max_chars: 4000, minimum_score: 2, mode: 'file_index' }, summary: { documents: 3, user_documents: 1, shared_documents: 1, global_documents: 1 }, documents: [{ scope: 'user', relative_path: 'notes.md', title: '个人笔记', size: 120, updated_at: 1 }, { scope: 'shared', relative_path: 'team.md', title: '共享笔记', size: 90, updated_at: 1 }, { scope: 'global', relative_path: 'guide.md', title: '全局指南', size: 160, updated_at: 1 }], extensions: { kemo_graph: 'not_connected' } })),
  http.get('/api/users/kesepain/skills', () => HttpResponse.json({ user: 'kesepain', summary: { registered: 1, enabled: 1, user: 0, shared: 0, core: 1 }, tools: [{ name: 'clock', description: '读取当前时间', version: '1', enabled: true, source: 'plugins', layer: 'core', overrides: 0 }] })),
  http.get('/api/users/kesepain/sense', () => HttpResponse.json({ user: 'kesepain', registry_available: false, injection_enabled: false, core_available: true, core_files: 1, summary: { registered: 0, enabled: 0, user: 0, shared: 0, global: 0 }, sources: [], decisions: [] })),
  http.get('/api/users/kesepain/settings', () => HttpResponse.json({ user: 'kesepain', schema_version: 1, provider: { type: 'kemo', base_url: 'http://127.0.0.1:8741/v1', model: 'test-model', timeout: 120, stream: false, credential_source: 'environment', configured: true }, features: { tools: true, knowledge: true, memory_extraction: true, memory_injection: true, task_plan_auto_accept: false, cron: true, cron_auto_start: false }, limits: { context_tokens: 120000, compression_ratio: 0.6, task_plan_steps: 10, tool_iterations: 8, tool_timeout: 60, knowledge_items: 4, knowledge_chars: 4000, memory_items: 8, memory_chars: 2000 }, users: ['kesepain'] })),
]

export const server = setupServer(...handlers)
