import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { BookOpen, FileText, Layers3, LoaderCircle, Quote, Search, Share2, UserRound, X } from 'lucide-react'
import type { KnowledgeDocumentSummary } from '../types/api'
import { formatBytes, formatDateTime } from './ModuleUi'
import styles from './KnowledgeReferenceDrawer.module.css'

type KnowledgeScope = 'all' | 'user' | 'shared' | 'global'

const scopeLabels: Record<Exclude<KnowledgeScope, 'all'>, string> = {
  user: '用户',
  shared: '共享',
  global: '全局',
}

const scopeIcons = {
  user: UserRound,
  shared: Share2,
  global: Layers3,
}

export interface KnowledgeReferenceDrawerProps {
  open: boolean
  documents: KnowledgeDocumentSummary[]
  loading?: boolean
  error?: boolean
  onClose: () => void
  onReference: (document: KnowledgeDocumentSummary) => void
}

export function KnowledgeReferenceDrawer({
  open,
  documents,
  loading = false,
  error = false,
  onClose,
  onReference,
}: KnowledgeReferenceDrawerProps) {
  const [scope, setScope] = useState<KnowledgeScope>('all')
  const [query, setQuery] = useState('')
  const searchRef = useRef<HTMLInputElement>(null)
  const normalizedQuery = query.trim().toLocaleLowerCase()
  const filteredDocuments = useMemo(() => documents.filter((document) => (
    (scope === 'all' || document.scope === scope)
    && (!normalizedQuery || `${document.title} ${document.relative_path}`.toLocaleLowerCase().includes(normalizedQuery))
  )), [documents, normalizedQuery, scope])

  useEffect(() => {
    if (open) requestAnimationFrame(() => searchRef.current?.focus())
    else {
      setQuery('')
      setScope('all')
    }
  }, [open])

  return createPortal(<>
    <aside className={`drawer ${styles.drawer} ${open ? 'show' : ''}`} inert={!open} role="dialog" aria-modal="true" aria-label="知识库引用" aria-hidden={!open}>
      <div className="drawer-head">
        <div className={styles.headerLead}>
          <span className={styles.headerIcon}><BookOpen size={18} /></span>
          <span className="context-drawer-heading"><strong>引用知识库</strong><span>{documents.length} 个可见知识文件</span></span>
        </div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="关闭知识库引用"><X size={17} /></button>
      </div>

      <div className={`drawer-body ${styles.body}`}>
        <label className={styles.searchBox}>
          <Search size={17} />
          <input
            ref={searchRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索知识库名称或路径…"
            aria-label="搜索知识库"
          />
          {query && <button type="button" onClick={() => setQuery('')} aria-label="清空知识库搜索"><X size={15} /></button>}
        </label>

        <nav className={styles.scopeTabs} aria-label="知识库层级">
          {(['all', 'user', 'shared', 'global'] as const).map((value) => <button
            key={value}
            type="button"
            className={scope === value ? styles.activeTab : ''}
            aria-pressed={scope === value}
            onClick={() => setScope(value)}
          >{value === 'all' ? '全部' : scopeLabels[value]}</button>)}
        </nav>

        <div className={styles.results}>
          {loading && <div className={styles.empty}><LoaderCircle className={styles.spinning} size={22} /><strong>正在加载知识库</strong></div>}
          {error && !loading && <div className={styles.empty}><BookOpen size={22} /><strong>知识库加载失败</strong><span>关闭后重新打开即可再次加载。</span></div>}
          {!loading && !error && filteredDocuments.map((document) => {
            const normalizedScope = document.scope === 'user' || document.scope === 'shared' || document.scope === 'global' ? document.scope : 'user'
            const ScopeIcon = scopeIcons[normalizedScope]
            return <article className={styles.knowledgeCard} key={`${document.scope}:${document.relative_path}`}>
              <span className={`${styles.cardIcon} ${styles[normalizedScope]}`}><ScopeIcon size={18} /></span>
              <span className={styles.cardCopy}>
                <strong>{document.title}</strong>
                <span>{document.relative_path}</span>
                <small>{formatBytes(document.size)} · {formatDateTime(document.updated_at)}</small>
              </span>
              <span className={`${styles.scopeBadge} ${styles[normalizedScope]}`}>{scopeLabels[normalizedScope]}</span>
              <button type="button" className={styles.referenceButton} onClick={() => onReference(document)} aria-label={`引用 ${document.title}`}>
                <Quote size={14} /><span>引用</span>
              </button>
            </article>
          })}
          {!loading && !error && filteredDocuments.length === 0 && <div className={styles.empty}>
            <FileText size={22} />
            <strong>{documents.length === 0 ? '暂无知识文件' : '没有匹配的知识文件'}</strong>
            <span>{documents.length === 0 ? '可前往知识库页面导入知识文件。' : '请调整搜索词或切换知识层级。'}</span>
          </div>}
        </div>
      </div>
    </aside>
    {open && <button type="button" className="drawer-backdrop" aria-label="关闭知识库引用" onClick={onClose} />}
  </>, document.body)
}
