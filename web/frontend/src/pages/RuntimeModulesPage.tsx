import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Bot,
  Boxes,
  Cable,
  FileCode2,
  Layers3,
  RadioTower,
  RefreshCw,
  ShieldCheck,
  UsersRound,
} from 'lucide-react'
import { useOutletContext } from 'react-router-dom'
import { getAgents, getExpands, getMessageStatus } from '../api/client'
import type { ShellOutletContext } from '../components/AppShell'
import {
  EmptyPanel,
  formatBytes,
  formatDateTime,
  MetricCard,
  ModuleError,
  ModuleFrame,
  StatusChip,
} from '../components/ModuleUi'
import styles from './RuntimeModulesPage.module.css'

type RuntimeTab = 'agents' | 'messages' | 'expand'

export function RuntimeModulesPage({ fixedTab }: { fixedTab?: RuntimeTab } = {}) {
  const { user } = useOutletContext<ShellOutletContext>()
  const [selectedTab, setTab] = useState<RuntimeTab>('agents')
  const tab = fixedTab ?? selectedTab
  const agentsQuery = useQuery({ queryKey: ['agents', user], queryFn: () => getAgents(user), enabled: Boolean(user) })
  const messageQuery = useQuery({ queryKey: ['message-status', user], queryFn: () => getMessageStatus(user), enabled: Boolean(user), refetchInterval: 30_000 })
  const expandQuery = useQuery({ queryKey: ['expands', user], queryFn: () => getExpands(user), enabled: Boolean(user) })
  const activeQuery = tab === 'agents' ? agentsQuery : tab === 'messages' ? messageQuery : expandQuery
  const page = fixedTab === 'agents'
    ? { kicker: 'Subagents', title: '子智能体', description: '查看内置子代理与当前用户的可信热插拔子代理。' }
    : fixedTab === 'messages'
      ? { kicker: 'External Messaging', title: '外部消息', description: '查看当前用户的身份绑定、传输插件、运行状态与错误。' }
      : fixedTab === 'expand'
        ? { kicker: 'Expand Modules', title: '拓展', description: '查看全局、共享与用户三层 Expand 注册库存。' }
        : { kicker: 'Agents, Transports & Expand', title: '运行模块', description: '查看当前用户可见的子代理、外部消息身份与传输状态，以及三层 Expand 注册库存。' }

  const refreshAll = () => {
    void Promise.all([agentsQuery.refetch(), messageQuery.refetch(), expandQuery.refetch()])
  }

  return (
    <ModuleFrame
      kicker={page.kicker}
      title={page.title}
      description={page.description}
      actions={<button className="module-btn" onClick={refreshAll}><RefreshCw size={15} />刷新运行模块</button>}
    >
      {activeQuery.isError && <ModuleError message="运行模块状态读取失败，请检查配置文件或 RuntimeHost 状态。" />}
      {!fixedTab && <div className={styles.tabs} role="tablist" aria-label="运行模块分类">
        <button role="tab" aria-selected={tab === 'agents'} className={tab === 'agents' ? styles.active : ''} onClick={() => setTab('agents')}><Bot size={17} /><span>子代理</span><b>{agentsQuery.data?.summary.total ?? '—'}</b></button>
        <button role="tab" aria-selected={tab === 'messages'} className={tab === 'messages' ? styles.active : ''} onClick={() => setTab('messages')}><RadioTower size={17} /><span>外部消息</span><b>{messageQuery.data?.summary.total_transports ?? '—'}</b></button>
        <button role="tab" aria-selected={tab === 'expand'} className={tab === 'expand' ? styles.active : ''} onClick={() => setTab('expand')}><Boxes size={17} /><span>Expand</span><b>{expandQuery.data?.summary.total ?? '—'}</b></button>
      </div>}

      {tab === 'agents' && <AgentInventory data={agentsQuery.data} />}
      {tab === 'messages' && <MessageInventory data={messageQuery.data} />}
      {tab === 'expand' && <ExpandInventory data={expandQuery.data} />}
    </ModuleFrame>
  )
}

export function AgentsPage() {
  return <RuntimeModulesPage fixedTab="agents" />
}

export function MessagesPage() {
  return <RuntimeModulesPage fixedTab="messages" />
}

export function ExpandPage() {
  return <RuntimeModulesPage fixedTab="expand" />
}

function AgentInventory({ data }: { data: Awaited<ReturnType<typeof getAgents>> | undefined }) {
  return <>
    <section className="metric-strip">
      <MetricCard label="已发现" value={data?.summary.total ?? '—'} detail="全局与用户子代理" symbol={<UsersRound size={16} />} />
      <MetricCard label="已启用" value={data?.summary.enabled ?? '—'} detail="可进入运行管线" symbol={<ShieldCheck size={16} />} tone="success" />
      <MetricCard label="全局骨架" value={data?.summary.global ?? '—'} detail="agents/" symbol={<Bot size={16} />} />
      <MetricCard label="用户骨架" value={data?.summary.user ?? '—'} detail="users/<user>/agents" symbol={<Layers3 size={16} />} />
    </section>
    {data?.agents.length ? <div className={styles.cardGrid}>{data.agents.map((agent) => <article className={`panel ${styles.agentCard}`} key={`${agent.source}:${agent.name}`}>
      <div className="panel-head"><div className="panel-title"><span className="panel-title-icon"><Bot size={15} /></span><span><strong>{agent.name}</strong><span>{agent.description || '未提供描述'}</span></span></div><StatusChip status={agent.enabled ? 'enabled' : 'paused'}>{agent.enabled ? '已启用' : '已停用'}</StatusChip></div>
      <div className={styles.definitionGrid}><span><small>来源</small><strong>{agent.source === 'global' ? '全局' : '用户'}</strong></span><span><small>执行器</small><strong>{agent.execution}</strong></span><span><small>模型档位</small><strong>{agent.model_profile}</strong></span><span><small>暴露范围</small><strong>{agent.exposure}</strong></span></div>
      <div className={styles.pathLine}>{agent.root}</div>
      <details className={styles.fileDetails}><summary><FileCode2 size={14} />目录文件 <b>{agent.files.length}</b></summary><div>{agent.files.map((file) => <span key={file.relative_path}><code>{file.relative_path}</code><small>{formatBytes(file.size)} · {formatDateTime(file.updated_at)}</small></span>)}</div></details>
    </article>)}</div> : <EmptyPanel title="没有可显示的子代理" description="当前用户没有发现有效的全局或用户子代理骨架。" icon={<Bot size={21} />} />}
  </>
}

function MessageInventory({ data }: { data: Awaited<ReturnType<typeof getMessageStatus>> | undefined }) {
  return <>
    <section className="metric-strip">
      <MetricCard label="身份绑定" value={data?.summary.total_bindings ?? '—'} detail="外部身份映射" symbol={<UsersRound size={16} />} />
      <MetricCard label="传输插件" value={data?.summary.total_transports ?? '—'} detail="仅展示插件元数据" symbol={<Cable size={16} />} />
      <MetricCard label="正在运行" value={data?.summary.running_transports ?? '—'} detail="RuntimeHost 已接管" symbol={<RadioTower size={16} />} tone="success" />
      <MetricCard label="异常" value={data?.summary.error_transports ?? '—'} detail={`${data?.summary.stopped_transports ?? 0} 个已停止`} symbol={<ShieldCheck size={16} />} tone={data?.summary.error_transports ? 'warning' : 'muted'} />
    </section>
    {data?.issues.length ? <div className={styles.issueList}>{data.issues.map((issue) => <span key={`${issue.name}:${issue.error}`}><strong>{issue.name}</strong>{issue.error}</span>)}</div> : null}
    <div className={styles.messageGrid}>
      <article className="panel">
        <div className="panel-head"><div className="panel-title"><span className="panel-title-icon"><RadioTower size={15} /></span><span><strong>传输插件</strong><span>状态 API 不导入或执行插件代码</span></span></div><span className="panel-count">{data?.transports.length || 0}</span></div>
        {data?.transports.length ? <div className={styles.transportList}>{data.transports.map((transport) => <div className={styles.transportRow} key={`${transport.platform}:${transport.name}`}>
          <span className={styles.transportIcon}><RadioTower size={16} /></span>
          <span><strong>{transport.display_name || transport.name}</strong><small>{transport.platform} · {transport.name}</small><em>{transport.capabilities.join('、') || '未声明能力'}</em></span>
          <span><StatusChip status={transport.state} /><small>收 {transport.messages_received_today} / 发 {transport.messages_sent_today}</small><small>{transport.last_check ? `检查 ${formatDateTime(transport.last_check)}` : transport.health}</small></span>
          {transport.last_error ? <p>{String(transport.last_error)}</p> : null}
        </div>)}</div> : <EmptyPanel title="没有绑定的消息传输" description="message/out 中没有属于当前用户的有效插件配置。" icon={<RadioTower size={21} />} />}
      </article>
      <article className="panel">
        <div className="panel-head"><div className="panel-title"><span className="panel-title-icon"><UsersRound size={15} /></span><span><strong>身份绑定</strong><span>外部身份到内部用户的解析结果</span></span></div><span className="panel-count">{data?.bindings.length || 0}</span></div>
        {data?.bindings.length ? <div className={styles.bindingList}>{data.bindings.map((binding, index) => <div key={`${binding.platform}:${binding.external_user_id}:${index}`}><span><strong>{binding.platform}</strong><small>{binding.chat_type || '任意会话类型'}</small></span><code>{binding.external_user_id}</code><span>→ {binding.internal_user}</span></div>)}</div> : <EmptyPanel title="没有身份绑定" description="当前用户尚未配置外部平台身份映射。" icon={<UsersRound size={21} />} />}
      </article>
    </div>
  </>
}

function ExpandInventory({ data }: { data: Awaited<ReturnType<typeof getExpands>> | undefined }) {
  return <>
    <section className="metric-strip">
      <MetricCard label="模块总数" value={data?.summary.total ?? '—'} detail="仅浏览注册库存" symbol={<Boxes size={16} />} />
      <MetricCard label="全局层" value={data?.summary.global ?? '—'} detail="global_expand" symbol={<Layers3 size={16} />} />
      <MetricCard label="共享层" value={data?.summary.shared ?? '—'} detail="shared_expand" symbol={<Layers3 size={16} />} />
      <MetricCard label="用户层" value={data?.summary.user ?? '—'} detail="users/<user>/expand" symbol={<Layers3 size={16} />} />
    </section>
    <div className={styles.expandGrid}>{data?.expands.map((scope) => <article className={`panel ${styles.expandScope}`} key={scope.scope}>
      <div className="panel-head"><div className="panel-title"><span className="panel-title-icon">{scope.scope.slice(0, 1).toUpperCase()}</span><span><strong>{scope.scope === 'global' ? '全局 Expand' : scope.scope === 'shared' ? '共享 Expand' : '用户 Expand'}</strong><span>{scope.root}</span></span></div><span className="panel-count">{scope.items.length}</span></div>
      {scope.items.length ? <div className={styles.expandList}>{scope.items.map((item) => <details key={item.relative_path}><summary><span><Boxes size={15} /><strong>{item.name}</strong></span><span><StatusChip status={item.has_register ? 'enabled' : 'missing'}>{item.has_register ? '已注册' : '缺少 expand.json'}</StatusChip><b>{item.files.length} 文件</b></span></summary><div>{item.files.map((file) => <span key={file.relative_path}><code>{file.relative_path}</code><small>{formatBytes(file.size)}</small></span>)}</div></details>)}</div> : <EmptyPanel title="该层没有 Expand 模块" description="目录不存在或没有可展示的模块子目录。" icon={<Boxes size={21} />} />}
    </article>)}</div>
  </>
}
