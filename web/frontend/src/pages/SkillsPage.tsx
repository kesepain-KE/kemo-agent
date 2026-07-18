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

function policyLabel(mode: 'all' | 'allowlist' | undefined, names: string[] | undefined) {
  if (!mode) return '—'
  return mode === 'all' ? '全量启用' : `白名单：${names?.join('、') || '无'}`
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
      description="分别展示可执行插件与 Prompt 技能库存；用户白名单只过滤主智能体的共享/用户技能，不改变注册结果。"
      actions={<button className="module-btn" onClick={() => void query.refetch()}><RefreshCw size={15} />刷新注册表</button>}
    >
      {query.isError && <ModuleError />}
      <section className="metric-strip">
        <MetricCard label="已注册" value={data?.summary.registered ?? '—'} detail="工具清单" symbol={<Boxes size={16} />} />
        <MetricCard label="已启用" value={data?.summary.enabled ?? '—'} detail="可供 Run 调用" symbol={<ShieldCheck size={16} />} tone="success" />
        <MetricCard label="Prompt 技能" value={data ? `${data.prompt_summary.active}/${data.prompt_summary.registered}` : '—'} detail="主智能体启用 / 已注册" symbol={<UserRound size={16} />} />
        <MetricCard label="基础插件" value={data?.summary.core ?? '—'} detail="plugins 目录" symbol={<Wrench size={16} />} />
      </section>

      <section className="layer-strip">
        <article className="layer-card"><small>用户技能策略</small><strong>{policyLabel(data?.source_policy.skills.user.mode, data?.source_policy.skills.user.names)}</strong><p>只影响当前用户的主智能体；不会限制子代理自己的授权。</p></article>
        <article className="layer-card"><small>共享技能策略</small><strong>{policyLabel(data?.source_policy.skills.shared.mode, data?.source_policy.skills.shared.names)}</strong><p>空白名单表示全量启用；非空列表按相对技能 ID 精确匹配。</p></article>
        <article className="layer-card"><small>基础插件</small><strong>独立工具注册表</strong><p>插件是否可执行由工具系统控制，不使用 Prompt 技能白名单。</p></article>
      </section>

      {data?.prompt_skills.length ? <section className="skill-grid">
        {data.prompt_skills.map((skill) => <article className="skill-card" key={`${skill.scope}:${skill.name}`}>
          <div className="skill-card-top"><span className="skill-mark">{skill.title.slice(0, 2).toUpperCase()}</span><StatusChip status={skill.active_for_main_agent ? 'enabled' : 'paused'}>{skill.active_for_main_agent ? '主智能体已启用' : '已过滤'}</StatusChip></div>
          <h3>{skill.title}</h3><p>{skill.description || skill.name}</p>
          <div className="skill-meta"><span className="skill-tag">{layerLabels[skill.scope]}</span><span>{skill.name}</span></div>
        </article>)}
      </section> : null}

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
