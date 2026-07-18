import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'

const sourcePolicy = {
  knowledge: { enabled: true, effective_scopes: ['user', 'shared', 'global'] },
  skills: { shared: { mode: 'all', names: [] }, user: { mode: 'all', names: [] } },
  expand: { global: { mode: 'all', names: [] }, shared: { mode: 'all', names: [] } },
  perception: { global: { mode: 'all', names: [] } },
  kemo_graph: { requested: false, connected: false, effective: false, status: 'disabled' },
}

export const handlers = [
  http.get('/api/health', () => HttpResponse.json({ status: 'ok', service: 'kemo-agent-web', version: 1 })),
  http.get('/api/auth/status', () => HttpResponse.json({ enabled: false, authenticated: true, methods: { token: false, password: false }, session_cookie_configured: false })),
  http.post('/api/auth/logout', () => HttpResponse.json({ authenticated: false })),
  http.post('/api/runs/:runId/guidance', ({ params }) => HttpResponse.json({ run_id: params.runId, status: 'queued', queued: 1 })),
  http.get('/api/users', () => HttpResponse.json({ users: [{ name: 'kesepain' }] })),
  http.get('/api/users/kesepain/sessions', () => HttpResponse.json({
    user: 'kesepain', source: 'web', sessions: [{ session_id: 's1', window: 'w1', title: '', rounds: 2, updated_at: 'now' }],
  })),
  http.get('/api/users/kesepain/sessions/s1/history', () => HttpResponse.json({
    user: 'kesepain', source: 'web', session_id: 's1', messages: [], round_metrics: [], round_traces: [],
  })),
  http.get('/api/users/kesepain/overview', () => HttpResponse.json({
    user: 'kesepain', session_id: '',
    context: { usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, estimated: false }, limit: 120000, percent: 0, rounds: 0, round_limit: 30 },
    provider: { type: 'kemo', base_url: 'http://127.0.0.1:8741/v1', model: 'test-model', timeout: 120, stream: false, credential_source: 'environment', configured: true },
    counts: { sessions: 1, knowledge_documents: 3, enabled_tools: 2, enabled_agents: 1, active_tasks: 0 },
    agents: [{ name: 'context_manage', description: '上下文摘要', enabled: true, source: 'builtin', execution: 'sync', model_profile: 'default', exposure: 'internal' }],
    summary_cache: { exists: false, covered_rounds: [], created_at: '', window: '' },
    runtime_host: { state: 'unmanaged', components: {} },
    active_plan: null, activities: [],
  })),
  http.get('/api/users/kesepain/tasks', () => HttpResponse.json({ user: 'kesepain', summary: { active_plans: 0, waiting_plans: 0, enabled_crons: 0, completed_plans: 0 }, plans: [], cron_tasks: [] })),
  http.get('/api/users/kesepain/knowledge', () => HttpResponse.json({ user: 'kesepain', enabled: true, retrieval: { max_items: 4, max_chars: 4000, minimum_score: 2, mode: 'file_index' }, summary: { documents: 3, user_documents: 1, shared_documents: 1, global_documents: 1 }, documents: [{ scope: 'user', relative_path: 'notes.md', title: '个人笔记', size: 120, updated_at: 1, active_for_main_agent: true }, { scope: 'shared', relative_path: 'team.md', title: '共享笔记', size: 90, updated_at: 1, active_for_main_agent: true }, { scope: 'global', relative_path: 'guide.md', title: '全局指南', size: 160, updated_at: 1, active_for_main_agent: true }], extensions: { kemo_graph: 'disabled' }, source_policy: sourcePolicy })),
  http.get('/api/users/kesepain/skills', () => HttpResponse.json({ user: 'kesepain', summary: { registered: 1, enabled: 1, user: 0, shared: 0, core: 1 }, tools: [{ name: 'clock', description: '读取当前时间', version: '1', enabled: true, source: 'plugins', layer: 'core', overrides: 0 }], prompt_summary: { registered: 1, active: 1, user: 0, shared: 1 }, prompt_skills: [{ name: 'example', title: 'example', description: '示例技能', scope: 'shared', active_for_main_agent: true }], source_policy: sourcePolicy })),
  http.get('/api/users/kesepain/sense', () => HttpResponse.json({ user: 'kesepain', registry_available: true, injection_enabled: true, core_available: true, core_files: 1, summary: { registered: 1, enabled: 1, user: 0, shared: 0, global: 1 }, sources: [{ id: 'runtime', name: 'runtime', description: '1 个可注入 Markdown 文件', layer: 'global', enabled: true, active_for_main_agent: true, status: 'active', files: 1 }], decisions: [], source_policy: sourcePolicy })),
  http.get('/api/users/kesepain/settings', () => HttpResponse.json({ user: 'kesepain', schema_version: 1, provider: { type: 'kemo', base_url: 'http://127.0.0.1:8741/v1', model: 'test-model', timeout: 120, stream: false, credential_source: 'environment', configured: true }, features: { tools: true, knowledge: true, memory_extraction: true, memory_injection: true, task_plan_auto_accept: false, cron: true, cron_auto_start: false }, limits: { context_rounds: 30, context_tokens: 120000, compression_ratio: 0.6, task_plan_steps: 10, tool_iterations: 8, tool_timeout: 60, knowledge_items: 4, knowledge_chars: 4000, memory_items: 8, memory_chars: 2000 }, users: ['kesepain'], authentication: { enabled: false, token_enabled: false, password_enabled: false, session_cookie_configured: false }, source_policy: sourcePolicy, provenance: { 'provider.model': 'user', 'tools.enabled': 'global' } })),
  http.get('/api/users/kesepain/config/full', () => HttpResponse.json({ user: 'kesepain', config: { schema_version: 1, provider: { model: 'test-model' } }, etag: 'mock-etag', redacted_paths: [], write_enabled: false })),
  http.get('/api/users/kesepain/prompt/sections', () => HttpResponse.json({ user: 'kesepain', total_chars: 120, sections: [{ name: 'user_soul', status: 'injected', original_items: 1, injected_items: 1, original_chars: 20, injected_chars: 20, truncated: false, source_files: ['users/kesepain/user_soul.md'] }], source_policy: sourcePolicy, source_selection: {}, expand: { global: { mode: 'all', discovered: [], selected: [], filtered: [], unmatched: [] } } })),
  http.get('/api/users/kesepain/memory/summary', () => HttpResponse.json({ user: 'kesepain', summary: { total: 0, seven_days: 0, one_month: 0, half_year: 0, permanent: 0 }, items: [] })),
]

export const server = setupServer(...handlers)
