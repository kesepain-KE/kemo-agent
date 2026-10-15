import { LIVE_AGENTS, h, pageHead, refreshPage, icon, $$, statTile, fill, makeSearchBox, emptyState, copyText, fmtBytes, fmtRelative, Overlay, mutate, Api, Store, navigate, append, chooseFile, LIVE_SKILLS, makeSwitch, renderMarkdownWithWidgets, downloadUrl, clamp, makePager } from './shared.js'
/* ============================================================================
   pages/agents-skills · 子智能体 / 工具与技能
   ========================================================================= */

export function renderAgentsPage(ctx) {
  let activeLayer = 'all';
  let query = '';
  let selected = LIVE_AGENTS.agents[0] || null;
  let page = 1;
  const PER_PAGE = 6;

  const pageEl = h('div.page', h('div.page-inner.wide'));
  const inner = pageEl.firstElementChild;

  inner.appendChild(pageHead({
    title: '子智能体',
    desc: '全局层内置子智能体与用户层自定义子智能体。网页没有创建或修改子智能体的接口，用户层可以删除整包。',
    actions: [
      h('button.btn.btn-outline', { onclick: () => { void refreshPage(); } },
        h('span', { html: icon('refresh', 'icon-sm') }), '刷新'),
    ],
  }));

  const listHost = h('div');
  const detailHost = h('div');

  const tabBar = h('div.tabs', [
    { key: 'all', label: '全部', count: LIVE_AGENTS.summary.total },
    { key: 'global', label: '全局层', count: LIVE_AGENTS.summary.global },
    { key: 'user', label: '用户层', count: LIVE_AGENTS.summary.user },
  ].map((t) => h('button.tab' + (t.key === activeLayer ? '.is-active' : ''), {
    onclick: (ev) => {
      activeLayer = t.key;
      page = 1;
      $$('.tab', tabBar).forEach((x) => x.classList.remove('is-active'));
      ev.currentTarget.classList.add('is-active');
      draw();
    },
  }, t.label, h('span.tab-count', String(t.count)))));
  inner.appendChild(tabBar);

  inner.appendChild(h('div.stat-row',
    statTile('已发现', LIVE_AGENTS.summary.total, { icon: 'agents', foot: '全局层 + 用户层' }),
    statTile('已启用', LIVE_AGENTS.summary.enabled, { icon: 'checkCircle', color: 'var(--success)', foot: '参与主智能体调度' }),
    statTile('全局层', LIVE_AGENTS.summary.global, { icon: 'globe', foot: '内置，不可删除' }),
    statTile('用户层', LIVE_AGENTS.summary.user, { icon: 'user', foot: '可删除整包' }),
  ));

  const split = h('div.split.agents-split');
  inner.appendChild(split);
  const search = makeSearchBox({
    placeholder: '搜索子智能体名称或描述…',
    onInput: (v) => { query = v.toLowerCase(); page = 1; draw(); },
  });
  listHost.appendChild(h('div.toolbar', search));
  const rowsHost = h('div');
  listHost.appendChild(rowsHost);

  function draw() {
    fill(rowsHost);
    const rows = LIVE_AGENTS.agents.filter((a) => {
      if (activeLayer !== 'all' && a.source !== activeLayer) return false;
      if (query && !(a.name + a.description).toLowerCase().includes(query)) return false;
      return true;
    });

    const totalPages = Math.max(1, Math.ceil(rows.length / PER_PAGE));
    page = clamp(page, 1, totalPages);
    const visible = rows.slice((page - 1) * PER_PAGE, page * PER_PAGE);
    if (!visible.includes(selected)) {
      selected = visible[0] || null;
      drawDetail();
    }

    if (!rows.length) {
      rowsHost.appendChild(emptyState({
        icon: 'agents',
        title: '没有找到匹配的子智能体',
        desc: '调整搜索词或切换层级后重试。',
      }));
      return;
    }

    const list = h('div', { style: { display: 'flex', flexDirection: 'column', gap: 'var(--s-3)' } });
    visible.forEach((a) => {
      const item = h('div.agent-item' + (selected === a ? '.is-active' : ''), {
        onclick: () => { selected = a; draw(); drawDetail(); },
      },
        h('div.agent-avatar', h('span', { html: icon(a.source === 'global' ? 'globe' : 'user', 'icon-sm') })),
        h('div', { style: { flex: '1', minWidth: '0' } },
          h('div', { style: { display: 'flex', alignItems: 'center', gap: '6px' } },
            h('span', { style: { fontSize: 'var(--t-sm)', fontWeight: '570' } }, a.name),
            h('span.badge.badge-outline', 'v' + a.version),
            a.enabled ? h('span.badge.badge-success', '已启用') : h('span.badge', '已停用'),
          ),
          h('div', { style: { fontSize: 'var(--t-xs)', color: 'var(--text-tertiary)', marginTop: '4px', lineHeight: '1.55' } }, a.description),
          h('div', { style: { display: 'flex', alignItems: 'center', gap: 'var(--s-3)', marginTop: '6px' } },
            h('span', { class: 'scope-tag scope-' + (a.source === 'global' ? 'global' : 'user') }, a.source === 'global' ? '全局层' : '用户层'),
            h('span', { style: { fontSize: 'var(--t-2xs)', color: 'var(--text-faint)' } }, '执行器 ' + a.executor),
            h('span', { style: { fontSize: 'var(--t-2xs)', color: 'var(--text-faint)' } }, '模型档 ' + a.model_profile),
          ),
        ),
        h('button.chip-x', {
          title: '复制名称', 'aria-label': '复制名称',
          onclick: (ev) => { ev.stopPropagation(); void copyText(a.name); },
        }, h('span', { html: icon('copy', 'icon-sm') })),
      );
      list.appendChild(item);
    });
    rowsHost.appendChild(list);
    const pager = makePager(page, totalPages, (p) => { page = p; draw(); });
    if (pager) rowsHost.appendChild(pager);
  }

  function drawDetail() {
    fill(detailHost);
    if (!selected) {
      detailHost.appendChild(h('div.editor-panel',
        h('div.editor-head', h('div.editor-title', '子智能体详情')),
        h('div.editor-body', emptyState({ title: '暂无可查看的子智能体', desc: '当前层级中没有符合条件的子智能体。', icon: 'agents' })),
      ));
      return;
    }
    const a = selected;
    detailHost.appendChild(h('div.editor-panel',
      h('div.editor-head',
        h('div', { style: { flex: '1', minWidth: '0' } },
          h('div.editor-title', a.name),
          h('div', { style: { fontSize: 'var(--t-2xs)', color: 'var(--text-faint)', marginTop: '2px' } }, `v${a.version} · ${a.root}`),
        ),
        a.enabled ? h('span.badge.badge-success', '已启用') : h('span.badge', '已停用'),
      ),
      h('div.editor-body',
        h('div', { style: { display: 'flex', flexDirection: 'column', gap: 'var(--s-4)' } },
          h('div.desc-list',
            h('div.desc-row', h('span.desc-key', '来源层级'), h('span.desc-val', a.source === 'global' ? '全局层（内置）' : '用户层')),
            h('div.desc-row', h('span.desc-key', '执行器'), h('span.desc-val', a.executor)),
            h('div.desc-row', h('span.desc-key', '执行方式'), h('span.desc-val', a.execution)),
            h('div.desc-row', h('span.desc-key', '模型档位'), h('span.desc-val', a.model_profile)),
            h('div.desc-row', h('span.desc-key', '暴露方式'), h('span.desc-val', a.exposure)),
            h('div.desc-row', h('span.desc-key', '注册目录'), h('span.desc-val.mono', a.root)),
          ),
          h('div.doc-block',
            h('div.doc-label', '描述'),
            h('div.doc-content', renderMarkdownWithWidgets(a.description)),
          ),
          h('div.doc-block',
            h('div.doc-label', '触发文档'),
            h('div.doc-content', renderMarkdownWithWidgets(a.trigger || '（未声明触发条件）')),
          ),
          h('div.doc-block',
            h('div.doc-label', '配置与规则'),
            h('div.doc-content', renderMarkdownWithWidgets(a.rules || '（未声明规则）')),
          ),
        ),
      ),
      h('div.editor-foot',
        a.source === 'user'
          ? h('button.btn.btn-danger.btn-sm', {
            onclick: async () => {
              const ok = await Overlay.confirm({
                title: '删除用户子智能体',
                description: '会永久删除该用户的整个包目录，包括配置文件与文档。内置全局层不可删除。',
                detail: a.root, confirmLabel: '永久删除', tone: 'danger',
              });
              if (ok) await mutate(() => Api.deleteUserAgent(Store.user, a.name), '已删除用户子智能体');
            },
          }, h('span', { html: icon('trash', 'icon-sm') }), '删除用户包')
          : h('span', { style: { fontSize: 'var(--t-2xs)', color: 'var(--text-faint)' } }, '全局层内置子智能体不可通过用户接口删除。'),
        h('div', { style: { flex: '1' } }),
        h('button.btn.btn-ghost.btn-sm', {
          onclick: () => { void copyText(a.name); },
        }, h('span', { html: icon('copy', 'icon-sm') }), '复制名称'),
        h('button.btn.btn-ghost.btn-sm', { onclick: () => navigate('/chat', { prompt: `请使用子智能体 ${a.name}，先向我确认需要执行的目标。` }) }, '手动触发'),
      ),
    ));
  }

  draw();
  drawDetail();
  split.append(listHost, detailHost);
  return pageEl;
}

/* ============================================================================
   pages/skills · 工具与技能
   ========================================================================= */

export const SKILL_CATEGORIES = [
  { key: 'all', label: '全部技能' },
  { key: 'builtin', label: '基础插件' },
  { key: 'shared', label: '共享技能' },
  { key: 'agent_generated', label: '智能体生成' },
  { key: 'user_created', label: '用户自建' },
];

export function renderSkillsPage(ctx) {
  let category = 'all';
  let query = '';
  let selected = null;
  let previewMode = 'preview';
  let docContent = '';
  let docLoading = false;
  let docError = '';
  let page = 1;
  let selectionRevision = 0;
  const PER_PAGE = 6;

  const pageEl = h('div.page', h('div.page-inner.wide'));
  const inner = pageEl.firstElementChild;

  inner.appendChild(pageHead({
    title: '工具与技能',
    desc: 'builtin 是可执行工具，其余分类主要是 Prompt 技能。是否可编辑、可切换、可下载由每项的 editable / toggleable / downloadable 决定，不能用一个开关统管。',
    actions: [
      h('button.btn.btn-outline', {
        onclick: () => chooseFile('.zip', 32, file => mutate(() => Api.uploadUserSkills(Store.user, file), '技能 ZIP 已导入')),
      }, h('span', { html: icon('upload', 'icon-sm') }), '导入技能 ZIP'),
      h('button.btn.btn-outline', { onclick: () => { void refreshPage(); } },
        h('span', { html: icon('refresh', 'icon-sm') }), '刷新'),
    ],
  }));

  inner.appendChild(h('div.stat-row',
    statTile('注册总数', LIVE_SKILLS.catalog_summary.total, { icon: 'package', foot: '工具 + Prompt 技能' }),
    statTile('已启用', LIVE_SKILLS.catalog_summary.enabled, { icon: 'checkCircle', color: 'var(--success)' }),
    statTile('可执行工具', LIVE_SKILLS.catalog_summary.builtin, { icon: 'terminal', foot: 'builtin 插件' }),
    statTile('Prompt 技能', LIVE_SKILLS.prompt_summary.registered, { icon: 'sparkles', foot: `${LIVE_SKILLS.prompt_summary.active} 项已激活` }),
  ));

  const tabBar = h('div.tabs', SKILL_CATEGORIES.map((c) =>
    h('button.tab' + (c.key === category ? '.is-active' : ''), {
      onclick: (ev) => {
        category = c.key;
        page = 1;
        $$('.tab', tabBar).forEach((x) => x.classList.remove('is-active'));
        ev.currentTarget.classList.add('is-active');
        draw();
      },
    }, c.label,
      h('span.tab-count', String(c.key === 'all'
        ? LIVE_SKILLS.catalog_summary.total
        : LIVE_SKILLS.catalog_summary[c.key] ?? 0))),
  ));
  inner.appendChild(tabBar);

  const listHost = h('div');
  const detailHost = h('div');

  const allItems = LIVE_SKILLS.items;
  const count = h('span.section-count');
  const search = makeSearchBox({
    placeholder: '按名称、描述或路径搜索…',
    onInput: (v) => { query = v.toLowerCase(); page = 1; draw(); },
  });
  const card = h('div.card', { style: { overflow: 'hidden' } },
    h('div.card-head',
      h('div.section-title', { style: { fontSize: 'var(--t-sm)' } }, '技能列表', count),
      search,
    ),
  );
  const rowsHost = h('div');
  const pagerHost = h('div');
  card.appendChild(rowsHost);
  listHost.append(card, pagerHost);

  function selectSkill(s) {
    selected = s;
    docContent = '';
    docError = '';
    docLoading = !!s;
    previewMode = 'preview';
    const revision = ++selectionRevision;
    drawDetail();
    if (s) void loadSkillDocument(s, revision);
  }

  async function loadSkillDocument(s, revision) {
    try {
      const result = await Api.getSkillDocument(Store.user, s.category, s.name);
      if (ctx.active && selected === s && selectionRevision === revision) docContent = result.content;
    } catch (error) {
      if (ctx.active && selected === s && selectionRevision === revision) {
        docError = error instanceof Error ? error.message : 'SKILL.md 读取失败';
      }
    } finally {
      if (ctx.active && selected === s && selectionRevision === revision) {
        docLoading = false;
        drawDetail();
      }
    }
  }

  function draw() {
    fill(rowsHost);
    fill(pagerHost);
    const rows = allItems.filter((s) => {
      if (category !== 'all' && s.category !== category) return false;
      if (query && ![s.name, s.title, s.description, s.path].join(' ').toLowerCase().includes(query)) return false;
      return true;
    });

    const totalPages = Math.max(1, Math.ceil(rows.length / PER_PAGE));
    page = clamp(page, 1, totalPages);
    const visible = rows.slice((page - 1) * PER_PAGE, page * PER_PAGE);
    count.textContent = `当前显示 ${visible.length} / ${rows.length} 个项目`;
    if (!visible.includes(selected)) selectSkill(visible[0] || null);

    if (!rows.length) {
      rowsHost.appendChild(emptyState({
        icon: 'skills',
        title: query ? '没有找到匹配的技能' : '当前分类暂无技能',
        desc: query ? '请尝试其他名称、描述或路径关键词。' : '该技能目录中尚未注册有效的 SKILL.md。',
      }));
      return;
    }

    const list = h('div.list', {
      style: {
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--s-2)',
        padding: 'var(--s-2)',
      },
    });
    visible.forEach((s) => {
      const isActive = selected && selected.id === s.id;
      const row = h('div.skill-item' + (isActive ? '.is-active' : ''), {
        onclick: () => {
          selectSkill(s);
          draw();
        },
      },
        h('div.file-icon' + (s.category === 'builtin' ? '' : '.is-image'), {
          style: { background: s.category === 'builtin' ? 'var(--bg-active)' : 'var(--accent-soft)', color: s.category === 'builtin' ? 'var(--text-secondary)' : 'var(--accent-text)' },
        }, h('span', { html: icon(s.category === 'builtin' ? 'terminal' : 'sparkles', 'icon-sm') })),
        h('div', { style: { flex: '1', minWidth: '0' } },
          h('div', { style: { display: 'flex', alignItems: 'center', gap: '6px' } },
            h('span', { style: { fontSize: 'var(--t-sm)', fontWeight: '540' } }, s.title),
            h('span.skill-code', s.name),
            s.version && s.version !== '—' ? h('span.badge.badge-outline', s.version) : null,
          ),
          h('div', { style: { fontSize: 'var(--t-xs)', color: 'var(--text-tertiary)', marginTop: '3px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, s.description),
          h('div', { style: { display: 'flex', gap: '5px', marginTop: '5px', flexWrap: 'wrap' } },
            h('span', { class: 'scope-tag ' + (s.category === 'builtin' ? 'scope-global' : s.category === 'shared' ? 'scope-shared' : 'scope-user') },
              (SKILL_CATEGORIES.find((c) => c.key === s.category) || {}).label || s.category),
            s.editable ? h('span.badge', '可编辑') : null,
            s.toggleable ? h('span.badge', '可切换') : null,
            s.downloadable ? h('span.badge', '可下载') : null,
            s.memory_lifecycle?.status === 'stale' ? h('span.badge.badge-warning', '来源已失效 · 待复核') : null,
          ),
        ),
        s.toggleable
          ? makeSwitch(s.enabled, (v) => mutate(() => Api.setSkillEnabled(Store.user, s.category, s.name, v), v ? '技能已启用' : '技能已禁用'), { label: '启用开关' })
          : h('span', { style: { fontSize: 'var(--t-2xs)', color: 'var(--text-faint)', flex: 'none' } }, s.enabled ? '内置' : '未启用'),
      );
      list.appendChild(row);
    });
    rowsHost.appendChild(list);
    const pager = makePager(page, totalPages, (p) => { page = p; draw(); });
    if (pager) pagerHost.appendChild(pager);
  }

  function drawDetail() {
    fill(detailHost);
    if (!selected) {
      detailHost.appendChild(h('div.editor-panel',
        h('div.editor-head', h('div.editor-title', '技能详情')),
        h('div.editor-body', emptyState({ title: '暂无可查看的技能', desc: '从左侧选择一个技能查看说明与 SKILL.md。', icon: 'skills' })),
      ));
      return;
    }
    const s = selected;
    const panel = h('div.editor-panel');

    panel.appendChild(h('div.editor-head',
      h('div', { style: { flex: '1', minWidth: '0' } },
        h('div.editor-title', s.title),
        h('div', { style: { fontSize: 'var(--t-2xs)', color: 'var(--text-faint)', marginTop: '2px', fontFamily: 'var(--font-mono)' } }, s.path),
      ),
      h('span.skill-code', s.name),
    ));

    const body = h('div.editor-body');
    body.appendChild(h('div.desc-list', { style: { marginBottom: 'var(--s-4)' } },
      h('div.desc-row', h('span.desc-key', '分类'), h('span.desc-val', (SKILL_CATEGORIES.find((c) => c.key === s.category) || {}).label || s.category)),
      h('div.desc-row', h('span.desc-key', '版本'), h('span.desc-val', s.version || '—')),
      h('div.desc-row', h('span.desc-key', '当前状态'), h('span.desc-val', s.memory_lifecycle?.status === 'stale' ? '来源已失效 · 待复核' : s.enabled ? h('span', { style: { color: 'var(--success)' } }, '已启用') : '未启用')),
      s.memory_lifecycle ? h('div.desc-row', h('span.desc-key', '记忆来源'), h('span.desc-val',
        s.memory_lifecycle.sources.map(source => source.filename).join('、'),
        s.memory_lifecycle.reason ? `；${s.memory_lifecycle.reason}` : '；来源有效')) : null,
      h('div.desc-row', h('span.desc-key', '说明'), h('div.desc-val', renderMarkdownWithWidgets(s.description))),
    ));

    body.appendChild(h('div', { style: { display: 'flex', alignItems: 'center', gap: 'var(--s-2)', marginBottom: 'var(--s-3)' } },
      h('div.segments',
        h('button.segment' + (previewMode === 'preview' ? '.is-active' : ''), { onclick: () => { previewMode = 'preview'; drawDetail(); } }, '预览'),
        h('button.segment' + (previewMode === 'edit' ? '.is-active' : ''), {
          onclick: () => { if (!s.editable || docLoading || docError) return; previewMode = 'edit'; drawDetail(); },
          disabled: !s.editable || docLoading || !!docError,
          style: s.editable ? {} : { opacity: '0.45' },
        }, '编辑'),
      ),
      h('div.spacer'),
      s.toggleable ? h('div', { style: { display: 'flex', alignItems: 'center', gap: '7px', fontSize: 'var(--t-xs)' } },
        h('span.muted', s.enabled ? '已启用' : '已禁用'),
        makeSwitch(s.enabled, (v) => mutate(() => Api.setSkillEnabled(Store.user, s.category, s.name, v), v ? '技能已启用' : '技能已禁用')),
      ) : null,
    ));

    if (previewMode === 'edit') {
      const ta = h('textarea.textarea.mono', { rows: 16, spellcheck: 'false', oninput: (e) => { docContent = e.target.value; } });
      ta.value = docContent;
      body.appendChild(ta);
    } else {
      body.appendChild(h('div.doc-content.skill-document-preview', {
        role: 'region', 'aria-label': 'SKILL.md 预览', 'aria-busy': String(docLoading), tabindex: '0',
      },
        docLoading ? h('div', { role: 'status' }, '正在读取 SKILL.md…')
          : docError ? h('div.banner.banner-danger', { role: 'alert' },
            h('div', docError),
            h('button.btn.btn-outline.btn-sm', { onclick: () => selectSkill(s) }, '重新读取'),
          )
          : renderMarkdownWithWidgets(docContent || 'SKILL.md 文件为空。'),
      ));
    }

    panel.appendChild(body);
    panel.appendChild(h('div.editor-foot',
      h('div', { style: { flex: '1' } }),
      s.downloadable ? h('button.btn.btn-ghost.btn-sm', {
        onclick: () => downloadUrl(Api.getSkillDownloadUrl(Store.user, s.category, s.name)),
      }, h('span', { html: icon('download', 'icon-sm') }), '下载 ZIP') : null,
      s.editable ? h('button.btn.btn-danger.btn-sm', {
        onclick: async () => {
          const ok = await Overlay.confirm({
            title: '删除技能包',
            description: '删除的是整个技能包目录，不只是 SKILL.md。',
            detail: s.path, confirmLabel: '删除整包', tone: 'danger',
          });
          if (ok) await mutate(() => Api.deleteSkill(Store.user, s.category, s.name), '技能包已删除');
        },
      }, h('span', { html: icon('trash', 'icon-sm') }), '删除') : null,
      h('button.btn.btn-primary.btn-sm', {
        disabled: !s.editable || docLoading || !!docError,
        onclick: () => mutate(() => Api.putSkillDocument(Store.user, s.category, s.name, docContent), 'SKILL.md 已保存'),
      }, '保存'),
    ));

    detailHost.appendChild(panel);
  }

  inner.appendChild(h('div.split.skills-split', listHost, detailHost));
  draw();
  return pageEl;
}


