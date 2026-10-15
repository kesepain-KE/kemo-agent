import { h, pageHead, LIVE_AGENTS, LIVE_MESSAGES, LIVE_EXPANDS, $$, fill, icon, statusBadge, statusText, fmtNum } from './shared.js'
import { EXPAND_SCOPE_LABEL } from './modules.js'
/* ============================================================================
   运行模块兼容入口 /runtime
   ========================================================================= */

export function renderRuntimeModulesPage(ctx) {
  let tab = 'agents';
  const pageEl = h('div.page', h('div.page-inner'));
  const inner = pageEl.firstElementChild;

  inner.appendChild(pageHead({
    title: '运行模块',
    desc: '旧版聚合入口，把子智能体、外部消息与拓展的只读清单合并到一个页面。常规导航不直接展示它，但路由仍然保留。',
  }));

  const tabs = [
    { key: 'agents', label: '子智能体', count: LIVE_AGENTS.summary.total },
    { key: 'messages', label: '外部消息', count: LIVE_MESSAGES.summary.total_transports },
    { key: 'expand', label: '拓展', count: LIVE_EXPANDS.summary.total },
  ];
  const tabBar = h('div.tabs', tabs.map((t) =>
    h('button.tab' + (t.key === tab ? '.is-active' : ''), {
      onclick: (ev) => {
        tab = t.key;
        $$('.tab', tabBar).forEach((x) => x.classList.remove('is-active'));
        ev.currentTarget.classList.add('is-active');
        draw();
      },
    }, t.label, h('span.tab-count', String(t.count)))));
  inner.appendChild(tabBar);

  const body = h('div');
  inner.appendChild(body);

  function draw() {
    fill(body);
    if (tab === 'agents') {
      body.appendChild(h('div.card-grid',
        LIVE_AGENTS.agents.map((a) => h('div.entry', { style: { cursor: 'default' } },
          h('div.entry-icon' + (a.enabled ? '.accent' : ''), h('span', { html: icon('agents', 'icon-sm') })),
          h('div', { style: { flex: '1', minWidth: '0' } },
            h('div.entry-title', a.name, h('span.badge.badge-outline', 'v' + a.version)),
            h('div.entry-desc', a.description),
            h('div.entry-meta',
              h('span', a.source === 'global' ? '全局层' : '用户层'),
              h('span', a.enabled ? '已启用' : '已停用'),
            ),
          ),
        )),
      ));
    } else if (tab === 'messages') {
      body.appendChild(h('div.card-grid',
        LIVE_MESSAGES.transports.map((t) => h('div.entry', { style: { cursor: 'default' } },
          h('div.entry-icon' + (t.state === 'running' ? '.accent' : ''), h('span', { html: icon('messages', 'icon-sm') })),
          h('div', { style: { flex: '1', minWidth: '0' } },
            h('div.entry-title', t.display_name, statusBadge(t.state, t.state === 'running' ? '运行中' : statusText(t.state))),
            h('div.entry-desc', t.description),
            h('div.entry-meta', h('span', '绑定 ' + t.bound_user), h('span', t.platform)),
          ),
        )),
      ));
    } else {
      const allItems = LIVE_EXPANDS.expands.flatMap((g) => g.items);
      body.appendChild(h('div.card-grid',
        allItems.map((m) => h('div.entry', { style: { cursor: 'default' } },
          h('div.entry-icon' + (m.valid ? '.accent' : ''), h('span', { html: icon('expand', 'icon-sm') })),
          h('div', { style: { flex: '1', minWidth: '0' } },
            h('div.entry-title', m.display_name),
            h('div.entry-desc', m.error || m.description),
            h('div.entry-meta',
              h('span', EXPAND_SCOPE_LABEL[m.scope]),
              h('span', m.valid ? '有效' : '无效'),
              m.injected_tokens ? h('span', fmtNum(m.injected_tokens) + ' tok') : null,
            ),
          ),
        )),
      ));
    }
  }
  draw();
  return pageEl;
}

/* ============================================================================
   身份与人格 /profile
   ========================================================================= */


