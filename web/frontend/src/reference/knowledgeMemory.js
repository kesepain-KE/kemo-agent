import { t as tr } from '../i18n'
import { h, pageHead, chooseFile, mutate, Api, Store, icon, onClickOutside, toast, navigate, LIVE_KNOWLEDGE, $$, makeSearchBox, append, fill, clamp, emptyState, readDetail, truncateMiddle, fmtBytes, fmtRelative, makePager, renderMarkdownWithWidgets, Overlay, LIVE_MEMORY, LIVE_IMPORTANT_MEMORY, statusBadge, statusText, fmtDateTime, refreshPage, closeOverlays } from './shared.js'
import { getKnowledgeCategories, getKnowledgeCategoryFilters, knowledgeCategoryKey } from '../knowledgeCategories'
/* ============================================================================
   pages/knowledge · 三级知识库
   ========================================================================= */

export const SCOPE_LABEL = {
  get user() { return tr('用户层'); },
  get shared() { return tr('共享层'); },
  get global() { return tr('全局层'); },
};

export function renderKnowledgePage(ctx) {
  let activeScope = 'all';
  let activeCategory = 'all';
  let query = '';
  let selected = null;
  let draft = '';
  let categoryDraft = '';
  let newCategoryDraft = '';
  let previewMode = 'preview';
  let editorSizeObserver = null;
  ctx.registerCleanup?.(() => editorSizeObserver?.disconnect());
  let page = 1;
  const PER_PAGE = 10;

  const pageEl = h('div.page', h('div.page-inner.wide'));
  const inner = pageEl.firstElementChild;

  const listHost = h('div');
  const categoryHost = h('div.knowledge-category-host');
  const editorHost = h('div');
  const UNCATEGORIZED = '未分类';

  function drawCategories() {
    fill(categoryHost);
    const allCount = LIVE_KNOWLEDGE.documents.filter((document) => (
      activeScope === 'all' || document.scope === activeScope
    )).length;
    const items = [
      { label: tr('全部分类'), value: 'all', count: allCount },
      ...getKnowledgeCategoryFilters(LIVE_KNOWLEDGE, activeScope),
    ];
    const categorySelect = h('select.select.knowledge-category-filter', {
      'aria-label': tr('筛选知识分类'),
      onchange: (event) => { activeCategory = event.target.value; page = 1; draw(); },
    }, items.map((item) => h('option', {
      value: item.value,
      selected: activeCategory === item.value,
    }, `${item.label}（${item.count}）`)));
    const createInput = h('input.input.knowledge-category-new-input', {
      value: newCategoryDraft,
      placeholder: '分类名…',
      'aria-label': '新的分类名称',
      oninput: (event) => {
        newCategoryDraft = event.target.value;
        createButton.disabled = !selected || !newCategoryDraft.trim();
      },
    });
    const createButton = h('button.btn.btn-outline.btn-sm', {
      type: 'button',
      'aria-label': '新建分类名',
      disabled: !selected || !newCategoryDraft.trim(),
      onclick: () => {
        const value = newCategoryDraft.trim();
        if (!selected || !value) return;
        void mutate(
          () => Api.setKnowledgeCategory(Store.user, selected.scope, selected.relative_path, value),
          `已创建分类“${value}”并归类当前文件`,
          () => { categoryDraft = value; newCategoryDraft = ''; },
        );
      },
    }, tr('新建分类'));
    categoryHost.append(
      categorySelect,
      h('div.knowledge-category-create-controls',
        createInput,
        createButton,
      ),
    );
  }

  inner.appendChild(pageHead({
    title: tr('知识库'),
    desc: '三级目录分别是用户层、共享层与全局层。检索模式为 index_only ── 正文不会自动全量注入 Prompt，只有被引用时才进入上下文。',
    actions: [
      h('button.btn.btn-outline', {
        onclick: (ev) => {
          const menu = h('div.menu',
            h('div.menu-label', '选择导入层级'),
            ['user', 'shared'].map((s) => h('button.menu-item', {
              onclick: () => { menu.remove(); chooseFile('.md,.txt,.json', 80, file => mutate(async () => Api.putKnowledgeDocument(Store.user, s, file.name, await file.text()), '知识文件已导入')); },
            }, h('span', { html: icon('upload') }), h('span.mi-label', '导入到' + SCOPE_LABEL[s]))),
            h('div.menu-sep'),
            h('div', { style: { padding: '6px 9px', fontSize: 'var(--t-2xs)', color: 'var(--text-faint)', lineHeight: '1.5' } },
              '全局层在界面上不提供导入入口。'),
          );
          const r = ev.currentTarget.getBoundingClientRect();
          menu.style.position = 'fixed';
          menu.style.left = r.left + 'px';
          menu.style.top = r.bottom + 4 + 'px';
          document.body.appendChild(menu);
          onClickOutside(menu, () => menu.remove());
        },
      }, h('span', { html: icon('upload', 'icon-sm') }), '导入文件'),
      h('button.btn.btn-outline', {
        onclick: () => { toast('已跳转到对话以刷新索引', { detail: '刷新全部索引通过对话执行，不是 /knowledge/refresh 接口' }); navigate('/chat', { prompt: '刷新知识库全部索引。' }); },
      }, h('span', { html: icon('refresh', 'icon-sm') }), '刷新全部索引'),
    ],
  }));

  /* 工具栏 + 分页 */
  const toolbar = h('div.toolbar.knowledge-toolbar');
  const tabBar = h('div.tabs', ['all', 'user', 'shared', 'global'].map((s) =>
    h('button.tab' + (s === activeScope ? '.is-active' : ''), {
      onclick: (ev) => {
        activeScope = s; activeCategory = 'all'; page = 1;
        $$('.tab', tabBar).forEach((x) => x.classList.remove('is-active'));
        ev.currentTarget.classList.add('is-active');
        drawCategories();
        draw();
      },
    }, s === 'all' ? tr('全部') : SCOPE_LABEL[s],
      h('span.tab-count', String(s === 'all' ? LIVE_KNOWLEDGE.summary.documents : LIVE_KNOWLEDGE.summary[s + '_documents']))),
  ));
  const search = makeSearchBox({
    placeholder: activeScope === 'user' ? `搜索 users/${Store.user}/knowledge …` : '按名称或路径搜索…',
    onInput: (v) => { query = v.toLowerCase(); page = 1; draw(); },
  });
  toolbar.append(search, categoryHost);

  /* 行渲染 */
  function draw() {
    fill(listHost);
    const rows = LIVE_KNOWLEDGE.documents.filter((d) => {
      if (activeScope !== 'all' && d.scope !== activeScope) return false;
      if (activeCategory !== 'all' && knowledgeCategoryKey(d.scope, d.category) !== activeCategory) return false;
      if (query && !(d.title + d.relative_path).toLowerCase().includes(query)) return false;
      return true;
    });
    const totalPages = Math.max(1, Math.ceil(rows.length / PER_PAGE));
    page = clamp(page, 1, totalPages);
    const slice = rows.slice((page - 1) * PER_PAGE, page * PER_PAGE);

    if (!rows.length) {
      listHost.appendChild(h('div.card', emptyState({
        icon: 'book',
        title: query ? tr('没有匹配的知识文件') : '当前知识目录中还没有可索引文件',
        desc: query ? '调整搜索词或切换层级后重试。' : '可以从右上角导入用户层或共享层文件。',
      })));
      return;
    }

    const wrap = h('div.table-wrap.knowledge-table-wrap');
    const table = h('table.table.knowledge-document-table',
      h('colgroup', [28, 9, 12, 12, 17, 9, 13].map(width => h('col', { style: { width: `${width}%` } }))),
      h('thead', h('tr',
        h('th', tr('名称')),
        h('th', '层级'),
        h('th', tr('分类')),
        h('th', '主智能体'),
        h('th', tr('路径')),
        h('th.num', tr('大小')),
        h('th', tr('更新时间')),
      )),
      h('tbody', slice.map((d) => h('tr' + (selected && selected.relative_path === d.relative_path ? '.is-selected' : ''), {
        style: { cursor: 'pointer' },
        onclick: () => {
          selected = d; draft = ''; categoryDraft = d.category && d.category !== UNCATEGORIZED ? d.category : ''; previewMode = 'preview'; drawCategories(); draw(); drawEditor();
          void readDetail(() => Api.getKnowledgeDocument(Store.user, d.scope, d.relative_path),
            result => { if (selected === d) { draft = result.content; categoryDraft = result.category && result.category !== UNCATEGORIZED ? result.category : ''; drawEditor(); } });
        },
      },
        h('td',
          h('div', { style: { display: 'flex', alignItems: 'center', gap: '9px' } },
            h('div.file-icon', h('span', { html: icon(d.relative_path.endsWith('.json') ? 'fileCode' : 'fileText', 'icon-sm') })),
            h('div', { style: { minWidth: '0' } },
              h('div.knowledge-document-title', { title: d.title, style: { fontSize: 'var(--t-sm)', fontWeight: '520' } }, d.title),
              h('div.file-path', { title: d.relative_path }, d.relative_path.split('/').pop()),
            ),
          ),
        ),
        h('td', h('span', { class: 'scope-tag scope-' + d.scope }, SCOPE_LABEL[d.scope])),
        h('td', h('span', { class: 'knowledge-category-tag', title: d.category || UNCATEGORIZED }, d.category || UNCATEGORIZED)),
        h('td', d.active_for_main_agent
          ? h('span.badge.badge-success', tr('已启用'))
          : h('span.badge', '已过滤')),
        h('td', h('span.file-path', { title: d.relative_path }, d.relative_path)),
        h('td.num', { style: { fontVariantNumeric: 'tabular-nums' } }, fmtBytes(d.size)),
        h('td', { style: { fontSize: 'var(--t-xs)', color: 'var(--text-tertiary)' } }, fmtRelative(d.updated_at)),
      ))),
    );
    wrap.appendChild(table);
    listHost.append(wrap, makePager(page, totalPages, (p) => { page = p; draw(); }) || h('div'));
  }

  /* 编辑器 */
  function drawEditor() {
    editorSizeObserver?.disconnect();
    fill(editorHost);
    if (!selected) {
      editorHost.appendChild(h('div.editor-panel',
        h('div.editor-head', h('div.editor-title', '知识文件')),
        h('div.editor-body', emptyState({ title: tr('选择知识文件'), desc: '点击左侧知识文件后，可在此处编辑或预览。', icon: 'penLine' })),
      ));
      return;
    }
    const isGlobal = selected.scope === 'global';
    const panel = h('div.editor-panel.knowledge-document-panel');

    panel.appendChild(h('div.editor-head',
      h('div', { style: { flex: '1', minWidth: '0' } },
        h('div.editor-title', selected.title),
        h('div', { style: { fontSize: 'var(--t-2xs)', color: 'var(--text-faint)', marginTop: '2px', fontFamily: 'var(--font-mono)' } }, truncateMiddle(selected.relative_path, 38)),
      ),
      h('span', { class: 'scope-tag scope-' + selected.scope }, SCOPE_LABEL[selected.scope]),
    ));

    const body = h('div.editor-body');
    if (isGlobal) {
      body.appendChild(h('div.banner.banner-warning', { style: { marginBottom: 'var(--s-4)' } },
        h('span', { html: icon('shield') }),
        h('div', h('div', { style: { fontWeight: '600' } }, '界面只读'),
          '全局层在 UI 上不提供编辑入口，但 HTTP 层仍可写入。这不是权限缺失，而是界面约束。'),
      ));
    }
    const categorySelect = h('select.select.knowledge-category-select', {
      'aria-label': '选择知识分类',
      onchange: (event) => { categoryDraft = event.target.value; },
    },
      h('option', { value: '', selected: !categoryDraft }, UNCATEGORIZED),
      ...getKnowledgeCategories(LIVE_KNOWLEDGE, selected.scope)
        .filter((item) => item.name !== UNCATEGORIZED)
        .map((item) => h('option', {
          value: item.name,
          selected: categoryDraft === item.name,
        }, item.name)),
    );
    body.appendChild(h('div.knowledge-category-editor',
      h('div.knowledge-category-editor-copy',
        h('strong', '归类到分类'),
        h('span', '分类文件夹是逻辑标签，文件仍保留原路径'),
      ),
      h('div.knowledge-category-controls',
        categorySelect,
        h('button.btn.btn-outline.btn-sm', {
          type: 'button',
          onclick: () => {
            const value = categorySelect.value.trim();
            void mutate(
              () => Api.setKnowledgeCategory(Store.user, selected.scope, selected.relative_path, value),
              value ? `已移动到“${value}”` : '已移出分类，归入未分类',
            );
          },
        }, '保存分类'),
        h('button.btn.btn-ghost.btn-sm', {
          type: 'button',
          onclick: () => {
            categorySelect.value = '';
            categoryDraft = '';
            void mutate(
              () => Api.setKnowledgeCategory(Store.user, selected.scope, selected.relative_path, ''),
              '已移出分类，归入未分类',
            );
          },
        }, '移出分类'),
      ),
    ));
    body.appendChild(h('div', { style: { display: 'flex', alignItems: 'center', gap: 'var(--s-2)', marginBottom: 'var(--s-3)' } },
      h('div.segments',
        h('button.segment' + (previewMode === 'preview' ? '.is-active' : ''), {
          onclick: () => { previewMode = 'preview'; drawEditor(); },
        }, tr('预览')),
        h('button.segment' + (previewMode === 'edit' ? '.is-active' : ''), {
          onclick: () => { if (isGlobal) return; previewMode = 'edit'; drawEditor(); },
          disabled: isGlobal,
          style: isGlobal ? { opacity: '0.45' } : {},
        }, tr('编辑')),
      ),
      h('div.spacer'),
      h('button.icon-btn', { title: tr('放大预览'), 'aria-label': tr('放大预览'), onclick: () => openKnowledgeZoom(selected, draft) },
        h('span', { html: icon('maximize') })),
    ));

    if (previewMode === 'edit') {
      const ta = h('textarea.textarea.mono.editor-md.knowledge-document-editor', { spellcheck: 'false', oninput: (e) => { draft = e.target.value; markDirty(); } });
      ta.value = draft;
      body.appendChild(ta);
    } else {
      body.appendChild(h('div.knowledge-document-preview', renderMarkdownWithWidgets(draft)));
    }
    panel.appendChild(body);

    const dirtyFlag = h('span.badge.badge-warning', { style: { display: 'none' } }, tr('有未保存修改'));
    panel.appendChild(h('div.editor-foot',
      dirtyFlag,
      h('div', { style: { flex: '1' } }),
      h('button.btn.btn-ghost.btn-sm', {
        onclick: () => { void readDetail(() => Api.getKnowledgeDocument(Store.user, selected.scope, selected.relative_path), result => { draft = result.content; drawEditor(); }); },
      }, tr('撤销')),
      h('button.btn.btn-ghost.btn-sm', {
        disabled: isGlobal,
        onclick: async () => {
          const ok = await Overlay.confirm({
            title: '删除此知识文件',
            description: '文件将被永久删除，相关索引也会失效。',
            detail: selected.relative_path, confirmLabel: tr('删除'), tone: 'danger',
          });
          if (ok && !isGlobal) await mutate(() => Api.deleteKnowledgeDocument(Store.user, selected.scope, selected.relative_path), '知识文件已删除');
        },
      }, h('span', { html: icon('trash', 'icon-sm') }), tr('删除')),
      h('button.btn.btn-primary.btn-sm', {
        disabled: isGlobal,
        onclick: () => mutate(() => Api.putKnowledgeDocument(Store.user, selected.scope, selected.relative_path, draft), '知识文件已保存；下次请求刷新索引'),
      }, tr('保存')),
    ));

    function markDirty() { dirtyFlag.style.display = ''; }
    editorHost.appendChild(panel);
    if (typeof ResizeObserver !== 'undefined') {
      editorSizeObserver = new ResizeObserver(() => {
        const content = panel.querySelector('.knowledge-document-preview, .knowledge-document-editor');
        // 扣除实际标题、工具栏、留白和底栏，沿用原预览面板的视口高度上限。
        const chromeHeight = Math.floor(panel.getBoundingClientRect().height - content.getBoundingClientRect().height);
        const value = `${chromeHeight}px`;
        if (panel.style.getPropertyValue('--knowledge-editor-chrome') !== value) {
          panel.style.setProperty('--knowledge-editor-chrome', value);
        }
      });
      editorSizeObserver.observe(panel);
    }
  }

  inner.append(tabBar, toolbar, h('div.knowledge-content-layout', listHost, editorHost));
  drawCategories();
  draw();
  drawEditor();
  return pageEl;
}

export function openKnowledgeZoom(doc, content) {
  Overlay.modal({
    title: doc.title,
    sub: doc.relative_path,
    size: 'xl',
    body: renderMarkdownWithWidgets(content),
    foot: [h('button.btn.btn-ghost', { onclick: () => Overlay.stack[Overlay.stack.length - 1].close() }, tr('关闭'))],
  });
}

/* ============================================================================
   pages/memory · 分层记忆 + 临时重要记忆
   ========================================================================= */

export const MEMORY_TIERS = [
  { key: 'seven_days', label: '七日记忆', icon: 'clock', hint: '短期，易过期' },
  { key: 'one_month', label: '一月记忆', icon: 'history', hint: '中期' },
  { key: 'half_year', label: '半年记忆', icon: 'archive', hint: '长期' },
  { key: 'permanent', label: '永久记忆', icon: 'star', hint: '不过期，禁止降级' },
  { key: 'important', label: '临时重要记忆', icon: 'zap', hint: '独立文件，不可删除' },
];

const WEIGHTED_MEMORY_TIERS = new Set(['seven_days', 'one_month', 'half_year']);

function formatMemoryTime(value, timeZone = 'UTC') {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat('sv-SE', {
    timeZone, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).format(date);
}

export function renderMemoryPage(ctx) {
  let activeTier = ctx.viewState.memoryTier || ctx.params.tier || 'seven_days';
  let selected = null;
  let draft = '';
  let dirty = false;
  let query = ctx.viewState.memorySearchQuery || '';
  let searchDraft = ctx.viewState.memorySearchDraft || '';
  let sortBy = 'updated_at';
  let page = 1;
  let previewMode = 'preview';
  const PER_PAGE = 6;

  const pageEl = h('div.page', h('div.page-inner.wide'));
  const inner = pageEl.firstElementChild;

  inner.appendChild(pageHead({
    title: tr('记忆'),
    desc: '不同来源的会话共享当前用户的记忆。七日、一月、半年记忆按不同日期出现次数累计权重；编辑后 +1 且同日最多一次。永久记忆不加权；普通记忆不能降级。',
    actions: [
      h('button.btn.btn-outline', { onclick: () => refresh() }, h('span', { html: icon('refresh', 'icon-sm') }), tr('重新读取')),
      h('button.btn.btn-primary', {
        onclick: () => openNewMemory(),
      }, h('span', { html: icon('plus', 'icon-sm') }), '新建长期记忆'),
    ],
  }));

  const tierPanel = h('div.editor-panel', { style: { position: 'sticky', top: 'var(--s-4)' } });
  const listHost = h('div');
  const editorHost = h('div');
  const layout = h('div.split-narrow.memory-layout', tierPanel, listHost, editorHost);

  const drawTiers = () => {
    fill(tierPanel);
    tierPanel.appendChild(h('div.editor-head',
      h('div.editor-title', '记忆分栏'),
      h('span.badge', String(LIVE_MEMORY.summary.total) + ' 片段'),
    ));
    const body = h('div', { style: { padding: 'var(--s-2)' } });
    const tiers = h('div.memory-tiers');
    MEMORY_TIERS.forEach((t) => {
      const count = t.key === 'important' ? 1 : LIVE_MEMORY.summary[t.key] ?? 0;
      const item = h('div.tier-item' + (t.key === activeTier ? '.is-active' : ''), {
        onclick: () => { activeTier = t.key; ctx.viewState.memoryTier = t.key; selected = null; dirty = false; page = 1; drawTiers(); drawList(); drawEditor(); },
      },
        h('div.tier-glyph', h('span', { html: icon(t.icon, 'icon-sm') })),
        h('div', { style: { flex: '1', minWidth: '0' } },
          h('div.tier-name', tr(t.label)),
          h('div', { style: { fontSize: 'var(--t-2xs)', color: 'var(--text-faint)' } }, t.hint),
        ),
        h('span.tier-count' + (t.key === 'important' ? '.is-accent' : ''), count + (t.key === 'important' ? ' 文件' : '')),
      );
      tiers.appendChild(item);
    });
    body.appendChild(tiers);
    tierPanel.appendChild(body);

  };

  const drawList = () => {
    fill(listHost);
    if (!WEIGHTED_MEMORY_TIERS.has(activeTier) && sortBy === 'weight') sortBy = 'updated_at';
    layout.classList.toggle('is-important', activeTier === 'important');
    editorHost.hidden = activeTier === 'important';
    if (activeTier === 'important') {
      const memory = LIVE_IMPORTANT_MEMORY;
      const lifecycle = memory.lifecycle;
      listHost.appendChild(h('section.card.important-memory-detail', { 'aria-label': '临时重要记忆详情' },
        h('div.card-head', h('div.section-title', tr('临时重要记忆')),
          statusBadge(lifecycle.status, lifecycle.status === 'valid' ? '有效' : statusText(lifecycle.status)),
        ),
        h('div.card-body',
          h('div.important-memory-summary',
            h('span', { title: memory.path }, memory.path),
            h('span', fmtBytes(memory.size)),
            h('span', formatMemoryTime(memory.updated_at, 'Asia/Shanghai') + '（+08:00）'),
          ),
          h('p.important-memory-status', lifecycle.reason,
            lifecycle.prompt_eligible ? ' · 可注入' : ' · 暂不注入'),
          h('div.textarea.memory-document-preview.important-memory-preview',
            renderMarkdownWithWidgets(memory.content || '_暂无内容_')),
        ),
      ));
      return;
    }

    const items = LIVE_MEMORY.items
      .filter((m) => m.tier === activeTier)
      .filter((m) => !query || (m.preview + m.filename).toLowerCase().includes(query));

    const sorted = items.slice().sort((a, b) => {
      if (sortBy === 'weight') return b.weight - a.weight;
      if (sortBy === 'created_at') return new Date(a.created_at) - new Date(b.created_at);
      return new Date(b.updated_at) - new Date(a.updated_at);
    });

    const listCard = h('div.card', { style: { overflow: 'hidden' } });
    const head = h('div.card-head.memory-list-head',
      h('div.section-title', { style: { fontSize: 'var(--t-sm)' } }, MEMORY_TIERS.find((t) => t.key === activeTier).label,
        h('span.section-count', String(sorted.length)),
      ),
    );
    let composing = false;
    const restoreSearchFocus = () => {
      const next = listHost.querySelector('.memory-search-box input');
      next?.focus();
      next?.setSelectionRange(next.value.length, next.value.length);
    };
    const applySearch = () => {
      query = searchDraft.trim().toLowerCase();
      ctx.viewState.memorySearchQuery = query;
      page = 1;
      drawList();
      restoreSearchFocus();
    };
    const input = h('input.input', {
      type: 'search', placeholder: tr('搜索记忆内容…'), 'aria-label': tr('搜索记忆内容…'),
      value: searchDraft,
      oninput: () => {
        searchDraft = input.value;
        ctx.viewState.memorySearchDraft = searchDraft;
        clear.hidden = !searchDraft;
      },
      oncompositionstart: () => { composing = true; },
      oncompositionend: () => { composing = false; },
      onkeydown: (event) => {
        if (event.key === 'Enter' && (composing || event.isComposing || event.keyCode === 229)) event.preventDefault();
      },
    });
    const clear = h('button.search-clear', {
      type: 'button', hidden: !searchDraft, 'aria-label': tr('清除搜索'), title: tr('清除搜索'),
      onclick: () => {
        searchDraft = '';
        ctx.viewState.memorySearchDraft = '';
        applySearch();
      },
    }, h('span', { html: icon('close', 'icon-sm'), 'aria-hidden': 'true' }));
    const search = h('form.search-box.memory-search-box', {
      role: 'search', 'aria-label': tr('搜索记忆内容…'),
      onsubmit: (event) => { event.preventDefault(); if (!composing) applySearch(); },
    }, input, clear, h('button.memory-search-submit', {
      type: 'submit', 'aria-label': tr('搜索'), title: tr('搜索'),
    }, h('span', { html: icon('search', 'icon-sm'), 'aria-hidden': 'true' })));
    const sortSel = h('select.select', { 'aria-label': '记忆排序', style: { width: 'auto' }, onchange: (e) => { sortBy = e.target.value; drawList(); } },
      h('option', { value: 'updated_at', selected: sortBy === 'updated_at' }, tr('按更新时间')),
      h('option', { value: 'created_at', selected: sortBy === 'created_at' }, '按最早时间'),
      WEIGHTED_MEMORY_TIERS.has(activeTier) ? h('option', { value: 'weight', selected: sortBy === 'weight' }, tr('按权重')) : null,
    );
    head.append(h('div.memory-list-controls', search, sortSel));
    listCard.appendChild(head);

    const totalPages = Math.max(1, Math.ceil(sorted.length / PER_PAGE));
    page = clamp(page, 1, totalPages);
    const slice = sorted.slice((page - 1) * PER_PAGE, page * PER_PAGE);

    if (!slice.length) {
      listCard.appendChild(emptyState({ icon: 'memory', title: '当前记忆栏暂无内容', desc: '可以切换其他记忆栏或重新读取数据。' }));
    } else {
      const list = h('div.list.memory-fragment-list', { style: { padding: 'var(--s-2)' } });
      slice.forEach((m) => {
        const isActive = selected && selected.filename === m.filename;
        const row = h('div.list-row' + (isActive ? '.is-active' : ''), {
          style: { alignItems: 'flex-start', padding: '10px var(--s-3)' },
          onclick: () => { selected = m; draft = ''; dirty = false; previewMode = 'preview'; drawList(); drawEditor(); void readDetail(() => Api.getMemoryItem(Store.user, m.tier, m.filename), result => { if (selected === m) { selected = {...m, ...result}; draft = result.content; drawEditor(); } }); },
        },
          h('div', { style: { flex: '1', minWidth: '0' } },
            h('div', { style: { display: 'flex', alignItems: 'center', gap: 'var(--s-2)', marginBottom: '3px' } },
              h('span.lr-title', { style: { flex: '1' } }, m.preview.slice(0, 34) + (m.preview.length > 34 ? '…' : '')),
              WEIGHTED_MEMORY_TIERS.has(m.tier) && selected?.filename === m.filename && selected?.weighted_today
                ? h('span.badge.badge-warning', { title: '今日权重已锁定' }, '权重锁')
                : null,
            ),
            h('div', { style: { fontSize: 'var(--t-xs)', color: 'var(--text-tertiary)', lineHeight: '1.5', display: '-webkit-box', WebkitLineClamp: '2', WebkitBoxOrient: 'vertical', overflow: 'hidden' } }, m.preview),
            h('div', { style: { display: 'flex', alignItems: 'center', gap: 'var(--s-3)', marginTop: '6px' } },
              h('span', { style: { fontSize: 'var(--t-2xs)', color: 'var(--text-faint)', fontFamily: 'var(--font-mono)' } }, m.filename),
              h('div', { style: { flex: '1' } }),
              WEIGHTED_MEMORY_TIERS.has(m.tier) ? h('span', { style: { display: 'inline-flex', alignItems: 'center', gap: '5px', fontSize: 'var(--t-2xs)', color: 'var(--text-faint)' } },
                tr('权重'),
                h('span.weight-bars', Array.from({ length: 5 }, (_, i) =>
                  h('span', { class: 'weight-bar' + (i < Math.min(5, Math.ceil(m.weight / 5)) ? ' is-on' : '') }))),
                h('span', { style: { fontVariantNumeric: 'tabular-nums' } }, String(m.weight)),
              ) : null,
              h('span', { style: { fontSize: 'var(--t-2xs)', color: 'var(--text-faint)' } }, fmtRelative(m.updated_at)),
            ),
          ),
        );
        list.appendChild(row);
      });
      listCard.appendChild(list);
      listCard.appendChild(makePager(page, totalPages, (p2) => { page = p2; drawList(); }) || h('div'));
    }
    listHost.appendChild(listCard);
    listHost.appendChild(h('div', { style: { marginTop: 'var(--s-3)', fontSize: 'var(--t-2xs)', color: 'var(--text-faint)', lineHeight: '1.6' } },
      '普通记忆多为 UTC 时间；临时重要记忆的 updated_at 为上海时区 ISO。expires_at、tier_entered_at、content_updated_at 含义各不相同。',
    ));
  };

  const drawEditor = () => {
    fill(editorHost);
    if (activeTier === 'important') return;
    if (!selected) {
      editorHost.appendChild(h('div.editor-panel',
        h('div.editor-head', h('div.editor-title', '记忆正文')),
        h('div.editor-body', emptyState({ title: '选择一条记忆', desc: '点击左侧记忆后在此处查看和编辑。', icon: 'penLine' })),
      ));
      return;
    }
    const panel = h('div.editor-panel');

    panel.appendChild(h('div.editor-head',
      h('div', { style: { flex: '1', minWidth: '0' } },
        h('div.editor-title', selected.preview.slice(0, 24) || tr('记忆')),
        h('div', { style: { fontSize: 'var(--t-2xs)', color: 'var(--text-faint)', marginTop: '2px', fontFamily: 'var(--font-mono)' } },
          selected.filename),
      ),
    ));

    const body = h('div.editor-body');

    /* 元数据 */
    const meta = [
        ['记忆类型', MEMORY_TIERS.find((t) => t.key === selected.tier).label],
        ...(WEIGHTED_MEMORY_TIERS.has(selected.tier)
          ? [['当前权重', String(selected.weight) + (selected.weighted_today ? ' · 今日已加权' : '')]]
          : []),
        ['创建时间', formatMemoryTime(selected.created_at) + '（UTC）'],
        ['内容更新', formatMemoryTime(selected.content_updated_at) + '（UTC）'],
        ['最近使用', formatMemoryTime(selected.last_used_at) + '（UTC）'],
        ['进入本层', formatMemoryTime(selected.tier_entered_at) + '（UTC）'],
        ['过期时间', selected.expires_at ? formatMemoryTime(selected.expires_at) + '（UTC）' : '不过期'],
      ];
    body.appendChild(h('div.desc-list', { style: { marginBottom: 'var(--s-4)' } },
      meta.map(([k, v]) => h('div.desc-row', { style: { gridTemplateColumns: '104px 1fr', padding: '6px 0' } },
        h('span.desc-key', k), h('span.desc-val', v))),
    ));

    body.appendChild(h('div', { style: { display: 'flex', alignItems: 'center', gap: 'var(--s-2)', marginBottom: 'var(--s-3)' } },
      h('div.segments',
        h('button.segment' + (previewMode === 'preview' ? '.is-active' : ''), { onclick: () => { previewMode = 'preview'; drawEditor(); } }, tr('预览')),
        h('button.segment' + (previewMode === 'edit' ? '.is-active' : ''), { onclick: () => { previewMode = 'edit'; drawEditor(); } }, tr('编辑')),
      ),
      h('div.spacer'),
      h('span', { class: 'badge badge-warning', style: { display: dirty ? '' : 'none' } }, tr('有未保存修改')),
    ));

    if (previewMode === 'edit') {
      const ta = h('textarea.textarea.mono.editor-md.memory-document-editor', { spellcheck: 'false' });
      ta.value = draft;
      ta.addEventListener('input', () => { draft = ta.value; dirty = true; const b = panel.querySelector('.badge-warning'); if (b) b.style.display = ''; });
      body.appendChild(ta);
    } else {
      body.appendChild(h('div.textarea.memory-document-preview', renderMarkdownWithWidgets(draft || '_暂无内容_')));
    }

    panel.appendChild(body);

    panel.appendChild(h('div.editor-foot',
      h('button.btn.btn-danger.btn-sm', {
          onclick: async () => {
            const target = selected;
            const ok = await Overlay.confirm({
              title: '删除此记忆',
              description: '记忆文件、索引行与元数据将被一并清理。',
              detail: target.filename, confirmLabel: '删除记忆', tone: 'danger',
            });
            if (ok) await mutate(() => Api.deleteMemory(Store.user, target.tier, target.filename), '记忆已删除');
          },
        }, h('span', { html: icon('trash', 'icon-sm') }), tr('删除')),
      h('div', { style: { flex: '1' } }),
      h('button.btn.btn-ghost.btn-sm', {
        onclick: () => {
          const target = selected;
          void readDetail(() => Api.getMemoryItem(Store.user, target.tier, target.filename), result => {
            if (selected !== target) return;
            selected = {...target, ...result}; draft = result.content; dirty = false; drawEditor();
          });
        },
      }, tr('恢复')),
      h('button.btn.btn-primary.btn-sm', {
        onclick: () => {
          if (!draft.trim()) { toast('记忆内容不能为空', { kind: 'error' }); return; }
          void mutate(() => Api.putMemory(Store.user, selected.filename, draft, selected.tier), '记忆已保存');
        },
      }, tr('保存')),
    ));

    editorHost.appendChild(panel);
  };

  function refresh() {
    void refreshPage();
  }

  function openNewMemory() {
    let content = '';
    Overlay.modal({
      title: '新建长期记忆',
      sub: '当前界面固定创建到永久层（permanent）',
      body: h('div', { style: { display: 'flex', flexDirection: 'column', gap: 'var(--s-4)' } },
        h('div.banner.banner-info',
          h('span', { html: icon('info') }),
          h('div', '接口在省略 tier 时默认落到 seven_days，界面显式传 permanent，避免意图与结果不一致。'),
        ),
        h('div.field',
          h('label.field-label', '记忆内容'),
          h('textarea.textarea.mono', { rows: 8, placeholder: '要长期保留的事实、约定或结论…', oninput: (e) => { content = e.target.value; } }),
          h('div.field-hint', '非空，字符上限由服务端配置约束。'),
        ),
      ),
      foot: [
        h('button.btn.btn-ghost', { onclick: () => Overlay.stack[Overlay.stack.length - 1].close() }, tr('取消')),
        h('button.btn.btn-primary', {
          onclick: () => {
            if (!content.trim()) { toast('内容不能为空', { kind: 'error' }); return; }
            void mutate(() => Api.putMemory(Store.user, `memory_${Date.now()}.md`, content.trim(), 'permanent'), '永久记忆已创建', closeOverlays);
          },
        }, tr('创建')),
      ],
    });
  }

  inner.appendChild(layout);
  drawTiers(); drawList(); drawEditor();
  return pageEl;
}
