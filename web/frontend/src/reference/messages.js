import { t as tr } from '../i18n'
import { LIVE_MESSAGES, Store, h, pageHead, refreshPage, icon, statTile, fill, emptyState, statusBadge, statusText, fmtRelative, fmtBytes, $$, fmtDateTime, makePager, copyText, mutate, Api, Overlay, renderMarkdownWithWidgets } from './shared.js'
export function renderMessagesPage(ctx) {
  const transports = LIVE_MESSAGES.transports.filter((t) => t.bound_user === Store.user);
  let selectedId = transports.find((t) => t.id === ctx.viewState.messageId)?.id || transports[0]?.id;
  let page = Math.floor(Math.max(0, transports.findIndex((t) => t.id === selectedId)) / 6) + 1;
  let logFilter = ctx.viewState.messageLogFilter || 'all';
  let logPage = ctx.viewState.messageLogPage || 1;
  const PER_PAGE = 6;
  const LOG_PER_PAGE = 10;

  const pageEl = h('div.page', h('div.page-inner.wide'));
  const inner = pageEl.firstElementChild;

  inner.appendChild(pageHead({
    title: tr('外部消息'),
    desc: '查看当前用户绑定的消息模块、连接状态与收发记录。状态每 30 秒刷新一次。',
    actions: [
      h('button.btn.btn-outline', { onclick: () => { void refreshPage(); } },
        h('span', { html: icon('refresh', 'icon-sm') }), tr('刷新')),
    ],
  }));

  inner.appendChild(h('div.stat-row',
    statTile('绑定模块', LIVE_MESSAGES.summary.total_bindings, { icon: 'messages', foot: '当前用户可见' }),
    statTile('在线通道', LIVE_MESSAGES.summary.running_transports, { icon: 'wifi', color: 'var(--success)' }),
    statTile('临时文件', LIVE_MESSAGES.summary.temporary_files, { icon: 'folder', foot: 'message/out/*/files' }),
    statTile('今日日志', LIVE_MESSAGES.summary.today_logs, { icon: 'terminal', foot: '收发合计' }),
  ));
  inner.appendChild(h('div.section-label', { style: { padding: '0' } },
    h('span', { html: icon('messages', 'icon-sm') }), '已绑定消息模块'));

  const listHost = h('div.message-list-column');
  const detailHost = h('div.message-detail-column');

  function selectModule(t) {
    selectedId = t.id;
    logPage = 1;
    ctx.viewState.messageId = selectedId;
    ctx.viewState.messageLogPage = logPage;
    drawList();
    drawDetail();
  }

  function drawList() {
    fill(listHost);
    if (!transports.length) {
      listHost.appendChild(emptyState({
        icon: 'messages',
        title: '当前用户未绑定消息模块',
        desc: '在 message/out 的模块配置中设置 bound_user 后会显示在这里。',
      }));
      return;
    }
    const visible = transports.slice((page - 1) * PER_PAGE, page * PER_PAGE);
    const list = h('div.message-module-list');
    visible.forEach((t) => {
      list.appendChild(h('div.agent-item.transport-card.message-module-item' + (t.id === selectedId ? '.is-active' : ''), {
        role: 'button', tabindex: '0', 'aria-label': t.display_name, 'aria-pressed': String(t.id === selectedId),
        onclick: () => selectModule(t),
        onkeydown: (ev) => {
          if (ev.target !== ev.currentTarget || !['Enter', ' '].includes(ev.key)) return;
          ev.preventDefault();
          selectModule(t);
        },
      },
        h('div.agent-avatar', h('span', { html: icon(t.platform === 'telegram' ? 'send' : 'globe', 'icon-sm') })),
        h('div.message-module-copy',
          h('div.message-module-title',
            h('span', t.display_name),
            h('span.badge.badge-outline', t.platform),
            statusBadge(t.state, t.state === 'running' ? '运行中' : statusText(t.state)),
          ),
          h('div.message-module-desc', t.description || t.name),
          h('div.message-module-meta',
            h('span', h('span.dot' + (t.connection_status === 'connected' ? '.dot-success' : '.dot-danger')),
              t.connection_status === 'connected' ? '已连接' : tr('未连接')),
            h('span', t.latency_ms != null ? t.latency_ms + ' ms' : '—'),
            h('span', `收 ${t.messages_received_today} / 发 ${t.messages_sent_today}`),
          ),
        ),
        h('button.chip-x', { title: '复制模块名称', 'aria-label': `复制 ${t.display_name} 名称`,
          onclick: (ev) => { ev.stopPropagation(); void copyText(t.name); } },
          h('span', { html: icon('copy', 'icon-sm') })),
      ));
    });
    listHost.appendChild(list);
    const pager = makePager(page, Math.max(1, Math.ceil(transports.length / PER_PAGE)), (next) => {
      page = next;
      selectModule(transports[(page - 1) * PER_PAGE]);
    });
    if (pager) listHost.appendChild(pager);
  }

  function drawDetail() {
    fill(detailHost);
    const t = transports.find((x) => x.id === selectedId);
    if (!t) {
      detailHost.appendChild(h('div.editor-panel.messages-detail-panel',
        h('div.editor-head', h('div.editor-title', '消息模块详情')),
        h('div.editor-body', emptyState({ title: '暂无可查看的消息模块', icon: 'messages' })),
      ));
      return;
    }

    const panel = h('div.editor-panel.messages-detail-panel');
    panel.appendChild(h('div.editor-head',
      h('div', { style: { flex: '1', minWidth: '0' } },
        h('div.editor-title', t.display_name),
        h('div.message-detail-path', t.path),
      ),
      statusBadge(t.state, t.state === 'running' ? '运行中' : statusText(t.state)),
    ));

    const body = h('div.editor-body');
    body.appendChild(h('div', { style: { display: 'flex', flexDirection: 'column', gap: 'var(--s-4)' } },
      t.last_error ? h('div.banner.banner-danger', h('span', { html: icon('alertCircle') }), h('div', t.last_error)) : null,

      h('div.desc-list',
        h('div.desc-row', h('span.desc-key', '绑定用户'), h('span.desc-val.mono', t.bound_user)),
        h('div.desc-row', h('span.desc-key', '连接状态'), h('span.desc-val',
          h('span', { style: { display: 'inline-flex', alignItems: 'center', gap: '6px' } },
            h('span.dot' + (t.connection_status === 'connected' ? '.dot-success' : '.dot-error')),
            t.connection_status === 'connected' ? '已连接' : tr('未连接')),
        )),
        h('div.desc-row', h('span.desc-key', '最后心跳'), h('span.desc-val', t.last_check ? fmtRelative(t.last_check) : '—')),
        h('div.desc-row', h('span.desc-key', '最后消息'), h('span.desc-val', t.last_message_at ? fmtRelative(t.last_message_at) : '—')),
        h('div.desc-row', h('span.desc-key', '模块路径'), h('span.desc-val.mono', t.path)),
        h('div.desc-row', h('span.desc-key', '临时文件目录'), h('span.desc-val.mono', t.files_path || '—')),
        h('div.desc-row', h('span.desc-key', '日志数据库'), h('span.desc-val.mono', t.log_path)),
        h('div.desc-row', h('span.desc-key', '支持能力'), h('span.desc-val', (t.capabilities || []).join(' / ') || '—')),
        h('div.desc-row', h('span.desc-key', '允许工具'), h('span.desc-val', t.allowed_tools ? t.allowed_tools.join('、') : '不限制')),
      ),
      h('div.doc-block',
        h('div.doc-label', '描述'),
        h('div.doc-content', renderMarkdownWithWidgets(t.description || '（暂无模块说明）')),
      ),

      /* 参数摘要 */
      h('div',
        h('div.doc-label', { style: { marginBottom: '8px' } }, '参数摘要'),
        h('div.message-parameters-grid',
          [['状态 API 已导入', t.api_imported ? '是' : '否'], ['轮询间隔', t.polling_interval + ' 秒'],
            ['健康检测间隔', t.health_interval], ['文件中转', t.file_relay_enabled ? '开启' : '关闭'],
            ['日志轮转', t.log_rotation], ['消息缓冲', String(t.message_buffer)],
            ['临时文件', `${t.temporary_file_count} 个 · ${fmtBytes(t.temporary_file_bytes)}`],
            ['今日日志', String(t.today_log_count)],
          ].map(([k, v]) => h('div', {
            style: { padding: '8px 10px', borderRadius: 'var(--r-sm)', background: 'var(--bg-inset)', border: '1px solid var(--border-subtle)' },
          },
            h('div', { style: { fontSize: 'var(--t-2xs)', color: 'var(--text-faint)' } }, k),
            h('div', { style: { fontSize: 'var(--t-xs)', fontWeight: '540', marginTop: '2px' } }, v),
          )),
        ),
      ),

      /* 日志 */
      h('div',
        h('div', { style: { display: 'flex', alignItems: 'center', gap: 'var(--s-3)', marginBottom: 'var(--s-3)' } },
          h('div.doc-label', '消息日志'),
          h('div', { style: { flex: '1' } }),
          h('div.segments', ['all', 'send', 'receive', 'file'].map((k) =>
            h('button.segment' + (k === logFilter ? '.is-active' : ''), {
              onclick: (ev) => {
                logFilter = k; logPage = 1;
                ctx.viewState.messageLogFilter = logFilter;
                ctx.viewState.messageLogPage = logPage;
                $$('.segment', ev.currentTarget.parentElement).forEach((x) => x.classList.remove('is-active'));
                ev.currentTarget.classList.add('is-active');
                drawDetail();
              },
            }, k === 'all' ? tr('全部') : k === 'send' ? tr('发送') : k === 'receive' ? '接收' : '文件'),
          )),
        ),
        (() => {
          const logs = t.logs.filter((l) => logFilter === 'all' || l.direction === logFilter || (logFilter === 'file' && l.kind === 'file'));
          if (!logs.length) return emptyState({ icon: 'terminal', title: '当前分类暂无记录', desc: '部分日志源可能不可用。' });
          const totalPages = Math.max(1, Math.ceil(logs.length / LOG_PER_PAGE));
          logPage = Math.max(1, Math.min(logPage, totalPages));
          const slice = logs.slice((logPage - 1) * LOG_PER_PAGE, logPage * LOG_PER_PAGE);
          const box = h('div', { style: { border: '1px solid var(--border-subtle)', borderRadius: 'var(--r-md)', overflow: 'hidden' } },
            slice.map((l) => h('div.log-line',
              h('div', { class: 'log-dir is-' + l.direction },
                h('span', { html: icon(l.direction === 'send' ? 'arrowUp' : 'arrowDown', 'icon-sm') })),
              h('span.log-time', fmtDateTime(l.timestamp, { timeOnly: true })),
              h('div.log-content',
                h('div.message-log-body', l.content || '（无文本正文）'),
                l.file_path ? h('div', { style: { fontFamily: 'var(--font-mono)', fontSize: 'var(--t-2xs)', color: 'var(--text-faint)', marginTop: '2px' } }, l.file_path) : null,
              ),
              h('span.badge', l.kind),
            )),
          );
          const pg = makePager(logPage, totalPages, (p) => {
            logPage = p; ctx.viewState.messageLogPage = logPage; drawDetail();
          });
          return pg ? h('div', box, pg) : box;
        })(),
        t.logs_truncated ? h('div', { style: { marginTop: 'var(--s-3)' } },
          h('div.banner.banner-warning',
            h('span', { html: icon('alertTriangle') }),
            h('div', '日志已截断：每个模块最多返回 500 条记录，这里不代表完整历史。'),
          )) : null,
      ),
    ));
    panel.appendChild(body);

    panel.appendChild(h('div.editor-foot',
      h('span.message-detail-note',
        '页面为只读，不提供配置保存或直接发消息的接口。'),
      h('button.btn.btn-ghost.btn-sm.message-copy-path', {
        onclick: () => { void copyText(t.path); },
      }, h('span', { html: icon('copy', 'icon-sm') }), '复制路径'),
      h('button.btn.btn-outline.btn-sm', {
        onclick: (ev) => {
          const btn = ev.currentTarget;
          const ic = btn.querySelector('.icon');
          const sp = h('span.spinner.spinner-sm');
          ic.replaceWith(sp); btn.disabled = true;
          void mutate(() => Api.checkMessageModule(Store.user, t.name), '消息模块连接状态已更新').finally(() => { if (btn.isConnected) { sp.replaceWith(h('span', {html:icon('check','icon-sm')})); btn.disabled=false; } });
        },
      }, h('span', { html: icon('plug', 'icon-sm') }), '检测连接'),
      h('button.btn.btn-danger.btn-sm', {
        onclick: async () => {
          const ok = await Overlay.confirm({
            title: '删除消息模块',
            description: '这会删除实际的 message/out 模块包目录，不是单纯解绑。日志与临时文件也会一并移除。',
            detail: t.path, confirmLabel: '删除模块', tone: 'danger',
          });
          if (ok) await mutate(() => Api.deleteMessageModule(Store.user, t.name), '消息模块已删除');
        },
      }, h('span', { html: icon('trash', 'icon-sm') }), '删除模块'),
    ));

    detailHost.appendChild(panel);
  }

  drawList();
  drawDetail();
  inner.appendChild(h('div.split.messages-split', listHost, detailHost));
  return pageEl;
}


