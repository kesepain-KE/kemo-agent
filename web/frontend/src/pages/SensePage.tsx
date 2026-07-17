import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Activity, BrainCircuit, DatabaseZap, Eye, RefreshCw, ShieldAlert } from 'lucide-react'
import { useOutletContext } from 'react-router-dom'
import { getSense } from '../api/client'
import type { ShellOutletContext } from '../components/AppShell'
import { EmptyPanel, MetricCard, ModuleError, ModuleFrame, StatusChip } from '../components/ModuleUi'

type Layer = 'all' | 'user' | 'shared' | 'global'

const layerLabels: Record<string, string> = { user: '用户层', shared: '共享层', global: '全局层' }

export function SensePage() {
  const { user } = useOutletContext<ShellOutletContext>()
  const [layer, setLayer] = useState<Layer>('all')
  const query = useQuery({ queryKey: ['sense', user], queryFn: () => getSense(user), enabled: Boolean(user) })
  const data = query.data
  const sources = useMemo(() => (data?.sources || []).filter((source) => layer === 'all' || source.layer === layer), [data?.sources, layer])

  return (
    <ModuleFrame
      kicker="User-Registered Context Sources"
      title="全局感知"
      description="观察当前用户已注册的数据来源与 Prompt 注入闸门；来源的安装、授权和修改仍由配置或插件流程完成。"
      actions={<button className="module-btn" onClick={() => void query.refetch()}><RefreshCw size={15} />刷新来源</button>}
    >
      {query.isError && <ModuleError />}
      <div className="observer-banner">
        <span className="observer-banner-icon"><Eye size={17} /></span>
        <span><strong>观察视图</strong><small>只展示实际注册状态；`global_sense` 目录中的说明文件不等同于已注册运行来源。</small></span>
        <span className="observer-badge">当前用户 · {user}</span>
      </div>
      <section className="metric-strip">
        <MetricCard label="已注册来源" value={data?.summary.registered ?? '—'} detail="当前用户清单" symbol={<DatabaseZap size={16} />} />
        <MetricCard label="已启用" value={data?.summary.enabled ?? '—'} detail="允许进入评估" symbol={<Activity size={16} />} tone={data?.summary.enabled ? 'success' : 'muted'} />
        <MetricCard label="全局层" value={data?.core_available ? '可用' : '缺失'} detail={`global_sense · ${data?.core_files ?? 0} 个文件`} symbol={<BrainCircuit size={16} />} />
        <MetricCard label="注入闸门" value={data?.injection_enabled ? '已开启' : '未配置'} detail="运行时真实状态" symbol={<ShieldAlert size={16} />} tone={data?.injection_enabled ? 'success' : 'warning'} />
      </section>

      <section className="layer-strip">
        <article className="layer-card"><small>用户层</small><strong>当前用户自有来源</strong><p>由当前用户注册与授权，只影响该用户的 Prompt 组装。</p></article>
        <article className="layer-card"><small>共享层</small><strong>工作区共享来源</strong><p>来源可共享，但仍需当前用户显式启用后才参与判断。</p></article>
        <article className="layer-card"><small>全局层</small><strong>全局感知来源</strong><p>由 global_sense 与全局配置提供，作为所有用户的基础感知层。</p></article>
      </section>

      <div className="sense-pipeline" aria-label="全局感知注入流程">
        <span><b>01</b><strong>来源注册</strong><small>{data?.registry_available ? '配置段可用' : '尚未配置注册表'}</small></span><i>→</i>
        <span><b>02</b><strong>用户授权</strong><small>{data?.summary.enabled || 0} 个来源启用</small></span><i>→</i>
        <span><b>03</b><strong>注入判断</strong><small>{data?.injection_enabled ? '闸门已开启' : '未进入 Prompt'}</small></span>
      </div>

      <div className="module-toolbar">
        <div className="module-tabs">
          {([['all', '全部'], ['user', '用户层'], ['shared', '共享层'], ['global', '全局层']] as const).map(([value, label]) => (
            <button key={value} className={`module-tab-btn ${layer === value ? 'active' : ''}`} onClick={() => setLayer(value)}>{label}</button>
          ))}
        </div>
        <div className="toolbar-spacer" />
        <span className="toolbar-note">Web Observer · 只读</span>
      </div>

      {sources.length ? <section className="source-grid sense-source-grid">
        {sources.map((source) => <article className="source-card" key={source.id}>
          <div className="source-head"><span className="source-icon"><Activity size={16} /></span><StatusChip status={source.enabled ? 'enabled' : 'paused'}>{source.enabled ? '已启用' : '已停用'}</StatusChip></div>
          <h3>{source.name}</h3><p>{source.description || '该来源没有提供说明。'}</p>
          <div className="source-foot"><span>{layerLabels[source.layer] || source.layer}</span><span>{source.status}</span></div>
        </article>)}
      </section> : <EmptyPanel title="尚无已注册感知来源" description="核心说明目录存在，但当前配置没有来源注册表；因此不会显示演示传感器或伪造注入记录。" icon={<Eye size={21} />} />}

      <div className="module-grid sense-bottom-grid">
        <article className="panel">
          <div className="panel-head"><div className="panel-title"><span className="panel-title-icon">L</span><span><strong>注入决策记录</strong><span>实际评估结果</span></span></div><StatusChip status={data?.injection_enabled ? 'enabled' : 'paused'} /></div>
          <div className="panel-body">{data?.decisions.length ? <pre className="decision-output">{JSON.stringify(data.decisions, null, 2)}</pre> : <EmptyPanel title="暂无决策记录" description="当前运行时尚未实现感知决策事件流。" />}</div>
        </article>
        <aside><article className="panel"><div className="panel-head"><div className="panel-title"><span className="panel-title-icon">G</span><span><strong>当前用户注入策略</strong><span>只读镜像</span></span></div></div><div className="panel-body permission-list">
          <div className="permission-row"><span><strong>跨用户隔离</strong><span>仅加载 users/{user} 对应状态</span></span><StatusChip status="enabled">已启用</StatusChip></div>
          <div className="permission-row"><span><strong>未注册来源</strong><span>不会进入 Prompt 或状态面板</span></span><StatusChip status="enabled">已阻止</StatusChip></div>
          <div className="permission-row"><span><strong>Web 修改能力</strong><span>本页不修改来源和授权</span></span><StatusChip status="paused">只读</StatusChip></div>
        </div></article></aside>
      </div>
    </ModuleFrame>
  )
}
