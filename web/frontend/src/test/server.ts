import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'

const sourcePolicy = {
  knowledge: { enabled: true, effective_scopes: ['user', 'shared', 'global'] },
  plugins: { mode: 'all', names: [] },
  skills: { shared: { mode: 'all', names: [] }, user: { mode: 'all', names: [] } },
  expand: { global: { mode: 'all', names: [] }, shared: { mode: 'all', names: [] } },
  perception: { global: { mode: 'all', names: [] } },
  kemo_graph: { requested: false, connected: false, effective: false, status: 'disabled', replacement_active: false, replaces_knowledge: false, replaces_memory: false },
}

export const handlers = [
  http.post('/api/system/restart', async ({ request }) => {
    const body = await request.json() as { port: number }
    return HttpResponse.json({ ok: true, port: body.port, helper_pid: 4321 })
  }),
  http.get('/api/health', () => HttpResponse.json({ status: 'ok', service: 'kemo-agent-web', version: 1 })),
  http.get('/api/auth/status', () => HttpResponse.json({ enabled: false, authenticated: true, methods: { token: false, password: false }, session_cookie_configured: false })),
  http.post('/api/auth/logout', () => HttpResponse.json({ authenticated: false })),
  http.post('/api/runs/:runId/guidance', ({ params }) => HttpResponse.json({ run_id: params.runId, status: 'queued', queued: 1 })),
  http.post('/api/runs/:runId/cancel', ({ params }) => HttpResponse.json({ run_id: params.runId, user: 'kesepain', session_id: 's1', status: 'stopping' })),
  http.get('/api/users', () => HttpResponse.json({ users: [{ name: 'kesepain' }, { name: 'reviewer' }] })),
  http.get('/api/users/:user/preferences', ({ params }) => HttpResponse.json({ user: params.user, appearance: { theme: 'light', font_size: 'medium' } })),
  http.patch('/api/users/:user/preferences', async ({ params, request }) => HttpResponse.json({ user: params.user, appearance: await request.json() })),
  http.get('/api/users/kesepain/sessions', () => HttpResponse.json({
    user: 'kesepain', source: 'web', sessions: [{ session_id: 's1', window: 'w1', title: '', rounds: 2, updated_at: 'now' }],
  })),
  http.get('/api/users/kesepain/sessions/active', () => HttpResponse.json({
    user: 'kesepain', active_key: 'interactive:kesepain', created: false,
    session: { session_id: 's1', conversation_id: 'legacy_s1', window: 'w1', title: '', rounds: 2, updated_at: 'now', state: 'open', run_state: 'idle', chain: 'interactive' },
  })),
  http.post('/api/users/kesepain/sessions', () => HttpResponse.json({
    user: 'kesepain', active_key: 'interactive:kesepain', created: true,
    session: { session_id: 'conv_new_session', conversation_id: 'conv_new_session', window: '', title: '', rounds: 0, updated_at: 'now', state: 'open', run_state: 'idle', chain: 'interactive' },
  })),
  http.post('/api/users/kesepain/sessions/:sessionId/lease', async ({ params, request }) => {
    const body = await request.json() as { client_id: string }
    return HttpResponse.json({
      user: 'kesepain', source: 'web', session_id: params.sessionId,
      client_id: body.client_id, active_clients: 1, leased: true,
    })
  }),
  http.post('/api/users/kesepain/sessions/:sessionId/lease/release', async ({ params, request }) => {
    const body = await request.json() as { client_id: string }
    return HttpResponse.json({
      user: 'kesepain', source: 'web', session_id: params.sessionId,
      client_id: body.client_id, active_clients: 0, released: true,
    })
  }),
  http.post('/api/users/kesepain/sessions/:sessionId/close', ({ params }) => HttpResponse.json({
    user: 'kesepain', source: 'web', session_id: params.sessionId, closed: true,
    session: { session_id: params.sessionId, window: 'w1', title: '', rounds: 2, updated_at: 'now', state: 'closed', run_state: 'idle', chain: 'interactive' },
  })),
  http.get('/api/users/kesepain/sessions/s1/history', () => HttpResponse.json({
    user: 'kesepain', source: 'web', session_id: 's1', messages: [], round_metrics: [], round_traces: [],
  })),
  http.post('/api/users/kesepain/sessions/:sessionId/compress', ({ params }) => HttpResponse.json({
    user: 'kesepain', source: 'web', session_id: params.sessionId, requested: true,
    compressed: true, rounds_removed: 2, summary_cache_exists: true,
    context: { rounds_removed: 2 },
    memory: {
      status: 'completed', user: 'kesepain', source: 'web', session_id: params.sessionId,
      round: 2, candidates: 1, extraction: { status: 'completed', candidate_count: 1 },
      retry_pending: false,
    },
  })),
  http.post('/api/users/kesepain/sessions/:sessionId/extract-memory', ({ params }) => HttpResponse.json({
    status: 'completed', user: 'kesepain', source: 'web', session_id: params.sessionId,
    round: 2, candidates: 1, extraction: { status: 'completed', candidate_count: 1 },
  })),
  http.post('/api/users/kesepain/sessions/:sessionId/undo-last-round', async ({ params, request }) => {
    const body = await request.json() as { expected_round: number; prompt: string }
    return HttpResponse.json({
      user: 'kesepain', source: 'web', session_id: params.sessionId, found: true,
      rolled_back: true, round: body.expected_round,
      remaining_rounds: Math.max(0, body.expected_round - 1), prompt: body.prompt,
      content: [{ type: 'text', text: body.prompt }],
    })
  }),
  http.delete('/api/users/kesepain/sessions/:sessionId', ({ params }) => HttpResponse.json({
    user: 'kesepain', source: 'web', session_id: params.sessionId, deleted: true,
  })),
  http.get('/api/users/kesepain/overview', () => HttpResponse.json({
    user: 'kesepain', session_id: '',
    context: { usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, estimated: false }, limit: 120000, percent: 0, rounds: 0, round_limit: 30 },
    provider: { type: 'kemo', base_url: 'http://127.0.0.1:8741/v1', model: 'test-model', reasoning_effort: 'medium', timeout: 120, stream: false, credential_source: 'environment', configured: true },
    counts: { sessions: 1, knowledge_documents: 3, enabled_tools: 2, enabled_agents: 1, active_tasks: 0 },
    context_window: {
      tokens: { system_prompt_tokens: 18200, tool_schema_tokens: 2400, conversation_tokens: 1800, summary_tokens: 0, other_tokens: 0, context_tokens: 4200, total_tokens: 22400, capacity_tokens: 120000, percent: 18.67, source: 'runtime_recalculated', measurement: 'estimated', captured_at: '2026-07-22T00:00:00Z' },
      conversation: { foreground_rounds: 8, archived_rounds: 36, total_tool_calls: 19, session_total_rounds: 44, session_tool_calls: 19 },
      tasks: { active_plans: 2, waiting_crons: 3 },
      capabilities: { tools_enabled: 12, tools_disabled: 2, agents_enabled: 4 },
      knowledge: { enabled: 9, disabled: 2, graph_enabled: true },
      messages: { connected: 1 },
      integrations: { expands: 2, senses: 1 },
    },
    context_snapshot: { available: true, source: 'runtime_recalculated', measurement: 'estimated', captured_at: '2026-07-22T00:00:00Z', system_prompt_tokens: 18200, tool_schema_tokens: 2400, conversation_tokens: 1800, summary_tokens: 0, other_tokens: 0, total_tokens: 22400, capacity_tokens: 120000, percent: 18.67, foreground_rounds: 8 },
    session_context_stats: { selected: false, foreground_rounds: 8, background_archived_rounds: 36, session_total_rounds: 44, session_tool_calls: 19 },
    agents: [{ name: 'context_manage', description: '上下文摘要', enabled: true, source: 'builtin', execution: 'sync', model_profile: 'default', exposure: 'internal' }],
    summary_cache: { exists: false, covered_rounds: [], created_at: '', window: '' },
    runtime_host: { state: 'unmanaged', components: {} },
    active_plan: null, activities: [],
  })),
  http.get('/api/users/kesepain/runtime/status', ({ request }) => HttpResponse.json({
    schema_version: 1,
    generated_at: '2026-07-21T14:30:00+08:00',
    user: 'kesepain',
    session_id: new URL(request.url).searchParams.get('session_id') || '',
    api: { type: 'chat', base_url: 'http://127.0.0.1:8741', model: 'test-model', thinking_effort: 'medium', configured: true, credential_source: 'environment' },
    context: { selected: false, used_tokens: 55900, max_tokens: 1000000, percent: 5.59, rounds: 12, round_limit: 80, compression_threshold: 300000, source: 'runtime_estimate' },
    tokens: { date: '2026-07-21', timezone: 'Asia/Shanghai', sent_tokens: 168732, received_tokens: 159113, total_tokens: 327845, cached_tokens: 145329, cache_rate: 45.2, request_count: 1248, estimated: false, trend: [8, 10, 13, 11, 17, 21, 13, 18, 29, 52] },
    prompt: {
      content: '[user_soul]\n# 用户人格\n\n保持清晰。\n\n[global_soul]\n# 全局规则\n\n安全第一。',
      total_chars: 58,
      estimated_tokens: 24,
      components: [
        { id: 'user_soul', name: 'user_soul', state: 'injected', chars: 20, tokens: 8, source_files: ['users/kesepain/user_soul.md'], injected_items: 1, original_items: 1 },
        { id: 'perception', name: 'perception', state: 'truncated', chars: 38, tokens: 16, source_files: ['global_sense/runtime/sense.md'], injected_items: 1, original_items: 2 },
      ],
    },
    components: {
      sense: [{ id: 'runtime', name: '运行时感知', health: 'healthy', state: 'injected', description: '', updated_at: 1 }],
      expand: [{ id: 'global:bridge', name: '全局桥接', scope: 'global', health: 'warning', state: 'loaded', description: '最近更新偏慢', updated_at: 1 }],
    },
    memory: { updated_today: 1, upgraded_today: 1, upgrade_tracking: 'system_cron_log', updates: [{ id: 'one_month:project.md', filename: 'project.md', tier: 'one_month', weight: 0, updated_at: '2026-07-21T12:30:00+08:00', upgraded: true, from_tier: 'seven_days', to_tier: 'one_month' }] },
    tasks: { summary: { active_plans: 1, waiting_plans: 1, enabled_crons: 1, completed_plans: 0 }, items: [{ id: 'plan-1', kind: 'plan', title: '运行状态重构', status: 'running', next_run_at: '', trigger: '进度 2 / 4', updated_at: '2026-07-21T13:00:00+08:00' }] },
    system_cron: { tracking: 'execution_log', tasks: [], executions: [{ id: 'memory_promotion:1', task_id: 'memory_promotion', title: '记忆碎片到期晋升检查', executed_at: '2026-07-21T14:00:00+08:00', status: 'success', duration_ms: 1230, result: {}, error: null, source: 'execution_log' }] },
    message_routes: { summary: { total_bindings: 1, total_transports: 1, running_transports: 1, stopped_transports: 0, error_transports: 0, connected_transports: 1, temporary_files: 0, today_logs: 2 }, routes: [{ id: 'onebot', name: 'OneBot 正向 WebSocket', platform: 'onebot', health: 'healthy', state: 'running', latency_ms: 42, last_check: '2026-07-21T14:20:00+08:00', description: 'healthy' }] },
    runtime_host: { state: 'running', components: { cron: { name: 'cron', kind: 'scheduler', state: 'running' } } },
    congestion: {
      provider: { active_requests: 2, max_requests: 10, available_requests: 8, waiting_estimate: 0 },
      web: { active_chats: 1, max_chats: 3, pending_chats: 0, max_pending: 5 },
      message_router: { active_workers: 1, max_workers: 8, queued_messages: 0, max_queued: 20 },
    },
  })),
  http.get('/api/users/kesepain/tasks', () => HttpResponse.json({ user: 'kesepain', summary: { active_plans: 0, waiting_plans: 0, enabled_crons: 1, completed_plans: 0 }, plans: [], cron_tasks: [{ task_id: 'daily-check', title: '每日检查', user_defined: true, status: 'enabled', type: 'daily', time: '18:00', next_run_at: '2026-07-20T18:00:00+08:00', latest_run_at: '', created_at: '2026-07-20T12:00:00+08:00', last_state: 'never' }], executions: [] })),
  http.post('/api/users/kesepain/tasks/plans/:planId/actions/:action', ({ params }) => HttpResponse.json({ user: 'kesepain', action: params.action, updated: true, plan: { plan_id: params.planId, status: params.action === 'cancel' ? 'cancelled' : 'paused', revision: 2, title: '测试计划', description: '', auto_accept: false, reminder: '', source: 'web', session_id: 's1', current_step: 'step_1', created_at: '', updated_at: '', progress: { completed: 0, total: 1, percent: 0 }, steps: [{ step_id: 'step_1', title: '执行', description: '', status: 'pending', depends_on: [], critical: true, tool_name: '', started_at: '', finished_at: '' }] } })),
  http.get('/api/users/kesepain/knowledge', () => HttpResponse.json({ user: 'kesepain', enabled: true, retrieval: { mode: 'index_only', full_index: true }, summary: { documents: 3, user_documents: 1, shared_documents: 1, global_documents: 1 }, documents: [{ scope: 'user', relative_path: 'notes.md', title: '个人笔记', size: 120, updated_at: 1, active_for_main_agent: true }, { scope: 'shared', relative_path: 'team.md', title: '共享笔记', size: 90, updated_at: 1, active_for_main_agent: true }, { scope: 'global', relative_path: 'guide.md', title: '全局指南', size: 160, updated_at: 1, active_for_main_agent: true }], extensions: { kemo_graph: 'disabled' }, source_policy: sourcePolicy })),
  http.get('/api/users/kesepain/knowledge/:scope/document', ({ params }) => HttpResponse.json({ user: 'kesepain', scope: params.scope, relative_path: params.scope === 'shared' ? 'team.md' : params.scope === 'global' ? 'guide.md' : 'notes.md', content: '# 知识正文\n\n测试内容', size: 24, updated_at: 1 })),
  http.put('/api/users/kesepain/knowledge/:scope/document', async ({ params, request }) => { const body = await request.json() as { content?: string }; return HttpResponse.json({ user: 'kesepain', scope: params.scope, relative_path: new URL(request.url).searchParams.get('path') || 'notes.md', size: String(body.content || '').length, updated: true, index_refresh: 'next_request' }) }),
  http.delete('/api/users/kesepain/knowledge/:scope/document', ({ params, request }) => HttpResponse.json({ user: 'kesepain', scope: params.scope, relative_path: new URL(request.url).searchParams.get('path') || 'notes.md', deleted: true })),
  http.get('/api/users/kesepain/skills', () => HttpResponse.json({
    user: 'kesepain',
    summary: { registered: 1, enabled: 1, user: 0, shared: 0, core: 1 },
    tools: [{ name: 'clock', description: '读取当前时间', version: '1', enabled: true, source: 'plugins', layer: 'core', overrides: 0 }],
    catalog_summary: { total: 4, enabled: 4, builtin: 1, shared: 1, agent_generated: 1, user_created: 1 },
    items: [
      { id: 'builtin:clock', name: 'clock', title: 'clock', description: '读取当前时间', category: 'builtin', version: '1', enabled: true, editable: false, toggleable: true, downloadable: true, path: 'plugins/clock' },
      { id: 'shared:example', name: 'example', title: '示例技能', description: '共享示例技能', category: 'shared', version: '', enabled: true, editable: false, toggleable: true, downloadable: true, path: 'shared_skills/example' },
      { id: 'agent_generated:agent_create/generated', name: 'agent_create/generated', title: '智能体生成技能', description: '由智能体创建', category: 'agent_generated', version: '', enabled: true, editable: true, toggleable: false, downloadable: false, path: 'users/kesepain/user_skills/agent_create/generated' },
      { id: 'user_created:user_create/manual', name: 'user_create/manual', title: '用户自建技能', description: '由用户上传', category: 'user_created', version: '', enabled: true, editable: true, toggleable: false, downloadable: false, path: 'users/kesepain/user_skills/user_create/manual' },
    ],
    prompt_summary: { registered: 3, active: 3, user: 2, shared: 1 },
    prompt_skills: [{ name: 'example', title: '示例技能', description: '共享示例技能', scope: 'shared', category: 'shared', path: 'shared_skills/example', active_for_main_agent: true }],
    source_policy: sourcePolicy,
  })),
  http.get('/api/users/kesepain/skills/:category/document', ({ params, request }) => {
    const name = new URL(request.url).searchParams.get('name') || ''
    return HttpResponse.json({ user: 'kesepain', category: params.category, name, path: `${name}/SKILL.md`, content: `# ${name.split('/').at(-1)}\n\n技能正文`, size: 24, updated_at: 1, editable: params.category === 'agent_generated' || params.category === 'user_created' })
  }),
  http.patch('/api/users/kesepain/skills/:category/enabled', async ({ request }) => {
    const body = await request.json() as { enabled: boolean }
    return HttpResponse.json({ enabled: body.enabled })
  }),
  http.put('/api/users/kesepain/skills/:category/document', async ({ params, request }) => {
    const name = new URL(request.url).searchParams.get('name') || ''
    const body = await request.json() as { content: string }
    return HttpResponse.json({ user: 'kesepain', category: params.category, name, path: `${name}/SKILL.md`, content: body.content, size: body.content.length, updated_at: 2, editable: true })
  }),
  http.delete('/api/users/kesepain/skills/:category', ({ params, request }) => HttpResponse.json({ user: 'kesepain', category: params.category, name: new URL(request.url).searchParams.get('name'), path: 'deleted', deleted: true })),
  http.get('/api/users/kesepain/sense', () => HttpResponse.json({
    user: 'kesepain',
    registry_available: true,
    injection_enabled: true,
    core_available: true,
    core_files: 1,
    summary: { registered: 1, enabled: 1, user: 0, shared: 0, global: 1, healthy: 1, unhealthy: 0, invalid: 0, registered_data: 1, injected_data: 1 },
    sources: [{ id: 'runtime', name: 'runtime', display_name: '运行时感知', description: '标准数据文件：sense.md', layer: 'global', enabled: true, whitelisted: true, active_for_main_agent: true, status: 'active', data_md: 'sense.md', recent_update: '2026-07-19 12:00:00', health: '正常', valid: true, error: '', start_update: 'data_update.py', files: 1, registered_items: 1, injected_items: 1, data_items: ['sense.md'], value_preview: 'CPU 使用率 23%', collected_markdown: '# 运行时采集\n\n- CPU 使用率：23%', injected_markdown: '[runtime]\n# 运行时采集\n\n- CPU 使用率：23%', injected_tokens: 12, update_interval: '', updated_at: 1 }],
    injection: { enabled: true, registered_items: 1, injected_items: 1, original_chars: 23, injected_chars: 23, estimated_tokens: 12, truncated: false, preview: '[runtime]\nruntime ready', preview_truncated: false, content: '[runtime]\n# 运行时采集\n\n- CPU 使用率：23%', source_files: ['global_sense/runtime/sense.md'], prompt_section: 'perception', prompt_position: 'System Prompt / Global Sense' },
    decisions: [],
    source_policy: sourcePolicy,
  })),
  http.post('/api/users/kesepain/sense/:module/refresh', () => HttpResponse.json({ updated: true })),
  http.patch('/api/users/kesepain/sense/:module/enabled', async ({ request }) => {
    const body = await request.json() as { enabled: boolean }
    return HttpResponse.json({ enabled: body.enabled })
  }),
  http.delete('/api/users/kesepain/sense/:module', ({ params }) => HttpResponse.json({ module: params.module, deleted: true })),
  http.get('/api/users/kesepain/settings', () => HttpResponse.json({ user: 'kesepain', schema_version: 1, provider: { type: 'kemo', base_url: 'http://127.0.0.1:8741/v1', model: 'test-model', reasoning_effort: 'medium', timeout: 120, stream: false, credential_source: 'environment', configured: true }, features: { tools: true, knowledge: true, history_read: true, memory_injection: true, task_plan_auto_accept: false, cron: true, background_scheduler: true }, limits: { context_rounds: 80, context_tokens: 1000000, compression_ratio: 0.3, task_plan_steps: 20, tool_iterations: 8, tool_timeout: 240, memory_items: 600, memory_chars: 2000 }, users: ['kesepain', 'reviewer'], authentication: { enabled: false, token_enabled: false, password_enabled: false, session_cookie_configured: false }, source_policy: sourcePolicy, provenance: { 'provider.model': 'user', 'tools.enabled': 'global' } })),
  http.get('/api/users/kesepain/config/full', () => HttpResponse.json({
    user: 'kesepain',
    config: {
      schema_version: 1,
      provider: { type: 'kemo', model: 'test-model', base_url: 'http://127.0.0.1:8741', api_key: '***', stream: false, reasoning_effort: 'medium' },
      agent_models: { default: 'agent-default', cheap: 'summary-model', reasoning: 'agent-reasoning' },
      multimodal_models: { vision: 'vision-model', image_generation: '', image_edit: '', audio_transcription: '', speech_generation: '', speech_to_speech: '', video_generation: '' },
      task_plan: { auto_accept: false },
      knowledge: { use_shared: true, use_global: true },
      kemo_graph: { kemo_graph_global_knowledge: false, kemo_graph_shared_knowledge: false, kemo_graph_user_knowledge: false, kemo_graph_temporary_memory: false },
      skills: { shared_whitelist: [] },
      expand: { shared_whitelist: [], global_whitelist: [] },
      perception: { global_whitelist: [] },
      plugins: { whitelist: [] },
    },
    redacted_paths: ['provider.api_key'],
  })),
  http.patch('/api/users/:user/config', ({ params }) => HttpResponse.json({ user: params.user, config: {}, redacted_paths: ['provider.api_key'], updated: true })),
  http.get('/api/global-config', () => HttpResponse.json({
    scope: 'global',
    config: {
      schema_version: 1,
      agents: { token_limit: 1000000, token_compression_ratio: 0.3, max_rounds: 80, rounds_after_compression: 20 },
      memory: { temporary_injection_limits: { seven_days: 100, one_month: 200, half_year: 300 } },
      kemo_graph: { kemo_graph_global_knowledge: false, kemo_graph_shared_knowledge: false, kemo_graph_user_knowledge: false, kemo_graph_temporary_memory: false },
      tools: { timeout: 240, max_iterations: 80, consecutive_identical_call_limit: 8 },
      history: { consecutive_tool_fail_limit: 5 },
      task_plan: { max_steps: 20 },
      provider_runtime: { max_concurrent_requests: 10, request_semaphore_timeout: 300 },
      web: { max_concurrent_chats: 3, max_pending_chats: 5, pending_chat_timeout: 30 },
      message: { max_workers: 8, max_queued_messages: 20 },
      cron: { poll_interval: 30, avoid_congestion: true, congestion_threshold_ratio: 0.2 },
      agent_runtime: { default_timeout: 600, queue_maxsize: 50 },
    },
    redacted_paths: [],
  })),
  http.patch('/api/global-config', () => HttpResponse.json({ scope: 'global', config: {}, redacted_paths: [], updated: true })),
  http.get('/api/users/kesepain/prompt/sections', () => HttpResponse.json({ user: 'kesepain', total_chars: 120, sections: [{ name: 'user_soul', status: 'injected', original_items: 1, injected_items: 1, original_chars: 20, injected_chars: 20, truncated: false, source_files: ['users/kesepain/user_soul.md'] }], source_policy: sourcePolicy, source_selection: {}, expand: { global: { mode: 'all', discovered: [], selected: [], filtered: [], invalid: [], unmatched: [], health_status: {} }, shared: { mode: 'all', discovered: [], selected: [], filtered: [], invalid: [], unmatched: [], health_status: {} }, user: { mode: 'all', discovered: [], selected: [], filtered: [], invalid: [], unmatched: [], health_status: {} } } })),
  http.get('/api/users/kesepain/memory/summary', () => HttpResponse.json({ user: 'kesepain', summary: { total: 0, seven_days: 0, one_month: 0, half_year: 0, permanent: 0 }, items: [] })),
  http.get('/api/users/kesepain/memory/important', () => HttpResponse.json({ user: 'kesepain', path: 'users/kesepain/memory_temporary_important.md', content: '', size: 0 })),
  http.get('/api/users/kesepain/files/:scope', ({ params }) => HttpResponse.json({
    user: 'kesepain',
    scope: params.scope,
    root: `users/kesepain/${params.scope}`,
    summary: { total_files: 3, total_dirs: 2, total_size: 3200 },
    tree: [
      { type: 'directory', name: 'screenshots', relative_path: 'screenshots', children: [
        { type: 'directory', name: 'release', relative_path: 'screenshots/release', children: [{ type: 'file', name: 'final-shot.png', relative_path: 'screenshots/release/final-shot.png', size: 2048, updated_at: 2, extension: '.png' }] },
        { type: 'file', name: 'shot.png', relative_path: 'screenshots/shot.png', size: 1024, updated_at: 1, extension: '.png' },
      ] },
      { type: 'file', name: 'readme.txt', relative_path: 'readme.txt', size: 128, updated_at: 1, extension: '.txt' },
    ],
  })),
  http.post('/api/users/kesepain/files/:scope/upload', ({ request, params }) => HttpResponse.json({ user: 'kesepain', scope: params.scope, path: new URL(request.url).searchParams.get('path'), size: 1, updated: true })),
  http.patch('/api/users/kesepain/files/:scope/move', ({ request, params }) => {
    const url = new URL(request.url)
    return HttpResponse.json({ user: 'kesepain', scope: params.scope, path: url.searchParams.get('path'), new_path: url.searchParams.get('new_path'), moved: true })
  }),
  http.post('/api/users/kesepain/files/:scope/delete-many', async ({ request, params }) => {
    const body = await request.json() as { paths: string[] }
    return HttpResponse.json({ user: 'kesepain', scope: params.scope, deleted_paths: body.paths, deleted_count: body.paths.length })
  }),
  http.delete('/api/users/kesepain/files/:scope/all', ({ params }) => HttpResponse.json({ user: 'kesepain', scope: params.scope, deleted_paths: ['readme.txt', 'screenshots/shot.png', 'screenshots/release/final-shot.png'], deleted_count: 3 })),
  http.delete('/api/users/kesepain/files/:scope', ({ request, params }) => HttpResponse.json({ user: 'kesepain', scope: params.scope, path: new URL(request.url).searchParams.get('path'), deleted: true })),
  http.get('/api/tmp', () => HttpResponse.json({ root: 'tmp', summary: { total_files: 1, total_dirs: 0, total_size: 64 }, tree: [{ type: 'file', name: 'cache.tmp', relative_path: 'cache.tmp', size: 64, updated_at: 1, extension: '.tmp' }] })),
  http.delete('/api/tmp', ({ request }) => HttpResponse.json({ path: new URL(request.url).searchParams.get('path'), deleted: true })),
  http.post('/api/tmp/delete-many', async ({ request }) => {
    const body = await request.json() as { paths: string[] }
    return HttpResponse.json({ deleted_paths: body.paths, deleted_count: body.paths.length })
  }),
  http.delete('/api/tmp/all', () => HttpResponse.json({ deleted_paths: ['cache.tmp'], deleted_count: 1 })),
  http.post('/api/users/kesepain/avatar', () => HttpResponse.json({ user: 'kesepain', avatar_path: 'users/kesepain/avatar/avatar.png', size: 68, format: 'image/png' })),
  http.get('/api/users/kesepain/agents', () => HttpResponse.json({
    user: 'kesepain', summary: { total: 2, enabled: 2, global: 1, user: 1 },
    agents: [
      { name: 'context_manage', version: '1.2.0', description: '上下文管理子代理', enabled: true, source: 'global', trigger: '上下文接近上限时', rules: '# context_manage\n\n压缩并保留关键事实。', executor: 'executor.py:execute', execution: 'sync', model_profile: 'cheap', exposure: 'tool', root: 'agents/context_manage', files: [{ name: 'AGENT.md', relative_path: 'AGENT.md', size: 128, updated_at: 1 }] },
      { name: 'custom_agent', version: '1.0.0', description: '用户自定义子代理', enabled: true, source: 'user', trigger: '用户明确指定时', rules: '# custom_agent\n\n按用户规则处理输入。', executor: 'builtin:llm', execution: 'sync', model_profile: 'default', exposure: 'tool', root: 'users/kesepain/agents/custom_agent', files: [{ name: 'AGENT.md', relative_path: 'AGENT.md', size: 96, updated_at: 1 }] },
    ],
  })),
  http.delete('/api/users/kesepain/agents/:agent', ({ params }) => HttpResponse.json({ user: 'kesepain', name: params.agent, path: `users/kesepain/agents/${params.agent}`, deleted: true })),
  http.get('/api/users/kesepain/message/status', () => HttpResponse.json({
    user: 'kesepain',
    bindings: [{ platform: 'onebot', external_user_id: '123456', internal_user: 'kesepain', chat_type: 'private', external_chat_id: null, match_priority: 3 }],
    transports: [{
      id: 'onebot', name: 'onebot_ws_01', platform: 'onebot', display_name: 'OneBot 正向 WebSocket', description: 'OneBot 消息传输模块',
      capabilities: ['receive_text', 'send_text', 'receive_file'], state: 'running', connection_status: 'connected', bound_user: 'kesepain', allowed_tools: null,
      last_error: null, health: 'healthy', last_check: '2026-07-20T12:00:00Z', last_message_at: null, latency_ms: 12, messages_received_today: 2, messages_sent_today: 1,
      path: 'message/out/onebot', files_path: 'message/out/onebot/files', log_path: 'message/out/onebot/log', message_buffer: 'message/out/onebot/message.md',
      modules: { input: 'input.py', output: 'output.py', detect: 'detect.py' }, api_imported: true, polling_interval: '1s', health_interval: '30s', file_relay_enabled: true,
      log_rotation: '每日轮换', temporary_file_count: 1, temporary_file_bytes: 128, today_log_count: 12, logs_truncated: false,
      logs: [
        { id: 'log-1', direction: 'receive', kind: 'text', timestamp: '2026-07-21 12:49:08', content: '请查看今日任务', file_path: null, success: true, chat_type: 'private', chat_id: '123456', source: 'message/out/onebot/log/2026-07-21.md' },
        { id: 'log-2', direction: 'receive', kind: 'file', timestamp: '2026-07-21 12:48:21', content: 'meeting_notes.docx', file_path: 'message/out/onebot/files/meeting_notes.docx', success: true, chat_type: 'private', chat_id: '123456', source: 'message/out/onebot/log/2026-07-21.md' },
        { id: 'log-3', direction: 'send', kind: 'text', timestamp: '2026-07-21 12:47:58', content: '任务已整理完成', file_path: null, success: true, chat_type: 'private', chat_id: '123456', source: 'message/out/onebot/log/2026-07-21.md' },
        ...Array.from({ length: 9 }, (_, index) => ({ id: `log-${index + 4}`, direction: 'receive', kind: 'text', timestamp: `2026-07-21 12:${String(46 - index).padStart(2, '0')}:00`, content: `历史日志 ${index + 4}`, file_path: null, success: true, chat_type: 'private', chat_id: '123456', source: 'message/out/onebot/log/2026-07-21.md' })),
      ],
    }],
    summary: { total_bindings: 1, total_transports: 1, running_transports: 1, stopped_transports: 0, error_transports: 0, connected_transports: 1, temporary_files: 1, today_logs: 12 }, issues: [],
  })),
  http.post('/api/users/kesepain/message/modules/:module/check', ({ params }) => HttpResponse.json({ user: 'kesepain', module: params.module, checked: true, state: { health: 'healthy' }, transport: null })),
  http.delete('/api/users/kesepain/message/modules/:module', ({ params }) => HttpResponse.json({ user: 'kesepain', module: params.module, platform: params.module, path: `message/out/${params.module}`, deleted: true })),
  http.get('/api/users/kesepain/soul', () => HttpResponse.json({ user: 'kesepain', path: 'users/kesepain/user_soul.md', content: '# 用户人格', size: 12, updated_at: 1 })),
  http.put('/api/users/kesepain/soul', async ({ request }) => {
    const body = await request.json() as { content: string }
    return HttpResponse.json({ user: 'kesepain', path: 'users/kesepain/user_soul.md', content: body.content, size: body.content.length, updated_at: 2 })
  }),
  http.get('/api/global-soul', () => HttpResponse.json({ path: 'config/global_soul.md', content: '# 全局人格', size: 12, updated_at: 1 })),
  http.put('/api/global-soul', async ({ request }) => {
    const body = await request.json() as { content: string }
    return HttpResponse.json({ path: 'config/global_soul.md', content: body.content, size: body.content.length, updated_at: 2 })
  }),
  http.get('/api/users/kesepain/expand', () => HttpResponse.json({
    user: 'kesepain', summary: { total: 1, global: 1, shared: 0, user: 0 }, status_summary: { enabled: 1, healthy: 1, invalid: 0 },
    expands: [
      { scope: 'global', root: 'global_expand', items: [{
        id: 'global:example', scope: 'global', name: 'example', display_name: '智能灯光控制', description: '控制客厅与卧室的智能灯组。',
        type: 'directory', root: 'global_expand', path: 'global_expand/example', relative_path: 'example', has_register: true,
        valid: true, error: '', whitelisted: true, active_for_main_agent: true, input_health: '正常', open_input: true, open_control: true,
        input_data: 'input_data.md', start_update: 'data_update.py', start_expand: 'start_expand.py', start_control: 'expand_control.md',
        control_document: '## 注入层\n灯光控制可用\n\n## 操作层\n用户要求开灯时调用 start_expand.py',
        control_injection_markdown: '灯光控制可用', control_operation_markdown: '用户要求开灯时调用 start_expand.py',
        collected_markdown: '# 灯光状态\n客厅已开启', injected_markdown: '[global:example]\n## 数据采集\n# 灯光状态\n客厅已开启', injected_tokens: 18,
        files: [{ name: 'expand.json', relative_path: 'example/expand.json', size: 64, updated_at: 1 }], updated_at: 1,
      }] },
      { scope: 'shared', root: 'shared_expand', items: [] },
      { scope: 'user', root: 'users/kesepain/expand', items: [] },
    ],
    injection: { content: '[global:example]\n## 数据采集\n# 灯光状态\n客厅已开启', source_files: ['global_expand/example/input_data.md'], original_chars: 48, injected_chars: 48, original_items: 1, injected_items: 1, estimated_tokens: 18, truncated: false, prompt_section: 'expand_data', prompt_position: 'System Prompt / Expand Data' },
    source_policy: sourcePolicy,
  })),
  http.post('/api/users/kesepain/expand/:scope/:module/refresh', () => HttpResponse.json({ updated: true })),
  http.patch('/api/users/kesepain/expand/:scope/:module/enabled', () => HttpResponse.json({ enabled: false })),
  http.delete('/api/users/kesepain/expand/user/:module', () => HttpResponse.json({ deleted: true })),
]

export const server = setupServer(...handlers)
