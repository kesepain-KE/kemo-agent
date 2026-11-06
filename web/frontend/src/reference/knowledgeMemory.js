import { t as tr } from '../i18n'
import { h, pageHead, mutate, Api, Store, icon, toast, append, fill, clamp, emptyState, readDetail, fmtBytes, fmtRelative, makePager, renderMarkdownWithWidgets, Overlay, LIVE_MEMORY, LIVE_IMPORTANT_MEMORY, statusBadge, statusText, refreshPage, closeOverlays } from './shared.js'
export { renderKnowledgePage, openKnowledgeZoom, SCOPE_LABEL } from './knowledge.js'

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
      }, h('span', { html: icon('plus', 'icon-sm') }), tr('新建长期记忆')),
    ],
  }));

  const tierPanel = h('div.editor-panel', { style: { position: 'sticky', top: 'var(--s-4)' } });
  const listHost = h('div');
  const editorHost = h('div');
  const layout = h('div.split-narrow.memory-layout', tierPanel, listHost, editorHost);

  const drawTiers = () => {
    fill(tierPanel);
    tierPanel.appendChild(h('div.editor-head',
      h('div.editor-title', tr('记忆分栏')),
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
          statusBadge(lifecycle.status, lifecycle.status === 'valid' ? tr('有效') : statusText(lifecycle.status)),
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
    const sortSel = h('select.select', { 'aria-label': tr('记忆排序'), style: { width: 'auto' }, onchange: (e) => { sortBy = e.target.value; drawList(); } },
      h('option', { value: 'updated_at', selected: sortBy === 'updated_at' }, tr('按更新时间')),
      h('option', { value: 'created_at', selected: sortBy === 'created_at' }, tr('按最早时间')),
      WEIGHTED_MEMORY_TIERS.has(activeTier) ? h('option', { value: 'weight', selected: sortBy === 'weight' }, tr('按权重')) : null,
    );
    head.append(h('div.memory-list-controls', search, sortSel));
    listCard.appendChild(head);

    const totalPages = Math.max(1, Math.ceil(sorted.length / PER_PAGE));
    page = clamp(page, 1, totalPages);
    const slice = sorted.slice((page - 1) * PER_PAGE, page * PER_PAGE);

    if (!slice.length) {
      listCard.appendChild(emptyState({ icon: 'memory', title: tr('当前记忆栏暂无内容'), desc: '可以切换其他记忆栏或重新读取数据。' }));
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
        h('div.editor-head', h('div.editor-title', tr('记忆正文'))),
        h('div.editor-body', emptyState({ title: tr('选择一条记忆'), desc: '点击左侧记忆后在此处查看和编辑。', icon: 'penLine' })),
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
        [tr('记忆类型'), MEMORY_TIERS.find((t) => t.key === selected.tier).label],
        ...(WEIGHTED_MEMORY_TIERS.has(selected.tier)
          ? [[tr('当前权重'), String(selected.weight) + (selected.weighted_today ? ' · 今日已加权' : '')]]
          : []),
        [tr('创建时间'), formatMemoryTime(selected.created_at) + '（UTC）'],
        [tr('内容更新'), formatMemoryTime(selected.content_updated_at) + '（UTC）'],
        [tr('最近使用'), formatMemoryTime(selected.last_used_at) + '（UTC）'],
        [tr('进入本层'), formatMemoryTime(selected.tier_entered_at) + '（UTC）'],
        [tr('过期时间'), selected.expires_at ? formatMemoryTime(selected.expires_at) + '（UTC）' : tr('不过期')],
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
              title: tr('删除此记忆'),
              description: '记忆文件、索引行与元数据将被一并清理。',
              detail: target.filename, confirmLabel: tr('删除记忆'), tone: 'danger',
            });
            if (ok) await mutate(() => Api.deleteMemory(Store.user, target.tier, target.filename), tr('记忆已删除'));
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
          void mutate(() => Api.putMemory(Store.user, selected.filename, draft, selected.tier), tr('记忆已保存'));
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
      title: tr('新建长期记忆'),
      sub: '当前界面固定创建到永久层（permanent）',
      body: h('div', { style: { display: 'flex', flexDirection: 'column', gap: 'var(--s-4)' } },
        h('div.banner.banner-info',
          h('span', { html: icon('info') }),
          h('div', '接口在省略 tier 时默认落到 seven_days，界面显式传 permanent，避免意图与结果不一致。'),
        ),
        h('div.field',
          h('label.field-label', tr('记忆内容')),
          h('textarea.textarea.mono', { rows: 8, placeholder: '要长期保留的事实、约定或结论…', oninput: (e) => { content = e.target.value; } }),
          h('div.field-hint', '非空，字符上限由服务端配置约束。'),
        ),
      ),
      foot: [
        h('button.btn.btn-ghost', { onclick: () => Overlay.stack[Overlay.stack.length - 1].close() }, tr('取消')),
        h('button.btn.btn-primary', {
          onclick: () => {
            if (!content.trim()) { toast(tr('内容不能为空'), { kind: 'error' }); return; }
            void mutate(() => Api.putMemory(Store.user, `memory_${Date.now()}.md`, content.trim(), 'permanent'), tr('永久记忆已创建'), closeOverlays);
          },
        }, tr('创建')),
      ],
    });
  }

  inner.appendChild(layout);
  drawTiers(); drawList(); drawEditor();
  return pageEl;
}
