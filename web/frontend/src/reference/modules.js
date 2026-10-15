import { Current, LIVE_SENSE, h, pageHead, refreshPage, icon, Bus, statTile, fmtNum, fill, fmtRelative, fmtDateTime, makeCopyButton, makeSwitch, mutate, Api, Store, Overlay, LIVE_EXPANDS, $$, emptyState, renderMarkdownWithWidgets, makePager, clamp } from './shared.js'
import { expandDataContent, expandDataFragment } from './expandDataPreview.js'
/* ============================================================================
   pages/modules · 感知 / 拓展（同构，均含动态面板）
   ========================================================================= */

/* ---------------------------- 动态面板渲染 ---------------------------- */

export function renderModulePanel(panel, opts = {}) {
  return Current.mountPanel(panel, opts);
}

const MODULES_PER_PAGE = 6;

// 感知和拓展共用完整文档预览、采集按钮与分页器，接口仍由各自页面传入。
function moduleDocumentCard(title, content, placeholder, options = {}) {
  return h('section.sense-document-card',
    h('div.sense-document-head',
      h('span', { html: icon('fileText', 'icon-sm') }),
      h('h3', title),
      h('span.sense-document-lines', `${content ? content.trimEnd().split(/\r?\n/).length : 0} 行`),
      makeCopyButton(content || ''),
    ),
    h('div.doc-content.sense-document-content', { role: 'region', 'aria-label': title, tabindex: '0' },
      renderMarkdownWithWidgets(content || placeholder, options)),
  );
}

function moduleRefreshButton(refresh, message) {
  return h('button.btn.btn-outline.btn-sm.sense-refresh-button', {
    onclick: (ev) => {
      const btn = ev.currentTarget;
      const ic = btn.querySelector('.icon');
      const sp = h('span.spinner.spinner-sm');
      ic.replaceWith(sp);
      btn.disabled = true;
      void mutate(refresh, message).finally(() => {
        if (btn.isConnected) {
          sp.replaceWith(h('span', { html: icon('refresh', 'icon-sm') }));
          btn.disabled = false;
        }
      });
    },
  }, h('span', { html: icon('refresh', 'icon-sm') }), '刷新采集');
}

export function renderSensePage(ctx) {
  const view = ctx.viewState.sense || (ctx.viewState.sense = {});
  let selectedName = LIVE_SENSE.sources.find(s => s.name === view.selectedName)?.name || LIVE_SENSE.sources[0]?.name;
  let previewMode = view.previewMode || 'overview';
  let detailTab = view.detailTab || 'data';
  let page = view.page || 1;

  function saveView() {
    Object.assign(view, { selectedName, previewMode, detailTab, page });
  }

  const pageEl = h('div.page', h('div.page-inner.wide'));
  const inner = pageEl.firstElementChild;

  inner.appendChild(pageHead({
    title: '感知',
    desc: '全局感知模块按目录注册，一个目录一个模块，读取 global_sense/<module>/sense.json。区分「采集到的数据」与「真正进入 Prompt 的片段」── 二者经常不同。',
    actions: [
      h('button.btn.btn-outline', { onclick: () => refreshPage() },
        h('span', { html: icon('refresh', 'icon-sm') }), '重新读取'),
      h('button.btn.btn-outline', {
        onclick: () => Bus.emit('composer:fill', { text: '帮我创建一个新的全局感知模块。' }),
      }, h('span', { html: icon('plus', 'icon-sm') }), '创建模块'),
    ],
  }));

  inner.appendChild(h('div.stat-row',
    statTile('感知模块', LIVE_SENSE.summary.registered, { icon: 'sense', foot: '全局注册总数' }),
    statTile('当前启用', LIVE_SENSE.summary.enabled, { icon: 'checkCircle', color: 'var(--success)', foot: '当前用户白名单内' }),
    statTile('注入 Tokens', LIVE_SENSE.injection.estimated_tokens, {
      icon: 'activity', unit: 'tok', foot: `${fmtNum(LIVE_SENSE.injection.injected_chars)} 字符 · ${LIVE_SENSE.injection.injected_items}/${LIVE_SENSE.injection.registered_items} 项`,
    }),
    statTile('异常模块', LIVE_SENSE.summary.unhealthy, {
      icon: 'alertTriangle', color: LIVE_SENSE.summary.unhealthy ? 'var(--warning)' : null,
      foot: LIVE_SENSE.summary.unhealthy ? '需要检查数据文件' : '全部健康',
    }),
  ));

  const listHost = h('div');
  const detailHost = h('div');

  function drawList() {
    fill(listHost);
    const totalPages = Math.max(1, Math.ceil(LIVE_SENSE.sources.length / MODULES_PER_PAGE));
    page = clamp(page, 1, totalPages);
    const visible = LIVE_SENSE.sources.slice((page - 1) * MODULES_PER_PAGE, page * MODULES_PER_PAGE);
    if (!visible.some(s => s.name === selectedName)) {
      selectedName = visible[0]?.name;
      detailTab = 'data';
    }
    saveView();
    listHost.appendChild(h('div.toolbar',
      h('div.section-label', { style: { padding: '0', marginBottom: '0' } }, h('span', { html: icon('sense', 'icon-sm') }), '感知模块'),
      h('div.spacer'),
      h('span', { style: { fontSize: 'var(--t-2xs)', color: 'var(--text-faint)' } }, `${LIVE_SENSE.sources.length} 个模块`),
    ));

    const list = h('div', { style: { display: 'flex', flexDirection: 'column', gap: 'var(--s-3)' } });
    visible.forEach((s) => {
      const card = h('div.module-card' + (s.name === selectedName ? '.is-active' : ''), {
        onclick: () => { selectedName = s.name; previewMode = 'detail'; detailTab = 'data'; saveView(); drawList(); drawDetail(); },
      },
        h('div.module-glyph', h('span', { html: icon(s.valid ? 'sense' : 'alertTriangle', 'icon-sm') })),
        h('div', { style: { flex: '1', minWidth: '0' } },
          h('div.module-name', s.display_name,
            s.active_for_main_agent ? h('span.badge.badge-success', '已注入') : s.valid ? h('span.badge', '已过滤') : h('span.badge.badge-danger', '无效'),
          ),
          h('div.module-id', `${s.name} · ${s.data_md || ''}`),
          s.error
            ? h('div', { style: { fontSize: 'var(--t-xs)', color: 'var(--danger)', marginTop: '6px', lineHeight: '1.5' } }, s.error)
            : h('div.module-preview', s.value_preview || s.description),
          h('div.module-meta',
            h('span', '热加载'),
            h('span', '·'),
            h('span', s.recent_update || fmtRelative(s.updated_at)),
            h('span', '·'),
            h('span', { style: { color: s.health === '正常' ? 'var(--success)' : 'var(--danger)' } }, s.health),
            s.injected_tokens ? h('span', { class: 'badge' }, fmtNum(s.injected_tokens) + ' tok') : null,
          ),
        ),
      );
      list.appendChild(card);
    });
    listHost.appendChild(visible.length ? list : emptyState({
      icon: 'sense', title: '尚无感知模块', desc: '创建模块后重新读取即可查看。',
    }));
    const pager = makePager(page, totalPages, (p) => { page = p; drawList(); drawDetail(); });
    if (pager) listHost.appendChild(pager);
  }

  function drawDetail() {
    fill(detailHost);
    const s = LIVE_SENSE.sources.find((x) => x.name === selectedName);
    if (!s) previewMode = 'overview';
    detailHost.appendChild(h('div.sense-preview-tabs',
      h('div.segments',
        [['overview', '全局注入'], ['detail', '模块详情']].map(([mode, label]) =>
          h('button.segment' + (previewMode === mode ? '.is-active' : ''), {
            disabled: mode === 'detail' && !s,
            onclick: () => { previewMode = mode; saveView(); drawDetail(); },
          }, label)),
      ),
    ));

    const panel = h('div.editor-panel.sense-detail-panel');

    if (previewMode === 'overview' || !s) {
      panel.appendChild(h('div.editor-head',
        h('div.editor-title', '全局感知注入预览'),
        h('span.badge' + (LIVE_SENSE.injection.enabled ? '.badge-success' : ''), LIVE_SENSE.injection.enabled ? '当前默认总览' : '当前无注入'),
      ));
      panel.appendChild(h('div.editor-body',
        h('div', { style: { display: 'flex', flexDirection: 'column', gap: 'var(--s-4)' } },
          h('div', { style: { display: 'flex', gap: 'var(--s-4)', flexWrap: 'wrap', fontSize: 'var(--t-xs)', color: 'var(--text-tertiary)' } },
            h('span', `注入位置：${LIVE_SENSE.injection.prompt_position}`),
            h('span', `章节：${LIVE_SENSE.injection.prompt_section}`),
            h('span', `${LIVE_SENSE.injection.injected_items} / ${LIVE_SENSE.injection.registered_items} 项`),
            h('span', `约 ${fmtNum(LIVE_SENSE.injection.estimated_tokens)} tokens`),
          ),
          h('div.doc-label', '实际注入 Markdown'),
          h('div.doc-content.sense-global-preview', {
            role: 'region', 'aria-label': '全局感知注入预览', tabindex: '0',
          }, renderMarkdownWithWidgets(LIVE_SENSE.injection.content || '（当前没有注入内容）')),
          LIVE_SENSE.decisions.length ? h('div',
            h('div.doc-label', { style: { marginBottom: '6px' } }, '注入决策'),
            h('div', LIVE_SENSE.decisions.map((d) => h('div', {
              style: { display: 'flex', alignItems: 'center', gap: 'var(--s-3)', padding: '7px 0', borderBottom: '1px solid var(--border-subtle)', fontSize: 'var(--t-xs)' },
            },
              h('span.mono', { style: { width: '100px', flex: 'none' } }, d.name),
              h('span.badge' + (d.decision === 'injected' ? '.badge-success' : '.badge-warning'), d.decision === 'injected' ? '注入' : '过滤'),
              h('span', { style: { color: 'var(--text-tertiary)', flex: '1' } }, d.reason),
            ))),
          ) : null,
        ),
      ));
      detailHost.appendChild(panel);
      return;
    }

    panel.appendChild(h('div.editor-head',
      h('div.module-glyph', h('span', { html: icon('sense') })),
      h('div', { style: { flex: '1', minWidth: '0' } },
        h('div.editor-title', s.display_name),
        h('div.sense-detail-source', `${s.name} · ${s.data_md || ''}`),
      ),
      s.active_for_main_agent ? h('span.badge.badge-success', '已注入') : h('span.badge.badge-warning', '已过滤'),
    ));

    const body = h('div.editor-body');
    if (s.error) body.appendChild(h('div.banner.banner-danger', { role: 'alert' }, h('span', { html: icon('alertCircle') }), h('div', s.error)));
    body.appendChild(h('div.sense-status-grid',
      [['健康状态', s.health], ['更新机制', `${s.update_interval || '—'} · ${s.start_update || '未提供更新入口'}`],
        ['最近更新', s.recent_update || fmtRelative(s.updated_at)], ['实际注入', `${fmtNum(s.injected_tokens)} tokens`]]
        .map(([label, value]) => h('div.sense-status-item', h('span', label), h('strong', value))),
    ));
    body.appendChild(h('div.sense-detail-tabs',
      h('div.segments',
        [['data', '注入数据'], ['control', '用户操作']].map(([tab, label]) =>
          h('button.segment' + (detailTab === tab ? '.is-active' : ''), {
            onclick: () => { detailTab = tab; saveView(); drawDetail(); },
          }, label)),
      ),
      moduleRefreshButton(() => Api.refreshSenseModule(Store.user, s.name), '感知信息已刷新'),
    ));

    if (detailTab === 'data') {
      body.appendChild(h('div.sense-injection-meta',
        `${s.registered_items} 项采集 · ${s.injected_items} 项注入 · ${LIVE_SENSE.injection.prompt_position}`,
      ));
      body.appendChild(h('div.sense-document-grid',
        moduleDocumentCard('模块采集信息', s.collected_markdown, '（当前没有采集数据）'),
        moduleDocumentCard('系统提示词注入片段', s.injected_markdown, '（该模块当前没有注入内容）'),
      ));
    } else {
      body.appendChild(h('div.sense-module-actions',
        h('div.sense-whitelist-card',
          h('div',
            h('strong', '当前用户白名单'),
            h('span', '决定该模块是否进入主智能体 Prompt'),
          ),
          makeSwitch(s.whitelisted, (v) => mutate(() => Api.setSenseModuleEnabled(Store.user, s.name, v), v ? '感知已启用' : '感知已禁用'), { label: '当前用户白名单' }),
        ),
        h('button.btn.btn-danger.btn-sm.sense-delete-button', {
          onclick: async () => {
            const ok = await Overlay.confirm({
              title: '删除全局感知模块',
              description: '这是全局模块，删除会影响所有用户，不只是你。目录及采集数据都会被移除。',
              detail: `global_sense/${s.name}`, confirmLabel: '删除全局模块', tone: 'danger',
            });
            if (ok) await mutate(() => Api.deleteSenseModule(Store.user, s.name), '全局感知模块已删除');
          },
        }, h('span', { html: icon('trash', 'icon-sm') }), '删除模块'),
      ));
      body.appendChild(h('div.sense-user-controls', { role: 'region', 'aria-label': '用户操控组件' },
        renderModulePanel(s.panel, {kind:'sense',name:s.name,scope:'global',panelError:s.panel_error}),
      ));
    }
    panel.appendChild(body);
    detailHost.appendChild(panel);
  }

  drawList();
  drawDetail();
  inner.appendChild(h('div.sense-layout', listHost, detailHost));
  return pageEl;
}

/* ============================================================================
   拓展 /expand
   ========================================================================= */

export const EXPAND_SCOPE_LABEL = { global: '全局拓展', shared: '共享拓展', user: '用户拓展' };

export function renderExpandPage(ctx) {
  const view = ctx.viewState.expand || (ctx.viewState.expand = {});
  let activeScope = ['global', 'shared', 'user'].includes(view.activeScope) ? view.activeScope : 'global';
  let selectedId = view.selectedId;
  let previewMode = view.previewMode || 'overview';
  let detailTab = view.detailTab || 'data';
  let page = view.page || 1;
  const dataContent = expandDataContent(LIVE_EXPANDS);

  function saveView() {
    Object.assign(view, { activeScope, selectedId, previewMode, detailTab, page });
  }

  const pageEl = h('div.page', h('div.page-inner.wide'));
  const inner = pageEl.firstElementChild;

  inner.appendChild(pageHead({
    title: '拓展',
    desc: '三层拓展：全局层由系统维护、共享层来自共享目录、用户层只属于当前用户。全局与共享层通过白名单准入，用户层默认进入管线。',
    actions: [
      h('button.btn.btn-outline', { onclick: () => refreshPage() },
        h('span', { html: icon('refresh', 'icon-sm') }), '刷新'),
      h('button.btn.btn-outline', {
        onclick: () => Bus.emit('composer:fill', { text: '创建一个新的用户拓展，扩展要求为：' }),
      }, h('span', { html: icon('plus', 'icon-sm') }), '增加到用户层'),
    ],
  }));

  inner.appendChild(h('div.stat-row',
    statTile('拓展模块', LIVE_EXPANDS.summary.total, { icon: 'expand', foot: '三层合计' }),
    statTile('已启用', LIVE_EXPANDS.status_summary.enabled, { icon: 'checkCircle', color: 'var(--success)' }),
    statTile('注入 Tokens', LIVE_EXPANDS.injection.estimated_tokens, {
      icon: 'activity', unit: 'tok',
      foot: `${LIVE_EXPANDS.injection.injected_items}/${LIVE_EXPANDS.injection.original_items} 项 · ${LIVE_EXPANDS.injection.truncated ? '已截断' : '未截断'}`,
    }),
    statTile('无效模块', LIVE_EXPANDS.status_summary.invalid, {
      icon: 'alertTriangle', color: LIVE_EXPANDS.status_summary.invalid ? 'var(--warning)' : null,
      foot: LIVE_EXPANDS.status_summary.invalid ? '注册文件解析失败' : '全部有效',
    }),
  ));

  const tabBar = h('div.tabs', ['global', 'shared', 'user'].map((sc) =>
    h('button.tab' + (sc === activeScope ? '.is-active' : ''), {
      onclick: (ev) => {
        activeScope = sc;
        page = 1;
        selectedId = undefined;
        detailTab = 'data';
        $$('.tab', tabBar).forEach((x) => x.classList.remove('is-active'));
        ev.currentTarget.classList.add('is-active');
        drawList(); drawDetail();
      },
    }, EXPAND_SCOPE_LABEL[sc],
      h('span.tab-count', String(LIVE_EXPANDS.summary[sc]))),
  ));
  inner.appendChild(tabBar);

  const listHost = h('div');
  const detailHost = h('div');

  function itemsOf(scope) {
    const grp = LIVE_EXPANDS.expands.find((g) => g.scope === scope);
    return grp ? grp.items : [];
  }

  function drawList() {
    fill(listHost);
    const items = itemsOf(activeScope);
    const totalPages = Math.max(1, Math.ceil(items.length / MODULES_PER_PAGE));
    page = clamp(page, 1, totalPages);
    const visible = items.slice((page - 1) * MODULES_PER_PAGE, page * MODULES_PER_PAGE);
    if (!visible.some(m => m.id === selectedId)) {
      selectedId = visible[0]?.id;
      detailTab = 'data';
    }
    saveView();
    const scopeDesc = {
      global: '系统级模块，由全局白名单决定是否启用',
      shared: '来自共享目录，可被多个用户使用',
      user: '只属于当前用户，默认进入管线',
    }[activeScope];

    listHost.appendChild(h('div', { style: { marginBottom: 'var(--s-3)' } },
      h('div.section-label', { style: { padding: '0' } },
        h('span', { html: icon('expand', 'icon-sm') }), EXPAND_SCOPE_LABEL[activeScope]),
      h('div', { style: { fontSize: 'var(--t-xs)', color: 'var(--text-tertiary)', marginTop: '4px' } }, scopeDesc),
    ));

    if (!items.length) {
      listHost.appendChild(emptyState({
        icon: 'expand',
        title: `尚无${EXPAND_SCOPE_LABEL[activeScope]}`,
        desc: '标准模块目录创建后，点击刷新即可通过热加载发现。',
      }));
      return;
    }

    const list = h('div', { style: { display: 'flex', flexDirection: 'column', gap: 'var(--s-3)' } });
    visible.forEach((m) => {
      const card = h('div.module-card' + (selectedId === m.id ? '.is-active' : ''), {
        onclick: () => { selectedId = m.id; previewMode = 'detail'; detailTab = 'data'; saveView(); drawList(); drawDetail(); },
      },
        h('div.module-glyph', { style: m.valid ? {} : { background: 'var(--danger-soft)', color: 'var(--danger)' } },
          h('span', { html: icon(m.valid ? 'package' : 'alertTriangle', 'icon-sm') })),
        h('div', { style: { flex: '1', minWidth: '0' } },
          h('div.module-name', m.display_name,
            m.active_for_main_agent ? h('span.badge.badge-success', '已注入')
              : m.valid ? h('span.badge', '已过滤') : h('span.badge.badge-danger', '无效'),
          ),
          h('div.module-id', `ID：${m.name} · ${m.relative_path}`),
          m.error
            ? h('div', { style: { fontSize: 'var(--t-xs)', color: 'var(--danger)', marginTop: '6px', lineHeight: '1.5' } }, m.error)
            : h('div.module-preview', m.description),
          h('div.module-meta',
            h('span', '数据 ' + (m.open_input ? '开启' : '关闭')),
            h('span', '·'),
            h('span', m.open_control ? '控制开启' : '控制关闭'),
            h('span', '·'),
            h('span', { style: { color: m.input_health === '正常' ? 'var(--success)' : 'var(--danger)' } }, m.input_health),
            m.injected_tokens ? h('span.badge', fmtNum(m.injected_tokens) + ' tok') : null,
          ),
        ),
      );
      list.appendChild(card);
    });
    listHost.appendChild(list);
    const pager = makePager(page, totalPages, (p) => { page = p; drawList(); drawDetail(); });
    if (pager) listHost.appendChild(pager);
  }

  function drawDetail() {
    fill(detailHost);
    const m = itemsOf(activeScope).find(item => item.id === selectedId);
    if (!m) previewMode = 'overview';
    detailHost.appendChild(h('div.sense-preview-tabs',
      h('div.segments',
        [['overview', '注入总览'], ['detail', '模块详情']].map(([mode, label]) =>
          h('button.segment' + (previewMode === mode ? '.is-active' : ''), {
            disabled: mode === 'detail' && !m,
            onclick: () => { previewMode = mode; saveView(); drawDetail(); },
          }, label)),
      ),
    ));
    const panel = h('div.editor-panel.sense-detail-panel.expand-detail-panel');

    if (previewMode === 'overview' || !m) {
      panel.appendChild(h('div.editor-head',
        h('div.editor-title', '拓展数据注入预览'),
        h('span.badge', '三层数据 · 只读'),
      ));
      panel.appendChild(h('div.editor-body',
        h('div.sense-injection-meta',
          `${fmtNum(dataContent.length)} 字符 · ${LIVE_EXPANDS.injection.prompt_position}`,
        ),
        h('div.doc-content.sense-global-preview', {
          role: 'region', 'aria-label': '拓展注入预览', tabindex: '0',
        }, renderMarkdownWithWidgets(dataContent || '（当前没有数据注入）', { readOnly: true })),
      ));
      detailHost.appendChild(panel);
      return;
    }

    panel.appendChild(h('div.editor-head',
      h('div.module-glyph', h('span', { html: icon('expand') })),
      h('div', { style: { flex: '1', minWidth: '0' } },
        h('div.editor-title', m.display_name),
        h('div.sense-detail-source', `${EXPAND_SCOPE_LABEL[m.scope]} · ${m.path}`),
      ),
      m.active_for_main_agent ? h('span.badge.badge-success', '已注入') : h('span.badge', '已过滤'),
    ));

    const body = h('div.editor-body');
    if (m.error) body.appendChild(h('div.banner.banner-danger', { role: 'alert' },
      h('span', { html: icon('alertCircle') }), h('div', m.error)));
    if (m.description) body.appendChild(h('div.expand-detail-description', renderMarkdownWithWidgets(m.description)));
    body.appendChild(h('div.sense-status-grid',
      [['输入健康', m.input_health], ['更新入口', m.start_update || '未提供更新入口'],
        ['最近更新', m.runtime?.update?.last_success || fmtDateTime(m.updated_at ? m.updated_at * 1000 : null)],
        ['实际数据注入', `${fmtNum(m.data_injected_tokens ?? expandDataFragment(m).length)} ${m.data_injected_tokens === undefined ? '字符' : 'tokens'}`],
        ['数据输入', m.open_input ? '开启' : '关闭'], ['控制入口', m.open_control ? '开启' : '关闭']]
        .map(([label, value]) => h('div.sense-status-item', h('span', label), h('strong', value))),
    ));
    body.appendChild(h('div.sense-detail-tabs',
      h('div.segments',
        [['data', '数据注入'], ['docs', '操作文档'], ['control', '用户配置']].map(([tab, label]) =>
          h('button.segment' + (detailTab === tab ? '.is-active' : ''), {
            onclick: () => { detailTab = tab; saveView(); drawDetail(); },
          }, label)),
      ),
      moduleRefreshButton(() => Api.refreshExpandModule(Store.user, m.scope, m.name), '拓展采集数据已更新'),
    ));

    if (detailTab === 'data') {
      body.appendChild(h('div.sense-injection-meta', `${EXPAND_SCOPE_LABEL[m.scope]} · ${LIVE_EXPANDS.injection.prompt_position}`));
      body.appendChild(h('div.sense-document-grid',
        moduleDocumentCard('模块采集信息', m.collected_markdown, '（当前没有采集数据）', { readOnly: true }),
        moduleDocumentCard('系统提示词注入片段', expandDataFragment(m), '（该模块当前没有数据注入）', { readOnly: true }),
      ));
    } else if (detailTab === 'docs') {
      body.appendChild(h('div.expand-document-stack',
        moduleDocumentCard('完整操作文档', m.control_document, '（未声明操作文档）'),
        h('div.sense-document-grid',
          moduleDocumentCard('注入层文档', m.control_injection_markdown, '（无注入层文档）'),
          moduleDocumentCard('操作层文档', m.control_operation_markdown, '（无操作层文档）'),
        ),
      ));
    } else {
      body.appendChild(h('div.sense-module-actions',
        h('div.sense-whitelist-card',
          h('div', h('strong', '当前用户准入'),
            h('span', m.scope === 'user' ? '用户层始终可用，默认进入管线' : '仅控制当前用户对该模块的准入')),
          makeSwitch(m.whitelisted,
            v => mutate(() => Api.setExpandModuleEnabled(Store.user, m.scope, m.name, v), v ? '拓展已启用' : '拓展已禁用'),
            { label: '当前用户准入', disabled: m.scope === 'user' || !m.valid }),
        ),
        m.scope === 'user' ? h('button.btn.btn-danger.btn-sm.sense-delete-button', {
          onclick: async () => {
            const ok = await Overlay.confirm({
              title: '删除用户拓展',
              description: '只会删除当前用户的拓展模块，不影响全局层与共享层。',
              detail: m.path, confirmLabel: '删除', tone: 'danger',
            });
            if (ok) await mutate(() => Api.deleteExpandModule(Store.user, m.name), '用户拓展已删除');
          },
        }, h('span', { html: icon('trash', 'icon-sm') }), '删除模块') : null,
      ));
      body.appendChild(h('div.sense-user-controls.expand-user-controls', { role: 'region', 'aria-label': '用户操控组件' },
        renderModulePanel(m.panel, { kind: 'expand', name: m.name, scope: m.scope, panelError: m.panel_error }),
      ));
    }

    panel.appendChild(body);
    detailHost.appendChild(panel);
  }

  drawList();
  drawDetail();
  inner.appendChild(h('div.sense-layout.expand-layout', listHost, detailHost));
  return pageEl;
}


