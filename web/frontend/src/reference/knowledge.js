import { t as tr } from '../i18n'
import { h, pageHead, chooseFile, mutate, Api, Store, icon, onClickOutside, toast, LIVE_KNOWLEDGE, makeSearchBox, fill, emptyState, fmtBytes, fmtRelative, renderMarkdownWithWidgets, Overlay, refreshPage, copyText } from './shared.js'
import { canPasteKnowledge, isCutEntry, isWithin, pasteKnowledgeClipboard, useKnowledgeClipboard } from '../store/knowledgeClipboard'

const SCOPES = ['user', 'shared', 'global'];
export const SCOPE_LABEL = {
  get user() { return tr('用户'); },
  get shared() { return tr('共享'); },
  get global() { return tr('全局'); },
};
const key = (entry) => `${entry.scope}:${entry.relative_path}`;
const parentPath = (path) => path.split('/').slice(0, -1).join('/');
const joinPath = (parent, name) => [parent, name].filter(Boolean).join('/');

export function renderKnowledgePage(ctx) {
  const user = Store.user;
  useKnowledgeClipboard.getState().activateUser(user);
  const state = ctx.viewState;
  let activeScope = state.knowledgeScope || 'all';
  let directoryPath = state.knowledgeDirectory || '';
  let query = state.knowledgeQuery || '';
  const expanded = new Set(state.knowledgeExpanded || []);
  const roots = LIVE_KNOWLEDGE.roots.map(root => ({ ...root, kind: 'directory', relative_path: '', name: SCOPE_LABEL[root.scope], root: true }));
  const entries = [
    ...LIVE_KNOWLEDGE.directories.map(item => ({ ...item, kind: 'directory' })),
    ...LIVE_KNOWLEDGE.documents.map(item => ({ ...item, kind: 'file', name: item.relative_path.split('/').pop() })),
  ];
  let selected = entries.find(entry => key(entry) === state.knowledgeSelected) || null;
  let draft = '';
  let savedContent = '';
  let loaded = false;
  let loading = false;
  let previewMode = 'preview';
  let closeMenu = () => {};
  let marked = new Set(state.knowledgeMarked || []);
  let anchor = '';
  let focusedEntry = state.knowledgeFocused || '';
  const treeHost = h('nav.knowledge-tree', { role: 'tree', 'aria-label': tr('知识目录') });
  const listHost = h('section.knowledge-directory-panel');
  const clipboardHost = h('div.knowledge-clipboard-status', { role: 'status', hidden: true });
  const editorHost = h('section.knowledge-preview-panel', { 'aria-label': tr('知识文件预览') });
  const page = h('div.page.knowledge-page', h('div.page-inner.wide'));
  const inner = page.firstElementChild;
  ctx.registerCleanup(closeContextMenu);

  function remember() {
    Object.assign(state, { knowledgeScope: activeScope, knowledgeDirectory: directoryPath,
      knowledgeSelected: selected ? key(selected) : '', knowledgeQuery: query,
      knowledgeExpanded: [...expanded], knowledgeMarked: [...marked], knowledgeFocused: focusedEntry });
  }
  function closeContextMenu() { closeMenu(); }
  function currentDirectory() {
    return roots.find(root => root.scope === activeScope) &&
      (entries.find(entry => entry.scope === activeScope && entry.kind === 'directory' && entry.relative_path === directoryPath)
       || roots.find(root => root.scope === activeScope));
  }
  const clipboard = () => {
    const value = useKnowledgeClipboard.getState().clipboard;
    return value?.user === user ? value : null;
  };
  const pending = () => ctx.busy || useKnowledgeClipboard.getState().pending;
  function pasteDirectory(entry) {
    return entry.kind === 'directory' ? entry
      : entries.find(item => item.scope === entry.scope && item.kind === 'directory' && item.relative_path === parentPath(entry.relative_path))
        || roots.find(root => root.scope === entry.scope);
  }
  function markedEntries(entry) {
    return marked.has(key(entry)) ? entries.filter(item => marked.has(key(item))) : [entry];
  }
  function copyEntries(entry, action) {
    if (entry.root || pending()) return;
    useKnowledgeClipboard.getState().setClipboard(user, markedEntries(entry), action);
  }
  async function pasteInto(directory) {
    const source = clipboard();
    if (pending() || !source || !canPasteKnowledge(source, directory) || !await canLeave() || !ctx.active) return;
    remember();
    let transferred = [];
    await mutate(async () => {
      try { transferred = await pasteKnowledgeClipboard(user, directory); }
      catch (error) {
        // Earlier entries of a multi-selection may already have moved.
        await ctx.refresh();
        throw error;
      }
    }, tr(source.action === 'copy' ? '知识条目已复制' : '知识条目已移动'), () => {
      state.knowledgeScope = directory.scope;
      state.knowledgeDirectory = directory.relative_path;
      state.knowledgeMarked = transferred.map(item => `${item.scope}:${item.path}`);
      const moved = selected && source.action === 'move' && transferred.find(item =>
        item.source.scope === selected.scope && isWithin(selected.relative_path, item.source.relative_path));
      if (moved) state.knowledgeSelected = `${moved.scope}:${moved.path}${selected.relative_path.slice(moved.source.relative_path.length)}`;
      expanded.add(`${directory.scope}:`);
      let path = '';
      for (const part of directory.relative_path.split('/').filter(Boolean)) {
        path = joinPath(path, part);
        expanded.add(`${directory.scope}:${path}`);
      }
      state.knowledgeExpanded = [...expanded];
    });
  }
  function drawClipboard() {
    const source = clipboard();
    clipboardHost.hidden = !source;
    fill(clipboardHost);
    if (!source) return;
    const name = source.entries.length === 1 ? source.entries[0].name : tr('{count} 个条目', { count: source.entries.length });
    clipboardHost.append(h('span', tr(source.action === 'copy' ? '待粘贴副本：{name}' : '待移动：{name}', { name })),
      h('button.icon-btn', { 'aria-label': tr('取消知识条目操作'), onclick: () => useKnowledgeClipboard.getState().clear() },
        h('span', { html: icon('close', 'icon-sm') })));
  }
  function drawNavigation() {
    const focused = document.activeElement?.closest('[data-entry]');
    const host = focused && (treeHost.contains(focused) ? treeHost : listHost.contains(focused) ? listHost : null);
    const entry = focused?.dataset.entry;
    drawTree(); drawList(); drawClipboard();
    if (host && entry) host.querySelector(`[data-entry="${CSS.escape(entry)}"]`)?.focus({ preventScroll: true });
  }
  async function canLeave() {
    return draft === savedContent || await Overlay.confirm({ title: tr('放弃未保存的修改？'),
      description: tr('离开后将丢弃当前文件的未保存修改。'), confirmLabel: tr('放弃修改') });
  }
  async function browse(entry, expand = true) {
    if (!await canLeave() || !ctx.active) return;
    activeScope = entry.scope;
    directoryPath = entry.kind === 'directory' ? entry.relative_path : parentPath(entry.relative_path);
    focusedEntry = key(entry);
    marked = new Set(entry.kind === 'file' ? [key(entry)] : []);
    expanded.add(`${entry.scope}:`);
    const parts = directoryPath.split('/').filter(Boolean);
    parts.forEach((_, index) => expanded.add(`${entry.scope}:${parts.slice(0, index + 1).join('/')}`));
    if (entry.kind === 'directory') {
      if (expand) expanded.add(key(entry)); else expanded.delete(key(entry));
    }
    else {
      selected = entry;
      previewMode = 'preview';
      loaded = false;
    }
    draft = savedContent;
    remember();
    draw();
    if (entry.kind === 'file') void loadDocument(entry);
  }
  async function loadDocument(entry) {
    loading = true;
    drawEditor();
    try {
      const result = await Api.getKnowledgeDocument(Store.user, entry.scope, entry.relative_path);
      if (!ctx.active || selected !== entry) return;
      draft = savedContent = result.content;
      loaded = true;
    } catch (error) {
      if (ctx.active && selected === entry) toast(error.message, { kind: 'error' });
    } finally {
      if (ctx.active && selected === entry) { loading = false; drawEditor(); }
    }
  }
  function children(entry) {
    return entries.filter(item => item.scope === entry.scope && parentPath(item.relative_path) === entry.relative_path)
      .sort((a, b) => (a.kind === 'directory' ? 0 : 1) - (b.kind === 'directory' ? 0 : 1) || a.name.localeCompare(b.name));
  }
  function matches(entry) {
    return !query || `${entry.name} ${entry.relative_path}`.toLocaleLowerCase().includes(query.toLocaleLowerCase());
  }
  function branchMatches(entry) {
    return matches(entry) || entry.kind === 'directory' && entries.some(item => item.scope === entry.scope &&
      item.relative_path.startsWith(entry.relative_path ? entry.relative_path + '/' : '') && matches(item));
  }
  function drawTree() {
    fill(treeHost);
    function branch(entry, depth) {
      if (query && !branchMatches(entry)) return null;
      const directory = entry.kind === 'directory';
      const open = directory && (expanded.has(key(entry)) || Boolean(query));
      const active = directory ? entry.scope === activeScope && entry.relative_path === directoryPath : selected && key(entry) === key(selected);
      const row = h('div.knowledge-tree-row' + (active ? '.is-active' : '') + (isCutEntry(clipboard(), entry) ? '.is-cut' : ''), {
        role: 'treeitem', tabindex: '0', 'aria-level': String(depth + 1),
        ...(directory ? { 'aria-expanded': String(open) } : {}),
        'aria-selected': String(Boolean(active)), 'aria-label': entry.name, title: entry.relative_path || entry.absolute_path,
        style: { '--tree-depth': depth },
        onclick: () => void browse(entry, !open),
        oncontextmenu: event => showContextMenu(event, entry),
        onkeydown: event => {
          if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); void browse(entry, !open); }
          if (event.key === 'ContextMenu' || event.shiftKey && event.key === 'F10') { event.preventDefault(); showContextMenu(event, entry); }
          if (directory && ['ArrowLeft', 'ArrowRight'].includes(event.key)) {
            event.preventDefault();
            if (event.key === 'ArrowLeft') expanded.delete(key(entry)); else expanded.add(key(entry));
            remember(); drawTree(); treeHost.querySelector(`[data-entry="${CSS.escape(key(entry))}"]`)?.focus();
          }
          if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
            event.preventDefault();
            const items = [...treeHost.querySelectorAll('[role="treeitem"]')];
            const index = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : items.indexOf(row) + (event.key === 'ArrowDown' ? 1 : -1);
            items[index]?.focus();
          }
        }, dataset: { entry: key(entry) },
      },
        h('span.knowledge-tree-toggle', { 'aria-hidden': 'true' }, directory ? h('span', { html: icon(open ? 'chevronDown' : 'chevronRight', 'icon-sm') }) : null),
        h('span.knowledge-entry-icon', { html: icon(directory ? 'folder' : entry.name.endsWith('.json') ? 'fileCode' : 'fileText', 'icon-sm') }),
        h('span.knowledge-entry-name', entry.name),
      );
      const group = h('div.knowledge-tree-branch', row);
      if (open) group.appendChild(h('div', { role: 'group' }, children(entry).map(item => branch(item, depth + 1))));
      return group;
    }
    roots.forEach(root => {
      const node = branch(root, 0);
      if (node) treeHost.appendChild(node);
    });
  }
  function drawList() {
    fill(listHost);
    const directory = currentDirectory();
    const breadcrumb = h('div.knowledge-breadcrumb',
      h('button.btn.btn-ghost.btn-sm', { onclick: async () => {
        if (!await canLeave()) return;
        activeScope = 'all'; directoryPath = ''; draft = savedContent; remember(); draw();
      } }, tr('全部')),
    );
    if (directory) {
      let path = '';
      [SCOPE_LABEL[activeScope], ...directoryPath.split('/').filter(Boolean)].forEach((part, index) => {
        if (index) path = joinPath(path, part);
        const target = { scope: activeScope, kind: 'directory', relative_path: path };
        breadcrumb.append(h('span', '/'), h('button.btn.btn-ghost.btn-sm', { onclick: () => void browse(target) }, part));
      });
    }
    const rows = activeScope === 'all' ? roots : query
      ? entries.filter(entry => entry.scope === activeScope && entry.relative_path.startsWith(directoryPath ? directoryPath + '/' : '') && matches(entry))
      : children(directory);
    listHost.append(h('div.knowledge-panel-head', breadcrumb,
      directory ? h('button.icon-btn', { title: tr('目录操作'), 'aria-label': tr('目录操作'), onclick: event => showContextMenu(event, directory) }, h('span', { html: icon('moreH') })) : null));
    const list = h('div.knowledge-directory-list', { role: 'listbox', tabindex: '0',
      'aria-multiselectable': 'true', 'aria-label': tr('当前目录'),
      oncontextmenu: event => { if (directory) showContextMenu(event, directory); } });
    rows.forEach(entry => {
      if (query && entry.root && !branchMatches(entry)) return;
      list.appendChild(h('div.knowledge-directory-entry' + (marked.has(key(entry)) ? '.is-selected' : '') + (isCutEntry(clipboard(), entry) ? '.is-cut' : ''),
        { role: 'option', 'aria-selected': String(marked.has(key(entry))), 'aria-label': entry.name,
          tabindex: '-1', dataset: { entry: key(entry) }, oncontextmenu: event => showContextMenu(event, entry) },
        h('button.knowledge-entry-open', { onclick: event => {
          focusedEntry = key(entry);
          if (!entry.root && (event.ctrlKey || event.metaKey || event.shiftKey)) {
            if (event.shiftKey && anchor) {
              const start = rows.findIndex(item => key(item) === anchor);
              const end = rows.indexOf(entry);
              marked = new Set(rows.slice(Math.min(start < 0 ? end : start, end), Math.max(start, end) + 1).filter(item => !item.root).map(key));
            } else {
              if (marked.has(key(entry))) marked.delete(key(entry)); else marked.add(key(entry));
              anchor = key(entry);
            }
            remember(); drawNavigation();
          } else { anchor = key(entry); void browse(entry); }
        } },
          h('span.knowledge-entry-icon', { html: icon(entry.kind === 'directory' ? 'folder' : 'fileText') }),
          h('span.knowledge-entry-copy', h('strong', { title: entry.name }, entry.name),
            h('small', entry.root ? entry.relative_path || LIVE_KNOWLEDGE.roots.find(root => root.scope === entry.scope).relative_path :
              entry.kind === 'directory' ? tr('文件夹') : `${fmtBytes(entry.size)} · ${fmtRelative(entry.updated_at * 1000)}`)),
        ),
        h('button.icon-btn', { title: tr('更多操作'), 'aria-label': tr('操作 {name}', { name: entry.name }), onclick: event => showContextMenu(event, entry) }, h('span', { html: icon('moreH', 'icon-sm') })),
      ));
    });
    if (!rows.length) list.appendChild(emptyState({ title: tr(query ? '没有匹配的知识文件' : '此文件夹为空'), icon: 'folder' }));
    listHost.appendChild(list);
  }
  function drawEditor() {
    fill(editorHost);
    if (!selected) {
      editorHost.appendChild(emptyState({ title: tr('选择知识文件'), desc: tr('从目录中选择文件，在此预览或编辑。'), icon: 'book' }));
      return;
    }
    editorHost.appendChild(h('div.knowledge-panel-head',
      h('div.knowledge-preview-title', h('strong', { title: selected.name }, selected.name),
        h('small', { title: selected.relative_path }, `${SCOPE_LABEL[selected.scope]} / ${selected.relative_path}`)),
      h('button.icon-btn', { title: tr('放大预览'), 'aria-label': tr('放大预览'), disabled: !loaded, onclick: () => openKnowledgeZoom(selected, draft) }, h('span', { html: icon('maximize') })),
    ));
    if (!loaded) {
      editorHost.appendChild(h('div.knowledge-preview-content', emptyState({ title: tr(loading ? '正在加载知识文件' : '读取失败'), icon: 'fileText',
        action: !loading ? h('button.btn.btn-outline', { onclick: () => void loadDocument(selected) }, tr('重试')) : null })));
      return;
    }
    const controls = h('div.knowledge-preview-toolbar', h('div.segments',
      ['preview', 'edit'].map(mode => h('button.segment' + (previewMode === mode ? '.is-active' : ''), {
        onclick: () => { previewMode = mode; drawEditor(); },
      }, tr(mode === 'preview' ? '预览' : '编辑')))), h('span.knowledge-file-meta', fmtBytes(new TextEncoder().encode(draft).length)));
    editorHost.appendChild(controls);
    const dirtyFlag = h('span.knowledge-unsaved', { hidden: draft === savedContent }, tr('有未保存修改'));
    if (previewMode === 'edit') {
      const editor = h('textarea.textarea.mono.editor-md.knowledge-document-editor', { 'aria-label': tr('知识文件内容'), spellcheck: 'false',
        oninput: event => { draft = event.target.value; dirtyFlag.hidden = draft === savedContent; } });
      editor.value = draft;
      editorHost.appendChild(editor);
    } else editorHost.appendChild(h('div.knowledge-document-preview', renderMarkdownWithWidgets(draft)));
    editorHost.appendChild(h('div.knowledge-preview-foot', dirtyFlag, h('div.spacer'),
      h('button.btn.btn-ghost.btn-sm', { onclick: async () => { if (await canLeave()) void loadDocument(selected); } }, tr('撤销')),
      h('button.btn.btn-primary.btn-sm', { onclick: () => {
        remember();
        void mutate(() => Api.putKnowledgeDocument(Store.user, selected.scope, selected.relative_path, draft), tr('知识文件已保存'), () => { savedContent = draft; });
      } }, tr('保存')),
    ));
  }
  function draw() {
    drawNavigation(); drawEditor();
  }
  function handleClipboardKey(event, menuEntry) {
    const target = event.target;
    if (!(target instanceof HTMLElement) || target.closest('input, textarea, select, [contenteditable="true"]')
      || !(event.ctrlKey || event.metaKey) || pending()) return;
    const command = event.key.toLowerCase();
    if (command === 'a' && !menuEntry && target.closest('.knowledge-directory-list') && activeScope !== 'all') {
      event.preventDefault();
      marked = new Set([...listHost.querySelectorAll('[data-entry]')].map(row => row.dataset.entry));
      remember(); drawNavigation();
      listHost.querySelector('.knowledge-directory-list')?.focus();
      return;
    }
    const selectedKey = target.closest('[data-entry]')?.dataset.entry
      || (listHost.contains(target) && marked.size ? marked.values().next().value : focusedEntry);
    const entry = menuEntry || entries.find(item => key(item) === selectedKey)
      || roots.find(root => key(root) === focusedEntry);
    if ((command === 'c' || command === 'x') && entry && !entry.root) {
      event.preventDefault(); copyEntries(entry, command === 'c' ? 'copy' : 'move'); closeContextMenu();
    } else if (command === 'v') {
      const directory = menuEntry ? pasteDirectory(menuEntry) : treeHost.contains(target) && entry ? pasteDirectory(entry) : currentDirectory();
      if (directory && canPasteKnowledge(clipboard(), directory)) {
        event.preventDefault(); closeContextMenu(); void pasteInto(directory);
      }
    }
  }
  treeHost.addEventListener('keydown', handleClipboardKey);
  listHost.addEventListener('keydown', handleClipboardKey);
  function pathDialog(title, entry, mode) {
    const creating = mode === 'file' || mode === 'directory';
    const renaming = mode === 'rename';
    const parent = entry.kind === 'directory' ? entry.relative_path : parentPath(entry.relative_path);
    const initial = creating ? joinPath(parent, mode === 'file' ? 'untitled.md' : tr('新建文件夹')) : entry.relative_path;
    const input = h('input.input', { value: initial, 'aria-label': tr(renaming ? '名称' : '相对路径'), required: true });
    if (renaming) input.value = entry.name;
    const form = h('form.knowledge-path-form', { onsubmit: async event => {
      event.preventDefault();
      const value = input.value.trim();
      if (!value || !await canLeave() || !ctx.active) return;
      if (renaming && /[/\\]/.test(value)) { input.setCustomValidity(tr('名称不能包含路径分隔符')); input.reportValidity(); return; }
      const targetScope = entry.scope;
      const targetPath = renaming ? joinPath(parentPath(entry.relative_path), value) : value;
      const wasSelected = selected && selected.scope === entry.scope && (selected.relative_path === entry.relative_path || selected.relative_path.startsWith(entry.relative_path + '/'));
      submit.disabled = true;
      const success = await mutate(() => creating
        ? Api.createKnowledgeEntry(Store.user, entry.scope, targetPath, mode)
        : Api.transferKnowledgeEntry(Store.user, entry.scope, entry.relative_path, 'move', targetScope, targetPath),
      tr('知识目录已更新'), () => {
        if (renaming) useKnowledgeClipboard.getState().discard(entry.scope, entry.relative_path);
        if (wasSelected && renaming) state.knowledgeSelected = `${targetScope}:${targetPath}${selected.relative_path.slice(entry.relative_path.length)}`;
        if (creating && mode === 'file') state.knowledgeSelected = `${targetScope}:${targetPath}`;
        state.knowledgeScope = targetScope;
        state.knowledgeDirectory = creating && mode === 'directory' ? targetPath : parentPath(targetPath);
        expanded.add(`${targetScope}:`);
        const parts = state.knowledgeDirectory.split('/').filter(Boolean);
        parts.forEach((_, index) => expanded.add(`${targetScope}:${parts.slice(0, index + 1).join('/')}`));
        state.knowledgeExpanded = [...expanded];
        dialog.close();
      });
      if (!success && ctx.active) submit.disabled = false;
    } });
    input.addEventListener('input', () => input.setCustomValidity(''));
    form.append(h('label', tr(renaming ? '名称' : '相对路径'), input), h('small.knowledge-path-hint', tr('路径相对于所选知识库根目录；同名目标不会被覆盖。')));
    const submit = h('button.btn.btn-primary', { type: 'submit' }, tr('确认'));
    form.appendChild(h('div.knowledge-dialog-actions', h('button.btn.btn-ghost', { type: 'button', onclick: () => dialog.close() }, tr('取消')), submit));
    const dialog = Overlay.modal({ title: tr(title), sub: `${SCOPE_LABEL[entry.scope]} / ${entry.relative_path}`, body: form, size: 'sm' });
  }
  async function removeEntry(entry) {
    if (!await canLeave()) return;
    const confirmed = await Overlay.confirm({ title: tr(entry.kind === 'directory' ? '删除此文件夹？' : '删除此知识文件'),
      description: tr(entry.kind === 'directory' ? '将永久删除此文件夹及其全部内容。' : '文件将被永久删除，相关索引也会失效。'),
      detail: `${SCOPE_LABEL[entry.scope]} / ${entry.relative_path}`, confirmLabel: tr('删除'), tone: 'danger' });
    if (!confirmed || !ctx.active) return;
    await mutate(() => Api.deleteKnowledgeEntry(Store.user, entry.scope, entry.relative_path), tr('知识条目已删除'), () => {
      useKnowledgeClipboard.getState().discard(entry.scope, entry.relative_path);
      if (selected?.scope === entry.scope && (selected.relative_path === entry.relative_path || selected.relative_path.startsWith(entry.relative_path + '/'))) state.knowledgeSelected = '';
      state.knowledgeScope = entry.scope;
      state.knowledgeDirectory = parentPath(entry.relative_path);
    });
  }
  function showContextMenu(event, entry) {
    event.preventDefault(); event.stopPropagation(); closeContextMenu();
    const trigger = event.currentTarget;
    const rect = trigger.getBoundingClientRect();
    focusedEntry = key(entry);
    if (!entry.root && (!listHost.contains(trigger) || !marked.has(key(entry)))) marked = new Set([key(entry)]);
    remember(); drawNavigation();
    const root = LIVE_KNOWLEDGE.roots.find(root => root.scope === entry.scope);
    const separator = root.absolute_path.includes('\\') ? '\\' : '/';
    const absolute = root.absolute_path.replace(/[\\/]$/, '') + (entry.relative_path ? separator + entry.relative_path.split('/').join(separator) : '');
    const menu = h('div.menu.knowledge-context-menu', { role: 'menu', 'aria-label': tr('知识条目操作') });
    const actions = [
      ['重命名', 'penLine', () => pathDialog('重命名', entry, 'rename'), entry.root],
      ['删除', 'trash', () => void removeEntry(entry), entry.root],
      ['复制', 'copy', () => copyEntries(entry, 'copy'), entry.root || pending()],
      ['剪切', 'cut', () => copyEntries(entry, 'move'), entry.root || pending()],
      ['粘贴', 'paste', () => void pasteInto(pasteDirectory(entry)), pending() || !canPasteKnowledge(clipboard(), pasteDirectory(entry))],
      ['复制相对路径', 'link', () => void copyText(joinPath(root.relative_path, entry.relative_path))],
      ['复制绝对路径', 'link', () => void copyText(absolute)],
      ['新建文件', 'fileText', () => pathDialog('新建文件', entry, 'file')],
      ['新建文件夹', 'folder', () => pathDialog('新建文件夹', entry, 'directory')],
    ];
    actions.forEach(([label, symbol, action, disabled]) => menu.appendChild(h('button.menu-item', { role: 'menuitem', disabled: Boolean(disabled), onclick: () => { closeContextMenu(); action(); } },
      h('span', { html: icon(symbol, 'icon-sm') }), tr(label))));
    document.body.appendChild(menu);
    const x = event.type === 'contextmenu' ? event.clientX : rect.left;
    const y = event.type === 'contextmenu' ? event.clientY : rect.bottom;
    menu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - menu.offsetWidth - 8))}px`;
    menu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - menu.offsetHeight - 8))}px`;
    const cleanup = onClickOutside(menu, closeContextMenu);
    closeMenu = () => { cleanup(); menu.remove(); };
    menu.addEventListener('keydown', keyEvent => {
      handleClipboardKey(keyEvent, entry);
      if (keyEvent.key === 'Escape') {
        keyEvent.stopPropagation(); closeContextMenu();
        treeHost.querySelector(`[data-entry="${CSS.escape(key(entry))}"]`)?.focus();
      }
      if (['ArrowDown', 'ArrowUp'].includes(keyEvent.key)) {
        keyEvent.preventDefault();
        const items = [...menu.querySelectorAll('button:not(:disabled)')];
        const index = items.indexOf(document.activeElement);
        items[(index + (keyEvent.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length]?.focus();
      }
    });
    menu.querySelector('button:not(:disabled)')?.focus();
  }

  const search = makeSearchBox({ placeholder: tr('按名称或路径搜索…'), value: query, onInput: value => { query = value; remember(); drawTree(); drawList(); } });
  const searchInput = search.querySelector('input');
  searchInput.value = query;
  inner.append(pageHead({ title: tr('知识库'), actions: [
    search,
    h('button.btn.btn-outline', { onclick: async () => { if (await canLeave()) void refreshPage(); } }, h('span', { html: icon('refresh', 'icon-sm') }), tr('重新读取')),
    h('button.btn.btn-outline', { onclick: async () => {
      if (!await canLeave()) return;
      const directory = currentDirectory() || roots[0];
      chooseFile('.md,.txt,.json', 80, async file => {
        const path = joinPath(directory.relative_path, file.name);
        if (entries.some(entry => entry.scope === directory.scope && entry.relative_path === path) && !await Overlay.confirm({ title: tr('覆盖已有文件？'), detail: path, confirmLabel: tr('覆盖') })) return;
        void mutate(async () => Api.putKnowledgeDocument(Store.user, directory.scope, path, await file.text()), tr('知识文件已导入'));
      });
    } }, h('span', { html: icon('upload', 'icon-sm') }), tr('导入文件')),
    h('button.btn.btn-primary', { onclick: event => showContextMenu(event, currentDirectory() || roots[0]) }, h('span', { html: icon('plus', 'icon-sm') }), tr('新建')),
  ] }),
  clipboardHost,
  h('div.knowledge-workspace', h('aside.knowledge-tree-panel', h('div.knowledge-panel-head', h('strong', tr('知识目录'))), treeHost), listHost, editorHost));
  ctx.registerCleanup(useKnowledgeClipboard.subscribe(() => { if (ctx.active) drawNavigation(); }));
  draw();
  if (selected) void loadDocument(selected);
  return page;
}

export function openKnowledgeZoom(doc, content) {
  Overlay.modal({ title: doc.name || doc.title, sub: doc.relative_path, size: 'knowledge', body: renderMarkdownWithWidgets(content),
    foot: [h('button.btn.btn-ghost', { onclick: () => Overlay.stack.at(-1).close() }, tr('关闭'))] });
}
