import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Activity, BrainCircuit, DatabaseZap, Eye, RefreshCw, ShieldAlert } from 'lucide-react'
import { useOutletContext } from 'react-router-dom'
import { getSense } from '../api/client'
import type { ShellOutletContext } from '../components/AppShell'
import { EmptyPanel, MetricCard, ModuleError, ModuleFrame, StatusChip } from '../components/ModuleUi'

type Layer = 'all' | 'global'

const layerLabels: Record<string, string> = { global: '全局模块' }

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
      description="global_sense 的每个直接子目录都是独立感知模块；注册阶段发现全部模块，用户白名单决定主智能体实际注入范围。"
      actions={<button className="module-btn" onClick={() => void query.refetch()}><RefreshCw size={15} />刷新来源</button>}
    >
      {query.isError && <ModuleError />}
      <div className="observer-banner">
        <span className="observer-banner-icon"><Eye size={17} /></span>
        <span><strong>观察视图</strong><small>只读取模块目录内的 Markdown；global_sense 根目录文件不会注入。</small></span>
        <span className="observer-badge">当前用户 · {user}</span>
      </div>
      <section className="metric-strip">
        <MetricCard label="发现模块" value={data?.summary.registered ?? '—'} detail="直接子目录" symbol={<DatabaseZap size={16} />} />
        <MetricCard label="主智能体启用" value={data?.summary.enabled ?? '—'} detail="注册 + 白名单 + 非空" symbol={<Activity size={16} />} tone={data?.summary.enabled ? 'success' : 'muted'} />
        <MetricCard label="模块文件" value={data?.core_files ?? '—'} detail="可注入 Markdown" symbol={<BrainCircuit size={16} />} />
        <MetricCard label="注册模块" value={data?.registry_available ? '可用' : '缺失'} detail="global_sense/register.py" symbol={<ShieldAlert size={16} />} tone={data?.registry_available ? 'success' : 'warning'} />
      </section>

      <section className="layer-strip">
        <article className="layer-card"><small>发现边界</small><strong>直接子目录即模块</strong><p>模块内部递归读取 Markdown，并跳过隐藏目录与其他文件类型。</p></article>
        <article className="layer-card"><small>用户过滤</small><strong>{data?.source_policy.perception.global.mode === 'all' ? '全量启用' : `白名单：${data?.source_policy.perception.global.names.join('、') || '无'}`}</strong><p>空数组代表全量；非空数组按模块目录名精确匹配。</p></article>
        <article className="layer-card"><small>子代理边界</small><strong>不继承主策略</strong><p>子代理继续使用自己的 agent-config.json，不与此白名单求交集。</p></article>
      </section>

      <div className="sense-pipeline" aria-label="全局感知注入流程">
        <span><b>01</b><strong>全量注册</strong><small>{data?.registry_available ? 'register.py 已加载' : '注册模块缺失'}</small></span><i>→</i>
        <span><b>02</b><strong>用户过滤</strong><small>{data?.summary.enabled || 0} 个模块通过</small></span><i>→</i>
        <span><b>03</b><strong>Prompt 注入</strong><small>{data?.injection_enabled ? '存在有效模块' : '注入为空'}</small></span>
      </div>

      <div className="module-toolbar">
        <div className="module-tabs">
          {([['all', '全部模块'], ['global', '全局模块']] as const).map(([value, label]) => (
            <button key={value} className={`module-tab-btn ${layer === value ? 'active' : ''}`} onClick={() => setLayer(value)}>{label}</button>
          ))}
        </div>
        <div className="toolbar-spacer" />
        <span className="toolbar-note">Web Observer · 只读</span>
      </div>

      {sources.length ? <section className="source-grid sense-source-grid">
        {sources.map((source) => <article className="source-card" key={source.id}>
          <div className="source-head"><span className="source-icon"><Activity size={16} /></span><StatusChip status={source.enabled ? 'enabled' : 'paused'}>{source.enabled ? '已启用' : '已停用'}</StatusChip></div>
          <h3>{source.name}</h3><p>{source.description || '该模块没有可注入文件。'}</p>
          <div className="source-foot"><span>{layerLabels[source.layer] || source.layer}</span><span>{source.status} · {source.files} 文件</span></div>
        </article>)}
      </section> : <EmptyPanel title="尚无感知模块" description="请在 global_sense 下创建独立模块目录；根目录 Markdown 不会被当成模块。" icon={<Eye size={21} />} />}

      <div className="module-grid sense-bottom-grid">
        <article className="panel">
          <div className="panel-head"><div className="panel-title"><span className="panel-title-icon">L</span><span><strong>注入决策记录</strong><span>实际评估结果</span></span></div><StatusChip status={data?.injection_enabled ? 'enabled' : 'paused'} /></div>
          <div className="panel-body">{data?.decisions.length ? <pre className="decision-output">{JSON.stringify(data.decisions, null, 2)}</pre> : <EmptyPanel title="暂无决策记录" description="当前运行时尚未实现感知决策事件流。" />}</div>
        </article>
        <aside><article className="panel"><div className="panel-head"><div className="panel-title"><span className="panel-title-icon">G</span><span><strong>当前用户注入策略</strong><span>只读镜像</span></span></div></div><div className="panel-body permission-list">
          <div className="permission-row"><span><strong>跨用户隔离</strong><span>仅加载 users/{user} 对应状态</span></span><StatusChip status="enabled">已启用</StatusChip></div>
          <div className="permission-row"><span><strong>未注册来源</strong><span>可显示库存，但不会进入 Prompt</span></span><StatusChip status="enabled">已阻止</StatusChip></div>
          <div className="permission-row"><span><strong>Web 修改能力</strong><span>本页不修改来源和授权</span></span><StatusChip status="paused">只读</StatusChip></div>
        </div></article></aside>
      </div>
    </ModuleFrame>
  )
}
