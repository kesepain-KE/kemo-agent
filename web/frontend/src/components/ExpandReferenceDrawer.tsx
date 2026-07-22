import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Boxes, Layers3, LoaderCircle, Quote, Search, Share2, UserRound, X } from 'lucide-react'
import type { ExpandModuleSummary, ExpandScope } from '../types/api'
import styles from './ExpandReferenceDrawer.module.css'

type ExpandFilter = 'all' | ExpandScope

const scopeLabels: Record<ExpandScope, string> = {
  user: '用户',
  shared: '共享',
  global: '全局',
}

const scopeIcons = {
  user: UserRound,
  shared: Share2,
  global: Layers3,
}

function moduleStatus(module: ExpandModuleSummary) {
  if (!module.valid) return '配置异常'
  if (!module.active_for_main_agent || !module.whitelisted) return '未启用'
  return '已启用'
}

export interface ExpandReferenceDrawerProps {
  open: boolean
  modules: ExpandModuleSummary[]
  loading?: boolean
  error?: boolean
  onClose: () => void
  onReference: (module: ExpandModuleSummary) => void
}

export function ExpandReferenceDrawer({
  open,
  modules,
  loading = false,
  error = false,
  onClose,
  onReference,
}: ExpandReferenceDrawerProps) {
  const [scope, setScope] = useState<ExpandFilter>('all')
  const [query, setQuery] = useState('')
  const searchRef = useRef<HTMLInputElement>(null)
  const normalizedQuery = query.trim().toLocaleLowerCase()
  const filteredModules = useMemo(() => modules.filter((module) => (
    (scope === 'all' || module.scope === scope)
    && (!normalizedQuery || `${module.display_name} ${module.name} ${module.description} ${module.path} ${module.relative_path}`
      .toLocaleLowerCase()
      .includes(normalizedQuery))
  )), [modules, normalizedQuery, scope])

  useEffect(() => {
    if (open) requestAnimationFrame(() => searchRef.current?.focus())
    else {
      setQuery('')
      setScope('all')
    }
  }, [open])

  if (!open) return null

  return createPortal(<>
    <aside className={`drawer ${styles.drawer} show`} role="dialog" aria-modal="true" aria-label="拓展引用">
      <div className="drawer-head">
        <div className={styles.headerLead}>
          <span className={styles.headerIcon}><Boxes size={18} /></span>
          <span className="context-drawer-heading"><strong>引用拓展</strong><span>{modules.length} 个可见拓展</span></span>
        </div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="关闭拓展引用"><X size={17} /></button>
      </div>

      <div className={`drawer-body ${styles.body}`}>
        <label className={styles.searchBox}>
          <Search size={17} />
          <input
            ref={searchRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索拓展名称、说明或路径…"
            aria-label="搜索拓展"
          />
          {query && <button type="button" onClick={() => setQuery('')} aria-label="清空拓展搜索"><X size={15} /></button>}
        </label>

        <nav className={styles.scopeTabs} aria-label="拓展层级">
          {(['all', 'user', 'shared', 'global'] as const).map((value) => <button
            key={value}
            type="button"
            className={scope === value ? styles.activeTab : ''}
            aria-pressed={scope === value}
            onClick={() => setScope(value)}
          >{value === 'all' ? '全部' : scopeLabels[value]}</button>)}
        </nav>

        <div className={styles.results}>
          {loading && <div className={styles.empty}><LoaderCircle className={styles.spinning} size={22} /><strong>正在加载拓展</strong></div>}
          {error && !loading && <div className={styles.empty}><Boxes size={22} /><strong>拓展加载失败</strong><span>关闭后重新打开即可再次加载。</span></div>}
          {!loading && !error && filteredModules.map((module) => {
            const ScopeIcon = scopeIcons[module.scope]
            const displayName = module.display_name || module.name
            return <article className={styles.expandCard} key={module.id || `${module.scope}:${module.name}`}>
              <span className={`${styles.cardIcon} ${styles[module.scope]}`}><ScopeIcon size={18} /></span>
              <span className={styles.cardCopy}>
                <strong>{displayName}</strong>
                <span>{module.description || module.relative_path || module.path}</span>
                <small>{module.name} · {moduleStatus(module)}</small>
              </span>
              <span className={`${styles.scopeBadge} ${styles[module.scope]}`}>{scopeLabels[module.scope]}</span>
              <button type="button" className={styles.referenceButton} onClick={() => onReference(module)} aria-label={`引用 ${displayName}`}>
                <Quote size={14} /><span>引用</span>
              </button>
            </article>
          })}
          {!loading && !error && filteredModules.length === 0 && <div className={styles.empty}>
            <Boxes size={22} />
            <strong>{modules.length === 0 ? '暂无拓展' : '没有匹配的拓展'}</strong>
            <span>{modules.length === 0 ? '可前往拓展页面接入拓展模块。' : '请调整搜索词或切换拓展层级。'}</span>
          </div>}
        </div>
      </div>
    </aside>
    <button type="button" className="drawer-backdrop" aria-label="关闭拓展引用" onClick={onClose} />
  </>, document.body)
}
