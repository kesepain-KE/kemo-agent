import { t as tr, useLanguage } from '../i18n'
import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { BookOpen, FileText, Layers3, LoaderCircle, Quote, Search, Share2, UserRound, X } from 'lucide-react'
import type { KnowledgeDocumentSummary, KnowledgeResponse } from '../types/api'
import { getKnowledgeCategoryFilters, knowledgeCategoryKey } from '../knowledgeCategories'
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
  categories?: KnowledgeResponse['categories']
  loading?: boolean
  error?: boolean
  onClose: () => void
  onReference: (document: KnowledgeDocumentSummary) => void
}

export function KnowledgeReferenceDrawer({
  open,
  documents,
  categories,
  loading = false,
  error = false,
  onClose,
  onReference,
}: KnowledgeReferenceDrawerProps) {
  useLanguage()
  const [scope, setScope] = useState<KnowledgeScope>('all')
  const [categoryFilter, setCategoryFilter] = useState('all')
  const [query, setQuery] = useState('')
  const searchRef = useRef<HTMLInputElement>(null)
  const resultsRef = useRef<HTMLDivElement>(null)
  const categoryFilters = useMemo(
    () => getKnowledgeCategoryFilters({ documents, categories }, scope),
    [documents, categories, scope],
  )
  const activeCategory = categoryFilters.some((category) => category.value === categoryFilter) ? categoryFilter : 'all'
  const normalizedQuery = query.trim().toLocaleLowerCase()
  const filteredDocuments = useMemo(() => documents.filter((document) => (
    (scope === 'all' || document.scope === scope)
    && (activeCategory === 'all' || knowledgeCategoryKey(document.scope, document.category) === activeCategory)
    && (!normalizedQuery || `${document.title} ${document.relative_path}`.toLocaleLowerCase().includes(normalizedQuery))
  )), [documents, normalizedQuery, scope, activeCategory])
  const filtering = scope !== 'all' || activeCategory !== 'all' || Boolean(normalizedQuery)
  const countLabel = filtering && !loading && !error
    ? tr('匹配 {matched} / 共 {count} 个知识文件', { matched: filteredDocuments.length, count: documents.length })
    : tr('{count} 个可见知识文件', { count: documents.length })

  useEffect(() => {
    if (!loading && activeCategory !== categoryFilter) setCategoryFilter('all')
  }, [activeCategory, categoryFilter, loading])

  useEffect(() => {
    if (resultsRef.current) resultsRef.current.scrollTop = 0
  }, [open, scope, activeCategory, normalizedQuery])

  useEffect(() => {
    if (open) requestAnimationFrame(() => searchRef.current?.focus())
    else {
      setQuery('')
      setScope('all')
      setCategoryFilter('all')
    }
  }, [open])

  return createPortal(<>
    <aside className={`drawer ${styles.drawer} ${open ? 'show' : ''}`} inert={!open} role="dialog" aria-modal="true" aria-label={tr("知识库引用")} aria-hidden={!open}>
      <div className="drawer-head">
        <div className={styles.headerLead}>
          <span className={styles.headerIcon}><BookOpen size={18} /></span>
          <span className="context-drawer-heading"><strong>引用知识库</strong><span aria-live="polite" title={countLabel}>{countLabel}</span></span>
        </div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label={tr("关闭知识库引用")}><X size={17} /></button>
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

        <nav className={styles.scopeTabs} aria-label={tr("知识库层级")}>
          {(['all', 'user', 'shared', 'global'] as const).map((value) => <button
            key={value}
            type="button"
            className={scope === value ? styles.activeTab : ''}
            aria-pressed={scope === value}
            onClick={() => {
              setScope(value)
              setCategoryFilter('all')
            }}
          >{value === 'all' ? tr('全部') : tr(scopeLabels[value])}</button>)}
        </nav>

        <label className={styles.categoryRow}>
          <span>{tr("分类")}</span>
          <select
            className={`select ${styles.categorySelect}`}
            aria-label={tr("知识库分类")}
            value={activeCategory}
            disabled={loading || error || documents.length === 0}
            onChange={(event) => setCategoryFilter(event.target.value)}
          >
            <option value="all">{tr("全部分类")}</option>
            {categoryFilters.map((category) => <option key={category.value} value={category.value}>
              {category.label}（{category.count}）
            </option>)}
          </select>
        </label>

        <div className={styles.results} ref={resultsRef}>
          {loading && <div className={styles.empty}><LoaderCircle className={styles.spinning} size={22} /><strong>{tr("正在加载知识库")}</strong></div>}
          {error && !loading && <div className={styles.empty}><BookOpen size={22} /><strong>{tr("知识库加载失败")}</strong><span>{tr("关闭后重新打开即可再次加载。")}</span></div>}
          {!loading && !error && filteredDocuments.map((document) => {
            const normalizedScope = document.scope === 'user' || document.scope === 'shared' || document.scope === 'global' ? document.scope : 'user'
            const ScopeIcon = scopeIcons[normalizedScope]
            return <article className={styles.knowledgeCard} key={`${document.scope}:${document.relative_path}`}>
              <span className={`${styles.cardIcon} ${styles[normalizedScope]}`}><ScopeIcon size={18} /></span>
              <span className={styles.cardCopy}>
                <strong title={document.title}>{document.title}</strong>
                <span title={document.relative_path}>{document.relative_path}</span>
                <span className={styles.categoryBadge} title={document.category || tr('未分类')}>{document.category || tr('未分类')}</span>
                <small>{formatBytes(document.size)} · {formatDateTime(document.updated_at)}</small>
              </span>
              <span className={`${styles.scopeBadge} ${styles[normalizedScope]}`}>{tr(scopeLabels[normalizedScope])}</span>
              <button type="button" className={styles.referenceButton} onClick={() => onReference(document)} aria-label={tr('引用 {title}', { title: document.title })}>
                <Quote size={14} /><span>{tr("引用")}</span>
              </button>
            </article>
          })}
          {!loading && !error && filteredDocuments.length === 0 && <div className={styles.empty}>
            <FileText size={22} />
            <strong>{documents.length === 0 ? tr('暂无知识文件') : tr('没有匹配的知识文件')}</strong>
            <span>{documents.length === 0 ? tr('可前往知识库页面导入知识文件。') : tr('请调整搜索词、分类或知识层级。')}</span>
          </div>}
        </div>
      </div>
    </aside>
    {open && <button type="button" className="drawer-backdrop" aria-label={tr("关闭知识库引用")} onClick={onClose} />}
  </>, document.body)
}
