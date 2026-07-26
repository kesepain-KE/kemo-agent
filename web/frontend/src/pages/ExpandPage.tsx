import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  Activity,
  BookOpen,
  Boxes,
  CheckCircle2,
  ChevronDown,
  Clipboard,
  Clock3,
  Code2,
  Copy,
  Database,
  Eye,
  FileText,
  Globe2,
  Layers3,
  Plus,
  RefreshCw,
  Share2,
  ShieldCheck,
  Sparkles,
  Trash2,
  UserRound,
  X,
  Zap,
} from 'lucide-react'
import { useNavigate, useOutletContext } from 'react-router-dom'
import {
  deleteExpandModule,
  getExpands,
  refreshExpandModule,
  setExpandModuleEnabled,
} from '../api/client'
import type { ShellOutletContext } from '../components/AppShell'
import { EmptyPanel, ModuleError, ModuleFrame, RefreshActionButton, formatDateTime } from '../components/ModuleUi'
import type { ExpandModuleSummary, ExpandScope } from '../types/api'
import { copyText } from '../utils/clipboard'
import styles from './ExpandPage.module.css'

const LAYERS: Array<{ scope: ExpandScope; label: string; description: string; icon: ReactNode }> = [
  { scope: 'global', label: '全局拓展', description: '系统级拓展，由当前用户白名单决定是否启用', icon: <Globe2 size={15} /> },
  { scope: 'shared', label: '共享拓展', description: '多用户共享拓展，由当前用户白名单决定是否启用', icon: <Share2 size={15} /> },
  { scope: 'user', label: '用户拓展', description: '只属于当前用户，默认进入当前用户运行管线', icon: <UserRound size={15} /> },
]

function statusOf(item: ExpandModuleSummary) {
  if (!item.valid) return { label: '配置异常', tone: 'danger' }
  if (!item.whitelisted) return { label: '白名单禁用', tone: 'warning' }
  return { label: '已启用', tone: 'success' }
}

function layerIcon(scope: ExpandScope) {
  if (scope === 'global') return <Globe2 size={19} />
  if (scope === 'shared') return <Share2 size={19} />
  return <UserRound size={19} />
}

function runtimeLabel(status?: string) {
  if (status === 'completed') return '最近成功'
  if (status === 'failed') return '最近失败'
  return '暂无记录'
}

function SummaryCard({ icon, label, value, detail, tone = 'purple' }: {
  icon: ReactNode
  label: string
  value: ReactNode
  detail: ReactNode
  tone?: 'purple' | 'green' | 'blue'
}) {
  return <article className={styles.summaryCard}>
    <span className={`${styles.summaryIcon} ${styles[tone]}`}>{icon}</span>
    <span className={styles.summaryCopy}><span>{label}</span><strong>{value}</strong><small>{detail}</small></span>
  </article>
}

function TextPreview({ title, subtitle, content, empty, copied, onCopy }: {
  title: string
  subtitle: string
  content: string
  empty: string
  copied: boolean
  onCopy: () => void
}) {
  const lineCount = content ? content.split(/\r?\n/).length : 0
  return <section className={styles.textPreview}>
    <header>
      <span><FileText size={15} /><span><strong>{title}</strong><small>{subtitle}{lineCount ? ` · ${lineCount} 行` : ''}</small></span></span>
      <button type="button" disabled={!content} onClick={onCopy}>
        {copied ? <Clipboard size={13} /> : <Copy size={13} />}{copied ? '已复制' : '复制'}
      </button>
    </header>
    {content
      ? <div className={styles.markdownContent}><ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown></div>
      : <div className={styles.previewEmpty}><Eye size={19} /><span>{empty}</span></div>}
  </section>
}

export function ExpandPage() {
  const { user } = useOutletContext<ShellOutletContext>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [activeScope, setActiveScope] = useState<ExpandScope>('global')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [addOpen, setAddOpen] = useState(false)
  const [previewMode, setPreviewMode] = useState<'data' | 'document'>('data')
  const [copiedBlock, setCopiedBlock] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  const [actionError, setActionError] = useState('')
  const addRef = useRef<HTMLDivElement>(null)

  const query = useQuery({
    queryKey: ['expands', user],
    queryFn: () => getExpands(user),
    enabled: Boolean(user),
  })
  const groups = query.data?.expands || []
  const allModules = useMemo(() => groups.flatMap((group) => group.items), [groups])
  const visibleModules = groups.find((group) => group.scope === activeScope)?.items || []
  const selected = visibleModules.find((item) => item.id === selectedId) || visibleModules[0] || null

  useEffect(() => {
    if (!visibleModules.some((item) => item.id === selectedId)) {
      setSelectedId(visibleModules[0]?.id || null)
      setPreviewMode('data')
    }
  }, [activeScope, selectedId, visibleModules])

  useEffect(() => {
    const close = (event: PointerEvent) => {
      if (addRef.current && !addRef.current.contains(event.target as Node)) setAddOpen(false)
    }
    document.addEventListener('pointerdown', close)
    return () => document.removeEventListener('pointerdown', close)
  }, [])

  const refreshMutation = useMutation({
    mutationFn: (item: ExpandModuleSummary) => refreshExpandModule(user, item.scope, item.name),
    onMutate: () => { setNotice(''); setActionError('') },
    onSuccess: async (_, item) => {
      await queryClient.invalidateQueries({ queryKey: ['expands', user] })
      setNotice(`${item.display_name || item.name} 的采集数据已更新`)
    },
    onError: (error: Error) => setActionError(error.message || '拓展数据更新失败'),
  })
  const toggleMutation = useMutation({
    mutationFn: ({ item, enabled }: { item: ExpandModuleSummary; enabled: boolean }) => setExpandModuleEnabled(user, item.scope, item.name, enabled),
    onMutate: () => { setNotice(''); setActionError('') },
    onSuccess: async (_, { item, enabled }) => {
      await queryClient.invalidateQueries({ queryKey: ['expands', user] })
      setNotice(`${item.display_name || item.name} 已${enabled ? '加入' : '移出'}当前用户白名单`)
    },
    onError: (error: Error) => setActionError(error.message || '拓展白名单更新失败'),
  })
  const deleteMutation = useMutation({
    mutationFn: (item: ExpandModuleSummary) => deleteExpandModule(user, item.name),
    onMutate: () => { setNotice(''); setActionError('') },
    onSuccess: async (_, item) => {
      setSelectedId(null)
      await queryClient.invalidateQueries({ queryKey: ['expands', user] })
      setNotice(`${item.display_name || item.name} 用户拓展已删除`)
    },
    onError: (error: Error) => setActionError(error.message || '用户拓展删除失败'),
  })

  const latestModule = useMemo(
    () => [...allModules].sort((left, right) => right.updated_at - left.updated_at)[0],
    [allModules],
  )
  const pendingId = refreshMutation.isPending
    ? refreshMutation.variables?.id
    : toggleMutation.isPending
      ? toggleMutation.variables?.item.id
      : deleteMutation.isPending
        ? deleteMutation.variables?.id
        : null

  const changeScope = (scope: ExpandScope) => {
    setActiveScope(scope)
    const first = groups.find((group) => group.scope === scope)?.items[0]
    setSelectedId(first?.id || null)
    setPreviewMode('data')
  }

  const handleCopy = async (key: string, content: string) => {
    if (!content) return
    try {
      await copyText(content)
      setCopiedBlock(key)
      window.setTimeout(() => setCopiedBlock((current) => current === key ? null : current), 1500)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '复制失败')
    }
  }

  const handleCreate = () => {
    const prompt = [
      '请为当前用户创建一个新的拓展模块。',
      '请先询问拓展名称、数据来源、需要注入系统提示词的内容、外部操作能力、触发条件和安全边界，',
      `然后在 users/${user}/expand 下创建符合现有标准协议的模块。`,
    ].join('')
    setAddOpen(false)
    navigate(`/chat?user=${encodeURIComponent(user)}&prompt=${encodeURIComponent(prompt)}`)
  }

  const handleDelete = (item: ExpandModuleSummary) => {
    if (!window.confirm(`确定删除用户拓展“${item.display_name || item.name}”吗？\n此操作会删除 ${item.path} 整个目录，且无法撤销。`)) return
    deleteMutation.mutate(item)
  }

  return <ModuleFrame
    kicker="Expand Modules / Hot Reload"
    title="拓展"
    description="拓展模块保留数据采集与系统提示词注入能力，并可按操作文档操纵外部系统；全局层、共享层由当前用户白名单控制。"
    actions={<>
      <RefreshActionButton pending={query.isFetching} label="刷新拓展数据" pendingLabel="刷新中…" onClick={() => { void query.refetch() }} />
      <div className={styles.addControl} ref={addRef}>
        <button className="module-btn primary" type="button" aria-expanded={addOpen} onClick={() => setAddOpen((current) => !current)}>
          <Plus size={15} />增加拓展模块<ChevronDown size={13} />
        </button>
        {addOpen && <div className={styles.addPopover} role="dialog" aria-label="增加拓展模块">
          <button type="button" onClick={handleCreate}>
            <span><UserRound size={17} /></span>
            <span><strong>增加到用户层</strong><small>提示智能体创建新的拓展模块</small></span>
          </button>
        </div>}
      </div>
    </>}
  >
    {query.isError && <ModuleError message="拓展模块读取失败，请检查三层注册文件与模块配置。" />}
    {actionError && <div className={styles.actionMessage} role="alert">{actionError}</div>}
    {notice && <div className={`${styles.actionMessage} ${styles.successMessage}`} role="status">{notice}</div>}

    <section className={styles.summaryGrid} aria-label="拓展统计">
      <SummaryCard icon={<Boxes size={21} />} label="拓展模块" value={query.data?.summary.total || 0} detail="全部已发现模块" />
      <SummaryCard icon={<CheckCircle2 size={21} />} label="已启用" value={query.data?.status_summary.enabled || 0} detail="当前用户可用模块" tone="green" />
      <SummaryCard icon={<Sparkles size={21} />} label="注入 Tokens" value={query.data?.injection.estimated_tokens || 0} detail={`${query.data?.injection.injected_items || 0} 个注入模块`} />
      <SummaryCard icon={<Clock3 size={21} />} label="最近更新" value={latestModule?.updated_at ? formatDateTime(latestModule.updated_at) : '—'} detail={latestModule?.display_name || '暂无模块'} tone="blue" />
    </section>

    <div className={styles.workspace}>
      <section className={styles.listPanel} aria-label="拓展模块列表">
        <div className={styles.layerTabs} role="tablist" aria-label="拓展层级">
          {LAYERS.map((layer) => <button
            key={layer.scope}
            type="button"
            role="tab"
            aria-selected={activeScope === layer.scope}
            className={activeScope === layer.scope ? styles.active : ''}
            onClick={() => changeScope(layer.scope)}
          >{layer.icon}<span>{layer.label}</span><b>{query.data?.summary[layer.scope] || 0}</b></button>)}
        </div>
        <div className={styles.listDescription}>
          <span>{LAYERS.find((layer) => layer.scope === activeScope)?.description}</span>
          <strong>{visibleModules.length} 个模块</strong>
        </div>
        {visibleModules.length ? <div className={styles.moduleList}>
          {visibleModules.map((item) => {
            const status = statusOf(item)
            const isSelected = selected?.id === item.id
            return <article
              key={item.id}
              className={`${styles.moduleCard} ${isSelected ? styles.selected : ''}`}
              role="button"
              tabIndex={0}
              aria-pressed={isSelected}
              onClick={() => { setSelectedId(item.id); setPreviewMode('data') }}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault()
                  setSelectedId(item.id)
                  setPreviewMode('data')
                }
              }}
            >
              <span className={`${styles.moduleIcon} ${styles[item.scope]}`}>{layerIcon(item.scope)}</span>
              <span className={styles.moduleCopy}>
                <span className={styles.moduleTitle}><h3>{item.display_name || item.name}</h3><span className={`${styles.statusBadge} ${styles[status.tone]}`}>{status.label}</span></span>
                <code>{item.path}</code>
                <p>{item.valid ? item.description || '未填写拓展说明' : item.error}</p>
                <span className={styles.moduleStats}>
                  <span><Database size={11} />数据 {item.open_input ? '开启' : '关闭'}</span>
                  <span><Sparkles size={11} />注入 {item.injected_tokens} tokens</span>
                  <span><Zap size={11} />操作 {item.open_control ? '开启' : '关闭'}</span>
                </span>
                <span className={styles.moduleTags}><span>热加载</span>{item.open_input && <span>数据采集</span>}{item.open_control && <span>外部操纵</span>}</span>
              </span>
              <span className={styles.moduleSide}><small>{item.updated_at ? formatDateTime(item.updated_at) : '未更新'}</small><Eye size={16} /></span>
            </article>
          })}
        </div> : <EmptyPanel title={`尚无${LAYERS.find((layer) => layer.scope === activeScope)?.label}`} description="标准模块目录创建后，点击刷新即可通过热加载发现。" icon={<Boxes size={21} />} />}
      </section>

      <aside className={styles.detailPanel} aria-label="拓展模块查看">
        {selected ? <div className={styles.detail}>
          <header className={styles.detailHeader}>
            <span className={styles.detailIdentity}>
              <span className={`${styles.moduleIcon} ${styles[selected.scope]}`}>{layerIcon(selected.scope)}</span>
              <span><span className={styles.moduleTitle}><h2>{selected.display_name || selected.name}</h2><span className={`${styles.statusBadge} ${styles[statusOf(selected).tone]}`}>{statusOf(selected).label}</span></span><small>{selected.path}</small></span>
            </span>
            <button type="button" className={styles.previewButton} onClick={() => setPreviewMode('document')}><BookOpen size={14} />查看操作文档</button>
          </header>

          <div className={styles.metadata}>
            <span><small>模块 ID</small><strong>{selected.name}</strong></span>
            <span><small>更新机制</small><strong>{selected.start_update || '未配置'}</strong></span>
            <span title={selected.runtime?.update?.error?.message || ''}><small>采集运行</small><strong>{selected.input_health} · {runtimeLabel(selected.runtime?.update?.status)}</strong></span>
            <span title={selected.runtime?.control?.error?.message || ''}><small>操控运行</small><strong>{runtimeLabel(selected.runtime?.control?.status)}</strong></span>
          </div>

          <div className={styles.detailCards}>
            <section><header><Code2 size={14} /><strong>功能能力</strong></header><p>{selected.description || '模块没有提供功能说明。'}</p><div className={styles.chips}>{selected.open_input && <span>数据采集</span>}{selected.open_control && <span>外部操纵</span>}<span>文件热加载</span></div></section>
            <section><header><Activity size={14} /><strong>触发场景与使用操作</strong></header><p>{selected.control_operation_markdown || '操作层文档未提供触发场景和具体用法。'}</p><button type="button" onClick={() => setPreviewMode('document')}>打开完整操作层</button></section>
            <section><header><ShieldCheck size={14} /><strong>{selected.scope === 'user' ? '用户层状态' : '白名单状态'}</strong></header>
              {selected.scope === 'user' ? <div className={styles.userDefault}><CheckCircle2 size={15} /><span><strong>默认启用</strong><small>用户拓展不经过全局或共享白名单</small></span></div> : <div className={styles.whitelistControl}><span><strong>当前用户白名单</strong><small>决定模块是否进入主智能体 Prompt</small></span><button type="button" role="switch" aria-label={`${selected.display_name || selected.name} 白名单`} aria-checked={selected.whitelisted} className={selected.whitelisted ? styles.checked : ''} disabled={!selected.valid || pendingId === selected.id} onClick={() => toggleMutation.mutate({ item: selected, enabled: !selected.whitelisted })}><span /></button></div>}
              <div className={styles.permissionLine}><span>数据采集</span><strong>{selected.open_input ? '已配置' : '未开启'}</strong></div>
              <div className={styles.permissionLine}><span>提示词注入</span><strong>{selected.injected_markdown ? '已注入' : '未注入'}</strong></div>
              <div className={styles.permissionLine}><span>外部操作</span><strong>{selected.open_control ? '已配置' : '未开启'}</strong></div>
            </section>
          </div>

          <div className={styles.previewTabs} role="tablist" aria-label="拓展详情内容">
            <button type="button" role="tab" aria-selected={previewMode === 'data'} className={previewMode === 'data' ? styles.active : ''} onClick={() => setPreviewMode('data')}><Database size={13} />数据与注入</button>
            <button type="button" role="tab" aria-selected={previewMode === 'document'} className={previewMode === 'document' ? styles.active : ''} onClick={() => setPreviewMode('document')}><BookOpen size={13} />操作文档</button>
          </div>
          <div className={styles.previewGrid}>
            {previewMode === 'data' ? <>
              <TextPreview title="采集数据预览" subtitle={selected.input_data || '未配置数据文件'} content={selected.collected_markdown} empty={selected.open_input ? '当前采集数据为空' : '该模块未开启数据采集'} copied={copiedBlock === 'collected'} onCopy={() => void handleCopy('collected', selected.collected_markdown)} />
              <TextPreview title="系统提示词注入预览" subtitle="真实 Expand Data 注入片段" content={selected.injected_markdown} empty={selected.whitelisted ? '该模块当前没有内容进入 Prompt' : '该模块已被当前用户白名单禁用'} copied={copiedBlock === 'injected'} onCopy={() => void handleCopy('injected', selected.injected_markdown)} />
            </> : <>
              <TextPreview title="操控能力（注入层）" subtitle={selected.start_control || '未配置操作文档'} content={selected.control_injection_markdown} empty="操作文档未提供“注入层”内容" copied={copiedBlock === 'control'} onCopy={() => void handleCopy('control', selected.control_injection_markdown)} />
              <TextPreview title="触发场景与具体操作（操作层）" subtitle="完整使用方法与命令说明" content={selected.control_operation_markdown} empty="操作文档未提供“操作层”内容" copied={copiedBlock === 'operation'} onCopy={() => void handleCopy('operation', selected.control_operation_markdown)} />
            </>}
          </div>

          <div className={styles.detailActions}>
            <button type="button" onClick={() => setPreviewMode('document')}><BookOpen size={14} />查看文档</button>
            <button type="button" disabled={!selected.valid || !selected.start_update || pendingId === selected.id} onClick={() => refreshMutation.mutate(selected)}><RefreshCw className={refreshMutation.isPending && pendingId === selected.id ? styles.spinning : ''} size={14} />更新数据</button>
            <button type="button" onClick={() => setPreviewMode('data')}><Eye size={14} />预览注入</button>
            {selected.scope === 'user' && <button type="button" className={styles.dangerButton} disabled={pendingId === selected.id} onClick={() => handleDelete(selected)}><Trash2 size={14} />删除用户拓展</button>}
          </div>
        </div> : <div className={styles.detailEmpty}><Boxes size={28} /><h3>请选择拓展模块</h3><p>从左侧选择模块后查看功能、触发场景、操作文档、采集数据与真实注入片段。</p></div>}
      </aside>
    </div>
  </ModuleFrame>
}
