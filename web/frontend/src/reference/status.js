import { icon, h, LIVE_RUNTIME, pageHead, refreshPage, fmtNum, fmtRelative, $$, fill, fmtThousands, statusText, copyText, buildTokenChart, navigate, LIVE_CAPABILITIES, statusBadge, readDetail, Api, Store, setRuntimeLogs, LIVE_RUNTIME_LOGS, emptyState, clamp, fmtDateTime, fmtDuration, makePager, renderMarkdownWithWidgets } from './shared.js'
/* ============================================================================
   pages/status-profile · 运行诊断 / 身份与人格 / 运行模块兼容入口
   ========================================================================= */

export const RUNTIME_TABS = [
  { key: 'prompt', label: '系统提示词上下文', icon: 'fileCode' },
  { key: 'tokens', label: '今日 Token', icon: 'activity' },
  { key: 'api', label: '用户 API 配置', icon: 'cpu' },
  { key: 'external', label: '外部组件与路由', icon: 'route' },
  { key: 'maintenance', label: '调度与维护', icon: 'workflow' },
  { key: 'logs', label: '运行日志', icon: 'terminal' },
];

const runtimeBadge = state => statusBadge(state, {
  injected: '已注入', truncated: '已截断', loaded: '已加载', offline: '未连接',
}[state]);

export function renderStatusPage(ctx) {
  let activeTab = ctx.viewState.statusTab || ctx.params.tab || 'prompt';

  const pageEl = h('div.page', h('div.page-inner.wide'));
  const inner = pageEl.firstElementChild;

  const R = LIVE_RUNTIME;

  inner.appendChild(pageHead({
    title: '运行诊断',
    desc: '查看提示词上下文、Token 用量、模型配置、外部组件与运行日志。',
    actions: [
      h('button.btn.btn-outline', {
        onclick: () => { void refreshPage(); },
      }, h('span', { html: icon('refresh', 'icon-sm') }), '刷新'),
    ],
  }));

  /* 顶部 4 张摘要卡 */
  const summaryCards = h('div.stat-row');
  const cardDefs = [
    { label: 'API 配置', value: R.api.model, foot: `${R.api.type} · ${R.api.configured ? '已配置' : '待配置'}`, icon: 'cpu', tab: 'api', smallValue: true },
    { label: '上下文占用', value: `${Number(R.context.percent).toFixed(1)}%`, foot: `${fmtNum(R.context.used_tokens)} / ${fmtNum(R.context.max_tokens)} tokens`, icon: 'gauge', tab: 'tokens' },
    { label: '对话轮次', value: `${R.context.rounds} / ${R.context.round_limit}`, foot: `压缩阈值 ${fmtNum(R.context.compression_threshold)} tokens`, icon: 'chat', tab: 'prompt' },
    { label: '消息路由', value: `${R.message_routes.summary.connected_transports} / ${R.message_routes.summary.total_transports}`, foot: R.message_routes.summary.error_transports ? '部分异常' : '无错误通道', icon: 'route', tab: 'external' },
  ];
  cardDefs.forEach((c) => {
    const card = h('button.stat', {
      style: { textAlign: 'left', cursor: 'pointer', borderColor: activeTab === c.tab ? 'var(--accent)' : '' },
      onclick: () => setTab(c.tab),
    },
      h('div.stat-label', h('span', { html: icon(c.icon, 'icon-sm') }), c.label),
      h('div.stat-value', { style: c.smallValue ? { fontSize: 'var(--t-md)', letterSpacing: '-0.01em', marginTop: '8px' } : {} }, c.value),
      h('div.stat-foot', c.foot),
    );
    summaryCards.appendChild(card);
  });
  inner.appendChild(summaryCards);

  const tabBar = h('div.tabs', RUNTIME_TABS.map((t) =>
    h('button.tab' + (t.key === activeTab ? '.is-active' : ''), {
      onclick: () => setTab(t.key),
    }, h('span', { style: { display: 'inline-flex', alignItems: 'center', gap: '6px' } },
      h('span', { html: icon(t.icon, 'icon-sm') }), t.label)),
  ));
  inner.appendChild(tabBar);

  const body = h('div');
  inner.appendChild(body);

  function setTab(key) {
    activeTab = key;
    ctx.viewState.statusTab=key;
    $$('.tab', tabBar).forEach((x, i) => x.classList.toggle('is-active', RUNTIME_TABS[i].key === key));
    $$('.stat', summaryCards).forEach((x, i) => { x.style.borderColor = cardDefs[i].tab === key ? 'var(--accent)' : ''; });
    draw();
  }

  function draw() {
    fill(body);
    if (activeTab === 'prompt') body.appendChild(drawPromptTab());
    else if (activeTab === 'tokens') body.appendChild(drawTokensTab());
    else if (activeTab === 'api') body.appendChild(drawApiTab());
    else if (activeTab === 'external') body.appendChild(drawExternalTab());
    else if (activeTab === 'maintenance') body.appendChild(drawMaintenanceTab());
    else body.appendChild(drawLogsTab());
  }

  /* ---- 系统提示词 ---- */
  function drawPromptTab() {
    const names = {
      user_soul: '用户人格', global_soul: '全局人格', agents: '运行手册',
      global_agents: '全局子智能体', user_agents: '用户子智能体', plugins: '工具插件',
      skills: '技能指令', knowledge: '知识库索引', permanent_memory: '永久记忆',
      important_memory: '临时重要记忆', task_plan: '任务计划', expand_data: '拓展数据',
      perception: '感知数据',
      agents_manual: '运行手册', global_subagent_registry: '全局子智能体',
      user_subagent_registry: '用户子智能体', knowledge_index: '知识库索引',
      'temporary_memory:half_year': '半年记忆', 'temporary_memory:one_month': '月记忆',
      'temporary_memory:seven_days': '周记忆',
    };
    return h('div.card.runtime-prompt-panel',
      h('div.card-head',
        h('div.section-title', { style: { fontSize: 'var(--t-sm)' } },
          h('span', { html: icon('fileCode', 'icon-sm') }), '系统提示词与拼接状态'),
        h('div.runtime-prompt-summary',
          h('span', `${fmtThousands(R.prompt.total_chars)} 字符`),
          h('span', `约 ${fmtThousands(R.prompt.estimated_tokens)} Tokens`),
          h('span.badge', '估算值'),
        ),
      ),
      h('div.runtime-prompt-layout',
        h('div.doc-content.runtime-prompt-content',
          renderMarkdownWithWidgets(R.prompt.content || '（本次提示词为空）', { readOnly: true })),
        h('div.runtime-prompt-components',
          h('table.table',
            h('thead', h('tr', h('th', '模块'), h('th', '状态'), h('th.num', 'Tokens（估算）'), h('th', '来源'))),
            h('tbody', (R.prompt.components || []).map(c => h('tr',
              h('td', h('div', names[c.name] || c.name),
                h('small', `${c.injected_items}/${c.original_items} 项 · ${fmtThousands(c.chars)} 字符`)),
              h('td', runtimeBadge(c.state)),
              h('td.num', fmtThousands(c.tokens)),
              h('td', h('button.icon-btn', {
                title: (c.source_files || []).join('\n') || '无来源文件',
                'aria-label': `复制 ${names[c.name] || c.name} 来源文件`,
                onclick: () => { void copyText((c.source_files || []).join('\n')); },
              }, h('span', { html: icon('copy', 'icon-sm') }))),
            ))),
          ),
          !R.prompt.components?.length ? emptyState({ title: '本次暂无拼接组件' }) : null,
        ),
      ),
    );
  }

  /* ---- Token ---- */
  function drawTokensTab() {
    const wrap = h('div', { style: { display: 'flex', flexDirection: 'column', gap: 'var(--s-5)' } });
    const tk = R.tokens;

    wrap.appendChild(h('div.card.card-pad',
      h('div', { style: { display: 'flex', alignItems: 'baseline', gap: 'var(--s-3)', marginBottom: 'var(--s-4)', flexWrap: 'wrap' } },
        h('div', { style: { fontSize: 'var(--t-sm)', fontWeight: '600' } }, `自然日 ${tk.date}（${tk.timezone}）`),
        tk.estimated ? h('span.badge.badge-warning', '含估算值') : null,
        h('div', { style: { flex: '1' } }),
        h('span', { style: { fontSize: 'var(--t-2xs)', color: 'var(--text-faint)' } }, `共 ${tk.request_count} 次请求`),
      ),
      h('div.metric-grid',
        h('div.metric', h('div.metric-label', '发送'), h('div.metric-value', fmtThousands(tk.sent_tokens))),
        h('div.metric', h('div.metric-label', '接收'), h('div.metric-value', fmtThousands(tk.received_tokens))),
        h('div.metric', h('div.metric-label', '合计'), h('div.metric-value', fmtThousands(tk.total_tokens))),
        h('div.metric', h('div.metric-label', '缓存命中'), h('div.metric-value', `${Number(tk.cache_rate).toFixed(1)}%`)),
        h('div.metric', h('div.metric-label', '缓存 Token'), h('div.metric-value', fmtThousands(tk.cached_tokens))),
      ),
    ));

    wrap.appendChild(h('div.card.card-pad',
      h('div.section-title', { style: { marginBottom: 'var(--s-3)' } },
        h('span', { html: icon('activity', 'icon-sm') }), '24 小时趋势'),
      (() => {
        return buildTokenChart(tk.trend);
      })(),
      h('div', { style: { fontSize: 'var(--t-2xs)', color: 'var(--text-faint)', marginTop: 'var(--s-3)' } },
        '时间粒度为小时，按上海自然日切分。'),
    ));

    wrap.appendChild(h('div.card', { style: { overflow: 'hidden' } },
      h('div.card-head', h('div.section-title', { style: { fontSize: 'var(--t-sm)' } }, '按小时明细')),
      h('div.table-wrap', { style: { border: 'none', borderRadius: '0' } },
        h('table.table',
          h('thead', h('tr', h('th', '小时'), h('th.num', 'Token 合计'))),
          h('tbody', tk.trend.map((t, index) => h('tr',
            h('td.mono', index + ':00'),
            h('td.num', fmtThousands(t)),
          ))),
        ),
      ),
    ));
    return wrap;
  }

  /* ---- API 配置 ---- */
  function drawApiTab() {
    const capability = LIVE_CAPABILITIES.capabilities;
    const hasCapabilities = Boolean(capability.metadata.context_window || capability.input_modalities?.length);
    const modelLabels = {
      default: '默认子智能体', cheap: '轻量子智能体', reasoning: '推理子智能体',
      vision: '视觉理解', image_generation: '图片生成', image_edit: '图片编辑',
      audio_transcription: '音频转写', speech_generation: '语音生成',
      speech_to_speech: '语音转换', video_understanding: '视频理解', video_generation: '视频生成',
    };
    const modelGroup = (title, values) => h('div.runtime-api-group',
      h('div.doc-label', title),
      h('div.desc-list', Object.entries(values || {}).map(([key, value]) =>
        h('div.desc-row', h('span.desc-key', modelLabels[key] || key),
          h('span.desc-val.mono', value || '沿用主模型')))),
      !Object.keys(values || {}).length ? h('div.runtime-muted', '未单独配置') : null,
    );
    return h('div.card.card-pad',
      h('div', { style: { display: 'flex', alignItems: 'center', gap: 'var(--s-3)', marginBottom: 'var(--s-4)' } },
        h('div.section-title', { style: { fontSize: 'var(--t-sm)' } }, h('span', { html: icon('cpu', 'icon-sm') }), '用户 API 配置'),
        h('div', { style: { flex: '1' } }),
        R.api.configured ? h('span.badge.badge-success', '已配置') : h('span.badge.badge-warning', '待配置'),
        h('button.btn.btn-ghost.btn-sm', { onclick: () => navigate('/settings', { tab: 'provider' }) },
          h('span', { html: icon('settings', 'icon-sm') }), '编辑配置'),
      ),
      h('div.banner.banner-info', { style: { marginBottom: 'var(--s-4)' } },
        h('span', { html: icon('shield') }),
        h('div', '凭据按来源脱敏展示，占位符不代表真实密钥；实际密钥只在明确修改时提交。'),
      ),
      h('div.desc-list',
        h('div.desc-row', h('span.desc-key', '协议类型'), h('span.desc-val', R.api.type + (R.api.type === 'kemo' ? '（Kemo 协议）' : '（OpenAI 兼容）'))),
        h('div.desc-row', h('span.desc-key', 'Base URL'), h('span.desc-val.mono', R.api.base_url)),
        h('div.desc-row', h('span.desc-key', '主模型'), h('span.desc-val.mono', R.api.model)),
        h('div.desc-row', h('span.desc-key', '思考档位'), h('span.desc-val', h('span.badge.badge-accent', R.api.thinking_effort))),
        h('div.desc-row', h('span.desc-key', '凭据来源'), h('span.desc-val',
          h('span.badge', R.api.credential_source === 'inline' ? '配置文件内联' : R.api.credential_source === 'environment' ? '环境变量' : '缺失'))),
        h('div.desc-row', h('span.desc-key', '流式'), h('span.desc-val', R.api.stream === undefined ? '未提供' : R.api.stream ? '已开启' : '已关闭')),
        h('div.desc-row', h('span.desc-key', '请求超时'), h('span.desc-val', R.api.timeout == null ? '未提供' : `${R.api.timeout} 秒`)),
        h('div.desc-row', h('span.desc-key', '配置上下文上限'), h('span.desc-val.tnum', `${fmtThousands(R.api.context_tokens ?? R.context.max_tokens)} tokens`)),
        h('div.desc-row', h('span.desc-key', '配置输入模态'), h('span.desc-val', (R.api.input_modalities || []).join(' / ') || '未单独声明')),
      ),
      h('div.runtime-api-models',
        modelGroup('子智能体模型', R.api.agent_models),
        modelGroup('多模态模型', R.api.multimodal_models),
        modelGroup('多模态路由', R.api.multimodal_routing),
      ),
      h('div', { style: { marginTop: 'var(--s-5)' } },
        h('div.doc-label', { style: { marginBottom: '8px' } }, '模型能力声明'),
        !hasCapabilities ? h('div.runtime-muted', R.api.type === 'chat'
          ? 'Chat 协议未提供模型能力声明，以上为实际配置值。'
          : '模型能力声明暂不可用，以上为实际配置值。') : null,
        hasCapabilities ? 
        h('div.desc-list',
          h('div.desc-row', h('span.desc-key', '输入模态'), h('span.desc-val',
            h('div', { style: { display: 'flex', gap: '5px', flexWrap: 'wrap' } },
              (LIVE_CAPABILITIES.capabilities.input_modalities || []).map((m) => h('span.badge', m))))),
          h('div.desc-row', h('span.desc-key', '输出模态'), h('span.desc-val',
            h('div', { style: { display: 'flex', gap: '5px' } },
              (LIVE_CAPABILITIES.capabilities.output_modalities || []).map((m) => h('span.badge', m))))),
          h('div.desc-row', h('span.desc-key', '推理支持'), h('span.desc-val',
            LIVE_CAPABILITIES.capabilities.reasoning.supported
              ? `支持 · 档位 ${LIVE_CAPABILITIES.capabilities.reasoning.efforts.join(' / ')}`
              : '不支持')),
          h('div.desc-row', h('span.desc-key', '工具能力'), h('span.desc-val',
            `并行调用 ${LIVE_CAPABILITIES.capabilities.tools.parallel_calls ? '支持' : '不支持'} · 多模态结果 ${LIVE_CAPABILITIES.capabilities.tools.multimodal_results ? '支持' : '不支持'}`)),
          h('div.desc-row', h('span.desc-key', '模型上下文窗口'), h('span.desc-val.tnum', capability.metadata.context_window ? fmtThousands(capability.metadata.context_window) + ' tokens' : '模型未声明')),
        ) : null,
      ),
    );
  }

  /* ---- 外部组件 ---- */
  function drawExternalTab() {
    const componentCard = (item, kind) => h('article.runtime-component-card',
      h('div.runtime-component-head',
        h('span', { html: icon(kind, 'icon-sm') }),
        h('strong', item.name || item.id),
        item.scope ? h('span.badge', item.scope) : null,
      ),
      h('div.runtime-component-badges', runtimeBadge(item.state), runtimeBadge(item.health)),
      h('p.runtime-component-description', item.description || '暂无状态说明'),
      item.platform ? h('div.runtime-component-meta',
        h('span', item.platform),
        h('span', item.latency_ms == null ? '延迟未提供' : `${item.latency_ms} ms`),
        item.last_check ? h('span', `检测 ${fmtRelative(item.last_check)}`) : null,
      ) : item.updated_at ? h('div.runtime-component-meta', `更新 ${fmtRelative(
        typeof item.updated_at === 'number' && item.updated_at < 1e12 ? item.updated_at * 1000 : item.updated_at
      )}`) : null,
    );
    const group = (title, items, kind) => h('section.runtime-component-group',
      h('div.section-title', h('span', { html: icon(kind, 'icon-sm') }), title,
        h('span.badge', `${items.filter(item => item.health === 'healthy').length} / ${items.length} 健康`)),
      h('div.runtime-component-grid', items.map(item => componentCard(item, kind))),
      !items.length ? emptyState({ title: '暂无模块', icon: kind }) : null,
    );
    return h('div.runtime-external-layout',
      h('div.card.card-pad.runtime-component-panel',
        h('div.section-title', '外部组件'),
        h('div.runtime-health-groups',
          group('感知组件', R.components.sense || [], 'sense'),
          group('拓展组件', R.components.expand || [], 'expand'),
        ),
      ),
      h('div.runtime-route-column',
        h('div.card.card-pad', group('消息路由', R.message_routes.routes || [], 'route')),
        h('div.card.card-pad',
          h('div.section-title', h('span', { html: icon('server', 'icon-sm') }), '运行时宿主'),
          h('div.runtime-component-grid', Object.entries(R.runtime_host.components || {}).map(([key, value]) =>
            h('article.runtime-component-card', h('strong', key), statusBadge(value.state || 'unknown')))),
          !Object.keys(R.runtime_host.components || {}).length ? h('div.runtime-muted', '当前无宿主组件') : null,
        ),
      ),
    );
  }

  /* ---- 调度与维护 ---- */
  function drawMaintenanceTab() {
    const wrap = h('div', { style: { display: 'flex', flexDirection: 'column', gap: 'var(--s-5)' } });
    const cg = R.congestion;

    wrap.appendChild(h('div.card.card-pad',
      h('div.section-title', { style: { marginBottom: 'var(--s-4)' } }, h('span', { html: icon('activity', 'icon-sm') }), '拥塞与并发'),
      h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 'var(--s-4)' } },
        [['Provider 请求', cg.provider, 'active_requests', 'max_requests', 'available_requests', '可用'],
          ['Web 聊天槽位', cg.web, 'active_chats', 'max_chats', 'pending_chats', '排队'],
          ['消息工作线程', cg.message_router, 'active_workers', 'max_workers', 'queued_messages', '排队'],
        ].map(([label, obj, usedKey, maxKey, extraKey, extraLabel]) => {
          const pct = (obj[usedKey] / (obj[maxKey] || 1)) * 100;
          return h('div', { style: { padding: 'var(--s-4)', borderRadius: 'var(--r-md)', background: 'var(--bg-inset)', border: '1px solid var(--border-subtle)' } },
            h('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '8px' } },
              h('span', { style: { fontSize: 'var(--t-xs)', fontWeight: '600' } }, label),
              h('span', { style: { fontSize: 'var(--t-sm)', fontWeight: '620', fontVariantNumeric: 'tabular-nums' } }, `${obj[usedKey]} / ${obj[maxKey]}`),
            ),
            h('div.progress-track', h('div.progress-fill', {
              style: { width: pct + '%', background: pct > 80 ? 'var(--danger)' : pct > 55 ? 'var(--warning)' : 'var(--accent)' },
            })),
            h('div', { style: { display: 'flex', justifyContent: 'space-between', fontSize: 'var(--t-2xs)', color: 'var(--text-faint)', marginTop: '6px' } },
              h('span', `占用 ${pct.toFixed(0)}%`),
              h('span', `${extraLabel} ${obj[extraKey]}`),
            ),
          );
        }),
      ),
    ));

    wrap.appendChild(h('div.card.card-pad',
      h('div.section-title', { style: { marginBottom: 'var(--s-4)' } }, h('span', { html: icon('memory', 'icon-sm') }), '记忆更新'),
      h('div.metric-grid',
        h('div.metric', h('div.metric-label', '今日更新'), h('div.metric-value', String(R.memory.updated_today))),
        h('div.metric', h('div.metric-label', '今日晋升'), h('div.metric-value', String(R.memory.upgraded_today))),
        h('div.metric', h('div.metric-label', '晋升跟踪'), h('div.metric-value', String(R.memory.upgrade_tracking))),
      ),
      h('div.table-wrap', { style: { marginTop: 'var(--s-4)' } },
        h('table.table',
          h('thead', h('tr', h('th', '层级'), h('th', '文件'), h('th.num', '权重'), h('th', '更新时间'))),
          h('tbody', (R.memory.updates || []).map((u) => h('tr',
            h('td', h('span', { class: 'scope-tag scope-user' }, u.tier)),
            h('td.mono', { style: { fontSize: 'var(--t-2xs)' } }, u.filename),
            h('td.num', String(u.weight)),
            h('td', { style: { fontSize: 'var(--t-xs)', color: 'var(--text-tertiary)' } }, fmtRelative(u.updated_at)),
          ))),
        ),
      ),
    ));

    wrap.appendChild(h('div.card.card-pad',
      h('div.section-title', { style: { marginBottom: 'var(--s-4)' } }, h('span', { html: icon('tasks', 'icon-sm') }), '任务与调度'),
      h('div.metric-grid',
        h('div.metric', h('div.metric-label', '执行中计划'), h('div.metric-value', String(R.tasks.summary.active_plans))),
        h('div.metric', h('div.metric-label', '等待计划'), h('div.metric-value', String(R.tasks.summary.waiting_plans))),
        h('div.metric', h('div.metric-label', '启用调度'), h('div.metric-value', String(R.tasks.summary.enabled_crons))),
        h('div.metric', h('div.metric-label', '已完成'), h('div.metric-value', String(R.tasks.summary.completed_plans))),
      ),
      h('div', { style: { marginTop: 'var(--s-4)' } },
        h('div.doc-label', { style: { marginBottom: '8px' } }, '系统维护 Cron'),
        h('div.desc-list',
          h('div.desc-row', h('span.desc-key', '跟踪状态'), h('span.desc-val', R.system_cron.tracking ? h('span', { style: { color: 'var(--success)' } }, '已跟踪') : '未跟踪')),
          h('div.desc-row', h('span.desc-key', '任务数'), h('span.desc-val.tnum', String(R.system_cron.tasks.length))),
          h('div.desc-row', h('span.desc-key', '执行记录'), h('span.desc-val.tnum', String(R.system_cron.executions.length))),
        ),
      ),
      h('button.btn.btn-ghost.btn-sm', { style: { marginTop: 'var(--s-3)' }, onclick: () => navigate('/tasks') },
        '在任务中枢查看', h('span', { html: icon('arrowRight', 'icon-sm') })),
    ));
    return wrap;
  }

  /* ---- 运行日志 ---- */
  function drawLogsTab() {
    let category = ctx.viewState.logsCategory || 'all';
    let page = ctx.viewState.logsPage || 1;
    const PER_PAGE = 25;
    const host = h('div', { style: { display: 'flex', flexDirection: 'column', gap: 'var(--s-4)' } });
    let terminalView = null;

    function updateTerminal(view) {
      const entries = LIVE_RUNTIME_LOGS.entries;
      const info = LIVE_RUNTIME_LOGS.terminal || {};
      const follow = ctx.viewState.terminalFollow !== false;
      const top = view.initialized ? view.term.scrollTop : ctx.viewState.terminalScrollTop || 0;
      const ids = new Set(entries.map(entry => entry.id));
      for (const [id, node] of view.rows) {
        if (!ids.has(id)) { node.remove(); view.rows.delete(id); }
      }
      entries.forEach((entry, index) => {
        let node = view.rows.get(entry.id);
        if (!node) {
          node = h('div.runtime-terminal-line',
            h('time', fmtDateTime(entry.occurred_at, { timeOnly: true })),
            h('span.badge', entry.stream === 'stderr' ? 'ERR' : 'OUT'),
            // Console output lives in title; detail is its source label.
            h('pre', entry.content ?? entry.title ?? entry.detail ?? ''),
          );
          view.rows.set(entry.id, node);
        }
        if (view.term.children[index] !== node) {
          view.term.insertBefore(node, view.term.children[index] || null);
        }
      });
      const values = ['实时接收', `${info.loaded_items ?? entries.length} / ${info.window_size ?? 300}`,
        info.stdout_items ?? 0, info.stderr_items ?? 0];
      view.metrics.forEach((node, index) => { node.textContent = String(values[index]); });
      view.followButton.textContent = follow ? '跟随最新输出' : '回到最新输出';
      view.retention.textContent = `保留 ${info.retention_seconds ?? 3600} 秒 · 仅当前进程`;
      fill(view.errors, (LIVE_RUNTIME_LOGS.source_errors || []).map(error =>
        h('div.banner.banner-warning', { role: 'status' }, error)));
      view.initialized = true;
      requestAnimationFrame(() => {
        if (!ctx.active || !view.term.isConnected) return;
        view.term.scrollTop = ctx.viewState.terminalFollow !== false
          ? view.term.scrollHeight : top;
      });
    }

    const render = () => {
      // Keep the terminal and its controls mounted across 2-second snapshots.
      // This preserves selection, scrolling and a stable click target.
      if (category === 'terminal' && LIVE_RUNTIME_LOGS.category === 'terminal' && terminalView?.panel.isConnected) {
        updateTerminal(terminalView);
        return;
      }
      const previousTerminal = host.querySelector('.runtime-terminal');
      if (previousTerminal?.isConnected) {
        ctx.viewState.terminalScrollTop = previousTerminal.scrollTop;
        ctx.viewState.terminalFollow = previousTerminal.scrollHeight - previousTerminal.clientHeight - previousTerminal.scrollTop < 28;
      }
      terminalView = null;
      fill(host);
      host.appendChild(h('div', { style: { display: 'flex', alignItems: 'center', gap: 'var(--s-3)', flexWrap: 'wrap' } },
        h('div.segments', ['all', 'backend', 'threads', 'terminal', 'message'].map((k) =>
          h('button.segment' + (k === category ? '.is-active' : ''), {
            onclick: (ev) => {
              category = k; page = 1; ctx.viewState.logsCategory=k;ctx.viewState.logsPage=1;
              void readDetail(() => Api.getRuntimeLogs(Store.user, k, 1), result => { if(category===k){setRuntimeLogs(result); render();} });
              $$('.segment', ev.currentTarget.parentElement).forEach((x) => x.classList.remove('is-active'));
              ev.currentTarget.classList.add('is-active');
              render();
            },
          }, k === 'all' ? '全部' : k === 'backend' ? '后端日志' : k === 'threads' ? '后端线程' : k === 'terminal' ? '终端日志' : '消息日志')),
        ),
        h('div', { style: { flex: '1' } }),
        h('span', { style: { fontSize: 'var(--t-2xs)', color: 'var(--text-faint)' } },
          category === 'terminal' ? '每 2 秒刷新' : '每 10 秒刷新'),
        h('button.btn.btn-outline.btn-sm', { onclick: () => {
          const requestedCategory = category, requestedPage = page;
          void readDetail(() => Api.getRuntimeLogs(Store.user, requestedCategory, requestedPage, true),
            result => { if (category === requestedCategory && page === requestedPage) { setRuntimeLogs(result); render(); } });
        } },
          h('span', { html: icon('refresh', 'icon-sm') }), '刷新'),
      ));

      /* 分类说明 */
      const hintMap = {
        all: '所有分类的合并视图。',
        backend: '后端业务日志，来自持久化记录。',
        threads: '后端线程活动，来自本进程内存，重启即消失。',
        terminal: '进程标准输出与错误输出，实时快照。',
        message: '外部消息收发记录。',
      };
      host.appendChild(h('div', { style: { fontSize: 'var(--t-xs)', color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 'var(--s-2)' } },
        h('span', { html: icon('info', 'icon-sm') }),
        hintMap[category] + ' 这是有界的近期视图，不代表完整历史。',
      ));

      const entries = LIVE_RUNTIME_LOGS.entries;
      for (const error of LIVE_RUNTIME_LOGS.source_errors || []) {
        host.appendChild(h('div.banner.banner-warning', { role: 'status' }, error));
      }
      // A category switch can leave the previous snapshot in memory until
      // the new request resolves. Never label old rows as the new category.
      if (LIVE_RUNTIME_LOGS.category !== category) {
        host.appendChild(h('div.runtime-muted', '正在读取日志…'));
        return;
      }
      if (!entries.length && category !== 'terminal') {
        host.appendChild(emptyState({ icon: 'terminal', title: '当前分类暂无记录', desc: '部分日志源可能不可用，或该分类真的为空。' }));
        return;
      }

      const totalPages = LIVE_RUNTIME_LOGS.pagination.total_pages || 1;
      page = clamp(page, 1, totalPages);
      const slice = entries;

      /* 终端输出合并视图 */
      if (category === 'terminal') {
        const info = LIVE_RUNTIME_LOGS.terminal || {};
        const follow = ctx.viewState.terminalFollow !== false;
        const summary = h('div.runtime-terminal-summary',
          [['采集状态', '实时接收'], ['载入窗口', `${info.loaded_items ?? slice.length} / ${info.window_size ?? 300}`],
            ['标准输出', info.stdout_items ?? 0], ['标准错误', info.stderr_items ?? 0]]
            .map(([label, value]) => h('div.card.card-pad',
              h('div.runtime-muted', label), h('strong', String(value)))),
        );
        host.appendChild(summary);
        const term = h('div.terminal.runtime-terminal', { role: 'region', 'aria-label': '启动终端输出', tabindex: '0' });
        const followButton = h('button.btn.btn-ghost.btn-sm', {
          onclick: () => { ctx.viewState.terminalFollow = true; term.scrollTop = term.scrollHeight; },
        }, follow ? '跟随最新输出' : '回到最新输出');
        term.addEventListener('scroll', () => {
          // Removing an old snapshot can dispatch a late scroll event with
          // zero dimensions. It must not overwrite the new window's state.
          if (!ctx.active || !term.isConnected) return;
          ctx.viewState.terminalScrollTop = term.scrollTop;
          ctx.viewState.terminalFollow = term.scrollHeight - term.clientHeight - term.scrollTop < 28;
          followButton.textContent = ctx.viewState.terminalFollow ? '跟随最新输出' : '回到最新输出';
        });
        const retention = h('span.runtime-muted');
        const errors = h('div.runtime-terminal-errors');
        const panel = h('div.card.runtime-terminal-panel',
          h('div.card-head', h('div.section-title', 'kemo-agent 启动终端'), h('span.badge', '只读')),
          errors,
          term,
          h('div.card-head', followButton, retention),
        );
        host.appendChild(panel);
        terminalView = { panel, term, followButton, retention, errors, rows: new Map(), metrics: [...summary.querySelectorAll('strong')] };
        updateTerminal(terminalView);
      } else {
        const list = h('div', { style: { display: 'flex', flexDirection: 'column', gap: 'var(--s-2)' } });
        slice.forEach((e) => {
          const tone = e.status === 'failed' ? 'danger' : e.status === 'success' ? 'success' : e.status === 'warning' ? 'warning' : 'neutral';
          list.appendChild(h('div.runtime-log-card',
            h('div', { style: { color: tone === 'danger' ? 'var(--danger)' : tone === 'success' ? 'var(--success)' : tone === 'warning' ? 'var(--warning)' : 'var(--text-faint)', flex: 'none', marginTop: '1px' } },
              h('span', { html: icon(tone === 'danger' ? 'xCircle' : tone === 'success' ? 'checkCircle' : tone === 'warning' ? 'alertTriangle' : 'info') })),
            h('div', { style: { flex: '1', minWidth: '0' } },
              h('div.runtime-log-title', e.title),
              e.detail ? h('div.runtime-log-detail', e.detail) : null,
              e.category === 'message' ? h('div.runtime-message-body',
                h('span.badge', e.direction === 'receive' ? '正文输入' : e.direction === 'send' ? '正文输出' : '消息正文'),
                h('pre', e.content || '（无文本正文）'),
              ) : null,
              h('div.runtime-log-meta',
                h('span.badge', e.category),
                h('span', e.source === 'sqlite' ? '持久化记录' : e.source === 'memory' ? '本进程内存' : '实时快照'),
                e.duration_ms ? h('span', fmtDuration(e.duration_ms)) : null,
                e.exit_code ? h('span', 'exit ' + e.exit_code) : null,
                h('span', fmtRelative(e.occurred_at)),
              ),
            ),
            h('button.icon-btn', {
              style: { width: '26px', height: '26px' },
              'aria-label': '复制日志', title: '复制日志',
              onclick: () => { void copyText([e.title, e.detail, e.content].filter(Boolean).join('\n')); },
            }, h('span', { html: icon('copy', 'icon-sm') })),
          ));
        });
        host.appendChild(list);
      }

      const pager = makePager(page, totalPages, (p) => { page = p;ctx.viewState.logsPage=p; void readDetail(() => Api.getRuntimeLogs(Store.user, category, p), result => { if(page===p){setRuntimeLogs(result); render();} }); });
      if (pager) host.appendChild(pager);
    };

    let ticks=0;
    const timer=setInterval(()=>{
      if(!host.isConnected || !ctx.active){clearInterval(timer);return}
      if(document.hidden || ctx.busy)return;
      if(category!=='terminal' && ++ticks%5!==0)return;
      const requestedCategory=category,requestedPage=page;
      void readDetail(()=>Api.getRuntimeLogs(ctx.user,requestedCategory,requestedPage),result=>{
        if(category===requestedCategory && page===requestedPage){setRuntimeLogs(result);render()}
      });
    },2_000);
    ctx.registerCleanup?.(()=>clearInterval(timer));
    if(LIVE_RUNTIME_LOGS.category!==category || LIVE_RUNTIME_LOGS.pagination.page!==page){
      void readDetail(()=>Api.getRuntimeLogs(ctx.user,category,page),result=>{setRuntimeLogs(result);render()});
    }
    render();
    return host;
  }

  draw();
  return pageEl;
}

