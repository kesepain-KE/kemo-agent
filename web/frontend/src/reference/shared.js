import { t as tr } from '../i18n'
import { useUiStore } from '../store/ui'

/**
 * 原型展示层移植自 E:\\cache\\front，保留原样布局与控件。
 * 不导入 mock/data.js、模拟 app.js 或原型 Markdown；所有读写经 production api/client。
 * React 宿主提供认证身份、真实查询快照、安全 Markdown 与声明式 ModulePanel。
 */
import * as Api from '../api/client'
import { copyText as writeClipboard } from '../utils/clipboard'
import { icon } from './icons.js'
export let Current;
export const Store = {user:'', sessionId:'', running:false};
export const Bus = {emit: (_event, payload) => navigate('/chat', {prompt:payload?.text || ''})};
export let LIVE_AGENTS, LIVE_SKILLS, LIVE_KNOWLEDGE, LIVE_MEMORY, LIVE_IMPORTANT_MEMORY;
export let LIVE_TASKS, LIVE_SENSE, LIVE_EXPANDS, LIVE_MESSAGES, LIVE_RUNTIME, LIVE_RUNTIME_LOGS, LIVE_CAPABILITIES;
export const navigate = (path, params={}) => Current.navigate(path, params);
export const renderMarkdownWithWidgets = (content, options = {}) => Current.mountMarkdown(String(content ?? ''), options);

export function closeOverlays() {
  Overlay.stack.slice().forEach(entry => entry.close());
}
export async function readDetail(fn, onResult) {
  const ctx = Current;
  try { const result = await fn(); if (ctx.active) onResult(result); }
  catch (error) { if (ctx.active) toast(error instanceof Error ? error.message : '读取失败', {kind:'error'}); }
}
export async function mutate(fn, message, after) {
  const ctx = Current;
  if (!ctx.active || ctx.busy) return false;
  ctx.busy = true;
  ctx.host?.setAttribute('aria-busy', 'true');
  try {
    await fn();
    if (ctx.active) {
      if (message) toast(message, {kind:'success'});
      after?.();
      await ctx.refresh();
    }
    return true;
  } catch(error) {
    if (ctx.active) {
      toast(error instanceof Error ? error.message : '操作失败', {kind:'error',ttl:7000});
      ctx.restoreSwitches?.();
    }
    return false;
  } finally {
    ctx.busy=false; ctx.host?.removeAttribute('aria-busy');
  }
}
export async function refreshPage() {
  const ctx = Current;
  try { await ctx.refresh(); }
  catch(error) { if(ctx.active) toast(error.message || '刷新失败',{kind:'error'}); }
}
export function buildTokenChart(trend) {
  const values = (trend || []).map(v => Math.max(0, Number(v) || 0));
  if (!values.length) return emptyState({title:'暂无小时趋势'});
  const ns='http://www.w3.org/2000/svg';
  const svg=document.createElementNS(ns,'svg');
  svg.setAttribute('viewBox','0 0 960 260'); svg.setAttribute('class','chart-svg runtime-token-chart');
  svg.setAttribute('role','img'); svg.setAttribute('aria-label','今日按小时 Token 总量趋势，完整数据见下方表格');
  const max=Math.max(...values,1);
  const x=i=>82+i/Math.max(values.length-1,1)*850;
  const y=v=>38+(1-v/max)*168;
  const label=(text,px,py,anchor='middle')=>{
    const node=document.createElementNS(ns,'text');
    node.setAttribute('x',String(px));node.setAttribute('y',String(py));
    node.setAttribute('text-anchor',anchor);node.setAttribute('class','runtime-chart-label');
    node.textContent=text;svg.appendChild(node);
  };
  label(`最高 ${fmtThousands(Math.max(...values))} · 最低 ${fmtThousands(Math.min(...values))} Tokens`,82,18,'start');
  for(let i=0;i<=4;i++){
    const line=document.createElementNS(ns,'line');
    line.setAttribute('x1','82');line.setAttribute('x2','932');
    line.setAttribute('y1',String(y(max*i/4)));line.setAttribute('y2',String(y(max*i/4)));
    line.setAttribute('class','chart-grid-line');svg.appendChild(line);
    label(fmtThousands(Math.round(max*i/4)),72,y(max*i/4)+4,'end');
  }
  const poly=document.createElementNS(ns,'polyline');
  poly.setAttribute('points',values.map((v,i)=>`${x(i)},${y(v)}`).join(' '));
  poly.setAttribute('class','chart-line');svg.appendChild(poly);
  values.forEach((v,i)=>{
    const dot=document.createElementNS(ns,'circle');dot.setAttribute('cx',String(x(i)));dot.setAttribute('cy',String(y(v)));dot.setAttribute('r','3');dot.setAttribute('class','chart-dot');
    const title=document.createElementNS(ns,'title');title.textContent=`${String(i).padStart(2,'0')}:00 · ${fmtThousands(v)} tokens`;dot.appendChild(title);svg.appendChild(dot);
    label(String(i).padStart(2,'0'),x(i),229);
  });
  return svg;
}
export function chooseFile(accept, maxMiB, onFile) {
  const ctx=Current;
  const input = document.createElement('input'); input.type='file'; input.accept=accept;
  input.onchange = () => {
    const file=input.files?.[0]; if(!file || !ctx.active) return;
    if(file.size > maxMiB*1024*1024) {toast(`文件不能超过 ${maxMiB} MiB`,{kind:'error'});return;}
    void onFile(file);
  };
  input.click();
}
export function downloadUrl(url) {
  const a=document.createElement('a'); a.href=url; a.download=''; a.click();
}
/* ============================================================================
   dom · 极简 DOM 构建 / 事件 / 格式化 工具
   ========================================================================= */

/**
 * 创建元素。
 *   h('div.card', {onclick}, '文本', h('span', '子'))
 *   h('button.btn.btn-primary', {disabled: true}, '发送')
 *   h('svg.icon') → 自动带 viewBox
 */
export function h(spec, props, ...children) {
  // 解析 "tag.class1.class2#id"
  let tag = 'div', id = null;
  const classes = [];
  const m = String(spec || 'div').match(/^([a-zA-Z][\w-]*)?((?:[.#][\w-]+)*)$/);
  if (m) {
    if (m[1]) tag = m[1];
    const rest = m[2] || '';
    rest.split(/(?=[.#])/).filter(Boolean).forEach((tok) => {
      if (tok[0] === '.') classes.push(tok.slice(1));
      else if (tok[0] === '#') id = tok.slice(1);
    });
  } else {
    tag = spec;
  }

  const isSvg = tag === 'svg';
  const el = isSvg
    ? document.createElementNS('http://www.w3.org/2000/svg', 'svg')
    : document.createElement(tag);

  if (isSvg) el.setAttribute('viewBox', '0 0 24 24');
  if (id) el.id = id;
  if (classes.length) el.className = classes.join(' ');

  if (props && typeof props === 'object' && !(props instanceof Node) && !Array.isArray(props)) {
    for (const [k, v] of Object.entries(props)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class' || k === 'className') {
        el.className = (el.className ? el.className + ' ' : '') + v;
      } else if (k === 'style' && typeof v === 'object') {
        for (const [sk, sv] of Object.entries(v)) {
          if (sk.startsWith('--')) el.style.setProperty(sk, sv);
          else el.style[sk] = sv;
        }
      } else if (k === 'dataset' && typeof v === 'object') {
        Object.assign(el.dataset, v);
      } else if (k === 'html') {
        el.innerHTML = v;
      } else if (k === 'text') {
        el.textContent = v;
      } else if (k.startsWith('on') && typeof v === 'function') {
        el.addEventListener(k.slice(2).toLowerCase(), v);
      } else if (isSvg) {
        el.setAttribute(k, v === true ? '' : v);
      } else if (k in el && k !== 'list' && typeof v !== 'object') {
        try { el[k] = v; } catch { el.setAttribute(k, v); }
      } else {
        el.setAttribute(k, v === true ? '' : v);
      }
    }
  } else if (props !== undefined && props !== null) {
    children.unshift(props);
  }

  append(el, children);
  if (el.tagName === 'DIV' && classes.includes('entry') && typeof props?.onclick === 'function') {
    el.setAttribute('role', 'button'); el.tabIndex = 0;
    el.addEventListener('keydown', event => {
      if (event.target === el && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); el.click(); }
    });
  }
  return el;
}

export function append(el, children) {
  // 容忍单个节点 / 字符串 / 类数组（不要求调用方一定传数组）
  if (children === null || children === undefined || children === false || children === true) return;
  if (children instanceof Node || typeof children === 'string' || typeof children === 'number') {
    children = [children];
  }
  if (typeof children[Symbol.iterator] !== 'function') {
    children = [children];
  }
  for (const c of children) {
    if (c === null || c === undefined || c === false || c === true) continue;
    if (Array.isArray(c)) { append(el, c); continue; }
    if (c instanceof Node) { el.appendChild(c); continue; }
    el.appendChild(document.createTextNode(String(c)));
  }
}

/** 清空并重新填充 */
export function fill(el, ...children) {
  el.textContent = '';
  append(el, children);
  return el;
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export function on(el, type, fn, opts) {
  el.addEventListener(type, fn, opts);
  return () => el.removeEventListener(type, fn, opts);
}

/** 事件委托 */
export function delegate(root, type, selector, fn) {
  return on(root, type, (ev) => {
    const t = ev.target.closest(selector);
    if (t && root.contains(t)) fn(ev, t);
  });
}

/* ============================ 格式化 ============================ */

/** 字节 → 人类可读 */
export function fmtBytes(n) {
  if (n === null || n === undefined || isNaN(n)) return '—';
  if (n < 1024) return n + ' B';
  const u = ['KB', 'MB', 'GB', 'TB'];
  let v = n / 1024, i = 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return (v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2)) + ' ' + u[i];
}

/** 大数简写：1234 → 1.2k */
export function fmtNum(n) {
  if (n === null || n === undefined || isNaN(n)) return '—';
  const a = Math.abs(n);
  if (a >= 1e9) return (n / 1e9).toFixed(a >= 1e10 ? 0 : 1) + 'B';
  if (a >= 1e6) return (n / 1e6).toFixed(a >= 1e7 ? 0 : 1) + 'M';
  if (a >= 1e4) return (n / 1e3).toFixed(0) + 'k';
  return String(n);
}

/** 千分位 */
export function fmtThousands(n) {
  if (n === null || n === undefined || isNaN(n)) return '—';
  return Number(n).toLocaleString('en-US');
}

export function pad2(n) { return n < 10 ? '0' + n : String(n); }

/**
 * 相对时间（中文）。输入 ISO 字符串或 Date。
 * 规则与 kemo 一致：1 分钟内“刚刚”，然后 分钟/小时/昨天/日期。
 */
export function fmtRelative(input) {
  if (!input) return '—';
  const d = input instanceof Date ? input : new Date(input);
  if (isNaN(d.getTime())) return String(input);
  const now = Date.now();
  const diff = now - d.getTime();
  const sec = Math.floor(diff / 1000);
  const language = useUiStore.getState().language;
  if (language !== 'zh-CN') {
    const relative = new Intl.RelativeTimeFormat(language, { numeric: 'auto' });
    if (sec < 45) return tr('刚刚');
    if (sec < 60) return relative.format(-sec, 'second');
    if (sec < 3600) return relative.format(-Math.floor(sec / 60), 'minute');
    if (sec < 86400) return relative.format(-Math.floor(sec / 3600), 'hour');
    return relative.format(-Math.floor(sec / 86400), 'day');
  }
  if (sec < 45) return '刚刚';
  if (sec < 60) return sec + ' 秒前';
  const min = Math.floor(sec / 60);
  if (min < 60) return min + ' 分钟前';
  const hr = Math.floor(min / 60);
  if (hr < 24 && new Date(now).getDate() === d.getDate()) return hr + ' 小时前';
  const day = Math.floor(hr / 24);
  if (day === 1) return '昨天 ' + pad2(d.getHours()) + ':' + pad2(d.getMinutes());
  if (day < 7) return day + ' 天前';
  if (d.getFullYear() === new Date(now).getFullYear()) {
    return (d.getMonth() + 1) + '月' + d.getDate() + '日';
  }
  return d.getFullYear() + '年' + (d.getMonth() + 1) + '月' + d.getDate() + '日';
}

/** 绝对时间：2026-10-10 14:32 */
export function fmtDateTime(input, opts = {}) {
  if (!input) return '—';
  const d = input instanceof Date ? input : new Date(input);
  if (isNaN(d.getTime())) return String(input);
  if (opts.dateOnly) return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  if (opts.timeOnly) return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

/** 时长毫秒 → 人类可读 */
export function fmtDuration(ms) {
  if (ms === null || ms === undefined || isNaN(ms)) return '—';
  if (ms < 1000) return Math.round(ms) + ' ms';
  const s = ms / 1000;
  if (s < 60) return (s >= 10 ? s.toFixed(1) : s.toFixed(2)) + ' s';
  const m = Math.floor(s / 60);
  const rs = Math.round(s % 60);
  if (m < 60) return m + ' 分 ' + rs + ' 秒';
  const hh = Math.floor(m / 60);
  return hh + ' 小时 ' + (m % 60) + ' 分';
}

/** 秒 → mm:ss */
export function fmtClock(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  return pad2(Math.floor(s / 60)) + ':' + pad2(s % 60);
}

/** 时段问候 */
export function greeting(date = new Date()) {
  const hr = date.getHours();
  if (hr < 5) return '夜深了';
  if (hr < 11) return '上午好';
  if (hr < 13) return '中午好';
  if (hr < 18) return '下午好';
  return '晚上好';
}

/** ISO 时间偏移若干分钟，用于 mock 数据 */
export function isoAgo(minutes) { return new Date(Date.now() - minutes * 60000).toISOString(); }
export function isoIn(minutes) { return new Date(Date.now() + minutes * 60000).toISOString(); }

/* ============================ 杂项 ============================ */

export function uid(prefix = 'id') {
  return prefix + '_' + Math.random().toString(36).slice(2, 10);
}

/** 生成 kemo 风格的 run_id：字母数字下划线连字符，8–128 位 */
export function mockRunId() {
  return 'run_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
}

/** 生成 kemo 风格的 client_id */
export function mockClientId() {
  return 'web_' + Math.random().toString(36).slice(2, 12);
}

export function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

export async function copyText(text) {
  try { await writeClipboard(String(text || '')); toast('已复制到剪贴板', {kind:'success',ttl:1800}); return true; }
  catch(error) { toast(error.message || '复制失败，请手动选择文本', {kind:'error'}); return false; }
}

/** 简易语法高亮：仅在真实代码块上使用，输入已 escape */
export function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** 截断中间，保留首尾 */
export function truncateMiddle(s, max = 60) {
  if (!s || s.length <= max) return s || '';
  const head = Math.ceil((max - 1) / 2);
  const tail = Math.floor((max - 1) / 2);
  return s.slice(0, head) + '…' + s.slice(-tail);
}

/** 数字 → 百分比展示 */
export function fmtPercent(v, digits = 0) {
  if (v === null || v === undefined || isNaN(v)) return '—';
  return (v * 100).toFixed(digits) + '%';
}

/** 根据文件扩展名选择图标 */
export function fileIconName(ext) {
  const e = String(ext || '').toLowerCase().replace(/^\./, '');
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'ico', 'svg'].includes(e)) return 'image';
  if (['mp4', 'webm', 'mov', 'm4v', 'avi', 'mkv'].includes(e)) return 'video';
  if (['mp3', 'wav', 'ogg', 'm4a', 'flac', 'webm'].includes(e)) return 'audio';
  if (['md', 'txt', 'log'].includes(e)) return 'fileText';
  if (['json', 'js', 'ts', 'tsx', 'jsx', 'py', 'sh', 'yaml', 'yml', 'toml', 'css', 'html', 'sql'].includes(e)) return 'fileCode';
  if (['zip', 'tar', 'gz', '7z', 'rar'].includes(e)) return 'archive';
  return 'file';
}

/** 媒体类型分组 */
export function mediaKindOf(ext) {
  const n = fileIconName(ext);
  if (n === 'image') return 'image';
  if (n === 'video') return 'video';
  if (n === 'audio') return 'audio';
  return 'file';
}

/** 为元素挂载 tooltip */
export let _tipEl = null;
export function attachTip(el, text) {
  if (!text) return;
  el.addEventListener('mouseenter', () => {
    if (_tipEl) _tipEl.remove();
    _tipEl = h('div.tip', text);
    document.body.appendChild(_tipEl);
    const r = el.getBoundingClientRect();
    const tr = _tipEl.getBoundingClientRect();
    let left = r.left + r.width / 2 - tr.width / 2;
    left = clamp(left, 6, window.innerWidth - tr.width - 6);
    let top = r.top - tr.height - 7;
    if (top < 4) top = r.bottom + 7;
    _tipEl.style.left = left + 'px';
    _tipEl.style.top = top + 'px';
  });
  el.addEventListener('mouseleave', () => {
    if (_tipEl) { _tipEl.remove(); _tipEl = null; }
  });
}

/** 点击外部关闭 */
export function onClickOutside(el, fn) {
  const handler = (ev) => { if (!el.isConnected) { cleanup(); return; } if (!el.contains(ev.target)) fn(ev); };
  const timer=setTimeout(() => document.addEventListener('mousedown', handler), 0);
  const cleanup=()=>{clearTimeout(timer);document.removeEventListener('mousedown', handler)};
  Current?.registerCleanup?.(cleanup);
  return cleanup;
}


/* ---------------------------- 通知 ---------------------------- */
export function toast(message, opts = {}) {
  let stack = $('.toast-stack');
  if (!stack) {
    stack = h('div.toast-stack', { role: 'status', 'aria-live': 'polite' });
    document.body.appendChild(stack);
  }
  const kind = opts.kind || 'default';
  const iconName = kind === 'success' ? 'checkCircle' : kind === 'error' ? 'xCircle' : kind === 'warning' ? 'alertTriangle' : 'info';
  const color = kind === 'success' ? 'var(--success)' : kind === 'error' ? 'var(--danger)' : kind === 'warning' ? 'var(--warning)' : 'var(--text-tertiary)';
  const el = h('div.toast',
    h('span', { style: { color, marginTop: '1px' }, html: icon(iconName) }),
    h('div', { style: { flex: '1', minWidth: '0' } },
      h('div', { style: { fontWeight: '540' } }, message),
      opts.detail ? h('div', { style: { fontSize: 'var(--t-xs)', color: 'var(--text-tertiary)', marginTop: '2px', lineHeight: '1.5' } }, opts.detail) : null,
    ),
    h('button.icon-btn', {
      style: { width: '20px', height: '20px', marginTop: '-1px' },
      onclick: () => dismiss(),
      'aria-label': '关闭通知',
    }, h('span', { html: icon('close', 'icon-sm') })),
  );
  const dismiss = () => {
    el.classList.add('is-leaving');
    setTimeout(() => el.remove(), 200);
  };
  stack.appendChild(el);
  const ttl = opts.ttl || 3600;
  if (ttl > 0) setTimeout(dismiss, ttl);
  return dismiss;
}

/* ---------------------------- 弹层管理 ---------------------------- */
export const Overlay = {
  stack: [],
  /**
   * 打开弹层。
   * opts: { title, sub, body(Node), foot(Node[]), size, onClose, dismissable }
   */
  modal(opts) {
    const body = h('div.modal-body');
    append(body, opts.body);
    const modal = h('div.modal' + (opts.size ? '.modal-' + opts.size : ''),
      opts.title ? h('div.modal-head',
        h('div',
          h('div.modal-title', opts.title),
          opts.sub ? h('div.modal-sub', opts.sub) : null,
        ),
        h('button.icon-btn', { onclick: () => close(), 'aria-label': tr('关闭') }, h('span', { html: icon('close') })),
      ) : null,
      body,
      opts.foot ? h('div.modal-foot' + (opts.footBetween ? '.between' : ''), opts.foot) : null,
    );
    const overlay = h('div.overlay.overlay-center', { role: 'dialog', 'aria-modal': 'true', 'aria-label':opts.title || '操作窗口' }, modal);
    const previousFocus=document.activeElement;
    const close = () => {
      overlay.remove();
      document.removeEventListener('keydown', onKey);
      Overlay.stack = Overlay.stack.filter((s) => s !== entry);
      if (opts.onClose) opts.onClose();
      if(previousFocus?.isConnected)previousFocus.focus();
    };
    const onKey = (ev) => {
      if(Overlay.stack.at(-1)?.overlay!==overlay)return;
      if (ev.key === 'Escape' && opts.dismissable !== false) { ev.stopPropagation(); close(); }
      if(ev.key==='Tab'){
        const focusable=$$('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),a[href]',modal);
        const first=focusable[0],last=focusable.at(-1);
        if(ev.shiftKey && document.activeElement===first){ev.preventDefault();last?.focus()}
        else if(!ev.shiftKey && document.activeElement===last){ev.preventDefault();first?.focus()}
      }
    };
    if (opts.dismissable !== false) {
      overlay.addEventListener('mousedown', (ev) => { if (ev.target === overlay) close(); });
    }
    document.addEventListener('keydown', onKey);
    document.body.appendChild(overlay);
    const entry = { overlay, close };
    Overlay.stack.push(entry);
    setTimeout(() => {
      const f = modal.querySelector('input, textarea, button.btn-primary');
      if (f) f.focus();
    }, 60);
    return entry;
  },

  confirm(opts) {
    return new Promise((resolve) => {
      let done = false;
      const finish = (v) => { if (!done) { done = true; resolve(v); } };
      const tone = opts.tone || 'warning';
      const entry = Overlay.modal({
        title: opts.title || '确认操作',
        size: 'sm',
        body: [
          h('div', { style: { display: 'flex', gap: 'var(--s-3)', alignItems: 'flex-start' } },
            h('span', { style: { color: tone === 'danger' ? 'var(--danger)' : 'var(--warning)', marginTop: '1px' }, html: icon(tone === 'danger' ? 'alertTriangle' : 'info', 'icon-lg') }),
            h('div', { style: { fontSize: 'var(--t-sm)', lineHeight: '1.65', color: 'var(--text-secondary)' } },
              opts.description || '',
              opts.detail ? h('div', {
                style: {
                  marginTop: 'var(--s-3)', padding: '9px 11px', borderRadius: 'var(--r-sm)',
                  background: 'var(--bg-inset)', border: '1px solid var(--border-subtle)',
                  fontFamily: 'var(--font-mono)', fontSize: 'var(--t-xs)', color: 'var(--text-primary)',
                  wordBreak: 'break-all',
                },
              }, opts.detail) : null,
            ),
          ),
        ],
        foot: [
          h('button.btn.btn-ghost', { onclick: () => { finish(false); entry.close(); } }, opts.cancelLabel || tr('取消')),
          h('button.btn' + (tone === 'danger' ? '.btn-danger-solid' : '.btn-primary'), {
            onclick: () => { finish(true); entry.close(); },
          }, opts.confirmLabel || tr('确认')),
        ],
        onClose: () => finish(false),
      });
    });
  },

  /** 抽屉 */
  drawer(opts) {
    const side = opts.side || 'right';
    const body = h('div.drawer-body');
    append(body, opts.body);
    const drawer = h('div.drawer' + (side === 'right' ? '.drawer-right' : '.drawer-left') + (opts.wide ? '.drawer-wide' : ''),
      h('div.drawer-head',
        opts.title ? h('div', { style: { flex: '1', minWidth: '0' } },
          h('div', { style: { fontSize: 'var(--t-md)', fontWeight: '600' } }, opts.title),
          opts.sub ? h('div', { style: { fontSize: 'var(--t-xs)', color: 'var(--text-tertiary)', marginTop: '2px' } }, opts.sub) : null,
        ) : h('div', { style: { flex: '1' } }),
        opts.headExtra || null,
        h('button.icon-btn', { onclick: () => close(), 'aria-label': tr('关闭') }, h('span', { html: icon('close') })),
      ),
      body,
      opts.foot ? h('div.drawer-foot', opts.foot) : null,
    );
    const overlay = h('div.overlay', { style: side === 'right' ? { justifyContent: 'flex-end' } : { justifyContent: 'flex-start' } }, drawer);
    const close = () => {
      overlay.remove();
      document.removeEventListener('keydown', onKey);
      if (opts.onClose) opts.onClose();
    };
    const onKey = (ev) => { if (ev.key === 'Escape') { ev.stopPropagation(); close(); } };
    overlay.addEventListener('mousedown', (ev) => { if (ev.target === overlay) close(); });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(overlay);
    return { overlay, close, body };
  },
};

/* ---------------------------- 危险色 / 状态映射 ---------------------------- */
export const STATUS_TONE = {
  completed: 'success', success: 'success', enabled: 'success', active: 'success',
  running: 'accent', processing: 'accent', queued: 'info', pending: 'info',
  failed: 'danger', error: 'danger', invalid: 'danger', exhausted: 'danger',
  cancelled: 'neutral', paused: 'warning', retry_wait: 'warning', stopping: 'warning',
  skipped: 'neutral', none: 'neutral', waiting: 'warning',
};

export function statusTone(status) { return STATUS_TONE[status] || 'neutral'; }

export const STATUS_TEXT = {
  open: '进行中', closed: '已归档',
  pending: '待处理', approved: '已批准', running: '执行中', completed: '已完成',
  failed: '失败', cancelled: '已取消', paused: '已暂停', stopping: '停止中',
  queued: '排队中', processing: '处理中', retry_wait: '等待重试', exhausted: '重试耗尽',
  none: '未开始', idle: '空闲', enabled: '已启用', disabled: '已停用',
  healthy: '正常', unhealthy: '异常', active: '启用', filtered: '已过滤', invalid: '无效',
  success: '成功', error: '错误', never: '从未执行',
  up_to_date: '已是最新', update_available: '有新版本', local_newer: '本地较新', check_failed: '检查失败',
  valid: '有效', untracked: '未跟踪', empty: '空', connected: '已连接', disconnected: '未连接',
  stopped: '已停止', accepted_current_run: '已接收', queued_next_turn: '下一轮',
};

export function statusText(s) { return STATUS_TEXT[s] ? tr(STATUS_TEXT[s]) : s || '—'; }

/* ---------------------------- 空态 ---------------------------- */
export function emptyState(opts = {}) {
  return h('div.empty',
    h('div.empty-icon', h('span', { html: icon(opts.icon || 'inbox', 'icon-lg') })),
    h('div.empty-title', opts.title || tr('暂无内容')),
    opts.desc ? h('div.empty-desc', opts.desc) : null,
    opts.action || null,
  );
}

/* ---------------------------- 页头 ---------------------------- */
export function pageHead(opts) {
  return h('div.page-head',
    h('div', { style: { minWidth: '0' } },
      h('h1.page-title', opts.title),
      opts.desc ? h('div.page-desc', opts.desc) : null,
    ),
    opts.actions ? h('div.page-head-actions', opts.actions) : null,
  );
}

/* ---------------------------- 统计磁贴 ---------------------------- */
export function statTile(label, value, opts = {}) {
  return h('div.stat',
    h('div.stat-label',
      opts.icon ? h('span', { html: icon(opts.icon, 'icon-sm') }) : null,
      label,
    ),
    h('div.stat-value', { style: opts.color ? { color: opts.color } : {} },
      String(value),
      opts.unit ? h('span.unit', opts.unit) : null,
    ),
    opts.foot ? h('div.stat-foot', opts.foot) : null,
  );
}

/* ---------------------------- 徽标 ---------------------------- */
export function badge(text, tone) {
  return h('span.badge' + (tone && tone !== 'neutral' ? '.badge-' + tone : ''), text);
}
export function statusBadge(status, labelOverride) {
  const tone = statusTone(status);
  return h('span.badge' + (tone !== 'neutral' ? '.badge-' + tone : ''),
    h('span.dot' + (tone === 'success' ? '.dot-success' : tone === 'danger' ? '.dot-danger' : tone === 'warning' ? '.dot-warning' : tone === 'accent' ? '.dot-accent' : ''),
      { style: { width: '5px', height: '5px', background: 'currentColor' } }),
    labelOverride || statusText(status),
  );
}

/* ---------------------------- 开关 ---------------------------- */
export function makeSwitch(on, onChange, opts = {}) {
  const el = h('button.switch' + (on ? '.is-on' : ''), {
    role: 'switch',
    'aria-checked': on ? 'true' : 'false',
    'aria-label': opts.label || '切换',
    disabled: !!opts.disabled,
    onclick: async () => {
      const next = !el.classList.contains('is-on');
      el.classList.toggle('is-on', next);
      el.setAttribute('aria-checked', next ? 'true' : 'false');
      el.disabled = true;
      try {
        const result = onChange ? await onChange(next) : true;
        if (result === false) {
          el.classList.toggle('is-on', on);
          el.setAttribute('aria-checked', on ? 'true' : 'false');
        } else on = next;
      } finally { el.disabled = !!opts.disabled; }
    },
  });
  return el;
}

/* ---------------------------- 搜索框 ---------------------------- */
export function makeSearchBox(opts = {}) {
  const input = h('input.input', {
    type: 'search',
    placeholder: opts.placeholder || '搜索…',
    value: opts.value || '',
    'aria-label': opts.placeholder || tr('搜索'),
  });
  const clear = h('button.search-clear', { 'aria-label': '清除搜索', onclick: () => { input.value = ''; input.dispatchEvent(new Event('input')); input.focus(); } },
    h('span', { html: icon('close', 'icon-sm') }));
  const box = h('div.search-box',
    h('span', { html: icon('search', 'icon-sm') }),
    input,
    clear,
  );
  if (opts.onInput) input.addEventListener('input', () => opts.onInput(input.value));
  box.input = input;
  return box;
}

/* ---------------------------- 复制按钮 ---------------------------- */
export function makeCopyButton(text, label) {
  const btn = h('button.btn.btn-ghost' + (label ? '' : '.btn-icon'), { title: tr('复制') },
    h('span', { html: icon('copy', label ? 'icon-sm' : '') }),
    label || null,
  );
  btn.addEventListener('click', async () => {
    if (!await copyText(typeof text === 'function' ? text() : text)) return;
    btn.style.color = 'var(--success)';
    setTimeout(() => { btn.style.color = ''; }, 1100);
  });
  return btn;
}

/* ---------------------------- 分页器 ---------------------------- */
export function makePager(page, totalPages, onGo) {
  if (totalPages <= 1) return null;
  const wrap = h('div.pager');
  const btn = (label, target, opts = {}) => h('button', {
    class: opts.active ? 'is-active' : '',
    disabled: opts.disabled,
    onclick: () => onGo(target),
  }, label);

  wrap.appendChild(btn('上一页', Math.max(1, page - 1), { disabled: page <= 1 }));
  const windowSize = 5;
  let from = Math.max(1, page - Math.floor(windowSize / 2));
  const to = Math.min(totalPages, from + windowSize - 1);
  from = Math.max(1, Math.min(from, to - windowSize + 1));
  if (from > 1) wrap.appendChild(btn('1', 1, { active: page === 1 }));
  if (from > 2) wrap.appendChild(h('span', { style: { padding: '0 2px' } }, '…'));
  for (let p = from; p <= to; p++) wrap.appendChild(btn(String(p), p, { active: p === page }));
  if (to < totalPages - 1) wrap.appendChild(h('span', { style: { padding: '0 2px' } }, '…'));
  if (to < totalPages) wrap.appendChild(btn(String(totalPages), totalPages, { active: page === totalPages }));
  wrap.appendChild(btn('下一页', Math.min(totalPages, page + 1), { disabled: page >= totalPages }));
  return wrap;
}

/* ---------------------------- 表格排序头 ---------------------------- */
export function sortableTh(label, key, state, onSort, extra) {
  const active = state.by === key;
  return h('th.sortable' + (extra ? '.' + extra : ''), {
    onclick: () => onSort(key, active && state.order === 'desc' ? 'asc' : 'desc'),
    'aria-sort': active ? (state.order === 'asc' ? 'ascending' : 'descending') : 'none',
    style: extra === 'num' ? { textAlign: 'right' } : {},
  }, h('span', { style: { display: 'inline-flex', alignItems: 'center', gap: '4px' } },
    label,
    active ? h('span', { style: { color: 'var(--accent)' }, html: icon(state.order === 'asc' ? 'chevronUp' : 'chevronDown', 'icon-sm') }) : null,
  ));
}



export function setContext(ctx) { Current=ctx; Object.assign(Store,{user:ctx.user,sessionId:ctx.sessionId,running:ctx.running}); }
export function setRuntimeLogs(data) { LIVE_RUNTIME_LOGS=data; }
export function setLiveData(data) {
  LIVE_AGENTS=data.agents; LIVE_SKILLS=data.skills; LIVE_KNOWLEDGE=data.knowledge;
  LIVE_MEMORY=data.memory; LIVE_IMPORTANT_MEMORY=data.important;
  LIVE_TASKS=data.tasks; LIVE_SENSE=data.sense; LIVE_EXPANDS=data.expand;
  LIVE_MESSAGES=data.messages; LIVE_RUNTIME=data.runtime; LIVE_RUNTIME_LOGS=data.logs;
  LIVE_CAPABILITIES=data.capabilities;
}

export { Api, icon };
