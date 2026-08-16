import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  BookOpenCheck,
  Boxes,
  Layers3,
  LoaderCircle,
  PlugZap,
  Quote,
  Search,
  Share2,
  UserRound,
  X,
} from 'lucide-react'
import styles from './CapabilityReferenceDrawer.module.css'

export type CapabilityKind = 'expand' | 'skill' | 'plugin'
export type CapabilityScope = 'global' | 'shared' | 'user'

export interface CapabilityReferenceItem {
  id: string
  kind: CapabilityKind
  scope: CapabilityScope
  name: string
  title: string
  description: string
  path: string
  status: string
}

type ScopeFilter = 'all' | CapabilityScope

const kindLabels: Record<CapabilityKind, string> = {
  expand: '拓展',
  skill: '技能',
  plugin: '插件',
}

const kindIcons = {
  expand: Boxes,
  skill: BookOpenCheck,
  plugin: PlugZap,
}

const scopeLabels: Record<CapabilityScope, string> = {
  global: '全局',
  shared: '共享',
  user: '用户',
}

const scopeIcons = {
  global: Layers3,
  shared: Share2,
  user: UserRound,
}

const scopeOrder: readonly CapabilityScope[] = ['global', 'shared', 'user']

const emptyHelp: Record<CapabilityKind, string> = {
  expand: '可前往拓展页面接入拓展模块。',
  skill: '可前往技能页面安装共享技能或用户技能。',
  plugin: '可前往技能与插件页面检查全局插件。',
}

export interface CapabilityReferenceDrawerProps {
  open: boolean
  items: CapabilityReferenceItem[]
  loading?: Partial<Record<CapabilityKind, boolean>>
  error?: Partial<Record<CapabilityKind, boolean>>
  onClose: () => void
  onReference: (item: CapabilityReferenceItem) => void
}

export function CapabilityReferenceDrawer({
  open,
  items,
  loading = {},
  error = {},
  onClose,
  onReference,
}: CapabilityReferenceDrawerProps) {
  const [kind, setKind] = useState<CapabilityKind>('expand')
  const [scope, setScope] = useState<ScopeFilter>('all')
  const [query, setQuery] = useState('')
  const searchRef = useRef<HTMLInputElement>(null)
  const normalizedQuery = query.trim().toLocaleLowerCase()
  const kindItems = useMemo(() => items.filter((item) => item.kind === kind), [items, kind])
  const scopeOptions = useMemo<ScopeFilter[]>(() => {
    const available = new Set(kindItems.map((item) => item.scope))
    const scopes = scopeOrder.filter((value) => available.has(value))
    return scopes.length > 1 ? ['all', ...scopes] : scopes
  }, [kindItems])
  const filteredItems = useMemo(() => kindItems.filter((item) => (
    (scope === 'all' || item.scope === scope)
    && (!normalizedQuery || `${item.title} ${item.name} ${item.description} ${item.path} ${item.status}`
      .toLocaleLowerCase()
      .includes(normalizedQuery))
  )), [kindItems, normalizedQuery, scope])

  useEffect(() => {
    if (open) requestAnimationFrame(() => searchRef.current?.focus())
    else {
      setKind('expand')
      setScope('all')
      setQuery('')
    }
  }, [open])

  useEffect(() => {
    if (!scopeOptions.includes(scope)) setScope(scopeOptions[0] ?? 'all')
  }, [scope, scopeOptions])

  const selectKind = (value: CapabilityKind) => {
    setKind(value)
    setScope('all')
  }
  const KindIcon = kindIcons[kind]
  const busy = Boolean(loading[kind])
  const failed = Boolean(error[kind])

  return createPortal(<>
    <aside
      className={`drawer ${styles.drawer} ${open ? 'show' : ''}`}
      inert={!open}
      role="dialog"
      aria-modal="true"
      aria-label="能力引用"
      aria-hidden={!open}
    >
      <div className="drawer-head">
        <div className={styles.headerLead}>
          <span className={styles.headerIcon}><KindIcon size={18} /></span>
          <span className="context-drawer-heading"><strong>引用能力</strong><span>{items.length} 项可引用能力</span></span>
        </div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="关闭能力引用"><X size={17} /></button>
      </div>

      <div className={`drawer-body ${styles.body}`}>
        <nav className={styles.kindTabs} aria-label="能力类型" role="tablist">
          {(Object.keys(kindLabels) as CapabilityKind[]).map((value) => {
            const Icon = kindIcons[value]
            return <button
              key={value}
              type="button"
              role="tab"
              aria-selected={kind === value}
              className={kind === value ? styles.activeKindTab : ''}
              onClick={() => selectKind(value)}
            ><Icon size={15} /><span>{kindLabels[value]}</span></button>
          })}
        </nav>

        <label className={styles.searchBox}>
          <Search size={17} />
          <input
            ref={searchRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={`搜索${kindLabels[kind]}名称、说明或路径…`}
            aria-label={`搜索${kindLabels[kind]}`}
          />
          {query && <button type="button" onClick={() => setQuery('')} aria-label={`清空${kindLabels[kind]}搜索`}><X size={15} /></button>}
        </label>

        {scopeOptions.length > 0 && <nav className={styles.scopeTabs} aria-label={`${kindLabels[kind]}层级`}>
          {scopeOptions.map((value) => <button
            key={value}
            type="button"
            className={scope === value ? styles.activeScopeTab : ''}
            aria-pressed={scope === value}
            onClick={() => setScope(value)}
          >{value === 'all' ? '全部' : scopeLabels[value]}</button>)}
        </nav>}

        <div className={styles.results}>
          {busy && <div className={styles.empty}><LoaderCircle className={styles.spinning} size={22} /><strong>正在加载{kindLabels[kind]}</strong></div>}
          {failed && !busy && <div className={styles.empty}><KindIcon size={22} /><strong>{kindLabels[kind]}加载失败</strong><span>关闭后重新打开即可再次加载。</span></div>}
          {!busy && !failed && filteredItems.map((item) => {
            const ScopeIcon = scopeIcons[item.scope]
            return <article className={styles.capabilityCard} key={item.id}>
              <span className={`${styles.cardIcon} ${styles[item.scope]}`}><ScopeIcon size={18} /></span>
              <span className={styles.cardCopy}>
                <strong>{item.title || item.name}</strong>
                <span>{item.description || item.path}</span>
                <small>{item.name} · {item.status}</small>
              </span>
              <span className={`${styles.scopeBadge} ${styles[item.scope]}`}>{scopeLabels[item.scope]}</span>
              <button type="button" className={styles.referenceButton} onClick={() => onReference(item)} aria-label={`引用 ${item.title || item.name}`}>
                <Quote size={14} /><span>引用</span>
              </button>
            </article>
          })}
          {!busy && !failed && filteredItems.length === 0 && <div className={styles.empty}>
            <KindIcon size={22} />
            <strong>{kindItems.length === 0 ? `暂无${kindLabels[kind]}` : `没有匹配的${kindLabels[kind]}`}</strong>
            <span>{kindItems.length === 0 ? emptyHelp[kind] : `请调整搜索词或切换${kindLabels[kind]}层级。`}</span>
          </div>}
        </div>
      </div>
    </aside>
    {open && <button type="button" className="drawer-backdrop" aria-label="关闭能力引用" onClick={onClose} />}
  </>, document.body)
}
