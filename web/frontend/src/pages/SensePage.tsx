import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  CheckCircle2,
  Clipboard,
  Clock3,
  Copy,
  Database,
  Eye,
  FileText,
  Info,
  Layers3,
  Plus,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react'
import { useOutletContext } from 'react-router-dom'
import {
  deleteSenseModule,
  getSense,
  refreshSenseModule,
  setSenseModuleEnabled,
} from '../api/client'
import type { ShellOutletContext } from '../components/AppShell'
import { EmptyPanel, ModuleError, ModuleFrame } from '../components/ModuleUi'
import type { SenseSourceSummary } from '../types/api'
import { copyText } from '../utils/clipboard'
import styles from './SensePage.module.css'

function moduleStatus(source: SenseSourceSummary) {
  if (!source.valid) return { label: '配置异常', tone: 'danger' }
  if (!source.whitelisted) return { label: '白名单禁用', tone: 'muted' }
  return { label: '已启用', tone: 'success' }
}

function SummaryCard({
  icon,
  label,
  value,
  detail,
  tone = 'purple',
}: {
  icon: ReactNode
  label: string
  value: ReactNode
  detail: ReactNode
  tone?: 'purple' | 'green' | 'blue'
}) {
  return <article className={styles.summaryCard}>
    <span className={`${styles.summaryIcon} ${styles[tone]}`}>{icon}</span>
    <span className={styles.summaryCopy}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </span>
  </article>
}

function MarkdownPane({
  title,
  content,
  empty,
  copied,
  onCopy,
  className = '',
}: {
  title: string
  content: string
  empty: string
  copied: boolean
  onCopy: () => void
  className?: string
}) {
  const lineCount = content ? content.split(/\r?\n/).length : 0
  return <section className={`${styles.markdownPane} ${className}`}>
    <header>
      <span><FileText size={15} /><strong>{title}</strong>{lineCount > 0 && <small>{lineCount} 行</small>}</span>
      <button type="button" onClick={onCopy} disabled={!content}>
        {copied ? <Clipboard size={14} /> : <Copy size={14} />}
        {copied ? '已复制' : '复制'}
      </button>
    </header>
    {content
      ? <pre>{content}</pre>
      : <div className={styles.markdownEmpty}><Eye size={20} /><span>{empty}</span></div>}
  </section>
}

export function SensePage() {
  const { user } = useOutletContext<ShellOutletContext>()
  const queryClient = useQueryClient()
  const [selectedModuleId, setSelectedModuleId] = useState<string | null>(null)
  const [guideOpen, setGuideOpen] = useState(false)
  const [copiedBlock, setCopiedBlock] = useState<'global' | 'collected' | 'injected' | null>(null)
  const [notice, setNotice] = useState('')
  const [actionError, setActionError] = useState('')

  const query = useQuery({
    queryKey: ['sense', user],
    queryFn: () => getSense(user),
    enabled: Boolean(user),
  })
  const data = query.data
  const sources = data?.sources || []
  const selectedModule = sources.find((source) => source.id === selectedModuleId) || null

  useEffect(() => {
    if (selectedModuleId && data && !data.sources.some((source) => source.id === selectedModuleId)) {
      setSelectedModuleId(null)
    }
  }, [data, selectedModuleId])

  const refreshMutation = useMutation({
    mutationFn: (moduleName: string) => refreshSenseModule(user, moduleName),
    onMutate: () => { setActionError(''); setNotice('') },
    onSuccess: async (_, moduleName) => {
      await queryClient.invalidateQueries({ queryKey: ['sense', user] })
      setNotice(`${moduleName} 感知信息已更新`)
    },
    onError: (error: Error) => setActionError(error.message || '感知模块更新失败'),
  })
  const toggleMutation = useMutation({
    mutationFn: ({ moduleName, enabled }: { moduleName: string; enabled: boolean }) => setSenseModuleEnabled(user, moduleName, enabled),
    onMutate: () => { setActionError(''); setNotice('') },
    onSuccess: async (_, variables) => {
      await queryClient.invalidateQueries({ queryKey: ['sense', user] })
      setNotice(`${variables.moduleName} 已${variables.enabled ? '加入' : '移出'}当前用户白名单`)
    },
    onError: (error: Error) => setActionError(error.message || '感知白名单更新失败'),
  })
  const deleteMutation = useMutation({
    mutationFn: (moduleName: string) => deleteSenseModule(user, moduleName),
    onMutate: () => { setActionError(''); setNotice('') },
    onSuccess: async (_, moduleName) => {
      setSelectedModuleId(null)
      await queryClient.invalidateQueries({ queryKey: ['sense', user] })
      setNotice(`${moduleName} 全局感知模块已删除`)
    },
    onError: (error: Error) => setActionError(error.message || '感知模块删除失败'),
  })

  const latestSource = useMemo(
    () => [...sources].sort((left, right) => (right.updated_at || 0) - (left.updated_at || 0))[0],
    [sources],
  )
  const pendingModule = refreshMutation.isPending
    ? refreshMutation.variables
    : toggleMutation.isPending
      ? toggleMutation.variables?.moduleName
      : deleteMutation.isPending
        ? deleteMutation.variables
        : null

  const handleCopy = async (block: 'global' | 'collected' | 'injected', content: string) => {
    if (!content) return
    try {
      await copyText(content)
      setCopiedBlock(block)
      window.setTimeout(() => setCopiedBlock((current) => current === block ? null : current), 1500)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '复制失败')
    }
  }

  const handleDelete = (source: SenseSourceSummary) => {
    if (!window.confirm(`确定删除全局感知模块“${source.display_name || source.name}”吗？\n此操作会删除 global_sense/${source.name} 整个目录，且无法撤销。`)) return
    deleteMutation.mutate(source.name)
  }

  return <ModuleFrame
    kicker="System Capability / Global Sense"
    title="感知"
    description="全局感知模块通过标准 Markdown 数据文件热加载；模块负责采集信息并注入系统提示词，当前用户配置中的白名单决定最终启用范围。"
    actions={<>
      <button className="module-btn" type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>
        <RefreshCw className={query.isFetching ? styles.spinning : ''} size={15} />重新读取
      </button>
      <button className="module-btn primary" type="button" onClick={() => setGuideOpen((current) => !current)}>
        <Plus size={15} />注册感知模块
      </button>
    </>}
  >
    {query.isError && <ModuleError message="全局感知读取失败，请检查模块配置后重试。" />}
    {actionError && <div className={styles.actionMessage} role="alert">{actionError}</div>}
    {notice && <div className={`${styles.actionMessage} ${styles.successMessage}`} role="status">{notice}</div>}

    {guideOpen && <section className={styles.registrationGuide} aria-label="感知模块注册说明">
      <span><Info size={18} /></span>
      <div>
        <strong>一个目录对应一个全局感知模块</strong>
        <p><code>sense.json</code> 指定唯一 <code>data_md</code> 和更新入口；Markdown 文件更新后，下一轮系统提示词会自动读取最新内容。</p>
      </div>
      <code>global_sense/&lt;module&gt;/sense.json</code>
      <button type="button" aria-label="关闭注册说明" onClick={() => setGuideOpen(false)}><X size={15} /></button>
    </section>}

    <section className={styles.summaryGrid} aria-label="感知统计">
      <SummaryCard icon={<Layers3 size={21} />} label="感知模块" value={data?.summary.registered || 0} detail="个全局模块" />
      <SummaryCard icon={<CheckCircle2 size={21} />} label="当前启用" value={data?.summary.enabled || 0} detail={`共 ${data?.summary.registered || 0} 个模块`} tone="green" />
      <SummaryCard icon={<Sparkles size={21} />} label="注入 Tokens" value={data?.injection.estimated_tokens || 0} detail={`${data?.injection.injected_chars || 0} 个字符`} />
      <SummaryCard icon={<Clock3 size={21} />} label="最近更新" value={latestSource?.recent_update ? '已更新' : '—'} detail={latestSource?.recent_update || '暂无更新'} tone="blue" />
    </section>

    <div className={styles.workspace}>
      <section className={styles.panel} aria-label="感知模块列表">
        <header className={styles.panelHead}>
          <span><strong>感知模块</strong><small>全局层 · {sources.length} 个模块</small></span>
          {selectedModule && <button className={styles.overviewButton} type="button" onClick={() => setSelectedModuleId(null)}><X size={14} />查看全部注入</button>}
        </header>
        {sources.length
          ? <div className={styles.moduleList}>
            {sources.map((source) => {
              const status = moduleStatus(source)
              const selected = selectedModuleId === source.id
              return <article
                key={source.id}
                className={`${styles.moduleCard} ${selected ? styles.selected : ''}`}
                role="button"
                tabIndex={0}
                aria-pressed={selected}
                onClick={() => setSelectedModuleId(selected ? null : source.id)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    setSelectedModuleId(selected ? null : source.id)
                  }
                }}
              >
                <span className={styles.moduleIcon}><Activity size={20} /></span>
                <span className={styles.moduleCopy}>
                  <span className={styles.moduleTitle}>
                    <h3>{source.display_name || source.name}</h3>
                    <span className={`${styles.statusBadge} ${styles[status.tone]}`}>{status.label}</span>
                  </span>
                  <small>模块 ID：{source.name} · 文件：{source.data_md || '配置缺失'}</small>
                  <p>{source.valid ? source.value_preview || source.description : source.error}</p>
                  <span className={styles.moduleMeta}>
                    <span>文件热加载</span><span>{source.recent_update || '未记录更新时间'}</span><span>{source.health}</span>
                  </span>
                </span>
                <Eye className={styles.rowArrow} size={17} />
              </article>
            })}
          </div>
          : <EmptyPanel title="尚无全局感知模块" description="在 global_sense 下注册模块后，点击重新读取即可热发现。" icon={<Activity size={21} />} />}
      </section>

      <aside className={`${styles.panel} ${styles.previewPanel}`} aria-label="感知模块查看">
        {!selectedModule
          ? <div className={styles.overview}>
            <header className={styles.previewHead}>
              <span><strong>全局感知注入预览</strong><small>未选择模块时，默认展示当前用户全部实际注入 Markdown</small></span>
              <span className={`${styles.statusBadge} ${data?.injection.enabled ? styles.success : styles.muted}`}>
                {data?.injection.enabled ? '当前默认总览' : '当前无注入'}
              </span>
            </header>
            <div className={styles.infoGrid}>
              <span><ShieldCheck size={15} /><small>白名单范围</small><strong>{data?.summary.enabled || 0} / {data?.summary.registered || 0}</strong></span>
              <span><Database size={15} /><small>注入位置</small><strong>{data?.injection.prompt_position || 'System Prompt / Global Sense'}</strong></span>
              <span><FileText size={15} /><small>来源文件</small><strong>{data?.injection.source_files.length || 0} 个</strong></span>
              <span><Sparkles size={15} /><small>注入规模</small><strong>{data?.injection.estimated_tokens || 0} tokens</strong></span>
            </div>
            <MarkdownPane
              className={styles.globalMarkdown}
              title="全部注入 Markdown"
              content={data?.injection.content || data?.injection.preview || ''}
              empty="当前用户没有启用任何可注入的全局感知模块"
              copied={copiedBlock === 'global'}
              onCopy={() => void handleCopy('global', data?.injection.content || data?.injection.preview || '')}
            />
          </div>
          : <div className={styles.detail}>
            <header className={styles.detailHead}>
              <span className={styles.detailIdentity}>
                <span className={styles.moduleIcon}><Activity size={20} /></span>
                <span><strong>{selectedModule.display_name || selectedModule.name}</strong><small>{selectedModule.name} · {selectedModule.data_md || '配置缺失'}</small></span>
              </span>
              <span className={`${styles.statusBadge} ${styles[moduleStatus(selectedModule).tone]}`}>{moduleStatus(selectedModule).label}</span>
            </header>

            <div className={styles.detailActions}>
              <span className={styles.whitelistControl}>
                <span><strong>当前用户白名单</strong><small>决定该模块是否进入主智能体 Prompt</small></span>
                <button
                  type="button"
                  role="switch"
                  aria-checked={selectedModule.whitelisted}
                  aria-label={`${selectedModule.display_name || selectedModule.name} 白名单`}
                  className={`${styles.switch} ${selectedModule.whitelisted ? styles.checked : ''}`}
                  disabled={pendingModule === selectedModule.name}
                  onClick={() => toggleMutation.mutate({ moduleName: selectedModule.name, enabled: !selectedModule.whitelisted })}
                ><span /></button>
              </span>
              <button type="button" disabled={!selectedModule.valid || pendingModule === selectedModule.name} onClick={() => refreshMutation.mutate(selectedModule.name)}>
                <RefreshCw className={refreshMutation.isPending && refreshMutation.variables === selectedModule.name ? styles.spinning : ''} size={15} />信息更新
              </button>
              <button className={styles.dangerButton} type="button" disabled={pendingModule === selectedModule.name} onClick={() => handleDelete(selectedModule)}>
                <Trash2 size={15} />删除模块
              </button>
            </div>

            <div className={styles.moduleFacts}>
              <span><small>健康状态</small><strong>{selectedModule.health}</strong></span>
              <span><small>更新机制</small><strong>{selectedModule.start_update ? `手动入口 · ${selectedModule.start_update}` : '未配置'}</strong></span>
              <span><small>最近更新</small><strong>{selectedModule.recent_update || '未记录'}</strong></span>
              <span><small>实际注入</small><strong>{selectedModule.injected_tokens || 0} tokens</strong></span>
            </div>

            <div className={styles.detailPreviewGrid}>
              <MarkdownPane
                title="模块采集信息"
                content={selectedModule.collected_markdown || ''}
                empty={selectedModule.valid ? '数据文件当前为空' : selectedModule.error || '模块配置无效'}
                copied={copiedBlock === 'collected'}
                onCopy={() => void handleCopy('collected', selectedModule.collected_markdown || '')}
              />
              <MarkdownPane
                title="系统提示词注入片段"
                content={selectedModule.injected_markdown || ''}
                empty={selectedModule.whitelisted ? '该模块当前没有内容进入 Prompt' : '该模块未在当前用户白名单中'}
                copied={copiedBlock === 'injected'}
                onCopy={() => void handleCopy('injected', selectedModule.injected_markdown || '')}
              />
            </div>
          </div>}
      </aside>
    </div>
  </ModuleFrame>
}
