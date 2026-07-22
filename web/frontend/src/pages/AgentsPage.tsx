import { useMemo, useState, type MouseEvent, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, Check, Copy, Layers3, Search, Trash2, Users } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useOutletContext } from 'react-router-dom'
import { deleteUserAgent, getAgents } from '../api/client'
import type { ShellOutletContext } from '../components/AppShell'
import type { AgentsResponse } from '../types/api'
import { copyText } from '../utils/clipboard'
import styles from './AgentsPage.module.css'

type AgentItem = AgentsResponse['agents'][number]
type AgentLayer = AgentItem['source']

function layerLabel(layer: AgentLayer) {
  return layer === 'global' ? '全局层' : '用户层'
}

function statusLabel(enabled: boolean) {
  return enabled ? '已启用' : '已停用'
}

function versionLabel(version: string) {
  const normalized = String(version || '').trim().replace(/^v/i, '')
  return normalized ? `v${normalized}` : '未声明版本'
}

function agentKey(agent: AgentItem) {
  return `${agent.source}:${agent.name}`
}

export function AgentsPage() {
  const { user } = useOutletContext<ShellOutletContext>()
  const queryClient = useQueryClient()
  const [activeLayer, setActiveLayer] = useState<AgentLayer>('global')
  const [searchText, setSearchText] = useState('')
  const [selectedKey, setSelectedKey] = useState('')
  const [copiedName, setCopiedName] = useState('')
  const [deleteTarget, setDeleteTarget] = useState('')
  const [feedback, setFeedback] = useState('')
  const agentsQuery = useQuery({
    queryKey: ['agents', user],
    queryFn: () => getAgents(user),
    enabled: Boolean(user),
  })
  const agents = agentsQuery.data?.agents ?? []
  const layerAgents = useMemo(
    () => agents.filter((agent) => agent.source === activeLayer),
    [activeLayer, agents],
  )
  const layerSummary = agentsQuery.data ? {
    total: layerAgents.length,
    enabled: layerAgents.filter((agent) => agent.enabled).length,
  } : null
  const layerCounts = agentsQuery.data ? {
    global: agents.filter((agent) => agent.source === 'global').length,
    user: agents.filter((agent) => agent.source === 'user').length,
  } : null
  const filteredAgents = useMemo(() => {
    const keyword = searchText.trim().toLocaleLowerCase()
    return layerAgents.filter((agent) => {
      if (!keyword) return true
      return [agent.name, agent.description, agent.trigger, agent.version]
        .some((value) => String(value || '').toLocaleLowerCase().includes(keyword))
    })
  }, [layerAgents, searchText])
  const selectedAgent = filteredAgents.find((agent) => agentKey(agent) === selectedKey)
    ?? filteredAgents[0]
    ?? null

  const deleteMutation = useMutation({
    mutationFn: (name: string) => deleteUserAgent(user, name),
    onSuccess: (result) => {
      queryClient.setQueryData<AgentsResponse>(['agents', user], (current) => {
        if (!current) return current
        const nextAgents = current.agents.filter((agent) => !(agent.source === 'user' && agent.name === result.name))
        return {
          ...current,
          summary: {
            total: nextAgents.length,
            enabled: nextAgents.filter((agent) => agent.enabled).length,
            global: nextAgents.filter((agent) => agent.source === 'global').length,
            user: nextAgents.filter((agent) => agent.source === 'user').length,
          },
          agents: nextAgents,
        }
      })
      setSelectedKey('')
      setDeleteTarget('')
      setFeedback(`已删除用户子智能体 ${result.name}`)
      void queryClient.invalidateQueries({ queryKey: ['agents', user] })
    },
  })

  const changeLayer = (layer: AgentLayer) => {
    setActiveLayer(layer)
    setSelectedKey('')
    setDeleteTarget('')
    setFeedback('')
  }

  const copyName = async (event: MouseEvent, name: string) => {
    event.stopPropagation()
    try {
      await copyText(name)
      setCopiedName(name)
      window.setTimeout(() => setCopiedName((current) => current === name ? '' : current), 1600)
    } catch {
      setCopiedName('')
    }
  }

  return (
    <div className="view module-view active">
      <div className="module-shell">
        <main className={`module-inner ${styles.page}`}>
          <header className={styles.header}>
            <div>
              <div className={styles.titleRow}><h2>子智能体</h2><span>{user || '未选择用户'}</span></div>
              <p>kemo-agent 支持两种层级的子智能体：全局层由系统提供并对所有用户可用，用户层由当前用户创建，仅在该用户范围内生效。</p>
            </div>
          </header>

          <div className={styles.layerTabs} role="tablist" aria-label="子智能体层级">
            <LayerTab layer="global" active={activeLayer === 'global'} count={layerCounts?.global} onClick={() => changeLayer('global')} />
            <LayerTab layer="user" active={activeLayer === 'user'} count={layerCounts?.user} onClick={() => changeLayer('user')} />
          </div>

          <section className={styles.summaryGrid} aria-label="子智能体统计">
            <SummaryCard label="已发现" value={layerSummary?.total ?? '—'} description="当前层级子智能体总数" icon={<Bot size={18} />} />
            <SummaryCard label="已启用" value={layerSummary?.enabled ?? '—'} description="当前层级启用数量" icon={<Check size={18} />} />
            <SummaryCard label="当前层级" value={layerLabel(activeLayer)} description={activeLayer === 'global' ? '系统内置，所有用户可用' : `仅对用户 ${user} 生效`} icon={activeLayer === 'global' ? <Layers3 size={18} /> : <Users size={18} />} />
          </section>

          {agentsQuery.isError ? <div className={styles.errorBanner}>子智能体读取失败，请检查子代理配置文件。</div> : null}
          {feedback ? <div className={styles.feedback} role="status">{feedback}</div> : null}

          <section className={styles.workspace}>
            <div className={styles.listPanel}>
              <div className={styles.panelHeading}>
                <div><h3>子智能体列表</h3><p>选择子智能体查看详细信息</p></div>
                <label className={styles.search}>
                  <Search size={16} />
                  <input type="search" value={searchText} placeholder="搜索子智能体名称" aria-label="搜索子智能体" onChange={(event) => setSearchText(event.target.value)} />
                </label>
              </div>
              <div className={styles.agentList}>
                {agentsQuery.isLoading ? <div className={styles.emptyState}><Bot size={25} /><strong>正在读取子智能体</strong><p>正在解析全局层与用户层注册信息。</p></div> : null}
                {!agentsQuery.isLoading && filteredAgents.map((agent) => (
                  <AgentListItem
                    key={agentKey(agent)}
                    agent={agent}
                    selected={selectedAgent ? agentKey(selectedAgent) === agentKey(agent) : false}
                    copied={copiedName === agent.name}
                    onSelect={() => { setSelectedKey(agentKey(agent)); setDeleteTarget(''); setFeedback('') }}
                    onCopy={(event) => void copyName(event, agent.name)}
                  />
                ))}
                {!agentsQuery.isLoading && filteredAgents.length === 0 ? <EmptyState layer={activeLayer} hasSearch={Boolean(searchText.trim())} /> : null}
              </div>
            </div>

            <div className={styles.detailPanel}>
              {selectedAgent ? (
                <AgentDetail
                  agent={selectedAgent}
                  copied={copiedName === selectedAgent.name}
                  confirmingDelete={deleteTarget === selectedAgent.name}
                  deleting={deleteMutation.isPending}
                  deleteError={deleteMutation.isError ? (deleteMutation.error instanceof Error ? deleteMutation.error.message : '删除失败') : ''}
                  onCopy={(event) => void copyName(event, selectedAgent.name)}
                  onRequestDelete={() => { setDeleteTarget(selectedAgent.name); deleteMutation.reset() }}
                  onCancelDelete={() => { setDeleteTarget(''); deleteMutation.reset() }}
                  onConfirmDelete={() => deleteMutation.mutate(selectedAgent.name)}
                />
              ) : <div className={styles.detailEmpty}><Bot size={28} /><h3>暂无可查看的子智能体</h3><p>当前层级中没有符合条件的子智能体。</p></div>}
            </div>
          </section>
        </main>
      </div>
    </div>
  )
}

function LayerTab({ layer, active, count, onClick }: { layer: AgentLayer; active: boolean; count?: number; onClick: () => void }) {
  const Icon = layer === 'global' ? Layers3 : Users
  return <button type="button" role="tab" aria-selected={active} className={active ? styles.active : ''} onClick={onClick}><Icon size={17} /><span>{layerLabel(layer)}</span><b>{count ?? '—'}</b></button>
}

function SummaryCard({ label, value, description, icon }: { label: string; value: ReactNode; description: string; icon: ReactNode }) {
  return <article className={styles.summaryCard}><span>{label}</span><strong>{value}</strong><p>{description}</p><i>{icon}</i></article>
}

function AgentListItem({ agent, selected, copied, onSelect, onCopy }: { agent: AgentItem; selected: boolean; copied: boolean; onSelect: () => void; onCopy: (event: MouseEvent) => void }) {
  return <article className={`${styles.agentItem} ${selected ? styles.selected : ''}`}>
    <button type="button" className={styles.agentSelect} onClick={onSelect}>
      <span className={styles.agentIcon}><Bot size={19} /></span>
      <span className={styles.agentContent}>
        <span className={styles.agentName}>{agent.name}</span>
        <span className={styles.agentDescription}>{agent.description || '未提供描述'}</span>
        <span className={styles.agentMetadata}><span>{versionLabel(agent.version)}</span><i /><span>触发：{agent.trigger || '未声明独立触发条件'}</span></span>
      </span>
      <StatusBadge enabled={agent.enabled} />
    </button>
    <button type="button" className={`${styles.itemCopy} ${copied ? styles.copied : ''}`} aria-label={`复制子智能体名称 ${agent.name}`} title="复制子智能体名称" onClick={onCopy}>{copied ? <Check size={14} /> : <Copy size={14} />}</button>
  </article>
}

function AgentDetail({ agent, copied, confirmingDelete, deleting, deleteError, onCopy, onRequestDelete, onCancelDelete, onConfirmDelete }: {
  agent: AgentItem
  copied: boolean
  confirmingDelete: boolean
  deleting: boolean
  deleteError: string
  onCopy: (event: MouseEvent) => void
  onRequestDelete: () => void
  onCancelDelete: () => void
  onConfirmDelete: () => void
}) {
  const executorTags = [...new Set([agent.executor, agent.execution, agent.model_profile, agent.exposure].filter(Boolean))]
  return <article className={styles.detail}>
    <header className={styles.detailHeader}>
      <div className={styles.identity}><span className={styles.detailIcon}><Bot size={22} /></span><div><div className={styles.detailName}><h3>{agent.name}</h3><button type="button" className={copied ? styles.copied : ''} onClick={onCopy}>{copied ? <><Check size={14} />已复制</> : <><Copy size={14} />复制名称</>}</button></div><p>{agent.description || '未提供描述'}</p></div></div>
      <div className={styles.detailActions}><StatusBadge enabled={agent.enabled} />{agent.source === 'user' ? <button type="button" className={styles.deleteButton} onClick={onRequestDelete}><Trash2 size={15} />删除</button> : null}</div>
    </header>
    {confirmingDelete ? <div className={styles.deleteConfirm} role="alertdialog" aria-label="确认删除用户子智能体"><span><strong>确认删除 {agent.name}？</strong><small>将永久删除 users 目录中的整个子智能体包，无法撤销。</small>{deleteError ? <em>{deleteError}</em> : null}</span><div><button type="button" onClick={onCancelDelete} disabled={deleting}>取消</button><button type="button" className={styles.confirmDelete} onClick={onConfirmDelete} disabled={deleting}>{deleting ? '正在删除…' : '确认删除'}</button></div></div> : null}
    <div className={styles.detailRows}>
      <DetailRow label="名称"><code>{agent.name}</code></DetailRow>
      <DetailRow label="版本号">{versionLabel(agent.version)}</DetailRow>
      <DetailRow label="触发条件"><p>{agent.trigger || '未声明独立触发条件'}</p></DetailRow>
      <DetailRow label="规则"><div className={styles.rulesText}><ReactMarkdown remarkPlugins={[remarkGfm]}>{agent.rules || '未提供 AGENT.md 规则。'}</ReactMarkdown></div></DetailRow>
      <DetailRow label="来源层级"><div className={styles.layerValue}><span className={agent.source === 'global' ? styles.globalBadge : styles.userBadge}>{layerLabel(agent.source)}</span><span>{agent.source === 'global' ? '系统内置，所有用户可用' : '用户自定义，仅对当前用户生效'}</span></div></DetailRow>
      <DetailRow label="执行器"><div className={styles.executorList}>{executorTags.map((tag) => <code key={tag}>{tag}</code>)}</div></DetailRow>
      <DetailRow label="路径"><code>{agent.root}</code></DetailRow>
      <DetailRow label="描述"><p>{agent.description || '未提供描述'}</p></DetailRow>
    </div>
  </article>
}

function DetailRow({ label, children }: { label: string; children: ReactNode }) {
  return <div className={styles.detailRow}><strong>{label}</strong><div>{children}</div></div>
}

function StatusBadge({ enabled }: { enabled: boolean }) {
  return <span className={`${styles.status} ${enabled ? styles.enabled : styles.disabled}`}><i />{statusLabel(enabled)}</span>
}

function EmptyState({ layer, hasSearch }: { layer: AgentLayer; hasSearch: boolean }) {
  const Icon = layer === 'global' ? Layers3 : Users
  const title = hasSearch ? '没有找到匹配的子智能体' : `${layerLabel(layer)}暂无子智能体`
  return <div className={styles.emptyState}><Icon size={25} /><strong>{title}</strong><p>{hasSearch ? '请尝试更换搜索关键词。' : '当前层级没有可展示的子智能体。'}</p></div>
}
