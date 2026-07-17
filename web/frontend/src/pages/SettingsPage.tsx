import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Bot, Database, Moon, RefreshCw, Sun, Wrench } from 'lucide-react'
import { useOutletContext } from 'react-router-dom'
import { getSettings } from '../api/client'
import type { ShellOutletContext } from '../components/AppShell'
import { ModuleError, ModuleFrame, StatusChip } from '../components/ModuleUi'
import { useUiStore } from '../store/ui'

type SettingsTab = 'appearance' | 'provider' | 'users' | 'memory' | 'permissions' | 'runtime'

const credentialLabels: Record<string, string> = {
  environment: '环境变量已配置', inline: '配置文件内联', missing: '凭据未检测到',
}

function SettingRow({ title, description, control }: { title: string; description: string; control: React.ReactNode }) {
  return <div className="setting-row"><span className="setting-copy"><strong>{title}</strong><span>{description}</span></span><span className="setting-control">{control}</span></div>
}

export function SettingsPage() {
  const { user } = useOutletContext<ShellOutletContext>()
  const ui = useUiStore()
  const [tab, setTab] = useState<SettingsTab>('appearance')
  const query = useQuery({ queryKey: ['settings', user], queryFn: () => getSettings(user), enabled: Boolean(user) })
  const data = query.data

  return (
    <ModuleFrame
      kicker="Configuration Overview"
      title="配置概览"
      description="查看脱敏后的运行配置镜像；仅主题与字号在 Web 中可修改，其余设置仍由配置文件和专用流程维护。"
      actions={<button className="module-btn" onClick={() => void query.refetch()}><RefreshCw size={15} />重新读取</button>}
    >
      {query.isError && <ModuleError />}
      <div className="observer-banner settings-observer-banner">
        <span className="observer-banner-icon">R</span>
        <span><strong>默认只读</strong><small>接口不会返回 API Key、环境变量值、完整配置对象或内部路径。</small></span>
        <span className="observer-badge">Schema v{data?.schema_version || '—'}</span>
      </div>

      <div className="settings-layout">
        <nav className="settings-nav" aria-label="配置分类">
          {([
            ['appearance', '外观与主题'], ['provider', '模型与 Provider'], ['users', '用户切换'],
            ['memory', '记忆与上下文'], ['permissions', '权限边界'], ['runtime', '运行限制'],
          ] as const).map(([value, label]) => <button key={value} className={tab === value ? 'active' : ''} onClick={() => setTab(value)}><span>{label}</span><span>›</span></button>)}
        </nav>

        <div className="settings-content">
          {tab === 'appearance' && <>
            <article className="setting-section">
              <div className="setting-section-head"><strong>界面主题</strong><span>侧边栏、工作区、浮层与控件使用统一语义色。</span></div>
              <div className="theme-choice-grid">
                <button className={`theme-choice ${ui.theme === 'light' ? 'active' : ''}`} onClick={() => ui.setTheme('light')}><span className="theme-preview light"><Sun size={18} /></span><span><strong>高级白</strong><small>统一灰白 · 低对比边界</small></span><i>{ui.theme === 'light' ? '✓' : ''}</i></button>
                <button className={`theme-choice ${ui.theme === 'dark' ? 'active' : ''}`} onClick={() => ui.setTheme('dark')}><span className="theme-preview dark"><Moon size={18} /></span><span><strong>高级黑</strong><small>中性黑灰 · 统一层级</small></span><i>{ui.theme === 'dark' ? '✓' : ''}</i></button>
              </div>
            </article>
            <article className="setting-section">
              <div className="setting-section-head"><strong>界面字号</strong><span>调整文字比例，不改变功能布局。</span></div>
              <div className="font-choice-row">{(['small', 'medium', 'large'] as const).map((size) => <button key={size} className={ui.fontSize === size ? 'active' : ''} onClick={() => ui.setFontSize(size)}><b>Aa</b><span>{size === 'small' ? '小' : size === 'medium' ? '中' : '大'}</span><small>{size === 'small' ? '紧凑' : size === 'medium' ? '默认' : '舒适'}</small></button>)}</div>
            </article>
          </>}

          {tab === 'provider' && <>
            <article className="setting-section">
              <div className="setting-section-head"><strong>Provider 与基础 Chat API</strong><span>只展示路由元数据，不探测或调用模型。</span></div>
              <SettingRow title="Provider 类型" description="当前合并配置中的适配器" control={<span className="select-like">{data?.provider.type || '—'}</span>} />
              <SettingRow title="模型" description="当前默认模型标识" control={<span className="select-like wide">{data?.provider.model || '—'}</span>} />
              <SettingRow title="兼容端点" description="仅显示配置的基础 URL" control={<span className="select-like wide">{data?.provider.base_url || '—'}</span>} />
              <SettingRow title="凭据状态" description="只返回来源状态，不返回变量名或值" control={<StatusChip status={data?.provider.credential_source === 'missing' ? 'missing' : 'configured'}>{credentialLabels[data?.provider.credential_source || 'missing']}</StatusChip>} />
            </article>
            <article className="setting-section">
              <div className="setting-section-head"><strong>调用参数</strong><span>Provider 运行时的只读镜像。</span></div>
              <SettingRow title="请求超时" description="单次 Provider 调用上限" control={<span className="value-pill">{data?.provider.timeout ?? '—'} 秒</span>} />
              <SettingRow title="原生流式" description="Provider 配置中的 stream 开关；Web 自身仍使用 SSE" control={<StatusChip status={data?.provider.stream ? 'enabled' : 'paused'}>{data?.provider.stream ? '已开启' : '已关闭'}</StatusChip>} />
            </article>
          </>}

          {tab === 'users' && <>
            <article className="setting-section current-user-section">
              <div className="setting-section-head"><strong>用户切换机制</strong><span>每个 Web 窗口只激活一个 users/&lt;user_id&gt; 目录，不并行运行多个智能体。</span></div>
              <div className="current-user-card"><span className="user-card-avatar">{user.slice(0, 1).toUpperCase()}</span><span><strong>{user}</strong><small>users/{user} · 当前用户</small></span><StatusChip status="enabled">已载入</StatusChip></div>
            </article>
            <article className="setting-section">
              <div className="setting-section-head"><strong>可用用户</strong><span>切换操作位于左侧栏底部。</span></div>
              <div className="user-space-list">{data?.users.map((name) => <div className="user-space-row" key={name}><span className="mini-avatar">{name.slice(0, 1).toUpperCase()}</span><span><strong>{name}</strong><small>users/{name}</small></span><StatusChip status={name === user ? 'enabled' : 'gray'}>{name === user ? '当前' : '可切换'}</StatusChip></div>)}</div>
            </article>
          </>}

          {tab === 'memory' && <>
            <article className="setting-section">
              <div className="setting-section-head"><strong>上下文窗口</strong><span>历史窗口的真实 Token 使用量会显示在顶栏。</span></div>
              <SettingRow title="Token 上限" description="达到限制前由上下文生命周期执行压缩" control={<span className="value-pill">{data?.limits.context_tokens.toLocaleString() || '—'}</span>} />
              <SettingRow title="压缩比例" description="上下文压缩后的目标比例" control={<span className="value-pill">{data ? Math.round(data.limits.compression_ratio * 100) : '—'}%</span>} />
            </article>
            <article className="setting-section">
              <div className="setting-section-head"><strong>记忆管线</strong><span>抽取与注入开关来自当前用户合并配置。</span></div>
              <SettingRow title="记忆抽取" description="成功对话后生成候选记忆" control={<StatusChip status={data?.features.memory_extraction ? 'enabled' : 'paused'} />} />
              <SettingRow title="记忆注入" description={`最多 ${data?.limits.memory_items || '—'} 条 / ${data?.limits.memory_chars || '—'} 字符`} control={<StatusChip status={data?.features.memory_injection ? 'enabled' : 'paused'} />} />
            </article>
          </>}

          {tab === 'permissions' && <article className="setting-section">
            <div className="setting-section-head"><strong>安全与数据边界</strong><span>Web Observer API 的固定策略。</span></div>
            <SettingRow title="跨用户资源访问" description="路径参数必须对应已存在用户，历史按 user/source/session 隔离" control={<StatusChip status="enabled">已隔离</StatusChip>} />
            <SettingRow title="敏感配置返回" description="API Key、环境变量值与完整配置不会进入响应" control={<StatusChip status="enabled">已脱敏</StatusChip>} />
            <SettingRow title="系统级修改" description="任务、配置、技能和来源页面默认只读" control={<StatusChip status="paused">Web 禁止</StatusChip>} />
          </article>}

          {tab === 'runtime' && <>
            <article className="setting-section">
              <div className="setting-section-head"><strong>功能开关</strong><span>当前用户合并配置中的运行能力。</span></div>
              <SettingRow title="工具调用" description="Run 工具编排" control={<StatusChip status={data?.features.tools ? 'enabled' : 'paused'}><Wrench size={12} />{data?.features.tools ? '已启用' : '已停用'}</StatusChip>} />
              <SettingRow title="文件知识" description="本地文件索引与 Prompt 注入" control={<StatusChip status={data?.features.knowledge ? 'enabled' : 'paused'}><Database size={12} />{data?.features.knowledge ? '已启用' : '已停用'}</StatusChip>} />
              <SettingRow title="任务计划自动接受" description="计划是否跳过人工批准" control={<StatusChip status={data?.features.task_plan_auto_accept ? 'enabled' : 'paused'}><Bot size={12} />{data?.features.task_plan_auto_accept ? '已开启' : '需确认'}</StatusChip>} />
              <SettingRow title="Cron 调度" description="Web 不启动调度器，只显示主宿主配置" control={<StatusChip status={data?.features.cron ? 'enabled' : 'paused'}>{data?.features.cron ? '已启用' : '已停用'}</StatusChip>} />
            </article>
            <article className="setting-section">
              <div className="setting-section-head"><strong>执行限制</strong><span>用于约束工具、知识和任务计划。</span></div>
              <SettingRow title="工具迭代" description={`单次调用超时 ${data?.limits.tool_timeout || '—'} 秒`} control={<span className="value-pill">{data?.limits.tool_iterations || '—'} 轮</span>} />
              <SettingRow title="任务步骤" description="单个计划最大步骤数" control={<span className="value-pill">{data?.limits.task_plan_steps || '—'} 步</span>} />
              <SettingRow title="知识注入" description={`最多 ${data?.limits.knowledge_chars || '—'} 字符`} control={<span className="value-pill">{data?.limits.knowledge_items || '—'} 项</span>} />
            </article>
          </>}
        </div>
      </div>
    </ModuleFrame>
  )
}
