import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Boxes, RefreshCw, Search, ShieldCheck, UserRound, Wrench } from 'lucide-react'
import { useOutletContext } from 'react-router-dom'
import { getSkills } from '../api/client'
import type { ShellOutletContext } from '../components/AppShell'
import { EmptyPanel, MetricCard, ModuleError, ModuleFrame, StatusChip } from '../components/ModuleUi'

type Layer = 'all' | 'user' | 'shared' | 'core'

const layerLabels: Record<string, string> = {
  user: '用户层', shared: '共享层', core: '基础插件',
}

export function SkillsPage() {
  const { user } = useOutletContext<ShellOutletContext>()
  const [layer, setLayer] = useState<Layer>('all')
  const [search, setSearch] = useState('')
  const query = useQuery({ queryKey: ['skills', user], queryFn: () => getSkills(user), enabled: Boolean(user) })
  const tools = useMemo(() => {
    const term = search.trim().toLocaleLowerCase()
    return (query.data?.tools || []).filter((tool) => (
      (layer === 'all' || tool.layer === layer)
      && (!term || `${tool.name} ${tool.description}`.toLocaleLowerCase().includes(term))
    ))
  }, [layer, query.data?.tools, search])
  const data = query.data

  return (
    <ModuleFrame
      kicker="Capability Registry"
      title="技能中心"
      description="按用户层、共享层与基础插件展示当前工具注册结果；Web 仅负责观察，不修改本地技能文件。"
      actions={<button className="module-btn" onClick={() => void query.refetch()}><RefreshCw size={15} />刷新注册表</button>}
    >
      {query.isError && <ModuleError />}
      <section className="metric-strip">
        <MetricCard label="已注册" value={data?.summary.registered ?? '—'} detail="工具清单" symbol={<Boxes size={16} />} />
        <MetricCard label="已启用" value={data?.summary.enabled ?? '—'} detail="可供 Run 调用" symbol={<ShieldCheck size={16} />} tone="success" />
        <MetricCard label="用户层" value={data?.summary.user ?? '—'} detail={`users/${user}/user_skills`} symbol={<UserRound size={16} />} />
        <MetricCard label="基础插件" value={data?.summary.core ?? '—'} detail="plugins 目录" symbol={<Wrench size={16} />} />
      </section>

      <section className="layer-strip">
        <article className="layer-card"><small>用户层</small><strong>当前用户技能</strong><p>由智能体或用户在当前用户目录创建，具有最高覆盖优先级。</p></article>
        <article className="layer-card"><small>共享层</small><strong>工作区共享技能</strong><p>多个用户可按权限共同调用，适合作为公共能力集合。</p></article>
        <article className="layer-card"><small>基础层</small><strong>内置插件注册表</strong><p>由项目 plugins 目录提供，更新时可被项目版本维护。</p></article>
      </section>

      <div className="module-toolbar">
        <div className="module-tabs">
          {([['all', '全部技能'], ['user', '用户层'], ['shared', '共享层'], ['core', '基础插件']] as const).map(([value, label]) => (
            <button key={value} className={`module-tab-btn ${layer === value ? 'active' : ''}`} onClick={() => setLayer(value)}>{label}</button>
          ))}
        </div>
        <div className="toolbar-spacer" />
        <label className="module-search"><Search size={14} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索技能…" /></label>
      </div>

      {tools.length ? <section className="skill-grid">
        {tools.map((tool) => (
          <article className="skill-card" key={`${tool.source}:${tool.name}`}>
            <div className="skill-card-top">
              <span className="skill-mark">{tool.name.slice(0, 2).toUpperCase()}</span>
              <StatusChip status={tool.enabled ? 'enabled' : 'paused'}>{tool.enabled ? '已启用' : '已停用'}</StatusChip>
            </div>
            <h3>{tool.name}</h3>
            <p>{tool.description}</p>
            <div className="skill-meta"><span className="skill-tag">{layerLabels[tool.layer] || tool.layer}</span><span>v{tool.version}{tool.overrides ? ` · 覆盖 ${tool.overrides}` : ''}</span></div>
          </article>
        ))}
      </section> : <EmptyPanel title="没有匹配的技能" description={search || layer !== 'all' ? '调整筛选条件后重试。' : '当前用户尚未注册任何工具。'} icon={<Wrench size={21} />} />}
    </ModuleFrame>
  )
}
