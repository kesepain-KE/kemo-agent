import { t as tr, useLanguage } from '../i18n'
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent, type MouseEvent } from 'react'
import { createPortal } from 'react-dom'
import {
  AlertTriangle, BookOpen, Check, ChevronDown, ChevronRight, ClipboardPaste, Copy, FileText,
  Folder, FolderOpen, Layers3, LoaderCircle, Pencil, Quote, Scissors, Search, Share2, Trash2, UserRound, X,
} from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { deleteKnowledgeEntry, getKnowledgeDocument, transferKnowledgeEntry } from '../api/client'
import type { KnowledgeDirectory, KnowledgeDocumentSummary, KnowledgeRoot } from '../types/api'
import { DocumentMarkdown } from './DocumentMarkdown'
import { GlobalConfirmDialog } from './GlobalConfirmDialog'
import { formatBytes, formatDateTime } from './ModuleUi'
import {
  canPasteKnowledge, isCutEntry, isWithin, pasteKnowledgeClipboard, useKnowledgeClipboard,
  type ClipboardAction, type KnowledgeEntryRef, type KnowledgeScope,
} from '../store/knowledgeClipboard'
import styles from './KnowledgeReferenceDrawer.module.css'

interface KnowledgeEntry extends KnowledgeEntryRef {
  root?: boolean
  absolute_path?: string
  document?: KnowledgeDocumentSummary
}

interface Props {
  open: boolean
  user: string
  roots: KnowledgeRoot[]
  directories: KnowledgeDirectory[]
  documents: KnowledgeDocumentSummary[]
  loading?: boolean
  error?: boolean
  onClose: () => void
  onReference: (document: KnowledgeDocumentSummary) => void
  onReferenceDirectory: (scope: KnowledgeScope, relativePath: string, name: string) => void
}

const scopes: KnowledgeScope[] = ['user', 'shared', 'global']
const scopeLabels: Record<KnowledgeScope, string> = { user: '用户', shared: '共享', global: '全局' }
const scopeIcons = { user: UserRound, shared: Share2, global: Layers3 }

function isScope(value: string): value is KnowledgeScope {
  return scopes.includes(value as KnowledgeScope)
}

function entryKey(entry: Pick<KnowledgeEntry, 'scope' | 'relative_path'>) {
  return `${entry.scope}:${entry.relative_path}`
}

function parentPath(path: string) {
  return path.split('/').slice(0, -1).join('/')
}

function joinPath(parent: string, name: string) {
  return [parent, name].filter(Boolean).join('/')
}

function replacePathPrefix(path: string, from: string, to: string) {
  return path === from ? to : `${to}${path.slice(from.length)}`
}

function entryName(path: string) {
  return path.split('/').at(-1) || path
}

export function KnowledgeReferenceDrawer({
  open, user, roots, directories, documents, loading = false, error = false,
  onClose, onReference, onReferenceDirectory,
}: Props) {
  const language = useLanguage()
  const queryClient = useQueryClient()
  const [query, setQuery] = useState('')
  const [expanded, setExpanded] = useState(() => new Set<string>())
  const [selectedKey, setSelectedKey] = useState('')
  const [context, setContext] = useState<{ key: string; x: number; y: number } | null>(null)
  const [menuPosition, setMenuPosition] = useState({ left: 8, top: 8 })
  const clipboardState = useKnowledgeClipboard()
  const clipboard = clipboardState.clipboard?.user === user ? clipboardState.clipboard : null
  const [operationError, setOperationError] = useState('')
  const [operationStatus, setOperationStatus] = useState('')
  const [operationPending, setPending] = useState(false)
  const pending = operationPending || clipboardState.pending
  const [dialog, setDialog] = useState<'rename' | 'delete' | ''>('')
  const [dialogEntryKey, setDialogEntryKey] = useState('')
  const [renameValue, setRenameValue] = useState('')
  const [dialogError, setDialogError] = useState('')
  const [preview, setPreview] = useState<{
    key: string
    title: string
    path: string
    content?: string
    loading: boolean
    error?: string
  } | null>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const treeRef = useRef<HTMLElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const previewKeyRef = useRef('')
  const normalizedQuery = query.trim().toLocaleLowerCase()

  const entries = useMemo<KnowledgeEntry[]>(() => [
    ...roots.filter((root) => isScope(root.scope)).map((root) => ({
      scope: root.scope as KnowledgeScope,
      relative_path: '',
      name: tr(scopeLabels[root.scope as KnowledgeScope]),
      kind: 'directory' as const,
      root: true,
      absolute_path: root.absolute_path,
    })),
    ...directories.filter((directory) => isScope(directory.scope)).map((directory) => ({
      scope: directory.scope as KnowledgeScope,
      relative_path: directory.relative_path,
      name: directory.name,
      kind: 'directory' as const,
    })),
    ...documents.filter((document) => isScope(document.scope)).map((document) => ({
      scope: document.scope as KnowledgeScope,
      relative_path: document.relative_path,
      name: entryName(document.relative_path),
      kind: 'file' as const,
      document,
    })),
  ], [directories, documents, language, roots])

  const entriesByKey = useMemo(() => new Map(entries.map((entry) => [entryKey(entry), entry])), [entries])
  const menuEntry = context ? entriesByKey.get(context.key) : undefined
  const dialogEntry = entriesByKey.get(dialogEntryKey)
  const matchedDocuments = normalizedQuery
    ? documents.filter((document) => isScope(document.scope)
      && `${document.title} ${document.relative_path}`.toLocaleLowerCase().includes(normalizedQuery)).length
    : documents.length
  const countLabel = normalizedQuery
    ? tr('匹配 {matched} / 共 {count} 个知识文件', { matched: matchedDocuments, count: documents.length })
    : tr('{count} 个可见知识文件', { count: documents.length })

  const childrenOf = (parent: KnowledgeEntry) => entries
    .filter((entry) => !entry.root && entry.scope === parent.scope && parentPath(entry.relative_path) === parent.relative_path)
    .sort((left, right) => (left.kind === right.kind ? 0 : left.kind === 'directory' ? -1 : 1)
      || left.name.localeCompare(right.name, language))

  const matchesEntry = (entry: KnowledgeEntry) => (
    `${entry.name} ${entry.relative_path} ${entry.document?.title || ''}`.toLocaleLowerCase().includes(normalizedQuery)
  )

  const branchMatches = (entry: KnowledgeEntry) => !normalizedQuery || matchesEntry(entry)
    || entries.some((candidate) => candidate.scope === entry.scope
      && isWithin(candidate.relative_path, entry.relative_path)
      && matchesEntry(candidate))

  const pasteDirectory = (entry: KnowledgeEntry) => entry.kind === 'directory' ? entry
    : entriesByKey.get(`${entry.scope}:${parentPath(entry.relative_path)}`)
  const canPasteInto = (target: KnowledgeEntry) => canPasteKnowledge(clipboard, target)

  useEffect(() => {
    useKnowledgeClipboard.getState().activateUser(user)
    setSelectedKey('')
    setContext(null)
    previewKeyRef.current = ''
    setPreview(null)
  }, [user])

  useEffect(() => {
    if (open) requestAnimationFrame(() => searchRef.current?.focus())
    else {
      setQuery('')
      setContext(null)
    }
  }, [open])

  useEffect(() => {
    if (!context) return
    const outside = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setContext(null)
    }
    const keydown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopImmediatePropagation()
        setContext(null)
      }
    }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', keydown, true)
    return () => {
      document.removeEventListener('pointerdown', outside)
      document.removeEventListener('keydown', keydown, true)
    }
  }, [context])

  useLayoutEffect(() => {
    if (!context || !menuRef.current) return
    const menu = menuRef.current
    setMenuPosition({
      left: Math.max(8, Math.min(context.x, window.innerWidth - menu.offsetWidth - 8)),
      top: Math.max(8, Math.min(context.y, window.innerHeight - menu.offsetHeight - 8)),
    })
    menu.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus({ preventScroll: true })
  }, [context, clipboard, pending])

  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      const target = event.target
      if (!(target instanceof HTMLElement) || target.closest('input, textarea, select, [contenteditable="true"]')) return
      if (!treeRef.current?.contains(target) && !menuRef.current?.contains(target)) return
      if (!(event.ctrlKey || event.metaKey)) return
      if (pending) return
      const selected = entriesByKey.get(target.closest<HTMLElement>('[data-entry-key]')?.dataset.entryKey || selectedKey)
      if (event.key.toLowerCase() === 'c' && selected && !selected.root) {
        event.preventDefault()
        setClipboardFor(selected, 'copy')
      } else if (event.key.toLowerCase() === 'x' && selected && !selected.root) {
        event.preventDefault()
        setClipboardFor(selected, 'move')
      } else if (event.key.toLowerCase() === 'v' && selected && pasteDirectory(selected)) {
        event.preventDefault()
        void pasteInto(pasteDirectory(selected)!)
      }
    }
    document.addEventListener('keydown', onKeyDown, true)
    return () => document.removeEventListener('keydown', onKeyDown, true)
  })

  function showContextMenu(event: MouseEvent<HTMLElement>, entry: KnowledgeEntry) {
    event.preventDefault()
    event.stopPropagation()
    setSelectedKey(entryKey(entry))
    setContext({ key: entryKey(entry), x: event.clientX, y: event.clientY })
  }

  function openEntry(entry: KnowledgeEntry) {
    setSelectedKey(entryKey(entry))
    if (entry.kind === 'directory') {
      setExpanded((current) => new Set(current).add(entryKey(entry)))
      return
    }
    const key = entryKey(entry)
    previewKeyRef.current = key
    setPreview({ key, title: entry.name, path: entry.relative_path, loading: true })
    void getKnowledgeDocument(user, entry.scope, entry.relative_path).then((result) => {
      if (previewKeyRef.current === key) setPreview({ key, title: entry.name, path: entry.relative_path, content: result.content, loading: false })
    }).catch((cause: unknown) => {
      if (previewKeyRef.current === key) setPreview({
        key, title: entry.name, path: entry.relative_path, loading: false,
        error: cause instanceof Error ? cause.message : tr('读取失败'),
      })
    })
  }

  function referenceFolder(entry: KnowledgeEntry) {
    onReferenceDirectory(entry.scope, entry.relative_path || '.', entry.name)
    setContext(null)
  }

  function beginRename(entry: KnowledgeEntry) {
    setContext(null)
    setDialogEntryKey(entryKey(entry))
    setRenameValue(entry.name)
    setDialogError('')
    setDialog('rename')
  }

  function beginDelete(entry: KnowledgeEntry) {
    setContext(null)
    setDialogEntryKey(entryKey(entry))
    setDialogError('')
    setDialog('delete')
  }

  function setClipboardFor(entry: KnowledgeEntry, action: ClipboardAction) {
    clipboardState.setClipboard(user, [entry], action)
    setOperationError('')
    setOperationStatus('')
    setContext(null)
  }

  async function pasteInto(target: KnowledgeEntry) {
    if (!canPasteInto(target) || !clipboard || pending) return
    setOperationError('')
    setOperationStatus('')
    setPending(true)
    try {
      const transferred = await pasteKnowledgeClipboard(user, target)
      if (!transferred.length) return
      if (clipboard.action === 'move') {
        setSelectedKey(`${transferred[0].scope}:${transferred[0].path}`)
        setExpanded((current) => {
          const next = new Set(current)
          next.add(entryKey(target))
          const parent = target.relative_path
          let path = ''
          for (const part of parent.split('/').filter(Boolean)) {
            path = joinPath(path, part)
            next.add(`${target.scope}:${path}`)
          }
          return next
        })
      }
      setOperationStatus(tr(clipboard.action === 'copy' ? '知识条目已复制' : '知识条目已移动'))
      setContext(null)
    } catch (cause) {
      setOperationError(cause instanceof Error ? cause.message : tr('操作失败'))
    } finally {
      setPending(false)
      await queryClient.invalidateQueries({ queryKey: ['knowledge', user] })
    }
  }

  async function confirmDialog() {
    if (!dialogEntry || pending) return
    setDialogError('')
    if (dialog === 'rename') {
      const name = renameValue.trim()
      if (!name || /[/\\]/.test(name)) {
        setDialogError(tr('请输入有效名称'))
        return
      }
    }
    setPending(true)
    try {
      if (dialog === 'rename') {
        const destination = joinPath(parentPath(dialogEntry.relative_path), renameValue.trim())
        await transferKnowledgeEntry(user, dialogEntry.scope, dialogEntry.relative_path, 'move', dialogEntry.scope, destination)
        clipboardState.discard(dialogEntry.scope, dialogEntry.relative_path)
        const prefix = dialogEntry.relative_path
        const nextPath = destination
        setSelectedKey((current) => {
          const selected = entriesByKey.get(current)
          return selected?.scope === dialogEntry.scope && isWithin(selected.relative_path, prefix)
            ? `${dialogEntry.scope}:${replacePathPrefix(selected.relative_path, prefix, nextPath)}` : current
        })
        setExpanded((current) => new Set([...current].map((key) => {
          const [scope, ...parts] = key.split(':')
          const path = parts.join(':')
          return scope === dialogEntry.scope && isWithin(path, prefix)
            ? `${scope}:${replacePathPrefix(path, prefix, nextPath)}` : key
        })))
      } else {
        await deleteKnowledgeEntry(user, dialogEntry.scope, dialogEntry.relative_path)
        setSelectedKey((current) => {
          const selected = entriesByKey.get(current)
          return selected?.scope === dialogEntry.scope && isWithin(selected.relative_path, dialogEntry.relative_path) ? '' : current
        })
        clipboardState.discard(dialogEntry.scope, dialogEntry.relative_path)
        if (preview && preview.key.startsWith(`${dialogEntry.scope}:`)
          && isWithin(preview.path, dialogEntry.relative_path)) {
          previewKeyRef.current = ''
          setPreview(null)
        }
      }
      setDialog('')
      setOperationStatus(tr(dialog === 'rename' ? '知识条目已重命名' : '知识条目已删除'))
      await queryClient.invalidateQueries({ queryKey: ['knowledge', user] })
    } catch (cause) {
      setDialogError(cause instanceof Error ? cause.message : tr('操作失败'))
    } finally {
      setPending(false)
    }
  }

  function handleTreeKeyDown(event: KeyboardEvent<HTMLElement>, entry: KnowledgeEntry, visibleKeys: string[]) {
    if (event.target !== event.currentTarget) return
    event.stopPropagation()
    const index = visibleKeys.indexOf(entryKey(entry))
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp' || event.key === 'Home' || event.key === 'End') {
      event.preventDefault()
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? visibleKeys.length - 1
        : Math.max(0, Math.min(visibleKeys.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1)))
      treeRef.current?.querySelector<HTMLElement>(`[data-entry-key="${CSS.escape(visibleKeys[next])}"]`)?.focus()
    } else if (event.key === 'ArrowRight' && entry.kind === 'directory') {
      event.preventDefault()
      setExpanded((current) => new Set(current).add(entryKey(entry)))
    } else if (event.key === 'ArrowLeft' && entry.kind === 'directory') {
      event.preventDefault()
      setExpanded((current) => {
        const next = new Set(current)
        next.delete(entryKey(entry))
        return next
      })
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      setSelectedKey(entryKey(entry))
      if (entry.kind === 'directory') setExpanded((current) => {
        const next = new Set(current)
        if (next.has(entryKey(entry))) next.delete(entryKey(entry))
        else next.add(entryKey(entry))
        return next
      })
      else openEntry(entry)
    } else if (event.key === 'ContextMenu' || event.shiftKey && event.key === 'F10') {
      event.preventDefault()
      const rect = event.currentTarget.getBoundingClientRect()
      setSelectedKey(entryKey(entry))
      setContext({ key: entryKey(entry), x: rect.left, y: rect.bottom })
    }
  }

  const visibleKeys: string[] = []
  function renderBranch(entry: KnowledgeEntry, depth: number) {
    if (!entry.root && !branchMatches(entry)) return null
    const children = entry.kind === 'directory' ? childrenOf(entry) : []
    const autoExpanded = Boolean(normalizedQuery && children.some((child) => branchMatches(child)))
    const isExpanded = entry.kind === 'directory' && (expanded.has(entryKey(entry)) || autoExpanded)
    visibleKeys.push(entryKey(entry))
    const Icon = entry.root ? scopeIcons[entry.scope] : entry.kind === 'directory'
      ? isExpanded ? FolderOpen : Folder : FileText
    const isCut = isCutEntry(clipboard, entry)
    const selected = selectedKey === entryKey(entry)
    return <div
      className={`${styles.treeNode} ${selected ? styles.selected : ''} ${isCut ? styles.cut : ''}`}
      key={entryKey(entry)}
      role="treeitem"
      tabIndex={0}
      data-entry-key={entryKey(entry)}
      aria-level={depth + 1}
      aria-expanded={entry.kind === 'directory' ? isExpanded : undefined}
      aria-selected={selected}
      aria-label={entry.name}
      data-scope={entry.scope}
      style={{ '--tree-depth': depth } as CSSProperties}
      title={entry.root ? entry.absolute_path : `${scopeLabels[entry.scope]} / ${entry.relative_path}`}
      onContextMenu={(event) => showContextMenu(event, entry)}
      onClick={(event) => {
        event.stopPropagation()
        setSelectedKey(entryKey(entry))
        if (entry.kind === 'directory') setExpanded((current) => {
          const next = new Set(current)
          if (next.has(entryKey(entry))) next.delete(entryKey(entry))
          else next.add(entryKey(entry))
          return next
        })
      }}
      onDoubleClick={() => { if (entry.kind === 'file') openEntry(entry) }}
      onKeyDown={(event) => handleTreeKeyDown(event, entry, visibleKeys)}
    >
      <div className={styles.treeRow}>
        <span className={styles.disclosure} aria-hidden="true">
          {entry.kind === 'directory' ? isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} /> : null}
        </span>
        <Icon size={16} className={styles.entryIcon} />
        <span className={styles.entryName}>{entry.name}</span>
        {entry.document && <small className={styles.entryMeta}>{formatBytes(entry.document.size)} · {formatDateTime(entry.document.updated_at)}</small>}
        {entry.kind === 'file' && entry.document && <button
          type="button"
          className={styles.referenceButton}
          aria-label={tr('引用 {title}', { title: entry.document.title })}
          title={tr('引用此文件')}
          onClick={(event) => { event.stopPropagation(); onReference(entry.document!) }}
        ><Quote size={14} /></button>}
      </div>
      {isExpanded && children.length > 0 && <div role="group" className={styles.treeGroup}>
        {children.map((child) => renderBranch(child, depth + 1))}
      </div>}
    </div>
  }

  const renderContextMenu = () => {
    if (!context || !menuEntry) return null
    const directory = menuEntry.kind === 'directory'
    const root = Boolean(menuEntry.root)
    const destination = pasteDirectory(menuEntry)
    const clipboardMatchesTarget = destination && canPasteInto(destination)
    return createPortal(<div
      ref={menuRef}
      className={styles.menu}
      style={menuPosition}
      role="menu"
      aria-label={tr('知识条目操作')}
      onKeyDown={(event) => {
        const items = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')]
        const index = items.indexOf(document.activeElement as HTMLButtonElement)
        const next = event.key === 'ArrowDown' ? (index + 1) % items.length
          : event.key === 'ArrowUp' ? (index - 1 + items.length) % items.length
            : event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : -1
        if (next >= 0) { event.preventDefault(); items[next]?.focus() }
      }}
    >
      <button role="menuitem" onClick={() => directory
        ? referenceFolder(menuEntry)
        : menuEntry.document && (onReference(menuEntry.document), setContext(null))}>
        <Quote size={15} />{tr(directory ? '引用文件夹相对路径' : '引用此文件')}
      </button>
      <button role="menuitem" disabled={root || pending} onClick={() => beginRename(menuEntry)}><Pencil size={15} />{tr('重命名')}</button>
      <button role="menuitem" disabled={root || pending} onClick={() => beginDelete(menuEntry)}><Trash2 size={15} />{tr('删除')}</button>
      <button role="menuitem" disabled={pending} onClick={() => { setContext(null); openEntry(menuEntry) }}>
        {directory ? <FolderOpen size={15} /> : <FileText size={15} />}{tr('打开')}
      </button>
      <div role="separator" />
      <button role="menuitem" disabled={root || pending} onClick={() => setClipboardFor(menuEntry, 'move')}><Scissors size={15} />{tr('剪切')}</button>
      <button role="menuitem" disabled={root || pending} onClick={() => setClipboardFor(menuEntry, 'copy')}><Copy size={15} />{tr('复制')}</button>
      <button role="menuitem" disabled={!clipboardMatchesTarget || pending} onClick={() => destination && void pasteInto(destination)}>
        <ClipboardPaste size={15} />{tr('粘贴')}
      </button>
    </div>, document.body)
  }

  return createPortal(<>
    <aside className={`drawer ${styles.drawer} ${open ? 'show' : ''}`} inert={!open} role="dialog" aria-modal="true" aria-label={tr('知识库引用')} aria-hidden={!open}>
      <div className="drawer-head">
        <div className={styles.headerLead}>
          <span className={styles.headerIcon}><BookOpen size={18} /></span>
          <span className="context-drawer-heading"><strong>{tr('引用知识库')}</strong><span aria-live="polite" title={countLabel}>{countLabel}</span></span>
        </div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label={tr('关闭知识库引用')}><X size={17} /></button>
      </div>

      <div className={`drawer-body ${styles.body}`}>
        <label className={styles.searchBox}>
          <Search size={17} />
          <input
            ref={searchRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={tr('搜索知识库名称或路径…')}
            aria-label={tr('搜索知识库')}
          />
          {query && <button type="button" onClick={() => setQuery('')} aria-label={tr('清空知识库搜索')}><X size={15} /></button>}
        </label>
        {clipboard && <div className={styles.clipboardStatus} role="status">
          <span><Check size={14} />{tr(clipboard.action === 'copy' ? '待粘贴副本：{name}' : '待移动：{name}', {
            name: clipboard.entries.length === 1 ? clipboard.entries[0].name : tr('{count} 个条目', { count: clipboard.entries.length }),
          })}</span>
          <button type="button" onClick={() => { clipboardState.clear(); setOperationStatus('') }} aria-label={tr('取消知识条目操作')}><X size={14} /></button>
        </div>}
        {operationError && <div className={styles.error} role="alert">{operationError}</div>}
        {!operationError && operationStatus && <div className={styles.status} role="status">{operationStatus}</div>}
        <nav className={styles.results} ref={treeRef} role="tree" aria-label={tr('知识目录')} onContextMenu={(event) => {
          const selected = entriesByKey.get(selectedKey)
          const destination = selected && pasteDirectory(selected)
          if (destination) showContextMenu(event, destination)
        }}>
          {loading && <div className={styles.empty}><LoaderCircle className={styles.spinning} size={22} /><strong>{tr('正在加载知识库')}</strong></div>}
          {error && !loading && <div className={styles.empty}><BookOpen size={22} /><strong>{tr('知识库加载失败')}</strong><span>{tr('关闭后重新打开即可再次加载。')}</span></div>}
          {!loading && !error && entries.filter((entry) => entry.root).map((root) => renderBranch(root, 0))}
          {!loading && !error && normalizedQuery && !entries.some((entry) => !entry.root && matchesEntry(entry)) && <div className={styles.empty}>
            <FileText size={22} /><strong>{tr('没有匹配的知识文件')}</strong><span>{tr('请调整搜索词。')}</span>
          </div>}
          {!loading && !error && documents.length === 0 && !normalizedQuery && <div className={styles.empty}>
            <FileText size={22} /><strong>{tr('暂无知识文件')}</strong><span>{tr('可前往知识库页面导入知识文件。')}</span>
          </div>}
        </nav>
      </div>
    </aside>
    {open && <button type="button" className="drawer-backdrop" aria-label={tr('关闭知识库引用')} onClick={onClose} />}
    {renderContextMenu()}
    <GlobalConfirmDialog
      open={Boolean(dialog)}
      title={dialog === 'rename' ? tr('重命名') : tr(dialogEntry?.kind === 'directory' ? '删除此文件夹？' : '删除此知识文件')}
      detail={dialogEntry ? `${tr(scopeLabels[dialogEntry.scope])} / ${dialogEntry.relative_path}` : undefined}
      description={dialog === 'rename'
        ? <label className={styles.rename}><span>{tr('名称')}</span><input aria-label={tr('名称')} value={renameValue}
          disabled={pending} onChange={(event) => setRenameValue(event.target.value)}
          onKeyDown={(event) => { if (event.key === 'Enter' && renameValue.trim()) { event.preventDefault(); void confirmDialog() } }} /></label>
        : tr(dialogEntry?.kind === 'directory' ? '将永久删除此文件夹及其全部内容。' : '文件将被永久删除，相关索引也会失效。')}
      icon={dialog === 'rename' ? <Pencil size={21} /> : <AlertTriangle size={21} />}
      tone={dialog === 'delete' ? 'danger' : 'warning'}
      error={dialogError}
      confirmLabel={tr(dialog === 'rename' ? '保存名称' : '删除')}
      pending={pending}
      pendingLabel={tr('正在处理…')}
      confirmDisabled={dialog === 'rename' && !renameValue.trim()}
      onCancel={() => { if (!pending) setDialog('') }}
      onConfirm={() => void confirmDialog()}
    />
    {preview && createPortal(<div className={styles.previewOverlay} onMouseDown={(event) => {
      if (event.target === event.currentTarget) { previewKeyRef.current = ''; setPreview(null) }
    }}>
      <section className={styles.previewDialog} role="dialog" aria-modal="true" aria-label={preview.title}>
        <header className={styles.previewHeader}>
          <div><strong>{preview.title}</strong><small>{preview.path}</small></div>
          <button type="button" className="icon-btn" aria-label={tr('关闭预览')} onClick={() => { previewKeyRef.current = ''; setPreview(null) }}><X size={17} /></button>
        </header>
        <div className={styles.previewContent}>
          {preview.loading ? <div className={styles.empty}><LoaderCircle className={styles.spinning} size={22} /><strong>{tr('正在加载知识文件')}</strong></div>
            : preview.error ? <div className={styles.empty}><FileText size={22} /><strong>{preview.error}</strong></div>
              : <DocumentMarkdown content={preview.content || ''} />}
        </div>
      </section>
    </div>, document.body)}
  </>, document.body)
}
